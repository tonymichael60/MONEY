"""Money local web server: the dashboard and a small JSON API. Standard library only."""
import datetime
import json
import os
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import data as D
import engine as E

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
JOB = {"state": "idle", "done": 0, "total": 0, "msg": "", "result": None, "error": None, "kind": None}
LOCK = threading.Lock()


def _set(**kw):
    with LOCK:
        JOB.update(kw)


def _progress(done, total, msg):
    _set(done=done, total=total, msg=msg)


def _num(q, k, default=None, cast=float):
    v = (q.get(k) or [None])[0]
    if v in (None, ""):
        return default
    return cast(v)


def _params(q):
    basis = (q.get("basis") or [None])[0]
    p = E.params((q.get("mode") or ["standard"])[0], budget=_num(q, "budget"), profit_goal=_num(q, "profit"),
                 r=_num(q, "rr"), min_move=_num(q, "min_move"), max_move=_num(q, "max_move"),
                 basis=basis if basis in ("open", "prev_close") else None)
    if p.get("basis") == "open":
        p["market_basis"] = "open"          # judge NIFTY on its own first hour too
    return p


def _ws(q):
    """0 = the first-hour strategy; 120 = hour trend on 11:15 AM to 12:15 PM;
    hournow = hour trend on the latest completed hour (or the hour ending at hour_end)."""
    st = (q.get("strategy") or ["firsthour"])[0]
    if st == "hour":
        return E.HOUR_TESTED
    if st != "hournow":
        return 0
    end = ((q.get("hour_end") or [""])[0] or "now").strip()
    if end == "now":
        t = D.now_ist()
        m = t.hour * 60 + t.minute
    else:
        try:
            h, mm = map(int, end.split(":")[:2])
        except ValueError:
            raise D.DataError("hour ending must look like 12:15")
        m = h * 60 + mm
    m = (m - (9 * 60 + 15)) // 5 * 5           # minutes after 9:15, on the 5-minute grid
    if m < 60:
        raise D.DataError("the first hour ends at 10:15 AM IST: 'up to now' works from 10:15")
    return min(m - 60, 240) or -1               # the last hour that can still trade ends 2:15 PM; -1 marks 9:15-10:15


def _wsfix(ws):
    return 0 if ws == -1 else ws


def _runner(ws):
    if ws:
        ws = _wsfix(ws)
        return lambda d, L, bar_min, p, live=False: E.run_hour(d, L["data"], L["daily"], L["index"], L["vix"], bar_min, p, ws, live=live)
    return lambda d, L, bar_min, p, live=False: E.run_day(d, L["data"], L["daily"], L["index"], L["vix"], bar_min, p, live=live)


def _bars_out(bars):
    return [{"t": b["t"].strftime("%H:%M"), "o": round(b["o"], 2), "h": round(b["h"], 2), "l": round(b["l"], 2),
             "c": round(b["c"], 2), "v": b["v"]} for b in bars]


def analyse(q):
    p = _params(q)
    st = D.market_status()
    day = (q.get("date") or [None])[0]
    live = not day
    day = datetime.date.fromisoformat(day) if day else datetime.date.fromisoformat(st["date"])
    L = D.load((q.get("list") or ["nifty100"])[0], 5, progress=_progress, live=live)
    if day not in L["index"]:
        prior = [d for d in L["index"] if d < day]
        raise D.DataError("no NSE session on %s (%s). Pick a trading day, e.g. %s." % (
            day, st["phase"] if live else "holiday or weekend", max(prior) if prior else "a recent date"))
    raw = _ws(q)
    ws = _wsfix(raw)
    if not E.first_hour((E.shift(L["index"], ws) if ws else L["index"]).get(day) or [], 5):
        raise D.DataError("the first hour of %s is not complete yet: come back after 10:15 AM IST" % day if not ws else
                          "the %s hour of %s is not complete yet: come back after %s IST" % (
                              E.window_label(ws), day, E.window_label(ws).split(" to ")[1]))
    res = _runner(raw)(day, L, 5, p, live=live and day.isoformat() == st["date"])
    res["names"] = {c["symbol"]: L["names"].get(c["symbol"], "") for c in res["shortlist"]}
    res["shortlist"] = res["shortlist"][:15]
    res["live"] = live and day.isoformat() == st["date"]
    res["params"] = p
    if res.get("pick"):
        sym = res["pick"]["symbol"]
        res["pick"]["name"] = L["names"].get(sym, "")
        res["pick"]["bars"] = _bars_out(L["data"][sym].get(day) or [])
        if ws:
            hp = dict(p, cutoff=E._shift_clock(p["cutoff"], ws), flat_by=E._shift_clock(p["flat_by"], ws))
            res["pick"]["history"] = E.history(E.shift(L["data"][sym], ws), res["pick"]["side"], day, hp, 5)
        else:
            res["pick"]["history"] = E.history(L["data"][sym], res["pick"]["side"], day, p, 5)
    res["strategy"] = (q.get("strategy") or ["firsthour"])[0]
    res["window"] = ws
    res["window_label"] = E.window_label(ws)
    res["tested"] = (raw == E.HOUR_TESTED) or raw == 0
    res["failed"] = L["failed"]
    return res


def backtest(q):
    p = _params(q)
    bar_min = 60 if (q.get("period") or ["year"])[0] == "year" else 5
    if bar_min == 60 and _wsfix(_ws(q)) % 60:
        raise D.DataError("12-month backtests use 1-hour candles, so the hour must end at :15 (e.g. 12:15). Use 40 days for other times.")
    L = D.load((q.get("list") or ["nifty100"])[0], bar_min, progress=_progress)
    days = sorted(d for d in L["index"] if E.first_hour(L["index"][d], bar_min))
    days = [d for k, d in enumerate(days) if k >= 25][-(250 if bar_min == 60 else 40):]
    trades, nodays = [], 0
    run = _runner(_ws(q))
    for k, d in enumerate(days):
        if k % 10 == 0:
            _progress(k, len(days), "Replaying %s" % d)
        r = run(d, L, bar_min, p)
        if not r:
            continue
        pk = r.get("pick")
        o = (pk or {}).get("outcome") or {}
        if o.get("net") is None:
            nodays += 1
            continue
        trades.append({"date": d.isoformat(), "bias": r["market"]["bias"], "symbol": pk["symbol"], "side": pk["side"],
                       "score": pk["score"], "entry_time": o.get("entry_time"), "entry": o["entry"], "exit": o["exit"],
                       "exit_time": o.get("exit_time"), "exit_price": o["exit_price"], "qty": o["qty"], "r": o["r"],
                       "charges": o["charges"], "net": o["net"]})
    wrapped = [{"pick": {"outcome": {"net": t["net"], "r": t["r"], "exit": t["exit"]}}} for t in trades]
    s = E.summarize(wrapped)
    s["days"] = len(days)
    month = {}
    for t in trades:
        m = month.setdefault(t["date"][:7], {"month": t["date"][:7], "trades": 0, "wins": 0, "net": 0.0})
        m["trades"] += 1
        m["wins"] += t["net"] > 0
        m["net"] = round(m["net"] + t["net"], 2)
    return {"summary": s, "trades": trades, "months": list(month.values()), "from": days[0].isoformat(),
            "to": days[-1].isoformat(), "bars": "1-hour" if bar_min == 60 else "5-minute", "params": p,
            "stocks": len(L["data"])}


def calc(q):
    """Trade calculator for any NSE stock: desired profit + risk:reward -> side, entry, stop, target, quantity."""
    sym = ((q.get("symbol") or [""])[0]).strip().upper().replace(".NS", "")
    if not sym:
        raise D.DataError("type an NSE symbol, for example RELIANCE or TCS")
    p = _params(q)
    st = D.market_status()
    day = (q.get("date") or [None])[0]
    live = not day
    day = datetime.date.fromisoformat(day) if day else datetime.date.fromisoformat(st["date"])
    try:
        days = D.bars(sym, 5, live=live)
    except D.DataError:
        raise D.DataError("no NSE data for %s: check the symbol (as on NSE, e.g. HDFCBANK, M&M)" % sym)
    if day not in days:
        prior = [d for d in days if d < day]
        if live and prior:
            day = max(prior)
            note = "No session yet today: showing the last session, %s." % day
        else:
            raise D.DataError("no NSE session for %s on %s" % (sym, day))
    else:
        note = ""
    ws = _wsfix(_ws(q))
    full = days[day]
    idx = D.bars(D.INDEX, 5, live=live)
    if ws:
        days, idx = E.shift(days, ws), E.shift(idx, ws)
        p = dict(p, basis="open", cutoff=E._shift_clock(p["cutoff"], ws), flat_by=E._shift_clock(p["flat_by"], ws))
        if day not in days or not E.first_hour(days[day], 5):
            raise D.DataError("the %s hour of %s is not complete yet: the levels come at %s IST" % (
                E.window_label(ws), day, E.window_label(ws).split(" to ")[1]))
    if not E.first_hour(days[day], 5):
        raise D.DataError("the first hour of %s is not complete yet: the levels come at 10:15 AM IST" % day)
    dl = D.daily([sym, D.INDEX])
    fh_i = E.first_hour(idx.get(day) or [], 5)
    pc_i = E.prev_close(dl.get(D.INDEX) or {}, day)
    if p.get("basis") == "open":
        ichg = 100.0 * (fh_i[-1]["c"] / fh_i[0]["o"] - 1) if fh_i else 0.0
    else:
        ichg = 100.0 * (fh_i[-1]["c"] / pc_i - 1) if fh_i and pc_i else 0.0
    f = E.features(sym, days, dl.get(sym) or {}, day, 5, ichg, p.get("basis", "prev_close"))
    if not f:
        raise D.DataError("not enough data for %s (needs yesterday's close and today's first hour)" % sym)
    side = (q.get("side") or ["auto"])[0]
    if side in ("buy", "sell"):
        f["side"] = side
    pl = E.plan_for(f, p)
    s = 1 if f["side"] == "buy" else -1
    bars = days[day]
    last = bars[-1]["c"]
    hour = "%s to %s" % tuple(E.window_label(ws).split(" to ")) if ws else "9:15 to 10:15"
    o = E.simulate(bars, pl, 5, live=live and day.isoformat() == st["date"])
    entry = o["entry"] if o.get("entered") else pl["trigger"]
    risk = abs(entry - pl["stop"])
    target = E.round_tick(entry + s * p["r"] * risk, "nearest") if risk > 0 else None
    want = p["profit_goal"] / (p["r"] * risk) if risk > 0 else 0
    qty = E.size(entry, pl["stop"], p)
    capped = int(want) > qty
    ch = E.charges(qty, *((entry, target) if s > 0 else (target, entry))) if qty and target else 0
    res = {"symbol": sym, "date": day.isoformat(), "note": note, "side": f["side"],
           "side_from": "your choice" if side in ("buy", "sell") else ("%s %.2f%% in the first hour (9:15 to 10:15)" % (
               "up" if s > 0 else "down", abs(f["move_pct"])) if not ws else "%s %.2f%% from %s" % (
               "up" if s > 0 else "down", abs(f["move_pct"]), hour)),
           "change_pct": f["change_pct"], "score": f["score"], "why": f["why"], "fh_high": f["fh_high"], "fh_low": f["fh_low"],
           "last": round(last, 2), "trigger": pl["trigger"], "entry": round(entry, 2), "entered": bool(o.get("entered")),
           "entry_time": o.get("entry_time"), "stop": pl["stop"], "target": target, "risk_per_share": round(risk, 2),
           "qty": qty, "qty_wanted": int(want), "capped": capped, "profit_at_target": round(qty * abs(target - entry), 2) if target else 0,
           "loss_at_stop": round(qty * risk, 2), "charges": ch, "params": p, "outcome": o, "bars": _bars_out(full),
           "window": ws, "window_label": E.window_label(ws) if ws else "9:15 AM to 10:15 AM",
           "move_pct": f["move_pct"],
           "fits_rules": (f["score"] >= 80 and 1.0 <= abs(f["change_pct"]) <= 3.0 and (f["change_pct"] > 0) == (s > 0)) if ws else
                         f["score"] >= p["min_score"] and p["min_move"] <= abs(f["move_pct"]) <= p["max_move"]}
    if live and day.isoformat() == st["date"]:
        lp = D.ltp(sym)
        if lp:
            res["ltp"] = lp
            res["last"] = lp
            if o.get("open"):
                o["last_price"] = lp
                o["last_time"] = "live"
                o["r_now"] = round(s * (lp - o["entry"]) / risk, 2) if risk else 0
    return res


def _run(kind, q):
    try:
        res = (analyse if kind == "analyse" else backtest)(q)
        _set(state="done", result=json.loads(json.dumps(res, default=str)), msg="Done")
    except D.DataError as e:
        _set(state="error", error=str(e))
    except Exception as e:
        traceback.print_exc()
        _set(state="error", error="%s: %s" % (type(e).__name__, e))


def password():
    """MONEY_PASSWORD (cloud) or the PIN set by --phone. Empty = no password (laptop only)."""
    return os.environ.get("MONEY_PASSWORD") or PIN.get("pin") or ""


PIN = {}


def _authorized(handler):
    pw = password()
    if not pw:
        return True
    if handler.client_address[0] in ("127.0.0.1", "::1") and not os.environ.get("MONEY_PASSWORD"):
        return True                      # the laptop itself never needs the phone PIN
    import base64
    import hmac
    h = handler.headers.get("Authorization") or ""
    if h.startswith("Basic "):
        try:
            given = base64.b64decode(h[6:]).decode("utf-8", "replace").split(":", 1)[-1]
        except ValueError:
            given = ""
        if hmac.compare_digest(given.encode(), pw.encode()):
            return True
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="Money", charset="UTF-8"')
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return False


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/healthz":           # for the keep-awake ping on the cloud: no data, no password
            return self._json({"ok": True})
        if not _authorized(self):
            return
        if u.path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/status":
            self._json(dict(D.market_status(), source=D.source()))
        elif u.path == "/api/source":
            want = (q.get("set") or [""])[0].lower()
            if want in ("upstox", "yahoo"):
                D.save_settings(source=want)
            self._json({"source": D.source()})
        elif u.path == "/api/token":
            if not D.has_token():
                return self._json({"set": False})
            p = D.ltp("^NSEI")
            self._json({"set": True, "ok": p is not None, "nifty": p})
        elif u.path == "/api/source/test":
            import time as _t
            t0 = _t.time()
            try:
                days = D.upstox_bars("^NSEI", 5, live=True)
                last = max(days)
                b = days[last][-1]
                self._json({"ok": True, "source": "upstox", "day": str(last), "bars": len(days[last]),
                            "last": "%s %s" % (b["t"].strftime("%H:%M"), b["c"]), "ms": int((_t.time() - t0) * 1000)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200]})
        elif u.path in ("/api/analyse", "/api/backtest"):
            kind = u.path.rsplit("/", 1)[1]
            with LOCK:
                if JOB["state"] == "running":
                    return self._json({"error": "a job is already running"}, 409)
                JOB.update(state="running", done=0, total=0, msg="Starting", result=None, error=None, kind=kind)
            threading.Thread(target=_run, args=(kind, q), daemon=True).start()
            self._json({"started": kind})
        elif u.path == "/api/calc":
            try:
                self._json(json.loads(json.dumps(calc(q), default=str)))
            except D.DataError as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:
                traceback.print_exc()
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif u.path == "/api/job":
            with LOCK:
                self._json(dict(JOB))
        else:
            self._json({"error": "not found"}, 404)


def _do_post(self):
    if not _authorized(self):
        return
    u = urllib.parse.urlparse(self.path)
    if u.path != "/api/token":
        return self._json({"error": "not found"}, 404)
    try:
        n = int(self.headers.get("Content-Length") or 0)
        tok = (json.loads(self.rfile.read(n) or b"{}").get("token") or "").strip()
    except ValueError:
        return self._json({"error": "bad request"}, 400)
    s = D.settings()
    if tok:
        s["upstox_token"] = tok
    else:
        s.pop("upstox_token", None)
    with open(D.SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2)
    try:
        os.chmod(D.SETTINGS, 0o600)
    except OSError:
        pass
    if not tok:
        return self._json({"set": False})
    p = D.ltp("^NSEI")
    self._json({"set": True, "ok": p is not None, "nifty": p})


H.do_POST = _do_post


def serve(port=8050, host="127.0.0.1"):
    srv = ThreadingHTTPServer((host, port), H)
    return srv
