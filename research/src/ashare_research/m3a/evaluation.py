"""Signal-first v1: assignments never accept future labels as an input."""
import math
import statistics

import polars as pl

KEYS = ["symbol", "trade_date"]


def assign_quantiles(signals, quantiles=3, min_cross_section=30, min_bucket=5):
    if quantiles < 2 or min_cross_section < quantiles or min_bucket < 1:
        raise ValueError("Invalid assignment policy")
    d = signals.select(*KEYS, "momentum", "signal_reason")
    if d.height != d.unique(KEYS).height:
        raise ValueError("Duplicate signal keys")
    outputs = []
    for g in d.partition_by("trade_date", maintain_order=True):
        eligible = g.filter(pl.col("signal_reason") == "ready")
        n = eligible.height
        distinct = eligible["momentum"].n_unique()
        if eligible.filter(pl.col("momentum").is_null() | ~pl.col("momentum").is_finite()).height:
            raise ValueError("Ready signal must have finite momentum")
        if n < min_cross_section or distinct < quantiles:
            continue
        ranked = eligible.with_columns(pl.col("momentum").rank(method="average").alias("rank"))
        ranked = ranked.with_columns((((pl.col("rank") - 1) * quantiles / n).floor().cast(pl.Int64) + 1).alias("bucket"), pl.lit(n).alias("cross_section_size"))
        sizes = ranked.group_by("bucket").len()
        suitable = sizes.height == quantiles and sizes["len"].min() >= min_bucket
        outputs.append(ranked.with_columns(pl.lit(suitable).alias("signal_comparison_eligible")))
    schema = {"symbol": pl.String, "trade_date": pl.Date, "momentum": pl.Float64, "signal_reason": pl.String, "rank": pl.Float64, "bucket": pl.Int64, "cross_section_size": pl.Int64, "signal_comparison_eligible": pl.Boolean}
    return pl.concat(outputs).sort(KEYS) if outputs else pl.DataFrame(schema=schema)


def summarize_outcomes(g):
    assigned = g.height
    valid = g["forward_return"].count()
    missing = assigned - valid
    crossing = g.filter(pl.col("delisting_in_forward_window").fill_null(False))
    return {"assigned": assigned, "valid": valid, "missing": missing, "missing_rate": missing / assigned if assigned else None, "coverage": valid / assigned if assigned else None, "delisting_window_assigned": crossing.height, "delisting_window_missing": crossing["forward_return"].null_count()}


def evaluate_assignments(assignments, labels, quantiles=3, min_paired=30, coverage_threshold=0.9):
    if labels.height != labels.unique(KEYS).height:
        raise ValueError("Duplicate label keys")
    if labels.filter(pl.col("forward_return").is_not_null() & ~pl.col("forward_return").is_finite()).height:
        raise ValueError("Invalid nonfinite label; convert to reason-coded missing first")
    joined = assignments.join(labels, on=KEYS, how="left", validate="1:1").with_columns(pl.col("label_reason").fill_null("label_record_absent"), pl.col("delisting_in_forward_window").fill_null(False))
    daily = []
    for key, g in joined.group_by("trade_date", maintain_order=True):
        paired = g.drop_nulls("forward_return")
        ic = paired.select(pl.corr("momentum", "forward_return", method="spearman")).item() if paired.height >= min_paired else None
        ic = float(ic) if ic is not None and math.isfinite(ic) else None
        buckets = {}
        for q in range(1, quantiles + 1):
            b = g.filter(pl.col("bucket") == q)
            buckets[str(q)] = {**summarize_outcomes(b), "mean_return": b["forward_return"].mean()}
        comparable = bool(g["signal_comparison_eligible"].all()) and all(b["coverage"] is not None and b["coverage"] >= coverage_threshold for b in buckets.values())
        daily.append({"trade_date": str(key[0]), "rank_ic": ic, "paired_count": paired.height, "paired_coverage": paired.height / g.height, "primary_comparison": comparable, "buckets": buckets})
    daily.sort(key=lambda r: r["trade_date"])
    ics = [r["rank_ic"] for r in daily if r["rank_ic"] is not None]
    primary = [r for r in daily if r["primary_comparison"]]
    def avg(values):
        return statistics.mean(values) if values else None
    buckets = {}
    for q in range(1, quantiles + 1):
        b = joined.filter(pl.col("bucket") == q)
        buckets[str(q)] = {**summarize_outcomes(b), "available_outcome_mean": avg([r["buckets"][str(q)]["mean_return"] for r in daily if r["buckets"][str(q)]["mean_return"] is not None]), "available_outcome_dates": sum(r["buckets"][str(q)]["mean_return"] is not None for r in daily), "primary_common_date_mean": avg([r["buckets"][str(q)]["mean_return"] for r in primary])}
    rates = [b["missing_rate"] for b in buckets.values() if b["missing_rate"] is not None]
    metrics = {"mean_daily_rank_ic": avg(ics), "valid_ic_dates": len(ics), "assigned_dates": len(daily), "primary_comparison_dates": len(primary), "dates_failing_primary_coverage_or_bucket_size": len(daily)-len(primary), "outcomes": summarize_outcomes(joined), "buckets": buckets, "bucket_missing_rate_range": max(rates)-min(rates) if rates else None, "primary_high_minus_low": avg([r["buckets"][str(quantiles)]["mean_return"]-r["buckets"]["1"]["mean_return"] for r in primary]), "missing_label_reasons": joined.filter(pl.col("forward_return").is_null()).group_by("label_reason").len().to_dicts(), "inference": "Descriptive only; overlapping 20-session outcomes are dependent. No t-statistics, p-values, annualized or compounded portfolio performance."}
    return joined, metrics, daily
