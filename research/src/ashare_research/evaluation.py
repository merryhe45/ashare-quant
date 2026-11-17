import math
import polars as pl


def evaluate(factors, labels, start, end, quantiles=3):
    if quantiles < 2:
        raise ValueError("Require at least two quantiles")
    d = factors.join(labels, on=["symbol", "trade_date"], how="left").filter(pl.col("trade_date").is_between(pl.lit(start).str.to_date(), pl.lit(end).str.to_date()))
    exclusions = {"evaluation_grid_rows": d.height, "missing_factor": d["momentum"].null_count(), "missing_forward_label": d["forward_return"].null_count()}
    rows = []
    for group in d.partition_by("trade_date", maintain_order=True):
        valid = group.drop_nulls(["momentum", "forward_return"]).sort("momentum", "symbol")
        n = valid.height
        if n < quantiles:
            rows.append({"date": str(group["trade_date"][0]), "observations": n, "rank_ic": None, "quantiles": {}})
            continue
        ic = valid.select(pl.corr("momentum", "forward_return", method="spearman")).item()
        ic = float(ic) if ic is not None and math.isfinite(ic) else None
        # Average ranks keep tied factor values together; buckets can be empty.
        valid = valid.with_columns((((pl.col("momentum").rank(method="average") - 1) * quantiles / n).floor().cast(pl.Int64) + 1).alias("q"))
        qs = {str(q): {"mean_return": g["forward_return"].mean(), "observations": g.height} for (q,), g in valid.group_by("q")}
        rows.append({"date": str(group["trade_date"][0]), "observations": n, "rank_ic": ic, "quantiles": qs})
    ics = [x["rank_ic"] for x in rows if x["rank_ic"] is not None]
    qs = {}
    for q in range(1, quantiles + 1):
        vals = [x["quantiles"][str(q)]["mean_return"] for x in rows if str(q) in x["quantiles"]]
        qs[str(q)] = {"mean_daily_cross_sectional_forward_return": sum(vals)/len(vals) if vals else None, "dates": len(vals)}
    exclusions["dates_below_minimum_cross_section"] = sum(x["observations"] < quantiles for x in rows)
    return {"mean_daily_rank_ic": sum(ics)/len(ics) if ics else None, "valid_ic_dates": len(ics), "quantile_returns": qs, "daily": rows, "inference": "Descriptive date-level averages only; overlapping 20-session outcomes are dependent. No t-statistic, p-value, annualization or compounded portfolio return."}, exclusions
