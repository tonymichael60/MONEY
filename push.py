"""Money push alerts: a watcher that runs inside the server and sends phone notifications through ntfy.sh.

Set MONEY_NTFY to a hard-to-guess topic name (e.g. money-tony-x7k2q9) and subscribe to the same topic in the
free ntfy app (Android / iPhone). The server then alerts you even when no Money page is open:
the pick (or "no trade") at 10:15 AM and 12:15 PM, ENTRY with quantity, stop-loss and target, +1R,
TARGET HIT / STOP HIT, no entry by 2:30 PM, and CLOSE NOW at 3:05 PM. Paper trading only: nothing is ordered.
"""
import json
import os
import threading
import time
import urllib.request

import data as D
import server as S

NTFY = "https://ntfy.sh/"


def topic():
    return (os.environ.get("MONEY_NTFY") or D.settings().get("ntfy_topic") or "").strip()


def send(title, body, prio="default", tags=""):
    t = topic()
    if not t:
        return False
    req = urllib.request.Request(NTFY + urllib.request.quote(t), data=body.encode("utf-8"), method="POST",
                                 headers={"Title": title.encode("utf-8").decode("latin-1", "ignore"),
                                          "Priority": prio, "Tags": tags, "User-Agent": D.UA})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception:
        return False


def _q(strategy, extra=None):
    q = {"strategy": [strategy], "mode": [os.environ.get("MONEY_MODE", "standard")]}
    for k, env in (("budget", "MONEY_BUDGET"), ("profit", "MONEY_PROFIT"), ("rr", "MONEY_RR"), ("list", "MONEY_LIST")):
        if os.environ.get(env):
            q[k] = [os.environ[env]]
    for k, v in (extra or {}).items():
        q[k] = [v]
    return q


STRATS = (("firsthour", "10:15", "First hour"), ("hour", "12:15", "Hour trend"))


class Watcher:
    def __init__(self):
        self.day = None
        self.fired = set()
        self.picks = {}          # strategy -> {"symbol", "side"} or None (no trade)

    def once(self, key, title, body, prio="default", tags=""):
        if key in self.fired:
            return
        self.fired.add(key)
        send(title, body, prio, tags)

    def step(self):
        st = D.market_status()
        now = D.now_ist().strftime("%H:%M")
        if st["date"] != self.day:
            self.day, self.fired, self.picks = st["date"], set(), {}
        if not st["trading_day"] or now < "09:10" or now > "15:15":
            return
        self.once("open", "Money: market opens 9:15", "First-hour pick at 10:15 AM, hour-trend pick at 12:15 PM.", "low", "bell")
        for strat, gate, name in STRATS:
            if now < gate:
                continue
            if strat not in self.picks:
                try:
                    res = S.analyse(_q(strat, {"date": st["date"]}))
                except Exception as e:
                    if now > "14:30":
                        self.picks[strat] = None
                    continue
                pk = res.get("pick")
                self.picks[strat] = {"symbol": pk["symbol"], "side": pk["side"]} if pk else None
                if not pk:
                    self.once(strat + "-none", "Money %s: no trade" % name, res.get("note") or "No stock met the rules.", "low", "zzz")
                    continue
                pl = pk["plan"]
                self.once(strat + "-pick", "Money %s: %s %s" % (name, "BUY" if pk["side"] == "buy" else "SHORT", pk["symbol"]),
                          "Wait for a 5-min close %s Rs %.2f. Stop-loss Rs %.2f, target Rs %.2f. Entry only before 2:30 PM." % (
                              "above" if pk["side"] == "buy" else "below", pl["trigger"], pl["stop"], pl["target"]), "high", "chart_with_upwards_trend")
            p = self.picks.get(strat)
            if p:
                self.track(strat, name, p, now)

    def track(self, strat, name, p, now):
        try:
            c = S.calc(_q(strat, {"symbol": p["symbol"], "side": p["side"]}))
        except Exception:
            return
        o = c.get("outcome") or {}
        word, k = ("BUY" if p["side"] == "buy" else "SHORT"), strat + "-" + p["symbol"]
        if o.get("entered"):
            if o.get("exit") and k + "-entry" not in self.fired:
                self.fired.add(k + "-entry")           # already over when Money started: report the result only
            self.once(k + "-entry", "ENTRY: %s %s x%d" % (word, p["symbol"], c["qty"]),
                      "%s at Rs %.2f. Place it now (MIS): stop-loss Rs %.2f, target Rs %.2f. Profit at target Rs %.0f, loss at stop Rs %.0f." % (
                          o.get("entry_time", ""), o["entry"], c["stop"], c["target"], c["profit_at_target"], c["loss_at_stop"]), "urgent", "rotating_light")
            ex = o.get("exit")
            if (o.get("r_now") or 0) >= 1 or o.get("moved_be"):
                self.once(k + "-1r", "+1R: %s" % p["symbol"], "Move your stop-loss to the entry, Rs %.2f." % o["entry"], "high", "white_check_mark")
            if ex == "target":
                self.once(k + "-exit", "TARGET HIT: %s" % p["symbol"], "Rs %.2f at %s. Cancel the stop-loss order." % (o["exit_price"], o["exit_time"]), "urgent", "tada")
            elif ex == "stop":
                self.once(k + "-exit", "STOP HIT: %s" % p["symbol"], "Rs %.2f at %s. Cancel the target order." % (o["exit_price"], o["exit_time"]), "urgent", "x")
            elif ex == "breakeven":
                self.once(k + "-exit", "OUT AT ENTRY: %s" % p["symbol"], "The stop had moved to the entry. Cancel the target order.", "high", "warning")
            elif o.get("open") and now >= "15:05":
                self.once(k + "-flat", "CLOSE %s NOW" % p["symbol"], "Exit by 3:10 PM (last Rs %.2f). Cancel the stop-loss and target orders." % (o.get("last_price") or 0), "urgent", "alarm_clock")
        elif now >= "14:30":
            self.once(k + "-noentry", "No entry: %s" % p["symbol"], "%s never triggered by 2:30 PM. No trade." % name, "low", "zzz")


def start():
    """Run the watcher in the background if a topic is set."""
    if not topic():
        return None
    w = Watcher()

    def loop():
        while True:
            try:
                w.step()
            except Exception:
                pass
            time.sleep(60)

    th = threading.Thread(target=loop, daemon=True, name="money-push")
    th.start()
    return w
