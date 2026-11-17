# Exploratory research foundation

Independent from the production scanner. M1 remains open; this package does not
claim full-market representativeness, survivorship-free results or profitability.
No ingestion, repair, Azure or production entry points are invoked.

From the repository root, using already installed dependencies in PowerShell:

```powershell
$env:PYTHONPATH = "$PWD/research/src"
python -B -m unittest discover -s research/tests -v
python -B -m ashare_research.experiment --config research/configs/smoke.toml
```

No installation is required for these commands. Source lives under `src/`;
`pyproject.toml` declares a package and optional future installation entry point.

The smoke configuration requests nine explicitly selected stocks for 2025 only.
The adapter reads one daily partition at a time, hashes consumed files and retains
only requested symbols. It applies stored HFQ factors itself so it never invokes
lake caches or substitutes raw prices. Calendar-complete grids preserve absent
sessions as nulls. Any missing/invalid price breaks both intersecting historical
and forward windows, even when the gap is just one session. No session gap is
interpreted as a suspension or automatically filled. The adapter supplies the
required grid contract to factor/label functions; callers must not compress it.

Momentum requires 61 prices for a 60-session return. Labels require 21 prices for
a 20-session return. Factors depend only on historical prices; labels are built
independently and joined only for evaluation. Labels are adjusted close-to-close
outcomes, not an executable same-close strategy. Price columns and factors must
be valid and positive; duplicate keys or malformed calendars fail the run.

Daily Spearman IC uses average ranks for ties. Quantile assignments also use
average ranks, preserving ties together; some buckets may be empty. Bucket means
are equal weighted within a date and averaged equally over dates where present.
Cross-sections below the configured number of quantiles are excluded. No
significance test treats overlapping outcomes as independent; no annualization,
portfolio compounding, cost model or claim of investable returns is provided.

Each run creates a unique `artifacts/<experiment_id>/` containing `results.json`,
`report.md`, `factors.parquet`, and `labels.parquet`. Configuration, source/input
SHA256 hashes, installed dependency versions and UTC timestamps are recorded.
Input files must remain stable during a run; hashes do not provide transactional
lake snapshots. Exclusion reasons can overlap and are not additive.

Tests use temporary fixtures only within `artifacts/` and remove those fixtures.
Neither unit tests nor the experiment write to the Data Lake or production results.
