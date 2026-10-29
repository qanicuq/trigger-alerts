# Trigger Format

One trigger per inbox/*.yml file.

## Stock example

```yaml
id: ABCD-forensic-2026-10-12
source: forensic
ticker: ABCD
action: BUY
conditions:
  - {type: price, op: "<=", value: 42.50}
  - {type: price_vs, ref: sma50, op: "<=", pct: -2}
  - {type: rsi14, op: "<=", value: 35}
  - {type: volume_ratio, op: ">=", value: 1.5}
exit_plan:
  stop: {pct: -8}
  target: {pct: 15}
  max_hold_days: 30
recommendation: "HOLD — buy only if all conditions met"
report_date: 2026-10-12
expires: 2026-11-12
```

## Option example

```yaml
id: ABCD-option-2026-10-12
source: option
ticker: ABCD
action: BUY_CALL
conditions:
  - {type: price, op: ">=", value: 50.00}
contract:
  right: call
  strike: 45
  expiry: 2026-12-18
recommendation: "Buy call if price breaks 50"
report_date: 2026-10-12
expires: 2026-11-12
```

Supported refs: sma20, sma50, sma200, ema20, 52w_high, 52w_low, support20, resistance20.

## Memory condition: touched

`{type: touched, op: ">=", value: 90}` is true once price was at or above 90 in an earlier check since the trigger was added. `op: "<="` works the same for a low. Use it with a normal price condition for "bounced to 90, then fell back":

```yaml
conditions:
  - {type: touched, op: ">=", value: 90}
  - {type: price, op: "<=", value: 88.5}
```

The bot saves the highest and lowest price it has seen in `seen_high` / `seen_low`. It starts remembering when the trigger is added, and only counts earlier checks, not the current one. Not used in stock backtests.

Stop/target forms: {price: 39.00} or {pct: -8} or {ref: support20, pct: -1}.

Options ignore exit_plan; use fixed +20%/-20%/7-day rules.

## Earnings reminders

The bot looks up each trigger's next earnings date once a day (Yahoo Finance) and saves it as `earnings_date`. The first check after that date, it sends a high-priority phone alert to update the data: run a fresh Full analysis on Forensic Analysis (or OPTION CALC for option triggers), which replaces the trigger automatically. If the "confirm trade?" alert fires before earnings, or after earnings but before the data was updated, it says so.

You can also set the date yourself: `earnings_date: 2026-11-02`. ETFs have no earnings, so they get no reminder.

## Thesis broken (value exits)

Forensic Analysis sends this when a re-check finds a walk-away signal (auditor change, restatement, late filing) or the verdict turns to AVOID:

```yaml
id: ABCD-thesis-2026-10-15
source: forensic
ticker: ABCD
action: THESIS_BROKEN
reason: "Auditor resigned (8-K item 4.01)"
```

The bot cancels that ticker's active triggers from the same source, flags any open paper position from that source to be sold at the next market-hours check, and sends a high-priority alert. Value-based buy triggers use `target: {price: <intrinsic value>}`, a wide `stop`, and `max_hold_days: 540` (an 18-month time stop).

## Spread example

```yaml
id: ABCD-spread-2026-10-29
source: option
ticker: ABCD
action: SPREAD
conditions:
  - {type: price, op: ">=", value: 50.00}
contract:
  expiry: 2026-12-18
  legs:
    - {side: buy, right: put, strike: 45}
    - {side: sell, right: put, strike: 40}
recommendation: "Bear put spread if price is at or above 50"
report_date: 2026-10-29
expires: 2026-11-29
```

Spreads exit at +50% of max profit, at -50% of max loss, or 7 days before expiry. Only two-leg spreads with the same expiry are allowed.
