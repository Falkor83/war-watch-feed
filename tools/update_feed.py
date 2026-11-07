#!/usr/bin/env python3
"""
War Watch ratings (feed schema 2). Claude researches the world's wars and cyber/sabotage attacks; Python checks and
scores everything before feed.json changes. Standard library only.

  python3 tools/update_feed.py daily     # Sonnet searches (notes), Opus decides from the notes only
  python3 tools/update_feed.py weekly    # Sundays: every region re-researched, then the daily decision over all notes
  python3 tools/update_feed.py check     # only validate feed.json
  python3 tools/update_feed.py migrate   # one-off: schema 1 -> 2 without any API call

How trust is kept (design: claude/war-watch-review-2026-11-03/analysis.md in Matt's AI Projects):
- Notes may only carry URLs the search tool really returned. Outlet, country, region and media type come from
  outlets.json by domain, not from the model. Each note gets an independence key: five reprints of one AP story, or
  ten outlets relaying one ministry statement, are one source. A warring side's own media is "X says", never agreement.
- The analyst cites note IDs only. Python fills in every source and counts every number the app shows (independent
  sources, regions, the 0-5 evidence meter, confidence caps, tripwire states, the world tension index).
- Figures in analysis text must appear in a cited note, or the sentence is removed.
- A severity or US-impact change needs 2 independent sources, at least one not from a party to the fighting.
- Hotspots are only removed by a sourced "ended" call in the Sunday review; at most 6 at once.

Cost (Batch API, half price): about $0.80 a day and $2.50 on Sundays, about $41-42 a month with translation, focus
updates and actions; cap $45 (WW_MONTHLY_CAP_USD).
History of the cap: $40, raised to $50 on 2026-11-03 (Matt approved, for cyber tracking), then lowered to $35 the same
night because Matt is moving back to the $100/month plan and everything on his API credits must stay under $100;
raised to $45 on 2026-11-07 (Matt approved; Aegis's share drops from $65 to $55, total still $100). A run that would pass the cap is skipped and the app is told
("paused"); the workflow then fails so GitHub emails. Prints [STEP]/[OK]/[WARN]/[ERROR]. Never prints the API key.
"""
import json, os, re, sys, time, datetime, calendar
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wwapi, wwdata, wwscore, wwboil, wwstories, wwai, wwactions
from wwapi import step, ok, warn, err, save_json, MONTHLY_CAP_USD

GATHER_MODEL = os.environ.get("WW_GATHER_MODEL", "claude-sonnet-5-5")
ANALYST_MODEL = os.environ.get("WW_ANALYST_MODEL", "claude-opus-5-5")
REGIONS = ["Europe", "Middle East", "Africa", "Asia-Pacific", "Americas"]
ROOT = wwapi.ROOT
FEED, HISTORY, OUTLOOKS = (os.path.join(ROOT, f) for f in ("feed.json", "history.json", "outlooks.json"))
LIVE = os.environ.get("WW_LIVE_DIR", os.path.join(ROOT, "live"))     # the hourly headlines job's branch, fetched by the workflow
def corpus_path():
    """The full worldwide corpus (corpus.json); headlines.json, the app's smaller copy, before that existed."""
    c = os.path.join(LIVE, "corpus.json")
    return c if os.path.exists(c) else os.path.join(LIVE, "headlines.json")
BOIL_GDELT = os.environ.get("WW_BOIL_GDELT", "1") == "1"
CORPUS = {"items": []}
NOTES_DIR = os.path.join(ROOT, "notes")
SEV = {"red", "yellow"}; TREND = {"escalating", "steady", "de-escalating"}; LEVEL = {"direct", "indirect", "low"}
CHANNELS = {"troops", "treaty", "energy", "shipping", "trade", "chips", "cyber", "nuclear", "migration", "citizens", "homeland", "aid"}
TYPES = {"interstate war", "civil war", "insurgency", "criminal violence", "standoff"}
US_STATUS = {"hit_in_us", "suspected_in_us", "same_tech_in_us", "no_known_link"}
ACTOR_CONF = {"claimed", "suspected", "attributed", "unknown"}
MAX_WATCH = 6

def today(): return datetime.date.today().isoformat()
def read(p): return open(p, encoding="utf-8").read()
def load(p, default=None):
    try: return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError): return default
LISTS = load(os.path.join(ROOT, "config", "lists.json"), {})
def prompt(name): return read(os.path.join(ROOT, "prompt", name))

# ---------- keywords ----------
KW_BLOCK = {"doha", "sofia", "patriot", "turkey", "us embassy", "washington", "white house", "pentagon", "rama", "fla",
            "nato", "un", "eu", "president", "minister", "army", "military", "war", "attack", "strike", "talks",
            "midterms", "midterm", "election", "elections", "vote", "voters", "ballot", "polls", "cyberattack", "refinery strike"}
KW_ACRONYMS_OK = {"IDF", "RSF", "M23", "TTP", "SDF", "PKK", "ADF", "BLA", "PLA", "SAF", "ISW", "FDLR", "IRGC", "DPRK",
                  "JNIM", "ISIS", "ISWAP", "NPA", "ELN", "CJNG", "HTS", "TPLF"}

def keyword_ok(k):
    k = str(k).strip()
    if len(k) < 3 or len(k) > 60 or k.lower() in KW_BLOCK: return False
    if re.fullmatch(r"[A-Z0-9]{2,4}", k) and k not in KW_ACRONYMS_OK: return False
    return True

def kwlist(xs, n):
    out = []
    for k in xs or []:
        if isinstance(k, str) and keyword_ok(k) and k not in out: out.append(k.strip())
    return out[:n]

# ---------- hotspots ----------
def region_for(lat, lon):
    if lon < -30: return "Americas"
    if lon > 60 or (lat < 0 and lon > 100): return "Asia-Pacific"
    if lat < 15 or (lat < 33 and lon < 33 and lon > -20): return "Africa"
    if lon > 34 and lat < 42: return "Middle East"
    return "Europe"

def to_latest(notes):
    return [{"date": n["date"], "text": n["fact"][:400], "outlet": n["outlet"][:60],
             "country": wwdata.country_name(n["home"]) if n["home"] else "", "url": n["url"],
             "region": n["region"], "tag": n["tag"], "state_media": n["media"] in ("state-controlled", "state-funded") and n["tag"] == "party-gov"}
            for n in sorted(notes, key=lambda n: n["date"], reverse=True)]

def clean_conflict(c):
    """A hotspot as stored (schema 2). Raises ValueError with the reason."""
    if not isinstance(c, dict): raise ValueError("not an object")
    cid = re.sub(r"[^a-z0-9-]+", "-", str(c.get("id", "")).lower()).strip("-")[:80]
    if not cid: raise ValueError("no id")
    try: lat, lon = float(c["lat"]), float(c["lon"])
    except (KeyError, TypeError, ValueError): raise ValueError(f"{cid}: no usable lat/lon")
    if not (-60 <= lat <= 75 and -180 <= lon <= 180): raise ValueError(f"{cid}: coordinates out of range")
    sev = str(c.get("severity", "")).lower()
    if sev not in SEV: raise ValueError(f"{cid}: severity {sev!r}")
    ui = c.get("us_impact") if isinstance(c.get("us_impact"), dict) else {}
    level = str(ui.get("level", "")).lower()
    if level not in LEVEL: raise ValueError(f"{cid}: us level {level!r}")
    latest = []
    for dd in c.get("latest") or []:
        if not isinstance(dd, dict): continue
        url = str(dd.get("url", ""))
        if not url.startswith("https://") or not dd.get("text") or wwdata.blocked(url): continue
        latest.append({"date": str(dd.get("date", ""))[:10], "text": str(dd["text"])[:400], "outlet": str(dd.get("outlet", ""))[:60],
                       "country": str(dd.get("country", ""))[:40], "url": url[:500], "region": str(dd.get("region", ""))[:20],
                       "tag": str(dd.get("tag", ""))[:12], "state_media": bool(dd.get("state_media", False))})
    seen, uniq = set(), []
    for dd in sorted(latest, key=lambda x: x["date"], reverse=True):
        if wwapi.norm_url(dd["url"]) in seen: continue
        seen.add(wwapi.norm_url(dd["url"])); uniq.append(dd)
    if len(uniq) < 2: raise ValueError(f"{cid}: fewer than 2 sourced developments")
    region = str(c.get("region", ""))
    if region not in REGIONS: region = region_for(lat, lon)
    keywords = kwlist(c.get("keywords"), 40)
    anchors = kwlist(c.get("anchors"), 25)
    context = kwlist(c.get("context"), 25)
    watch = c.get("us_watch") if isinstance(c.get("us_watch"), dict) and c["us_watch"].get("why") else None
    vd = c.get("week_verdict") if isinstance(c.get("week_verdict"), dict) else None
    out = {
        "id": cid, "name": str(c.get("name", ""))[:80], "short_name": str(c.get("short_name") or c.get("name", ""))[:22],
        "region": region, "countries": [str(x)[:40] for x in (c.get("countries") or [])][:12],
        "parties": [str(x)[:40] for x in (c.get("parties") or [])][:10], "interstate": bool(c.get("interstate", False)),
        "type": str(c.get("type", "")).lower() if str(c.get("type", "")).lower() in TYPES else "",
        "reason": str(c.get("reason", ""))[:160],
        "lat": round(lat, 1), "lon": round(lon, 1), "severity": sev,
        "trend": str(c.get("trend", "steady")).lower() if str(c.get("trend", "")).lower() in TREND else "steady",
        "status": str(c.get("status", ""))[:120], "summary": str(c.get("summary", ""))[:2400], "latest": uniq[:8],
        "us_impact": {"level": level, "channels": [x for x in (ui.get("channels") or []) if x in CHANNELS],
                      "text": str(ui.get("text", ""))[:1200]},
        "us_watch": {"why": str(watch["why"])[:200]} if watch else None, "rank": int(c["rank"]) if str(c.get("rank", "")).isdigit() else None,
        "watch": str(c.get("watch", ""))[:400],
        "keywords": keywords, "anchors": anchors, "context": context, "exclude": kwlist(c.get("exclude"), 12),
        "confidence": str(c.get("confidence", "medium")).lower()[:10], "as_of": str(c.get("as_of", today()))[:10],
        "outlook": str(c.get("outlook", ""))[:500], "consensus": str(c.get("consensus", ""))[:500],
        "week_verdict": {"verdict": vd.get("verdict"), "why": str(vd.get("why", ""))[:200], "date": str(vd.get("date", ""))[:10]}
                        if vd and vd.get("verdict") in ("deteriorated", "improved", "unchanged") else None,
        "spark": [int(x) for x in (c.get("spark") or []) if str(x) in ("0", "1", "2")][-28:],
    }
    if len(out["name"]) < 3 or len(out["status"]) < 10 or len(out["summary"]) < 120 or len(out["us_impact"]["text"]) < 40:
        raise ValueError(f"{cid}: name/status/summary/US text too short")
    if len(out["keywords"] + out["anchors"]) < 5 or not any(k[:1].isupper() or re.search(r"[A-Z]", k) for k in out["keywords"] + out["anchors"]):
        raise ValueError(f"{cid}: needs 5+ keywords with at least one proper name")
    return out

def clean_cyber(x, notes_by_id, old=None):
    if not isinstance(x, dict): raise ValueError("not an object")
    cid = re.sub(r"[^a-z0-9-]+", "-", str(x.get("id", "")).lower()).strip("-")[:60]
    if not cid: raise ValueError("no id")
    places = []
    for p in x.get("places") or []:
        try:
            lat, lon = float(p["lat"]), float(p["lon"])
            if -60 <= lat <= 75 and -180 <= lon <= 180: places.append({"name": str(p.get("name", ""))[:60], "lat": round(lat, 1), "lon": round(lon, 1)})
        except (KeyError, TypeError, ValueError, AttributeError): pass
    ns = [notes_by_id[i] for i in (x.get("notes") or []) if i in notes_by_id]
    sources = to_latest(ns) + [s for s in (old or {}).get("sources", []) if s["url"] not in {n["url"] for n in ns}]
    official = set(LISTS.get("official_cyber_sources", []))
    advisories = [{"title": s["outlet"], "url": s["url"]} for s in sources if any(wwdata.host(s["url"]).endswith(o) for o in official)]
    actor = x.get("actor") if isinstance(x.get("actor"), dict) else {}
    out = {"id": cid, "title": str(x.get("title", ""))[:120], "date": str(x.get("date", ""))[:10],
           "updated": today() if ns else (old or {}).get("updated", today()),
           "places": places[:8], "sector": str(x.get("sector", "")).lower() if str(x.get("sector", "")).lower() in LISTS.get("cyber_sectors", []) else "industry",
           "method": str(x.get("method", ""))[:240], "summary": str(x.get("summary", ""))[:900],
           "actor": {"name": str(actor.get("name", "unknown"))[:80], "confidence": actor.get("confidence") if actor.get("confidence") in ACTOR_CONF else "unknown"},
           "us_status": x.get("us_status") if x.get("us_status") in US_STATUS else "no_known_link",
           "us_why": str(x.get("us_why", ""))[:500], "what_to_watch": str(x.get("what_to_watch", ""))[:240],
           "sources": sources[:8], "advisories": advisories[:4]}
    if len(out["title"]) < 8 or not places or len(out["sources"]) < 1 or len(out["us_why"]) < 30: raise ValueError(f"cyber {cid}: incomplete")
    return out

# ---------- compact views for the model ----------
def hotspot_table(feed, region=None, with_sources=False):
    rows = ["id|name|region|severity|trend|us_level|watch|type|parties|status|tripwires"]
    tws = {}
    for tw in feed.get("tripwires", []): tws.setdefault(tw["hotspot"], []).append(f'{tw["id"]}:{tw["kind"]}:{tw["status"]}:{tw["text"]}')
    for c in feed["conflicts"]:
        if region and c.get("region") != region: continue
        rows.append("|".join(str(x) for x in (c["id"], c["name"], c["region"], c["severity"], c["trend"], c["us_impact"]["level"],
                    "!" if c.get("us_watch") else "", c.get("type", ""), ",".join(c.get("parties", [])), c["status"], " ; ".join(tws.get(c["id"], [])))))
        if with_sources:
            for dd in c["latest"][:3]: rows.append(f'   {dd["date"]} {dd["outlet"]}: {dd["text"][:160]}')
    return "\n".join(rows)

def notes_table(notes):
    rows = ["id|date|hotspot|outlet|region|tag|source_key|event|stance|fact"]
    for n in notes:
        rows.append("|".join(str(x) for x in (n["id"], n["date"], n["hotspot"], n["outlet"], n["region"], n["tag"], n["key"],
                                              n["event"], n["stance"], n["fact"])))
    return "\n".join(rows)

def signals_table(feed):
    rows = ["id|title|direction|independent_sources|non_party_regions|first_seen"]
    for s in feed.get("signals", []):
        st = s.get("stats") or {}
        rows.append(f'{s.get("id", "")}|{s["title"]}|{s["direction"]}|{st.get("keys_all", "?")}|{st.get("regions_np", "?")}|{s.get("first_seen", "")}')
    return "\n".join(rows)

def cyber_table(feed):
    rows = ["id|title|date|sector|us_status|actor"]
    for x in feed.get("cyber", []): rows.append(f'{x["id"]}|{x["title"]}|{x["date"]}|{x["sector"]}|{x["us_status"]}|{x["actor"]["name"]} ({x["actor"]["confidence"]})')
    return "\n".join(rows)

def parties_map(feed):
    return {c["id"]: wwdata.parties_iso(c.get("parties")) for c in feed["conflicts"]}

def blocked_domains(): return sorted(wwdata.registry()["blocked"])

# ---------- stages ----------
def gather_requests(feed, focus_list, searches):
    system = prompt("rules.md") + "\n\n" + prompt("gather.md")
    reqs = []
    for label, focus, region in focus_list:
        user = (f"Today is {today()}. Focus: {focus}.\n\nCurrent hotspots:\n{hotspot_table(feed, region)}\n\n"
                f"Current cyber/sabotage items:\n{cyber_table(feed)}\n\nSearch and return the research-notes JSON described in your instructions.")
        reqs.append({"label": label, "model": GATHER_MODEL, "system": system, "user": user, "max_uses": searches,
                     "max_tokens": 48000, "estimate": 0.4, "blocked_domains": blocked_domains()})
    return reqs

def parse_notes(text, label):
    try:
        got = wwapi_extract(text)
        notes = got.get("findings") if isinstance(got, dict) else got
    except ValueError:
        notes = salvage_objects(text, "findings")
        warn(f"{label}: notes were cut off; kept {len(notes)} complete ones")
    return notes if isinstance(notes, list) else []

def wwapi_extract(text):
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
    for cand in reversed(blocks):
        try: return json.loads(cand)
        except ValueError: pass
    for o, c in (("{", "}"), ("[", "]")):
        i, j = text.find(o), text.rfind(c)
        if i != -1 and j > i:
            try: return json.loads(text[i:j + 1])
            except ValueError: pass
    raise ValueError("no JSON in the answer")

def salvage_objects(text, key):
    i = text.find(f'"{key}"')
    if i < 0: return []
    i = text.find("[", i)
    if i < 0: return []
    dec, out, pos = json.JSONDecoder(), [], i + 1
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,": pos += 1
        if pos >= len(text) or text[pos] != "{": break
        try: obj, pos = dec.raw_decode(text, pos)
        except ValueError: break
        out.append(obj)
    return out

def collect_notes(results, feed, prefix="n"):
    """Every gather answer -> one checked note list with IDs, archived to notes/YYYY-MM-DD.json."""
    raw, seen_all = [], set()
    for label, (text, seen) in results.items():
        got = parse_notes(text, label)
        if not seen: warn(f"{label}: no search results were visible; the URL check is skipped for its notes")
        for n in got:
            if isinstance(n, dict): n["_seen"] = bool(seen)
        raw += got; seen_all |= seen
    checked, dropped = [], {}
    for n in raw:
        one, dr = wwdata.clean_notes([n], seen_all if n.get("_seen") else set(), parties_map(feed), today(), prefix)
        for k, v in dr.items(): dropped[k] = dropped.get(k, 0) + v
        checked += one
    seen_urls, notes = set(), []
    for n in checked:                                       # same URL twice: keep the first
        u = wwapi.norm_url(n["url"])
        if u in seen_urls: continue
        seen_urls.add(u); n["id"] = f"{prefix}{len(notes) + 1:02d}"; notes.append(n)
    if any(dropped.values()): warn(f"notes dropped: {dropped}")
    ok(f"{len(notes)} checked notes from {len({n['home'] for n in notes})} countries, {len({n['region'] for n in notes})} regions, "
       f"{len({n['key'] for n in notes})} independent sources")
    os.makedirs(NOTES_DIR, exist_ok=True)
    path = os.path.join(NOTES_DIR, today() + ".json")
    old = load(path, {"notes": []})
    save_json(path, {"date": today(), "notes": old["notes"] + [wwdata.public_note(n) | {"event": n["event"], "claim_by": n["claim_by"]} for n in notes]}, indent=0)
    for f in sorted(os.listdir(NOTES_DIR))[:-120]:          # keep 120 days
        try: os.remove(os.path.join(NOTES_DIR, f))
        except OSError: pass
    return notes

def world_notes(feed, hours=36, total=140):
    """The deep dive: every hotspot's worldwide headlines of the last day and a half, grouped into stories, become citable
    notes (g01, g02 ...): for each of its best-covered stories, the clearest headline from outside the fighting sides,
    plus the sides' own version when they reported it, each saying how many independent sources in how many countries
    and regions carried the story. Red, '!' and escalating hotspots get more stories."""
    items = CORPUS.get("items") or []
    if not items: return []
    now, t = int(time.time()), today()
    tags = wwboil.assigned(feed["conflicts"], items)
    by_hot = {}
    for x, ids in zip(items, tags):
        if (x.get("p") or 0) < now - hours * 3600 or not x.get("t"): continue
        for cid in ids: by_hot.setdefault(cid, []).append(x)
    def weight(c): return 6 if c["severity"] == "red" or c.get("us_watch") or c["trend"] == "escalating" else 3
    pm, raw = parties_map(feed), []
    for c in sorted(feed["conflicts"], key=lambda c: -weight(c)):
        mine = by_hot.get(c["id"]) or []
        if len(mine) < 3: continue
        parties = {wwdata.iso(p) for p in c.get("parties") or []} - {""}
        st = wwstories.cluster(mine)
        for s_ in st: s_["stats"] = wwstories.stats(s_, parties)
        st = [s_ for s_ in st if s_["stats"]["independent"] >= 2 or (c["severity"] == "red" and s_["stats"]["headlines"] >= 1 and s_["stats"]["party_only"])]
        st.sort(key=lambda s_: (-(s_["stats"]["independent"] + 2 * s_["stats"]["outside_regions"]), -s_["stats"]["newest"]))
        for k, s_ in enumerate(st[:weight(c)]):
            sts = s_["stats"]
            line = (f"{sts['independent']} independent source{'s' if sts['independent'] != 1 else ''} in {len(sts['countries'])} "
                    f"countr{'ies' if len(sts['countries']) != 1 else 'y'} ({', '.join(sts['countries'][:6])})"
                    + ("; only the sides' own media" if sts["party_only"] else ""))
            pick = sorted(s_["items"], key=lambda x: (x.get("c") in parties, wwstories.tier(x) != "known", -(x.get("p") or 0)))
            chosen = [pick[0]]
            side = next((x for x in pick if x.get("c") in parties and x is not pick[0]), None)
            if side and not sts["party_only"]: chosen.append(side)
            for x in chosen:
                raw.append({"hotspot": c["id"], "event": f"world story {c['id'][:30]} {k + 1}",
                            "date": min(t, datetime.datetime.fromtimestamp(x.get("p") or now, datetime.timezone.utc).date().isoformat()),
                            "fact": f"Headline{' (translated from ' + x['l'] + ')' if x.get('l', 'en') != 'en' else ''}: {x['t'][:240]} "
                                    f"[story carried by {line}]",
                            "url": x["u"], "outlet": x.get("s", ""), "country": wwdata.country_name(x.get("c", "")) if x.get("c") else "",
                            "origin": "own", "claim_by": "reporters", "stance": "neutral", "frame": "", "tripwire": "",
                            "state_media": wwstories.tier(x) == "state"})
        if len(raw) >= total: break
    notes, _ = wwdata.clean_notes([r for r in raw if r["url"].startswith("https://")], set(), pm, t, "g")
    seen, out = set(), []
    for n in notes:
        u = wwapi.norm_url(n["url"])
        if u in seen: continue
        seen.add(u); n["id"] = f"g{len(out) + 1:02d}"; out.append(n)
    ok(f"{len(out)} worldwide-headline notes for {len({n['hotspot'] for n in out})} hotspots, from {len({n['home'] for n in out})} countries")
    return out

def ai_stage(out, budget):
    system = prompt("rules.md") + "\n\n" + prompt("ai_risk.md")
    before = (out.get("ai_risk") or {}).get("date")
    out = wwai.stage(out, CORPUS.get("items") or [], budget, ANALYST_MODEL, system, today(), warn)
    a = out.get("ai_risk") or {}
    if a.get("date") == today() and before != today():
        ok(f"AI risk: {len(a['items'])} items from {a['read']['headlines']} headlines ({a['read']['outlets']} outlets, "
           f"{a['read']['countries']} countries): " + "; ".join(f"{i['severity']} {i['category']}: {i['title']}" for i in a["items"][:5]))
    return out

def actions_stage(out, budget):
    """Actions, not words: preparedness actions vs the events around them (tools/wwactions.py, prompt/actions.md)."""
    system = prompt("rules.md") + "\n\n" + prompt("actions.md")
    r = wwactions.stage(out, CORPUS.get("items") or [], budget, ANALYST_MODEL, system, today(), ROOT, warn)
    a = out.get("actions") or {}
    ok(f"actions: {r['new_actions']} new, {r['events']} events logged, {r['patterns']} patterns, {r['flags']} warning signs on hotspots"
       + "".join(f"\n   [{p['support']}] {p['title']} ({len(p['actions'])} actions, {', '.join(p['countries'][:6])})" for p in a.get("patterns") or []))
    return out

def coverage_table(feed, hours=24):
    """Who reported each hotspot in the last day, from the hourly worldwide headlines (all languages, translated).
    Context for the analyst: it can't be cited, but it shows where coverage is one-sided or silent."""
    items = CORPUS.get("items") or []
    if not items: return "(worldwide headlines not available today)"
    now = int(time.time()); rows = []
    for c in feed["conflicts"]:
        cp, mine = wwboil.corpus_panel(c, items, now, hours, conflicts=feed["conflicts"])
        if not cp["n"]: rows.append(f"{c['id']}: no headlines"); continue
        regs = ", ".join(f"{wwdata.REGION_NAMES.get(r, r)} {n}" for r, n in sorted(cp["regions"].items(), key=lambda kv: -kv[1])[:6])
        parties = {wwdata.iso(p) for p in c.get("parties") or []} - {""}
        outside = [x for x in mine if x.get("c") not in parties][:2]
        rows.append(f"{c['id']}: {cp['n']} headlines, {cp['outlets']} outlets, {len(cp['countries'])} countries, "
                    f"{len(cp['languages'])} languages; {regs}; {round(100 * cp['party_share'])}% from the sides' own countries"
                    + "".join(f"\n   - [{x.get('c', '?')}] {x['t'][:140]}" for x in outside))
    return "\n".join(rows)

def analyst_request(feed, notes, label="daily analysis"):
    system = prompt("rules.md") + "\n\n" + prompt("daily.md")
    user = (f"Today is {today()}.\n\nHOTSPOTS\n{hotspot_table(feed)}\n\nYESTERDAY'S SIGNALS\n{signals_table(feed)}\n\n"
            f"WORLDWIDE COVERAGE in the last 24 hours (hourly headline collection, all languages translated; context only, not citable)\n"
            f"{coverage_table(feed)}\n\n"
            f"CYBER AND SABOTAGE ITEMS\n{cyber_table(feed)}\n\n"
            f"GOVERNMENT PREPAREDNESS ACTIONS, last 60 days (the action log: what governments did, e.g. stockpiling advice, "
            f"reservists, embassy drawdowns; context, not citable; weigh them with the news, they often say what statements don't)\n"
            f"{wwactions.context_lines(wwactions.load_log(ROOT), today())}\n\nUS OVERVIEW NOW\n{feed.get('us_overview', '')}\n\n"
            f"NOTES gathered today (cite these IDs only). n- and w-notes come from today's searches and read articles; g-notes are "
            f"worldwide headlines from the hourly collection (all languages, translated), one per story, each saying how many "
            f"independent sources in how many countries carried it. Use g-notes to confirm, contest or widen the others and to "
            f"see what other regions report; a headline is thinner evidence than an article, so prefer the others for details.\n"
            f"{notes_table(notes)}\n\nReturn the JSON described in your instructions.")
    return {"label": label, "model": ANALYST_MODEL, "system": system, "user": user, "max_uses": 0, "max_tokens": 20000, "estimate": 0.3}

SEV_RANK = {"yellow": 0, "red": 1}; LEVEL_RANK = {"low": 0, "indirect": 1, "direct": 2}

def gate_change(old, merged, ns, log):
    """Moving a hotspot UP (to red, or to direct US impact) needs 2 independent sources with at least one not a
    party's own account. Moving it down needs at least one cited note (calmer calls must not be harder to make)."""
    keys = {n["key"] for n in ns}
    nonparty = any(n["tag"] != "party-gov" for n in ns)
    for field, get, rank in (("severity", lambda c: c["severity"], SEV_RANK), ("US level", lambda c: c["us_impact"]["level"], LEVEL_RANK)):
        if get(old) == get(merged): continue
        up = rank.get(get(merged), 0) > rank.get(get(old), 0)
        if (up and (len(keys) < 2 or not nonparty)) or (not up and not ns):
            log(f"{old['id']}: {field} change {get(old)} -> {get(merged)} refused ("
                + ("needs 2 independent sources, one not a party's own)" if up else "no note cited)"))
            if field == "severity": merged["severity"] = old["severity"]
            else: merged["us_impact"]["level"] = old["us_impact"]["level"]

def merge_analysis(feed, patch, notes, history, mode="daily"):
    """Apply the analyst's answer under the rules. Returns (new feed, list of changes)."""
    if not isinstance(patch, dict): raise ValueError("the analyst's answer is not a JSON object")
    t = today(); log = warn
    nb = {n["id"]: n for n in notes}
    byid = {c["id"]: json.loads(json.dumps(c)) for c in feed["conflicts"]}
    changed = []
    for u in patch.get("updates") or []:
        if not isinstance(u, dict) or u.get("id") not in byid: log(f"update for unknown id {u.get('id') if isinstance(u, dict) else u!r} skipped"); continue
        cid = u["id"]; old = byid[cid]; merged = json.loads(json.dumps(old))
        for k in ("severity", "trend", "status", "summary", "watch", "confidence", "lat", "lon", "outlook", "consensus",
                  "reason", "type", "short_name", "parties", "interstate", "anchors", "context", "exclude"):
            if k in u and u[k] not in (None, "", []): merged[k] = u[k]
        if isinstance(u.get("us_impact"), dict): merged["us_impact"] = {**merged["us_impact"], **{k: v for k, v in u["us_impact"].items() if v}}
        ns = [nb[x] for x in (u.get("notes") or []) if x in nb]
        if ns:
            have = {wwapi.norm_url(dd["url"]) for dd in merged["latest"]}
            merged["latest"] = [dd for dd in to_latest(ns) if wwapi.norm_url(dd["url"]) not in have] + merged["latest"]
        if isinstance(u.get("add_keywords"), list): merged["keywords"] = kwlist(u["add_keywords"] + merged["keywords"], 40)
        if isinstance(u.get("week_verdict"), dict):
            vn = [nb[x] for x in (u["week_verdict"].get("notes") or []) if x in nb]
            if vn: merged["week_verdict"] = {"verdict": u["week_verdict"].get("verdict"), "why": u["week_verdict"].get("why", ""), "date": t}
        gate_change(old, merged, ns, log)
        merged["as_of"] = t
        try: byid[cid] = clean_conflict(merged); changed.append(cid)
        except ValueError as e: log(f"update {cid} refused: {e}")
    for n in patch.get("new") or []:
        if not isinstance(n, dict): continue
        ns = [nb[x] for x in (n.get("notes") or []) if x in nb]
        n = dict(n); n["latest"] = to_latest(ns); n["as_of"] = t
        if len({x["key"] for x in ns}) < 2: log(f"new {n.get('id')}: needs 2 independent sources; refused"); continue
        try:
            c = clean_conflict(n)
            if c["id"] in byid: log(f"new {c['id']} already exists; skipped"); continue
            byid[c["id"]] = c; changed.append(c["id"] + " (new)")
        except ValueError as e: log(f"new hotspot refused: {e}")
    for e in patch.get("ended") or []:
        if mode != "weekly" or not isinstance(e, dict) or e.get("id") not in byid: continue
        ns = [nb[x] for x in (e.get("notes") or []) if x in nb]
        if not ns: log(f"ended {e.get('id')}: no note backs it; kept"); continue
        del byid[e["id"]]; changed.append(f"{e['id']} (ended: {str(e.get('why', ''))[:80]})")
    # the "!" list: at most 6, each with a reason
    wl = patch.get("watch_list")
    if isinstance(wl, list) and wl:
        picks = [w for w in wl if isinstance(w, dict) and w.get("id") in byid and len(str(w.get("why", ""))) >= 20][:MAX_WATCH]
        if picks:
            for c in byid.values(): c["us_watch"] = None
            for w in picks: byid[w["id"]]["us_watch"] = {"why": str(w["why"])[:200]}
            changed.append(f"watch list ({len(picks)})")
    if not any(c.get("us_watch") for c in byid.values()):    # fallback: red and direct
        for c in byid.values():
            if c["severity"] == "red" and c["us_impact"]["level"] == "direct":
                c["us_watch"] = {"why": "Red and rated direct US impact."}
    rank = [x for x in (patch.get("rank") or []) if x in byid]
    if len(rank) >= 5:
        for c in byid.values(): c["rank"] = None
        for i, x in enumerate(dict.fromkeys(rank)): byid[x]["rank"] = i + 1
    out = dict(feed); out["schema"] = 2; out["conflicts"] = list(byid.values()); ids = set(byid)
    # overview, brief
    all_text_notes = notes + [{"fact": dd["text"]} for c in byid.values() for dd in c["latest"]]
    if len(str(patch.get("us_overview", ""))) >= 80:
        txt, _ = wwscore.drop_unsupported_sentences(str(patch["us_overview"])[:1200], all_text_notes)
        if len(txt) >= 80: out["us_overview"] = txt
    pts = [str(p)[:200] for p in (patch.get("us_points") or []) if isinstance(p, str) and len(p) > 15]
    pts = [p for p in pts if not wwscore.numbers_supported(p, all_text_notes)]
    if len(pts) >= 2: out["us_points"] = pts[:4]
    brief = [str(b)[:220] for b in (patch.get("brief") or []) if isinstance(b, str) and len(b) > 15]
    brief = [b for b in brief if not wwscore.numbers_supported(b, all_text_notes)]
    if brief: out["brief"] = {"date": t, "lines": brief[:3]}
    # signals
    prev = {s.get("id"): s for s in feed.get("signals", []) if s.get("id")}
    sigs, seen = [], set()
    for g in patch.get("signals") or []:
        s = wwscore.score_signal(g, nb, prev, t, ids, log)
        if s and s["id"] not in seen: sigs.append(s); seen.add(s["id"])
    def same_point(a, b):
        ta, tb = set(re.findall(r"[a-z]{4,}", a["title"].lower())), set(re.findall(r"[a-z]{4,}", b["title"].lower()))
        return bool(ta and tb) and len(ta & tb) / min(len(ta), len(tb)) >= 0.5 and bool(set(a.get("conflicts", [])) & set(b.get("conflicts", [])))
    for sid, old in prev.items():                           # yesterday's points with no new evidence: fade out
        if sid not in seen and old.get("stats") and not any(same_point(old, s) for s in sigs):
            k = wwscore.carry_signal(old, t)
            if k and set(k.get("conflicts", [])) & ids: sigs.append(k)
    if sigs or not feed.get("signals"):
        out["signals"] = sorted(sigs, key=lambda s: (s["status"] == "fading", -s["stats"]["dots"]))[:8]
        changed.append(f"analysis ({len(out['signals'])} points)")
    # frames, events
    frames = [f for f in (wwscore.check_frame(f, nb, ids, t) for f in (patch.get("frames") or [])) if f]
    old_frames = [f for f in feed.get("frames", []) if f.get("hotspot") in ids and wwscore.days_between(f.get("date", ""), t) <= 2
                  and f["id"] not in {x["id"] for x in frames}]
    out["frames"] = (frames + old_frames)[:5]
    ev = [{"hotspot": e["hotspot"], "label": str(e.get("label", ""))[:80], "date": str(e.get("date", t))[:10],
           "anchors": [str(a)[:30] for a in (e.get("anchors") or [])][:6]}
          for e in (patch.get("events") or []) if isinstance(e, dict) and e.get("hotspot") in ids and e.get("anchors")]
    out["events"] = (ev + [e for e in feed.get("events", []) if wwscore.days_between(e["date"], t) <= 3 and e["label"] not in {x["label"] for x in ev}])[:40]
    # tripwires: define (weekly, or daily for hotspots that have none), update, decay
    tws = {tw["id"]: dict(tw) for tw in feed.get("tripwires", []) if tw["hotspot"] in ids}
    have = {tw["hotspot"] for tw in tws.values() if tw.get("source") != "action"}
    for c in patch.get("updates") or []:
        if isinstance(c, dict) and isinstance(c.get("tripwires"), list) and c.get("id") in ids and (mode == "weekly" or c["id"] not in have):
            keep = {x.get("id") for x in c["tripwires"] if isinstance(x, dict) and x.get("id")}
            for tid in [k for k, v in tws.items() if v["hotspot"] == c["id"] and k not in keep]: del tws[tid]
            for x in c["tripwires"][:4]:
                if not isinstance(x, dict) or x.get("kind") not in ("escalation", "easing") or len(str(x.get("text", ""))) < 10: continue
                if x.get("id") in tws: tws[x["id"]]["text"] = str(x["text"])[:110]; continue
                n = 1
                while f"tw-{c['id']}-{n}" in tws: n += 1
                tws[f"tw-{c['id']}-{n}"] = {"id": f"tw-{c['id']}-{n}", "hotspot": c["id"], "kind": x["kind"], "text": str(x["text"])[:110],
                                            "status": "not_seen", "since": t, "evidence": []}
    for x in patch.get("new_tripwires") or []:
        if isinstance(x, dict) and x.get("hotspot") in ids and x.get("kind") in ("escalation", "easing") and len(str(x.get("text", ""))) >= 10 \
                and sum(1 for v in tws.values() if v["hotspot"] == x["hotspot"] and v.get("source") != "action") < 4:
            n = 1
            while f"tw-{x['hotspot']}-{n}" in tws: n += 1
            tws[f"tw-{x['hotspot']}-{n}"] = {"id": f"tw-{x['hotspot']}-{n}", "hotspot": x["hotspot"], "kind": x["kind"],
                                             "text": str(x["text"])[:110], "status": "not_seen", "since": t, "evidence": []}
    for upd in patch.get("tripwire_updates") or []:
        if isinstance(upd, dict) and upd.get("id") in tws:
            if wwscore.tripwire_update(tws[upd["id"]], upd, nb, t, log): changed.append(f"tripwire {upd['id']} {tws[upd['id']]['status']}")
    for tw in tws.values(): wwscore.tripwire_decay(tw, t)
    out["tripwires"] = sorted(tws.values(), key=lambda tw: (tw["hotspot"], tw["id"]))
    # cyber and sabotage
    cy = {x["id"]: x for x in feed.get("cyber", [])}
    for x in patch.get("cyber") or []:
        try:
            item = clean_cyber(x, nb, cy.get(str(x.get("id", "")) if isinstance(x, dict) else ""))
            cy[item["id"]] = item; changed.append(f"cyber {item['id']}")
        except ValueError as e: log(f"cyber item refused: {e}")
    out["cyber"] = sorted([x for x in cy.values() if wwscore.days_between(x["updated"], t) <= 45],
                          key=lambda x: ({"hit_in_us": 0, "suspected_in_us": 1, "same_tech_in_us": 2}.get(x["us_status"], 3), x["date"]))[:12]
    # outlooks for the track record
    ol = load(OUTLOOKS, {"outlooks": []})
    for o in patch.get("outlooks") or []:
        if isinstance(o, dict) and o.get("hotspot") in ids and o.get("text") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(o.get("check_by", ""))):
            ol["outlooks"].append({"made": t, "hotspot": o["hotspot"], "text": str(o["text"])[:300], "likelihood": str(o.get("likelihood", ""))[:30],
                                   "check_by": o["check_by"], "basis": [nb[x]["url"] for x in (o.get("basis") or []) if x in nb][:4]})
    ol["outlooks"] = ol["outlooks"][-400:]
    save_json(OUTLOOKS, ol, indent=0)
    out.pop("notes", None)                                   # sources travel inside each signal
    return out, changed

def finish(out, history):
    """Python-only measures: tension index, history, spark lines, change log, week line."""
    t = today()
    out["tension"] = wwscore.tension(out["conflicts"], out.get("tripwires", []), LISTS, t, history)
    history = wwscore.add_history(history, wwscore.history_row(out, t))
    out = boil_history(out, history)
    for c in out["conflicts"]: c["spark"] = wwscore.spark(history, c["id"])
    out["changes"] = wwscore.changes(history, days=30)
    out["week_line"] = wwscore.week_line(history, out, t)
    out.pop("paused", None)
    return out, history

# ---------- boiling point ----------
def boil_stage(out, notes, budget):
    """Could a tense hotspot tip into (wider) war? Data panel by rule, Opus's ladder, Python's checks."""
    t = today()
    prev = {b["id"]: b for b in out.get("boil") or [] if wwscore.days_between(b.get("date", t), t) <= 3}
    cands, scores = wwboil.candidates(out, list(prev.values()), t, LISTS.get("nuclear_states", []))
    if not cands: out["boil"] = []; ok("boiling point: no hotspot near the line"); return out
    step(f"boiling point: data for {', '.join(cands)}")
    src, items, now = wwboil.Sources(t), CORPUS.get("items") or [], int(time.time())
    alog = wwactions.load_log(ROOT)
    byid = {x["id"]: x for x in out["conflicts"]}
    series = wwboil.gdelt_prefetch({cid: wwboil.hotspot_query(byid[cid]) for cid in cands}) if BOIL_GDELT else {}
    panels, raws, heads, n_d, n_h = {}, {}, {}, 1, 1
    for cid in cands:
        c = byid[cid]
        pts, raw, mine = wwboil.build_panel(c, items, src, now, series.get(cid), first_id=n_d, conflicts=out["conflicts"])
        line = wwactions.hotspot_line(alog, cid, t)
        if line:                                             # Actions, not words: preparedness steps tied to this hotspot
            did = f"d{n_d + len(pts)}"
            pts.append({"id": did, "kind": "actions", "text": line})
            raw.append({"id": did, "kind": "actions", "label": "Preparedness actions (30 days)", "value": line.split(": ", 1)[1].split(" (")[0],
                        "note": line.split("(", 1)[1].rstrip(".").rstrip(")") if "(" in line else "", "level": 1})
        n_d += len(pts)
        parties = {wwdata.iso(p) for p in c.get("parties") or []} - {""}
        hs = []
        for h in wwboil.pick_headlines(mine, parties):
            hs.append(dict(h, hid=f"h{n_h}", party=h.get("c") in parties)); n_h += 1
        panels[cid], raws[cid], heads[cid] = pts, raw, hs
        ok(f"  {cid}: {len(pts)} data points, {len(hs)} headlines")
    nb = {}
    for n in notes: nb.setdefault(n["hotspot"], []).append(n)
    system = prompt("rules.md") + "\n\n" + prompt("boil.md")
    ans = wwapi.run([wwboil.boil_request(cands, out, panels, heads, nb, prev, ANALYST_MODEL, system, t)], budget)
    if not ans:
        warn("boiling point didn't come back; yesterday's kept"); out["boil"] = list(prev.values()); return out
    try: parsed = wwapi_extract(ans["boiling point"][0])
    except ValueError as e: warn(f"boiling point: {e}; yesterday's kept"); out["boil"] = list(prev.values()); return out
    log = []
    checked = wwboil.check(parsed, cands, panels, heads, nb, prev, t, log)
    for line in log: warn("boiling point: " + line)
    res = [wwboil.public(it, raws[it["id"]], heads[it["id"]], nb.get(it["id"], [])) for it in checked]
    for it in res: it["score"] = scores.get(it["id"], 0)
    res.sort(key=lambda b: (-b["stage"], b["direction"] != "rising", -b["score"]))
    out["boil"] = res
    ok("boiling point: " + "; ".join(f"{b['id']} stage {b['stage']} ({b['stage_label']}), {b['likelihood'] or 'no likelihood'}, {b['direction']}" for b in res))
    return out

def boil_history(out, history):
    for b in out.get("boil") or []:
        b["history"] = [[h["date"], (h.get("boil") or {}).get(b["id"])] for h in history[-14:] if (h.get("boil") or {}).get(b["id"])]
    return out

# ---------- validation ----------
def validate_feed(feed, previous=None):
    cs = feed.get("conflicts") or []
    if len(cs) < 15: raise ValueError(f"only {len(cs)} hotspots (need 15+)")
    ids = [c["id"] for c in cs]
    if len(ids) != len(set(ids)): raise ValueError("duplicate ids")
    for c in cs: clean_conflict(c)
    if previous:
        gone = set(c["id"] for c in previous["conflicts"]) - set(ids)
        if len(gone) > 6: raise ValueError(f"would remove {len(gone)} hotspots at once: {sorted(gone)}")
    if len(feed.get("us_overview", "")) < 80: raise ValueError("us_overview missing")
    if len(feed.get("signals") or []) < 2: raise ValueError("fewer than 2 analysis points (signals)")
    if sum(1 for c in cs if c.get("us_watch")) > MAX_WATCH: raise ValueError("too many '!' hotspots")

# ---------- runs ----------
def pace(budget):
    """Spending so far vs a straight line to the cap: 'normal', 'lite' (fewer searches) or 'stop'."""
    now = datetime.date.today()
    dim = calendar.monthrange(now.year, now.month)[1]
    fair = MONTHLY_CAP_USD * now.day / dim
    total = budget["usd"] + budget.get("headlines_usd", 0.0) + budget.get("focus_usd", 0.0)
    if total >= MONTHLY_CAP_USD - 1.0: return "stop"
    return "lite" if total > 0.9 * fair + 3 else "normal"

def run_daily(feed, budget, history, level):
    searches = int(os.environ.get("WW_DAILY_SEARCHES", "12")) - (4 if level == "lite" else 0)
    focus = ("everything from the last 48 hours: about two thirds of your searches on red, '!' and US-direct hotspots, two on how "
             "other sides and regions cover today's two biggest events, one on the hotspots' tripwires, one sweep for new hotspots, "
             "and three on cyberattacks and sabotage against infrastructure (food, water, power, telecom, hospitals, transport) "
             "anywhere, plus official advisories (CISA, FBI, CERT-FR, NCSC)")
    res = wwapi.run(gather_requests(feed, [("daily search notes", focus, None)], searches), budget)
    if not res: raise SystemExit("[ERROR] no research notes came back; feed left as it was")
    notes = collect_notes(res, feed)
    if len(notes) < 8: raise SystemExit(f"[ERROR] only {len(notes)} usable notes; feed left as it was")
    notes += world_notes(feed)
    ans = wwapi.run([analyst_request(feed, notes)], budget)
    if not ans: raise SystemExit("[ERROR] the analysis didn't come back; feed left as it was")
    patch = wwapi_extract(ans["daily analysis"][0])
    out, changed = merge_analysis(feed, patch, notes, history, "daily")
    if isinstance(patch, dict) and patch.get("log"): print(f"Notes: {str(patch['log'])[:400]}")
    out = boil_stage(out, notes, budget)
    out = ai_stage(out, budget)
    out = actions_stage(out, budget)
    return out, changed

def run_weekly(feed, budget, history, level):
    searches = int(os.environ.get("WW_WEEKLY_SEARCHES", "7"))
    focus = [(f"weekly {r} notes", f"the whole {r} region: every current hotspot there (search each separately), its tripwires, "
              f"and any new one that meets the bar", r) for r in REGIONS]
    focus.append(("weekly cyber notes", "cyberattacks and sabotage against infrastructure anywhere in the last 7 days, who is "
                  "suspected, whether the same methods or equipment are used in the US, and official advisories", None))
    res = wwapi.run(gather_requests(feed, focus, searches), budget)
    if not res: raise SystemExit("[ERROR] no research notes came back; feed left as it was")
    notes = collect_notes(res, feed, prefix="w")
    notes += world_notes(feed)
    system = prompt("rules.md") + "\n\n" + prompt("weekly.md")
    reqs = []
    for r in REGIONS:
        rn = [n for n in notes if any(n["hotspot"] == c["id"] for c in feed["conflicts"] if c["region"] == r) or
              (n["hotspot"].startswith("new") and n["region"] != "unknown")]
        reqs.append({"label": f"weekly {r} review", "model": ANALYST_MODEL, "system": system, "max_uses": 0, "max_tokens": 16000, "estimate": 0.3,
                     "user": f"Today is {today()}. Region: {r}.\n\nHOTSPOTS IN THIS REGION\n{hotspot_table(feed, r, with_sources=True)}\n\n"
                             f"NOTES gathered today (cite these IDs only)\n{notes_table(rn)}\n\nReturn the JSON described in your instructions."})
    ans = wwapi.run(reqs, budget)
    work, changed = feed, []
    for r in REGIONS:
        if f"weekly {r} review" not in ans: warn(f"{r}: no review came back; region kept as it was"); continue
        try: patch = wwapi_extract(ans[f"weekly {r} review"][0])
        except ValueError as e: warn(f"{r}: {e}"); continue
        if not isinstance(patch, dict): warn(f"{r}: answer is not an object"); continue
        mine = {c["id"] for c in work["conflicts"] if c["region"] == r}
        sub = {"updates": [u for u in patch.get("updates") or [] if isinstance(u, dict) and u.get("id") in mine],
               "new": patch.get("new"), "ended": [e for e in patch.get("ended") or [] if isinstance(e, dict) and e.get("id") in mine]}
        work, ch = merge_analysis(work, sub, notes, history, "weekly")
        changed += [c for c in ch if not c.startswith(("analysis", "watch list"))]
    # then the daily decision over all of today's notes (signals, frames, tripwire states, '!', brief, cyber)
    ans2 = wwapi.run([analyst_request(work, notes, "weekly big picture")], budget)
    if ans2:
        out, ch = merge_analysis(work, wwapi_extract(ans2["weekly big picture"][0]), notes, history, "daily")
        changed += ch
    else:
        warn("big picture didn't come back; signals kept as they were"); out = work
    out = boil_stage(out, notes, budget)
    out = ai_stage(out, budget)
    out = actions_stage(out, budget)
    return out, changed

def migrate(feed):
    """Schema 1 -> 2 with no API call: signal sources are counted by rule, '!' falls back to red + direct."""
    t = today(); out = dict(feed); out["schema"] = 2
    cs = []
    for c in feed["conflicts"]:
        c = dict(c); c.setdefault("anchors", []); c.setdefault("context", [])
        for dd in c["latest"]:
            src = wwdata.note_source({"url": dd["url"], "country": dd.get("country"), "outlet": dd.get("outlet")})
            dd["region"] = src["region"]; dd["tag"] = "non-party"
        if c["severity"] == "red" and c["us_impact"]["level"] == "direct": c["us_watch"] = {"why": "Red and rated direct US impact."}
        cs.append(clean_conflict(c))
    order = sorted(cs, key=lambda c: (c["severity"] != "red", c["us_impact"]["level"] != "direct", c["trend"] != "escalating", -len(c["latest"])))
    for i, c in enumerate(order): c["rank"] = i + 1
    out["conflicts"] = cs
    sigs = []
    for i, g in enumerate(feed.get("signals", [])):
        ns = []
        for j, s in enumerate(g.get("sources", [])):
            src = wwdata.note_source({"url": s["url"], "country": s.get("country"), "outlet": s.get("outlet")})
            n = {"id": f"m{i}{j}", "date": s.get("date", t), "fact": g.get("detail", ""), "url": s["url"], "outlet_text": s.get("outlet", ""),
                 "origin": "own", "claim_by": "reporters", **src}
            n["wire"] = n["wire"] or wwdata.wire_in(n["outlet_text"])
            n["tag"] = "non-party"; n["key"] = wwdata.indep_key(n); ns.append(n)
        sid = re.sub(r"[^a-z0-9]+", "-", g["title"].lower()).strip("-")[:50]
        stats = wwscore.evidence_stats(ns, [], t)
        sigs.append({**g, "id": sid, "claim": "", "status": "same", "first_seen": t, "last_confirmed": t, "stats": stats,
                     "confidence": wwscore.confidence_cap(stats, g.get("confidence", "medium")),
                     "countries": [wwdata.country_name(x) for x in stats["countries_iso"]],
                     "sources": [wwscore.source_ref(n) for n in ns], "against": [], "other_reading": "",
                     "would_change_view": "", "tripwires": []})
    out["signals"] = sigs
    out.setdefault("tripwires", []); out.setdefault("frames", []); out.setdefault("events", [])
    out.setdefault("cyber", []); out.setdefault("brief", None)
    return out

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if mode not in ("daily", "weekly", "check", "migrate", "panel", "ai", "actions"):
        raise SystemExit(f"[ERROR] unknown mode {mode!r} (daily, weekly, check, migrate, panel, ai, actions)")
    feed = load(FEED)
    if mode == "check":
        validate_feed(feed); ok(f"feed.json is valid: {len(feed['conflicts'])} hotspots, schema {feed.get('schema', 1)}"); return
    history = load(HISTORY, {"days": []})["days"]
    if mode == "panel":                                      # dry run of the boiling point's data: no API call, nothing saved
        CORPUS.update(load(corpus_path(), {"items": []}) or {"items": []})
        t = today(); cands, scores = wwboil.candidates(feed, feed.get("boil") or [], t, LISTS.get("nuclear_states", []))
        print(f"candidates: {[(c, scores[c]) for c in cands]}")
        src, n = wwboil.Sources(t), 1
        byid = {x["id"]: x for x in feed["conflicts"]}
        series = wwboil.gdelt_prefetch({cid: wwboil.hotspot_query(byid[cid]) for cid in cands}, budget_s=600) if BOIL_GDELT else {}
        for cid in cands:
            c = byid[cid]
            pts, raw, mine = wwboil.build_panel(c, CORPUS.get("items") or [], src, int(time.time()), series.get(cid), first_id=n, conflicts=feed["conflicts"])
            n += len(pts)
            print(f"\n== {cid}: {len(mine)} headlines in 48 h")
            for d in pts: print(f"  {d['id']} [{d['kind']}] {d['text']}")
            parties = {wwdata.iso(p) for p in c.get("parties") or []} - {""}
            for h in wwboil.pick_headlines(mine, parties)[:8]: print(f"    - [{h.get('c')} {h.get('l')}] {h['t'][:120]}")
        print("\n" + coverage_table(feed)[:6000]); return
    if mode == "migrate":
        out = migrate(feed)
        out, history = finish(out, history)
        validate_feed(out, previous=feed)
        save_json(FEED, out); save_json(HISTORY, {"days": history}, indent=0)
        ok(f"migrated to schema 2: {len(out['conflicts'])} hotspots, {len(out['signals'])} signals, tension {out['tension']['value']}"); return
    if mode in ("daily", "weekly") and os.environ.get("WW_FORCE") != "1" and str(feed.get("generated_at", ""))[:10] == today():
        ok(f"ratings already updated today ({feed['generated_at']}); nothing to do"); return
    budget = wwapi.load_budget()
    hs = load(os.path.join(LIVE, "spend.json"), {}) or {}
    if hs.get("month") == budget["month"]: budget["headlines_usd"] = round(hs.get("usd", 0.0), 4)
    fb = load(os.path.join(ROOT, "focus_budget.json"), {}) or {}        # the every-few-hours focus updates (tools/focus.py)
    if fb.get("month") == budget["month"]: budget["focus_usd"] = round(fb.get("usd", 0.0), 4)
    budget["total_usd"] = round(budget["usd"] + budget.get("headlines_usd", 0.0) + budget.get("focus_usd", 0.0), 4)
    CORPUS.update(load(corpus_path(), {"items": []}) or {"items": []})
    ok(f"worldwide headlines: {len(CORPUS.get('items') or [])} available (generated {CORPUS.get('generated', 'never')})")
    level = pace(budget)
    if level == "stop" or (mode == "weekly" and budget["total_usd"] > MONTHLY_CAP_USD - 4):
        feed["paused"] = f"Monthly budget reached (${budget['usd']:.2f} of ${MONTHLY_CAP_USD:.0f}); ratings resume on the 1st."
        save_json(FEED, feed)
        err(feed["paused"]); sys.exit(1)
    if level == "lite": warn(f"spending is ahead of pace (${budget['usd']:.2f}); running a lighter update")
    if mode in ("ai", "actions"):                              # one reading alone, between daily runs
        out = (ai_stage if mode == "ai" else actions_stage)(dict(feed), budget)
        save_json(FEED, out); ok(f"feed.json: {'AI risk' if mode == 'ai' else 'actions'} updated"); return
    if feed.get("schema", 1) < 2: feed = migrate(feed)
    step(f"{mode} update: {wwapi.GATHER_MODEL if hasattr(wwapi, 'GATHER_MODEL') else GATHER_MODEL} searches, {ANALYST_MODEL} decides"
         f"{', Batch API (half price)' if wwapi.USE_BATCH else ''}")
    out, changed = (run_daily if mode == "daily" else run_weekly)(feed, budget, history, level)
    out, history = finish(out, history)
    out["generated_at"] = wwapi.now_iso()
    out["generated_by"] = f"Claude {mode} update {today()}: Sonnet 5.5 searched worldwide outlets, Opus 5.5 decided; checked and scored by rule"
    try:
        validate_feed(out, previous=feed)
    except ValueError as e:
        err(f"The new feed failed its checks ({e}); feed.json was left as it was."); sys.exit(1)
    save_json(FEED, out); save_json(HISTORY, {"days": history}, indent=0)
    ok(f"feed.json written: {len(out['conflicts'])} hotspots, tension {out['tension']['value']} ({out['tension']['band']}); "
       f"changed: {', '.join(changed) or 'nothing'}")
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as s:
        s.write(f"### War Watch {mode} update\n\nTension {out['tension']['value']} ({out['tension']['band']}). "
                f"Changed: {', '.join(changed) or 'nothing'}\n\nMonth so far: ${budget['usd']:.2f} of ${MONTHLY_CAP_USD:.0f}\n")

if __name__ == "__main__":
    main()
