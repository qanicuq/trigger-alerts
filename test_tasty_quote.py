"""Live option quotes from tastytrade (read-only), Yahoo as the backup (2026-11-20)."""
from datetime import date
from unittest import mock

import common


class R:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


TOKEN = R(200, {"access_token": "AT"})
QUOTE = R(200, {"data": {"items": [{"bid": "23.7", "ask": "23.85", "last": "23.65"}]}})


def _keys(monkeypatch):
    for k, v in (("TT_CLIENT_ID", "cid"), ("TT_CLIENT_SECRET", "SECRETSEC456"), ("TT_REFRESH_TOKEN", "SECRETREF789")):
        monkeypatch.setenv(k, v)
    common._tt_token = None


def test_quote_symbol_read_only_and_token_reuse(monkeypatch):
    _keys(monkeypatch)
    with mock.patch.object(common.requests, "post", return_value=TOKEN) as post, \
         mock.patch.object(common.requests, "get", return_value=QUOTE) as get:
        q1 = common._tasty_option_quote("nbis", date(2026, 12, 18), 115, "put")
        q2 = common._tasty_option_quote("CRWV", "2027-01-15", 52.5, "call")
    assert q1 == {"bid": 23.7, "ask": 23.85, "last": 23.65}
    assert post.call_count == 1                      # one sign-in per run
    assert post.call_args.kwargs["json"]["scope"] == "read"
    assert get.call_args_list[0].kwargs["params"] == {"equity-option": "NBIS  261218P00115000"}
    assert get.call_args_list[1].kwargs["params"] == {"equity-option": "CRWV  270115C00052500"}
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer AT"
    assert q2 is not None


def test_failed_sign_in_uses_yahoo_and_hides_secrets(monkeypatch, capsys):
    _keys(monkeypatch)
    with mock.patch.object(common.requests, "post", return_value=R(401, {"error": "bad"})):
        assert common._tasty_option_quote("NBIS", date(2026, 12, 18), 115, "put") is None
    out = capsys.readouterr().out
    assert "SECRETSEC456" not in out and "SECRETREF789" not in out


def test_network_error_uses_yahoo_and_hides_secrets(monkeypatch, capsys):
    _keys(monkeypatch)
    with mock.patch.object(common.requests, "post", side_effect=RuntimeError("SECRETREF789 boom")):
        assert common._tasty_option_quote("NBIS", date(2026, 12, 18), 115, "put") is None
    assert "SECRETREF789" not in capsys.readouterr().out


def test_empty_quote_uses_yahoo(monkeypatch):
    _keys(monkeypatch)
    with mock.patch.object(common.requests, "post", return_value=TOKEN), \
         mock.patch.object(common.requests, "get", return_value=R(200, {"data": {"items": []}})):
        assert common._tasty_option_quote("NBIS", date(2026, 12, 18), 115, "put") is None


def test_no_secrets_skips_tastytrade(monkeypatch):
    for k in ("TT_CLIENT_ID", "TT_CLIENT_SECRET", "TT_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with mock.patch.object(common.requests, "post") as post:
        assert common._tasty_option_quote("NBIS", date(2026, 12, 18), 115, "put") is None
    post.assert_not_called()


def test_option_quote_falls_back_to_yahoo(monkeypatch):
    with mock.patch.object(common, "_tasty_option_quote", return_value=None), \
         mock.patch.object(common, "_yf_call", return_value=None) as yf:
        assert common.option_quote("NBIS", "2026-12-18", 115, "put") is None
    yf.assert_called()


def test_refusal_reason_is_printed(monkeypatch, capsys):
    _keys(monkeypatch)
    with mock.patch.object(common.requests, "post", return_value=TOKEN), \
         mock.patch.object(common.requests, "get", return_value=R(403, {"error": {"code": "not_permitted", "message": "No data"}})):
        assert common._tasty_option_quote("NBIS", date(2026, 12, 18), 115, "put") is None
    assert "not_permitted: No data" in capsys.readouterr().out
