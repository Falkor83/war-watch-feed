#!/usr/bin/env python3
"""
War Watch daily ratings: asks Claude (with web search) what changed in the world's wars and armed conflicts, merges
the answer into feed.json, checks the result and writes it only if it passes. Standard library only.

  python3 tools/update_feed.py daily     # Sonnet searches the last ~48 h, Opus writes the patch and the analysis
  python3 tools/update_feed.py weekly    # per region: Sonnet re-researches, Opus rebuilds the region; then Opus's big picture
  python3 tools/update_feed.py check     # only validate feed.json

Cost (measured 2026-11-04): the first daily run with 18 searches used 371k input tokens, about $1.07, because each
search round re-sends the growing context. Now: Sonnet does the searching (10 a day, 7 per region on Sundays) and Opus
only reads Sonnet's notes (no searching), so Opus's judgment costs little. Expect roughly $0.70 a day and $4 on Sundays,
about $30-35 a month; cyber and sabotage tracking (next) adds about $4. Cap $50 (raised from $40 on 2026-11-03, approved by Matt).
Spending cap: budget.json keeps an estimate of this month's spend from the API's own usage numbers; a run that would
start above MONTHLY_CAP_USD is skipped. Prints [STEP]/[OK]/[WARN]/[ERROR]. Never prints the API key.
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.error

API = "https://api.anthropic.com/v1/messages"
# Two stages: Sonnet runs the web searches (where the tokens pile up) and writes compact research notes; Opus reads only
# those notes, with no searching, and makes the calls: severity, trend, US impact, outlook and the cross-country analysis.
GATHER_MODEL = os.environ.get("WW_GATHER_MODEL", "claude-sonnet-5-5")
ANALYST_MODEL = os.environ.get("WW_ANALYST_MODEL", "claude-opus-5-5")
DAILY_MODEL = GATHER_MODEL      # kept for older log lines
SEARCH_TOOL = os.environ.get("WW_SEARCH_TOOL", "web_search_20250305")
MONTHLY_CAP_USD = float(os.environ.get("WW_MONTHLY_CAP_USD", "50"))
# USD per million tokens (input, output) and per search; from platform.claude.com/docs/en/about-claude/pricing (Nov 2026).
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (1.0, 5.0)}
SEARCH_USD = 0.01
REGIONS = ["Europe", "Middle East", "Africa", "Asia-Pacific", "Americas"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(ROOT, "feed.json")
BUDGET = os.path.join(ROOT, "budget.json")

def step(m): print(f"[STEP] {m}", flush=True)
def ok(m): print(f"[OK] {m}", flush=True)
def warn(m): print(f"[WARN] {m}", flush=True)
def err(m): print(f"[ERROR] {m}", flush=True)

def now_iso(): return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def today(): return datetime.date.today().isoformat()

# ---------- budget ----------
def load_budget():
    month = today()[:7]
    try: b = json.load(open(BUDGET))
    except Exception: b = {}
    if b.get("month") != month: b = {"month": month, "usd": 0.0, "runs": []}
    return b

def add_cost(b, model, usage, label):
    pin, pout = PRICES.get(model, (4.0, 20.0))
    tin = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
    tout = usage.get("output_tokens", 0)
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
    usd = tin / 1e6 * pin + tout / 1e6 * pout + searches * SEARCH_USD
    b["usd"] = round(b["usd"] + usd, 4)
    b["runs"] = (b.get("runs") or [])[-60:] + [{"at": now_iso(), "what": label, "model": model, "in": tin, "out": tout,
                                                "searches": searches, "usd": round(usd, 4)}]
    json.dump(b, open(BUDGET, "w"), indent=1)
    ok(f"{label}: {tin} in / {tout} out tokens, {searches} searches, about ${usd:.2f} (month so far ${b['usd']:.2f} of ${MONTHLY_CAP_USD:.0f})")

# ---------- API ----------
def call(model, system, user, max_uses, max_tokens):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key: raise SystemExit("[ERROR] ANTHROPIC_API_KEY is not set (run Set-WarWatchSetup.cmd on the PC)")
    messages = [{"role": "user", "content": user}]
    total = {"input_tokens": 0, "output_tokens": 0, "server_tool_use": {"web_search_requests": 0}}
    text = ""
    for turn in range(6):                                   # pause_turn: send the paused turn back unchanged
        body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if max_uses > 0: body["tools"] = [{"type": SEARCH_TOOL, "name": "web_search", "max_uses": max_uses}]
        req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=600) as r: resp = json.load(r); break
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:400]
                if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                    warn(f"API {e.code}, retrying in {20 * (attempt + 1)} s"); time.sleep(20 * (attempt + 1)); continue
                raise SystemExit(f"[ERROR] API {e.code}: {detail}")
        u = resp.get("usage") or {}
        total["input_tokens"] += u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        total["output_tokens"] += u.get("output_tokens", 0)
        total["server_tool_use"]["web_search_requests"] += (u.get("server_tool_use") or {}).get("web_search_requests", 0)
        text += "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        if resp.get("stop_reason") == "pause_turn":
            messages = messages + [{"role": "assistant", "content": resp["content"]}]
            continue
        if resp.get("stop_reason") == "max_tokens": warn("answer hit max_tokens; it may be cut off")
        break
    return text, total

def salvage_objects(text, key):
    """Complete {...} objects inside a (possibly cut-off) "key": [ ... ] list."""
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

def extract_json(text):
    """The last fenced ```json block, else the outermost {...} or [...]."""
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
    for cand in reversed(blocks):
        try: return json.loads(cand)
        except Exception: pass
    for o, c in (("{", "}"), ("[", "]")):
        i, j = text.find(o), text.rfind(c)
        if i != -1 and j > i:
            try: return json.loads(text[i:j + 1])
            except Exception: pass
    raise ValueError("no JSON in the answer")

# ---------- validation ----------
SEV = {"red", "yellow"}; TREND = {"escalating", "steady", "de-escalating"}; LEVEL = {"direct", "indirect", "low"}
CHANNELS = {"troops", "treaty", "energy", "shipping", "trade", "chips", "cyber", "nuclear", "migration", "citizens", "homeland", "aid"}

def clean_conflict(c):
    """Returns a cleaned conflict or raises ValueError with the reason."""
    if not isinstance(c, dict): raise ValueError("not an object")
    cid = re.sub(r"[^a-z0-9-]+", "-", str(c.get("id", "")).lower()).strip("-")[:80]
    if not cid: raise ValueError("no id")
    try: lat, lon = float(c["lat"]), float(c["lon"])
    except (KeyError, TypeError, ValueError): raise ValueError(f"{cid}: no usable lat/lon")
    if not (-60 <= lat <= 75 and -180 <= lon <= 180): raise ValueError(f"{cid}: coordinates out of range")
    sev = str(c.get("severity", "")).lower()
    if sev not in SEV: raise ValueError(f"{cid}: severity {sev!r}")
    ui = c.get("us_impact") or {}
    level = str(ui.get("level", "")).lower()
    if level not in LEVEL: raise ValueError(f"{cid}: us level {level!r}")
    latest = []
    for d in c.get("latest") or []:
        url = str(d.get("url", ""))
        if not url.startswith("https://") or not d.get("text"): continue
        latest.append({"date": str(d.get("date", ""))[:10], "text": str(d["text"])[:400], "outlet": str(d.get("outlet", ""))[:60],
                       "country": str(d.get("country", ""))[:40], "url": url[:500]})
    latest.sort(key=lambda d: d["date"], reverse=True)
    if len(latest) < 2: raise ValueError(f"{cid}: fewer than 2 sourced developments")
    out = {
        "id": cid, "name": str(c.get("name", ""))[:80], "region": str(c.get("region", "World"))[:40],
        "countries": [str(x)[:40] for x in (c.get("countries") or [])][:12], "lat": round(lat, 1), "lon": round(lon, 1),
        "severity": sev, "trend": str(c.get("trend", "steady")).lower() if str(c.get("trend", "")).lower() in TREND else "steady",
        "status": str(c.get("status", ""))[:120], "summary": str(c.get("summary", ""))[:2400], "latest": latest[:6],
        "us_impact": {"level": level, "channels": [x for x in (ui.get("channels") or []) if x in CHANNELS],
                      "text": str(ui.get("text", ""))[:1200]},
        "watch": str(c.get("watch", ""))[:400],
        "keywords": [str(k)[:60] for k in (c.get("keywords") or []) if len(str(k)) >= 3][:30],
        "confidence": str(c.get("confidence", "medium")).lower()[:10], "as_of": str(c.get("as_of", today()))[:10],
        "outlook": str(c.get("outlook", ""))[:500], "consensus": str(c.get("consensus", ""))[:500],
    }
    if len(out["name"]) < 3 or len(out["status"]) < 10 or len(out["summary"]) < 120 or len(out["us_impact"]["text"]) < 40:
        raise ValueError(f"{cid}: name/status/summary/US text too short")
    if len(out["keywords"]) < 5 or not any(k[:1].isupper() for k in out["keywords"]):
        raise ValueError(f"{cid}: needs 5+ keywords with at least one proper name")
    return out

def clean_signals(sigs, ids):
    """The big-picture analysis: 3-8 points, each backed by 2+ countries' outlets and real https sources."""
    out = []
    for g in sigs or []:
        if not isinstance(g, dict): continue
        srcs = [{"date": str(d.get("date", ""))[:10], "outlet": str(d.get("outlet", ""))[:60], "country": str(d.get("country", ""))[:40],
                 "url": str(d.get("url", ""))[:500]} for d in (g.get("sources") or []) if str(d.get("url", "")).startswith("https://")]
        countries = [str(x)[:40] for x in (g.get("countries") or [])][:12]
        conf = [x for x in (g.get("conflicts") or []) if x in ids]
        title, detail = str(g.get("title", ""))[:140], str(g.get("detail", ""))[:1200]
        if len(title) < 8 or len(detail) < 60 or len(countries) < 2 or not conf or not srcs: continue
        out.append({"title": title, "detail": detail,
                    "direction": g.get("direction") if g.get("direction") in ("escalating", "easing", "mixed", "steady") else "mixed",
                    "confidence": str(g.get("confidence", "medium")).lower()[:10], "countries": countries, "conflicts": conf, "sources": srcs[:6]})
    return out[:8]

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
    if len(clean_signals(feed.get("signals"), set(ids))) < 3: raise ValueError("fewer than 3 usable analysis points (signals)")

# ---------- prompts ----------
RULES = open(os.path.join(ROOT, "prompt", "rules.md")).read()

def compact(feed, region=None):
    rows = []
    for c in feed["conflicts"]:
        if region and c.get("region") != region: continue
        rows.append({k: c[k] for k in ("id", "name", "region", "severity", "trend", "status", "as_of")} |
                    {"us_level": c["us_impact"]["level"], "latest": [f'{d["date"]}: {d["text"]}' for d in c["latest"][:2]]})
    return json.dumps(rows, ensure_ascii=False)

NOTE_RULE = ("You do not search. You get research notes that a colleague gathered today from worldwide outlets. Use only "
             "those notes and the current hotspots. Every URL you cite must be one of the notes' URLs, copied exactly.")

def gather(feed, budget, label, focus, searches):
    """Stage 1 (Sonnet + web search): compact, sourced research notes. Returns (findings, allowed_urls)."""
    system = RULES + "\n\n" + open(os.path.join(ROOT, "prompt", "gather.md")).read()
    user = (f"Today is {today()}. Focus: {focus}. Current hotspots (compact):\n{compact(feed)}\n\n"
            "Search and return the research-notes JSON described in your instructions.")
    text, usage = call(GATHER_MODEL, system, user, max_uses=searches, max_tokens=24000)
    add_cost(budget, GATHER_MODEL, usage, f"{label} search notes")
    try:
        got = extract_json(text)
        notes = got.get("findings") if isinstance(got, dict) else got
    except ValueError:
        notes = salvage_objects(text, "findings")          # cut off: keep every complete note
        warn(f"{label}: notes were cut off; kept {len(notes)} complete ones")
    clean = []
    for n in notes or []:
        if not isinstance(n, dict): continue
        url = str(n.get("url", ""))
        if not url.startswith("https://") or not n.get("fact"): continue
        clean.append({"hotspot": str(n.get("hotspot", ""))[:80], "date": str(n.get("date", ""))[:10], "fact": str(n["fact"])[:400],
                      "outlet": str(n.get("outlet", ""))[:60], "country": str(n.get("country", ""))[:40], "url": url[:500],
                      "state_media": bool(n.get("state_media", False))})
    ok(f"{label}: {len(clean)} sourced notes from {len({n['country'] for n in clean})} countries")
    return clean, {n["url"] for n in clean}

def only_known_urls(items, allowed, what):
    kept = [d for d in (items or []) if isinstance(d, dict) and d.get("url") in allowed]
    if len(kept) < len(items or []): warn(f"{what}: dropped {len(items) - len(kept)} item(s) whose URL wasn't in the research notes")
    return kept

def run_daily(feed, budget):
    notes, allowed = gather(feed, budget, "daily", "everything from the last 48 hours, most searches on red and US-direct hotspots",
                            int(os.environ.get("WW_DAILY_SEARCHES", "10")))
    if not notes: raise SystemExit("[ERROR] no usable research notes; feed left as it was")
    allowed |= {d["url"] for c in feed["conflicts"] for d in c["latest"]} | {s["url"] for g in feed.get("signals", []) for s in g.get("sources", [])}
    system = RULES + "\n\n" + open(os.path.join(ROOT, "prompt", "daily.md")).read() + "\n\n" + NOTE_RULE
    user = (f"Today is {today()}. Current hotspots (compact):\n{compact(feed)}\n\nCurrent US overview:\n{feed['us_overview']}\n\n"
            f"Research notes gathered today:\n{json.dumps(notes, ensure_ascii=False)}\n\nReturn the patch JSON described in your instructions.")
    text, usage = call(ANALYST_MODEL, system, user, max_uses=0, max_tokens=16000)
    add_cost(budget, ANALYST_MODEL, usage, "daily analysis")
    patch = extract_json(text)
    for u in patch.get("updates") or []:
        if u.get("new_latest"): u["new_latest"] = only_known_urls(u["new_latest"], allowed, f"update {u.get('id')}")
    for n in patch.get("new") or []:
        n["latest"] = only_known_urls(n.get("latest"), allowed, f"new {n.get('id')}")
    for g in patch.get("signals") or []:
        g["sources"] = only_known_urls(g.get("sources"), allowed, "analysis point")
    byid = {c["id"]: c for c in feed["conflicts"]}
    changed = []
    for u in patch.get("updates") or []:
        cid = u.get("id")
        if cid not in byid: warn(f"update for unknown id {cid!r} skipped"); continue
        merged = json.loads(json.dumps(byid[cid]))
        for k in ("severity", "trend", "status", "summary", "watch", "confidence", "lat", "lon", "outlook", "consensus"):
            if k in u and u[k] not in (None, ""): merged[k] = u[k]
        if u.get("us_impact"): merged["us_impact"] = {**merged["us_impact"], **{k: v for k, v in u["us_impact"].items() if v}}
        if u.get("new_latest"):
            seen = {d["url"] for d in merged["latest"]}
            merged["latest"] = [d for d in u["new_latest"] if d.get("url") not in seen] + merged["latest"]
        if u.get("add_keywords"): merged["keywords"] = list(dict.fromkeys(merged["keywords"] + u["add_keywords"]))
        merged["as_of"] = today()
        try: byid[cid] = clean_conflict(merged); changed.append(cid)
        except ValueError as e: warn(f"update {cid} refused: {e}")
    for n in patch.get("new") or []:
        try:
            c = clean_conflict(n)
            if c["id"] in byid: warn(f"new {c['id']} already exists; skipped"); continue
            byid[c["id"]] = c; changed.append(c["id"] + " (new)")
        except ValueError as e: warn(f"new hotspot refused: {e}")
    out = dict(feed); out["conflicts"] = list(byid.values())
    if len(str(patch.get("us_overview", ""))) >= 80: out["us_overview"] = str(patch["us_overview"])[:1200]
    sig = clean_signals(patch.get("signals"), set(byid))
    if len(sig) >= 3: out["signals"] = sig; changed.append(f"analysis ({len(sig)} points)")
    elif patch.get("signals"): warn("new analysis had fewer than 3 usable points; kept the old one")
    out["signals"] = clean_signals(out.get("signals"), set(byid))
    return out, changed, patch.get("notes", "")

def run_weekly(feed, budget):
    byid = {c["id"]: c for c in feed["conflicts"]}
    changed, all_notes, allowed_all = [], [], set()
    for region in REGIONS:
        if budget["usd"] >= MONTHLY_CAP_USD: warn(f"monthly cap reached; {region} skipped"); continue
        sub = dict(feed); sub["conflicts"] = [c for c in feed["conflicts"] if c.get("region") == region]
        try:
            notes, allowed = gather(sub, budget, f"weekly {region}",
                                    f"the whole {region} region: every current hotspot there and any new one that meets the bar",
                                    int(os.environ.get("WW_WEEKLY_SEARCHES", "7")))
        except (SystemExit, ValueError) as e:
            warn(f"{region}: {e}"); continue
        all_notes += notes; allowed_all |= allowed
        system = RULES + "\n\n" + open(os.path.join(ROOT, "prompt", "weekly.md")).read() + "\n\n" + NOTE_RULE
        user = (f"Today is {today()}. Region: {region}. Current hotspots in this region (compact):\n{compact(feed, region)}\n\n"
                f"Research notes gathered today:\n{json.dumps(notes, ensure_ascii=False)}\n\n"
                "Return the JSON array described in your instructions.")
        try:
            text, usage = call(ANALYST_MODEL, system, user, max_uses=0, max_tokens=24000)
        except SystemExit as e:
            warn(f"{region}: {e}"); continue
        add_cost(budget, ANALYST_MODEL, usage, f"weekly {region} analysis")
        try: arr = extract_json(text)
        except ValueError as e: warn(f"{region}: {e}"); continue
        if isinstance(arr, dict): arr = arr.get("conflicts") or []
        old_urls = {d["url"] for c in sub["conflicts"] for d in c["latest"]}
        fresh = {}
        for n in arr:
            if isinstance(n, dict): n["latest"] = only_known_urls(n.get("latest"), allowed | old_urls, f"{region} {n.get('id')}")
            try: c = clean_conflict(n); c["region"] = region; fresh[c["id"]] = c
            except ValueError as e: warn(f"{region}: refused {e}")
        if len(fresh) < 2: warn(f"{region}: only {len(fresh)} usable hotspots; kept the old ones"); continue
        for cid in [k for k, v in byid.items() if v.get("region") == region and k not in fresh]:
            del byid[cid]; changed.append(cid + " (removed)")
        for cid, c in fresh.items():
            changed.append(cid if cid in byid else cid + " (new)"); byid[cid] = c
    out = dict(feed); out["conflicts"] = list(byid.values())
    if all_notes and budget["usd"] < MONTHLY_CAP_USD:
        try:
            system2 = RULES + "\n\n" + open(os.path.join(ROOT, "prompt", "signals.md")).read() + "\n\n" + NOTE_RULE
            user2 = (f"Today is {today()}. All current hotspots (compact):\n{compact(out)}\n\n"
                     f"This week's research notes:\n{json.dumps(all_notes, ensure_ascii=False)}\n\nWrite the analysis JSON described in your instructions.")
            text, usage = call(ANALYST_MODEL, system2, user2, max_uses=0, max_tokens=8000)
            add_cost(budget, ANALYST_MODEL, usage, "weekly big picture")
            got = extract_json(text)
            sigs = got.get("signals") if isinstance(got, dict) else got
            for g in sigs or []:
                if isinstance(g, dict): g["sources"] = only_known_urls(g.get("sources"), allowed_all, "analysis point")
            sig = clean_signals(sigs, set(byid))
            if len(sig) >= 3: out["signals"] = sig; changed.append(f"analysis ({len(sig)} points)")
            if isinstance(got, dict) and len(str(got.get("us_overview", ""))) >= 80: out["us_overview"] = str(got["us_overview"])[:1200]
        except (SystemExit, ValueError) as e:
            warn(f"analysis: {e}")
    out["signals"] = clean_signals(out.get("signals"), set(byid))
    return out, changed, ""

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    feed = json.load(open(FEED))
    if mode == "check":
        validate_feed(feed); ok(f"feed.json is valid: {len(feed['conflicts'])} hotspots"); return
    budget = load_budget()
    if budget["usd"] >= MONTHLY_CAP_USD:
        warn(f"This month's estimate ${budget['usd']:.2f} has reached the ${MONTHLY_CAP_USD:.0f} cap; nothing run."); return
    step(f"{mode} update: {GATHER_MODEL} searches, {ANALYST_MODEL} decides")
    out, changed, notes = (run_daily if mode == "daily" else run_weekly)(feed, budget)
    out["generated_at"] = now_iso()
    out["generated_by"] = f"Claude {mode} update {today()}: Sonnet 5.5 searched worldwide outlets, Opus 5.5 rated and analysed"
    try:
        validate_feed(out, previous=feed)
    except ValueError as e:
        err(f"The new feed failed its checks ({e}); feed.json was left as it was."); sys.exit(1)
    tmp = FEED + ".tmp"; json.dump(out, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, FEED)
    ok(f"feed.json written: {len(out['conflicts'])} hotspots; changed: {', '.join(changed) or 'nothing'}")
    if notes: print(f"Notes: {notes}")
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as s:
        s.write(f"### War Watch {mode} update\n\nChanged: {', '.join(changed) or 'nothing'}\n\nMonth so far: ${budget['usd']:.2f} of ${MONTHLY_CAP_USD:.0f}\n")

if __name__ == "__main__":
    main()
