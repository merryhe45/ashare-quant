"""Additional amount reads using the existing M3A reader and fingerprints."""
import polars as pl


def load_amounts(reader, symbols, days):
    chunks = []
    cols = ["symbol", "trade_date", "source", "amount", "volume", "low", "high", "close"]
    schema = {"symbol": pl.String, "trade_date": pl.Date, "source": pl.String, **{c: pl.Float64 for c in ["amount", "volume", "low", "high", "close"]}}
    for day in days:
        files = sorted((reader.root / "curated/daily_bars" / f"trade_date={day}").glob("*.parquet"))
        frames = [reader.read(p, cols).filter(pl.col("symbol").is_in(symbols)) for p in files]
        d = pl.concat(frames) if frames else pl.DataFrame(schema=schema)
        if d.height != d.unique(["symbol", "trade_date"]).height or d.filter(pl.col("trade_date") != day).height:
            raise ValueError("Invalid amount keys/partition")
        grid = pl.DataFrame({"symbol": symbols}).with_columns(pl.lit(day).alias("trade_date"))
        chunks.append(grid.join(d, on=["symbol", "trade_date"], how="left"))
    return pl.concat(chunks).sort("symbol", "trade_date")


def audit_amounts(panel, min_unit_rows=20, unit_pass_fraction=0.99):
    """Validate the complete loaded sample before enabling liquidity.

    Only TDX/Baostock are predeclared candidate yuan/share sources. Each must
    pass raw VWAP range checks, independently by calendar year. Unexplained
    zeros and other invalid rows are null, not fabricated or price-adjusted.
    """
    d = panel.with_columns(pl.col("trade_date").dt.year().alias("year"))
    known = pl.col("source").is_in(["tdx_protocol", "baostock"]).fill_null(False)
    amount = pl.col("amount")
    measurable = known & amount.is_not_null() & amount.is_finite() & (amount > 0) & pl.col("volume").is_finite() & (pl.col("volume") > 0) & (pl.col("low") > 0) & (pl.col("high") >= pl.col("low")) & pl.col("low").is_finite() & pl.col("high").is_finite()
    vwap_ok = ((amount/pl.col("volume") >= pl.col("low")*0.99) & (amount/pl.col("volume") <= pl.col("high")*1.01)).fill_null(False)
    reason = pl.when(pl.col("source").is_null()).then(pl.lit("missing_bar")).when(amount.is_null()).then(pl.lit("missing_amount")).when(~amount.is_finite()).then(pl.lit("nonfinite_amount")).when(amount < 0).then(pl.lit("negative_amount")).when(amount == 0).then(pl.lit("unexplained_zero_amount")).when(~known).then(pl.lit("unknown_unit_source")).when(~measurable.fill_null(False)).then(pl.lit("unit_check_unavailable")).when(~vwap_ok).then(pl.lit("unit_check_failed")).otherwise(pl.lit("ready"))
    d = d.with_columns(reason.alias("amount_reason"), measurable.fill_null(False).alias("unit_measurable"), vwap_ok.alias("vwap_in_range"))
    audit = d.filter(pl.col("listing_eligible"))
    checks = []
    reasons = []
    for key, group in audit.filter(pl.col("unit_measurable")).group_by("source", "year"):
        count = group.height
        fraction = group["vwap_in_range"].mean()
        passed = count >= min_unit_rows and fraction >= unit_pass_fraction
        checks.append({"source": key[0], "year": key[1], "positive_unit_check_rows": count, "pass_fraction": fraction, "passed": passed})
        if not passed:
            reasons.append(f"Inconclusive/inconsistent units for {key[0]} {key[1]}")
    positive_sources = audit.filter(pl.col("amount").is_not_null() & pl.col("amount").is_finite() & (pl.col("amount")>0))["source"].drop_nulls().unique().to_list()
    checked_sources = {r["source"] for r in checks}
    for source in positive_sources:
        if source not in checked_sources:
            reasons.append(f"No passing unit evidence for {source}")
    if not checks:
        reasons.append("No measurable unit evidence")
    # Even null/zero values from an unapproved provider do not become trusted.
    unknown = audit.filter(pl.col("source").is_not_null() & ~pl.col("source").is_in(["tdx_protocol", "baostock"]))
    if unknown.height:
        reasons.append("Unknown-unit source present; liquidity disabled pending validation")
    enabled = not reasons
    d = d.with_columns(pl.when((pl.col("amount_reason")=="ready") & pl.col("listing_eligible") & pl.lit(enabled)).then(pl.col("amount")).otherwise(None).alias("valid_amount"))
    result = {"enabled": enabled, "disabled_reasons": reasons, "scope": "All eligible stock/session slots over full loaded warm-up, evaluation and label-extension period", "units": "CNY per session; raw traded amount, never price-adjusted", "allowed_sources": ["tdx_protocol", "baostock"], "unit_check": "amount/volume against raw low/high with 1% tolerance, source/year checks", "min_unit_rows": min_unit_rows, "min_unit_pass_fraction": unit_pass_fraction, "source_year_checks": sorted(checks, key=lambda r:(r["source"],r["year"])), "eligible_rows": audit.height, "amount_reasons": audit.group_by("amount_reason").len().sort("amount_reason").to_dicts(), "source_coverage": audit.group_by("source").agg(pl.len().alias("rows"), pl.col("amount").null_count().alias("missing"), (~pl.col("amount").is_finite()).fill_null(False).sum().alias("nonfinite"), (pl.col("amount")<0).sum().alias("negative"), (pl.col("amount")==0).sum().alias("unexplained_zero")).sort("source").to_dicts(), "zero_policy": "All zeros conservatively rejected as unexplained; no reliable historical non-trading evidence available"}
    return d.drop("year"), result
