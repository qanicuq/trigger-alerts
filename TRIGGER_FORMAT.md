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

Options ignore exit_plan. Without option_exits they use fixed +20%/-20% and a 7-day-before-expiry exit. When option_exits is set, those levels replace the +20%/-20% rules. The 7-day exit still applies.

## Option exit levels (option_exits)

Optional on BUY_CALL, BUY_PUT and SPREAD. Each key is optional. Allowed keys only: target_premium, stop_premium, target_price (stock price), stop_price (stock price), exit_by (YYYY-MM-DD, not after the contract expiry). Any other key rejects the trigger.

For a single option, target_premium / stop_premium compare to the bid. For a call, target_price is the stock at or above that price and stop_price is the stock at or below it. A put is the reverse. For a spread, the premium keys compare to the spread's net value, and the price keys follow the spread: bullish spreads target a rising stock, bearish spreads target a falling stock. These replace the +20%/-20% rules (single options) or the +50%/-50% rules (spreads). Thesis-broken, no-quote, and the 7-day-before-expiry exit still apply.

LEAPS example. A call expiring in 2028 should not be sold on a normal 20% premium swing. target_price is the stock price where the call's intrinsic value is the goal. stop_premium is 40% of a $10 entry premium. exit_by is about 6 months before expiry:

```yaml
id: ABCD-leaps-2026-10-31
source: option
ticker: ABCD
action: BUY_CALL
conditions:
  - {type: price, op: ">=", value: 50.00}
contract:
  right: call
  strike: 40
  expiry: 2028-01-21
option_exits:
  target_price: 70.00
  stop_premium: 6.00
  stop_price: 35.00
  exit_by: 2027-07-21
recommendation: "LEAPS call: hold for the stock move, not a 20% premium swing"
report_date: 2026-10-31
expires: 2026-11-30
```

## Earnings reminders

The bot looks up each trigger's next earnings date once a day (Yahoo Finance) and saves it as `earnings_date`. The first check after that date, it sends a high-priority phone alert to update the data: run a fresh Full analysis on Forensic Analysis (or OPTION CALC for option triggers), which replaces the trigger automatically. If the "confirm trade?" alert fires before earnings, or after earnings but before the data was updated, it says so.

Until a fresh report replaces the trigger, the bot will not buy on the old numbers: if the conditions are met it sends one "buy on hold" alert and keeps the trigger active. Open stock positions get the same morning-after alert; a fresh Full analysis that says AVOID sells the position, otherwise it keeps its stop and target. (Option and spread positions use their own short exits and get no earnings alert.)

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

Spreads exit at +50% of max profit, at -50% of max loss, or 7 days before expiry, unless option_exits is set (those levels replace the +50%/-50% rules; the 7-day exit still applies). Only two-leg spreads with the same expiry are allowed.

## Other option positions (POSITION)

action: POSITION holds the Option Calc trades that are not a straight buy or a two-leg spread: short put, short call, covered call, buy-write, protective put, and collar. strategy is one of cash_secured_put, covered_call, buy_write, protective_put, collar, short_call. bullish is true or false. contract.expiry must be more than 7 days away. Legs are 1 to 3, and they must match the strategy:

- cash_secured_put: one leg, sell put
- short_call: one leg, sell call
- covered_call and buy_write: buy stock + sell call
- protective_put: buy stock + buy put
- collar: buy stock + buy put + sell call, put strike lower than call strike

Each leg has side (buy or sell) and right (call, put, or stock). Option legs need a number strike. A stock leg has no strike.

option_exits on a POSITION uses dollars of profit or loss for ONE unit (100 shares, or 1 contract): target_pl, stop_pl (negative), plus target_price, stop_price, and exit_by. Every POSITION must have a stop: stop_pl or stop_price (or both), or the trigger is rejected. Size: as many units as fit in 25% of the account; if one unit is bigger than that, the trade is skipped. A bullish position stops when the stock is at or below stop_price and targets a stock at or above target_price. A bearish position is the reverse. The 7-day-before-expiry exit still applies.

Short put:

```yaml
id: ABCD-option-2026-10-31
source: option
ticker: ABCD
action: POSITION
strategy: cash_secured_put
bullish: true
conditions:
  - {type: price, op: "<=", value: 42.00}
contract:
  expiry: 2026-12-18
  legs:
    - {side: sell, right: put, strike: 40}
option_exits:
  target_pl: 60
  stop_pl: -240
  stop_price: 30
  exit_by: 2026-12-11
recommendation: "Sell the Dec 18 $40 put if price is at or below 42"
report_date: 2026-10-31
expires: 2026-11-30
```

Short call:

```yaml
id: ABCD-shortcall-2026-10-31
source: option
ticker: ABCD
action: POSITION
strategy: short_call
bullish: false
conditions:
  - {type: price, op: ">=", value: 55.00}
contract:
  expiry: 2026-12-18
  legs:
    - {side: sell, right: call, strike: 60}
option_exits:
  target_pl: 80
  stop_pl: -200
  stop_price: 62
  exit_by: 2026-12-11
recommendation: "Sell the Dec 18 $60 call if price is at or above 55"
report_date: 2026-10-31
expires: 2026-11-30
```

Covered call:

```yaml
id: ABCD-cc-2026-10-31
source: option
ticker: ABCD
action: POSITION
strategy: covered_call
bullish: true
conditions:
  - {type: price, op: ">=", value: 48.00}
contract:
  expiry: 2026-12-18
  legs:
    - {side: buy, right: stock}
    - {side: sell, right: call, strike: 50}
option_exits:
  target_pl: 300
  stop_pl: -500
  target_price: 50
  stop_price: 42
  exit_by: 2026-12-11
recommendation: "Buy 100 shares and sell the Dec 18 $50 call"
report_date: 2026-10-31
expires: 2026-11-30
```

Buy-write (same legs as a covered call; the stock is bought for this trade):

```yaml
id: ABCD-bw-2026-10-31
source: option
ticker: ABCD
action: POSITION
strategy: buy_write
bullish: true
conditions:
  - {type: price, op: "<=", value: 49.00}
contract:
  expiry: 2026-12-18
  legs:
    - {side: buy, right: stock}
    - {side: sell, right: call, strike: 50}
option_exits:
  target_pl: 250
  stop_pl: -400
  exit_by: 2026-12-11
recommendation: "Buy-write: buy shares and sell the Dec 18 $50 call"
report_date: 2026-10-31
expires: 2026-11-30
```

Protective put:

```yaml
id: ABCD-pp-2026-10-31
source: option
ticker: ABCD
action: POSITION
strategy: protective_put
bullish: true
conditions:
  - {type: price, op: ">=", value: 50.00}
contract:
  expiry: 2026-12-18
  legs:
    - {side: buy, right: stock}
    - {side: buy, right: put, strike: 45}
option_exits:
  target_pl: 800
  stop_pl: -600
  target_price: 60
  stop_price: 44
  exit_by: 2026-12-11
recommendation: "Buy 100 shares and buy the Dec 18 $45 put"
report_date: 2026-10-31
expires: 2026-11-30
```

Collar:

```yaml
id: ABCD-collar-2026-10-31
source: option
ticker: ABCD
action: POSITION
strategy: collar
bullish: true
conditions:
  - {type: price, op: ">=", value: 50.00}
contract:
  expiry: 2026-12-18
  legs:
    - {side: buy, right: stock}
    - {side: buy, right: put, strike: 45}
    - {side: sell, right: call, strike: 55}
option_exits:
  target_pl: 400
  stop_pl: -500
  target_price: 55
  stop_price: 45
  exit_by: 2026-12-11
recommendation: "Collar: long shares, long the $45 put, short the $55 call"
report_date: 2026-10-31
expires: 2026-11-30
```

