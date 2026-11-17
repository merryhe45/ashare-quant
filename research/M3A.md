# M3A: listing-aware exploratory evaluation

The legacy M2 modules, smoke configuration and validated artifacts are retained
unchanged. New implementation is under `src/ashare_research/m3a/`.

From the repository root with existing dependencies:

```powershell
$env:PYTHONPATH = "$PWD/research/src"
python -B -m unittest discover -s research/tests -v
python -B -m ashare_research.m3a.experiment --config research/configs/m3a_2020_2021.toml
```

The fixed 2020-01-02 cohort selects the 50 smallest SHA256 hashes per exchange
(SH/SZ). Hash input is UTF-8 seed + NUL + symbol. Listing/delisting rules are
inclusive. Asset type must be stock and listing date known. BJ is excluded.
Selection never uses prices, future survival, return labels or completeness.
No replacements are made for missing data; subsequent IPOs do not enter this
fixed starting cohort. Future delisting dates are retained for retrospective
eligibility and diagnostics, not sample weighting or selection ordering.

Calendar padding is exactly 60 sessions before the first evaluation session and
20 after the last. Prices are null outside listing eligibility. Every historical
or forward window requires all prices, without raw-price substitution or fills.
Known ST/restricted or conflicting status excludes a signal; absent status stays
unknown and is retained for exploration, not certified tradable.

`signal_first_v1` assigns ranks/buckets before labels are constructed. Average
ranks preserve ties; the bucket is floor((rank-1)*3/n)+1. Signal cross-sections
require 30 stocks and three distinct values. Primary comparisons additionally
require five assigned stocks and 90% observed outcomes per bucket on common
dates. Available-outcome bucket means are also reported separately. IC uses at
least 30 valid pairs, with ranks recomputed within the paired set. Outcome
missingness remains a source of bias and is never zero-filled.

Both bucket metrics use equal-stock arithmetic means within a date and
equal-date means thereafter. High-minus-low uses the common primary dates.
Overlapping labels are dependent; no p-values, annualization, compounding or
investment profitability claims are made. Rank/bucket assignments are saved
separately from labels, with a content hash.

Each run exclusively creates a new artifacts directory with JSON/Markdown,
candidate and sample manifests, observations/reasons, factors, assignments,
labels, outcomes and daily/symbol coverage tables. Metadata-universe counts
cover all instruments; price/status diagnostics cover only the 100-stock sample.
Missing outcomes include bucket counts/rates and delisting-window diagnostics.
Primary exclusion reasons reconcile; independent diagnostic flags overlap.

M1 remains open: metadata is a retrospective 2026 vintage, security identity
links and full delisted histories are incomplete, and historical status does not
establish suspension/normal-trading coverage. Current HFQ factors are not certified
as originally available vintages. File hashes establish reproducibility of
consumed files, not market-data correctness or an atomic lake snapshot.
