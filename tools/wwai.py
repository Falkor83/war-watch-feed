"""
War Watch AI risk: once a day Opus reads the last 48 hours of worldwide AI headlines that touch on catastrophic risk
(tagged 'ai-risk' by tools/headlines.py), grouped into stories with who reported each by rule, and writes the items the
app shows: what happened, the plausible path to catastrophic harm, the kind of evidence, who says it. Python checks
citations and numbers and allows "high" only with a primary source (a lab, safety institute or research group) or
independent sources in 2+ countries. Standard library only.
"""
import json, os, re, time
import wwapi, wwdata, wwscore, wwstories

CATEGORIES = {"ai-building-ai": "AI building AI", "weapons": "Weapons of mass destruction", "cyber": "Cyberattacks",
              "loss-of-control": "Loss of control", "military": "Military AI", "critical-systems": "Critical systems",
              "misuse": "Misuse at scale", "race-and-rules": "Race and rules"}
EVIDENCE = ("demonstrated", "reported", "claimed", "forecast", "policy")
SEVERITY = ("high", "elevated", "watch")
PRIMARY = ("anthropic.com", "openai.com", "deepmind.google", "deepmind.com", "aisi.gov.uk", "gov.uk", "metr.org", "safe.ai",
           "epoch.ai", "cset.georgetown.edu", "rand.org", "incidentdatabase.ai", "futureoflife.org", "nist.gov", "cnas.org",
           "thebulletin.org", "transformernews.ai", "importai.substack.com", "aisafetychina.substack.com", "europa.eu")

def primary(x):
    d = (x.get("d") or wwdata.host(x.get("u", "")) or "").lower()
    return any(d == p or d.endswith("." + p) for p in PRIMARY)

def ai_items(items, now, hours=48):
    return [x for x in items if "ai-risk" in (x.get("h") or []) and (x.get("p") or 0) >= now - hours * 3600 and x.get("t")]

def stories_for(items, limit=45):
    st = wwstories.cluster(items, threshold=0.38)
    for s in st:
        s["parties"] = set()
        s["stats"] = wwstories.stats(s)
        s["primary"] = any(primary(x) for x in s["items"])
    # countries count more than outlets, so one country's many local outlets (e.g. an industrial-safety story) don't crowd out the rest
    st.sort(key=lambda s: (-(min(s["stats"]["independent"], 4) + 2 * len(s["stats"]["countries"]) + 3 * s["primary"]), -s["stats"]["newest"]))
    return st[:limit]

def request(stories, prev, model, system, today):
    lines = []
    for i, s in enumerate(stories):
        st = s["stats"]
        lines.append(f"a{i + 1}: [{st['independent']} independent source{'s' if st['independent'] != 1 else ''} in "
                     f"{len(st['countries'])} countr{'ies' if len(st['countries']) != 1 else 'y'} ({', '.join(st['countries'][:8])})"
                     + ("; primary source" if s["primary"] else "") + "]\n" + "\n".join(
                         f"   - ({x.get('c') or '?'} {x.get('s', '')[:40]}{', primary' if primary(x) else ''}) {x['t'][:220]}"
                         for x in wwstories.pick_titles(s, 3)))
    old = "\n".join(f"- {it['id']} [{it['category']}, {it['severity']}, {it['evidence']}] {it['title']}: {it['what']}"
                    for it in (prev or {}).get("items") or []) or "none"
    user = (f"Today is {today}.\n\nYESTERDAY'S ITEMS\n{old}\n\nSTORIES (last 48 hours)\n" + "\n".join(lines) +
            "\n\nReturn the JSON described in your instructions.")
    return {"label": "ai risk", "model": model, "system": system, "user": user, "max_uses": 0, "max_tokens": 16000, "estimate": 0.15}

def check(ans, stories, prev, today, log):
    by = {f"a{i + 1}": s for i, s in enumerate(stories)}
    def facts(ids): return [{"fact": " ".join(x["t"] for x in by[i]["items"][:15])} for i in ids if i in by]
    old = {it["id"]: it for it in (prev or {}).get("items") or []}
    out = []
    for it in (ans.get("items") if isinstance(ans, dict) else None) or []:
        if not isinstance(it, dict): continue
        ids = [i for i in dict.fromkeys(it.get("stories") or []) if i in by]
        iid = re.sub(r"[^a-z0-9-]", "", str(it.get("id", "")).lower())[:40]
        if not iid: log.append("an item without an id was dropped"); continue
        if not ids:
            if iid in old and it.get("status") == "ongoing":           # yesterday's thread, no new headlines: carried as it was
                out.append(dict(old[iid], status="ongoing")); continue
            log.append(f"{iid}: no valid stories; dropped"); continue
        texts = {k: str(it.get(k, "")).strip()[n] for k, n in (("title", slice(0, 90)), ("what", slice(0, 320)), ("why", slice(0, 320)),
                                                               ("who", slice(0, 120)), ("us", slice(0, 200)), ("watch", slice(0, 140)))}
        bad = set()
        for k in ("title", "what", "why"): bad |= wwscore.numbers_supported(texts[k], facts(ids))
        if bad: log.append(f"{iid}: numbers {sorted(bad)} not in its stories; dropped"); continue
        cat = it.get("category") if it.get("category") in CATEGORIES else None
        if not cat or not texts["title"] or not texts["what"]: log.append(f"{iid}: missing category/title/what; dropped"); continue
        sev = it.get("severity") if it.get("severity") in SEVERITY else "watch"
        merged = {"items": [x for i in ids for x in by[i]["items"]]}
        st = wwstories.stats(merged)
        prim = any(by[i]["primary"] for i in ids)
        if sev == "high" and not (prim or (st["independent"] >= 2 and len(st["countries"]) >= 2)):
            log.append(f"{iid}: 'high' needs a primary source or independent sources in 2 countries; set to elevated"); sev = "elevated"
        srcs, seen = [], set()
        for i in ids:
            for x in wwstories.pick_titles(by[i], 4):
                if x["u"] in seen: continue
                seen.add(x["u"])
                srcs.append({k: v for k, v in (("t", x["t"]), ("o", x.get("o", "")), ("u", x["u"]), ("s", x.get("s", "")),
                                                ("c", x.get("c", "")), ("l", x.get("l", "en")), ("p", x.get("p", 0)),
                                                ("r", "primary" if primary(x) else wwstories.tier(x))) if v not in ("", None)})
        first = old.get(iid, {}).get("first_seen", today)
        out.append({"id": iid, "category": cat, "category_label": CATEGORIES[cat], **texts,
                    "evidence": it.get("evidence") if it.get("evidence") in EVIDENCE else "reported", "severity": sev,
                    "status": it.get("status") if it.get("status") in ("new", "developing", "ongoing") else "developing",
                    "first_seen": first, "date": today, "sources": srcs[:5], "headlines": st["headlines"], "outlets": st["outlets"],
                    "independent": st["independent"], "countries": st["countries"], "primary": prim})
        if len(out) >= 8: break
    order = {s: i for i, s in enumerate(SEVERITY)}
    out.sort(key=lambda x: (order[x["severity"]], x["status"] != "new"))
    raw = str((ans or {}).get("summary", "")).strip()
    if len(raw) > 600: raw = raw[:600].rsplit(". ", 1)[0].rstrip(".") + "."     # whole sentences only
    summary, cut = wwscore.drop_unsupported_sentences(raw, facts(list(by)))
    if cut: log.append(f"{cut} summary sentence(s) with unbacked numbers removed")
    return summary, out

def stage(out, corpus_items, budget, model, system, today, log_fn):
    """Adds out['ai_risk']; on any failure keeps yesterday's."""
    prev = out.get("ai_risk")
    now = int(time.time())
    mine = ai_items(corpus_items, now)
    if len(mine) < 5:
        log_fn(f"AI risk: only {len(mine)} AI-risk headlines in 48 h; yesterday's kept"); return out
    stories = stories_for(mine)
    ans = wwapi.run([request(stories, prev, model, system, today)], budget)
    if not ans: log_fn("AI risk didn't come back; yesterday's kept"); return out
    text = ans["ai risk"][0]
    try:
        blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
        parsed = json.loads(blocks[-1] if blocks else text[text.find("{"): text.rfind("}") + 1])
    except ValueError as e:
        log_fn(f"AI risk: no JSON ({e}); yesterday's kept"); return out
    log = []
    summary, items = check(parsed, stories, prev, today, log)
    for line in log: log_fn("AI risk: " + line)
    if not items: log_fn("AI risk: nothing backed; yesterday's kept"); return out
    out["ai_risk"] = {"date": today, "summary": summary, "items": items,
                      "read": {"headlines": len(mine), "outlets": len({x.get("d") or x.get("s") for x in mine}),
                               "countries": len({x.get("c") for x in mine if x.get("c")}),
                               "languages": len({x.get("l", "en") for x in mine}), "stories": len(stories)},
                      "categories": CATEGORIES}
    return out
