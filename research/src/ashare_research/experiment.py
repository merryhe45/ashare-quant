"""Bounded local experiments with immutable output directories."""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import tomllib
from uuid import uuid4
import polars as pl

from . import __version__
from .data import load_prices
from .factors import momentum
from .labels import forward_returns
from .evaluation import evaluate

LIMITATIONS = [
    "Exploratory fixed-stock sample; not representative of the full A-share market and does not eliminate survivorship bias.",
    "M1 remains open: missing delisted histories, incomplete tradability/ST evidence, adjustment defects and missing amount data remain lake-wide limitations.",
    "No historical membership reconstruction, execution, costs, slippage, suspensions or price-limit fill model. No investment profitability claim.",
    "Any missing or invalid analytical price invalidates every intersecting factor/label window; no gap is silently bridged.",
    "HFQ factors are current stored vintages, not certified as originally available at each historical date; corrections can revise history.",
    "Overlapping forward returns are dependent. Quantile results are descriptive event outcomes, not portfolio performance.",
    "Input hashes establish local reproducibility, not independent source accuracy or corporate-action correctness.",
]


def run(config_path: Path):
    started = datetime.now(timezone.utc)
    config_bytes = config_path.read_bytes()
    cfg = tomllib.loads(config_bytes.decode("utf-8"))
    if not cfg["load_start"] <= cfg["evaluation_start"] <= cfg["evaluation_end"] <= cfg["load_end"]:
        raise ValueError("Evaluation period must lie within load period")
    prices, exclusions, hashes = load_prices(Path(cfg["data_root"]), cfg["symbols"], cfg["load_start"], cfg["load_end"])
    # Independent computations. Labels are joined only inside evaluation.
    factors = momentum(prices, cfg["lookback"])
    labels = forward_returns(prices, cfg["horizon"])
    metrics, eval_exclusions = evaluate(factors, labels, cfg["evaluation_start"], cfg["evaluation_end"], cfg["quantiles"])
    experiment_id = started.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    research = Path(__file__).resolve().parents[2]
    code_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))}
    report = {
        "schema_version": "1.0", "experiment_id": experiment_id,
        "evaluation_period": {"start": cfg["evaluation_start"], "end": cfg["evaluation_end"]},
        "data_coverage": {"load_start": cfg["load_start"], "load_end": cfg["load_end"], "calendar_grid_rows": prices.height, "valid_price_rows": prices["analytical_close"].count(), "per_symbol": prices.group_by("symbol").agg(pl.col("analytical_close").count().alias("valid_prices")).sort("symbol").to_dicts()},
        "universe_policy": {"type": "exploratory_fixed_stock_sample", "symbols": cfg["symbols"], "historical_membership_filter": False, "representative_of_full_market": False, "survivorship_bias_eliminated": False},
        "factor_parameters": {"name": "price_momentum", "lookback_sessions": cfg["lookback"], "formula": "adjusted_close(t)/adjusted_close(t-lookback)-1", "required_prices": cfg["lookback"] + 1},
        "return_convention": {"horizon_sessions": cfg["horizon"], "formula": "adjusted_close(t+horizon)/adjusted_close(t)-1", "execution_assumption": "None: same-close origin is an outcome label, not an executable close-based strategy", "requires_complete_window": True},
        "metrics": metrics, "exclusions": {"price_quality": exclusions, "evaluation": eval_exclusions, "counting_note": "Reasons overlap; missing factor includes warm-up and invalid-window effects. Missing labels include immature future windows and invalid-window effects."},
        "limitations": LIMITATIONS,
        "provenance": {"started_at_utc": started.isoformat(), "completed_at_utc": datetime.now(timezone.utc).isoformat(), "package_version": __version__, "dependencies": {p: version(p) for p in ["polars", "ashare-lake"]}, "configuration": cfg, "configuration_sha256": hashlib.sha256(config_bytes).hexdigest(), "source_sha256": code_hashes, "input_file_sha256": hashes},
    }
    destination = research / "artifacts" / experiment_id
    destination.mkdir(parents=True, exist_ok=False)
    # Exclusive creation prevents overwriting any existing artifact.
    for name, frame in [("factors", factors), ("labels", labels)]:
        with (destination / f"{name}.parquet").open("xb") as handle:
            frame.write_parquet(handle)
    with (destination / "results.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    text = f"""# Exploratory momentum research

Experiment: `{experiment_id}`

Evaluation period: {cfg['evaluation_start']} to {cfg['evaluation_end']}.

Universe policy: fixed list of {len(cfg['symbols'])} stocks; exploratory, not representative of the full A-share market; survivorship bias is not eliminated.

Factor parameters: {cfg['lookback']}-session HFQ price momentum; every intervening session required.

Return convention: {cfg['horizon']}-session adjusted close-to-close outcome, computed separately from factors. This is not a simulated trade return.

Data coverage: {prices.height} stock-session slots, {prices['analytical_close'].count()} valid adjusted prices.

## Metrics

Mean daily Spearman rank IC: {metrics['mean_daily_rank_ic']}; valid IC dates: {metrics['valid_ic_dates']}.

Quantile returns (equal weight within date, equal weight across available dates):

{json.dumps(metrics['quantile_returns'], indent=2)}

Overlapping outcomes are dependent. No independence-based inference or profitability conclusion is made.

## Exclusions

{json.dumps(report['exclusions'], indent=2)}

## Limitations

""" + "\n".join("- " + x for x in LIMITATIONS) + "\n\nReproducibility: configuration, versions, UTC timestamps, source hashes and input-file hashes are recorded in `results.json`. Factors and labels are stored separately.\n"
    (destination / "report.md").write_text(text, encoding="utf-8")
    return destination, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    destination, report = run(args.config)
    print(json.dumps({"artifact_directory": str(destination), "mean_daily_rank_ic": report["metrics"]["mean_daily_rank_ic"], "valid_ic_dates": report["metrics"]["valid_ic_dates"], "quantile_returns": report["metrics"]["quantile_returns"], "exclusions": report["exclusions"]}, indent=2))


if __name__ == "__main__":
    main()
