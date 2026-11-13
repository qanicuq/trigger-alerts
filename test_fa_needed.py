import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, os.path.dirname(__file__))
import common
import fa_needed

NY = ZoneInfo("America/New_York")


def ny(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=NY)


class TestFaNeeded(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._dir = tempfile.mkdtemp()
        os.chdir(self._dir)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._dir, ignore_errors=True)

    # DHT as it really sits in triggers.yml on Nov 12: report on old numbers, earnings out Nov 9
    DHT = {"id": "DHT-forensic-2026-11-07", "ticker": "DHT", "status": "active",
           "report_date": "2026-11-07", "data_through": "2026-06-30",
           "earnings_date": "2027-02-03", "earnings_last": "2026-11-09", "earnings_last_time": "AMC"}

    def test_earnings_due_is_red(self):
        red, yellow, green = fa_needed.build_lists([self.DHT], {"positions": []}, ny(2026, 11, 12, 8, 30))
        self.assertEqual(red, [{
            "ticker": "DHT",
            "reason": "earnings came out 2026-11-09; the report is on old numbers",
            "since": "2026-11-09",
        }])
        self.assertEqual(yellow, [])
        self.assertEqual(green, [])

    def test_position_fa_needed_since_is_red(self):
        ledger = {"positions": [{
            "id": "MNST-forensic-2026-11-06",
            "ticker": "MNST",
            "target": 51.95,
            "fa_needed_since": "2026-11-12",
        }]}
        red, yellow, green = fa_needed.build_lists([], ledger, ny(2026, 11, 12, 8, 30))
        self.assertEqual(red, [{
            "ticker": "MNST",
            "reason": "hit its target of $51.95",
            "since": "2026-11-12",
        }])
        self.assertEqual(yellow, [])
        self.assertEqual(green, [])

    def test_still_red_after_10_days(self):
        # the bot keeps blocking the buy until a fresh FA comes in, so the page keeps it red too
        red, _, _ = fa_needed.build_lists([self.DHT], {"positions": []}, ny(2026, 11, 30, 8, 30))
        self.assertEqual([r["ticker"] for r in red], ["DHT"])

    def test_earnings_out_before_check_runs_is_red(self):
        # AMC report last night; at 8:30 the market-hours check hasn't recorded it yet
        tr = {"id": "AMAT-1", "ticker": "AMAT", "status": "active", "report_date": "2026-11-12",
              "data_through": "2026-07-26", "earnings_date": "2026-11-12", "earnings_time": "AMC"}
        red, yellow, _ = fa_needed.build_lists([tr], {"positions": []}, ny(2026, 11, 13, 8, 30))
        self.assertEqual(red, [{"ticker": "AMAT", "reason": "earnings came out 2026-11-12; the report is on old numbers",
                                "since": "2026-11-12"}])
        self.assertEqual(yellow, [])

    def test_no_cash_fa_requested_is_red(self):
        tr = {"id": "X-1", "ticker": "X", "status": "active", "fa_requested": "2026-11-11T10:30:00-05:00"}
        red, _, _ = fa_needed.build_lists([tr], {"positions": []}, ny(2026, 11, 12, 8, 30))
        self.assertEqual(red, [{"ticker": "X", "reason": "no cash, and weaker than your weakest holding",
                                "since": "2026-11-11"}])

    def test_paused_nov6_trigger_on_old_numbers_is_not_red(self):
        tr = dict(self.DHT, id="DHT-forensic-2026-11-06-paused", status="paused")
        self.assertEqual(fa_needed.build_lists([tr], {"positions": []}, ny(2026, 11, 12, 8, 30)), ([], [], []))

    def test_inbox_file_removes_red(self):
        os.makedirs("inbox/rejected", exist_ok=True)
        with open("inbox/rejected/old.yml", "w") as f:
            yaml.dump({"ticker": "DHT"}, f)
        red, _, _ = fa_needed.build_lists([self.DHT], {"positions": []}, ny(2026, 11, 12, 8, 30))
        self.assertEqual([r["ticker"] for r in red], ["DHT"])
        with open("inbox/1700000000.yml", "w") as f:
            yaml.dump({"ticker": "DHT", "action": "BUY"}, f)
        red, _, _ = fa_needed.build_lists([self.DHT], {"positions": []}, ny(2026, 11, 12, 8, 30))
        self.assertEqual(red, [])

    def test_fa_run_after_earnings_on_old_quarter_says_waiting_and_not_green(self):
        tr = {"id": "AMAT-forensic-2026-11-13", "ticker": "AMAT", "status": "active",
              "report_date": "2026-11-13", "data_through": "2026-07-26",
              "earnings_last": "2026-11-12", "added_at": "2026-11-13 10:39"}
        red, _, green = fa_needed.build_lists([tr], {"positions": []}, ny(2026, 11, 13, 11, 0))
        self.assertEqual([r["ticker"] for r in red], ["AMAT"])
        self.assertIn("Webull doesn't have the new quarter yet", red[0]["reason"])
        self.assertEqual(green, [])

    def test_active_earnings_in_10_days_is_yellow(self):
        now = ny(2026, 11, 12, 9, 0)
        tr = {"id": "AAA-1", "ticker": "AAA", "status": "active", "earnings_date": "2026-11-22"}
        red, yellow, green = fa_needed.build_lists([tr], {"positions": []}, now)
        self.assertEqual(red, [])
        self.assertEqual(yellow, [{
            "ticker": "AAA",
            "reason": "earnings on 2026-11-22",
            "since": "2026-11-08",
        }])
        self.assertEqual(green, [])

    def test_active_earnings_in_20_days_is_not_yellow(self):
        now = ny(2026, 11, 12, 9, 0)
        tr = {"id": "BBB-1", "ticker": "BBB", "status": "active", "earnings_date": "2026-12-02"}
        _, yellow, _ = fa_needed.build_lists([tr], {"positions": []}, now)
        self.assertEqual(yellow, [])

    def test_red_ticker_is_not_also_yellow(self):
        tr = {"id": "AAA-1", "ticker": "AAA", "status": "active", "report_date": "2026-11-01",
              "earnings_date": "2026-11-22", "earnings_last": "2026-11-09"}
        red, yellow, _ = fa_needed.build_lists([tr], {"positions": []}, ny(2026, 11, 12, 9, 0))
        self.assertEqual([r["ticker"] for r in red], ["AAA"])
        self.assertEqual(yellow, [])

    def test_added_at_2_hours_ago_is_green(self):
        now = ny(2026, 11, 12, 10, 0)
        added = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
        tr = {"id": "NEW-1", "ticker": "NEW", "status": "active", "added_at": added}
        _, _, green = fa_needed.build_lists([tr], {"positions": []}, now)
        self.assertEqual(green, [{
            "ticker": "NEW",
            "reason": "trigger sent to the bot",
            "since": added,
        }])

    def test_added_at_30_hours_ago_is_not_green(self):
        now = ny(2026, 11, 12, 10, 0)
        added = (now - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M")
        tr = {"id": "OLD-1", "ticker": "OLD", "status": "active", "added_at": added}
        _, _, green = fa_needed.build_lists([tr], {"positions": []}, now)
        self.assertEqual(green, [])

    def test_paused_trigger_shows_in_no_list(self):
        now = ny(2026, 11, 12, 10, 0)
        tr = {
            "id": "ZZZ-1",
            "ticker": "ZZZ",
            "status": "paused",
            "earnings_date": "2026-11-20",
            "added_at": (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"),
        }
        red, yellow, green = fa_needed.build_lists([tr], {"positions": []}, now)
        self.assertEqual((red, yellow, green), ([], [], []))

    def _write_state(self, triggers=None, positions=None):
        with open("triggers.yml", "w") as f:
            yaml.dump({"triggers": triggers or []}, f)
        with open("ledger.yml", "w") as f:
            yaml.dump({"cash": 10000.0, "positions": positions or []}, f)

    def test_remind_sends_no_push_when_red_empty(self):
        self._write_state()
        with patch("common.ntfy") as ntfy:
            fa_needed.remind(ny(2026, 11, 12, 8, 30))
        ntfy.assert_not_called()

    def test_remind_push_title_is_ascii_and_tags(self):
        self._write_state(triggers=[self.DHT])
        with patch("common.ntfy") as ntfy:
            fa_needed.remind(ny(2026, 11, 12, 8, 30))
        ntfy.assert_called_once()
        title = ntfy.call_args[0][0]
        body = ntfy.call_args[0][1]
        self.assertEqual(title, "Fresh FA needed: 1 ticker(s)")
        self.assertTrue(title.isascii())
        self.assertEqual(ntfy.call_args.kwargs["tags"], "rotating_light,bar_chart")
        self.assertTrue(ntfy.call_args.kwargs["tags"].isascii())
        self.assertEqual(ntfy.call_args.kwargs["priority"], "high")
        self.assertEqual(ntfy.call_args.kwargs["click_url"], fa_needed.FA_URL)
        self.assertEqual(ntfy.call_args.kwargs["action_label"], "Open FA list")
        self.assertEqual(ntfy.call_args.kwargs["action_url"], fa_needed.FA_URL)
        self.assertEqual(body, "DHT: earnings came out 2026-11-09; the report is on old numbers (since 2026-11-09)")


if __name__ == "__main__":
    unittest.main()
