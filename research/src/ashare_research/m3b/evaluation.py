"""Generic bridge to unchanged signal_first_v1 evaluation."""
import polars as pl

from ..m3a.evaluation import assign_quantiles, evaluate_assignments

KEYS = ["symbol", "trade_date"]


def matched_keys(factor_tables: dict[str, pl.DataFrame]) -> pl.DataFrame:
    if not factor_tables:
        raise ValueError("No enabled factors")
    common = None
    for table in factor_tables.values():
        ready = table.filter(pl.col("factor_reason") == "ready").select(KEYS)
        common = ready if common is None else common.join(ready, on=KEYS, how="inner", validate="1:1")
    return common.sort(KEYS)


def freeze_assignments(table, cfg, common=None):
    d = table if common is None else table.join(common, on=KEYS, how="semi")
    d = d.rename({"factor_value": "momentum", "factor_reason": "signal_reason"})
    assigned = assign_quantiles(d, cfg["quantiles"], cfg["min_cross_section"], cfg["min_bucket"])
    return assigned.rename({"momentum": "factor_value"})


def evaluate(assigned, labels, cfg):
    joined, metrics, daily = evaluate_assignments(assigned.rename({"factor_value": "momentum"}), labels, cfg["quantiles"], cfg["min_paired"], cfg["coverage_threshold"])
    return joined.rename({"momentum": "factor_value"}), metrics, daily
