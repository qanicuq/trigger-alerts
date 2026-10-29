import os
import glob
import shutil
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo

import common
import confirm

NY = common.NY
ALLOWED_ACTIONS = {"BUY", "BUY_CALL", "BUY_PUT", "SPREAD"}


def _bucket(tr):
    source = tr.get("source", "forensic")
    if tr.get("action") == "SPREAD":
        return f"{source}-spread"
    is_opt = tr.get("action", "").startswith("BUY_")
    return f"{source}-{'option' if is_opt else 'stock'}"


MACRO_TYPES = {"macro", "manual"}


def _compare(x, op, val):
    if op == ">=":
        return x >= val
    if op == "<=":
        return x <= val
    return False


def _macro_ok(cond):
    """FRED condition. closes: N means the last N readings must all pass.
    transform: yoy turns a monthly index (e.g. CPIAUCNS) into a 12-month % change, rounded like BLS."""
    series = cond.get("series")
    op = cond.get("op")
    val = float(cond.get("value", 0))
    if cond.get("transform") == "yoy":
        # Find the reading from exactly 12 months earlier by date, since FRED skips missing
        # months (e.g. Oct 2025, never published due to the shutdown).
        obs = common.fred_values(series, 13)
        if not obs:
            return False
        prior = str(int(obs[0][0][:4]) - 1) + obs[0][0][4:]
        base = next((v for d, v in obs if d == prior), None)
        if base is None:
            return False
        yoy = round((obs[0][1] / base - 1) * 100, 1)
        return _compare(yoy, op, val)
    n = int(cond.get("closes", 1))
    obs = common.fred_values(series, n)
    return len(obs) == n and all(_compare(v, op, val) for _, v in obs)


def _manual_ok(cond):
    """Hand-entered number from manual.yml, e.g. the 30-year auction tail."""
    try:
        entry = (common.load_yaml("manual.yml") or {}).get(cond.get("key"))
        return entry is not None and _compare(float(entry["value"]), cond.get("op"), float(cond.get("value", 0)))
    except Exception:
        return False


def _cpi_gap_watch(today_d):
    """Nov 2026 only: Oct 2026 CPI has no y/y (Oct 2025 was never published), so give a
    heads-up when the other two TBF path 2 conditions are both close. Alert only, no trade."""
    if not (date(2026, 11, 1) <= today_d <= date(2026, 11, 30)):
        return False
    return (_manual_ok({"key": "auction_tail_30y_bp", "op": ">=", "value": 4})
            and _macro_ok({"series": "BAMLH0A0HYM2", "op": ">=", "value": 4.25}))


def _evaluate_conditions(tr, hist, high, low, refs, rsi, vol_ratio):
    """All conditions must be true. Returns True if all pass."""
    conditions = tr.get("conditions") or []
    if not conditions:
        return False
    for cond in conditions:
        ctype = cond.get("type")
        op = cond.get("op")
        if ctype == "price":
            val = float(cond.get("value", 0))
            if op == "<=":
                if low is None or low > val:
                    return False
            elif op == ">=":
                if high is None or high < val:
                    return False
            else:
                return False
        elif ctype == "price_vs":
            ref_name = cond.get("ref")
            ref_val = refs.get(ref_name)
            if ref_val is None:
                return False
            pct = float(cond.get("pct", 0))
            threshold = ref_val * (1 + pct / 100.0)
            if op == "<=":
                if low is None or low > threshold:
                    return False
            elif op == ">=":
                if high is None or high < threshold:
                    return False
            else:
                return False
        elif ctype == "rsi14":
            if rsi is None:
                return False
            val = float(cond.get("value", 0))
            if op == "<=" and rsi > val:
                return False
            elif op == ">=" and rsi < val:
                return False
            elif op not in ("<=", ">="):
                return False
        elif ctype == "volume_ratio":
            if vol_ratio is None:
                return False
            val = float(cond.get("value", 0))
            if op == ">=" and vol_ratio < val:
                return False
            elif op == "<=" and vol_ratio > val:
                return False
            elif op not in (">=", "<="):
                return False
        elif ctype == "touched":
            # Memory: has price been at/above (or at/below) this level in an earlier check since the trigger was added?
            val = float(cond.get("value", 0))
            if op == ">=":
                seen = tr.get("seen_high")
                if seen is None or seen < val:
                    return False
            elif op == "<=":
                seen = tr.get("seen_low")
                if seen is None or seen > val:
                    return False
            else:
                return False
        elif ctype == "macro":
            if not _macro_ok(cond):
                return False
        elif ctype == "manual":
            if not _manual_ok(cond):
                return False
        else:
            return False
    return True


def _backtest_trigger(tr):
    """Stock-only backtest. Returns {wins, losses, signals}."""
    if tr.get("action") != "BUY" or any(c.get("type") in MACRO_TYPES for c in tr.get("conditions") or []):
        return {"wins": 0, "losses": 0, "signals": 0}
    ticker = tr["ticker"]
    hist = common.get_daily_bars(ticker, period="2y")
    if hist is None or len(hist) < 50:
        return {"wins": 0, "losses": 0, "signals": 0}
    wins = losses = signals = 0
    i = 50  # enough for refs
    while i < len(hist) - 1:
        window = hist.iloc[: i + 1]
        refs = common.compute_refs(window)
        closes = window["Close"].tolist()
        rsi = common.wilder_rsi(closes)
        vol_ratio = common.volume_ratio(window)
        # Simulate day's high/low for price conditions
        day_high = float(hist.iloc[i]["High"])
        day_low = float(hist.iloc[i]["Low"])
        if _evaluate_conditions(tr, window, day_high, day_low, refs, rsi, vol_ratio):
            signals += 1
            entry = float(hist.iloc[i]["Close"])
            ep = tr.get("exit_plan") or {}
            stop = common.resolve_level(ep.get("stop"), refs, entry)
            target = common.resolve_level(ep.get("target"), refs, entry)
            max_hold = int(ep.get("max_hold_days") or 30)
            if stop is None or target is None:
                i += 1
                continue
            # Walk forward
            outcome = None
            for j in range(1, max_hold + 1):
                if i + j >= len(hist):
                    break
                day = hist.iloc[i + j]
                dlow = float(day["Low"])
                dhigh = float(day["High"])
                if dlow <= stop and dhigh >= target:
                    outcome = "loss"  # both same day
                    break
                if dlow <= stop:
                    outcome = "loss"
                    break
                if dhigh >= target:
                    outcome = "win"
                    break
            if outcome is None:
                # time out
                final_close = float(hist.iloc[min(i + max_hold, len(hist) - 1)]["Close"])
                outcome = "win" if final_close > entry else "loss"
            if outcome == "win":
                wins += 1
            else:
                losses += 1
            # skip overlapping
            i += max_hold
        else:
            i += 1
    return {"wins": wins, "losses": losses, "signals": signals}


def _validate_trigger(tr):
    """Return (ok, reason)."""
    if not tr.get("id") or not tr.get("ticker") or not tr.get("action"):
        return False, "missing id/ticker/action"
    if tr["action"] not in ALLOWED_ACTIONS:
        return False, f"action {tr['action']} not allowed"
    conds = tr.get("conditions") or []
    if not conds:
        return False, "no conditions"
    action = tr["action"]
    if action == "BUY":
        ep = tr.get("exit_plan") or {}
        if not ep.get("stop") or not ep.get("target"):
            return False, "stock needs stop and target"
    if action in ("BUY_CALL", "BUY_PUT"):
        c = tr.get("contract") or {}
        if not c.get("right") or c.get("strike") is None or not c.get("expiry"):
            return False, "option needs contract right/strike/expiry"
        try:
            exp_d = date.fromisoformat(str(c.get("expiry")))
            if (exp_d - date.today()).days <= common.OPTION_TIME_EXIT_DAYS:
                return False, "expiry is too soon"
        except Exception:
            return False, "bad contract expiry"
    if action == "SPREAD":
        c = tr.get("contract") or {}
        legs = c.get("legs")
        if not c.get("expiry") or not legs:
            return False, "spread needs contract expiry and legs"
        if len(legs) != 2:
            return False, "spread needs exactly 2 legs"
        sides = [str(leg.get("side") or "").lower() for leg in legs]
        if sides.count("buy") != 1 or sides.count("sell") != 1:
            return False, "spread needs one buy leg and one sell leg"
        rights = [str(leg.get("right") or "").lower() for leg in legs]
        if rights[0] != rights[1] or rights[0] not in ("call", "put"):
            return False, "both legs must be the same right"
        if float(legs[0].get("strike")) == float(legs[1].get("strike")):
            return False, "legs need different strikes"
        try:
            exp_d = date.fromisoformat(str(c.get("expiry")))
            if (exp_d - date.today()).days <= common.OPTION_TIME_EXIT_DAYS:
                return False, "expiry is too soon"
        except Exception:
            return False, "bad contract expiry"
    if tr.get("earnings_date") is not None and common._to_date(tr.get("earnings_date")) is None:
        return False, "bad earnings_date (use YYYY-MM-DD)"
    expires = tr.get("expires")
    if expires:
        try:
            exp_d = date.fromisoformat(str(expires))
            if exp_d < date.today():
                return False, "expires in the past"
        except Exception:
            return False, "bad expires date"
    return True, ""


def _earnings_reminder_body(tr, ed):
    t = tr.get("ticker", "?")
    rd = tr.get("report_date", "an earlier date")
    if tr.get("source") == "option":
        page, where = common.OPTION_CALC_PAGE, f"Open OPTION CALC and run {t} again"
    else:
        page, where = common.FORENSIC_PAGE, f"Open Forensic Analysis and run a Full analysis on {t}"
    body = (f"{t} reported earnings on {ed.isoformat()}. Your {t} trigger is based on the {rd} report, "
            f"which didn't have these results.\n{where}: it refreshes the data and replaces the trigger "
            f"automatically. Don't confirm a {t} trade until then.")
    return body, page


def _earnings_pass(triggers, now):
    """Morning-after-earnings reminders, then refresh each trigger's next earnings date once a day.
    The reminder check runs first so a passed date is never overwritten before its reminder goes out."""
    changed = False
    today = now.date()
    for tr in triggers:
        if tr.get("status") not in ("active", "awaiting_confirm"):
            continue
        ed = common._to_date(tr.get("earnings_date"))
        if ed and today > ed and str(tr.get("earnings_reminded")) != ed.isoformat():
            rd = common._to_date(tr.get("report_date"))
            if rd is None or rd <= ed:
                body, page = _earnings_reminder_body(tr, ed)
                common.ntfy(f"{tr.get('ticker')} earnings are out: update the data", body,
                            priority="high", click_url=page, action_label="Update the data", action_url=page)
                tr["earnings_last"] = ed.isoformat()
            tr["earnings_reminded"] = ed.isoformat()
            changed = True
        if tr.get("earnings_checked") != today.isoformat():
            nd = common.next_earnings(tr["ticker"], today)
            if nd and nd >= today and nd != ed:
                tr["earnings_date"] = nd.isoformat()
            tr["earnings_checked"] = today.isoformat()
            changed = True
    return changed


def _earnings_warning(tr, today):
    """Extra line for a 'confirm trade?' alert when earnings matter, else ''."""
    ed = common._to_date(tr.get("earnings_date"))
    rd = common._to_date(tr.get("report_date"))
    last = common._to_date(tr.get("earnings_last"))
    if last and (rd is None or rd <= last):
        return f"Earnings came out {last.isoformat()}, after this report. Update the data before confirming."
    if ed and ed >= today:
        return f"Earnings on {ed.isoformat()} haven't happened yet; this trade would be held through them."
    return ""


def _thesis_broken(tr, triggers, ledger):
    """Value-based exit: cancel the ticker's open triggers from that source and flag its open
    positions so the next market-hours check sells them."""
    ticker = tr.get("ticker")
    source = tr.get("source", "forensic")
    reason = tr.get("reason") or "thesis broken"
    for t in triggers:
        if t.get("ticker") == ticker and t.get("source") == source and t.get("status") in ("active", "awaiting_confirm"):
            t["status"] = "cancelled"
            t["cancel_reason"] = f"Thesis broken: {reason}"
    held = False
    for pos in (ledger or {}).get("positions") or []:
        if pos.get("ticker") == ticker and pos.get("source") == source:
            pos["thesis_broken"] = reason
            held = True
    body = reason + ("\nThe paper position will be sold at the next check." if held else "\nNo open paper position; buy trigger cancelled.")
    common.ntfy(f"Thesis broken: {ticker}", body, priority="high")


def _ingest_inbox(triggers, ledger=None):
    """Ingest inbox/*.yml. Returns True if any change."""
    os.makedirs("inbox", exist_ok=True)
    os.makedirs("inbox/rejected", exist_ok=True)
    changed = False
    for path in glob.glob("inbox/*.yml"):
        try:
            data = common.load_yaml(path)
            tr = data if isinstance(data, dict) and "ticker" in data else (data.get("triggers") or [None])[0]
            if not tr:
                shutil.move(path, f"inbox/rejected/{os.path.basename(path)}")
                common.ntfy("Trigger rejected: unknown", "empty or bad yaml")
                changed = True
                continue
            if tr.get("action") == "THESIS_BROKEN":
                _thesis_broken(tr, triggers, ledger)
                os.remove(path)
                changed = True
                continue
            ok, reason = _validate_trigger(tr)
            if not ok:
                shutil.move(path, f"inbox/rejected/{os.path.basename(path)}")
                common.ntfy(f"Trigger rejected: {tr.get('ticker', '?')}", reason)
                changed = True
                continue
            # set defaults
            if not tr.get("expires") and tr.get("report_date"):
                try:
                    rd = date.fromisoformat(str(tr["report_date"]))
                    tr["expires"] = (rd + timedelta(days=30)).isoformat()
                except Exception:
                    pass
            tr["status"] = "active"
            # replace same ticker+source
            ticker = tr["ticker"]
            source = tr.get("source", "forensic")
            triggers[:] = [t for t in triggers if not (t.get("ticker") == ticker and t.get("source") == source and t.get("status") in ("active", "awaiting_confirm"))]
            # backtest for stocks
            if tr["action"] == "BUY":
                tr["backtest"] = _backtest_trigger(tr)
            else:
                tr["backtest"] = {"wins": 0, "losses": 0, "signals": 0}
            triggers.append(tr)
            os.remove(path)
            # ntfy conditions in plain words
            lines = []
            for c in tr.get("conditions") or []:
                if c.get("type") == "price":
                    lines.append(f"price {c.get('op')} {c.get('value')}")
                elif c.get("type") == "price_vs":
                    lines.append(f"price {c.get('op')} {c.get('ref')} by {c.get('pct')}%")
                elif c.get("type") == "rsi14":
                    lines.append(f"RSI14 {c.get('op')} {c.get('value')}")
                elif c.get("type") == "volume_ratio":
                    lines.append(f"volume ratio {c.get('op')} {c.get('value')}")
                elif c.get("type") == "touched":
                    lines.append(f"price has touched {c.get('op')} {c.get('value')}")
                elif c.get("type") == "macro":
                    lines.append(f"{c.get('series')} {c.get('op')} {c.get('value')}")
                elif c.get("type") == "manual":
                    lines.append(f"{c.get('key')} {c.get('op')} {c.get('value')}")
            if not tr.get("earnings_date"):
                nd = common.next_earnings(ticker)
                if nd:
                    tr["earnings_date"] = nd.isoformat()
            tr["earnings_checked"] = common.now_ny().date().isoformat()
            ed = common._to_date(tr.get("earnings_date"))
            exp = common._to_date(tr.get("expires"))
            if ed and (exp is None or ed <= exp):
                lines.append(f"Earnings {ed.isoformat()}: you'll get a reminder to update the data the next morning.")
            common.ntfy(f"New trigger: {ticker}", "\n".join(lines) or "conditions loaded")
            changed = True
        except Exception as e:
            print(f"inbox error {path}: {e}")
            try:
                shutil.move(path, f"inbox/rejected/{os.path.basename(path)}")
            except Exception:
                pass
            changed = True
    return changed


def _close_position(ledger, pos, exit_price, reason, now):
    entry = float(pos.get("entry_price") or 0)
    qty = float(pos.get("quantity") or 1)
    is_option = bool(pos.get("is_option"))
    mult = 100 if is_option else 1
    pl = (exit_price - entry) * qty * mult
    ledger["cash"] = float(ledger.get("cash") or 0) + exit_price * qty * mult + float(pos.get("reserved") or 0)
    ledger["realized_pl"] = float(ledger.get("realized_pl") or 0) + pl
    closed = dict(pos)
    closed["exit_price"] = exit_price
    closed["exit_reason"] = reason
    closed["pl"] = round(pl, 2)
    closed["closed"] = now.isoformat()
    ledger.setdefault("closed_trades", []).append(closed)
    ledger["positions"] = [p for p in ledger.get("positions", []) if p.get("id") != pos.get("id")]
    # update stats
    bucket = _bucket(pos)
    stats = ledger.setdefault("stats", {})
    b = stats.setdefault(bucket, {"wins": 0, "losses": 0})
    if pl > 0:
        b["wins"] += 1
    else:
        b["losses"] += 1
    if pos.get("action") == "SPREAD":
        max_loss = float(pos.get("max_loss") or 0)
        pct = (pl / (max_loss * qty * 100) * 100) if max_loss and qty else 0
    else:
        pct = (pl / (entry * qty * mult) * 100) if entry and qty else 0
    body = f"Entry: {entry:.2f}\nExit: {exit_price:.2f}\nP/L: ${pl:.2f} ({pct:.1f}%)"
    common.ntfy(f"{pos.get('ticker')} paper trade closed: {reason}", body)
    return True


def _refresh_triggers_page():
    """Rewrite TRIGGERS.md from the saved files. Never let a page problem stop the bot."""
    try:
        import report
        report.write_triggers_page()
    except Exception as e:
        print(f"write_triggers_page failed: {e}")


def main():
    force = common.is_forced()
    ingest_only = os.environ.get("INGEST_ONLY", "").lower() == "true"
    if not force and not common.market_open() and not ingest_only:
        print("Market closed")
        return

    triggers_data = common.load_yaml("triggers.yml")
    triggers = triggers_data.get("triggers") or []
    ledger = common.load_yaml("ledger.yml")
    if not ledger or "cash" not in ledger:
        ledger = {
            "cash": common.STARTING_CASH,
            "peak_equity": common.STARTING_CASH,
            "day_start_equity": common.STARTING_CASH,
            "positions": [],
            "closed_trades": [],
            "realized_pl": 0.0,
            "last_checked": None,
            "sent_slots": {},
            "usage_checked_date": None,
            "usage_alerted_month": None,
            "paused": False,
            "stats": {},
        }

    now = common.now_ny()
    changed = False

    # Inbox ingest always
    if _ingest_inbox(triggers, ledger):
        changed = True

    if ingest_only:
        common.save_yaml("triggers.yml", {"triggers": triggers})
        common.save_yaml("ledger.yml", ledger)
        _refresh_triggers_page()
        print("Ingest only complete")
        return

    last_checked = ledger.get("last_checked")

    # Daily equity start
    today = now.date().isoformat()
    if ledger.get("day_start_date") != today:
        # compute current equity roughly
        equity = float(ledger.get("cash") or 0)
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
        ledger["day_start_equity"] = equity
        ledger["day_start_date"] = today
        changed = True

    # Peak tracking
    equity = float(ledger.get("cash") or 0)
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
    if equity > peak:
        ledger["peak_equity"] = equity
        changed = True
    dd_now = (peak - equity) / peak * 100 if peak and equity < peak else 0.0
    if dd_now > float(ledger.get("max_drawdown_pct") or 0):
        ledger["max_drawdown_pct"] = round(dd_now, 2)
        changed = True

    # Safety: daily loss / drawdown
    day_start = float(ledger.get("day_start_equity") or equity)
    if equity < day_start * (1 - common.DAILY_LOSS_LIMIT):
        if not ledger.get("daily_loss_alerted"):
            common.ntfy("Daily loss limit hit", "New confirms refused for rest of day", priority="high")
            ledger["daily_loss_alerted"] = today
            changed = True
    if equity < peak * (1 - common.MAX_DRAWDOWN):
        if not ledger.get("paused"):
            ledger["paused"] = True
            common.ntfy(
                f"Bot paused: down {common.MAX_DRAWDOWN:.0%} from peak. Open trades still managed. Set paused: false in ledger.yml to resume.",
                f"Equity {equity:.0f} vs peak {peak:.0f}",
                priority="high",
            )
            changed = True

    # Update resolved levels on triggers
    for tr in triggers:
        if tr.get("status") not in ("active", "awaiting_confirm"):
            continue
        hist = common.get_daily_bars(tr["ticker"], period="1y")
        refs = common.compute_refs(hist)
        rsi_now = common.wilder_rsi(hist["Close"].dropna().tolist()) if hist is not None else None
        if rsi_now is not None:
            refs["rsi14"] = round(rsi_now, 1)
        tr["resolved"] = refs
        # resolve stop/target if ref-based (for display; freeze after entry)
        ep = tr.get("exit_plan") or {}
        if not tr.get("frozen"):
            tr["resolved_stop"] = common.resolve_level(ep.get("stop"), refs)
            tr["resolved_target"] = common.resolve_level(ep.get("target"), refs)
        changed = True

    # Earnings: morning-after reminders, daily date refresh
    if _earnings_pass(triggers, now):
        changed = True

    # Check conditions for active triggers
    filled_now = set()
    for tr in triggers:
        if tr.get("status") != "active":
            continue
        expires = tr.get("expires")
        if expires:
            try:
                if now.date() > date.fromisoformat(str(expires)):
                    tr["status"] = "expired"
                    changed = True
                    continue
            except Exception:
                pass
        ticker = tr["ticker"]
        high, low = common.high_low_since(ticker, last_checked)
        hist = common.get_daily_bars(ticker, period="1y")
        refs = common.compute_refs(hist) or tr.get("resolved") or {}
        closes = hist["Close"].tolist() if hist is not None else []
        rsi = common.wilder_rsi(closes) if closes else None
        vol_ratio = common.volume_ratio(hist) if hist is not None else None
        if _evaluate_conditions(tr, hist, high, low, refs, rsi, vol_ratio):
            tr["status"] = "awaiting_confirm"
            tr["alerted_at"] = now.isoformat()
            price = common.last_price(ticker) or 0
            body = (
                f"Price: {price:.2f}\n"
                f"Rec: {tr.get('recommendation', '')}\n"
                f"Conditions met. Paper-buying automatically now."
            )
            if tr.get("contract"):
                body += f"\nContract: {tr['contract']}"
            warn = _earnings_warning(tr, now.date())
            if warn:
                body += f"\n{warn}"
            body += f"\nTrade ID: {tr['id']}"
            common.ntfy(f"{ticker}: trigger met", body, priority="high")
            # Auto paper-buy (no confirm needed). If refused, confirm.place sends the reason;
            # the trigger stays awaiting_confirm so the Confirm workflow can still retry it today.
            if confirm.place(tr, ledger, force):
                filled_now.add(tr.get("id"))
            changed = True
        # Remember highest/lowest price seen, after evaluating, so "touched" only counts earlier checks
        if high is not None:
            tr["seen_high"] = max(high, tr.get("seen_high", high))
        if low is not None:
            tr["seen_low"] = min(low, tr.get("seen_low", low))

    # One alert per day if macro data could not be read (otherwise macro triggers silently never fire)
    if common.MACRO_ERRORS and ledger.get("macro_error_alerted") != today:
        common.ntfy("Macro data unavailable", "\n".join(sorted(set(common.MACRO_ERRORS))), priority="high")
        ledger["macro_error_alerted"] = today
        changed = True

    # One-time heads-up in Nov 2026 (see _cpi_gap_watch)
    if not ledger.get("cpi_gap_watch_alerted") and _cpi_gap_watch(now.date()):
        common.ntfy("TBF path 2: re-examine",
                    "30-year auction tail >= 4bp and junk-bond spread >= 4.25%.\n"
                    "Oct CPI has no y/y number, so the bot can't confirm the CPI part. Re-examine before trading.",
                    priority="high")
        ledger["cpi_gap_watch_alerted"] = today
        changed = True

    # Expire awaiting at end of day
    for tr in triggers:
        if tr.get("status") != "awaiting_confirm":
            continue
        alerted = tr.get("alerted_at")
        if alerted:
            try:
                alert_dt = datetime.fromisoformat(alerted)
                if alert_dt.tzinfo is None:
                    alert_dt = alert_dt.replace(tzinfo=NY)
                if alert_dt.date() == now.date() and now.time() >= dtime(16, 0):
                    tr["status"] = "expired"
                    changed = True
            except Exception:
                pass

    # Manage open positions (exits)
    positions = list(ledger.get("positions") or [])
    for pos in positions:
        ticker = pos.get("ticker")
        if not ticker or pos.get("id") in filled_now:  # bought this run: price range so far predates the buy
            continue
        is_option = bool(pos.get("is_option"))
        entry = float(pos.get("entry_price") or 0)
        exit_reason = None
        exit_price = None
        if pos.get("action") == "SPREAD":
            value = common.spread_value(ticker, pos.get("expiry"), pos.get("legs"), opening=False)
            if value is None:
                pos["no_quote_count"] = pos.get("no_quote_count", 0) + 1
                if pos["no_quote_count"] >= 2:
                    exit_reason = "no quote"
                    exit_price = pos.get("last_value")
                    if exit_price is None:
                        exit_price = entry
                changed = True
            else:
                pos["no_quote_count"] = 0
                pos["last_value"] = value
                gain = value - entry
                max_profit = float(pos.get("max_profit") or 0)
                max_loss = float(pos.get("max_loss") or 0)
                if gain >= common.SPREAD_TARGET * max_profit:
                    exit_reason = "target"
                    exit_price = value
                elif gain <= -common.SPREAD_STOP * max_loss:
                    exit_reason = "stop"
                    exit_price = value
                try:
                    exp = date.fromisoformat(str(pos.get("expiry")))
                    if (exp - now.date()).days <= common.OPTION_TIME_EXIT_DAYS:
                        exit_reason = "time"
                        exit_price = value
                except Exception:
                    pass
                changed = True
        elif is_option:
            q = common.option_quote(ticker, pos.get("expiry"), pos.get("strike"), pos.get("right"))
            bid = q.get("bid") if q else None
            # no quote 2 runs
            if bid is None:
                pos["no_quote_count"] = pos.get("no_quote_count", 0) + 1
                if pos["no_quote_count"] >= 2:
                    exit_reason = "no quote"
                    exit_price = pos.get("last_bid") or entry * 0.5
                changed = True
            else:
                pos["no_quote_count"] = 0
                pos["last_bid"] = bid
                if bid >= entry * (1 + common.OPTION_TARGET):
                    exit_reason = "target"
                    exit_price = bid
                elif bid <= entry * (1 - common.OPTION_STOP):
                    exit_reason = "stop"
                    exit_price = bid
                # time exit
                try:
                    exp = date.fromisoformat(str(pos.get("expiry")))
                    if (exp - now.date()).days <= common.OPTION_TIME_EXIT_DAYS:
                        exit_reason = "time"
                        exit_price = bid
                except Exception:
                    pass
        else:
            # stock
            high, low = common.high_low_since(ticker, last_checked)
            stop = pos.get("stop")
            target = pos.get("target")
            if stop is not None and low is not None and low <= float(stop):
                exit_reason = "stop"
                cur = common.last_price(ticker)
                exit_price = min(float(stop), cur) if cur is not None else float(stop)
            elif target is not None and high is not None and high >= float(target):
                exit_reason = "target"
                exit_price = float(target)
            if exit_reason is None and pos.get("thesis_broken"):
                exit_reason = "thesis broken"
                exit_price = common.last_price(ticker) or entry
            # max hold
            opened = pos.get("opened")
            max_hold = int(pos.get("max_hold_days") or 30)
            if opened and exit_reason is None:
                try:
                    opend = datetime.fromisoformat(opened).date()
                    if (now.date() - opend).days >= max_hold:
                        exit_reason = "time"
                        exit_price = common.last_price(ticker) or entry
                except Exception:
                    pass
        if exit_reason and exit_price is not None:
            _close_position(ledger, pos, exit_price, exit_reason, now)
            changed = True

    # Reports: first run at or after slot time, before 16:45
    slots = [
        ("morning", dtime(9, 30)),
        ("midday", dtime(12, 30)),
        ("end of day", dtime(15, 55)),
    ]
    sent = ledger.setdefault("sent_slots", {})
    for name, start in slots:
        key = f"{today}:{name}"
        if now.time() >= start and now.time() <= dtime(16, 45) and sent.get(key) is not True:
            try:
                import report
                report.send_report(name)
            except Exception as e:
                print(f"report send failed: {e}")
            sent[key] = True
            changed = True

    if changed:
        try:
            import report
            report.write_pages()
        except Exception as e:
            print(f"write_pages failed: {e}")

    ledger["last_checked"] = now.isoformat()
    common.save_yaml("triggers.yml", {"triggers": triggers})
    common.save_yaml("ledger.yml", ledger)
    _refresh_triggers_page()
    print("Check complete")


if __name__ == "__main__":
    main()
