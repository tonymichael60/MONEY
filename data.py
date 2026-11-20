"""Money data: free Yahoo Finance charts and NSE's own index lists, cached on disk.

Nothing to install: standard-library Python only.
"""
import csv
import datetime
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import engine as E

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
CACHE = os.environ.get("MONEY_CACHE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
INDEX, VIX = "^NSEI", "^INDIAVIX"
LISTS = {
    "nifty50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "nifty100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
    "nifty200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "nifty500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "fno": "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",   # stocks with futures & options
}
RANGES = {5: ("60d", "5m"), 60: ("2y", "60m")}

# NSE equity holidays (weekdays) for 2026, from NSE's circular as listed by brokers.
# NSE publishes the 2027 list in December: add it here then.
HOLIDAYS = {
    "2026-01-15": "Maharashtra municipal elections", "2026-01-26": "Republic Day", "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami", "2026-03-31": "Shri Mahavir Jayanti", "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day", "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram", "2026-09-14": "Ganesh Chaturthi", "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra", "2026-11-10": "Diwali Balipratipada", "2026-11-24": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}


class DataError(Exception):
    pass


# ---------------------------------------------------------------- data source: Upstox API (default) or Yahoo

UPSTOX = "https://api.upstox.com"
UPSTOX_INSTRUMENTS = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
UPSTOX_INDEX = {"^NSEI": "NSE_INDEX|Nifty 50", "^INDIAVIX": "NSE_INDEX|India VIX"}
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
STATS = {"upstox": 0, "yahoo": 0, "fallback": []}


def settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def save_settings(**kw):
    s = settings()
    s.update({k: v for k, v in kw.items() if v is not None})
    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2)
    return s


def source():
    """'upstox' (default: Upstox's free candle API, no token needed) or 'yahoo'."""
    return (os.environ.get("MONEY_SOURCE") or settings().get("source") or "upstox").lower()


def _upstox_get(path):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    tok = settings().get("upstox_token") or os.environ.get("UPSTOX_TOKEN")
    if tok:
        headers["Authorization"] = "Bearer " + tok
    last = None
    for k in range(3):
        try:
            req = urllib.request.Request(UPSTOX + path, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                doc = json.loads(r.read().decode("utf-8", "replace"))
            if doc.get("status") != "success":
                raise DataError("Upstox: %s" % (doc.get("errors") or doc)[:200])
            return doc.get("data") or {}
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            last = "Upstox HTTP %d %s" % (e.code, body)
            if e.code == 401 and tok:          # a bad token must not block the free candles
                headers.pop("Authorization", None)
                tok = None
                continue
            if e.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = "Upstox: %s" % getattr(e, "reason", e)
        time.sleep(1.0 * (k + 1))
    raise DataError(last or "Upstox: no answer")


def has_token():
    return bool(settings().get("upstox_token") or os.environ.get("UPSTOX_TOKEN"))


def ltp(sym):
    """Last traded price from Upstox (needs the free Analytics Token). None when no token or no answer."""
    if not has_token():
        return None
    try:
        data = _upstox_get("/v3/market-quote/ltp?instrument_key=" + _ukey(sym))
    except DataError:
        return None
    for v in (data or {}).values():
        if isinstance(v, dict) and v.get("last_price") is not None:
            return float(v["last_price"])
    return None


def upstox_keys():
    """NSE symbol -> Upstox instrument key (NSE_EQ|ISIN), from Upstox's instrument file, cached a day."""
    import gzip
    p = os.path.join(CACHE, "upstox-NSE.json")
    if not (os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400):
        os.makedirs(CACHE, exist_ok=True)
        req = urllib.request.Request(UPSTOX_INSTRUMENTS, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                rows = json.loads(gzip.decompress(r.read()).decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            if not os.path.exists(p):
                raise DataError("could not load Upstox's instrument list: %s" % getattr(e, "reason", e))
            rows = None
        if rows is not None:
            keys = {x["trading_symbol"]: x["instrument_key"] for x in rows
                    if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"}
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(keys, fh)
    with open(p, encoding="utf-8") as fh:
        keys = json.load(fh)
    keys.update(UPSTOX_INDEX)
    return keys


def _ukey(sym):
    k = upstox_keys().get(sym.upper().replace(".NS", ""), UPSTOX_INDEX.get(sym))
    if not k:
        raise DataError("Upstox has no NSE instrument for %s" % sym)
    return urllib.parse.quote(k, safe="")


def _candles_to_days(candles, bar_min):
    days = {}
    for c in candles:
        ts = c[0]
        lt = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")      # already IST (+05:30)
        m = E.hm(lt.time()) - E.hm(E.OPEN)
        if m < 0 or m % bar_min or m >= 375 or None in c[1:5]:
            continue
        days.setdefault(lt.date(), []).append({"t": lt, "m": m, "o": c[1], "h": c[2], "l": c[3], "c": c[4], "v": c[5] or 0})
    for d in days:
        days[d].sort(key=lambda b: b["m"])
    return days


def upstox_bars(sym, bar_min, live=False):
    """5-minute (last ~60 days) or 1-hour (last ~2 years) candles from Upstox, plus today's intraday candles."""
    unit, iv, span, chunk = ("minutes", "5", 62, 28) if bar_min == 5 else ("hours", "1", 735, 85)
    key = _ukey(sym)
    today = now_ist().date()
    p = os.path.join(CACHE, "upx-%s.%s%s.json" % (fname(sym), iv, unit[0]))
    hist = None
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < (6 * 3600 if bar_min == 5 else 12 * 3600):
        with open(p, encoding="utf-8") as fh:
            hist = json.load(fh)
    if hist is None:
        rows = {}
        end = today + datetime.timedelta(days=1)
        # short request first: Upstox can leave the latest session out of long ranges
        ranges = [(end, today - datetime.timedelta(days=4))]
        to = today - datetime.timedelta(days=4)
        while (today - to).days < span:
            frm = to - datetime.timedelta(days=chunk)
            ranges.append((to, frm))
            to = frm - datetime.timedelta(days=1)
        for to_d, frm_d in ranges:
            try:
                data = _upstox_get("/v3/historical-candle/%s/%s/%s/%s/%s" % (key, unit, iv, to_d.isoformat(), frm_d.isoformat()))
            except DataError:
                if not rows:
                    raise
                break                                   # older history not available: keep what we have
            for c in data.get("candles") or []:
                rows[c[0]] = c
            time.sleep(0.12)
        hist = sorted(rows.values())
        os.makedirs(CACHE, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(hist, fh)
    candles = list(hist)
    st = market_status()
    if st["trading_day"] and st["phase"] not in ("pre-open",):
        ip = os.path.join(CACHE, "upx-%s.%s%s-today.json" % (fname(sym), iv, unit[0]))
        fresh = os.path.exists(ip) and time.time() - os.path.getmtime(ip) < (45 if st["phase"] in ("first hour", "open") else 3600)
        if fresh:
            with open(ip, encoding="utf-8") as fh:
                today_c = json.load(fh)
        else:
            try:
                today_c = _upstox_get("/v3/historical-candle/intraday/%s/%s/%s" % (key, unit, iv)).get("candles") or []
                with open(ip, "w", encoding="utf-8") as fh:
                    json.dump(today_c, fh)
            except DataError:
                today_c = []
        have = {c[0] for c in candles}
        candles += [c for c in today_c if c[0] not in have]
    STATS["upstox"] += 1
    return _candles_to_days(candles, bar_min)


def upstox_daily(sym):
    p = os.path.join(CACHE, "upx-%s.daily.json" % fname(sym))
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 3 * 3600:
        with open(p, encoding="utf-8") as fh:
            rows = json.load(fh)
    else:
        today = now_ist().date()
        data = _upstox_get("/v3/historical-candle/%s/days/1/%s/%s" % (
            _ukey(sym), (today + datetime.timedelta(days=1)).isoformat(), (today - datetime.timedelta(days=740)).isoformat()))
        rows = data.get("candles") or []
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
    return {datetime.date.fromisoformat(c[0][:10]): c[4] for c in rows if c[4] is not None}


def now_ist():
    return datetime.datetime.utcnow() + E.IST


def market_status(now=None):
    now = now or now_ist()
    d = now.date()
    t = now.time()
    trading = d.weekday() < 5 and d.isoformat() not in HOLIDAYS
    if not trading:
        phase = "closed (%s)" % ("weekend" if d.weekday() >= 5 else HOLIDAYS[d.isoformat()])
    elif t < datetime.time(9, 15):
        phase = "pre-open"
    elif t < datetime.time(10, 15):
        phase = "first hour"
    elif t < datetime.time(15, 30):
        phase = "open"
    else:
        phase = "closed for the day"
    nd = d if (trading and t < datetime.time(9, 15)) else d + datetime.timedelta(days=1)
    while nd.weekday() >= 5 or nd.isoformat() in HOLIDAYS:
        nd += datetime.timedelta(days=1)
    return {"now": now.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(), "trading_day": trading, "phase": phase,
            "first_hour_done": trading and t >= datetime.time(10, 15), "next_session": nd.isoformat(),
            "holiday_list_years": [2026], "calendar_known": d.year == 2026}


def _get(url, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code == 404:
                break
        except (urllib.error.URLError, OSError) as e:
            last = str(getattr(e, "reason", e))
        time.sleep(1.5 * (k + 1))
    raise DataError("%s for %s" % (last, url.split("?")[0]))


def yahoo(path):
    try:
        return _get("https://query2.finance.yahoo.com" + path, tries=2)
    except DataError:
        return _get("https://query1.finance.yahoo.com" + path, tries=2)


def _cached(name, max_age, fetch):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age:
        with open(p, encoding="utf-8") as fh:
            return fh.read()
    try:
        text = fetch()
    except DataError:
        if os.path.exists(p):                      # offline or rate-limited: use the last copy
            with open(p, encoding="utf-8") as fh:
                return fh.read()
        raise
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def ysym(sym):
    return sym if sym.startswith("^") or sym.endswith((".NS", ".BO")) else sym.upper() + ".NS"


def fname(sym):
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in ysym(sym))


def universe(name):
    key = name.lower().replace(" ", "")
    if key not in LISTS:
        raise DataError("choose one of %s" % ", ".join(LISTS))
    text = _cached("list-%s.csv" % key, 86400, lambda: _get(LISTS[key]))
    if key == "fno":
        return _fno(text)
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip().upper(): (v or "").strip() for k, v in r.items()}
        if r.get("SYMBOL") and r.get("SERIES", "EQ") == "EQ":
            out.append({"symbol": r["SYMBOL"], "name": r.get("COMPANY NAME") or r.get("NAME OF COMPANY") or "",
                        "industry": r.get("INDUSTRY", "")})
    return out


def _fno(text):
    """NSE's F&O lot-size file: index rows first, then 'Derivatives on Individual Securities' and one row a stock."""
    out, stocks = [], False
    for row in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in row]
        if len(cells) < 3:
            continue
        if cells[0].lower().startswith("derivatives on individual securities"):
            stocks = True
            continue
        if stocks and cells[1] and cells[1].upper() != "SYMBOL":
            lot = next((c for c in cells[2:] if c.isdigit()), "")
            out.append({"symbol": cells[1].upper(), "name": cells[0].title(), "industry": "",
                        "lot": int(lot) if lot else None})
    if not out:
        raise DataError("could not read NSE's F&O list")
    return out


def bars(sym, bar_min, live=False):
    if bar_min == 60:
        # 12-month backtests only: Yahoo has 2 years of 1-hour candles in one request (Upstox needs 9); Upstox as fallback
        try:
            STATS["yahoo"] += 1
            return yahoo_bars(sym, bar_min, live=live)
        except DataError:
            STATS["yahoo"] -= 1
    if source() == "upstox":
        try:
            days = upstox_bars(sym, bar_min, live=live)
            if days:
                return days
        except DataError as e:
            STATS["fallback"].append("%s (%s)" % (sym, str(e)[:80]))
    STATS["yahoo"] += 1
    return yahoo_bars(sym, bar_min, live=live)


def yahoo_bars(sym, bar_min, live=False):
    rng, iv = RANGES[bar_min]
    st = market_status()
    age = 75 if (live and st["phase"] in ("first hour", "open")) else (3 * 3600 if bar_min == 5 else 12 * 3600)
    text = _cached("%s.%s.json" % (fname(sym), iv), age,
                   lambda: yahoo("/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(ysym(sym), safe=".-^"), rng, iv)))
    try:
        return E.parse_chart(json.loads(text), bar_min)
    except (ValueError, KeyError, IndexError, TypeError):
        raise DataError("no %s data for %s" % (iv, sym))


def daily(symbols):
    if source() == "upstox":
        out, missing = {}, []
        for s in symbols:
            try:
                out[s] = upstox_daily(s)
            except DataError:
                missing.append(s)
        if missing:
            out.update(yahoo_daily(missing))
        return out
    return yahoo_daily(symbols)


def yahoo_daily(symbols):
    out = {}
    for i in range(0, len(symbols), 20):
        batch = symbols[i:i + 20]
        name = "daily-%s.json" % "-".join(fname(s) for s in batch)[:120]
        text = _cached(name.replace("^", "_") if False else name, 3 * 3600,
                       lambda b=batch: yahoo("/v7/finance/spark?symbols=%s&range=2y&interval=1d"
                                             % ",".join(urllib.parse.quote(ysym(s), safe=".-^") for s in b)))
        back = {ysym(s).upper(): s for s in batch}
        for it in (json.loads(text).get("spark") or {}).get("result") or []:
            r = (it.get("response") or [None])[0]
            s = back.get(str(it.get("symbol")).upper())
            if r and s:
                out[s] = E.parse_daily(r)
        time.sleep(0.2)
    return out


def load(list_name, bar_min, progress=None, live=False, symbols=None):
    STATS.update(upstox=0, yahoo=0, fallback=[])
    names = universe(list_name) if not symbols else [{"symbol": s, "name": ""} for s in symbols]
    syms = [x["symbol"] for x in names]
    data, failed = {}, []
    for k, s in enumerate(syms):
        if progress:
            progress(k, len(syms) + 3, "Charts: %s" % s)
        try:
            data[s] = bars(s, bar_min, live=live)
        except DataError as e:
            failed.append(s)
    if progress:
        progress(len(syms), len(syms) + 3, "NIFTY 50 and India VIX")
    idx = bars(INDEX, bar_min, live=live)
    try:
        vix = bars(VIX, bar_min, live=live)
    except DataError:
        vix = {}
    if progress:
        progress(len(syms) + 2, len(syms) + 3, "Daily closes")
    dl = daily(syms + [INDEX])
    return {"data": data, "index": idx, "vix": vix, "daily": dl, "failed": failed,
            "source": {"name": source(), "upstox": STATS["upstox"], "yahoo": STATS["yahoo"], "fallback": STATS["fallback"][:5]},
            "names": {x["symbol"]: x["name"] for x in names}}
