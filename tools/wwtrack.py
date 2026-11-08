"""
War Watch track record: were the outlooks right?

The daily analysis logs dated calls in outlooks.json ("The Abu Dhabi round ending without a ceasefire deal: likely,
check by 2026-11-12"). Once a call's date has passed, Sonnet checks it with a few web searches, against today's notes
and the worldwide headlines about its hotspot: happened, didn't happen, partly, or unclear. Python keeps only verdicts
backed by a cited URL Claude actually saw. An unclear call is re-checked every 2 days for a week, then stays unclear.

feed.json gets `track`: counts per likelihood word, a Brier score (0 = perfect, 0.25 = coin-flipping), and the latest
results. Standard library only.
"""
import hashlib, json, os, re
import wwapi, wwboil, wwscore

RESULTS = ("happened", "did_not", "partly", "unclear")
LABELS = {"happened": "Happened", "did_not": "Didn't happen", "partly": "Partly", "unclear": "Unclear"}
# The likelihood words of prompt/rules.md as probabilities, for the Brier score.
PROB = {"almost no chance": 0.02, "very unlikely": 0.1, "unlikely": 0.3, "roughly even chance": 0.5, "likely": 0.7,
        "very likely": 0.9, "almost certain": 0.98}
OUTCOME = {"happened": 1.0, "did_not": 0.0, "partly": 0.5}
RETRY_DAYS, GIVE_UP_DAYS, MAX_PER_DAY = 2, 7, 6

def oid(o):
    return "o" + hashlib.sha1(f"{o.get('made')}|{o.get('hotspot')}|{o.get('text')}".encode()).hexdigest()[:9]

def due(outlooks, today):
    """Calls whose date has passed and that have no verdict yet (or an 'unclear' one ready for a re-check)."""
    out = []
    for o in outlooks:
        cb = str(o.get("check_by", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cb) or wwscore.days_between(cb, today) < 0: continue
        r = o.get("result")
        if r in ("happened", "did_not", "partly"): continue
        if r == "unclear":
            if o.get("final") or wwscore.days_between(o.get("checked", cb), today) < RETRY_DAYS: continue
        out.append(o)
    out.sort(key=lambda o: o["check_by"])
    return out[:MAX_PER_DAY]

def request(calls, feed, items, notes, model, system, today):
    names = {c["id"]: c.get("name", c["id"]) for c in feed.get("conflicts") or []}
    tags = wwboil.assigned(feed.get("conflicts") or [], items)
    blocks = []
    for o in calls:
        hs = o.get("hotspot")
        heads = [x for x, ids in zip(items, tags) if hs in ids][:12]
        nl = [n for n in notes if n.get("hotspot") == hs][:6]
        blocks.append(
            f"{oid(o)}: [{names.get(hs, hs)}] \"{o['text']}\"; called {o.get('likelihood', '?')} on {o.get('made')}, check by {o['check_by']}"
            + (f"\n   Earlier check ({o.get('checked')}): unclear" if o.get("result") == "unclear" else "")
            + "".join(f"\n   - note: {n.get('fact', '')[:240]} ({n.get('url', '')})" for n in nl)
            + "".join(f"\n   - headline ({x.get('c') or '?'} {x.get('s', '')[:30]}): {x['t'][:200]} ({x['u']})" for x in heads))
    user = (f"Today is {today}.\n\nOUTLOOKS TO CHECK\n" + "\n\n".join(blocks) +
            "\n\nReturn the JSON described in your instructions.")
    return {"label": "track record", "model": model, "system": system, "user": user,
            "max_uses": min(2 * len(calls), 6), "max_tokens": 12000, "estimate": 0.1}

def check(ans, calls, seen_urls, known_urls, today, log):
    """Verdicts into the outlooks (in place). A verdict needs a URL Claude saw (its searches) or was given."""
    by = {oid(o): o for o in calls}
    ok_urls = {wwapi.norm_url(u) for u in seen_urls | known_urls}
    done = 0
    for r in (ans.get("results") if isinstance(ans, dict) else None) or []:
        if not isinstance(r, dict) or r.get("id") not in by: continue
        o, res = by[r["id"]], r.get("result")
        if res not in RESULTS: log.append(f"{r['id']}: unknown result {res!r}; skipped"); continue
        ev = [u for u in (r.get("evidence") or []) if isinstance(u, str) and wwapi.norm_url(u) in ok_urls][:3]
        if res != "unclear" and not ev:
            log.append(f"{r['id']}: '{res}' without a URL that was seen; set to unclear"); res = "unclear"
        o.update(result=res, checked=today, why=wwscore.clip(r.get("why", ""), 240), evidence=ev)
        if res == "unclear" and wwscore.days_between(o["check_by"], today) >= GIVE_UP_DAYS: o["final"] = True
        done += 1
    for o in calls:                       # never answered: try again later, and give up after a week
        if o.get("checked") != today and wwscore.days_between(o["check_by"], today) >= GIVE_UP_DAYS:
            o.update(result="unclear", checked=today, why="Not checked in time.", evidence=[], final=True)
    return done

def summary(outlooks, today):
    """For feed.json: per likelihood word, how often calls came true; Brier score; the latest verdicts."""
    scored = [o for o in outlooks if o.get("result") in RESULTS]
    bands = {}
    for o in scored:
        b = bands.setdefault(o.get("likelihood", "?"), {"calls": 0, "happened": 0, "did_not": 0, "partly": 0, "unclear": 0})
        b["calls"] += 1; b[o["result"]] += 1
    br = [(PROB[o["likelihood"]] - OUTCOME[o["result"]]) ** 2 for o in scored
          if o.get("likelihood") in PROB and o["result"] in OUTCOME]
    recent = sorted(scored, key=lambda o: (o.get("checked", ""), o["check_by"]), reverse=True)[:12]
    pending = sum(1 for o in outlooks if not o.get("result"))
    return {"date": today, "checked": len(scored), "pending": pending,
            "brier": round(sum(br) / len(br), 3) if br else None, "brier_calls": len(br),
            "bands": [dict(likelihood=k, **v) for k, v in sorted(bands.items(), key=lambda kv: PROB.get(kv[0], 0.5))],
            "recent": [{"id": oid(o), "hotspot": o.get("hotspot"), "text": o.get("text"), "likelihood": o.get("likelihood"),
                        "made": o.get("made"), "check_by": o.get("check_by"), "result": o["result"],
                        "result_label": LABELS[o["result"]], "why": o.get("why", ""), "evidence": o.get("evidence") or [],
                        "checked": o.get("checked")} for o in recent]}

def stage(out, outlooks_path, items, notes, budget, model, system, today, warn, extract):
    """Checks the calls that fell due and writes `track` into the feed. Returns (calls checked, verdicts)."""
    ol = json.load(open(outlooks_path, encoding="utf-8")) if os.path.exists(outlooks_path) else {"outlooks": []}
    calls = due(ol.get("outlooks") or [], today)
    n = 0
    if calls:
        ans = wwapi.run([request(calls, out, items, notes, model, system, today)], budget)
        if ans:
            text, seen = ans["track record"]
            known = {n_.get("url", "") for n_ in notes} | {x.get("u", "") for x in items}
            log = []
            try: n = check(extract(text), calls, set(seen), known, today, log)
            except ValueError as e: log.append(f"no usable answer ({e})")
            for line in log: warn(f"track record: {line}")
        else:
            warn("track record: the check didn't come back; the calls wait for tomorrow")
            check({}, calls, set(), set(), today, [])            # still gives up on calls a week past due
        wwapi.save_json(outlooks_path, ol, indent=0)
    out["track"] = summary(ol.get("outlooks") or [], today)
    return len(calls), n
