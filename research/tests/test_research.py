import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import polars as pl
from ashare_research.data import load_prices
from ashare_research.factors import momentum
from ashare_research.labels import forward_returns
from ashare_research.evaluation import evaluate


def prices(n=100):
    return pl.DataFrame({"symbol": ["A"] * n + ["B"] * n, "trade_date": [date(2025, 1, 1) + timedelta(days=i) for i in range(n)] * 2, "analytical_close": [100.0+i for i in range(n)] + [1000.0-2*i for i in range(n)]})


class ResearchTests(unittest.TestCase):
    def test_future_append_does_not_change_factors(self):
        full = prices(100)
        cut = date(2025, 3, 15)
        before = momentum(full.filter(pl.col("trade_date") <= cut))
        after = momentum(full).filter(pl.col("trade_date") <= cut)
        self.assertTrue(before.equals(after))
        self.assertNotIn("forward_return", after.columns)

    def test_insufficient_lookback_and_formula(self):
        m = momentum(prices()).filter(pl.col("symbol") == "A")
        self.assertEqual(m["momentum"][:60].null_count(), 60)
        self.assertAlmostEqual(m["momentum"][60], 0.6)

    def test_missing_session_invalidates_window(self):
        p = prices().with_columns(pl.when((pl.col("symbol") == "A") & (pl.col("trade_date") == date(2025, 1, 21))).then(None).otherwise(pl.col("analytical_close")).alias("analytical_close"))
        a = momentum(p).filter(pl.col("symbol") == "A")
        self.assertIsNone(a["momentum"][80])
        self.assertIsNotNone(a["momentum"][81])
        labels = forward_returns(p).filter(pl.col("symbol") == "A")
        self.assertIsNone(labels["forward_return"][0])

    def test_incomplete_forward_returns(self):
        f = forward_returns(prices()).filter(pl.col("symbol") == "A")["forward_return"]
        self.assertEqual(f[-20:].null_count(), 20)
        self.assertAlmostEqual(f[0], 0.2)

    def test_cross_symbol_isolation(self):
        p = prices()
        for fn, col in [(momentum, "momentum"), (forward_returns, "forward_return")]:
            together = fn(p).filter(pl.col("symbol") == "B")
            alone = fn(p.filter(pl.col("symbol") == "B"))
            self.assertTrue(together.equals(alone))
            self.assertEqual(together[col].count(), 40 if col == "momentum" else 80)

    def test_rank_ic_and_quantiles(self):
        day = date(2025, 1, 1)
        factors = pl.DataFrame({"symbol": ["A", "B", "C"], "trade_date": [day]*3, "momentum": [1., 2., 3.]})
        labels = factors.select("symbol", "trade_date").with_columns(pl.Series("forward_return", [0.3, 0.2, 0.1]))
        result, _ = evaluate(factors, labels, str(day), str(day))
        self.assertAlmostEqual(result["mean_daily_rank_ic"], -1)
        self.assertAlmostEqual(result["quantile_returns"]["1"]["mean_daily_cross_sectional_forward_return"], 0.3)

    def test_constant_factor_ic_is_undefined(self):
        day = date(2025, 1, 1)
        f = pl.DataFrame({"symbol": ["A", "B", "C"], "trade_date": [day]*3, "momentum": [1., 1., 1.]})
        labels = f.select("symbol", "trade_date").with_columns(pl.Series("forward_return", [0.1, 0.2, 0.3]))
        result, _ = evaluate(f, labels, str(day), str(day))
        self.assertIsNone(result["mean_daily_rank_ic"])

    def test_adapter_missing_adjustment_and_missing_bar(self):
        # Fixture writes are confined to research/artifacts; lake files untouched.
        artifacts = Path(__file__).resolve().parents[1] / "artifacts"
        with tempfile.TemporaryDirectory(dir=artifacts) as folder:
            r = Path(folder)
            day1, day2 = date(2025, 1, 1), date(2025, 1, 2)
            cal = r / "curated/trading_calendar/trade_date=2025"
            cal.mkdir(parents=True)
            pl.DataFrame({"trade_date": [day1, day2], "is_trading": [True, True]}).write_parquet(cal / "part.parquet")
            bars = r / f"curated/daily_bars/trade_date={day1}"
            bars.mkdir(parents=True)
            pl.DataFrame({"symbol": ["A"], "trade_date": [day1], "open": [10.], "high": [11.], "low": [9.], "close": [10.]}).write_parquet(bars / "part.parquet")
            p, exclusions, _ = load_prices(r, ["A"], str(day1), str(day2))
            self.assertEqual(p["analytical_close"].null_count(), 2)
            self.assertEqual(exclusions["missing_adjustment"], 1)
            self.assertEqual(exclusions["missing_bar"], 1)

    def test_adapter_adjusts_prices_and_rejects_invalid_ohlc(self):
        artifacts = Path(__file__).resolve().parents[1] / "artifacts"
        with tempfile.TemporaryDirectory(dir=artifacts) as folder:
            r = Path(folder)
            day = date(2025, 1, 1)
            cal = r / "curated/trading_calendar/trade_date=2025"
            bars = r / f"curated/daily_bars/trade_date={day}"
            adj = r / f"derived/adj_factors/trade_date={day}"
            for p in [cal, bars, adj]:
                p.mkdir(parents=True)
            pl.DataFrame({"trade_date": [day], "is_trading": [True]}).write_parquet(cal / "part.parquet")
            pl.DataFrame({"symbol": ["A", "B"], "trade_date": [day]*2, "open": [10., 0.], "high": [11., 11.], "low": [9., 0.], "close": [10., 10.]}).write_parquet(bars / "part.parquet")
            pl.DataFrame({"symbol": ["A", "B"], "trade_date": [day]*2, "factor": [2., 2.], "adjust_type": ["hfq"]*2}).write_parquet(adj / "part.parquet")
            p, exclusions, _ = load_prices(r, ["A", "B"], str(day), str(day))
            self.assertEqual(p.filter(pl.col("symbol") == "A")["analytical_close"][0], 20.)
            self.assertIsNone(p.filter(pl.col("symbol") == "B")["analytical_close"][0])
            self.assertEqual(exclusions["invalid_ohlc"], 1)


if __name__ == "__main__":
    unittest.main()
