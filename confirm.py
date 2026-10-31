import os
import sys
import math
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

import common

NY = common.NY


def _bucket(tr):
    source = tr.get("source", "forensic")
    action = tr.get("action")
    if action == "POSITION":
        return f"{source}-position"
    if action == "SPREAD":
        return f"{source}-spread"
    is_opt = str(action or "").startswith("BUY_")
    return f"{source}-{'option' if is_opt else 'stock'}"


def _compute_equity(ledger):
    equity = float(ledger.get("cash") or 0)
    for p in ledger.get("positions") or []:
        qty = float(p.get("quantity") or 0)
        entry = float(p.get("entry_price") or 0)
        if p.get("action") == "POSITION":
            px = p.get("last_value")
            if px is None:
                px = p.get("entry_value")
                if px is None:
                    px = entry
            equity += (float(px) + float(p.get("reserved") or 0)) * qty
        elif p.get("action") == "SPREAD":
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
    return equity


def _kelly_size(tr, ledger, entry, stop, target, max_profit=None, max_loss=None):
    """Return (shares_or_contracts, risk_fraction, p, b, refused_reason) or None if refuse."""
    bucket = _bucket(tr)
    stats = ledger.get("stats") or {}
    bkt = stats.get(bucket) or {"wins": 0, "losses": 0}
    live_trades = bkt["wins"] + bkt["losses"]
    bt = tr.get("backtest") or {"wins": 0, "losses": 0}
    alpha = common.PRIOR_ALPHA + bkt["wins"] + common.BACKTEST_WEIGHT * bt["wins"]
    beta = common.PRIOR_BETA + bkt["losses"] + common.BACKTEST_WEIGHT * bt["losses"]
    p = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5
    action = tr.get("action")
    is_spread = action == "SPREAD"
    is_option = action != "BUY"
    if is_spread:
        b = (common.SPREAD_TARGET * max_profit) / (common.SPREAD_STOP * max_loss)
    elif is_option:
        b = 1.0  # 0.20 / 0.20
    else:
        if stop is None or target is None or entry <= stop:
            return None, 0, p, 0, "bad stop/target"
        b = (target - entry) / (entry - stop)
    k = p - (1 - p) / b if b > 0 else -1
    risk_fraction = common.KELLY_FRACTION * k
    risk_fraction = min(risk_fraction, common.MAX_RISK_PER_TRADE)
    if live_trades < common.WARMUP_TRADES:
        risk_fraction = max(risk_fraction, common.WARMUP_RISK)
    elif k <= 0:
        return None, risk_fraction, p, b, f"odds not good enough (p={p:.2f}, b={b:.2f})"
    equity = _compute_equity(ledger)
    dollars_at_risk = risk_fraction * equity
    cash = float(ledger.get("cash") or 0)
    if is_spread:
        contracts = math.floor(dollars_at_risk / (common.SPREAD_STOP * max_loss * 100))
        max_by_pos = math.floor(common.MAX_POSITION_OPTION * equity / (max_loss * 100))
        max_by_cash = math.floor(cash / (max_loss * 100))
        contracts = min(contracts, max_by_pos, max_by_cash)
        if contracts < 1:
            return None, risk_fraction, p, b, f"one spread risks ${max_loss*100:.0f}, too big for the account"
        return contracts, risk_fraction, p, b, None
    if is_option:
        # need premium
        c = tr.get("contract") or {}
        q = common.option_quote(tr["ticker"], c.get("expiry"), c.get("strike"), c.get("right"))
        if not q:
            return None, risk_fraction, p, b, "no option quote"
        premium = q.get("ask") or q.get("last") or 0
        if premium <= 0:
            return None, risk_fraction, p, b, "bad premium"
        contracts = math.floor(dollars_at_risk / (premium * 100 * 0.20))
        max_by_pos = math.floor((common.MAX_POSITION_OPTION * equity) / (premium * 100))
        max_by_cash = math.floor(cash / (premium * 100))
        contracts = min(contracts, max_by_pos, max_by_cash)
        if contracts < 1:
            return None, risk_fraction, p, b, f"one contract costs ${premium*100:.0f}, too big for the account"
        return contracts, risk_fraction, p, b, None
    else:
        if entry <= stop:
            return None, risk_fraction, p, b, "stop not below entry"
        shares = dollars_at_risk / (entry - stop)
        max_by_pos = (common.MAX_POSITION_STOCK * equity) / entry
        max_by_cash = cash / entry
        shares = min(shares, max_by_pos, max_by_cash)
        shares = math.floor(shares * 10000) / 10000  # round down to 4 decimals
        if shares <= 0:
            return None, risk_fraction, p, b, "zero shares"
        return shares, risk_fraction, p, b, None


def main():
    trade_id = sys.argv[1] if len(sys.argv) > 1 else "latest"
    triggers_data = common.load_yaml("triggers.yml")
    triggers = triggers_data.get("triggers") or []
    ledger = common.load_yaml("ledger.yml")
    if not ledger:
        ledger = {"cash": common.STARTING_CASH, "positions": [], "closed_trades": [], "realized_pl": 0.0, "stats": {}, "paused": False}

    pending = [t for t in triggers if t.get("status") == "awaiting_confirm"]
    if not pending:
        common.ntfy("Confirm refused", "None pending")
        print("None pending")
        return

    if trade_id == "latest":
        tr = max(pending, key=lambda t: t.get("alerted_at") or "")
    else:
        tr = next((t for t in pending if t.get("id") == trade_id), None)
    if not tr:
        common.ntfy("Confirm refused", f"ID {trade_id} not found")
        print("ID not found")
        return

    if place(tr, ledger, common.is_forced()):
        common.save_yaml("triggers.yml", {"triggers": triggers})
        common.save_yaml("ledger.yml", ledger)
        print("Filled")


def place(tr, ledger, force=False):
    """Paper-buy one triggered trade in memory (caller saves files). Returns True if filled.
    Used by the manual Confirm workflow and automatically by check.py when a trigger is met."""
    now = common.now_ny()
    if not force and (not common.market_open() or now.time() > dtime(15, 55)):
        common.ntfy("Confirm refused", "Market closed or after 15:55")
        print("Market closed or after cutoff")
        return False

    if ledger.get("paused"):
        common.ntfy("Confirm refused", "Bot is paused")
        return False

    # daily loss
    equity = _compute_equity(ledger)
    day_start = float(ledger.get("day_start_equity") or equity)
    if equity < day_start * (1 - common.DAILY_LOSS_LIMIT):
        common.ntfy("Confirm refused", "Daily loss limit reached")
        return False

    if common.MAX_OPEN_POSITIONS is not None and len(ledger.get("positions") or []) >= common.MAX_OPEN_POSITIONS:
        common.ntfy("Confirm refused", f"Max open positions {common.MAX_OPEN_POSITIONS}")
        return False

    ticker = tr["ticker"]
    # Playbook: correlated bets are one bet (e.g. TBF path 1 and path 2), so one position per ticker
    if any(p.get("ticker") == ticker for p in ledger.get("positions") or []):
        common.ntfy("Confirm refused", f"Already holding {ticker}")
        return False
    price = common.last_price(ticker)
    if price is None:
        common.ntfy("Confirm refused", f"No price for {ticker}")
        return False

    action = tr.get("action", "BUY")
    is_position = action == "POSITION"
    is_spread = action == "SPREAD"
    is_single = action in ("BUY_CALL", "BUY_PUT")
    # Spreads stay "is_option" so close/equity math is unchanged. POSITION is not.
    is_option = is_single or is_spread
    contract = tr.get("contract")

    # Fill price
    max_profit = None
    max_loss = None
    reserved_per = 0.0
    if is_position:
        c = contract or {}
        legs = c.get("legs") or []
        fill = common.position_value(ticker, c.get("expiry"), legs, opening=True)
        if fill is None:
            common.ntfy("Confirm refused", "No position quote")
            return False
        strategy = tr.get("strategy")
        if strategy in ("cash_secured_put", "short_call"):
            opt = next(leg for leg in legs if str(leg.get("right") or "").lower() != "stock")
            q = common.option_quote(ticker, c.get("expiry"), opt.get("strike"), opt.get("right"))
            if not q:
                common.ntfy("Confirm refused", "No position quote")
                return False
            side = str(opt.get("side") or "").lower()
            premium = q["ask"] if side == "buy" else q["bid"]
            reserved_per = common.naked_margin(opt.get("right"), price, opt.get("strike"), premium) * 100
        else:
            reserved_per = 0.0
    elif is_spread:
        c = contract or {}
        fill = common.spread_value(ticker, c.get("expiry"), c.get("legs"), opening=True)
        if fill is None:
            common.ntfy("Confirm refused", "No spread quote")
            return False
        _width, max_profit, max_loss = common.spread_numbers(c.get("legs"), fill)
        if max_profit <= 0 or max_loss <= 0:
            common.ntfy("Confirm refused", "Spread quotes make no sense")
            return False
    elif is_single:
        q = common.option_quote(ticker, contract.get("expiry"), contract.get("strike"), contract.get("right"))
        if not q:
            common.ntfy("Confirm refused", "No option quote")
            return False
        fill = q.get("ask") or q.get("last") or 0
    else:
        fill = price * 1.0005  # slight slippage

    # Resolve stop/target for stocks (freeze them)
    hist = common.get_daily_bars(ticker, period="1y")
    refs = common.compute_refs(hist) or {}
    ep = tr.get("exit_plan") or {}
    if is_option or is_position:
        stop = None
        target = None
        if contract:
            try:
                exp_d = date.fromisoformat(str(contract.get("expiry")))
                if (exp_d - now.date()).days <= common.OPTION_TIME_EXIT_DAYS:
                    common.ntfy("Confirm refused", "expiry is too soon")
                    return False
            except Exception:
                common.ntfy("Confirm refused", "bad contract expiry")
                return False
    else:
        stop = common.resolve_level(ep.get("stop"), refs, fill)
        target = common.resolve_level(ep.get("target"), refs, fill)
        if stop is None or target is None:
            common.ntfy("Confirm refused", "Could not resolve stop/target")
            return False

    risk_frac = 0.0
    p = 0.0
    if is_position:
        denom = abs(fill) + reserved_per
        if denom <= 0:
            common.ntfy(f"Skipped {ticker}: one position costs $0, too big for the account", "position cap")
            return False
        qty = math.floor(common.MAX_POSITION_OPTION * equity / denom)
        if qty < 1:
            common.ntfy(
                f"Skipped {ticker}: one position costs ${denom:.0f}, too big for the account",
                "position cap",
            )
            return False
    else:
        qty, risk_frac, p, b, refuse = _kelly_size(tr, ledger, fill, stop, target, max_profit, max_loss)
        if refuse:
            common.ntfy(f"Skipped {ticker}: {refuse}", f"p={p:.2f} b={b:.2f}")
            return False

    # Cash check already in sizing
    if is_spread:
        cost = max_loss * 100 * qty
        reserved = (max_loss - fill) * 100 * qty
    elif is_position:
        cost = (fill + reserved_per) * qty
        reserved = reserved_per  # per unit; equity and close multiply by quantity
    else:
        cost = fill * qty * (100 if is_single else 1)
        reserved = 0
    if float(ledger.get("cash") or 0) < cost:
        common.ntfy("Confirm refused", f"Not enough cash (need ${cost:.0f})")
        return False

    ledger["cash"] = float(ledger.get("cash") or 0) - cost

    pos = {
        "id": tr.get("id"),
        "ticker": ticker,
        "action": action,
        "entry_price": round(fill, 4),
        "quantity": qty,
        "stop": stop,
        "target": target,
        "max_hold_days": int(ep.get("max_hold_days") or 30),
        "is_option": is_option,
        "reserved": reserved,
        "opened": common.now_ny().isoformat(),
        "source": tr.get("source"),
    }
    if is_position:
        pos["entry_value"] = fill
        pos["strategy"] = tr.get("strategy")
        pos["bullish"] = bool(tr.get("bullish"))
        pos["legs"] = (contract or {}).get("legs")
        pos["expiry"] = (contract or {}).get("expiry")
        pos["is_option"] = False
        pos["stop"] = None
        pos["target"] = None
    elif is_spread:
        pos["expiry"] = contract.get("expiry")
        pos["legs"] = contract.get("legs")
        pos["max_profit"] = max_profit
        pos["max_loss"] = max_loss
        pos["stop"] = None
        pos["target"] = None
    elif contract:
        pos["strike"] = contract.get("strike")
        pos["right"] = contract.get("right")
        pos["expiry"] = contract.get("expiry")
    if tr.get("option_exits") and (is_single or is_spread or is_position):
        pos["option_exits"] = dict(tr["option_exits"])
    # freeze
    tr["frozen"] = True
    tr["status"] = "filled"

    if is_spread:
        kind = "Credit" if fill < 0 else "Debit"
        common.ntfy(
            f"{ticker} paper trade placed",
            f"{kind}\nNet: {fill:.2f} per share\nQty: {qty}\n"
            f"Max loss: ${max_loss * 100 * qty:.2f}\nMax profit: ${max_profit * 100 * qty:.2f}",
        )
    elif is_position:
        common.ntfy(
            f"{ticker} paper trade placed",
            f"{tr.get('strategy')}\nValue: {fill:.2f}\nQty: {qty}\nReserved per unit: {reserved_per:.2f}",
        )
    else:
        common.ntfy(
            f"{ticker} paper trade placed",
            f"Fill: {fill:.2f}\nQty: {qty}\nStop: {stop}\nTarget: {target}\nRisk: {risk_frac*100:.1f}% (win chance {p*100:.0f}%)",
        )

    ledger.setdefault("positions", []).append(pos)
    return True


if __name__ == "__main__":
    main()
