"""Offline calendar equivalence and mocked updater control-flow checks."""

import importlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import patch

import polars as pl
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[2]
cli = importlib.import_module("ashare_lake.cli.main")
spec = importlib.util.spec_from_file_location("lake_trade_date", ROOT / "docker/lake_trade_date.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class CalendarLookupTests(unittest.TestCase):
    def test_status_equivalence(self):
        # Fixture contains a normal Friday, weekend and National Day closure.
        calendar = pl.DataFrame({
            "trade_date": [date(2026, 9, 25), date(2026, 9, 26),
                           date(2026, 9, 30), date(2026, 10, 1)],
            "is_trading": [True, False, True, False],
        })
        catalog = pl.DataFrame({
            "dataset": ["daily_bars"], "has_data": [True],
            "watermarked": [True], "watermark": [date(2026, 9, 1)],
            "coverage_end": [date(2026, 9, 1)],
        })
        for today, expected in [(date(2026, 9, 25), "2026-09-25"),
                                (date(2026, 9, 26), "2026-09-25"),
                                (date(2026, 10, 1), "2026-09-30")]:
            with self.subTest(today=today), \
                 patch.object(cli, "_cfg", return_value=object()), \
                 patch.object(helper, "_cfg", return_value=object()), \
                 patch.object(cli, "date") as old_date, \
                 patch.object(helper, "date") as new_date, \
                 patch("ashare_lake.steps.common._load_trading_calendar_df", return_value=calendar), \
                 patch("ashare_lake.adapters.calendar.exchange_calendar.ensure_seed_csv", side_effect=AssertionError("unexpected seed access")), \
                 patch("ashare_lake.query.reader.list_datasets", return_value=catalog) as datasets:
                old_date.today.return_value = new_date.today.return_value = today
                old = CliRunner().invoke(cli.status, ["--config", "fixture", "--datasets"])
                self.assertEqual(old.exit_code, 1)  # stale, but old updater proceeds
                old_anchor = re.search(r"last trading day: (\d{4}-\d{2}-\d{2})", old.output).group(1)
                datasets.assert_called_once()
                datasets.reset_mock()
                output = io.StringIO()
                with patch("sys.argv", ["lake_trade_date.py", "--config", "fixture"]), redirect_stdout(output):
                    helper.main()
                self.assertEqual(output.getvalue().strip(), old_anchor)
                self.assertEqual(old_anchor, expected)
                datasets.assert_not_called()

    def test_mocked_wrapper(self):
        bash = shutil.which("bash")
        git_bash = Path("C:/Program Files/Git/usr/bin/bash.exe")
        if git_bash.exists():
            bash = str(git_bash)
        self.assertIsNotNone(bash, "Bash required for wrapper control-flow checks")
        script = (ROOT / "update_lake.sh").read_text()
        self.assertEqual(script.count("$(asl status"), 1)
        self.assertIn("FINAL_STATUS_OUTPUT=$(asl status", script)
        self.assertIn("--stale-only", script)
        self.assertIn("COPY docker/lake_trade_date.py /app/docker/lake_trade_date.py", (ROOT / "Dockerfile").read_text())
        # Run the actual wrapper with shell functions replacing every service command.
        prelude = '''
python() { printf 'CALL python %s\\n' "$*" >&2; if [ "$CALENDAR_FAIL" = 1 ]; then return 2; fi; printf '%s\\n' "$FIXTURE_DATE"; }
asl() {
    printf 'CALL asl %s\\n' "$*" >&2
    case "$1" in
        run) return "$DAILY_FAIL" ;;
        derive) return "$DERIVE_FAIL" ;;
        status) return "$FINAL_FAIL" ;;
    esac
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "fixture.toml"
            config.touch()
            for anchor in ["2026-09-25", "2026-09-30"]:
                for calendar_fail, final_fail in [(0, 0), (1, 0), (0, 1)]:
                    with self.subTest(anchor=anchor, calendar_fail=calendar_fail, final_fail=final_fail):
                        env = dict(os.environ, ASHARE_LAKE_CONFIG=config.as_posix(), FIXTURE_DATE=anchor,
                                   CALENDAR_FAIL=str(calendar_fail), FINAL_FAIL=str(final_fail),
                                   DAILY_FAIL="0", DERIVE_FAIL="0")
                        run = subprocess.run([bash, "-s"], input=prelude + script, text=True,
                                             capture_output=True, env=env, timeout=20)
                        # Final status captures stderr into its output by design.
                        calls = [s for s in (run.stderr + run.stdout).splitlines() if s.startswith("CALL ")]
                        self.assertEqual(run.returncode, 1 if calendar_fail or final_fail else 0, run.stderr)
                        self.assertIn("--config", calls[0])
                        if calendar_fail:
                            self.assertEqual(len(calls), 1)
                        else:
                            self.assertEqual(len(calls), 4)
                            self.assertEqual(calls[1], f"CALL asl run daily --config {config.as_posix()} --trade-date {anchor} --stale-only")
                            self.assertEqual(calls[2], f"CALL asl derive adj_factors --config {config.as_posix()}")
                            self.assertEqual(calls[3], f"CALL asl status --config {config.as_posix()} --datasets")

            for command, variable, message, skipped in [
                ("run daily", "DAILY_FAIL", "DATA LAKE UPDATE FAILED", "CALL asl derive"),
                ("derive adj_factors", "DERIVE_FAIL", "ADJ FACTOR UPDATE FAILED", "CALL asl status"),
            ]:
                with self.subTest(command=command):
                    env = dict(os.environ, ASHARE_LAKE_CONFIG=config.as_posix(),
                               FIXTURE_DATE="2026-09-25", CALENDAR_FAIL="0",
                               DAILY_FAIL="0", DERIVE_FAIL="0", FINAL_FAIL="0")
                    env[variable] = "7"
                    run = subprocess.run([bash, "-s"], input=prelude + script, text=True,
                                         capture_output=True, env=env, timeout=20)
                    self.assertEqual(run.returncode, 7, run.stderr)
                    self.assertIn(message, run.stdout)
                    self.assertIn(f"asl {command} returned exit code 7", run.stderr)
                    self.assertNotIn(skipped, run.stderr + run.stdout)


if __name__ == "__main__":
    unittest.main()
