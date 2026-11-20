"""Money strategy engine: first-hour momentum, intraday, NSE.

Works on 5-minute bars (live, last 60 days) or 1-hour bars (two years of
history). Pure functions: data in, picks and trade results out.
"""
import datetime
import math

IST = datetime.timedelta(hours=5, minutes=30)
OPEN = datetime.time(9, 15)
FIRST_HOUR = 60

# NSE cash tick sizes (circular CMTR67133, from 15 Apr 2025)
TICKS = ((250, 0.01), (1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.00), (float("inf"), 5.00))

# Tuned on 12 months of NIFTY 100 data (Nov 2025 to Nov 2026): settings chosen on the first
# 8 months, confirmed on the last 4 and on 5-minute data. See README for the numbers.
DEFAULTS = {
    "min_move": 1.0, "max_move": 2.0,      # the stock's move at 10:15, percent
    "basis": "prev_close",                  # prev_close: from yesterday's close (tested, the default);
                                            # open: within the first hour, 9:15 open -> 10:15 (tested weaker, see README)
    "side": "trend",                        # trend: with the market, no trade on a neutral first hour;
                                            # auto: neutral -> both sides; buy; sell; both
    "neutral_band": 0.25,                   # NIFTY within +-0.25% of yesterday = neutral
    "stop": "mid",                          # mid: first-hour midpoint; low: first-hour low (high for shorts)
    "r": 2.0,                               # reward:risk, the target in R (2 = target twice as far as the stop)
    "entry": "break",                       # break: a close beyond the first-hour high/low; now: at 10:15
    "trail_be": True,                       # move the stop to the entry once +1R is reached
    "min_score": 70,
    "max_range_pct": 4.0,
    "cutoff": "14:30", "flat_by": "15:10",
    "budget": 50000, "max_loss": 500, "max_position_pct": 75, "cash_buffer_pct": 10,
    "profit_goal": 1000,                    # rupees wanted at the target; sets max_loss = profit_goal / r
}


MODES = {
    "standard": {},
    "high": {"min_score": 80},              # fewer trades, higher win rate
    "firsthour": {"basis": "open", "min_move": 1.5},   # the move inside the first hour only (user's rule)
    "original": {"min_move": 1.5, "side": "auto", "r": 2.0, "trail_be": False, "min_score": 0, "basis": "prev_close"},
}


def params(mode="standard", **over):
    p = dict(DEFAULTS)
    p.update(MODES.get(mode, {}))
    p.update({k: v for k, v in over.items() if v is not None})
    if p.get("profit_goal") and p.get("r"):
        # The user names the profit wanted per trade and the reward:risk ratio; the loss at the stop
        # follows: shares are sized so the target makes about profit_goal and the stop costs profit_goal / r.
        p["max_loss"] = round(float(p["profit_goal"]) / float(p["r"]), 2)
    return p


def tick_for(p):
    for up, t in TICKS:
        if abs(p) < up:
            return t
    return 5.0


def round_tick(x, how):
    t = tick_for(x)
    n = x / t
    n = math.floor(n + 1e-9) if how == "down" else math.ceil(n - 1e-9) if how == "up" else round(n)
    return round(n * t, 2)


def charges(qty, buy, sell):
    """Zerodha equity intraday charges for one round trip, rupees (zerodha.com/charges, Nov 2026)."""
    bv, sv = qty * buy, qty * sell
    brk = min(20.0, bv * 0.0003) + min(20.0, sv * 0.0003)
    stt = sv * 0.00025
    txn = (bv + sv) * 0.0000307
    sebi = (bv + sv) * 10 / 1e7
    stamp = bv * 0.00003
    gst = (brk + txn + sebi) * 0.18
    return round(brk + stt + txn + sebi + stamp + gst, 2)


def hm(t):
    return t.hour * 60 + t.minute


def parse_chart(doc, bar_min):
    """Yahoo chart JSON -> {date: [bar]} in IST, regular session, bars on the grid."""
    res = doc["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    days = {}
    for i, ts in enumerate(res.get("timestamp") or []):
        o, h, l, c, v = (q.get(k)[i] if q.get(k) and i < len(q[k]) else None for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        lt = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts) + IST
        m = hm(lt.time()) - hm(OPEN)
        if m < 0 or m % bar_min or m >= 375:
            continue
        days.setdefault(lt.date(), []).append({"t": lt, "m": m, "o": o, "h": h, "l": l, "c": c, "v": v or 0})
    for d in days:
        days[d].sort(key=lambda b: b["m"])
    return days


def parse_daily(doc):
    res = doc["chart"]["result"][0] if "chart" in doc else doc
    out = {}
    closes = res["indicators"]["quote"][0]["close"]
    for ts, c in zip(res.get("timestamp") or [], closes):
        if c is not None:
            out[(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts) + IST).date()] = c
    return out


def prev_close(daily, day):
    prior = [d for d in daily if d < day]
    return daily[max(prior)] if prior else None


def first_hour(bars, bar_min):
    fh = [b for b in bars if b["m"] < FIRST_HOUR]
    return fh if len(fh) == FIRST_HOUR // bar_min else None


def ema(vals, n):
    k, e = 2.0 / (n + 1), None
    for x in vals:
        e = x if e is None else x * k + e * (1 - k)
    return e


def market(index_days, index_daily, vix_days, breadth, day, bar_min, band, basis="prev_close"):
    fh = first_hour(index_days.get(day) or [], bar_min)
    if not fh:
        return None
    pc = prev_close(index_daily, day)
    last = fh[-1]["c"]
    chg = 100.0 * (last / pc - 1) if pc else 0.0
    vs_open = 100.0 * (last / fh[0]["o"] - 1)
    if basis == "open":     # NIFTY's own first hour, 9:15 open -> 10:15
        bias = "bullish" if vs_open >= band else "bearish" if vs_open <= -band else "neutral"
    else:
        bias = "bullish" if chg >= band and vs_open > -0.1 else "bearish" if chg <= -band and vs_open < 0.1 else "neutral"
    vx = first_hour(vix_days.get(day) or [], bar_min) if vix_days else None
    return {"index": round(last, 2), "change_pct": round(chg, 2), "vs_open_pct": round(vs_open, 2), "bias": bias,
            "move_pct": round(vs_open if basis == "open" else chg, 2), "basis": basis,
            "advancers": breadth[0], "decliners": breadth[1],
            "vix": round(vx[-1]["c"], 2) if vx else None,
            "vix_change_pct": round(100.0 * (vx[-1]["c"] / vx[0]["o"] - 1), 2) if vx else None}


def features(sym, days, daily, day, bar_min, index_chg, basis="prev_close"):
    """The stock's first-hour reading and chart score (independent of the trade rules)."""
    fh = first_hour(days.get(day) or [], bar_min)
    pc = prev_close(daily, day)
    if not fh or not pc:
        return None
    last = fh[-1]["c"]
    chg = 100.0 * (last / pc - 1)
    fh_move = 100.0 * (last / fh[0]["o"] - 1)          # the first hour itself: 9:15 open -> 10:15
    move = fh_move if basis == "open" else chg
    s = 1 if move > 0 else -1
    hi, lo = max(b["h"] for b in fh), min(b["l"] for b in fh)
    vol = sum(b["v"] for b in fh)
    pv = sum((b["h"] + b["l"] + b["c"]) / 3.0 * b["v"] for b in fh)
    vw = pv / vol if vol else (hi + lo + last) / 3.0
    prior = sorted(d for d in days if d < day)
    pts, why = 0, []
    if s * (last - vw) > 0:
        pts += 20
        why.append("%s VWAP" % ("above" if s > 0 else "below"))
        if all(s * (b["c"] - vw) > 0 for b in fh[-3:]):
            pts += 5
    loc = (last - lo) / (hi - lo) if hi > lo else 0.5
    loc = loc if s > 0 else 1 - loc
    pts += int(round(20 * max(0.0, min(1.0, (loc - 0.4) / 0.5))))
    if loc >= 0.7:
        why.append("near the first-hour %s" % ("high" if s > 0 else "low"))
    warm = [b["c"] for d in prior[-(2 if bar_min == 5 else 6):] for b in days[d]] + [b["c"] for b in fh]
    if s * (ema(warm, 9) - ema(warm, 20)) > 0:
        pts += 15
        why.append("EMA 9 %s EMA 20" % ("over" if s > 0 else "under"))
    pv_ = [sum(b["v"] for b in (first_hour(days[d], bar_min) or [])) for d in prior[-20:]]
    pv_ = [x for x in pv_ if x]
    rvol = vol / (sum(pv_) / len(pv_)) if pv_ and sum(pv_) else None
    if rvol:
        pts += int(round(20 * max(0.0, min(1.0, (rvol - 0.8) / 1.2))))
        if rvol >= 1.3:
            why.append("volume %.1fx usual" % rvol)
    rs = s * (move - index_chg)
    if rs >= 1.0:
        pts += 10
        why.append("%.1f%% %s than NIFTY" % (rs, "stronger" if s > 0 else "weaker"))
    elif rs > 0:
        pts += 5
    closes = [daily[d] for d in sorted(daily) if d < day][-20:]
    above20 = None
    if len(closes) >= 15:
        above20 = pc > sum(closes) / len(closes)
        if (above20 and s > 0) or (not above20 and s < 0):
            pts += 10
            why.append("%s its 20-day average" % ("above" if s > 0 else "below"))
    return {"symbol": sym, "side": "buy" if s > 0 else "sell", "change_pct": round(chg, 2), "fh_move_pct": round(fh_move, 2),
            "move_pct": round(move, 2), "price": round(last, 2),
            "prev_close": round(pc, 2), "fh_high": round(hi, 2), "fh_low": round(lo, 2), "fh_open": round(fh[0]["o"], 2),
            "fh_range_pct": round(100.0 * (hi - lo) / last, 2), "vwap": round(vw, 2), "rvol": round(rvol, 2) if rvol else None,
            "loc": round(loc, 2), "score": pts, "why": why}


def size(entry, stop, p):
    risk = abs(entry - stop)
    if risk <= 0 or not p.get("budget"):
        return 0
    b = float(p["budget"])
    lim = [p["max_loss"] / risk, b * p["max_position_pct"] / 100 / entry, b * (1 - p["cash_buffer_pct"] / 100) / entry]
    return max(0, int(min(lim)))


def plan_for(c, p):
    s = 1 if c["side"] == "buy" else -1
    if p["stop"] == "low":
        raw = c["fh_low"] if s > 0 else c["fh_high"]
    else:
        raw = (c["fh_high"] + c["fh_low"]) / 2.0
    stop = round_tick(raw, "down" if s > 0 else "up")
    trigger = c["fh_high"] if s > 0 else c["fh_low"]
    # expected target if the entry fills at the trigger (the real one is set from the actual entry price)
    target = round_tick(trigger + s * p["r"] * abs(trigger - stop), "up" if s > 0 else "down")
    return {"symbol": c["symbol"], "side": c["side"], "trigger": trigger, "stop": stop, "r": p["r"], "target": target,
            "entry_rule": p["entry"], "trail_be": p["trail_be"], "cutoff": p["cutoff"], "flat_by": p["flat_by"]}


def _clock(t):
    return "%d:%02d %s" % (t.hour % 12 or 12, t.minute, "AM" if t.hour < 12 else "PM")


def simulate(bars, plan, bar_min, live=False):
    """One day after the first hour: entry, then target / stop / flat-by. The stop wins a tie inside a bar.
    live=True: the day is still going; an unfinished trade is reported as waiting or open, not closed."""
    s = 1 if plan["side"] == "buy" else -1
    cut = hm(datetime.time(*map(int, plan["cutoff"].split(":")))) - hm(OPEN)
    flat = hm(datetime.time(*map(int, plan["flat_by"].split(":")))) - hm(OPEN)
    after = [b for b in bars if b["m"] >= FIRST_HOUR]
    out = {"entered": False}
    entry = None
    k0 = 0
    if plan["entry_rule"] == "now":
        fh = [b for b in bars if b["m"] < FIRST_HOUR]
        if not fh:
            return out
        entry = fh[-1]["c"]
        out.update(entry_time=_clock((fh[-1]["t"] + datetime.timedelta(minutes=bar_min)).time()))
    else:
        for k, b in enumerate(after):
            if b["m"] + bar_min > cut:
                out["reason"] = "no %s-minute close beyond the first-hour %s by %s" % (
                    bar_min, "high" if s > 0 else "low", plan["cutoff"])
                return out
            if s * (b["c"] - plan["trigger"]) > 0:
                entry, k0 = b["c"], k + 1
                out.update(entry_time=_clock((b["t"] + datetime.timedelta(minutes=bar_min)).time()))
                break
        if entry is None:
            out["reason"] = "waiting for a close beyond the trigger" if live else "no entry"
            out["waiting"] = live
            return out
    risk = s * (entry - plan["stop"])
    if risk <= entry * 0.0015:
        out["reason"] = "already through the stop"
        return out
    target = round_tick(entry + s * plan["r"] * risk, "nearest") if plan["r"] else None
    stop = plan["stop"]
    out.update(entered=True, entry=round(entry, 2), stop=stop, target=target, risk=round(risk, 2))
    for b in after[k0:]:
        if b["m"] >= flat:
            out.update(exit="time", exit_price=round(b["o"], 2), exit_time=_clock(b["t"].time()),
                       r=round(s * (b["o"] - entry) / risk, 2))
            return out
        if (b["l"] <= stop) if s > 0 else (b["h"] >= stop):
            px = stop if s * (b["o"] - stop) >= 0 else b["o"]
            out.update(exit="stop" if stop != entry else "breakeven", exit_price=round(px, 2), exit_time=_clock(b["t"].time()),
                       r=round(s * (px - entry) / risk, 2))
            return out
        if target and ((b["h"] >= target) if s > 0 else (b["l"] <= target)):
            out.update(exit="target", exit_price=target, exit_time=_clock(b["t"].time()), r=float(plan["r"]))
            return out
        if b["m"] + bar_min > flat:          # the bar that spans the flat-by time (hourly data): out at its close
            out.update(exit="time", exit_price=round(b["c"], 2), exit_time=_clock(datetime.time(15, 10)),
                       r=round(s * (b["c"] - entry) / risk, 2))
            return out
        if plan["trail_be"] and s * ((b["h"] if s > 0 else b["l"]) - (entry + s * risk)) >= 0:
            stop = entry
    lb = after[-1] if after else None
    if lb and live:
        out.update(open=True, last_price=round(lb["c"], 2), last_time=_clock((lb["t"] + datetime.timedelta(minutes=bar_min)).time()),
                   r_now=round(s * (lb["c"] - entry) / risk, 2), stop=stop)
        return out
    if lb:
        out.update(exit="time", exit_price=round(lb["c"], 2), exit_time=_clock(lb["t"].time()),
                   r=round(s * (lb["c"] - entry) / risk, 2))
    return out


def history(days, side, before, p, bar_min):
    """The same entry, stop and target rule on the stock's earlier sessions (no picking)."""
    rows = []
    for d in sorted(days):
        if d >= before:
            continue
        fh = first_hour(days[d], bar_min)
        if not fh:
            continue
        c = {"symbol": "", "side": side, "fh_high": max(b["h"] for b in fh), "fh_low": min(b["l"] for b in fh)}
        o = simulate(days[d], plan_for(c, p), bar_min)
        if o.get("entered") and "r" in o:
            rows.append(o)
    n = len(rows)
    ex = {}
    for o in rows:
        ex[o["exit"]] = ex.get(o["exit"], 0) + 1
    return {"sessions": len([d for d in days if d < before]), "fired": n, "exits": ex,
            "wins": sum(1 for o in rows if o["r"] > 0), "avg_r": round(sum(o["r"] for o in rows) / n, 2) if n else None}


def run_day(day, data, daily, index_days, vix_days, bar_min, p, feats=None, live=False):
    """The whole method for one day: market read, shortlist, pick, and the pick's trade."""
    up = down = 0
    for s, days in data.items():
        fh = first_hour(days.get(day) or [], bar_min)
        pc = prev_close(daily.get(s) or {}, day)
        ref = (fh[0]["o"] if fh else None) if p.get("basis") == "open" else pc
        if fh and ref:
            up += fh[-1]["c"] > ref
            down += fh[-1]["c"] < ref
    mc = market(index_days, daily.get("^NSEI") or {}, vix_days, (up, down), day, bar_min, p["neutral_band"],
                p.get("market_basis") or p.get("basis", "prev_close"))
    mc["move_pct"] = round(mc["vs_open_pct"] if p.get("basis") == "open" else mc["change_pct"], 2)   # strength compares like with like
    if not mc:
        return None
    rule = p["side"]
    if rule in ("auto", "trend"):
        # auto: with the market, both sides on a neutral day; trend: no trade on a neutral day
        rule = {"bullish": "buy", "bearish": "sell"}.get(mc["bias"], "both" if p["side"] == "auto" else "none")
    if rule == "none":
        return {"date": day.isoformat(), "market": mc, "side_rule": rule, "shortlist": [], "pick": None,
                "note": "neutral first hour: no trade"}
    if feats is None:
        feats = [f for f in (features(s, d, daily.get(s) or {}, day, bar_min, mc["move_pct"], p.get("basis", "prev_close"))
                             for s, d in data.items()) if f]
    cands = []
    for f in feats:
        if not p["min_move"] <= abs(f["move_pct"]) <= p["max_move"]:
            continue
        if rule in ("buy", "sell") and f["side"] != rule:
            continue
        c = dict(f)
        c["skip"] = None
        if c["fh_range_pct"] > p["max_range_pct"]:
            c["skip"] = "first-hour range too wide"
        elif c["score"] < p["min_score"]:
            c["skip"] = "score under %d" % p["min_score"]
        else:
            pl = plan_for(c, p)
            if size(pl["trigger"], pl["stop"], p) < 1:
                c["skip"] = "one share does not fit the budget"
        cands.append(c)
    cands.sort(key=lambda c: (c["skip"] is not None, -c["score"], -abs(c["move_pct"])))
    pick = next((c for c in cands if not c["skip"]), None)
    res = {"date": day.isoformat(), "market": mc, "side_rule": rule, "shortlist": cands, "pick": None}
    if pick:
        pl = plan_for(pick, p)
        o = simulate(data[pick["symbol"]].get(day) or [], pl, bar_min, live=live)
        if o.get("entered"):
            o["qty"] = q = size(o["entry"], o["stop"], p)
            if "exit_price" in o:
                g = q * (o["exit_price"] - o["entry"]) * (1 if pick["side"] == "buy" else -1)
                ch = charges(q, *((o["entry"], o["exit_price"]) if pick["side"] == "buy" else (o["exit_price"], o["entry"])))
                o.update(gross=round(g, 2), charges=ch, net=round(g - ch, 2))
        res["pick"] = dict(pick, plan=pl, outcome=o)
    return res


def summarize(results):
    t = [r["pick"]["outcome"] for r in results if r and r.get("pick") and r["pick"]["outcome"].get("net") is not None]
    if not t:
        return {"days": len(results), "trades": 0}
    wins = [x for x in t if x["net"] > 0]
    gp = sum(x["net"] for x in wins)
    gl = -sum(x["net"] for x in t if x["net"] <= 0)
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in t:
        eq += x["net"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    ex = {}
    for x in t:
        ex[x["exit"]] = ex.get(x["exit"], 0) + 1
    return {"days": len(results), "trades": len(t), "win_rate": round(100.0 * len(wins) / len(t), 1),
            "avg_r": round(sum(x["r"] for x in t) / len(t), 3), "net": round(sum(x["net"] for x in t), 2),
            "profit_factor": round(gp / gl, 2) if gl else None, "max_drawdown": round(dd, 2), "exits": ex,
            "avg_win": round(gp / len(wins), 2) if wins else 0, "avg_loss": round(gl / (len(t) - len(wins)), 2) if len(t) > len(wins) else 0}

# ---------------------------------------------------------------- hour trend: the same method on a later hour

HOUR_WINDOWS = (60, 120, 180, 240)     # 10:15-11:15, 11:15-12:15, 12:15-13:15, 13:15-14:15


def window_label(ws):
    a = datetime.datetime.combine(datetime.date.today(), OPEN) + datetime.timedelta(minutes=ws)
    return "%s to %s" % (_clock(a.time()), _clock((a + datetime.timedelta(minutes=60)).time()))


def shift(days, ws):
    """Re-time each session so the hour starting ws minutes after 9:15 reads as the 'first hour'.
    Bars before it are dropped; real clock times (t) are kept for the report."""
    if not ws:
        return days
    out = {}
    for d, bars in days.items():
        nb = [dict(b, m=b["m"] - ws) for b in bars if b["m"] >= ws]
        if nb:
            out[d] = nb
    return out


def _shift_clock(hhmm, ws):
    t = datetime.datetime.combine(datetime.date.today(), datetime.time(*map(int, hhmm.split(":")))) - datetime.timedelta(minutes=ws)
    return t.strftime("%H:%M")


HOUR_TESTED = 120                      # 11:15 AM to 12:15 PM: the only hour that held up out of sample


def run_hour(day, data, daily, index_days, vix_days, bar_min, p, ws=HOUR_TESTED, live=False):
    """Hour trend: after the first hour, read the market at the end of a later hour.
    Trend = NIFTY at least +/-0.25% on yesterday AND its last hour moving the same way.
    Stocks up/down 1% to 3% on yesterday whose last hour also moved that way, chart score 80+.
    Entry on a 5-minute close beyond that hour's high/low, stop at its midpoint, 1:2, out by 3:10 PM."""
    q = dict(p, basis="prev_close", market_basis="prev_close",
             min_move=p.get("hour_min_move", 1.0), max_move=p.get("hour_max_move", 3.0),
             min_score=max(p.get("min_score", 0), p.get("hour_min_score", 80)), neutral_band=p.get("hour_band", 0.25),
             cutoff=_shift_clock(p["cutoff"], ws), flat_by=_shift_clock(p["flat_by"], ws))
    sd = {s: shift(d, ws) for s, d in data.items()}
    si, sv = shift(index_days, ws), shift(vix_days or {}, ws)
    w = first_hour(si.get(day) or [], bar_min)
    if not w:
        return None
    pc = prev_close(daily.get("^NSEI") or {}, day)
    chg = 100.0 * (w[-1]["c"] / pc - 1) if pc else 0.0
    hmove = 100.0 * (w[-1]["c"] / w[0]["o"] - 1)
    # scored on the hour itself (move and strength vs NIFTY over that hour); filtered on the move since yesterday
    feats = [f for f in (features(s, d, daily.get(s) or {}, day, bar_min, hmove, "open") for s, d in sd.items()) if f]
    lo, hi = q["min_move"], q["max_move"]
    feats = [dict(f, hour_move_pct=f["move_pct"]) for f in feats
             if lo <= abs(f["change_pct"]) <= hi and (f["change_pct"] > 0) == (f["side"] == "buy")]
    q.update(min_move=0.0, max_move=99.0, market_basis="prev_close", basis="open")
    r = run_day(day, sd, daily, si, sv, bar_min, q, feats=feats, live=live)
    if not r:
        return None
    mc = r["market"]
    hour_move = mc["vs_open_pct"]
    mc["hour_move_pct"] = hour_move
    r.update(window=ws, window_label=window_label(ws), tested=ws == HOUR_TESTED)
    band = q["neutral_band"]
    bias = "bullish" if chg >= band and hour_move > 0 else "bearish" if chg <= -band and hour_move < 0 else "neutral"
    agree = bias != "neutral"
    if agree and r["side_rule"] == "none":      # run_day's own read (with its -0.1% open rule) said neutral
        rule = "buy" if bias == "bullish" else "sell"
        r = run_day(day, sd, daily, si, sv, bar_min, dict(q, side=rule), feats=feats, live=live)
        r["market"].update(hour_move_pct=hour_move)
        r.update(window=ws, window_label=window_label(ws), tested=ws == HOUR_TESTED)
        mc = r["market"]
    mc["bias"] = bias
    mc["change_pct"] = round(chg, 2)
    if mc["bias"] != "neutral" and not agree:
        r.update(side_rule="none", shortlist=[], pick=None)
    if r["side_rule"] == "none" or not agree:
        r["note"] = "no clear market trend at %s (NIFTY %+.2f%% on yesterday, %+.2f%% in that hour): no trade" % (
            window_label(ws).split(" to ")[1], mc["change_pct"], hour_move)
    return r
