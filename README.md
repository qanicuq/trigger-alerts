# trigger-alerts

Paper trading bot that watches conditions and alerts for manual confirmation.

## How it works

You drop a trigger file into the inbox folder. The bot checks every half hour during market hours. When all conditions are true it paper-buys automatically (no confirm needed), sizing the trade with a safety formula, then tracks the paper position and sends a phone alert. If a safety rule blocks the buy, the alert says why, and you can still retry it that day with the Confirm trade workflow. If a Forensic Analysis stock buy is blocked because there is no cash and its upside (target ÷ price − 1) is lower than every stock already held, the buy is held and a fresh Forensic Analysis runs first (the daily re-run task). If the fresh report still comes out weaker when its conditions are met, the trigger is shelved for two weeks and then tried again; an AVOID report cancels it. Stops and time exits close automatically. A Forensic Analysis stock that reaches its target is held and you get an alert to run a fresh analysis; the fresh analysis then keeps it, trims 10% of the profit (franchise), or sells it. It never risks more than a small fraction of the $10,000 account and pauses new buys if down 10% from its high. Reports arrive three times a day. Everything stays paper — no real money moves.

## Watchdog

A separate GitHub schedule (not cron-job.org) checks that the half-hour checks really ran, on weekdays that are not NYSE holidays. It sends a phone alert if a check is late, once before the open (did yesterday's last check run?) and once after the close (a quiet "all good" if the day's last check ran). See `watchdog.py` and `.github/workflows/watchdog.yml`.
