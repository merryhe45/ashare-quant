"""Bounded direct file reads, reason-coded prices and explicit status evidence."""
from datetime import date
from pathlib import Path
from hashlib import sha256

import polars as pl


class LakeReader:
    def __init__(self, root: Path):
        self.root = root
        self.hashes = {}

    def read(self, path, columns=None):
        before = sha256(path.read_bytes()).hexdigest()
        d = pl.read_parquet(path, columns=columns, hive_partitioning=False)
        if sha256(path.read_bytes()).hexdigest() != before:
            raise ValueError(f"Input changed during read: {path}")
        key = str(path.relative_to(self.root))
        if key in self.hashes and self.hashes[key] != before:
            raise ValueError(f"Input changed within experiment: {path}")
        self.hashes[key] = before
        return d

    def verify(self):
        for key, digest in self.hashes.items():
            if sha256((self.root / key).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Input changed before completion: {key}")

    def instruments(self):
        files = sorted((self.root / "curated/instruments").glob("*.parquet"))
        if not files:
            raise ValueError("No instruments")
        return pl.concat([self.read(p) for p in files])

    def calendar(self, start_year, end_year):
        files = sorted((self.root / "curated/trading_calendar").glob("trade_date=*/*.parquet"))
        files = [p for p in files if start_year <= int(p.parent.name.split("=")[1]) <= end_year]
        if not files:
            raise ValueError("No calendar")
        d = pl.concat([self.read(p, ["trade_date", "is_trading"]) for p in files]).sort("trade_date")
        if d.height != d["trade_date"].n_unique() or d.null_count().sum_horizontal().item():
            raise ValueError("Duplicate/null calendar")
        return d

    def prices(self, symbols: list[str], days: list[date]):
        if not 0 < len(symbols) <= 100 or len(set(symbols)) != len(symbols) or len(days) > 800:
            raise ValueError("M3A bounded sample limits exceeded")
        chunks = []
        for day in days:
            def partition(layer, columns, empty_schema):
                files = sorted((self.root / layer / f"trade_date={day}").glob("*.parquet"))
                return pl.concat([self.read(p, columns).filter(pl.col("symbol").is_in(symbols)) for p in files]) if files else pl.DataFrame(schema=empty_schema)

            bars = partition("curated/daily_bars", ["symbol", "trade_date", "open", "high", "low", "close"], {"symbol": pl.String, "trade_date": pl.Date, **{c: pl.Float64 for c in ["open", "high", "low", "close"]}})
            adj = partition("derived/adj_factors", ["symbol", "trade_date", "factor", "adjust_type"], {"symbol": pl.String, "trade_date": pl.Date, "factor": pl.Float64, "adjust_type": pl.String}).filter(pl.col("adjust_type") == "hfq")
            for d in (bars, adj):
                if d.height != d.unique(["symbol", "trade_date"]).height or d.filter(pl.col("trade_date").is_null() | (pl.col("trade_date") != day)).height:
                    raise ValueError("Duplicate or mispartitioned price/adjustment records")
            grid = pl.DataFrame({"symbol": symbols}).with_columns(pl.lit(day).alias("trade_date"))
            b = bars.with_columns(pl.lit(True).alias("bar_present"))
            d = grid.join(b, on=["symbol", "trade_date"], how="left").join(adj.select("symbol", "trade_date", "factor"), on=["symbol", "trade_date"], how="left")
            good_price = pl.all_horizontal([pl.col(c).is_not_null() & pl.col(c).is_finite() & (pl.col(c) > 0) for c in ["open", "high", "low", "close"]]) & (pl.col("high") >= pl.max_horizontal("open", "low", "close")) & (pl.col("low") <= pl.min_horizontal("open", "high", "close"))
            good_factor = pl.col("factor").is_not_null() & pl.col("factor").is_finite() & (pl.col("factor") > 0)
            product = pl.col("close") * pl.col("factor")
            reason = pl.when(pl.col("bar_present").is_null()).then(pl.lit("missing_bar")).when(~good_price).then(pl.lit("invalid_ohlc")).when(pl.col("factor").is_null()).then(pl.lit("missing_adjustment")).when(~good_factor).then(pl.lit("invalid_adjustment")).when(~product.is_finite() | (product <= 0)).then(pl.lit("invalid_adjusted_price")).otherwise(pl.lit("ready"))
            d = d.with_columns(reason.alias("price_reason"), (~good_price).alias("invalid_ohlc_flag"), (pl.col("factor").is_null()).alias("missing_adjustment_flag"), (pl.col("factor").is_not_null() & ~good_factor).alias("invalid_adjustment_flag"))
            chunks.append(d.with_columns(pl.when(pl.col("price_reason") == "ready").then(product).otherwise(None).alias("analytical_close")).select("symbol", "trade_date", "analytical_close", "price_reason", "invalid_ohlc_flag", "missing_adjustment_flag", "invalid_adjustment_flag"))
        return pl.concat(chunks).sort("symbol", "trade_date")

    def status(self, symbols, start, end):
        files = sorted((self.root / "curated/trading_status").glob("trade_date=*/*.parquet"))
        files = [p for p in files if str(start)[:7] <= p.parent.name.split("=")[1] <= str(end)[:7]]
        schema = {"symbol": pl.String, "trade_date": pl.Date, "status_evidence": pl.String}
        frames = [self.read(p, ["symbol", "trade_date", "status", "is_trading"]).filter(pl.col("symbol").is_in(symbols) & pl.col("trade_date").is_between(start, end)) for p in files]
        if not frames:
            return pl.DataFrame(schema=schema)
        d = pl.concat(frames)
        rows = []
        for key, g in d.group_by("symbol", "trade_date"):
            restricted = g.filter((pl.col("is_trading") == False) | pl.col("status").str.to_lowercase().is_in(["st", "*st", "suspended"]))
            normal = g.filter((pl.col("is_trading") == True) & (pl.col("status").str.to_lowercase() == "normal"))
            evidence = "conflict" if restricted.height and normal.height else "restricted" if restricted.height else "explicit_normal" if normal.height == g.height else "unknown_record"
            rows.append({"symbol": key[0], "trade_date": key[1], "status_evidence": evidence})
        return pl.DataFrame(rows, schema=schema)
