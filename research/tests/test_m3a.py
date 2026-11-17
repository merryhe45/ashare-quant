from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import unittest
import tempfile

import polars as pl

from ashare_research.evaluation import evaluate as evaluate_m2
from ashare_research.m3a.universe import eligibility_reason, select_cohort, universe_counts
from ashare_research.m3a.evaluation import assign_quantiles, evaluate_assignments
from ashare_research.m3a.coverage import coverage_tables
from ashare_research.m3a.data import LakeReader


DAY = date(2020, 1, 2)


def signals(n=36):
    return pl.DataFrame({"symbol":[f"S{i:03}" for i in range(n)],"trade_date":[DAY]*n,"momentum":[float(i) for i in range(n)],"signal_reason":["ready"]*n})


def labels(n=36):
    return pl.DataFrame({"symbol":[f"S{i:03}" for i in range(n)],"trade_date":[DAY]*n,"forward_return":[i/100 for i in range(n)],"label_reason":["valid"]*n,"delisting_in_forward_window":[False]*n})


def instruments():
    return pl.DataFrame({"symbol":["A.SH","B.SH","C.SZ","D.SZ","E.BJ","F.SH","G.SH"],"exchange":["SH","SH","SZ","SZ","BJ","SH","SH"],"asset_type":["stock"]*6+["etf"],"list_date":[date(2010,1,1)]*5+[None,date(2010,1,1)],"delist_date":[None,date(2021,1,1),None,None,None,None,None]})


class M3Tests(unittest.TestCase):
    def test_listing_and_delisting_boundaries(self):
        r=instruments().to_dicts()[1]
        self.assertEqual(eligibility_reason(r,date(2009,12,31)),"not_yet_listed")
        self.assertEqual(eligibility_reason(r,date(2010,1,1)),"eligible")
        self.assertEqual(eligibility_reason(r,date(2021,1,1)),"eligible")
        self.assertEqual(eligibility_reason(r,date(2021,1,2)),"no_longer_listed")
        rows=instruments().to_dicts()
        self.assertEqual(eligibility_reason(rows[4],DAY),"excluded_market")
        self.assertEqual(eligibility_reason(rows[5],DAY),"unknown_listing")
        self.assertEqual(eligibility_reason(rows[6],DAY),"non_stock")

    def test_deterministic_cohort_not_future_survival(self):
        d=instruments()
        candidates,a=select_cohort(d,DAY,"fixed",2)
        changed=d.with_columns(pl.lit(None,dtype=pl.Date).alias("delist_date"))
        _,b=select_cohort(changed.reverse(),DAY,"fixed",2)
        self.assertEqual(candidates.height,4)
        self.assertEqual(a['symbol'].to_list(),b['symbol'].to_list())
        self.assertIn('B.SH',a['symbol'].to_list())
        c=universe_counts(d,[DAY]).row(0,named=True)
        self.assertEqual(sum(c[k] for k in c if k!='trade_date'),d.height)
        self.assertEqual(c['eligible'],4)

    def test_future_labels_cannot_change_ranks_or_buckets(self):
        s=signals()
        frozen=assign_quantiles(s)
        original=frozen.clone()
        altered=labels().with_columns(pl.when(pl.col('symbol')=='S000').then(None).otherwise(-pl.col('forward_return')).alias('forward_return'))
        for outcomes in [labels(),altered,labels().head(20),labels().head(0)]:
            joined,_,_=evaluate_assignments(frozen,outcomes)
            self.assertTrue(joined.select(frozen.columns).equals(original))
            self.assertTrue(assign_quantiles(s).equals(original))

    def test_missing_outcomes_and_delisting_are_not_zero_filled(self):
        l=labels().with_columns(pl.when(pl.col('symbol')=='S000').then(None).otherwise(pl.col('forward_return')).alias('forward_return'),(pl.col('symbol')=='S000').alias('delisting_in_forward_window'),pl.when(pl.col('symbol')=='S000').then(pl.lit('missing_outcome_delisting_window')).otherwise(pl.lit('valid')).alias('label_reason'))
        joined,m,_=evaluate_assignments(assign_quantiles(signals()),l)
        self.assertEqual(m['outcomes']['assigned'],36)
        self.assertEqual(m['outcomes']['valid'],35)
        self.assertEqual(m['outcomes']['missing'],1)
        self.assertEqual(m['outcomes']['delisting_window_missing'],1)
        self.assertEqual(m['buckets']['1']['missing'],1)
        self.assertEqual(joined['forward_return'].null_count(),1)
        self.assertAlmostEqual(m['buckets']['1']['missing_rate'],1/12)

    def test_ties_small_cross_sections_and_coverage(self):
        self.assertTrue(assign_quantiles(signals(29)).is_empty())
        self.assertTrue(assign_quantiles(signals().with_columns(pl.lit(1.).alias('momentum'))).is_empty())
        s=signals().with_columns((pl.col('momentum')//12).alias('momentum'))
        a=assign_quantiles(s)
        self.assertEqual(a.group_by('momentum').agg(pl.col('bucket').n_unique())['bucket'].to_list(),[1,1,1])
        _,m,_=evaluate_assignments(a,labels().filter(~pl.col('symbol').is_in(['S000','S001'])))
        self.assertEqual(m['primary_comparison_dates'],0)
        self.assertIsNone(m['primary_high_minus_low'])

    def test_rank_ic(self):
        _,m,_=evaluate_assignments(assign_quantiles(signals()),labels())
        self.assertAlmostEqual(m['mean_daily_rank_ic'],1)
        self.assertEqual(m['primary_comparison_dates'],1)

    def test_unknown_and_conflicting_status_evidence(self):
        artifacts=Path(__file__).resolve().parents[1]/'artifacts'
        with tempfile.TemporaryDirectory(dir=artifacts) as folder:
            root=Path(folder)
            reader=LakeReader(root)
            self.assertTrue(reader.status(['A'],DAY,DAY).is_empty())
            dest=root/'curated/trading_status/trade_date=2020-01'
            dest.mkdir(parents=True)
            pl.DataFrame({'symbol':['A','A','B','C'],'trade_date':[DAY]*4,'status':['st','normal','unrecognized','normal'],'is_trading':[True]*4}).write_parquet(dest/'part.parquet')
            status=reader.status(['A','B','C'],DAY,DAY)
            values=dict(status.select('symbol','status_evidence').iter_rows())
            self.assertEqual(values,{'A':'conflict','B':'unknown_record','C':'explicit_normal'})

    def test_new_adapter_never_falls_back_to_raw_prices(self):
        artifacts=Path(__file__).resolve().parents[1]/'artifacts'
        with tempfile.TemporaryDirectory(dir=artifacts) as folder:
            root=Path(folder)
            dest=root/f'curated/daily_bars/trade_date={DAY}'
            dest.mkdir(parents=True)
            pl.DataFrame({'symbol':['A'],'trade_date':[DAY],'open':[10.],'high':[11.],'low':[9.],'close':[10.]}).write_parquet(dest/'part.parquet')
            d=LakeReader(root).prices(['A','B'],[DAY])
            self.assertEqual(d['analytical_close'].null_count(),2)
            self.assertEqual(dict(d.select('symbol','price_reason').iter_rows()),{'A':'missing_adjustment','B':'missing_bar'})

    def test_coverage_keeps_missing_histories_in_denominator(self):
        d=pl.DataFrame({'symbol':['A.SH']*2,'trade_date':[DAY,DAY+timedelta(days=1)],'list_date':[date(2010,1,1)]*2,'delist_date':[DAY+timedelta(days=1)]*2,'listing_eligible':[True]*2,'analytical_close':[None,None],'status_evidence':['missing_unknown']*2,'price_reason':['missing_bar']*2,'signal_reason':['missing_bar']*2,'invalid_ohlc_flag':[False]*2,'missing_adjustment_flag':[True]*2,'invalid_adjustment_flag':[False]*2},schema_overrides={'analytical_close':pl.Float64})
        u=universe_counts(instruments(),[DAY,DAY+timedelta(days=1)])
        daily,symbols,summary=coverage_tables(d,u,DAY,DAY+timedelta(days=1))
        self.assertEqual(summary['sample_eligible_observations'],2)
        self.assertEqual(summary['eligible_price_reasons']['missing_bar'],2)
        self.assertEqual(symbols['terminal_missing_sessions'][0],2)
        self.assertEqual(symbols['delisted_incomplete_history'][0],True)
        self.assertEqual(daily['sample_unknown_status'].sum(),2)


class M2RegressionTests(unittest.TestCase):
    def test_legacy_source_hashes_and_numerical_results_unchanged(self):
        research=Path(__file__).resolve().parents[1]
        old=research/'artifacts/20261102T053633585731Z-494d22cf'
        saved=json.loads((old/'results.json').read_text(encoding='utf-8'))
        for name,digest in saved['provenance']['source_sha256'].items():
            self.assertEqual(sha256((research/'src/ashare_research'/name).read_bytes()).hexdigest(),digest)
        factors=pl.read_parquet(old/'factors.parquet'); labels_=pl.read_parquet(old/'labels.parquet')
        result,_=evaluate_m2(factors,labels_,saved['evaluation_period']['start'],saved['evaluation_period']['end'],3)
        self.assertAlmostEqual(result['mean_daily_rank_ic'],-0.05962441314553991,places=14)
        self.assertEqual(result['valid_ic_dates'],142)
        self.assertEqual(result['daily'],saved['metrics']['daily'])
        self.assertEqual(result['quantile_returns'],saved['metrics']['quantile_returns'])


if __name__=='__main__':
    unittest.main()
