# Money

**First-hour momentum for NSE intraday trading, on your laptop.**
A personal trading assistant: it reads the market's first hour, picks one
stock, and from your desired profit and risk:reward fetches the quantity,
stop-loss and target (also for any stock you type, in the Trade calculator), and shows how the same rules
did over the past year. Paper trading: Money never places orders.

## Start it

You need Python 3.8 or newer and an internet connection. Nothing to install.

- **Windows:** double-click `start-money.bat`
- **Mac / Linux:** run `./start-money.sh` (or `python3 money.py`)

Your browser opens at <http://127.0.0.1:8050>. Stop it with Ctrl+C in the
terminal window.

From the terminal you can also run:

```
python money.py pick                       # today's pick (after 10:15 AM IST)
python money.py pick --date 2026-11-11     # replay a past day (last 60 days)
python money.py pick --mode high           # high-accuracy mode
python money.py backtest                   # 12 months, NIFTY 100
python money.py backtest --mode high --list nifty50
python money.py pick --profit 1500 --rr 2.5        # desired profit, risk:reward 1:2.5
python money.py calc RELIANCE --profit 1000         # quantity, stop-loss, target for any stock
```

## The method

1. **9:15 to 10:15 IST: read the market.** NIFTY 50 against yesterday's close
   and its own open, breadth (stocks up vs down) and India VIX.
   NIFTY up 0.25% or more: **bullish, buys only**. Down 0.25% or more:
   **bearish, short sells only**. In between: **neutral, no trade**.
2. **Shortlist** stocks up (buy) or down (short) **1% to 2%** from yesterday's
   close at 10:15 (the setting "Move measured" can switch to the move within the
   first hour, 9:15 to 10:15, 1.5% to 2%; see the table below).
3. **Score each chart out of 100**: VWAP side 25, close near the first-hour
   high/low 20, EMA 9 vs EMA 20 15, volume vs its usual first hour 20,
   stronger/weaker than NIFTY 10, 20-day trend 10. Standard mode needs 70+;
   high-accuracy mode 80+.
4. **Entry**: the first 5-minute candle that closes beyond the first-hour high
   (buy) or low (short), by 2:30 PM.
5. **Stop-loss**: the first-hour midpoint (on the NSE tick). **Target**: set by
   your risk:reward (1:2 by default = 2x the risk). At +1R the stop moves to the entry.
6. **Exit**: target, stop or 3:10 PM, whichever comes first. Intraday only (MIS).
7. **You enter the desired profit per trade** (default Rs 1,000) and the
   **risk:reward** (default 1:2). Money fetches the **quantity, stop-loss and
   target** automatically: the quantity makes the desired profit at the target,
   and the most you lose at the stop is the desired profit / 2 (Rs 500 at 1:2). Shares are also capped at 75% of
   the budget and the budget less a 10% cash buffer. **Charges**: Zerodha intraday.

## Alerts

Click **Alerts** (top right) and allow notifications. While the Money page
stays open on a trading day it watches by itself and alerts you once each,
with a sound, a banner and a Mac/Windows notification: market opening (9:10),
the pick or "no trade" (after 10:15), ENTRY with the quantity, stop-loss and
target to place, +1R (move the stop to the entry), TARGET HIT / STOP HIT,
"no entry" at 2:30 PM and CLOSE NOW at 3:05 PM. Prices are free Yahoo data a
few minutes delayed: your broker's stop-loss and target orders are what
actually protect the trade.

## Accuracy: 12 months of testing

NIFTY 100, 12 Nov 2025 to 19 Nov 2026 (250 sessions), Rs 50,000 paper account,
Rs 500 max loss a trade, after Zerodha intraday charges. One-hour bars (free
5-minute data only reaches back 60 days).

| Rules | Trades | Win rate | Net | Profit factor | Losing months |
|---|---|---|---|---|---|
| Original (1.5-2% movers, trade neutral days, 2R) | 137 | 52.6% | +Rs 2,660 | 1.19 | - |
| **Money standard, 1:2** | 75 | **54.7%** | **+Rs 6,681** | **2.12** | 3 of 13 |
| **Money high accuracy, 1:2** | 40 | **65.0%** | +Rs 5,121 | 2.82 | 4 of 13 |

Risk:reward over the same 12 months (standard, Rs 1,000 desired profit):
1:1 +Rs 6,806 (57.3% wins) · 1:1.5 +Rs 8,369 (56.0%) · 1:2 +Rs 6,681 (54.7%) ·
1:2.5 +Rs 5,848 (54.7%) · 1:3 +Rs 4,855 (54.7%). Most trades end at the 3:10 PM
exit; a wider target is reached less often in one afternoon.

How the improvements were chosen, so they are not just fitted to the past:
1,920 rule combinations were tested on the **first 8 months only**. The best
ones were then checked on the **last 4 months**, which the search never saw:
the original rules made -Rs 111 there; Money standard made +Rs 2,015 (53%
wins, profit factor 1.61); high accuracy +Rs 1,966 (60% wins, 2.04).
On the last 40 days of real 5-minute data: original +Rs 1,879 (44.8% wins),
standard +Rs 2,216 (57.9%), high accuracy +Rs 2,253 (61.1%).

The biggest single gain: **skipping neutral days**. Over the year the original
rules lost Rs 2,624 on days NIFTY's first hour went nowhere.

## How the move is measured (setting: "Move measured")

| Rule | 12 months | Last 40 days |
|---|---|---|
| **From yesterday's close, 1-2% (default)** | 75 trades, 54.7% wins, +Rs 6,681 | 17 trades, 58.8%, +Rs 2,787 |
| Within the first hour (9:15 open to 10:15), 1.5-2% | 61 trades, 45.9% wins, -Rs 1,387 | 18 trades, 44.4%, -Rs 2,095 |
| Within the first hour, score 80+ | 43 trades, 53.5%, +Rs 1,160 | 16 trades, 43.8%, -Rs 1,839 |

A stock that already ran 1.5-2% inside the first hour has often spent its move;
one that is up from yesterday (gap plus first hour) and still near its high
tends to keep going. The first-hour rule is kept as an option
(`--basis open`), not the default.

## F&O stocks

Choose "F&O stocks" in the stock list (or `--list fno`) to scan NSE's ~218
stocks with futures and options (NSE's lot-size file). Tested the same way:

| 12 months, standard | Trades | Win rate | Profit factor | Net |
|---|---|---|---|---|
| NIFTY 100 | 75 | 56.0% | 2.14 | +Rs 6,783 |
| F&O stocks | 83 | 51.8% | 1.31 | +Rs 2,877 |
| - of which in NIFTY 100 | 33 | 51.5% | 2.01 | +Rs 2,844 |
| - of which outside NIFTY 100 | 50 | 52.0% | 1.01 | +Rs 34 |

Last 40 days (5-minute bars): F&O 21 trades, 61.9% wins, +Rs 1,694; NIFTY 100
17 trades, 58.8%, +Rs 2,456. Over the year the extra F&O names only broke
even, so NIFTY 100 stays the default. Money trades the shares (cash, MIS), not
futures or options contracts. (The test uses today's F&O list for the whole
year, which flatters it slightly.)

## Files

```
money.py        start the app, or run pick / backtest in the terminal
engine.py       the strategy: market read, scoring, entry, stop, target, sizing, charges
data.py         Yahoo Finance charts and NSE index lists, cached in ./cache
server.py       the local web server and its JSON API
web/index.html  the dashboard
```

## Honest limits

- Free Yahoo Finance data: a few minutes delayed and unofficial.
- The 12-month test uses 1-hour bars, so its entries can be up to an hour later
  than live. A year is still a small sample.
- Past results are not a promise. Most people who day trade lose money.
- Money does not place orders. In Zerodha, a pick is an intraday (MIS) order;
  place the stop-loss and the target yourself and cancel the other when one fills.
- NSE holidays are listed for 2026; add 2027's to `data.py` when NSE publishes them.
- Not financial advice.

Money: AI-based personal financial assistance system. VJCET, CSE.


## Hour trend (second strategy)

Strategy → **Hour trend (11:15–12:15)**, or `python3 money.py pick --strategy hour`.
At 12:15 PM Money reads the 11:15 AM–12:15 PM hour: NIFTY at least ±0.25% on yesterday **and** moving the same way in that hour
(otherwise no trade). Stocks 1%–3% on yesterday whose 11:15–12:15 hour also moved that way, chart score 80+, best one picked.
Entry on a 5-minute close beyond that hour's high/low, stop at its midpoint, 1:2, stop to entry at +1R, out by 3:10 PM.

| Test | Trades | Wins | Profit factor | Net |
|---|---|---|---|---|
| 12 months, 1-hour bars | 63 | 54.0% | 1.97 | +₹3,671 |
| first 8 months / last 4 months | 39 / 24 | 59.0% / 45.8% | 2.09 / 1.80 | +₹2,406 / +₹1,265 |
| last 40 days, 5-minute bars | 20 | 65.0% | 5.74 | +₹2,486 |

The 10:15–11:15 and 12:15–1:15 hours did not hold up, so only 11:15–12:15 is used. With the first-hour trade, 12 months: +₹10,226.

## On your phone

**Same Wi-Fi, laptop on:** double-click `Start Money for phone.command`. The window shows an address like
`http://192.168.1.5:8050` and a 6-digit PIN (new each start). Open the address on your phone, username `money`, PIN as password.
The laptop's own browser never needs the PIN. If macOS asks whether Python may accept incoming connections, click Allow.

**Laptop off (free cloud, Render):**
1. Put this folder in a GitHub repository (github.com → New repository → private → "uploading an existing file", drag in every file and the `web` folder; not `cache`, `settings.json` or `__pycache__`).
2. render.com → sign in with GitHub → New → Blueprint → pick the repo. Render reads `render.yaml`.
3. Fill in the variables Render asks for: `MONEY_PASSWORD` (your choice), `MONEY_NTFY` (a hard-to-guess topic, e.g. `money-` + random letters), optionally `UPSTOX_TOKEN`.
4. Install the free **ntfy** app on your phone and subscribe to the same topic.
5. Keep it awake on market days: cron-job.org → new job → `https://<your-app>.onrender.com/healthz` every 10 minutes, Monday–Friday 8:45 AM–3:30 PM IST.
   Free Render apps sleep after 15 idle minutes and lose their cache; this uses about 150 of the 750 free hours a month.

The server then sends push alerts by itself (pick at 10:15 and 12:15, ENTRY, +1R, TARGET/STOP, CLOSE NOW at 3:05) even when no page is open.
Paper trading only: Money never places orders.
