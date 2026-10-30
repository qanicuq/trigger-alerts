import os
import time
import yaml
import requests
import yfinance as yf
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

NTFY_URL = "https://ntfy.sh/" + os.environ.get("NTFY_TOPIC", "")
FORENSIC_PAGE = "https://claude.ai/artifact/5EbzJAVRmDf7fvZCbe7eDF"
OPTION_CALC_PAGE = "https://claude.ai/artifact/LmJchqjk1dftRCJD5RHyjW"
NY = ZoneInfo("America/New_York")

# Account and risk constants
STARTING_CASH = 1000.0
KELLY_FRACTION = 0.25
PRIOR_ALPHA = 5
PRIOR_BETA = 5
BACKTEST_WEIGHT = 0.5
WARMUP_TRADES = 20
WARMUP_RISK = 0.01
MAX_RISK_PER_TRADE = 0.05
MAX_POSITION_STOCK = 0.25
MAX_POSITION_OPTION = 0.10
MAX_OPEN_POSITIONS = None  # None = unlimited (user, 2026-10-30). Original setting was 4.
DAILY_LOSS_LIMIT = 0.03
MAX_DRAWDOWN = 0.10
OPTION_TARGET = 0.20
OPTION_STOP = 0.20
OPTION_TIME_EXIT_DAYS = 7
SPREAD_TARGET = 0.50
SPREAD_STOP = 0.50

# Full-day NYSE holidays 2026-2027 (no early closes)
HOLIDAYS = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day observed
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
    date(2027, 1, 1),   # New Year's Day
    date(2027, 1, 18),  # MLK Day
    date(2027, 2, 15),  # Presidents Day
    date(2027, 3, 26),  # Good Friday
    date(2027, 5, 31),  # Memorial Day
    date(2027, 6, 18),  # Juneteenth observed
    date(2027, 7, 5),   # Independence Day observed
    date(2027, 9, 6),   # Labor Day
    date(2027, 11, 25), # Thanksgiving
    date(2027, 12, 24), # Christmas observed
}


FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
_fred_cache = {}
MACRO_ERRORS = []  # filled during a run; check.py sends one alert per day if non-empty


def fred_values(series, count):
    """Latest `count` valid observations for a FRED series, newest first: [(date_str, float), ...].
    Returns [] on any failure and records the error in MACRO_ERRORS."""
    if series in _fred_cache and len(_fred_cache[series]) >= count:
        return _fred_cache[series][:count]
    key = os.environ.get("FRED_API_KEY")
    if not key:
        MACRO_ERRORS.append("FRED_API_KEY secret is not set")
        return []
    obs = None
    for attempt in range(3):  # FRED has brief outages; retry before alerting
        try:
            r = requests.get(FRED_URL, params={
                "series_id": series, "api_key": key, "file_type": "json",
                "sort_order": "desc", "limit": count + 10,
            }, timeout=15)
            r.raise_for_status()
            obs = [(o["date"], float(o["value"])) for o in r.json().get("observations", []) if o.get("value") not in (None, ".", "")]
            break
        except Exception as e:
            err = type(e).__name__
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code:
                err += f" {code}"
            if attempt < 2:
                time.sleep(10)
    if obs is None:
        MACRO_ERRORS.append(f"FRED {series}: {err} (failed 3 tries)")
        return []
    _fred_cache[series] = obs
    return obs[:count]


def expectancy(closed_trades):
    """Average profit/loss per closed trade = (W/T x avg win) - (L/T x avg loss). Returns (dollars, count)."""
    pls = [float(c.get("pl") or 0) for c in closed_trades or []]
    if not pls:
        return None, 0
    return sum(pls) / len(pls), len(pls)


def load_yaml(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def ntfy(title, body, priority="default", click_url=None, action_label=None, action_url=None):
    headers = {"Title": title, "Priority": priority}
    if click_url:
        headers["Click"] = click_url
    if action_label and action_url:
        headers["Actions"] = f"view, {action_label}, {action_url}"
    try:
        requests.post(NTFY_URL, data=body.encode(), headers=headers, timeout=10)
    except Exception as e:
        print(f"ntfy failed: {e}")


def now_ny():
    return datetime.now(NY)


def is_forced():
    """True only when FORCE env is exactly 'true' (case-insensitive)."""
    return os.environ.get("FORCE", "").lower() == "true"


def market_open(force=False):
    if force:
        return True
    dt = now_ny()
    if dt.weekday() >= 5 or dt.date() in HOLIDAYS:
        return False
    t = dt.time()
    return dtime(9, 30) <= t <= dtime(16, 45)


def _yf_call(func, *args, **kwargs):
    for attempt in range(2):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"yfinance attempt {attempt + 1} failed: {e}")
            if attempt == 0:
                time.sleep(1)
    return None


def last_price(ticker):
    t = _yf_call(yf.Ticker, ticker)
    if t is None:
        return None
    hist = _yf_call(lambda: t.history(period="1d", interval="1m"))
    if hist is not None and not hist.empty:
        return float(hist["Close"].iloc[-1])
    info = _yf_call(lambda: t.info)
    if info:
        return info.get("regularMarketPrice") or info.get("currentPrice")
    return None


def high_low_since(ticker, since_iso):
    t = _yf_call(yf.Ticker, ticker)
    if t is None:
        return None, None
    hist = _yf_call(lambda: t.history(period="1d", interval="5m"))
    if hist is None or hist.empty:
        return None, None
    since = None
    if since_iso:
        try:
            since = datetime.fromisoformat(since_iso)
            if since.tzinfo is None:
                since = since.replace(tzinfo=NY)
        except Exception:
            since = None
    highs = []
    lows = []
    for idx, row in hist.iterrows():
        bar_time = idx.to_pydatetime()
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=NY)
        else:
            bar_time = bar_time.astimezone(NY)
        if since is None or bar_time > since:
            highs.append(float(row["High"]))
            lows.append(float(row["Low"]))
    if not highs:
        return None, None
    return max(highs), min(lows)


def _to_date(v):
    """date from a date, datetime, pandas Timestamp or 'YYYY-MM-DD' string, else None."""
    try:
        if isinstance(v, datetime):
            return v.astimezone(NY).date() if v.tzinfo else v.date()
        if isinstance(v, date):
            return v
        if hasattr(v, "to_pydatetime"):
            return _to_date(v.to_pydatetime())
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def next_earnings(ticker, today=None):
    """Next earnings date on or after `today` (New York), or None (ETFs, or Yahoo doesn't know).
    When Yahoo only gives a window (two dates), the later one is used so a reminder never comes early."""
    today = today or now_ny().date()
    t = _yf_call(yf.Ticker, ticker)
    if t is None:
        return None
    cal = _yf_call(lambda: t.calendar)
    if isinstance(cal, dict):
        cal_dates = [d for d in (_to_date(x) for x in (cal.get("Earnings Date") or [])) if d]
        if cal_dates and max(cal_dates) >= today:
            return max(cal_dates)
    df = _yf_call(lambda: t.get_earnings_dates(limit=8))
    if df is not None and not getattr(df, "empty", True):
        future = sorted(d for d in (_to_date(ts) for ts in df.index) if d and d >= today)
        if future:
            return future[0]
    return None


def get_daily_bars(ticker, period="2y"):
    t = _yf_call(yf.Ticker, ticker)
    if t is None:
        return None
    hist = _yf_call(lambda: t.history(period=period, interval="1d"))
    if hist is None or hist.empty:
        return None
    return hist


def compute_refs(hist):
    """Compute reference levels from daily bars DataFrame."""
    if hist is None or hist.empty:
        return {}
    closes = hist["Close"].dropna().tolist()
    highs = hist["High"].dropna().tolist()
    lows = hist["Low"].dropna().tolist()
    refs = {}
    if len(closes) >= 20:
        refs["sma20"] = sum(closes[-20:]) / 20
    if len(closes) >= 50:
        refs["sma50"] = sum(closes[-50:]) / 50
    if len(closes) >= 200:
        refs["sma200"] = sum(closes[-200:]) / 200
    # EMA20 seeded with first 20-day SMA
    if len(closes) >= 20:
        k = 2.0 / 21.0
        ema = sum(closes[:20]) / 20
        for c in closes[20:]:
            ema = c * k + ema * (1 - k)
        refs["ema20"] = ema
    if highs:
        refs["52w_high"] = max(highs)
    if lows:
        refs["52w_low"] = min(lows)
    if len(lows) >= 20:
        refs["support20"] = min(lows[-20:])
    if len(highs) >= 20:
        refs["resistance20"] = max(highs[-20:])
    return refs


def wilder_rsi(closes, period=14):
    """Wilder RSI on daily closes."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def volume_ratio(hist):
    """Today's volume so far / average daily volume of previous 20 days."""
    if hist is None or len(hist) < 21:
        return None
    vols = hist["Volume"].dropna().tolist()
    if len(vols) < 21:
        return None
    today_vol = vols[-1]
    avg_prev = sum(vols[-21:-1]) / 20
    if avg_prev == 0:
        return None
    return today_vol / avg_prev


def option_quote(ticker, expiry, strike, right):
    t = _yf_call(yf.Ticker, ticker)
    if t is None:
        return None
    exp_str = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    chain = _yf_call(lambda: t.option_chain(exp_str))
    if chain is None:
        return None
    df = chain.puts if str(right).lower() == "put" else chain.calls
    if df is None or df.empty:
        return None
    match = df[df["strike"] == float(strike)]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "bid": float(row["bid"] or 0),
        "ask": float(row["ask"] or 0),
        "last": float(row["lastPrice"] or 0),
    }


def spread_value(ticker, expiry, legs, opening):
    """Net price of a two-leg spread. Opening uses the price we would pay; closing uses the price we would get."""
    bought = None
    sold = None
    for leg in legs:
        q = option_quote(ticker, expiry, leg.get("strike"), leg.get("right"))
        if q is None:
            return None
        side = str(leg.get("side") or "").lower()
        if opening:
            px = q["ask"] if side == "buy" else q["bid"]
        else:
            px = q["bid"] if side == "buy" else q["ask"]
        use_ask = (opening and side == "buy") or ((not opening) and side == "sell")
        if use_ask and px == 0:
            return None
        if side == "buy":
            bought = px
        elif side == "sell":
            sold = px
    if bought is None or sold is None:
        return None
    return bought - sold


def spread_numbers(legs, net):
    """Width and max profit/loss per share for a two-leg spread. net > 0 is a debit."""
    width = abs(float(legs[0]["strike"]) - float(legs[1]["strike"]))
    if net >= 0:
        max_loss = net
        max_profit = width - net
    else:
        credit = -net
        max_profit = credit
        max_loss = width - credit
    return (width, max_profit, max_loss)


def resolve_level(level_spec, refs, entry=None):
    """Resolve stop/target or condition value from spec to a number."""
    if not isinstance(level_spec, dict):
        return level_spec
    if "price" in level_spec:
        return float(level_spec["price"])
    if "pct" in level_spec:
        if entry is None:
            return None
        return entry * (1 + float(level_spec["pct"]) / 100.0)
    if "ref" in level_spec:
        ref_name = level_spec["ref"]
        ref_val = refs.get(ref_name)
        if ref_val is None:
            return None
        pct = float(level_spec.get("pct", 0))
        return ref_val * (1 + pct / 100.0)
    return None
