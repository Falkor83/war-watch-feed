"""
War Watch facts: hard numbers and official notices, with no Claude involved (cost $0).

- prices: this week's West Coast and US regular gas price and Brent crude, against 4 weeks earlier (EIA data, read
  from FRED's public CSV download; no key).
- chokepoints: daily ship transits through Hormuz, Bab el-Mandeb, Suez, the Bosporus and the Taiwan Strait, as a
  7-day average against the 2025 average (IMF PortWatch's public ArcGIS service; no key; it lags a few days).
- homeland: official US notices tied to Iran or its proxies: DOJ and FBI cases, DHS NTAS bulletins, State Department
  advisories. Each is shown word for word with its own link, kept for a year, never rewritten.

Every source is optional: one that fails keeps its last good value, labelled with its own date, and the log says so.
The app (0.6.0) shows them on the US impact tab. Standard library only.
"""
import csv, datetime, io, json, re, time, urllib.parse
import wwscore

UA = "WarWatchBot/0.6 (+https://github.com/Falkor83/war-watch-feed)"

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
PRICE_SERIES = [("gas_west", "GASREGWCW", "Gas, West Coast (regular, $/gal)"),
                ("gas_us", "GASREGW", "Gas, US average (regular, $/gal)"),
                ("brent", "DCOILBRENTEU", "Brent crude ($/barrel)")]

PORTWATCH = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/"
             "FeatureServer/0/query")
CHOKEPOINTS = [("hormuz", "Hormuz", "Strait of Hormuz", ["us-israel-iran-war"]),
               ("bab-el-mandeb", "Mandeb", "Bab el-Mandeb (Red Sea)", ["yemen-houthi-saudi-war"]),
               ("suez", "Suez", "Suez Canal", ["yemen-houthi-saudi-war"]),
               ("bosporus", "Bosporus", "Bosporus (Black Sea)", ["russia-ukraine-war", "black-sea-shipping"]),
               ("taiwan", "Taiwan", "Taiwan Strait", ["china-taiwan-pressure"])]

# Iran and the groups it arms. Only notices that name one of them count.
IRAN = re.compile(r"\b(Iran\w*|IRGC|Revolutionary Guard\w*|Quds Force|MOIS|Hezbollah|Hizballah|Hizbollah|Kata.?ib|"
                  r"Houthis?|Ansarallah|Ansar Allah|Tehran)\b", re.I)
OFFICIAL = {"justice.gov": "Justice Department", "fbi.gov": "FBI", "dhs.gov": "Homeland Security",
            "state.gov": "State Department", "treasury.gov": "Treasury", "cisa.gov": "CISA"}
HOMELAND_SEARCHES = [
    ("case", '(site:justice.gov OR site:fbi.gov) (Iran OR IRGC OR "Revolutionary Guard" OR Hezbollah OR Hizballah OR "Kata\'ib" OR Houthi)'),
    ("bulletin", 'site:dhs.gov ("National Terrorism Advisory System" OR NTAS) bulletin'),
    ("advisory", 'site:state.gov ("Worldwide Caution" OR "Level 4") (Iran OR Iraq OR Israel OR Lebanon OR Yemen OR "Middle East")'),
]
HOMELAND_FEEDS = [("case", "https://www.justice.gov/news/rss?type=press_release&search_api_language=en&require_all=0"),
                  ("case", "https://www.fbi.gov/feeds/national-press-releases/rss.xml")]
CASE_WORDS = re.compile(r"\b(charged|indicted|indictment|arrested|convicted|sentenced|pleads?|pleaded|guilty|complaint|"
                        r"plot|scheme|material support|murder.for.hire|seized|forfeiture)\b", re.I)
KEEP_DAYS, MAX_ITEMS = 365, 40

def _get(url, timeout=30, accept="*/*"):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(6_000_000)

# ---------- prices ----------
def parse_fred(text):
    """[(date, value)] from FRED's CSV, skipping '.' (no value)."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0]): continue
        try: out.append((row[0], float(row[1])))
        except ValueError: continue
    return out

def price_entry(key, label, series, weeks_back=4):
    if len(series) < 2: return None
    d, v = series[-1]
    then = (datetime.date.fromisoformat(d) - datetime.timedelta(days=7 * weeks_back)).isoformat()
    prev = [x for x in series if x[0] <= then]
    pd_, pv = prev[-1] if prev else series[0]
    return {"id": key, "label": label, "value": round(v, 3), "date": d, "then": round(pv, 3), "then_date": pd_,
            "change_pct": round(100 * (v - pv) / pv, 1) if pv else None,
            "series": [[x[0], round(x[1], 3)] for x in series[-13:]] if key != "brent" else
                      [[x[0], round(x[1], 2)] for x in series[-60:][::5]]}

def prices(get=_get, log=print):
    out = []
    for key, sid, label in PRICE_SERIES:
        try:
            s = parse_fred(get(FRED + sid).decode("utf-8", "replace"))
            e = price_entry(key, label, s)
            if e: out.append(e)
            else: log(f"[WARN] prices: no values for {sid}")
        except Exception as ex: log(f"[WARN] prices: {sid} failed ({type(ex).__name__})")
    return out

# ---------- chokepoints ----------
def portwatch_url(like, year_from):
    q = {"where": f"portname LIKE '%{like}%' AND year >= {year_from}", "outFields": "year,month,day,date,portname,n_total,n_tanker",
         "orderByFields": "year,month,day", "resultRecordCount": "2000", "f": "json"}
    return PORTWATCH + "?" + urllib.parse.urlencode(q)

def parse_portwatch(data):
    """[(date, total, tankers)] from a PortWatch query answer."""
    rows = []
    for f in (data.get("features") or []):
        a = f.get("attributes") or {}
        try:
            if a.get("year") and a.get("month") and a.get("day"):
                d = datetime.date(int(a["year"]), int(a["month"]), int(a["day"])).isoformat()
            else:
                d = datetime.datetime.fromtimestamp(int(a["date"]) / 1000, datetime.timezone.utc).date().isoformat()
            rows.append((d, float(a.get("n_total") or 0), float(a.get("n_tanker") or 0)))
        except (TypeError, ValueError, KeyError): continue
    rows.sort()
    return rows

def chokepoint_entry(cid, name, hotspots, rows):
    base = [r for r in rows if r[0].startswith("2025-")]
    if len(rows) < 7 or len(base) < 60: return None
    last7 = rows[-7:]
    avg7, avg_base = sum(r[1] for r in last7) / 7, sum(r[1] for r in base) / len(base)
    tank7, tank_base = sum(r[2] for r in last7) / 7, sum(r[2] for r in base) / len(base)
    pct = round(100 * avg7 / avg_base) if avg_base else None
    level = "normal" if pct is None or pct >= 80 else "reduced" if pct >= 40 else "severe"
    return {"id": cid, "name": name, "hotspots": hotspots, "through": rows[-1][0], "avg7": round(avg7, 1),
            "avg_2025": round(avg_base, 1), "pct_of_normal": pct, "tankers7": round(tank7, 1),
            "tankers_2025": round(tank_base, 1), "level": level,
            "series": [[r[0], int(r[1])] for r in rows[-60:]]}

def chokepoints(get=_get, log=print, sleep=time.sleep):
    out = []
    for cid, like, name, hs in CHOKEPOINTS:
        try:
            data = json.loads(get(portwatch_url(like, 2025), accept="application/json").decode("utf-8", "replace"))
            if data.get("error"): raise ValueError(str(data["error"])[:80])
            e = chokepoint_entry(cid, name, hs, parse_portwatch(data))
            if e: out.append(e)
            else: log(f"[WARN] chokepoints: too little data for {name}")
        except Exception as ex: log(f"[WARN] chokepoints: {name} failed ({type(ex).__name__}: {str(ex)[:80]})")
        sleep(1)
    return out

# ---------- homeland ----------
def official_source(url, source_url=""):
    for u in (source_url, url):
        host = urllib.parse.urlparse(u or "").netloc.lower()
        for dom, name in OFFICIAL.items():
            if host == dom or host.endswith("." + dom): return name
    return ""

def homeland_items(get=_get, log=print, parse_feed=None, parse_date=None, sleep=time.sleep):
    """New official notices about Iran and its proxies: [{title, url, date, source, kind}]."""
    if parse_feed is None or parse_date is None:
        import headlines
        parse_feed, parse_date = parse_feed or headlines.parse_feed, parse_date or headlines.parse_date
    got = []
    for kind, url in HOMELAND_FEEDS:
        try:
            for it in parse_feed(get(url)):
                got.append((kind, it, official_source(it["link"])))
        except Exception as ex: log(f"[WARN] homeland: {urllib.parse.urlparse(url).netloc} feed failed ({type(ex).__name__})")
    for kind, q in HOMELAND_SEARCHES:
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:1y") + "&hl=en-US&gl=US&ceid=US:en"
        try:
            for it in parse_feed(get(url)):
                got.append((kind, it, official_source(it["link"], it.get("source_url", ""))))
        except Exception as ex: log(f"[WARN] homeland: search '{kind}' failed ({type(ex).__name__})")
        sleep(2)
    out = []
    for kind, it, src in got:
        title = re.sub(r"\s+-\s+(U\.S\.\s+)?(Department of Justice|Federal Bureau of Investigation|FBI|Homeland Security|"
                       r"DHS|U\.S\. Department of State|State Department|Travel\.State\.gov|\(\.gov\)).*$", "", it["title"]).strip()
        if not src or not IRAN.search(title) and kind == "case": continue
        if kind == "case" and not CASE_WORDS.search(title): continue
        if kind == "bulletin" and not re.search(r"NTAS|Terrorism Advisory", title, re.I): continue
        ts = parse_date(it.get("date"))
        if not ts: continue
        out.append({"title": wwscore.clip(title, 220), "url": it["link"], "source": src, "kind": kind,
                    "date": datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()})
    return out

def merge_homeland(old, new, today):
    """Keep a year of notices, newest first, one per title."""
    seen, out = set(), []
    for x in sorted((old or []) + new, key=lambda x: x["date"], reverse=True):
        k = re.sub(r"\W+", " ", x["title"].lower()).strip()[:90]
        if k in seen or wwscore.days_between(x["date"], today) > KEEP_DAYS: continue
        seen.add(k); out.append(x)
    return out[:MAX_ITEMS]

def homeland_summary(items, today):
    cases = [x for x in items if x["kind"] == "case"]
    year = [x for x in cases if wwscore.days_between(x["date"], today) <= 365]
    month = [x for x in cases if wwscore.days_between(x["date"], today) <= 30]
    bul = next((x for x in items if x["kind"] == "bulletin"), None)
    line = (f"{len(year)} Justice Department or FBI announcement{'s' if len(year) != 1 else ''} about cases tied to Iran or "
            f"its proxies in the last 12 months ({len(month)} in the last 30 days).")
    if bul: line += f" Latest terrorism advisory (DHS): {bul['date']}."
    return line

# ---------- stage ----------
def stage(out, today, log=print, get=_get, sleep=time.sleep, parse_feed=None, parse_date=None):
    """Adds prices, chokepoints and homeland to the feed; a source that fails keeps its last value."""
    old = {"prices": out.get("prices") or {}, "chokepoints": out.get("chokepoints") or {}, "homeland": out.get("homeland") or {}}
    p = prices(get, log)
    if p: out["prices"] = {"date": today, "items": p, "source": "U.S. Energy Information Administration, via FRED"}
    elif old["prices"]: log("[WARN] prices: none came back; the last ones are kept")
    c = chokepoints(get, log, sleep)
    if c: out["chokepoints"] = {"date": today, "items": c, "source": "IMF PortWatch (satellite ship tracking; lags a few days)"}
    elif old["chokepoints"]: log("[WARN] chokepoints: none came back; the last ones are kept")
    try: new = homeland_items(get, log, parse_feed, parse_date, sleep)
    except Exception as ex: new = []; log(f"[WARN] homeland failed ({type(ex).__name__})")
    items = merge_homeland((old["homeland"] or {}).get("items"), new, today)
    if items or old["homeland"]:
        out["homeland"] = {"date": today, "items": items, "line": homeland_summary(items, today),
                           "note": "Official US notices only, shown as published. Counts are announcements, not separate cases."}
    log(f"[OK] facts: {len(p)} prices, {len(c)} chokepoints, {len(new)} new official notices ({len(items)} kept)")
    return out
