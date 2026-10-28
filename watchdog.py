"""Lateness watchdog for the trigger check.

cron-job.org starts check.yml at :00 and :30. The first real check is 09:30 New York and the
last is 16:00. Every real check saves ledger.yml `last_checked`, so that timestamp is the heartbeat.
This script runs from its own GitHub schedule (a different path than cron-job.org) and sends a
phone alert when a check is missing. Weekdays that are not NYSE holidays only.

Modes, by New York time:
  08:30-09:37  before the open:  did yesterday's last check run?        (alert only on a problem)
  09:38-16:08  market hours:     did every check due 8+ minutes ago run? (alert only on a problem)
  16:09-17:59  after the close:  did today's last check run?             (quiet "all good" if yes)
Anything else exits silently.
"""
from datetime import datetime, time as dtime, timedelta

import common

NY = common.NY
SLOT_FIRST = dtime(9, 30)
SLOT_LAST = dtime(16, 0)
GRACE = timedelta(minutes=8)       # a check normally starts within a minute of its slot
TOLERANCE = timedelta(minutes=1)   # in case a check fires a few seconds early
RUNS_PAGE = "https://github.com/qanicuq/trigger-alerts/actions/workflows/check.yml"


def is_trading_day(d):
    return d.weekday() < 5 and d not in common.HOLIDAYS


def previous_trading_day(d):
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def slots_on(d):
    """Scheduled check times on day d: every :00 and :30 from 09:30 to 15:30."""
    t = datetime.combine(d, SLOT_FIRST, tzinfo=NY)
    end = datetime.combine(d, SLOT_LAST, tzinfo=NY)
    out = []
    while t <= end:
        out.append(t)
        t += timedelta(minutes=30)
    return out


def read_last_checked():
    raw = (common.load_yaml("ledger.yml") or {}).get("last_checked")
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=NY)


def _when(dt, now):
    if dt is None:
        return "never"
    dt = dt.astimezone(NY)
    return dt.strftime("%H:%M") if dt.date() == now.date() else dt.strftime("%a %H:%M")


def evaluate(now, last):
    """Return (title, body, priority) to send, or None when there is nothing to say."""
    today = now.date()
    if not is_trading_day(today):
        return None
    t = now.time()

    if dtime(8, 30) <= t < dtime(9, 38):
        final = slots_on(previous_trading_day(today))[-1]
        if last is None or last < final - TOLERANCE:
            return ("Trigger check problem before the open",
                    f"The last check of the previous trading day ({final:%a %H:%M}) never ran. "
                    f"Last check: {_when(last, now)}. Look at cron-job.org and the Check triggers runs.",
                    "high")
        return None

    if dtime(9, 38) <= t < dtime(16, 9):
        due = [s for s in slots_on(today) if s + GRACE <= now]
        missed = [s for s in due if last is None or last < s - TOLERANCE]
        if not missed:
            return None
        mins = f"{int((now - last).total_seconds() // 60)} min ago" if last else "no record"
        return ("Trigger check is late",
                f"Last check ran {_when(last, now)} ({mins}). Missed: "
                f"{', '.join(s.strftime('%H:%M') for s in missed)}. "
                f"Look at cron-job.org and the Check triggers runs.",
                "high")

    if dtime(16, 9) <= t < dtime(18, 0):
        final = slots_on(today)[-1]
        if last is None or last < final - TOLERANCE:
            return ("Trigger check missed today",
                    f"The last check of the day ({final:%H:%M}) never ran. Last check: {_when(last, now)}.",
                    "high")
        return ("Watchdog: all good", f"The last check of the day ran at {_when(last, now)}.", "min")

    return None


def main():
    now = common.now_ny()
    result = evaluate(now, read_last_checked())
    if result is None:
        print(f"{now:%Y-%m-%d %H:%M} New York: nothing to report")
        return
    title, body, priority = result
    print(f"{title}: {body}")
    common.ntfy(title, body, priority=priority, click_url=RUNS_PAGE)


if __name__ == "__main__":
    main()
