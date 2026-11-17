import pandas as pd
from factors import add_breakout_400_factor


rows = []

# Create 401 trading records.
# First 400 closes are 100.
for i in range(401):
    rows.append({
        "symbol": "TEST.SH",
        "trade_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
        "close": 100.0
    })

# Make the latest close a new high
rows[-1]["close"] = 99.0

df = pd.DataFrame(rows)

result = add_breakout_400_factor(df)

print(
    result[
        [
            "trade_date",
            "close",
            "prev_400d_high_close",
            "breakout_400"
        ]
    ].tail(3)
)