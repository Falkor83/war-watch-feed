"""
War Watch "boiling point": could a tense hotspot tip into open war (or a war widen)?

For the hotspots most at risk (picked by rule), Python builds a data panel from free public data:
  - world coverage and tone over 90 days (GDELT, all 65 languages), and how often escalation words (mobilisation,
    evacuation, airspace closures, ultimatums) and talk words (talks, ceasefire, mediation) appear vs the 90-day norm;
  - the last 48 hours of worldwide headlines (translated): who is reporting (countries, regions, languages, the sides'
    own press vs outside), and which kinds of signals they carry;
  - US State Department and UK FCDO travel advice for the countries involved, and recent changes;
  - currency moves vs the US dollar, Brent oil, internet outages (IODA), and betting-market prices where a market exists;
  - the hotspot's warning signs (tripwires) and its rating history.
Opus then reads the panel, the headlines and today's notes and places the hotspot on a 5-step ladder, with drivers,
brakes, what would tip it and the off-ramp. Python checks every citation and number and limits how far the ladder
can move in a day. Standard library only.
"""
import json, re, time, datetime, urllib.parse, urllib.request, urllib.error, csv, io
import wwapi, wwdata, wwscore
from wwapi import ok, warn, step

STAGES = {1: "Words", 2: "Posturing", 3: "Mobilizing", 4: "Brink", 5: "Open war"}
STAGE_HELP = {1: "threats, accusations and rhetoric", 2: "drills, deployments, sanctions, expulsions",
              3: "troop build-ups, reserve call-ups, evacuations, airspace closures, ultimatums",
              4: "clashes between state forces, final deadlines, war footing", 5: "sustained fighting between states"}
LIKELIHOOD = ["almost no chance", "very unlikely", "unlikely", "roughly even chance", "likely", "very likely", "almost certain"]
DIRECTIONS = {"rising", "steady", "easing"}
MAX_BOIL = 6

CURRENCY = {"US": "usd", "RU": "rub", "UA": "uah", "CN": "cny", "TW": "twd", "JP": "jpy", "KR": "krw", "KP": "", "IN": "inr",
            "PK": "pkr", "AF": "afn", "IR": "irr", "IL": "ils", "PS": "ils", "LB": "lbp", "SY": "syp", "IQ": "iqd",
            "YE": "yer", "SA": "sar", "AE": "aed", "QA": "qar", "BH": "bhd", "KW": "kwd", "OM": "omr", "TR": "try",
            "EG": "egp", "JO": "jod", "PL": "pln", "LT": "eur", "LV": "eur", "EE": "eur", "DE": "eur", "FR": "eur",
            "MD": "mdl", "RO": "ron", "BY": "byn", "GE": "gel", "AM": "amd", "AZ": "azn", "PH": "php", "VN": "vnd",
            "MM": "mmk", "TH": "thb", "ET": "etb", "ER": "ern", "SD": "sdg", "SS": "ssp", "SO": "sos", "CD": "cdf",
            "RW": "rwf", "UG": "ugx", "KE": "kes", "NG": "ngn", "CM": "xaf", "TD": "xaf", "CF": "xaf", "NE": "xof",
            "ML": "xof", "BF": "xof", "MZ": "mzn", "VE": "ves", "CU": "cup", "CO": "cop", "MX": "mxn", "EC": "usd",
            "HT": "htg", "GB": "gbp", "DZ": "dzd", "LY": "lyd"}
PEGGED = {"sar", "aed", "qar", "bhd", "kwd", "omr", "jod", "ern", "xaf", "xof", "cup", "hkd", "djf", "irr", "syp", "sdg",
          "ssp", "lbp", "yer", "mmk", "etb", "ves"}           # pegged, managed or official rates far from the street rate

UA = "Mozilla/5.0 (compatible; WarWatchBot/0.3; +https://github.com/Falkor83/war-watch-feed)"

SIGNALS = {
    "mobilization": r"mobili[sz]\w*|reservists?|conscript\w*|call-?ups?|troop build-?up|military build-?up|mass(ing|es) (troops|forces)|deploy\w*",
    "evacuation": r"evacuat\w*|leave immediately|ordered departure|embass\w* (clos|shut|suspend)\w*|citizens to leave|airlift",
    "airspace": r"airspace|no-fly|notam|flights? (suspend|cancel|halt|divert)\w*|airlines? (suspend|cancel|halt|avoid)\w*",
    "threats": r"ultimatum|red lines?|deadline|retaliat\w*|vows? to|warns?|threat\w*|declar\w* war|war footing|martial law",
    "clashes": r"clash\w*|exchange of fire|fired on|shot down|skirmish\w*|incursion\w*|strikes?|struck|attack\w*|shell\w*|killed",
    "talks": r"talks|negotiat\w*|ceasefire|truce|mediat\w*|envoy|summit|de-?escalat\w*|peace (deal|plan|process)|hotline",
    "nuclear": r"nuclear|uranium|warheads?|enrichment|icbm|ballistic",
}
SIGNAL_RE = {k: re.compile(r"\b(" + v + r")", re.I) for k, v in SIGNALS.items()}
ESCALATION_Q = ('(mobilization OR mobilisation OR reservists OR evacuation OR evacuate OR ultimatum OR airspace OR '
                '"troop buildup" OR "martial law" OR "state of emergency" OR "declaration of war")')
TALKS_Q = "(talks OR negotiations OR ceasefire OR mediation OR truce OR envoy OR summit)"

def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(12_000_000)

# ---------- choosing hotspots ----------
def candidates(feed, prev_boil, today, nuclear=(), max_n=MAX_BOIL):
    """Hotspots that could tip into (or widen into) war between states, scored by rule."""
    nuclear = set(nuclear)
    prev = {b["id"]: b for b in prev_boil or []}
    out = []
    for c in feed["conflicts"]:
        parties = {wwdata.iso(p) for p in c.get("parties") or []} - {""}
        s = 0.0
        if c.get("type") == "standoff": s += 3
        elif c.get("interstate") and c["severity"] == "yellow": s += 3
        elif c.get("interstate"): s += 2                       # already fighting: the question is a wider war
        elif len(parties) >= 2: s += 1                         # a civil war with foreign states involved
        s += {"escalating": 2, "steady": 0, "de-escalating": -1}.get(c["trend"], 0)
        if c.get("us_watch"): s += 1
        if parties & nuclear: s += 1
        tws = [t for t in feed.get("tripwires", []) if t["hotspot"] == c["id"] and t["kind"] == "escalation"]
        s += min(2, sum(1 for t in tws if t["status"] == "signs")) + min(4, 2 * sum(1 for t in tws if t["status"] == "tripped"))
        p = prev.get(c["id"])
        if p and p.get("stage", 0) >= 3 and wwscore.days_between(p.get("date", today), today) <= 7: s += 2   # don't drop a hot one overnight
        if s >= 4: out.append((s, c["id"]))
    out.sort(key=lambda x: -x[0])
    return [cid for _, cid in out[:max_n]], {cid: sc for sc, cid in out}

# ---------- GDELT timelines ----------
def gdelt_timeline(query, mode, gap, deadline=None, last=[0.0]):
    """One 90-day GDELT timeline, paced; None when GDELT is busy or time is up (a missing tile, not a failure)."""
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + urllib.parse.quote(query) +
           f"&mode={mode}&format=json&timespan=3m")
    for attempt in range(2):
        if deadline and time.time() > deadline: return None
        wait = last[0] + gap - time.time()
        if wait > 0: time.sleep(wait)
        try:
            t = _get(url, 30).decode("utf-8", errors="replace").strip()
            last[0] = time.time()
            if not t.startswith("{"): raise ValueError(t[:60])
            tl = json.loads(t).get("timeline") or []
            if not tl: return []
            return [(p["date"][:8], float(p["value"])) for p in tl[0].get("data") or []]
        except urllib.error.HTTPError as e:
            last[0] = time.time()
            if e.code == 429 and attempt == 0: time.sleep(20); continue
            return None
        except Exception:
            last[0] = time.time()
            if attempt == 0: time.sleep(6); continue
            return None
    return None

def gdelt_prefetch(queries, gap=12.0, budget_s=900):
    """All hotspots' timelines in passes (coverage for all first, then escalation words, tone, talk words), so a slow
    GDELT leaves every hotspot with its most useful tiles. {id: {kind: series}}."""
    deadline = time.time() + budget_s
    out = {cid: {} for cid in queries}
    passes = [("vol", "timelinevol", ""), ("esc", "timelinevol", ESCALATION_Q), ("tone", "timelinetone", ""), ("talk", "timelinevol", TALKS_Q)]
    for kind, mode, extra in passes:
        for cid, q in queries.items():
            if not q: continue
            r = gdelt_timeline(f"{q} {extra}".strip(), mode, gap, deadline)
            if r is not None: out[cid][kind] = r
    got = sum(len(v) for v in out.values())
    (ok if got else warn)(f"GDELT timelines: {got} of {4 * len([q for q in queries.values() if q])} in {round(time.time() - deadline + budget_s)} s")
    return out

def ratio(series, recent=7):
    """Average of the last `recent` days over the average of the rest (the ~90-day norm)."""
    if not series or len(series) < recent + 14: return None, None
    vals = [v for _, v in series]
    a = sum(vals[-recent:]) / recent
    b = sum(vals[:-recent]) / len(vals[:-recent])
    peak = max(series[-30:], key=lambda x: x[1])[0]
    return (round(a / b, 2) if b > 0 else None), f"{peak[:4]}-{peak[4:6]}-{peak[6:8]}"

def gdelt_panel(series):
    """Ratios and tone from prefetched timelines."""
    vol, tone, esc, talk = series.get("vol"), series.get("tone"), series.get("esc"), series.get("talk")
    out = {}
    if vol:
        r, peak = ratio(vol)
        if r is not None: out["coverage"] = {"ratio": r, "peak": peak, "series": [round(v, 4) for _, v in vol[-90:]]}
    if tone and len(tone) > 21:
        vals = [v for _, v in tone]
        a, b = sum(vals[-7:]) / 7, sum(vals[:-7]) / len(vals[:-7])
        out["tone"] = {"recent": round(a, 2), "norm": round(b, 2), "change": round(a - b, 2)}
    if esc:
        r, peak = ratio(esc)
        if r is not None: out["escalation_words"] = {"ratio": r, "peak": peak}
    if talk:
        r, peak = ratio(talk)
        if r is not None: out["talk_words"] = {"ratio": r, "peak": peak}
    return out

def hotspot_query(c):
    from headlines import gdelt_terms
    terms = gdelt_terms(c)
    return (terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")") if terms else ""

# ---------- worldwide headlines ----------
def hotspot_matcher(c):
    strong = [a for a in c.get("anchors") or [] if len(a) >= 4]
    weak = [x for x in (c.get("context") or []) + (c.get("countries") or []) if len(x) >= 4]
    rs = re.compile(r"(?<!\w)(" + "|".join(re.escape(a) for a in sorted(strong, key=len, reverse=True)) + r")", re.I) if strong else None
    rw = re.compile(r"(?<!\w)(" + "|".join(re.escape(a) for a in sorted(weak, key=len, reverse=True)) + r")", re.I) if weak else None
    sec = re.compile(r"\b(war|military|troops|army|navy|missile|drone|strike|attack|clash|border|nuclear|ceasefire|talks|"
                     r"sanction|drill|exercise|deploy|evacuat|airspace|mobili|threat|warn|tension|escalat|incursion|blockade)", re.I)
    def match(item):
        if c["id"] in (item.get("h") or []): return True
        t = item.get("t", "")
        if rs and rs.search(t): return True
        if rw:
            names = {m.group(1).lower() for m in rw.finditer(t)}
            return len(names) >= 2 or (len(names) == 1 and bool(sec.search(t)))
        return False
    return match

def corpus_panel(c, items, now_ts, hours=48):
    match = hotspot_matcher(c)
    mine = [x for x in items if x.get("p", 0) >= now_ts - hours * 3600 and match(x)]
    parties = {wwdata.iso(p) for p in c.get("parties") or []} - {""}
    regions = {}
    for x in mine:
        r = wwdata.REGION_OF.get(x.get("c", ""), "unknown")
        regions[r] = regions.get(r, 0) + 1
    cats = {k: sum(1 for x in mine if rx.search(x["t"])) for k, rx in SIGNAL_RE.items()}
    party = sum(1 for x in mine if x.get("c") in parties)
    return {"n": len(mine), "outlets": len({x.get("d") or x.get("s") for x in mine}),
            "countries": sorted({x["c"] for x in mine if x.get("c")}), "regions": regions,
            "languages": sorted({x.get("l", "en") for x in mine}), "party_share": round(party / len(mine), 2) if mine else 0,
            "signals": cats}, mine

def pick_headlines(mine, parties, n=24):
    """A spread: newest first, but at most 3 per country and 2 per outlet, signal-bearing ones preferred."""
    def has_sig(x): return any(rx.search(x["t"]) for rx in SIGNAL_RE.values())
    pool = sorted(mine, key=lambda x: (not has_sig(x), -x.get("p", 0)))
    per_c, per_o, out = {}, {}, []
    for x in pool:
        cc, oo = x.get("c", ""), x.get("d") or x.get("s")
        if per_c.get(cc, 0) >= 3 or per_o.get(oo, 0) >= 2: continue
        per_c[cc] = per_c.get(cc, 0) + 1; per_o[oo] = per_o.get(oo, 0) + 1
        out.append(x)
        if len(out) >= n: break
    return out

# ---------- travel advice, money, outages, markets ----------
class Sources:
    """Fetched once per run, shared by all hotspots; every failure is a missing data point, never a crash."""
    def __init__(self, today):
        self.today = today
        self._us = self._uk = self._fx = self._oil = None
        self._uk_index = None

    def us_advisories(self):
        if self._us is None:
            try:
                data = json.loads(_get("https://cadataapi.state.gov/api/TravelAdvisories"))
                self._us = {}
                for a in data:
                    title = str(a.get("Title", ""))
                    m = re.match(r"(.+?)\s*[-–]\s*Level (\d)", title)
                    if not m: continue
                    code = wwdata.iso(m.group(1))
                    date = str(a.get("Updated") or a.get("Published") or a.get("PubDate") or a.get("Date") or "")[:10]
                    if code: self._us[code] = {"level": int(m.group(2)), "title": title[:120], "date": date, "url": a.get("Link", "")}
            except Exception as e:
                warn(f"US travel advisories unavailable: {type(e).__name__}"); self._us = {}
        return self._us

    def uk_advice(self, code):
        if self._uk is None: self._uk = {}
        if code in self._uk: return self._uk[code]
        try:
            if self._uk_index is None:
                idx = json.loads(_get("https://www.gov.uk/api/content/foreign-travel-advice"))
                kids = (idx.get("links") or {}).get("children") or []
                self._uk_index = {wwdata.iso(k.get("title") or (k.get("details") or {}).get("country", {}).get("name", "")): k.get("base_path")
                                  for k in kids}
            path = self._uk_index.get(code)
            if not path: self._uk[code] = None; return None
            d = json.loads(_get("https://www.gov.uk/api/content" + path))
            det = d.get("details") or {}
            self._uk[code] = {"alert": [str(a).replace("_", " ") for a in det.get("alert_status") or []][:3],
                              "change": str(det.get("change_description", ""))[:200],
                              "date": str(d.get("public_updated_at", ""))[:10], "url": "https://www.gov.uk" + path}
        except Exception as e:
            warn(f"UK travel advice for {code} unavailable: {type(e).__name__}"); self._uk[code] = None
        return self._uk[code]

    def fx(self):
        if self._fx is None:
            self._fx = {}
            t = datetime.date.fromisoformat(self.today)
            for label, day in (("now", "latest"), ("d7", (t - datetime.timedelta(days=7)).isoformat()),
                               ("d30", (t - datetime.timedelta(days=30)).isoformat())):
                try:
                    self._fx[label] = json.loads(_get(f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day}/v1/currencies/usd.json"))["usd"]
                except Exception as e:
                    warn(f"exchange rates ({label}) unavailable: {type(e).__name__}")
        return self._fx

    def oil(self):
        if self._oil is None:
            self._oil = {}
            try:
                rows = [r for r in csv.reader(io.StringIO(_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU").decode()))][1:]
                rows = [(d, float(v)) for d, v in rows if v not in ("", ".")]
                last_d, last_v = rows[-1]
                def back(days):
                    target = (datetime.date.fromisoformat(last_d) - datetime.timedelta(days=days)).isoformat()
                    older = [v for d, v in rows if d <= target]
                    return older[-1] if older else None
                v7, v30 = back(7), back(30)
                self._oil = {"price": last_v, "date": last_d, "change_7d": round(100 * (last_v / v7 - 1), 1) if v7 else None,
                             "change_30d": round(100 * (last_v / v30 - 1), 1) if v30 else None}
            except Exception as e:
                warn(f"Brent price unavailable: {type(e).__name__}")
        return self._oil

    def outages(self, code):
        try:
            now = int(time.time())
            d = json.loads(_get(f"https://api.ioda.inetintel.cc.gatech.edu/v2/outages/alerts?entityType=country&entityCode={code}"
                                f"&from={now - 7 * 86400}&until={now}"))
            alerts = d.get("data") or []
            crit = [a for a in alerts if str(a.get("level", "")).lower() == "critical"]
            return {"alerts": len(alerts), "critical": len(crit)}
        except Exception:
            return None

    def markets(self, words):
        """Open betting markets on war questions for this hotspot (a price, not a forecast)."""
        out = []
        try:
            d = json.loads(_get("https://gamma-api.polymarket.com/public-search?" + urllib.parse.urlencode(
                {"q": words, "limit_per_type": 10, "events_status": "active"})))
            for ev in d.get("events") or []:
                for m in ev.get("markets") or []:
                    q = str(m.get("question") or ev.get("title") or "")
                    if m.get("closed") or not re.search(r"\b(war|invade|invasion|clash|strike|attack|military|ceasefire|blockade|"
                                                        r"nuclear|troops|conflict|peace)\b", q, re.I): continue
                    try: prices = json.loads(m.get("outcomePrices") or "[]")
                    except (ValueError, TypeError): prices = m.get("outcomePrices") or []
                    vol = float(m.get("volume") or m.get("volumeNum") or 0)
                    if not prices or vol < 50_000: continue
                    out.append({"question": q[:140], "yes": round(float(prices[0]) * 100), "volume": int(vol),
                                "week_change": round(float(m["oneWeekPriceChange"]) * 100) if m.get("oneWeekPriceChange") not in (None, "") else None,
                                "ends": str(m.get("endDate", ""))[:10],
                                "url": "https://polymarket.com/event/" + str(ev.get("slug", ""))})
        except Exception as e:
            warn(f"betting markets unavailable for {words!r}: {type(e).__name__}")
        out.sort(key=lambda x: -x["volume"])
        return out[:2]

def money_panel(codes, src):
    fx = src.fx()
    out = []
    for code in codes:
        cur = CURRENCY.get(code, "")
        if not cur or cur == "usd" or "now" not in fx or cur not in fx["now"]: continue
        now = fx["now"][cur]
        def ch(lbl):
            old = (fx.get(lbl) or {}).get(cur)
            return round(100 * (now / old - 1), 1) if old else None       # + = it takes more of it to buy a dollar (weaker)
        out.append({"country": code, "currency": cur.upper(), "weaker_7d": ch("d7"), "weaker_30d": ch("d30"), "managed": cur in PEGGED})
    return out

# ---------- the panel ----------
def build_panel(c, items, src, now_ts, series=None, first_id=1):
    """Every data point gets an ID (d1, d2...) and a plain sentence with its numbers, so the model can cite it and the
    numbers check can hold the model to it."""
    codes = [x for x in dict.fromkeys(wwdata.iso(p) for p in (c.get("parties") or []) + (c.get("countries") or [])) if x][:5]
    pts, tiles = [], []
    def add(kind, text, label, value, note="", level=0, **extra):
        did = f"d{first_id + len(pts)}"
        pts.append({"id": did, "kind": kind, "text": text})
        tiles.append({"id": did, "kind": kind, "label": label, "value": value, "note": note, "level": level, **extra})
    def lvl(x, warn_at, alarm_at): return 2 if x >= alarm_at else 1 if x >= warn_at else 0
    g = gdelt_panel(series or {})
    if "coverage" in g:
        cv = g["coverage"]
        add("coverage", f"World coverage in 65 languages (GDELT): the last 7 days ran at {cv['ratio']}x the 90-day norm; busiest day in the last month {cv['peak']}.",
            "World coverage", f"{cv['ratio']}×", f"last 7 days vs the 90-day norm, 65 languages (GDELT); busiest day {cv['peak']}",
            lvl(cv["ratio"], 1.4, 2.0), series=cv["series"])
    if "tone" in g:
        t = g["tone"]
        add("tone", f"Tone of that coverage: {t['recent']} over the last 7 days vs {t['norm']} normally ({'darker' if t['change'] < 0 else 'lighter'} by {abs(t['change'])}; GDELT tone, below 0 is negative).",
            "Tone", f"{t['recent']:+}", f"vs {t['norm']:+} normally: {'darker' if t['change'] < 0 else 'lighter'} by {abs(t['change'])} (below 0 is negative)",
            lvl(-t["change"], 0.8, 1.6))
    if "escalation_words" in g:
        e = g["escalation_words"]
        add("escalation_words", f"Coverage that mentions mobilisation, evacuation, airspace, ultimatums or martial law: {e['ratio']}x its 90-day norm over the last 7 days.",
            "Escalation words", f"{e['ratio']}×", "mobilisation, evacuation, airspace, ultimatums, martial law: last 7 days vs the norm",
            lvl(e["ratio"], 1.4, 2.0))
    if "talk_words" in g:
        e = g["talk_words"]
        add("talk_words", f"Coverage that mentions talks, ceasefire, mediation or envoys: {e['ratio']}x its 90-day norm over the last 7 days.",
            "Talk words", f"{e['ratio']}×", "talks, ceasefire, mediation, envoys: last 7 days vs the norm", 0)
    cp, mine = corpus_panel(c, items, now_ts)
    if cp["n"]:
        regs = ", ".join(f"{wwdata.REGION_NAMES.get(r, r)} {n}" for r, n in sorted(cp["regions"].items(), key=lambda kv: -kv[1])[:8])
        add("who_reports", f"Last 48 hours: {cp['n']} headlines from {cp['outlets']} outlets in {len(cp['countries'])} countries and "
            f"{len(cp['languages'])} languages ({regs}); {round(100 * cp['party_share'])}% from the countries involved.",
            "Who's reporting (48 h)", f"{cp['n']} headlines",
            f"{cp['outlets']} outlets, {len(cp['countries'])} countries, {len(cp['languages'])} languages; {round(100 * cp['party_share'])}% from the sides' own countries",
            1 if cp["party_share"] >= 0.6 else 0,
            parts=[[wwdata.REGION_NAMES.get(r, r), n] for r, n in sorted(cp["regions"].items(), key=lambda kv: -kv[1])[:8]])
        if any(cp["signals"].values()):
            sig = ", ".join(f"{k} {v}" for k, v in cp["signals"].items() if v)
            hot = cp["signals"]["mobilization"] + cp["signals"]["evacuation"] + cp["signals"]["airspace"]
            add("headline_signals", f"Those headlines by kind: {sig}.", "Headline signals (48 h)", f"{sum(cp['signals'].values())}",
                "what the headlines are about (one headline can count twice)", lvl(hot, 3, 8),
                parts=[[k, v] for k, v in cp["signals"].items() if v])
    us = src.us_advisories()
    for code in codes:
        a = us.get(code)
        name = wwdata.country_name(code)
        if a: add("advice_us", f"US travel advice for {name}: Level {a['level']} of 4" + (f", updated {a['date']}." if a.get("date") else "."),
                  f"US travel advice: {name}", f"Level {a['level']} of 4", f"updated {a['date']}" if a.get("date") else "",
                  2 if a["level"] >= 4 else 1 if a["level"] == 3 else 0, url=a.get("url", ""))
        u = src.uk_advice(code)
        if u: add("advice_uk", f"UK travel advice for {name}" + (f" ({'; '.join(u['alert'])})" if u["alert"] else "") +
                  f", updated {u['date']}" + (f": {u['change']}" if u["change"] else "."),
                  f"UK travel advice: {name}", (u["alert"][0].capitalize() if u["alert"] else "No travel warning"),
                  f"updated {u['date']}" + (f": {u['change']}" if u["change"] else ""),
                  2 if any("all travel" in a and "whole" in a for a in u["alert"]) else 1 if u["alert"] else 0, url=u.get("url", ""))
    for m in money_panel(codes, src):
        if m["weaker_7d"] is None: continue
        w7 = m["weaker_7d"]
        add("currency", f"{m['currency']} ({wwdata.country_name(m['country'])}) vs the US dollar: "
            f"{'weaker' if w7 > 0 else 'stronger'} by {abs(w7)}% in 7 days"
            + (f", {abs(m['weaker_30d'])}% {'weaker' if m['weaker_30d'] > 0 else 'stronger'} in 30 days" if m["weaker_30d"] is not None else "")
            + (" (a managed or official rate: moves little even in a crisis)." if m["managed"] else "."),
            f"{m['currency']} vs US dollar", f"{abs(w7)}% {'weaker' if w7 > 0 else 'stronger'}",
            "in 7 days" + (f"; {abs(m['weaker_30d'])}% {'weaker' if m['weaker_30d'] > 0 else 'stronger'} in 30" if m["weaker_30d"] is not None else "")
            + ("; a managed rate, moves little" if m["managed"] else ""),
            0 if m["managed"] else lvl(w7, 3, 8))
    oil = src.oil()
    if oil.get("price") and c.get("region") in ("Middle East", "Europe"):
        add("oil", f"Brent crude ${oil['price']} on {oil['date']}" + (f", {oil['change_7d']:+}% in 7 days" if oil.get("change_7d") is not None else "")
            + (f", {oil['change_30d']:+}% in 30 days." if oil.get("change_30d") is not None else "."),
            "Brent crude", f"${oil['price']}", (f"{oil['change_7d']:+}% in 7 days" if oil.get("change_7d") is not None else "") + f" (price of {oil['date']})",
            lvl(oil.get("change_7d") or 0, 5, 10))
    for code in codes[:3]:
        o = src.outages(code)
        if o and o["alerts"]: add("outages", f"Internet outage alerts in {wwdata.country_name(code)} over 7 days (IODA): {o['alerts']}"
                                  + (f", {o['critical']} critical." if o["critical"] else "."),
                                  f"Internet outages: {wwdata.country_name(code)}", f"{o['alerts']} alert{'s' if o['alerts'] != 1 else ''}",
                                  (f"{o['critical']} critical, " if o["critical"] else "") + "last 7 days (IODA)", 2 if o["critical"] else 1)
    words = c.get("short_name") or c["name"]
    for m in src.markets(re.sub(r"[^\w\s]", " ", words)[:40]):
        add("market", f"Betting market (Polymarket, a price, not a forecast): \"{m['question']}\" trades at {m['yes']}% yes"
            + (f", {m['week_change']:+} points in a week." if m.get("week_change") is not None else "."),
            "Betting market", f"{m['yes']}% yes", m["question"] + (f" ({m['week_change']:+} pts this week)" if m.get("week_change") is not None else "")
            + "; a market price, not a forecast", 0, url=m["url"])
    return pts, tiles, mine

# ---------- the model's part ----------
def boil_request(cands, feed, panels, heads, notes_by_hot, prev, model, system, today):
    blocks = []
    for cid in cands:
        c = next(x for x in feed["conflicts"] if x["id"] == cid)
        tws = [t for t in feed.get("tripwires", []) if t["hotspot"] == cid]
        p = prev.get(cid)
        blocks.append(
            f"### {cid}: {c['name']} ({c['severity']}, {c['trend']}, {c.get('type', '')}; sides: {', '.join(c.get('parties') or [])})\n"
            f"Now: {c.get('status', '')}\n"
            f"Yesterday's ladder: {('stage ' + str(p['stage']) + ' ' + STAGES[p['stage']] + ', ' + p.get('likelihood', '')) if p else 'not rated before'}\n"
            f"Warning signs: " + ("; ".join(f"{t['id']} [{t['kind']}, {t['status']}] {t['text']}" for t in tws) or "none") + "\n"
            f"DATA\n" + "\n".join(f"{d['id']}: {d['text']}" for d in panels[cid]) + "\n"
            f"HEADLINES (last 48 h, translated to English; country of the outlet; * = from a country involved)\n" +
            "\n".join(f"{h['hid']}: [{h.get('c', '?')}{'*' if h['party'] else ''} {h.get('l', 'en')}] {h['t']} ({h.get('s', '')})" for h in heads[cid]) +
            "\nNOTES (today's checked research notes)\n" +
            "\n".join(f"{n['id']}: [{n['home']} {n['tag']}] {n['fact']}" for n in notes_by_hot.get(cid, [])[:10]))
    user = (f"Today is {today}.\n\n" + "\n\n".join(blocks) + "\n\nReturn the JSON described in your instructions.")
    return {"label": "boiling point", "model": model, "system": system, "user": user, "max_uses": 0, "max_tokens": 12000, "estimate": 0.15}

def check(ans, cands, panels, heads, notes_by_hot, prev, today, log):
    """The model's ladder -> what the app shows. Citations must exist; numbers must be in what was cited; the ladder
    moves at most one step a day (two when the warning signs back it)."""
    out = []
    got = {b.get("id"): b for b in (ans.get("boil") if isinstance(ans, dict) else ans) or [] if isinstance(b, dict)}
    for cid in cands:
        b = got.get(cid)
        if not b: log.append(f"{cid}: no answer"); continue
        known = {d["id"]: d["text"] for d in panels[cid]} | {h["hid"]: h["t"] for h in heads[cid]} | \
                {n["id"]: n["fact"] for n in notes_by_hot.get(cid, [])}
        try: stage = max(1, min(5, int(b.get("stage"))))
        except (TypeError, ValueError): log.append(f"{cid}: no stage"); continue
        p = prev.get(cid)
        if p and abs(stage - p["stage"]) > 1:
            cites = set(b.get("stage_cites") or [])
            if not (len(cites & set(known)) >= 2 and stage > p["stage"]):
                log.append(f"{cid}: ladder {p['stage']}->{stage} limited to one step")
                stage = p["stage"] + (1 if stage > p["stage"] else -1)
        lk = str(b.get("likelihood", "")).strip().lower()
        if lk not in LIKELIHOOD: log.append(f"{cid}: likelihood {lk!r} not allowed"); lk = ""
        def facts(ids): return [{"fact": known[i]} for i in ids if i in known]
        def items(key, n):
            res = []
            for it in (b.get(key) or [])[:n]:
                if not isinstance(it, dict): continue
                ids = [i for i in it.get("cites") or [] if i in known]
                text = str(it.get("text", ""))[:260]
                if not ids or wwscore.numbers_supported(text, facts(ids)): log.append(f"{cid}: dropped a {key[:-1]} (no backing)"); continue
                res.append({"text": text, "cites": ids})
            return res
        all_cited = [i for k in ("drivers", "brakes") for it in b.get(k) or [] if isinstance(it, dict) for i in it.get("cites") or [] if i in known]
        summary, cut = wwscore.drop_unsupported_sentences(str(b.get("summary", ""))[:700], facts(list(known)))
        if cut: log.append(f"{cid}: {cut} summary sentence(s) with unbacked numbers removed")
        item = {"id": cid, "date": today, "question": str(b.get("question", ""))[:140], "stage": stage, "stage_label": STAGES[stage],
                "likelihood": lk, "likelihood_of": str(b.get("likelihood_of", ""))[:120], "horizon": "30 days",
                "direction": b.get("direction") if b.get("direction") in DIRECTIONS else "steady",
                "summary": summary, "drivers": items("drivers", 4), "brakes": items("brakes", 3),
                "tip": str(b.get("tip", ""))[:200], "offramp": str(b.get("offramp", ""))[:200],
                "watch": [str(w)[:140] for w in (b.get("watch") or [])[:3]],
                "agree": str(b.get("agree", ""))[:240], "sides": str(b.get("sides", ""))[:240],
                "prev_stage": p["stage"] if p else None}
        if not item["drivers"] and not item["brakes"]: log.append(f"{cid}: nothing backed; skipped"); continue
        item["_cited"] = sorted(set(all_cited))
        out.append(item)
    return out

def public(item, tiles, heads, notes_by_hot):
    """What the app gets: the ladder, the data tiles and only the headlines/notes that were cited."""
    hmap = {h["hid"]: h for h in heads}
    nmap = {n["id"]: n for n in notes_by_hot}
    cited = item.pop("_cited", [])
    item["headlines"] = [{"id": i, "t": hmap[i]["t"], "o": hmap[i].get("o", ""), "u": hmap[i]["u"], "s": hmap[i].get("s", ""),
                          "c": hmap[i].get("c", ""), "l": hmap[i].get("l", "en"), "p": hmap[i].get("p", 0)} for i in cited if i in hmap]
    item["notes"] = [wwdata.public_note(nmap[i]) for i in cited if i in nmap]
    item["tiles"] = tiles
    return item
