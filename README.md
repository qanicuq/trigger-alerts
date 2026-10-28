# trigger-alerts

Paper trading bot that watches conditions and alerts for manual confirmation.

## How it works

You drop a trigger file into the inbox folder. The bot checks every half hour during market hours. When all conditions are true it paper-buys automatically (no confirm needed), sizing the trade with a safety formula, then tracks the paper position and sends a phone alert. If a safety rule blocks the buy, the alert says why, and you can still retry it that day with the Confirm trade workflow. Stops, targets and time exits close automatically. It never risks more than a small fraction of the $1,000 account and pauses new buys if down 10% from its high. Reports arrive three times a day. Everything stays paper — no real money moves.

## Watchdog

A separate GitHub schedule (not cron-job.org) checks that the half-hour checks really ran, on weekdays that are not NYSE holidays. It sends a phone alert if a check is late, once before the open (did yesterday's last check run?) and once after the close (a quiet "all good" if the day's last check ran). See `watchdog.py` and `.github/workflows/watchdog.yml`.
