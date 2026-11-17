from datetime import date,timedelta
from hashlib import sha256
import json
from pathlib import Path
import statistics
import unittest

import polars as pl

from ashare_research.m3b.registry import REGISTRY
from ashare_research.m3b.factors import calculate
from ashare_research.m3b.data import audit_amounts
from ashare_research.m3b.evaluation import matched_keys,freeze_assignments,evaluate

CFG={"quantiles":3,"min_cross_section":30,"min_bucket":5,"min_paired":30,"coverage_threshold":0.9}
ROOT=Path(__file__).resolve().parents[1]
REFERENCE=ROOT/'artifacts/m3a-20261102T074851461727Z-9ad8e456'


def panel(n=150):
    return pl.DataFrame({'symbol':['A']*n+['B']*n,'trade_date':[date(2019,1,1)+timedelta(days=i) for i in range(n)]*2,'analytical_close':[100.+i for i in range(n)]+[1000.-i for i in range(n)],'valid_amount':[1000.+10*i for i in range(n)]+[5000.+i for i in range(n)],'listing_eligible':[True]*(n*2),'list_date':[date(2010,1,1)]*(n*2),'status_evidence':['missing_unknown']*(n*2),'price_reason':['ready']*(n*2)})


def table(n=36):
    return pl.DataFrame({'symbol':[f'S{i:03}' for i in range(n)],'trade_date':[date(2020,1,2)]*n,'factor_id':['test']*n,'factor_value':[float(i) for i in range(n)],'factor_reason':['ready']*n})


class MultiFactorTests(unittest.TestCase):
    def test_numerical_definitions_and_first_valid_boundaries(self):
        p=panel()
        for name,spec in REGISTRY.items():
            with self.subTest(factor=name):
                a=calculate(p,spec).filter(pl.col('symbol')=='A')
                self.assertEqual(a['factor_value'][:spec.prior_sessions].null_count(),spec.prior_sessions)
                self.assertEqual(a['factor_reason'][spec.prior_sessions],'ready')
                t=130; xs=[100.+i for i in range(150)]
                if spec.kind=='momentum': expected=xs[t]/xs[t-spec.sessions]-1
                elif spec.kind=='ma_distance': expected=xs[t]/statistics.mean(xs[t-59:t+1])-1
                elif spec.kind=='volatility': expected=statistics.stdev(xs[i]/xs[i-1]-1 for i in range(t-19,t+1))
                else: expected=statistics.mean(1000.+10*i for i in range(t-19,t+1))
                self.assertAlmostEqual(a['factor_value'][t],expected,places=12)

    def test_future_invariance_and_cross_symbol_isolation(self):
        p=panel(); cutoff=date(2019,5,1)
        for spec in REGISTRY.values():
            full=calculate(p,spec)
            self.assertTrue(full.filter(pl.col('trade_date')<=cutoff).equals(calculate(p.filter(pl.col('trade_date')<=cutoff),spec)))
            self.assertTrue(full.filter(pl.col('symbol')=='B').equals(calculate(p.filter(pl.col('symbol')=='B'),spec)))

    def test_missing_sessions_break_required_windows(self):
        p=panel().with_columns(pl.when(pl.col('trade_date')==date(2019,4,11)).then(None).otherwise(pl.col('analytical_close')).alias('analytical_close'),pl.when(pl.col('trade_date')==date(2019,4,11)).then(None).otherwise(pl.col('valid_amount')).alias('valid_amount'))
        for spec in REGISTRY.values():
            a=calculate(p,spec).filter(pl.col('symbol')=='A')
            self.assertIsNone(a['factor_value'][110])
            self.assertNotEqual(a['factor_reason'][110],'ready')

    def test_constant_prices_have_zero_volatility(self):
        p=panel().with_columns(pl.lit(100.).alias('analytical_close'))
        self.assertEqual(calculate(p,REGISTRY['volatility_20'])['factor_value'][30],0.)

    def test_matching_uses_readiness_not_labels(self):
        a=table(); b=a.with_columns(pl.when(pl.col('symbol')=='S000').then(pl.lit('missing')).otherwise(pl.col('factor_reason')).alias('factor_reason'))
        keys=matched_keys({'a':a,'b':b})
        self.assertEqual(keys.height,35)
        self.assertNotIn('S000',keys['symbol'])
        frozen=freeze_assignments(a,CFG,keys)
        labels=a.select('symbol','trade_date').with_columns(pl.lit(.1).alias('forward_return'),pl.lit('valid').alias('label_reason'),pl.lit(False).alias('delisting_in_forward_window'))
        for outcomes in [labels,labels.head(0),labels.with_columns(pl.lit(None,dtype=pl.Float64).alias('forward_return'))]:
            joined,_,_=evaluate(frozen,outcomes,CFG)
            self.assertTrue(joined.select(frozen.columns).equals(frozen))
        # Disabled liquidity is absent, rather than nulling the price intersection.
        self.assertEqual(matched_keys({'price':a}).height,36)

    def test_liquidity_validity_and_units_gate(self):
        rows=12
        d=pl.DataFrame({'symbol':[f'S{i}' for i in range(rows)],'trade_date':[date(2020,1,2)]*rows,'listing_eligible':[True]*rows,'source':['tdx_protocol']*rows,'amount':[1000.]*rows,'volume':[100.]*rows,'low':[9.]*rows,'high':[11.]*rows,'close':[10.]*rows})
        good,audit=audit_amounts(d,min_unit_rows=2)
        self.assertTrue(audit['enabled']); self.assertEqual(good['valid_amount'].sum(),12000.)
        for amount,reason in [(None,'missing_amount'),(-1.,'negative_amount'),(float('inf'),'nonfinite_amount'),(0.,'unexplained_zero_amount')]:
            bad=d.with_columns(pl.when(pl.col('symbol')=='S0').then(pl.lit(amount,dtype=pl.Float64)).otherwise(pl.col('amount')).alias('amount'))
            observed,_=audit_amounts(bad,min_unit_rows=2)
            r=observed.filter(pl.col('symbol')=='S0')
            self.assertEqual(r['amount_reason'][0],reason); self.assertIsNone(r['valid_amount'][0])
        _,audit=audit_amounts(d.with_columns(pl.lit('unverified').alias('source')),min_unit_rows=2)
        self.assertFalse(audit['enabled'])
        _,audit=audit_amounts(d.with_columns((pl.col('amount')*100).alias('amount')),min_unit_rows=2)
        self.assertFalse(audit['enabled'])
        _,audit=audit_amounts(d,min_unit_rows=20)
        self.assertFalse(audit['enabled'])

    def test_m3a_source_and_momentum60_compatibility(self):
        saved=json.loads((REFERENCE/'results.json').read_text(encoding='utf-8'))
        for name,digest in saved['provenance']['source_sha256'].items():
            self.assertEqual(sha256((ROOT/'src/ashare_research'/name).read_bytes()).hexdigest(),digest)
        p=pl.read_parquet(REFERENCE/'observations.parquet')
        t=calculate(p,REGISTRY['momentum_60']).filter(pl.col('trade_date').is_between(date(2020,1,2),date(2021,12,31)))
        expected=pl.read_parquet(REFERENCE/'factors.parquet').join(t.select('symbol','trade_date'),on=['symbol','trade_date'],how='semi').sort('symbol','trade_date')
        self.assertTrue(t.select('symbol','trade_date',pl.col('factor_value').alias('momentum')).equals(expected))
        a=freeze_assignments(t,CFG)
        self.assertTrue(a.rename({'factor_value':'momentum'}).equals(pl.read_parquet(REFERENCE/'assignments.parquet')))
        _,metrics,daily=evaluate(a,pl.read_parquet(REFERENCE/'labels.parquet'),CFG)
        self.assertEqual(metrics['mean_daily_rank_ic'],saved['metrics']['mean_daily_rank_ic'])
        self.assertEqual(metrics['buckets'],saved['metrics']['buckets'])
        self.assertEqual(daily,saved['daily_metrics'])


if __name__=='__main__':
    unittest.main()
