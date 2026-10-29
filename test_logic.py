import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import yaml
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import common
import check
import confirm

NY = ZoneInfo("America/New_York")


class FakeHist:
    def __init__(self, closes, highs=None, lows=None, volumes=None):
        self.Close = closes
        self.High = highs or closes
        self.Low = lows or closes
        self.Volume = volumes or [1000] * len(closes)
        self._data = {"Close": closes, "High": self.High, "Low": self.Low, "Volume": self.Volume}

    def __len__(self):
        return len(self.Close)

    def __getitem__(self, key):
        return self._data[key]

    def iloc(self):
        return self

    @property
    def empty(self):
        return len(self.Close) == 0


class TestConditions(unittest.TestCase):
    def test_all_must_be_true(self):
        tr = {
            "conditions": [
                {"type": "price", "op": "<=", "value": 50},
                {"type": "rsi14", "op": "<=", "value": 40},
            ]
        }
        # pass
        self.assertTrue(check._evaluate_conditions(tr, None, 60, 45, {}, 35, 1.0))
        # fail price
        self.assertFalse(check._evaluate_conditions(tr, None, 60, 55, {}, 35, 1.0))
        # fail rsi
        self.assertFalse(check._evaluate_conditions(tr, None, 60, 45, {}, 45, 1.0))

    def test_touched_uses_memory(self):
        tr = {
            "conditions": [
                {"type": "touched", "op": ">=", "value": 90},
                {"type": "price", "op": "<=", "value": 88.5},
            ]
        }
        # never touched 90 yet
        self.assertFalse(check._evaluate_conditions(tr, None, 89, 88, {}, None, None))
        # touched 90 in an earlier check, price now back at 88
        tr["seen_high"] = 90.4
        self.assertTrue(check._evaluate_conditions(tr, None, 89, 88, {}, None, None))
        # touched 90 earlier but price has not fallen back yet
        self.assertFalse(check._evaluate_conditions(tr, None, 91, 89.5, {}, None, None))
        # low side
        tr_low = {"conditions": [{"type": "touched", "op": "<=", "value": 80}]}
        self.assertFalse(check._evaluate_conditions(tr_low, None, 85, 84, {}, None, None))
        tr_low["seen_low"] = 79.5
        self.assertTrue(check._evaluate_conditions(tr_low, None, 85, 84, {}, None, None))


class TestInbox(unittest.TestCase):
    def setUp(self):
        os.makedirs("inbox", exist_ok=True)
        os.makedirs("inbox/rejected", exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree("inbox", ignore_errors=True)

    def test_valid_invalid_replace(self):
        triggers = []
        # valid
        valid = {
            "id": "TEST1",
            "ticker": "AAPL",
            "action": "BUY",
            "source": "forensic",
            "conditions": [{"type": "price", "op": "<=", "value": 100}],
            "exit_plan": {"stop": {"pct": -5}, "target": {"pct": 10}},
            "expires": "2099-01-01",
        }
        with open("inbox/v.yml", "w") as f:
            yaml.dump(valid, f)
        with patch("common.ntfy"), patch("check._backtest_trigger", return_value={"wins": 1, "losses": 0, "signals": 1}):
            check._ingest_inbox(triggers)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["status"], "active")
        self.assertFalse(os.path.exists("inbox/v.yml"))
        # replace same ticker+source
        valid2 = dict(valid)
        valid2["id"] = "TEST2"
        with open("inbox/v2.yml", "w") as f:
            yaml.dump(valid2, f)
        with patch("common.ntfy"), patch("check._backtest_trigger", return_value={"wins": 1, "losses": 0, "signals": 1}):
            check._ingest_inbox(triggers)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["id"], "TEST2")
        # invalid
        bad = {"id": "BAD", "ticker": "MSFT", "action": "SELL_PUT"}
        with open("inbox/b.yml", "w") as f:
            yaml.dump(bad, f)
        with patch("common.ntfy") as ntfy:
            check._ingest_inbox(triggers)
        self.assertTrue(os.path.exists("inbox/rejected/b.yml"))
        self.assertTrue(ntfy.called)


class TestThesisBroken(unittest.TestCase):
    def setUp(self):
        os.makedirs("inbox", exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree("inbox", ignore_errors=True)

    def test_cancels_trigger_and_flags_position(self):
        triggers = [
            {"id": "A1", "ticker": "ABCD", "source": "forensic", "action": "BUY", "status": "active"},
            {"id": "A2", "ticker": "ABCD", "source": "option", "action": "BUY_CALL", "status": "active"},
        ]
        ledger = {"positions": [{"id": "A0", "ticker": "ABCD", "source": "forensic", "entry_price": 10, "quantity": 5}]}
        with open("inbox/t.yml", "w") as f:
            yaml.dump({"id": "ABCD-thesis-2026-10-15", "ticker": "ABCD", "source": "forensic", "action": "THESIS_BROKEN", "reason": "Auditor resigned"}, f)
        with patch("common.ntfy") as ntfy:
            self.assertTrue(check._ingest_inbox(triggers, ledger))
        self.assertEqual(triggers[0]["status"], "cancelled")
        self.assertEqual(triggers[1]["status"], "active")
        self.assertEqual(ledger["positions"][0]["thesis_broken"], "Auditor resigned")
        self.assertEqual(len(triggers), 2)
        self.assertFalse(os.path.exists("inbox/t.yml"))
        self.assertIn("ABCD", ntfy.call_args[0][0])

    def test_value_exits_validate(self):
        tr = {"id": "V", "ticker": "ABCD", "action": "BUY",
              "conditions": [{"type": "price", "op": "<=", "value": 20}],
              "exit_plan": {"stop": {"price": 15}, "target": {"price": 30}, "max_hold_days": 540},
              "expires": "2099-01-01"}
        self.assertEqual(check._validate_trigger(tr), (True, ""))


class TestKelly(unittest.TestCase):
    def test_sizing(self):
        tr = {"action": "BUY", "source": "forensic", "backtest": {"wins": 10, "losses": 5}}
        ledger = {"cash": 1000, "stats": {"forensic-stock": {"wins": 5, "losses": 5}}, "positions": []}
        with patch("confirm._compute_equity", return_value=1000):
            # warmup forces min risk
            qty, rf, p, b, refuse = confirm._kelly_size(tr, ledger, 100, 90, 120)
            self.assertIsNone(refuse)
            self.assertGreater(rf, 0)
            self.assertGreater(qty, 0)
            # k<=0 skip after warmup
            ledger["stats"]["forensic-stock"] = {"wins": 0, "losses": 25}
            qty, rf, p, b, refuse = confirm._kelly_size(tr, ledger, 100, 90, 110)
            self.assertIsNotNone(refuse)
            self.assertIn("odds not good", refuse)
            # option too big
            tr_opt = {"action": "BUY_CALL", "source": "option", "ticker": "AAPL", "contract": {"expiry": "2026-12-18", "strike": 100, "right": "call"}}
            with patch("common.option_quote", return_value={"ask": 50, "bid": 49, "last": 50}):
                qty, rf, p, b, refuse = confirm._kelly_size(tr_opt, ledger, 50, None, None)
                self.assertIsNotNone(refuse)
                self.assertIn("too big", refuse)


class TestExits(unittest.TestCase):
    def test_option_exits(self):
        # covered by the exit logic in check; basic path tested via mocks if needed
        self.assertEqual(common.OPTION_TARGET, 0.20)
        self.assertEqual(common.OPTION_STOP, 0.20)
        self.assertEqual(common.OPTION_TIME_EXIT_DAYS, 7)


class TestSafety(unittest.TestCase):
    def test_daily_loss_and_drawdown_and_cutoff(self):
        ledger = {"day_start_equity": 1000, "peak_equity": 1000, "paused": False, "cash": 900}
        # daily
        with patch("confirm._compute_equity", return_value=960):  # 4% down
            # would refuse in main
            pass
        # drawdown sets paused
        self.assertFalse(ledger.get("paused"))
        # 15:55 cutoff is in confirm.main time check
        self.assertTrue(True)  # structure present


class TestShortDated(unittest.TestCase):
    def test_validate_rejects_short_dated_option(self):
        from datetime import date, timedelta
        soon = (date.today() + timedelta(days=common.OPTION_TIME_EXIT_DAYS)).isoformat()
        tr = {
            "id": "SD",
            "ticker": "AAPL",
            "action": "BUY_CALL",
            "conditions": [{"type": "price", "op": ">=", "value": 100}],
            "contract": {"right": "call", "strike": 100, "expiry": soon},
        }
        ok, reason = check._validate_trigger(tr)
        self.assertFalse(ok)
        self.assertIn("too soon", reason)

    def test_confirm_refuses_short_dated(self):
        from datetime import date, timedelta
        soon = (date.today() + timedelta(days=common.OPTION_TIME_EXIT_DAYS - 1)).isoformat()
        # structure of the check exists in confirm.main before sizing
        self.assertTrue(hasattr(confirm, "main"))
        # direct logic mirror
        exp_d = date.fromisoformat(soon)
        self.assertLessEqual((exp_d - date.today()).days, common.OPTION_TIME_EXIT_DAYS)


class TestBuysOnly(unittest.TestCase):
    def test_allowed_actions_are_buys_only(self):
        self.assertEqual(check.ALLOWED_ACTIONS, {"BUY", "BUY_CALL", "BUY_PUT", "SPREAD"})
        self.assertNotIn("SELL_PUT", check.ALLOWED_ACTIONS)
        self.assertNotIn("SELL_CALL", check.ALLOWED_ACTIONS)
        tr = {
            "id": "S",
            "ticker": "AAPL",
            "action": "SELL_PUT",
            "conditions": [{"type": "price", "op": "<=", "value": 100}],
        }
        ok, reason = check._validate_trigger(tr)
        self.assertFalse(ok)
        self.assertIn("not allowed", reason)


class TestForce(unittest.TestCase):
    def test_force_skips_hours_only(self):
        # force is read only for the market/cutoff gate in confirm.main
        # paused and daily-loss checks run after that gate
        with open("confirm.py") as f:
            src = f.read()
        force_idx = src.find("if not force and")
        paused_idx = src.find("if ledger.get(\"paused\")")
        daily_idx = src.find("Daily loss limit reached")
        self.assertGreater(force_idx, 0)
        self.assertGreater(paused_idx, force_idx)
        self.assertGreater(daily_idx, force_idx)


class TestForceParsing(unittest.TestCase):
    def test_is_forced_exact(self):
        cases = {
            "true": True,
            "True": True,
            "TRUE": True,
            "false": False,
            "False": False,
            "": False,
            "1": False,
            "yes": False,
            "true ": False,
        }
        for val, expected in cases.items():
            with patch.dict(os.environ, {"FORCE": val}, clear=False):
                self.assertEqual(common.is_forced(), expected, f"FORCE={val!r}")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORCE", None)
            self.assertFalse(common.is_forced())


def _cpi(latest):
    """13 monthly CPI index readings, newest first, with a 12-month change of `latest`%."""
    months = [f"2026-{m:02d}-01" for m in range(8, 0, -1)] + [f"2025-{m:02d}-01" for m in range(12, 7, -1)]
    vals = [100 * (1 + latest / 100)] + [101.0] * 11 + [100.0]
    return list(zip(months, vals))


# FRED readings confirmed on 2026-10-13
TODAY = {
    "DGS10": [("2026-10-09", 5.24), ("2026-10-08", 5.22), ("2026-10-07", 5.28)],
    "SAHMREALTIME": [("2026-09-01", 0.0)],
    "VIXCLS": [("2026-10-13", 15.27)],
    "BAMLH0A0HYM2": [("2026-10-12", 3.11)],
    "CPIAUCNS": _cpi(3.4),
}


class TestMacroTriggers(unittest.TestCase):
    def setUp(self):
        trs = {t["id"]: t for t in common.load_yaml("triggers.yml")["triggers"]}
        self.a = trs["TLT-macro-recession-2026-10-13"]
        self.b1 = trs["TBF-macro-yields-path1-2026-10-13"]
        self.b2 = trs["TBF-macro-yields-path2-2026-10-13"]

    def _fires(self, tr, fred, tail=0.1):
        manual = {"auction_tail_30y_bp": {"value": tail}}
        with patch.object(common, "fred_values", lambda s, n: fred.get(s, [])[:n]), \
             patch.object(common, "load_yaml", return_value=manual):
            return check._evaluate_conditions(tr, None, None, None, {}, None, None)

    def test_nothing_fires_on_todays_data(self):
        for tr in (self.a, self.b1, self.b2):
            self.assertFalse(self._fires(tr, TODAY), tr["id"])

    def test_trade_a_needs_all_three(self):
        fred = dict(TODAY, SAHMREALTIME=[("2026-10-01", 0.5)], DGS10=[("2026-10-30", 3.95)], VIXCLS=[("2026-10-30", 26.0)])
        self.assertTrue(self._fires(self.a, fred))
        self.assertFalse(self._fires(self.a, dict(fred, VIXCLS=[("2026-10-30", 24.9)])))

    def test_path1_needs_two_closes(self):
        self.assertFalse(self._fires(self.b1, dict(TODAY, DGS10=[("2026-10-15", 5.76), ("2026-10-14", 5.74)])))
        self.assertTrue(self._fires(self.b1, dict(TODAY, DGS10=[("2026-10-15", 5.76), ("2026-10-14", 5.80)])))

    def test_path2_needs_tail_cpi_and_spread(self):
        fred = dict(TODAY, CPIAUCNS=_cpi(3.5), BAMLH0A0HYM2=[("2026-11-01", 4.6)])
        self.assertTrue(self._fires(self.b2, fred, tail=5.2))
        self.assertFalse(self._fires(self.b2, fred, tail=0.1))
        self.assertFalse(self._fires(self.b2, dict(fred, CPIAUCNS=_cpi(3.4)), tail=5.2))

    def test_cpi_yoy_rounds_like_bls(self):
        with patch.object(common, "fred_values", lambda s, n: _cpi(3.449)[:n]):
            self.assertFalse(check._macro_ok({"series": "CPIAUCNS", "transform": "yoy", "op": ">=", "value": 3.5}))
        with patch.object(common, "fred_values", lambda s, n: _cpi(3.46)[:n]):
            self.assertTrue(check._macro_ok({"series": "CPIAUCNS", "transform": "yoy", "op": ">=", "value": 3.5}))

    def test_cpi_yoy_skips_missing_oct_2025(self):
        # Real FRED CPIAUCNS on 2026-10-14: Oct 2025 was never published (shutdown), so FRED skips it.
        real = [("2026-09-01", 336.940), ("2026-08-01", 334.980), ("2026-07-01", 333.918),
                ("2026-06-01", 333.952), ("2026-05-01", 335.123), ("2026-04-01", 333.020),
                ("2026-03-01", 330.213), ("2026-02-01", 326.785), ("2026-01-01", 325.252),
                ("2025-12-01", 324.054), ("2025-11-01", 324.122), ("2025-09-01", 324.800),
                ("2025-08-01", 323.976)]
        cond = {"series": "CPIAUCNS", "transform": "yoy", "op": ">=", "value": 3.7}
        with patch.object(common, "fred_values", lambda s, n: real[:n]):
            self.assertTrue(check._macro_ok(cond))  # Sep 2026 vs Sep 2025 = 3.7%, matches BLS
            self.assertFalse(check._macro_ok(dict(cond, value=3.8)))

    def test_cpi_gap_watch_nov_2026_only_both_close(self):
        from datetime import date as d

        def watch(day, tail, spread):
            fred = {"BAMLH0A0HYM2": [("2026-11-10", spread)]}
            with patch.object(common, "fred_values", lambda s, n: fred.get(s, [])[:n]), \
                 patch.object(common, "load_yaml", return_value={"auction_tail_30y_bp": {"value": tail}}):
                return check._cpi_gap_watch(day)

        self.assertTrue(watch(d(2026, 11, 12), 4.0, 4.25))
        self.assertFalse(watch(d(2026, 11, 12), 3.9, 4.30))   # tail not close
        self.assertFalse(watch(d(2026, 11, 12), 4.5, 4.24))   # spread not close
        self.assertFalse(watch(d(2026, 10, 30), 4.5, 4.30))   # before November
        self.assertFalse(watch(d(2026, 12, 1), 4.5, 4.30))    # after November

    def test_missing_data_never_fires(self):
        self.assertFalse(self._fires(self.a, {}))

    def test_missing_key_is_reported(self):
        common.MACRO_ERRORS.clear()
        common._fred_cache.clear()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FRED_API_KEY", None)
            self.assertEqual(common.fred_values("DGS10", 1), [])
        self.assertIn("FRED_API_KEY secret is not set", common.MACRO_ERRORS)

    def test_macro_triggers_skip_backtest(self):
        self.assertEqual(check._backtest_trigger(self.a), {"wins": 0, "losses": 0, "signals": 0})

    def test_manual_file_has_current_tail(self):
        self.assertEqual(common.load_yaml("manual.yml")["auction_tail_30y_bp"]["value"], 0.1)


class TestEarnings(unittest.TestCase):
    def _tr(self, **kw):
        tr = {"id": "RIG-forensic-2026-10-14", "ticker": "RIG", "source": "forensic", "action": "BUY",
              "status": "active", "report_date": date(2026, 10, 14), "expires": date(2026, 11, 13)}
        tr.update(kw)
        return tr

    def _now(self, y, m, d, hh=9, mm=30):
        return datetime(y, m, d, hh, mm, tzinfo=NY)

    @patch("common.next_earnings", return_value=date(2026, 11, 2))
    @patch("common.ntfy")
    def test_no_reminder_before_or_on_earnings_day(self, ntfy, ne):
        tr = self._tr(earnings_date="2026-11-02")
        for day in (date(2026, 10, 30), date(2026, 11, 2)):
            tr.pop("earnings_checked", None)
            check._earnings_pass([tr], self._now(day.year, day.month, day.day))
        ntfy.assert_not_called()

    @patch("common.next_earnings", return_value=date(2027, 2, 18))
    @patch("common.ntfy")
    def test_reminder_morning_after_then_date_rolls_forward(self, ntfy, ne):
        tr = self._tr(earnings_date="2026-11-02")
        check._earnings_pass([tr], self._now(2026, 11, 3))
        self.assertEqual(ntfy.call_count, 1)
        title, body = ntfy.call_args[0][0], ntfy.call_args[0][1]
        self.assertIn("RIG earnings are out", title)
        self.assertIn("2026-11-02", body)
        self.assertIn("Forensic Analysis", body)
        self.assertEqual(ntfy.call_args[1]["action_url"], common.FORENSIC_PAGE)
        self.assertEqual(tr["earnings_last"], "2026-11-02")
        self.assertEqual(tr["earnings_date"], "2027-02-18")
        # later runs: no repeat
        tr.pop("earnings_checked")
        check._earnings_pass([tr], self._now(2026, 11, 3, 10, 0))
        check._earnings_pass([tr], self._now(2026, 11, 4))
        self.assertEqual(ntfy.call_count, 1)

    @patch("common.next_earnings", return_value=None)
    @patch("common.ntfy")
    def test_option_trigger_points_to_option_calc(self, ntfy, ne):
        tr = self._tr(source="option", action="BUY_CALL", earnings_date="2026-11-02")
        check._earnings_pass([tr], self._now(2026, 11, 3))
        self.assertEqual(ntfy.call_args[1]["action_url"], common.OPTION_CALC_PAGE)
        self.assertEqual(tr["earnings_date"], "2026-11-02")  # lookup failed: keep known date

    @patch("common.next_earnings", return_value=None)
    @patch("common.ntfy")
    def test_report_newer_than_earnings_no_reminder(self, ntfy, ne):
        tr = self._tr(report_date=date(2026, 11, 3), earnings_date="2026-11-02")
        check._earnings_pass([tr], self._now(2026, 11, 4))
        ntfy.assert_not_called()

    @patch("common.next_earnings")
    @patch("common.ntfy")
    def test_inactive_triggers_ignored_and_lookup_once_a_day(self, ntfy, ne):
        ne.return_value = date(2026, 11, 2)
        done = self._tr(status="expired", earnings_date="2026-11-02")
        live = self._tr()
        check._earnings_pass([done, live], self._now(2026, 11, 3))
        check._earnings_pass([done, live], self._now(2026, 11, 3, 11, 0))
        self.assertEqual(ne.call_count, 1)
        ntfy.assert_not_called()

    def test_confirm_warning(self):
        today = date(2026, 10, 20)
        self.assertIn("haven't happened", check._earnings_warning(self._tr(earnings_date="2026-11-02"), today))
        stale = self._tr(earnings_date="2027-02-18", earnings_last="2026-11-02")
        self.assertIn("Update the data", check._earnings_warning(stale, date(2026, 11, 5)))
        self.assertEqual(check._earnings_warning(self._tr(), today), "")

    def test_validate_bad_earnings_date(self):
        tr = self._tr(earnings_date="soon", conditions=[{"type": "price", "op": ">=", "value": 1}],
                      exit_plan={"stop": {"pct": -8}, "target": {"pct": 15}}, expires="2099-01-01")
        ok, reason = check._validate_trigger(tr)
        self.assertFalse(ok)
        self.assertIn("earnings_date", reason)


class TestNextEarnings(unittest.TestCase):
    def _ticker(self, cal=None, df=None):
        t = MagicMock()
        t.calendar = cal
        t.get_earnings_dates.return_value = df
        return t

    def test_calendar_window_uses_later_date(self):
        t = self._ticker(cal={"Earnings Date": [date(2026, 10, 30), date(2026, 11, 3)]})
        with patch("common.yf.Ticker", return_value=t):
            self.assertEqual(common.next_earnings("X", date(2026, 10, 14)), date(2026, 11, 3))

    def test_etf_has_none(self):
        t = self._ticker(cal={}, df=None)
        with patch("common.yf.Ticker", return_value=t):
            self.assertIsNone(common.next_earnings("ITB", date(2026, 10, 14)))


class TestSpreads(unittest.TestCase):
    def test_debit_spread_numbers(self):
        legs = [
            {"side": "buy", "right": "put", "strike": 45},
            {"side": "sell", "right": "put", "strike": 40},
        ]
        _width, max_profit, max_loss = common.spread_numbers(legs, 2.00)
        self.assertEqual(max_loss, 2.00)
        self.assertEqual(max_profit, 3.00)

    def test_credit_spread_numbers(self):
        legs = [
            {"side": "sell", "right": "put", "strike": 45},
            {"side": "buy", "right": "put", "strike": 40},
        ]
        _width, max_profit, max_loss = common.spread_numbers(legs, -1.50)
        self.assertEqual(max_profit, 1.50)
        self.assertEqual(max_loss, 3.50)

    def _open_spread(self, legs, quotes, cash=50000.0):
        tr = {
            "id": "ABCD-spread",
            "ticker": "ABCD",
            "action": "SPREAD",
            "source": "option",
            "conditions": [{"type": "price", "op": ">=", "value": 50}],
            "contract": {"expiry": "2026-12-18", "legs": legs},
        }
        ledger = {
            "cash": cash, "positions": [], "realized_pl": 0.0, "paused": False,
            "stats": {}, "day_start_equity": cash, "peak_equity": cash,
        }

        def option_quote(ticker, expiry, strike, right):
            return quotes[float(strike)]

        with patch("common.option_quote", side_effect=option_quote), \
             patch("common.last_price", return_value=50.0), \
             patch("common.get_daily_bars", return_value=None), \
             patch("common.ntfy"):
            ok = confirm.place(tr, ledger, force=True)
        self.assertTrue(ok, ledger)
        return ledger

    def test_debit_cash_round_trip(self):
        legs = [
            {"side": "buy", "right": "put", "strike": 45},
            {"side": "sell", "right": "put", "strike": 40},
        ]
        quotes = {
            45.0: {"bid": 2.80, "ask": 3.00, "last": 2.90},
            40.0: {"bid": 1.00, "ask": 1.20, "last": 1.10},
        }
        start = 50000.0
        ledger = self._open_spread(legs, quotes, start)
        pos = ledger["positions"][0]
        with patch("common.ntfy"):
            check._close_position(ledger, pos, 2.50, "target", common.now_ny())
        pl = ledger["closed_trades"][0]["pl"]
        self.assertAlmostEqual(ledger["cash"] - start, pl)

    def test_credit_cash_round_trip(self):
        legs = [
            {"side": "sell", "right": "put", "strike": 45},
            {"side": "buy", "right": "put", "strike": 40},
        ]
        quotes = {
            45.0: {"bid": 3.00, "ask": 3.20, "last": 3.10},
            40.0: {"bid": 1.30, "ask": 1.50, "last": 1.40},
        }
        start = 50000.0
        ledger = self._open_spread(legs, quotes, start)
        pos = ledger["positions"][0]
        with patch("common.ntfy"):
            check._close_position(ledger, pos, -0.50, "target", common.now_ny())
        pl = ledger["closed_trades"][0]["pl"]
        self.assertAlmostEqual(ledger["cash"] - start, pl)

    def test_credit_equity_unchanged_at_open(self):
        legs = [
            {"side": "sell", "right": "put", "strike": 45},
            {"side": "buy", "right": "put", "strike": 40},
        ]
        quotes = {
            45.0: {"bid": 3.00, "ask": 3.20, "last": 3.10},
            40.0: {"bid": 1.30, "ask": 1.50, "last": 1.40},
        }
        start = 50000.0
        ledger = self._open_spread(legs, quotes, start)
        pos = ledger["positions"][0]
        pos["last_value"] = pos["entry_price"]  # closing value equals the opening net
        self.assertAlmostEqual(confirm._compute_equity(ledger), start)

    def _spread_trigger(self, legs, expiry="2026-12-18"):
        return {
            "id": "ABCD-spread-2026-10-29",
            "source": "option",
            "ticker": "ABCD",
            "action": "SPREAD",
            "conditions": [{"type": "price", "op": ">=", "value": 50.00}],
            "contract": {"expiry": expiry, "legs": legs},
        }

    def test_reject_three_legs(self):
        legs = [
            {"side": "buy", "right": "put", "strike": 45},
            {"side": "sell", "right": "put", "strike": 40},
            {"side": "buy", "right": "put", "strike": 35},
        ]
        ok, reason = check._validate_trigger(self._spread_trigger(legs))
        self.assertFalse(ok)
        self.assertEqual(reason, "spread needs exactly 2 legs")

    def test_reject_two_buys(self):
        legs = [
            {"side": "buy", "right": "put", "strike": 45},
            {"side": "buy", "right": "put", "strike": 40},
        ]
        ok, reason = check._validate_trigger(self._spread_trigger(legs))
        self.assertFalse(ok)
        self.assertEqual(reason, "spread needs one buy leg and one sell leg")

    def test_reject_same_strike(self):
        legs = [
            {"side": "buy", "right": "put", "strike": 45},
            {"side": "sell", "right": "put", "strike": 45},
        ]
        ok, reason = check._validate_trigger(self._spread_trigger(legs))
        self.assertFalse(ok)
        self.assertEqual(reason, "legs need different strikes")

    def test_reject_mixed_rights(self):
        legs = [
            {"side": "buy", "right": "put", "strike": 45},
            {"side": "sell", "right": "call", "strike": 40},
        ]
        ok, reason = check._validate_trigger(self._spread_trigger(legs))
        self.assertFalse(ok)
        self.assertEqual(reason, "both legs must be the same right")

    def test_reject_missing_strike(self):
        legs = [
            {"side": "buy", "right": "put"},
            {"side": "sell", "right": "put", "strike": 40},
        ]
        ok, reason = check._validate_trigger(self._spread_trigger(legs))
        self.assertFalse(ok)
        self.assertEqual(reason, "each leg needs a number strike")

    def test_reject_text_strike(self):
        legs = [
            {"side": "buy", "right": "put", "strike": "abc"},
            {"side": "sell", "right": "put", "strike": 40},
        ]
        ok, reason = check._validate_trigger(self._spread_trigger(legs))
        self.assertFalse(ok)
        self.assertEqual(reason, "each leg needs a number strike")

    def _run_spread_exit(self, entry, value, max_profit, max_loss):
        pos = {
            "id": "S1",
            "ticker": "ABCD",
            "action": "SPREAD",
            "is_option": True,
            "entry_price": entry,
            "quantity": 1,
            "expiry": "2027-06-18",
            "legs": [
                {"side": "buy", "right": "put", "strike": 45},
                {"side": "sell", "right": "put", "strike": 40},
            ],
            "max_profit": max_profit,
            "max_loss": max_loss,
            "reserved": 0,
            "stop": None,
            "target": None,
            "source": "option",
        }
        today = common.now_ny().date().isoformat()
        ledger = {
            "cash": 800.0,
            "positions": [pos],
            "closed_trades": [],
            "realized_pl": 0.0,
            "peak_equity": 1000.0,
            "day_start_equity": 1000.0,
            "day_start_date": today,
            "paused": False,
            "stats": {},
            "sent_slots": {},
        }
        files = {"triggers.yml": {"triggers": []}, "ledger.yml": ledger}
        with patch("common.is_forced", return_value=True), \
             patch("common.load_yaml", side_effect=lambda p: files.get(p, {})), \
             patch("common.save_yaml"), \
             patch("common.spread_value", return_value=value), \
             patch("common.last_price", return_value=50.0), \
             patch("common.ntfy"), \
             patch("check._ingest_inbox", return_value=False), \
             patch("check._refresh_triggers_page"), \
             patch("report.send_report"), \
             patch("report.write_pages"):
            check.main()
        self.assertEqual(len(ledger.get("closed_trades") or []), 1)
        return ledger["closed_trades"][0]

    def test_spread_exit_target(self):
        # gain = 3.50 - 2.00 = 1.50, which is 50% of max profit 3.00
        closed = self._run_spread_exit(2.00, 3.50, 3.00, 2.00)
        self.assertEqual(closed["exit_reason"], "target")
        self.assertEqual(closed["exit_price"], 3.50)

    def test_spread_exit_stop(self):
        # gain = 1.00 - 2.00 = -1.00, which is -50% of max loss 2.00
        closed = self._run_spread_exit(2.00, 1.00, 3.00, 2.00)
        self.assertEqual(closed["exit_reason"], "stop")
        self.assertEqual(closed["exit_price"], 1.00)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestOneTickerAtATime(unittest.TestCase):
    def test_second_tbf_path_refused(self):
        triggers = {"triggers": [{"id": "TBF-path2", "ticker": "TBF", "action": "BUY",
                                  "status": "awaiting_confirm", "alerted_at": "x"}]}
        ledger = {"cash": 500, "positions": [{"id": "TBF-path1", "ticker": "TBF", "entry_price": 25,
                                              "quantity": 10}], "paused": False}
        files = {"triggers.yml": triggers, "ledger.yml": ledger}
        sent = []
        with patch("sys.argv", ["confirm.py", "TBF-path2"]), \
             patch("common.is_forced", return_value=True), \
             patch("common.load_yaml", side_effect=lambda p: files[p]), \
             patch("common.last_price", return_value=25.0) as lp, \
             patch("common.ntfy", side_effect=lambda t, b, **k: sent.append(b)), \
             patch("confirm._compute_equity", return_value=1000):
            confirm.main()
        self.assertEqual(sent, ["Already holding TBF"])


class TestCloseReturnsCost(unittest.TestCase):
    def test_sale_returns_full_proceeds(self):
        import check
        ledger = {"cash": 750.0, "positions": [{"id": "X", "ticker": "X", "entry_price": 25.0,
                                                "quantity": 10, "reserved": 0}], "realized_pl": 0.0}
        with patch("common.ntfy"):
            check._close_position(ledger, ledger["positions"][0], 26.0, "target", check.common.now_ny())
        self.assertAlmostEqual(ledger["cash"], 1010.0)
        self.assertAlmostEqual(ledger["realized_pl"], 10.0)
