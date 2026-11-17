"""Run one bounded listing-aware cohort experiment without changing M2."""
import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
import tomllib
from uuid import uuid4

import polars as pl

from ..factors import momentum
from ..labels import forward_returns
from . import POLICY_VERSION, EVALUATION_VERSION
from .data import LakeReader
from .universe import select_cohort, universe_counts, add_eligibility
from .evaluation import assign_quantiles, evaluate_assignments
from .coverage import coverage_tables

LIMITATIONS = [
    "Exploratory exchange-balanced historical starting cohort, not a full-market or rolling-IPO universe. Subsequent IPOs are excluded; no profitability claim.",
    "Retrospective 2026 instrument metadata is not original point-in-time data. Missing securities, symbol changes and revised dates can leave survivorship bias.",
    "BJ/predecessor histories and unknown listing dates are excluded. Delisting-date boundary is inclusive, not evidence of trading that day.",
    "Unknown status is retained as unknown, never assumed normal. Historical ST coverage is incomplete; suspension and execution availability are not established.",
    "No invented delisting returns, zero-fills, interpolation, raw-price fallback or bridging missing sessions. Missing outcomes can bias available-outcome averages and IC.",
    "HFQ prices use current stored factors; originally available vintages and economic accuracy remain unverified. M1 remains open.",
    "Close-to-close outcomes are not executable same-close strategy returns. No costs, slippage, limit-price fills or portfolio simulation.",
    "Overlapping forward windows are dependent. Means are descriptive, without independence-based significance tests.",
    "File hashes and stability checks aid reproducibility, but do not provide an atomic Data Lake snapshot.",
]


def run(config_path: Path):
    started = datetime.now(timezone.utc)
    config_bytes = config_path.read_bytes()
    cfg = tomllib.loads(config_bytes.decode("utf-8"))
    cohort = date.fromisoformat(cfg["cohort_date"])
    start, end = date.fromisoformat(cfg["evaluation_start"]), date.fromisoformat(cfg["evaluation_end"])
    if not cohort <= start <= end or (end-start).days>800 or cfg["per_exchange"] != 50 or cfg["lookback"] != 60 or cfg["horizon"] != 20 or cfg["quantiles"] != 3:
        raise ValueError("M3A requires approved bounded 100-stock 60/20/3 configuration")
    reader = LakeReader(Path(cfg["data_root"]))
    instruments = reader.instruments()
    candidates, selected = select_cohort(instruments, cohort, cfg["selection_seed"], cfg["per_exchange"])
    print(f"Selected {selected.height} from {candidates.height} historical candidates", flush=True)
    cal = reader.calendar(start.year-1, end.year+1)
    sessions = cal.filter(pl.col("is_trading"))["trade_date"].to_list()
    evaluation_days = [d for d in sessions if start<=d<=end]
    if not evaluation_days:
        raise ValueError("No evaluation sessions")
    begin = sessions.index(evaluation_days[0])-cfg["lookback"]
    finish = sessions.index(evaluation_days[-1])+cfg["horizon"]
    if begin<0 or finish>=len(sessions):
        raise ValueError("Calendar cannot supply warm-up and outcome extension")
    days = sessions[begin:finish+1]
    calendar_window = cal.filter(pl.col("trade_date").is_between(days[0],days[-1]))
    if calendar_window.height != (days[-1]-days[0]).days+1:
        raise ValueError("Calendar missing requested calendar dates")
    symbols = selected["symbol"].to_list()
    prices = reader.prices(symbols, days)
    print(f"Read {len(days)} sessions for {len(symbols)} symbols", flush=True)
    panel = add_eligibility(prices, selected)
    panel = panel.with_columns(pl.when(pl.col("listing_eligible")).then(pl.col("analytical_close")).otherwise(None).alias("analytical_close"))
    status = reader.status(symbols, days[0], days[-1])
    panel = panel.join(status,on=["symbol","trade_date"],how="left",validate="1:1").with_columns(pl.col("status_evidence").fill_null("missing_unknown"))
    factors = momentum(panel, cfg["lookback"])
    panel = panel.join(factors,on=["symbol","trade_date"],how="left",validate="1:1")
    day_to_i = {d:i for i,d in enumerate(days)}
    # Primary signal reasons use only current/past metadata and price history.
    panel = panel.with_columns(
        pl.when(~pl.col("listing_eligible")).then(pl.lit("not_listing_eligible"))
        .when(pl.col("status_evidence").is_in(["restricted","conflict"])).then(pl.lit("restricted_or_conflicting_status"))
        .when(pl.col("price_reason")!="ready").then(pl.col("price_reason"))
        .when(pl.col("trade_date").replace_strict(day_to_i,return_dtype=pl.Int64)<cfg["lookback"]).then(pl.lit("insufficient_loaded_warmup"))
        .when(pl.col("list_date") > pl.col("trade_date").replace_strict({d:days[max(0,i-cfg['lookback'])] for i,d in enumerate(days)},return_dtype=pl.Date)).then(pl.lit("insufficient_listing_history"))
        .when(pl.col("momentum").is_null()).then(pl.lit("broken_historical_price_window"))
        .otherwise(pl.lit("ready")).alias("signal_reason")
    )
    signals = panel.filter(pl.col("trade_date").is_between(start,end))
    # Freeze assignments BEFORE constructing or joining future outcomes.
    assignments = assign_quantiles(signals,cfg["quantiles"],cfg["min_cross_section"],cfg["min_bucket"])
    assignment_hash = sha256(json.dumps(assignments.to_dicts(),default=str,sort_keys=True).encode()).hexdigest()
    labels = forward_returns(panel,cfg["horizon"])
    end_dates = {d:days[i+cfg['horizon']] if i+cfg['horizon']<len(days) else None for i,d in enumerate(days)}
    labels = labels.join(panel.select("symbol","trade_date","delist_date"),on=["symbol","trade_date"],validate="1:1").with_columns(pl.col("trade_date").replace_strict(end_dates,return_dtype=pl.Date).alias("forward_end_date"))
    labels = labels.with_columns(((pl.col("delist_date")>pl.col("trade_date")) & (pl.col("delist_date")<=pl.col("forward_end_date"))).fill_null(False).alias("delisting_in_forward_window"))
    labels = labels.with_columns(pl.when(pl.col("forward_return").is_not_null()).then(pl.lit("valid")).when(pl.col("forward_end_date").is_null()).then(pl.lit("insufficient_future_sessions")).when(pl.col("delisting_in_forward_window")).then(pl.lit("missing_outcome_delisting_window")).otherwise(pl.lit("missing_or_invalid_future_price_window")).alias("label_reason")).drop("delist_date")
    labels = labels.filter(pl.col("trade_date").is_between(start,end))
    outcomes, metrics, daily_metrics = evaluate_assignments(assignments,labels,cfg["quantiles"],cfg["min_paired"],cfg["coverage_threshold"])
    ucounts = universe_counts(instruments,evaluation_days)
    coverage_daily, coverage_symbols, coverage = coverage_tables(panel,ucounts,start,end)
    ready = signals.filter(pl.col("signal_reason")=="ready").height
    coverage["ready_not_assigned_cross_section_thresholds"] = ready-assignments.height
    research = Path(__file__).resolve().parents[3]
    source_root = research / "src/ashare_research"
    sources = {str(p.relative_to(source_root)):sha256(p.read_bytes()).hexdigest() for p in sorted(source_root.rglob("*.py"))}
    selected_manifest = selected.select("symbol","exchange","list_date","delist_date","selection_hash").to_dicts()
    candidate_manifest = candidates.select("symbol","selection_hash").sort("symbol").to_dicts()
    reader.verify()
    experiment_id = "m3a-"+started.strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8]
    report = {
        "schema_version":"2.0", "experiment_id":experiment_id, "policy_version":POLICY_VERSION,"evaluation_version":EVALUATION_VERSION,
        "evaluation_period":{"start":str(start),"end":str(end),"sessions":len(evaluation_days)},
        "data_coverage":coverage,
        "universe_policy":{"type":"retrospective_listing_aware_fixed_historical_cohort","cohort_date":str(cohort),"markets":["SH","SZ"],"asset_type":"stock","unknown_listing":"exclude","delisting_boundary":"inclusive","status_policy":"exclude known restricted/conflicting; retain unknown as unknown","BJ_policy":"excluded including predecessor-market history","sampling":"50 per exchange, smallest SHA256(seed + NUL + symbol); no replacement for missing data","seed":cfg["selection_seed"],"candidate_count":candidates.height,"sample_count":selected.height,"sample_exchange_counts":selected.group_by("exchange").len().sort("exchange").to_dicts(),"sample_eventual_delistings":selected["delist_date"].count(),"sample_delistings_during_evaluation":selected.filter(pl.col("delist_date").is_between(start,end)).height,"candidate_manifest_sha256":sha256(json.dumps(candidate_manifest,sort_keys=True).encode()).hexdigest(),"sample_manifest":selected_manifest,"survivorship_bias_eliminated":False},
        "factor_parameters":{"name":"60_session_price_momentum","required_prices":61,"price_basis":"raw close multiplied by stored HFQ factor; every intervening session required"},
        "return_convention":{"horizon_sessions":20,"formula":"adjusted_close(t+20)/adjusted_close(t)-1","requires_complete_window":True,"execution_assumption":"none; not an executable same-close strategy"},
        "assignment_policy":{"quantiles":3,"ranks":"average ties; floor((rank-1)*3/n)+1","min_cross_section":cfg["min_cross_section"],"min_bucket":cfg["min_bucket"],"min_paired_ic":cfg["min_paired"],"primary_common_date_min_bucket_coverage":cfg["coverage_threshold"],"assignment_content_sha256":assignment_hash,"aggregation":"equal-stock means within signal-date bucket; equal-date means across available or primary-common dates, separately labelled"},
        "metrics":metrics,"daily_metrics":daily_metrics,"exclusions":coverage,"limitations":LIMITATIONS,
        "provenance":{"started_at_utc":started.isoformat(),"completed_at_utc":datetime.now(timezone.utc).isoformat(),"python_version":platform.python_version(),"dependency_versions":{p:version(p) for p in ["polars","ashare-lake"]},"configuration":cfg,"configuration_sha256":sha256(config_bytes).hexdigest(),"source_sha256":sources,"input_file_sha256":reader.hashes,"load_start":str(days[0]),"load_end":str(days[-1])},
    }
    dest = research/"artifacts"/experiment_id
    dest.mkdir(parents=True,exist_ok=False)
    for name,frame in {"sample_manifest":selected,"candidate_manifest":candidates,"observations":panel,"factors":factors,"labels":labels,"assignments":assignments,"outcomes":outcomes,"coverage_daily":coverage_daily,"coverage_symbols":coverage_symbols}.items():
        with (dest/(name+".parquet")).open("xb") as handle:
            frame.write_parquet(handle)
    with (dest/"results.json").open("x",encoding="utf-8") as handle:
        json.dump(report,handle,indent=2,default=str,allow_nan=False)
        handle.write("\n")
    text = f"""# M3A exploratory historical cohort

Experiment: `{experiment_id}`. Policy: `{POLICY_VERSION}`. Evaluation: `{EVALUATION_VERSION}`.

Period: {start}–{end}, {len(evaluation_days)} sessions. 100 stocks (50 SH / 50 SZ) selected from {candidates.height} metadata-eligible instruments on {cohort}. Seed: `{cfg['selection_seed']}`. No selection by later survival or data completeness; no replacements. BJ and unknown listing dates excluded.

Sample has {report['universe_policy']['sample_eventual_delistings']} recorded eventual delistings, {report['universe_policy']['sample_delistings_during_evaluation']} during evaluation. This is a fixed starting cohort, not the entire historical market.

60-session HFQ momentum uses 61 consecutive valid prices. Unknown status remains unknown; known restrictions/conflicts exclude the signal. Rank assignments are frozen before labels. Three buckets use average ranks; minimum cross-section 30 and minimum bucket size 5 for primary comparisons.

20-session HFQ close-to-close labels require every intervening price. No delisting outcomes are invented. Bucket means equally weight observed stocks within date, then dates. Primary comparisons use common dates with at least 90% outcome coverage in every bucket. IC uses available paired ranks with at least 30 pairs. Overlapping labels are dependent; no statistical significance or portfolio profit is claimed.

## Metrics and missing outcomes

```json
{json.dumps(metrics,indent=2)}
```

## Coverage and exclusions

```json
{json.dumps(coverage,indent=2)}
```

Full-universe counts are metadata only. Price/status coverage describes the selected cohort. See coverage_daily.parquet and coverage_symbols.parquet for denominators, missing histories, longest gaps and delisted-history diagnostics. observations.parquet retains reason codes and overlapping quality flags.

## Limitations

"""+"\n".join("- "+s for s in LIMITATIONS)+"\n\nConfiguration, timestamps, sample/candidate manifests, policy versions, source hashes and consumed-file hashes are recorded in results.json. Existing M2 results are unchanged.\n"
    with (dest/"report.md").open("x",encoding="utf-8") as handle:
        handle.write(text)
    print(json.dumps({"artifact_directory":str(dest),"sample_eventual_delistings":report['universe_policy']['sample_eventual_delistings'],"sample_delistings_during_evaluation":report['universe_policy']['sample_delistings_during_evaluation'],"coverage":coverage,"metrics":metrics},indent=2),flush=True)
    return dest,report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,required=True)
    run(parser.parse_args().config)


if __name__ == "__main__":
    main()
