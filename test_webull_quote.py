"""Webull live option quotes, with Yahoo as the backup (2026-11-20)."""
from datetime import date
from unittest import mock

import common


class FakeRes:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


def _run(res, monkeypatch):
    monkeypatch.setenv("WEBULL_APP_KEY", "k")
    monkeypatch.setenv("WEBULL_APP_SECRET", "s")
    with mock.patch("webull.core.client.ApiClient"), \
         mock.patch("webull.data.quotes.option_market_data.OptionMarketData.get_option_snapshot", return_value=res) as m:
        q = common._webull_option_quote("nbis", date(2026, 12, 18), 115, "put")
    return q, m


def test_symbol_and_values(monkeypatch):
    q, m = _run(FakeRes(200, [{"bid": "23.70", "ask": "23.85", "price": "23.65"}]), monkeypatch)
    assert m.call_args[0][0] == "NBIS261218P00115000"
    assert q == {"bid": 23.70, "ask": 23.85, "last": 23.65}


def test_half_dollar_strike_and_string_date(monkeypatch):
    monkeypatch.setenv("WEBULL_APP_KEY", "k")
    monkeypatch.setenv("WEBULL_APP_SECRET", "s")
    with mock.patch("webull.core.client.ApiClient"), \
         mock.patch("webull.data.quotes.option_market_data.OptionMarketData.get_option_snapshot",
                    return_value=FakeRes(200, [{"bid": "1", "ask": "2", "price": "1.5"}])) as m:
        common._webull_option_quote("CRWV", "2027-01-15", 52.5, "call")
    assert m.call_args[0][0] == "CRWV270115C00052500"


def test_bad_answer_falls_back(monkeypatch):
    q, _ = _run(FakeRes(403, {"msg": "no permission"}), monkeypatch)
    assert q is None


def test_no_keys_skips_webull(monkeypatch):
    monkeypatch.delenv("WEBULL_APP_KEY", raising=False)
    monkeypatch.delenv("WEBULL_APP_SECRET", raising=False)
    assert common._webull_option_quote("NBIS", date(2026, 12, 18), 115, "put") is None


def test_option_quote_uses_yahoo_when_webull_fails(monkeypatch):
    with mock.patch.object(common, "_webull_option_quote", return_value=None), \
         mock.patch.object(common, "_yf_call", return_value=None):
        assert common.option_quote("NBIS", "2026-12-18", 115, "put") is None


def test_failure_never_shows_key(monkeypatch, capsys, caplog):
    """A failed Webull call (here: no network) must not print or log the key."""
    monkeypatch.setenv("WEBULL_APP_KEY", "SECRETKEY123")
    monkeypatch.setenv("WEBULL_APP_SECRET", "SECRETSEC456")
    with mock.patch("webull.core.client.ApiClient.get_response", side_effect=RuntimeError("boom")):
        assert common._webull_option_quote("NBIS", date(2026, 12, 18), 115, "put") is None
    shown = capsys.readouterr().out + capsys.readouterr().err + caplog.text
    assert "SECRETKEY123" not in shown and "SECRETSEC456" not in shown
