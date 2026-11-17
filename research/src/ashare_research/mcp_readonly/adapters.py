"""Normalize saved schemas without running research or reading external inputs."""
from datetime import date
import math

from .errors import ArtifactError


def require(condition):
    if not condition:
        raise ArtifactError("INVALID_ARTIFACT", "Unsupported or malformed research fields")


class Experiment:
    def __init__(self, data):
        self.raw = data
        schema = data.get("schema_version")
        if schema not in ("1.0", "2.0", "3.0"):
            raise ArtifactError("UNSUPPORTED_SCHEMA", "Unsupported research schema version")
        self.schema = schema
        self.stage = {"1.0": "M2", "2.0": "M3A", "3.0": "M3B"}[schema]
        # Disallow nonfinite numbers even in optional fields. No external paths read.
        stack = [data]
        nodes = 0
        while stack:
            value = stack.pop()
            nodes += 1
            require(nodes <= 1000000)
            if isinstance(value, float):
                require(math.isfinite(value))
            elif isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        for key in ["evaluation_period", "universe_policy", "provenance", "return_convention"]:
            require(isinstance(data.get(key), dict))
        period = data["evaluation_period"]
        try:
            require(date.fromisoformat(period["start"]) <= date.fromisoformat(period["end"]))
        except (ValueError, KeyError, TypeError):
            raise ArtifactError("INVALID_ARTIFACT", "Malformed evaluation period") from None
        require(isinstance(data.get("limitations"), list) and all(isinstance(x, str) for x in data["limitations"]))
        self.scopes = ["legacy_label_first"] if schema == "1.0" else ["native"] if schema == "2.0" else ["native", "matched"]
        if schema == "3.0":
            definitions = data.get("factor_definitions")
            require(isinstance(definitions, list) and 0 < len(definitions) <= 6)
            self.definitions = {}
            for d in definitions:
                require(isinstance(d, dict) and isinstance(d.get("factor_id"), str))
                require(d["factor_id"] not in self.definitions)
                self.definitions[d["factor_id"]] = d
            require(isinstance(data.get("results"), dict))
            for scope in self.scopes:
                require(isinstance(data["results"].get(scope), dict))
                for factor, item in data["results"][scope].items():
                    require(factor in self.definitions and isinstance(item, dict))
                    self._validate_metrics(item.get("metrics"), item.get("daily"))
        else:
            require(isinstance(data.get("factor_parameters"), dict))
            if schema == "1.0":
                require(data["factor_parameters"].get("lookback_sessions") == 60)
            else:
                require(data["factor_parameters"].get("name") == "60_session_price_momentum")
            self.definitions = {"momentum_60": {"factor_id": "momentum_60", "definition_version": None, "version_note": "Not explicitly versioned in saved artifact", "saved_parameters": data["factor_parameters"]}}
            self._validate_metrics(data.get("metrics"), data.get("metrics", {}).get("daily") if schema == "1.0" else data.get("daily_metrics"))

    def _validate_metrics(self, metrics, daily):
        require(isinstance(metrics, dict) and isinstance(daily, list))
        require("mean_daily_rank_ic" in metrics and "valid_ic_dates" in metrics)
        ic = metrics["mean_daily_rank_ic"]
        require(ic is None or type(ic) in (int, float) and -1.000000001 <= ic <= 1.000000001)
        require(type(metrics["valid_ic_dates"]) is int and metrics["valid_ic_dates"] >= 0)
        require(isinstance(metrics.get("quantile_returns" if self.schema == "1.0" else "buckets"), dict))
        seen = set()
        for row in daily:
            require(isinstance(row, dict))
            day = row.get("date" if self.schema == "1.0" else "trade_date")
            try:
                date.fromisoformat(day)
            except (ValueError, TypeError):
                raise ArtifactError("INVALID_ARTIFACT", "Malformed saved daily date") from None
            require(day not in seen)
            seen.add(day)
            require("rank_ic" in row)

    def context(self):
        return {
            "evaluation_semantics": "legacy labels selected before buckets" if self.schema == "1.0" else "signal_first_v1; assignments precede labels",
            "scope_semantics": {"native": "Each factor's own ready signal sample", "matched": "Common factor-ready keys determined before labels, with independent ranks", "legacy_label_first": "M2 legacy evaluation; not equivalent to signal-first native coverage"},
            "metric_semantics": "Rank IC is rank association. Quantile high-minus-low is a descriptive forward-outcome spread, not portfolio profit.",
            "return_convention": self.raw["return_convention"],
            "missing_outcomes": "Missing is unknown, never zero; rates are supplied only where saved.",
            "validation_semantics": "Numerical/regression correctness does not establish investment profitability or tradability.",
            "comparability": "Compare evaluation period, cohort, scope, factor definition and accepted dates; identical factor names do not establish comparable samples.",
            "status": "Exploratory samples, not representative market studies. M1 limitations remain open.",
            "limitations": self.raw["limitations"],
        }

    def summary(self):
        return {"experiment_id": self.raw["experiment_id"], "stage": self.stage, "schema_version": self.schema, "evaluation_period": self.raw["evaluation_period"], "factor_ids": list(self.definitions), "available_scopes": self.scopes, "research_version": self.raw.get("research_version"), "policy_version": self.raw.get("policy_version"), "evaluation_version": self.raw.get("evaluation_version"), "universe_policy": {k:v for k,v in self.raw["universe_policy"].items() if k != "sample_manifest"}, "survivorship_bias_eliminated": False}

    def results(self, factor, scope):
        if factor not in self.definitions:
            raise ArtifactError("UNAVAILABLE_FACTOR", "Factor not defined for this experiment")
        if scope not in self.scopes:
            raise ArtifactError("UNAVAILABLE_SCOPE", "Scope unavailable for this experiment")
        if self.schema == "3.0":
            item = self.raw["results"][scope].get(factor)
            if item is None:
                require(factor == "liquidity_20" and self.raw.get("liquidity_validation", {}).get("enabled") is False)
                return {"available": False, "disabled_reasons": self.raw["liquidity_validation"].get("disabled_reasons", [])}, []
            metrics, daily = item["metrics"], item["daily"]
        else:
            metrics = self.raw["metrics"]
            daily = metrics["daily"] if self.schema == "1.0" else self.raw["daily_metrics"]
        # Preserve distinctions; never substitute M3B comparison-date means for native means.
        normalized = {"available": True, "mean_daily_rank_ic": metrics["mean_daily_rank_ic"], "valid_ic_dates": metrics["valid_ic_dates"], "outcomes": metrics.get("outcomes"), "missing_outcome_statistics_available": "outcomes" in metrics, "quantiles": metrics.get("quantile_returns", metrics.get("buckets")), "high_minus_low": metrics.get("primary_high_minus_low"), "spread_available": "primary_high_minus_low" in metrics, "primary_comparison_dates": metrics.get("primary_comparison_dates"), "missing_label_reasons": metrics.get("missing_label_reasons"), "inference": metrics.get("inference"), "bucket_missing_rate_range": metrics.get("bucket_missing_rate_range")}
        comparisons = [r for r in self.raw.get("comparisons", []) if r.get("scope") == scope and r.get("factor_id") == factor]
        normalized["cross_factor_common_date_comparisons"] = comparisons
        return normalized, daily

    def coverage(self):
        fields = ["data_coverage", "exclusions"] if self.schema != "3.0" else ["data_quality", "diagnostics", "liquidity_validation"]
        return {key:self.raw.get(key) for key in fields}

    def provenance(self):
        p = self.raw["provenance"]
        # No absolute paths/configuration roots or dereferencing saved pointers.
        summary = {k:p[k] for k in ["started_at_utc", "completed_at_utc", "package_version", "python_version", "dependencies", "dependency_versions", "configuration_sha256", "load_start", "load_end"] if k in p}
        summary["configuration"] = {k:v for k,v in p.get("configuration", {}).items() if k not in ("data_root", "config_path")}
        entries = []
        for group in ["source_sha256", "input_file_sha256", "reference_artifact_sha256"]:
            values = p.get(group, {})
            require(isinstance(values, dict))
            for name, digest in sorted(values.items()):
                require(isinstance(name, str) and isinstance(digest, str))
                # Paths are historical labels only; hide absolute/drive/UNC references.
                if name.startswith(("/", "\\")) or ":" in name:
                    name = "[external reference redacted]"
                entries.append({"group":group,"reference_label":name,"stored_sha256":digest,"verification":"recorded only; external source not accessed"})
        summary["stored_hash_entry_count"] = len(entries)
        return summary, entries
