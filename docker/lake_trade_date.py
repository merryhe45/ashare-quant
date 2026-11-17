"""Calendar-only equivalent of ashare-lake 0.5.0's status date lookup.

The private helper is intentional: keep its calendar precedence, fallback,
15-day search and host-local date semantics. Revalidate when upgrading the pin.
"""

import argparse
from datetime import date

from ashare_lake.cli.main import _cfg, _last_trading_day


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    print(_last_trading_day(_cfg(args.config), date.today()).isoformat())


if __name__ == "__main__":
    main()
