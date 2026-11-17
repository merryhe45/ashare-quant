from pathlib import Path
import polars as pl

from ashare_lake.config import load_config
from ashare_lake.steps.http_common import write_fetched

CONFIG = r"C:\Users\merry\configs\ashare-lake.toml"
MAIN_RUN = "71eabce3-8585-4414-9a40-371ef2d60d91"

REPAIR_FILE = Path(
    r"C:\Users\merry\data\ashare-lake\staging\daily_bars"
    r"\run_id=manual-baostock-repair-117"
    r"\part-missing-117.parquet"
)

config = load_config(CONFIG)

df = pl.read_parquet(REPAIR_FILE)

print("Rows to merge:", df.height)
print("Symbols:", df["symbol"].n_unique())
print("Date range:", df["trade_date"].min(), "to", df["trade_date"].max())

result = write_fetched(
    config,
    MAIN_RUN,
    "daily_bars",
    df,
    source="baostock",
    batch_id="baostock-repair-117",
)

print("Write result:")
print(result)