import common
from datetime import datetime, date


def _pct_away(price, level):
    if price is None or level is None or level == 0:
        return ""
    try:
        return f"{(price - float(level)) / float(level) * 100:.1f}%"
    except Exception:
        return ""


REF_NAMES = {
    "sma20": "20-day average", "sma50": "50-day average", "sma200": "200-day average",
    "ema20": "20-day EMA", "52w_high": "52-week high", "52w_low": "52-week low",
    "support20": "20-day low (support)", "resistance20": "20-day high (resistance)",
}
MACRO_NAMES = {
    "SAHMREALTIME": "Sahm recession indicator", "DGS10": "10-year Treasury yield",
    "VIXCLS": "VIX (fear index)", "CPIAUCNS": "CPI inflation",
    "BAMLH0A0HYM2": "High-yield bond spread", "auction_tail_30y_bp": "30-year auction tail (bp)",
}
SOURCE_NAMES = {"forensic": "Forensic Analysis", "option": "Option Calc", "macro": "Macro"}
OP_WORDS = {">=": "at or above", "<=": "at or below"}
SPREAD_EXITS = "+50% of max profit / −50% of max loss / 7 days before expiry"


def _earn(tr):
    """Next earnings date with its release time, e.g. 'Jan 7, 2027 (before open)'."""
    if not tr or not tr.get("earnings_date"):
        return "—"
    when = {"BMO": " (before open)", "AMC": " (after close)"}.get(tr.get("earnings_time"), "")
    return _day(tr.get("earnings_date")) + when


def _day(v):
    """Any date-like value -> 'Oct 22, 2026'."""
    if not v:
        return "—"
    try:
        return date.fromisoformat(str(v)[:10]).strftime("%b %-d, %Y")
    except Exception:
        return str(v)


def _money(v):
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return "—"


def _spread_desc(legs, expiry):
    bits = [f"{lg.get('side')} {lg.get('strike')} {lg.get('right')}" for lg in (legs or [])]
    return f"{', '.join(bits)}, exp {_day(expiry)}"


def _what(tr):
    action = tr.get("action", "")
    if action == "BUY":
        return "Buy shares"
    if action == "SPREAD":
        c = tr.get("contract") or {}
        legs = c.get("legs") or tr.get("legs")
        expiry = c.get("expiry") or tr.get("expiry")
        return f"{_spread_desc(legs, expiry)} — {SPREAD_EXITS}"
    c = tr.get("contract") or {}
    kind = "call" if action == "BUY_CALL" else "put" if action == "BUY_PUT" else action
    if c:
        return f"Buy {kind} ({_money(c.get('strike'))}, exp {_day(c.get('expiry'))})"
    return f"Buy {kind}"


def _condition(c, resolved):
    """One trigger condition in plain English."""
    t, op = c.get("type"), c.get("op")
    words = OP_WORDS.get(op, op)
    if t == "price":
        return f"Price {words} {_money(c.get('value'))}"
    if t == "price_vs":
        ref = c.get("ref")
        pct = float(c.get("pct", 0))
        name = REF_NAMES.get(ref, ref)
        if op == ">=":
            side = f"at least {pct:g}% above" if pct >= 0 else f"no more than {-pct:g}% below"
        else:
            side = f"at least {-pct:g}% below" if pct <= 0 else f"no more than {pct:g}% above"
        text = f"Price {side} the {name}"
        if resolved.get(ref) is not None:
            text += f" (≈{_money(resolved[ref] * (1 + pct / 100))})"
        return text
    if t == "rsi14":
        return f"RSI {words} {c.get('value')}"
    if t == "volume_ratio":
        return f"Volume {words} {c.get('value')}× normal"
    if t == "touched":
        return f"Price has reached {words} {_money(c.get('value'))} since the trigger was added"
    if t in ("macro", "manual"):
        key = c.get("series") or c.get("key")
        name = MACRO_NAMES.get(key, key)
        if c.get("transform") == "yoy":
            name += " (yearly %)"
        text = f"{name} {words} {c.get('value')}"
        if int(c.get("closes", 1)) > 1:
            text += f" for {c.get('closes')} closes in a row"
        return text
    return str(c)


def _exit_level(spec, resolved_value):
    if resolved_value is not None:
        return _money(resolved_value)
    if not isinstance(spec, dict):
        return "—"
    if "price" in spec:
        return _money(spec["price"])
    if "ref" in spec:
        return f"{float(spec.get('pct', 0)):+g}% vs {REF_NAMES.get(spec['ref'], spec['ref'])}"
    if "pct" in spec:
        return f"{float(spec['pct']):+g}% from buy price"
    return "—"


def _cell(text):
    """Keep table cells on one line and stop | from breaking the table."""
    return str(text).replace("|", "/").replace("\n", " ")


def write_triggers_page():
    """TRIGGERS.md: plain-English summary of every trigger. Uses only saved files (no price lookups),
    so it is rewritten at the end of every bot run, including each time a trigger is added."""
    now = common.now_ny()
    triggers = (common.load_yaml("triggers.yml") or {}).get("triggers") or []
    ledger = common.load_yaml("ledger.yml") or {}
    positions = ledger.get("positions") or []

    watching = [t for t in triggers if t.get("status") in ("active", "awaiting_confirm")]
    bought = [t for t in triggers if t.get("status") == "filled"]
    ended = [t for t in triggers if t.get("status") not in ("active", "awaiting_confirm", "filled")]

    L = [
        "# Triggers",
        "",
        f"Updated {now.strftime('%b %-d, %Y at %-I:%M %p')} New York. This page rewrites itself every time "
        "the bot runs, including each time a trigger is added. Paper money only.",
        "",
        f"**{len(watching)} watching** · **{len(positions)} bought (open)** · **{len(ended)} ended**",
        "",
        "## Watching now",
        "",
        "The bot paper-buys automatically when **every** condition on a line is true.",
        "",
    ]
    if not watching:
        L.append("Nothing is being watched right now.")
    else:
        L += ["| Ticker | What | Buy when (all true) | Sell target | Stop | Next earnings | Watch ends | From |",
              "|---|---|---|---|---|---|---|---|"]
        for tr in watching:
            resolved = tr.get("resolved") or {}
            conds = "<br>".join(_condition(c, resolved) for c in tr.get("conditions") or [])
            if tr.get("action") == "SPREAD":
                target, stop = "+50% of max profit", "−50% of max loss / 7 days before expiry"
            elif tr.get("action", "").startswith("BUY_"):
                target, stop = "+20% on the option", "−20% on the option"
            else:
                ep = tr.get("exit_plan") or {}
                target = _exit_level(ep.get("target"), tr.get("resolved_target"))
                stop = _exit_level(ep.get("stop"), tr.get("resolved_stop"))
            what = _what(tr)
            if tr.get("status") == "awaiting_confirm":
                what += " — waiting for your OK"
            L.append("| " + " | ".join(_cell(x) for x in [
                f"**{tr.get('ticker')}**", what, conds, target, stop,
                _earn(tr), _day(tr.get("expires")),
                f"{SOURCE_NAMES.get(tr.get('source'), tr.get('source'))}, {_day(tr.get('report_date'))}",
            ]) + " |")
        L += ["", "**Notes from each report**", ""]
        for tr in watching:
            L.append(f"- **{tr.get('ticker')}:** {_cell(tr.get('recommendation', ''))}")

    L += ["", "## Bought (open paper trades)", ""]
    if not positions:
        L.append("No open trades.")
    else:
        by_id = {t.get("id"): t for t in triggers}
        L += ["| Ticker | What | Bought at | Amount | Sell target | Stop | Bought on | Next earnings | Note |",
              "|---|---|---|---|---|---|---|---|---|"]
        for p in positions:
            is_opt = p.get("is_option")
            qty = float(p.get("quantity") or 0)
            amount = f"{qty:g} contract(s)" if is_opt else f"{qty:.2f} shares"
            note = f"Flagged to sell at the next check: {p['thesis_broken']}" if p.get("thesis_broken") else ""
            if p.get("action") == "SPREAD":
                what = f"{_spread_desc(p.get('legs'), p.get('expiry'))} — {SPREAD_EXITS}"
                target, stop = "+50% of max profit", "−50% of max loss / 7 days before expiry"
            else:
                what = "Option" if is_opt else "Stock"
                target, stop = _money(p.get("target")), _money(p.get("stop"))
            L.append("| " + " | ".join(_cell(x) for x in [
                f"**{p.get('ticker')}**", what, _money(p.get("entry_price")), amount,
                target, stop, _day(p.get("opened")), _earn(by_id.get(p.get("id"))), note,
            ]) + " |")

    closed = ledger.get("closed_trades") or []
    L += ["", "## Closed paper trades", ""]
    if not closed:
        L.append("None yet.")
    else:
        L += ["| Ticker | Closed on | Why | Profit/loss |", "|---|---|---|---|"]
        for c in reversed(closed[-20:]):
            L.append("| " + " | ".join(_cell(x) for x in [
                f"**{c.get('ticker')}**", _day(c.get("closed")), c.get("exit_reason", ""), _money(c.get("pl")),
            ]) + " |")

    L += ["", "## Ended (no longer watched)", ""]
    if not ended:
        L.append("None.")
    else:
        L += ["| Ticker | What | Added | Status | Why it ended |", "|---|---|---|---|---|"]
        for tr in sorted(ended, key=lambda t: str(t.get("report_date")), reverse=True):
            status = tr.get("status", "")
            why = tr.get("cancel_reason")
            if not why and status == "expired":
                why = ("Conditions were met, but it wasn't confirmed by 4 PM that day" if tr.get("alerted_at")
                       else "Watch window ran out")
            why = why or "No reason saved"
            L.append("| " + " | ".join(_cell(x) for x in [
                f"**{tr.get('ticker')}**", _what(tr), _day(tr.get("report_date")), status.capitalize(), why,
            ]) + " |")

    with open("TRIGGERS.md", "w") as f:
        f.write("\n".join(L) + "\n")
    print("TRIGGERS.md written")


def _scorecard(ledger, closed):
    exp, n = common.expectancy(closed)
    exp_txt = f"${exp:,.2f} per trade ({n} closed)" if n else "n/a (no closed trades yet)"
    return f"Worst drawdown: {float(ledger.get('max_drawdown_pct') or 0):.1f}%  Expectancy: {exp_txt}"


def write_pages():
    now = common.now_ny()
    triggers_data = common.load_yaml("triggers.yml")
    triggers = triggers_data.get("triggers") or []
    ledger = common.load_yaml("ledger.yml") or {}

    cash = float(ledger.get("cash") or 0)
    equity = cash
    for p in ledger.get("positions") or []:
        qty = float(p.get("quantity") or 0)
        entry = float(p.get("entry_price") or 0)
        if p.get("action") == "SPREAD":
            px = p.get("last_value")
            if px is None:
                px = entry
            equity += float(px) * 100 * qty + float(p.get("reserved") or 0)
        elif p.get("is_option"):
            px = p.get("last_bid")
            if px is None:
                px = entry
            equity += float(px) * 100 * qty
        else:
            px = common.last_price(p.get("ticker")) or entry
            equity += px * qty
    peak = float(ledger.get("peak_equity") or equity)
    dd = ((peak - equity) / peak * 100) if peak else 0
    paused = "yes" if ledger.get("paused") else "no"

    positions = ledger.get("positions") or []
    closed = ledger.get("closed_trades") or []

    realized = float(ledger.get("realized_pl") or 0)
    today_str = now.date().isoformat()
    today_pl = sum(
        float(c.get("pl") or 0)
        for c in closed
        if str(c.get("closed", "")).startswith(today_str)
    )
    waiting = [t for t in triggers if t.get("status") == "awaiting_confirm"]
    report_lines = [
        f"# Report {now.strftime('%Y-%m-%d %H:%M')} NY",
        "",
        f"Equity: ${equity:,.0f}  Peak: ${peak:,.0f}  Drawdown: {dd:.1f}%  Paused: {paused}",
        f"Cash: ${cash:,.0f}",
        f"P/L today: ${today_pl:,.0f}",
        f"P/L overall: ${realized:,.0f}",
        _scorecard(ledger, closed),
        "",
        "## Open trades",
    ]
    if not positions:
        report_lines.append("None")
    for p in positions:
        if p.get("action") == "SPREAD":
            entry = float(p.get("entry_price") or 0)
            report_lines.append(
                f"- {p.get('ticker')} {_spread_desc(p.get('legs'), p.get('expiry'))} "
                f"entry {entry:.2f} exits {SPREAD_EXITS}"
            )
            continue
        entry = float(p.get("entry_price") or 0)
        if p.get("is_option"):
            px = p.get("last_bid")
            if px is None:
                px = entry
        else:
            px = common.last_price(p.get("ticker"))
        pl_pct = ((px - entry) / entry * 100) if px and entry else 0
        report_lines.append(
            f"- {p.get('ticker')} entry {entry:.2f} now {px or '?'} P/L {pl_pct:.1f}% "
            f"stop {p.get('stop')} target {p.get('target')}"
        )
    report_lines.append("")
    report_lines.append("## Exits today")
    exits_today = [c for c in closed if str(c.get("closed", "")).startswith(today_str)]
    if not exits_today:
        report_lines.append("None")
    for c in exits_today:
        report_lines.append(f"- {c.get('ticker')} {c.get('exit_reason')} P/L ${c.get('pl')}")
    report_lines.append("")
    report_lines.append("## Waiting for confirmation")
    if not waiting:
        report_lines.append("None")
    for t in waiting:
        report_lines.append(f"- {t.get('id')} {t.get('ticker')} {t.get('action')}")

    with open("REPORT.md", "w") as f:
        f.write("\n".join(report_lines) + "\n")
    print("Pages written")


def send_report(slot):
    now = common.now_ny()
    ledger = common.load_yaml("ledger.yml") or {}
    triggers_data = common.load_yaml("triggers.yml")
    triggers = triggers_data.get("triggers") or []
    cash = float(ledger.get("cash") or 0)
    equity = cash
    for p in ledger.get("positions") or []:
        qty = float(p.get("quantity") or 0)
        entry = float(p.get("entry_price") or 0)
        if p.get("action") == "SPREAD":
            px = p.get("last_value")
            if px is None:
                px = entry
            equity += float(px) * 100 * qty + float(p.get("reserved") or 0)
        elif p.get("is_option"):
            px = p.get("last_bid")
            if px is None:
                px = entry
            equity += float(px) * 100 * qty
        else:
            px = common.last_price(p.get("ticker")) or entry
            equity += px * qty
    realized = float(ledger.get("realized_pl") or 0)
    today_str = now.date().isoformat()
    closed = ledger.get("closed_trades") or []
    today_pl = sum(
        float(c.get("pl") or 0)
        for c in closed
        if str(c.get("closed", "")).startswith(today_str)
    )
    positions = ledger.get("positions") or []
    waiting = [t for t in triggers if t.get("status") == "awaiting_confirm"]

    title_map = {
        "morning": "Trades: morning",
        "midday": "Trades: midday",
        "end of day": "Trades: end of day",
    }
    title = title_map.get(slot, f"Trades: {slot}")
    lines = [
        f"Equity: ${equity:,.0f}",
        f"Cash: ${cash:,.0f}",
        f"Today P/L: ${today_pl:,.0f}",
        f"Overall: ${realized:,.0f}",
        _scorecard(ledger, closed),
        f"Open: {len(positions)}",
        f"Paused: {'yes' if ledger.get('paused') else 'no'}",
    ]
    for p in positions[:5]:
        if p.get("action") == "SPREAD":
            lines.append(f"{p.get('ticker')} {_spread_desc(p.get('legs'), p.get('expiry'))} exits {SPREAD_EXITS}")
        else:
            lines.append(f"{p.get('ticker')} entry {p.get('entry_price')} stop {p.get('stop')}")
    if waiting:
        lines.append(f"Waiting: {', '.join(t.get('id','') for t in waiting)}")
    body = "\n".join(lines[:12])
    common.ntfy(title, body)
    print(f"Report sent: {title}")
