# M3B independent factor research

All new implementation is in `src/ashare_research/m3b/`. Legacy M2/M3A code and
saved artifacts remain unchanged. No new dependency is required.

From the repository root:

```powershell
$env:PYTHONPATH = "$PWD/research/src"
python -B -m unittest discover -s research/tests -v
python -B -m ashare_research.m3b.experiment --config research/configs/m3b_2020_2021.toml
```

Six immutable v1 definitions: Momentum 20/60/120, MA60 distance, Volatility 20
(simple-return sample std, ddof=1, unannualized), and Liquidity 20 (CNY/session).
All windows include the signal session t; signals are available only after close.
MA60 needs 60 prices; return volatility needs 21 prices; L-session momentum needs
L+1 prices. Liquidity needs 20 valid amounts. Every session must be present; no
forward fill, raw-price substitution, or missing-return zero fill is used.

The M3A frozen sample manifest is reused without survival or completeness
selection. Load padding is extended to 120 sessions, but each factor's readiness
uses its own window. Current valid analytical close is required for all signals,
including liquidity, consistent with the existing signal-date price policy.
Liquidity windows themselves do not require adjusted historical prices. Listing
and historical status policy remain M3A's: inclusive listing interval; BJ excluded;
known restrictions/conflicts excluded; unknown status retained as unknown.

Liquidity audit covers the entire bounded loaded panel, including warm-up and
future-label extension. Only TDX/Baostock sources are candidates for yuan/share
unit validation. Raw amount/volume must fall within raw low/high plus 1% tolerance,
with at least 20 positive checks per observed source/year and 99% consistency.
Unknown sources or inconclusive evidence disable liquidity globally. Missing,
negative, nonfinite, unexplained zero and individually failed checks are null.
Zeros are not accepted as proven zero trading activity. Amount is never adjusted
or estimated from prices. The retrospective audit is a dataset acceptance gate,
not a historical predictive feature; enabling it does not certify point-in-time
data availability.

All factors are evaluated ascending by raw value with unchanged M3A signal-first
policies. The generic adapter maps factor_value to M3A's internal momentum field
only while invoking existing functions. No parameter selection or composite is
performed. Native factors retain individual coverage. Matched keys are the common
factor-ready stock/date intersection before labels; ranks are recomputed within
it. Disabled liquidity is omitted and the comparison labelled price-factors-only.
Assignments are frozen before labels are constructed. Missing outcomes and
delisting-window diagnostics are retained per factor and bucket.

Reports include individual native/matched metrics and equal-stock/equal-date
quantile means. Comparison tables use cross-factor common primary dates for
bucket means, while per-factor IC retains its own valid-pair dates (counts shown).
No ordinary independent-observation significance test, annualization or portfolio
compounding is applied to overlapping outcomes.

The runner refuses changed M3A historical inputs or changed legacy code, and
requires exact Momentum 60 evaluation-period values, assignments, labels and daily
metrics. It exclusively creates a new artifacts directory with configuration,
reference/input/source hashes, definition versions, timestamps, observations,
factor panels, matched keys, assignments, outcomes, JSON and Markdown. M1 data
limitations persist; these studies neither represent the full market nor establish
survivorship-free investable returns or profitability.
