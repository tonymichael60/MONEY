#!/usr/bin/env python3
"""Money: first-hour momentum, intraday, NSE. A personal trading assistant (paper trading).

  python money.py                 start the dashboard at http://127.0.0.1:8050 and open the browser
  python money.py --port 9000     another port
  python money.py pick            today's pick in the terminal (after 10:15 AM IST)
  python money.py pick --date 2026-11-11 --mode high
  python money.py backtest        12 months, hourly bars, NIFTY 100
  python money.py calc RELIANCE --profit 1500 --rr 2    quantity, stop-loss and target for any stock

Needs Python 3.8+ and an internet connection. Nothing to install. Not financial advice.
"""
import argparse
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402


def _cli(kind, a):
    q = {"list": [a.list], "mode": [a.mode], "strategy": [a.strategy], "hour_end": [a.hour_end or "now"]}
    if kind == "analyse" and a.date:
        q["date"] = [a.date]
    if kind == "backtest":
        q["period"] = [a.period]
    if a.budget:
        q["budget"] = [str(a.budget)]
    if a.profit:
        q["profit"] = [str(a.profit)]
    for k in ("basis", "min_move", "max_move"):
        if getattr(a, k, None):
            q[k] = [str(getattr(a, k))]
    if a.rr:
        q["rr"] = [str(a.rr)]

    def prog(done, total, msg):
        sys.stderr.write("\r%-60s" % ("%s (%d/%d)" % (msg, done, total))[:60])
    server._progress = prog
    try:
        res = (server.analyse if kind == "analyse" else server.backtest)(q)
    except server.D.DataError as e:
        sys.stderr.write("\n")
        print("Money: %s" % e)
        return 1
    sys.stderr.write("\r" + " " * 60 + "\r")
    if kind == "analyse":
        m = res["market"]
        pp = res["params"]
        print("Desired profit Rs %.0f a trade, risk:reward 1:%g -> max loss Rs %.0f" % (pp["profit_goal"], pp["r"], pp["max_loss"]))
        print("Money %s  NIFTY %+.2f%% -> %s  (%d up / %d down, VIX %s)" % (
            res["date"], m["change_pct"], m["bias"].upper(), m["advancers"], m["decliners"], m["vix"]))
        if res.get("strategy") != "firsthour":
            print("Hour %s%s" % (res["window_label"], "" if res.get("tested") else "  (NOT the tested hour: paper-trade only)"))
        if res.get("note"):
            print(res["note"])
        for c in res["shortlist"][:8]:
            print("  %-11s %-4s %+5.2f%% score %3d %s" % (c["symbol"], c["side"].upper(), c["change_pct"], c["score"],
                                                     ("[%s]" % c["skip"]) if c["skip"] else ""))
        pk = res.get("pick")
        if pk:
            o, pl = pk["outcome"], pk["plan"]
            print("\nPICK %s %s  trigger %.2f  stop %.2f  target %.2f (1:%s)" % (
                "BUY" if pk["side"] == "buy" else "SHORT", pk["symbol"], pl["trigger"], pl["stop"],
                o["target"] if o.get("entered") else pl["target"], pl["r"]))
            if o.get("entered"):
                print("  entry %s at %.2f, %d shares, stop %.2f, target %.2f" % (o["entry_time"], o["entry"], o.get("qty", 0), o["stop"], o["target"]))
                print("  at the target: +Rs %.0f   at the stop: -Rs %.0f (before charges)" % (
                    o.get("qty", 0) * abs(o["target"] - o["entry"]), o.get("qty", 0) * o["risk"]))
                if o.get("net") is not None:
                    print("  exit %s at %s %.2f: net Rs %.2f after Rs %.2f charges" % (
                        o["exit"], o["exit_time"], o["exit_price"], o["net"], o["charges"]))
                elif o.get("open"):
                    print("  OPEN: last %.2f at %s (%+.2fR)" % (o["last_price"], o["last_time"], o["r_now"]))
            else:
                print("  %s" % o.get("reason", "no entry"))
    else:
        s = res["summary"]
        print("Money backtest %s to %s, %s bars, %d stocks, mode %s" % (res["from"], res["to"], res["bars"], res["stocks"], a.mode))
        print("  trades %d  win rate %s%%  profit factor %s  net Rs %s  max drawdown Rs %s" % (
            s.get("trades"), s.get("win_rate"), s.get("profit_factor"), s.get("net"), s.get("max_drawdown")))
        for m in res["months"]:
            print("  %s  %2d trades  %2d wins  Rs %9.2f" % (m["month"], m["trades"], m["wins"], m["net"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Money: first-hour momentum for NSE intraday (paper trading).")
    ap.add_argument("cmd", nargs="?", choices=("serve", "pick", "backtest", "calc"), default="serve")
    ap.add_argument("symbol", nargs="?", help="calc: the NSE symbol")
    ap.add_argument("--side", default="auto", choices=("auto", "buy", "sell"))
    ap.add_argument("--strategy", default="firsthour", choices=("firsthour", "hour", "hournow"),
                    help="firsthour: 9:15-10:15 (default); hour: hour trend on 11:15 AM-12:15 PM (tested); hournow: hour trend on the latest hour")
    ap.add_argument("--hour-end", help="hournow: the hour ending at HH:MM (default: now)")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--phone", action="store_true", help="also open Money to your phone on the same Wi-Fi, with a PIN")
    ap.add_argument("--host", default=None, help="address to listen on (default 127.0.0.1; the cloud uses 0.0.0.0)")
    ap.add_argument("--list", default="nifty100", choices=("nifty50", "nifty100", "nifty200", "nifty500", "fno"))
    ap.add_argument("--mode", default="standard", choices=("standard", "high", "original"))
    ap.add_argument("--date", help="YYYY-MM-DD: replay a past day (last 60 days)")
    ap.add_argument("--period", default="year", choices=("year", "60d"))
    ap.add_argument("--budget", type=float)
    ap.add_argument("--basis", choices=("prev_close", "open"), help="measure the move from yesterday's close (default) or within the first hour (open)")
    ap.add_argument("--min-move", type=float)
    ap.add_argument("--max-move", type=float)
    ap.add_argument("--profit", type=float, help="desired profit per trade in rupees (default 1000)")
    ap.add_argument("--rr", type=float, help="risk:reward as the reward multiple, 2 means 1:2 (default): max loss = profit / rr")
    a = ap.parse_args()
    if a.cmd == "pick":
        return _cli("analyse", a)
    if a.cmd == "backtest":
        return _cli("backtest", a)
    if a.cmd == "calc":
        q = {"symbol": [a.symbol or ""], "side": [a.side], "mode": [a.mode], "strategy": [a.strategy], "hour_end": [a.hour_end or "now"]}
        for k, v in (("date", a.date), ("budget", a.budget), ("profit", a.profit), ("rr", a.rr), ("basis", a.basis),
                     ("min_move", a.min_move), ("max_move", a.max_move)):
            if v:
                q[k] = [str(v)]
        try:
            c = server.calc(q)
        except server.D.DataError as e:
            print("Money: %s" % e)
            return 1
        print("%s %s on %s (%s)%s" % ("BUY" if c["side"] == "buy" else "SHORT SELL", c["symbol"], c["date"], c["side_from"],
                                       "  " + c["note"] if c["note"] else ""))
        print("  quantity %d   entry %s %.2f   stop-loss %.2f   target %.2f  (risk:reward 1:%g)" % (
            c["qty"], "at" if c["entered"] else "trigger", c["entry"] if c["entered"] else c["trigger"], c["stop"], c["target"], c["params"]["r"]))
        print("  profit at target +Rs %.0f   loss at stop -Rs %.0f   charges about Rs %.0f" % (c["profit_at_target"], c["loss_at_stop"], c["charges"]))
        if c["capped"]:
            print("  budget limits it to %d shares (%d would make your Rs %.0f)" % (c["qty"], c["qty_wanted"], c["params"]["profit_goal"]))
        if not c["fits_rules"]:
            print("  not a Money pick today: the tested edge applies to Money's own picks")
        return 0
    host = a.host or "127.0.0.1"
    if os.environ.get("PORT"):                     # cloud host (Render): listen on its port, all addresses
        a.port, host, a.no_browser = int(os.environ["PORT"]), "0.0.0.0", True
        if not os.environ.get("MONEY_PASSWORD"):
            print("Money: set MONEY_PASSWORD before putting Money on the internet.")
            return 1
    if a.phone:
        import secrets
        import socket
        host = "0.0.0.0"
        if not os.environ.get("MONEY_PASSWORD"):
            server.PIN["pin"] = "%06d" % secrets.randbelow(10 ** 6)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
        except OSError:
            ip = socket.gethostname()
    srv = server.serve(a.port, host)
    import push
    if push.start():
        print("Phone push alerts on (ntfy topic set).")
    url = "http://127.0.0.1:%d" % a.port
    print("Money is running at %s  (Ctrl+C to stop)" % url)
    if a.phone:
        print("")
        print("  On your phone (same Wi-Fi):  http://%s:%d" % (ip, a.port))
        print("  Username: money    Password: %s" % (server.PIN.get("pin") or "your MONEY_PASSWORD"))
        print("  (a new PIN each time you start; keep this window open)")
        print("")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nMoney stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
