# A-Share Quant Engine v2

## Upstream and attribution

This A-share quant engine uses the data lake from [CNEquity](https://github.com/rootSunc/CNEquity), originally developed by rootSunc and the CNEquity Contributors. The upstream components are licensed under Apache License 2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE), and [CNEquity's citation information](https://github.com/rootSunc/CNEquity/blob/main/CITATION.cff).

This repository adds quantitative screening and deployment scripts, including a calendar-only initial date lookup and a final dataset freshness check. The Docker image uses the pinned `ashare-lake==0.5.0` release.

The local Git remote `upstream` points to CNEquity. On a new clone, configure it with `git remote add upstream https://github.com/rootSunc/CNEquity.git`.

For local runs, set `data.root` in `ashare-lake.toml` to your data lake directory.

A fast A-share technical factor scanning engine using:



\- ashare-lake

\- Polars

\- Python

\- PowerShell



\## Current Factors



\### Formula 1 — Breakout 400



```text

C > REF(HHV(C,400),1)

```



\### Formula 2 — Pit Signal



```text

Event A:



C > O

AND 4.9% < daily rise <= 9.0%

AND C < REF(HHV(H,5),1)

AND C > MA(C,250)



Final signal:



COUNT(A,30) >= 2

AND C >= MA(C,250)

AND 60-day amplitude <= 50%

```



\### Formula 3 — MA20 / MA60 Golden Cross



```text

CROSS(MA(C,20), MA(C,60))

```



\## Architecture



The engine obtains the current A-share universe on the scan date,

then loads complete historical data for those symbols without applying

historical universe filtering.



This is intentional.



Do NOT change the historical query to:



```python

universe="all\_a"

```



Historical loading must use:



```python

symbols=current\_symbols

universe=None

```



\## Requirements



\- Windows

\- PowerShell

\- Python available as `python`

\- `ashare\_lake` Python package installed

\- `asl` CLI available on PATH

\- A working ashare-lake TOML configuration

\- Polars



Install Python dependency:



```powershell

python -m pip install -r requirements.txt

```



\## Configuration



Set the ashare-lake config path:



```powershell

$env:ASHARE\_LAKE\_CONFIG = "C:\\path\\to\\ashare-lake.toml"

```



Or pass it explicitly:



```powershell

.\\daily\_quant.ps1 -Config "C:\\path\\to\\ashare-lake.toml"

```



\## Daily Run



```powershell

.\\daily\_quant.ps1

```



The workflow:



```text

Determine latest trading date

&#x20;       ↓

Update stale ashare-lake datasets

&#x20;       ↓

Derive adjustment factors

&#x20;       ↓

Verify Data Lake freshness

&#x20;       ↓

Run Quant Engine v2

&#x20;       ↓

Generate factor results

&#x20;       ↓

Write JSON results

```



\## Quant Engine Only



Run the latest trading day automatically:



```powershell

python .\\run\_pipeline\_v2.py

```



Or specify a date:



```powershell

python .\\run\_pipeline\_v2.py --scan-date 2026-08-28

```



Or specify a config:



```powershell

python .\\run\_pipeline\_v2.py `

&#x20;   --scan-date 2026-08-28 `

&#x20;   --config "C:\\path\\to\\ashare-lake.toml"

```



\## JSON Results



Successful runs write:



```text

results/

&#x20;   quant\_YYYY-MM-DD.json

&#x20;   latest.json

```



`latest.json` represents the latest successfully published scan result.



\## Regression Baseline



The validated regression date is:



```text

2026-08-28

```



Validated exact outputs:



\- breakout\_400: 21 symbols

\- pit\_signal: 18 symbols



Regression testing compares exact symbol sets, not only counts.



The regression date is a test fixture. It is NOT the production scan date.



Normal production scans dynamically use the latest trading day.



\## Adding New Factors



New factors should normally require:



1\. Add the factor calculator to `factors\_v2.py`.

2\. Register it in `factor\_registry.py`.



The generic factor engine in `run\_pipeline\_v2.py` should not require

factor-specific modifications.

