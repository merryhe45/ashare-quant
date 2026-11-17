"""Coverage denominators remain independent of observed-price availability."""
from collections import Counter

import polars as pl


def coverage_tables(panel, universe_daily, start, end):
    d = panel.filter(pl.col("trade_date").is_between(start, end))
    rows = []
    for key, group in d.group_by("trade_date", maintain_order=True):
        eligible = group.filter(pl.col("listing_eligible"))
        row = {"trade_date": key[0], "selected_slots": group.height, "sample_listing_eligible": eligible.height, "sample_not_listing_eligible": group.height-eligible.height, "sample_valid_adjusted_prices": eligible["analytical_close"].count(), "sample_unknown_status": eligible.filter(pl.col("status_evidence").is_in(["missing_unknown", "unknown_record"])).height, "sample_restricted_status": eligible.filter(pl.col("status_evidence") == "restricted").height, "sample_conflicting_status": eligible.filter(pl.col("status_evidence") == "conflict").height, "sample_explicit_normal": eligible.filter(pl.col("status_evidence") == "explicit_normal").height}
        for reason in ["missing_bar", "invalid_ohlc", "missing_adjustment", "invalid_adjustment", "invalid_adjusted_price"]:
            row["sample_" + reason] = eligible.filter(pl.col("price_reason") == reason).height
        for flag in ["invalid_ohlc_flag", "missing_adjustment_flag", "invalid_adjustment_flag"]:
            row["sample_" + flag] = eligible.filter(pl.col(flag)).height
        row["sample_ready_signals"] = group.filter(pl.col("signal_reason") == "ready").height
        rows.append(row)
    daily = pl.DataFrame(rows).join(universe_daily, on="trade_date", how="left", validate="1:1").sort("trade_date")
    symbol_rows = []
    for key, g in d.sort("symbol", "trade_date").group_by("symbol", maintain_order=True):
        eligible = g.filter(pl.col("listing_eligible"))
        flags = eligible["analytical_close"].is_not_null().to_list()
        present = [i for i, flag in enumerate(flags) if flag]
        run = longest = 0
        for flag in flags:
            run = 0 if flag else run + 1
            longest = max(longest, run)
        internal = 0
        current = 0
        if present:
            for flag in flags[present[0]:present[-1]+1]:
                current = 0 if flag else current+1
                internal = max(internal, current)
        delist = g["delist_date"][0]
        symbol_rows.append({"symbol": key[0], "list_date": g["list_date"][0], "delist_date": delist, "eligible_sessions": eligible.height, "valid_prices": sum(flags), "missing_or_invalid_sessions": len(flags)-sum(flags), "price_coverage": sum(flags)/len(flags) if flags else None, "first_valid_price": eligible.filter(pl.col("analytical_close").is_not_null())["trade_date"].min(), "last_valid_price": eligible.filter(pl.col("analytical_close").is_not_null())["trade_date"].max(), "longest_missing_run_sessions": longest, "longest_internal_gap_sessions": internal, "terminal_missing_sessions": len(flags)-1-present[-1] if present else len(flags), "eventual_delisting_recorded": delist is not None, "delisting_during_evaluation": delist is not None and start<=delist<=end, "delisted_incomplete_history": delist is not None and len(flags)>sum(flags)})
    symbols = pl.DataFrame(symbol_rows, schema_overrides={"list_date": pl.Date, "delist_date": pl.Date, "first_valid_price": pl.Date, "last_valid_price": pl.Date})
    summary = {"scope": "Full SH/SZ metadata listing counts; price/status coverage is selected sample only", "evaluation_grid_slots": d.height, "sample_eligible_observations": d.filter(pl.col("listing_eligible")).height, "signal_reasons": dict(Counter(d["signal_reason"])), "eligible_price_reasons": dict(Counter(d.filter(pl.col("listing_eligible"))["price_reason"])), "eligible_status_evidence": dict(Counter(d.filter(pl.col("listing_eligible"))["status_evidence"])), "delisted_with_incomplete_evaluation_history": symbols.filter(pl.col("delisted_incomplete_history")).height, "signal_exclusion_note": "Primary reasons are exclusive; diagnostic flags overlap. Ready signals can still be excluded by cross-sectional assignment thresholds."}
    return daily, symbols, summary
