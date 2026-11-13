"""FA Needed page and the morning reminder.

RED = blocked until a fresh Forensic Analysis runs.
YELLOW = earnings within 14 days, so an FA will be needed soon.
GREEN = a new trigger was sent to the bot in the last 24 hours.
"""
import os
import sys
from datetime import date, datetime, timedelta

import common
import check

PAGE = "FA_NEEDED.md"
FA_URL = "https://github.com/qanicuq/trigger-alerts/blob/main/FA_NEEDED.md"
SKIP_STATUS = {"paused", "cancelled", "expired"}


def _text(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _inbox_tickers():
    """Tickers with a file sitting in inbox/ (not inbox/rejected/). The fresh FA is already here."""
    found = set()
    folder = "inbox"
    if not os.path.isdir(folder):
        return found
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            data = common.load_yaml(path)
        except Exception:
            continue
        ticker = None
        if isinstance(data, dict) and data.get("ticker"):
            ticker = data.get("ticker")
        elif isinstance(data, dict):
            rows = data.get("triggers") or []
            if rows and isinstance(rows[0], dict):
                ticker = rows[0].get("ticker")
        if ticker:
            found.add(str(ticker))
    return found


def _within_24h(added_at, now):
    """True when added_at is less than 24 hours before now."""
    if not added_at or not isinstance(now, datetime):
        return False
    try:
        added = datetime.strptime(str(added_at), "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    if now.tzinfo is not None:
        added = added.replace(tzinfo=now.tzinfo)
    delta = now - added
    return timedelta(0) <= delta < timedelta(hours=24)


def _money(target):
    try:
        return f"${float(target):.2f}"
    except (TypeError, ValueError):
        return f"${target}"


def _earnings_red(tr, now):
    """The earnings date whose results tr's report doesn't have yet (the bot won't buy on it), else None."""
    last = common._to_date(tr.get("earnings_last"))
    if last and check._needs_refresh(tr):
        return last
    ed = common._to_date(tr.get("earnings_date"))
    timing = tr.get("earnings_time")
    if ed and now >= check._reminder_moment(ed, timing) and not check._covers(tr, ed, timing):
        return ed  # out since this morning; the next market-hours check records it
    return None


def build_lists(triggers, ledger, now):
    """Return (red, yellow, green). Each item is {ticker, reason, since}."""
    today = now.date() if isinstance(now, datetime) else now
    inbox = _inbox_tickers()
    red = []
    seen = set()

    open_ids = {p.get("id") for p in (ledger or {}).get("positions") or []}
    for tr in triggers or []:
        if not isinstance(tr, dict):
            continue
        status = tr.get("status")
        held = status == "filled" and tr.get("id") in open_ids
        if status not in ("active", "awaiting_confirm") and not held:
            continue  # paused (the old Nov 6 triggers), cancelled, expired, or closed
        ticker = tr.get("ticker")
        if not ticker or ticker in seen or ticker in inbox:
            continue
        if status == "active" and tr.get("fa_requested"):
            reason = "no cash, and weaker than your weakest holding"
            since = str(tr.get("fa_requested"))[:10]
        else:
            ed = _earnings_red(tr, now)
            if ed is None:
                continue
            reason = f"earnings came out {ed.isoformat()}; the report is on old numbers"
            rd = common._to_date(tr.get("report_date"))
            last = common._to_date(tr.get("earnings_last"))
            timing = tr.get("earnings_last_time") if last == ed else tr.get("earnings_time")
            if rd and not check._stale(rd, ed, timing):  # FA was written after the results came out
                dt = common._to_date(tr.get("data_through"))
                newest = f" (newest quarter ends {dt.isoformat()})" if dt else ""
                reason = (f"FA already run {rd.isoformat()}, but Webull doesn't have the new quarter yet"
                          f"{newest}; run FA again once it does")
            since = ed.isoformat()
        red.append({"ticker": ticker, "reason": reason, "since": since})
        seen.add(ticker)

    for pos in (ledger or {}).get("positions") or []:
        if "fa_needed_since" not in pos or not pos.get("fa_needed_since"):
            continue
        ticker = pos.get("ticker")
        if not ticker or ticker in seen or ticker in inbox:
            continue
        red.append({
            "ticker": ticker,
            "reason": f"hit its target of {_money(pos.get('target'))}",
            "since": _text(pos.get("fa_needed_since")),
        })
        seen.add(ticker)

    red.sort(key=lambda x: x["ticker"])
    red_tickers = {x["ticker"] for x in red}

    yellow = {}
    green = {}
    for tr in triggers or []:
        if not isinstance(tr, dict) or tr.get("status") in SKIP_STATUS:
            continue
        ticker = tr.get("ticker")
        if not ticker:
            continue
        status = tr.get("status")
        counts = status == "active" or (status == "filled" and tr.get("id") in open_ids)
        if counts and ticker not in red_tickers and ticker not in yellow:
            ed = check._earnings_soon(tr, today)
            if ed is not None:
                yellow[ticker] = {
                    "ticker": ticker,
                    "reason": f"earnings on {ed.isoformat()}",
                    "since": _yellow_since(tr, ed),
                }
        if status == "active" and ticker not in green and ticker not in red_tickers and _within_24h(tr.get("added_at"), now):
            green[ticker] = {
                "ticker": ticker,
                "reason": "trigger sent to the bot",
                "since": _text(tr.get("added_at")),
            }

    yellow_list = sorted(yellow.values(), key=lambda x: x["ticker"])
    green_list = sorted(green.values(), key=lambda x: x["ticker"])
    return red, yellow_list, green_list


def _yellow_since(tr, ed):
    """The day the trigger came within 14 days of earnings (or its report date, if later)."""
    start = ed - timedelta(days=check.EARNINGS_WINDOW_DAYS)
    rd = common._to_date(tr.get("report_date"))
    return max(start, rd).isoformat() if rd else start.isoformat()


def _section(heading, rows, run_link=True):
    lines = [heading, ""]
    if not rows:
        lines.append("Nothing here right now.")
        return "\n".join(lines)
    if not run_link:
        lines.append("| Ticker | Why | Since |")
        lines.append("| --- | --- | --- |")
        for row in rows:
            lines.append(f"| {row['ticker']} | {row['reason']} | {row['since']} |")
        return "\n".join(lines)
    link = f"[Run FA]({common.FORENSIC_PAGE})"
    lines.append("| Ticker | Why | Since | Run |")
    lines.append("| --- | --- | --- | --- |")
    for row in rows:
        lines.append(f"| {row['ticker']} | {row['reason']} | {row['since']} | {link} |")
    return "\n".join(lines)


def render_page(red, yellow, green, now):
    """Text of FA_NEEDED.md."""
    stamp = now.strftime("%Y-%m-%d %H:%M") if isinstance(now, datetime) else str(now)
    parts = [
        "# FA Needed",
        f"Updated {stamp} New York time",
        "",
        _section("## 🔴 Needs a fresh FA now", red),
        "",
        _section("## 🟡 FA needed soon", yellow),
        "",
        _section("## 🟢 Trigger sent (last 24 hours)", green, run_link=False),
        "",
    ]
    return "\n".join(parts)


def write_page(triggers, ledger, now):
    """Write FA_NEEDED.md only when the text changed. Returns (red, yellow, green)."""
    red, yellow, green = build_lists(triggers, ledger, now)
    text = render_page(red, yellow, green, now)
    try:
        with open(PAGE) as f:
            current = f.read()
    except FileNotFoundError:
        current = None
    if current != text:
        with open(PAGE, "w") as f:
            f.write(text)
    return red, yellow, green


def remind(now):
    """One phone push at reminder time, only while at least one ticker is RED."""
    triggers = (common.load_yaml("triggers.yml") or {}).get("triggers") or []
    ledger = common.load_yaml("ledger.yml") or {}
    red, _, _ = write_page(triggers, ledger, now)
    if not red:
        return
    n = len(red)
    title = f"Fresh FA needed: {n} ticker(s)"
    body = "\n".join(f"{row['ticker']}: {row['reason']} (since {row['since']})" for row in red)
    common.ntfy(
        title,
        body,
        priority="high",
        click_url=FA_URL,
        action_label="Open FA list",
        action_url=FA_URL,
        tags="rotating_light,bar_chart",
    )


if __name__ == "__main__" and "--remind" in sys.argv:
    remind(common.now_ny())
