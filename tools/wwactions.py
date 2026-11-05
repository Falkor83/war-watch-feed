"""
War Watch "Actions, not words": governments rarely say what they are preparing for, so War Watch keeps a log of what
they DO (stockpiling advice, shelters, reservist call-ups, embassy drawdowns, fuel and grain reserves, cable patrols,
hospital surge plans ...) next to a log of dated events (strikes, drone incursions, sabotage, cyberattacks, official
warnings), and once a day Opus reads both and says what the actions together point to — what is being done that is
not being said — next to the ordinary explanation and what would confirm or refute it.

- actions.json (main branch): the logs, seeded from config/actions_seed.json, grown daily from the worldwide headlines
  tagged 'action' by tools/headlines.py, and from the day's biggest stories (events, by rule).
- feed.json 'actions': the patterns, the summary and the recent actions for the app.
- feed.json 'tripwires': each action tied to a hotspot in the last 30 days becomes a warning sign there (source
  'action': tripped for 14 days, then 'signs'), so the app shows it on the hotspot page and alerts on it.
Standard library only.
"""
import json, os, re, time, datetime
import wwapi, wwdata, wwscore, wwstories

KINDS = {"civil-preparedness": "Civil preparedness", "citizens-abroad": "Citizens abroad", "military-readiness": "Military readiness",
         "health-surge": "Health surge", "supply-reserves": "Supply reserves", "infrastructure-protection": "Infrastructure protection",
         "continuity-of-government": "Continuity of government", "financial": "Financial", "reassurance": "Reassurance"}
SUPPORT = ("weak", "moderate", "strong")
BODIES = {"EU": "European Union", "NATO": "NATO", "UN": "United Nations"}
LOG_DAYS = 730

def cname(code): return BODIES.get(code) or wwdata.country_name(code)
def today_iso(): return datetime.date.today().isoformat()
def days(a, b): return wwscore.days_between(a, b)

def load_log(root):
    path = os.path.join(root, "actions.json")
    try: return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError): pass
    seed = json.load(open(os.path.join(root, "config", "actions_seed.json"), encoding="utf-8"))
    for a in seed["actions"]: a.setdefault("source", "seed")
    for e in seed["events"]: e.setdefault("source", "seed")
    return {"actions": seed["actions"], "events": seed["events"]}

def save_log(root, log, today):
    log["actions"] = sorted([a for a in log["actions"] if days(a["date"], today) <= LOG_DAYS], key=lambda a: a["date"])
    log["events"] = sorted([e for e in log["events"] if days(e["date"], today) <= LOG_DAYS], key=lambda e: e["date"])
    wwapi.save_json(os.path.join(root, "actions.json"), log)

def _date(ts): return datetime.datetime.fromtimestamp(ts or time.time(), datetime.timezone.utc).date().isoformat()

def candidate_stories(items, log, now, hours=72, limit=40):
    seen = {wwapi.norm_url(a.get("url", "")) for a in log["actions"]}
    mine = [x for x in items if "action" in (x.get("h") or []) and (x.get("p") or 0) >= now - hours * 3600 and x.get("t")
            and wwapi.norm_url(x.get("u", "")) not in seen]
    st = wwstories.cluster(mine)
    for s in st: s["parties"] = set(); s["stats"] = wwstories.stats(s)
    st.sort(key=lambda s: (-(2 * len(s["stats"]["countries"]) + min(s["stats"]["independent"], 4)), -s["stats"]["newest"]))
    return st[:limit], len(mine)

def add_events(log, feed, items, today, now, n=10):
    """The day's biggest stories become dated events (by rule: 4+ independent sources, 2+ outside regions)."""
    added = 0
    tags = None
    try:
        import wwboil
        tags = wwboil.assigned(feed["conflicts"], items)
    except Exception:
        tags = [[] for _ in items]
    recent = [(x, ids) for x, ids in zip(items, tags) if (x.get("p") or 0) >= now - 24 * 3600 and x.get("t") and ids]
    by_hot = {}
    for x, ids in recent: by_hot.setdefault(ids[0], []).append(x)
    cands = []
    for cid, xs in by_hot.items():
        c = next((k for k in feed["conflicts"] if k["id"] == cid), None)
        parties = {wwdata.iso(p) for p in (c or {}).get("parties") or []} - {""}
        for s in wwstories.cluster(xs):
            s["stats"] = wwstories.stats(s, parties)
            if s["stats"]["independent"] >= 4 and s["stats"]["outside_regions"] >= 2: cands.append((cid, s))
    cands.sort(key=lambda cs: -(cs[1]["stats"]["independent"] + 2 * cs[1]["stats"]["outside_regions"]))
    have = [(e["date"], wwstories.tokens(e["what"])) for e in log["events"] if days(e["date"], today) <= 3]
    for cid, s in cands:
        if added >= n: break
        lead = wwstories.pick_titles(s, 1)[0]
        tk = wwstories.tokens(lead["t"])
        if any(wwstories.similar(tk, t) >= 0.5 for _, t in have): continue
        k = sum(1 for e in log["events"] if e["date"] == today) + 1
        log["events"].append({"id": f"e-{today}-{k}", "date": _date(lead.get("p")), "kind": "news", "what": lead["t"][:200],
                              "countries": s["stats"]["countries"][:8], "hotspot": cid, "url": lead["u"], "source": "corpus",
                              "independent": s["stats"]["independent"]})
        have.append((today, tk)); added += 1
    for b in feed.get("boil") or []:
        if b.get("prev_stage") and b["stage"] != b["prev_stage"] and b.get("date") == today:
            log["events"].append({"id": f"e-{today}-ladder-{b['id']}"[:60], "date": today, "kind": "ladder",
                                  "what": f"Boiling point: {b['id']} moved from stage {b['prev_stage']} to {b['stage']} ({b['stage_label']})",
                                  "countries": [], "hotspot": b["id"], "url": "", "source": "rule"})
            added += 1
    return added

def request(stories, log, feed, prev, model, system, today):
    hot = "\n".join(f"{c['id']} | {c['name']} | {c['severity']} {c['trend']} | ladder "
                    f"{next((str(b['stage']) for b in feed.get('boil') or [] if b['id'] == c['id']), '-')}" for c in feed["conflicts"])
    acts = "\n".join(f"{a['id']} | {a['date']} | {a['country']} | {a['kind']} | scheduled={a.get('scheduled')} | {a['what']}"
                     + (f" | {', '.join(a.get('hotspots') or [])}" if a.get("hotspots") else "")
                     for a in log["actions"] if days(a["date"], today) <= LOG_DAYS)
    evs = "\n".join(f"{e['id']} | {e['date']} | {e['kind']} | {e['what'][:180]}" + (f" | {e['hotspot']}" if e.get("hotspot") else "")
                    for e in log["events"] if days(e["date"], today) <= LOG_DAYS)
    lines = []
    for i, s in enumerate(stories):
        st = s["stats"]
        lines.append(f"s{i + 1}: [{st['independent']} independent source{'s' if st['independent'] != 1 else ''} in "
                     f"{len(st['countries'])} countr{'ies' if len(st['countries']) != 1 else 'y'} ({', '.join(st['countries'][:8])})]\n" +
                     "\n".join(f"   - ({x.get('c') or '?'} {x.get('s', '')[:40]}, {_date(x.get('p'))}) {x['t'][:220]}" for x in wwstories.pick_titles(s, 3)))
    old = "\n".join(f"- {p['id']}: {p['title']} — {p.get('points_to', '')}" for p in (prev or {}).get("patterns") or []) or "none"
    user = (f"Today is {today}.\n\nHOTSPOTS\n{hot}\n\nTHE ACTION LOG\n{acts or 'empty'}\n\nEVENTS\n{evs or 'none'}\n\n"
            f"YESTERDAY'S PATTERNS\n{old}\n\nNEW STORIES (last 3 days)\n" + ("\n".join(lines) or "none") +
            "\n\nReturn the JSON described in your instructions.")
    return {"label": "actions", "model": model, "system": system, "user": user, "max_uses": 0, "max_tokens": 24000, "estimate": 0.2}

_CODE = re.compile(r"^[A-Z]{2}$")

def check(ans, stories, log, feed, today, msgs):
    """New actions into the log (checked), then patterns resting on logged actions. Returns (new actions, patterns, summary)."""
    ids = {c["id"] for c in feed["conflicts"]}
    by = {f"s{i + 1}": s for i, s in enumerate(stories)}
    new, story_action = [], {}
    for a in (ans.get("new_actions") if isinstance(ans, dict) else None) or []:
        if not isinstance(a, dict): continue
        s = by.get(a.get("story"))
        code = str(a.get("country", "")).strip().upper()
        kind = a.get("kind")
        what = str(a.get("what", "")).strip()[:140]
        if not s or kind not in KINDS or not what or not (_CODE.match(code) or code in BODIES):
            msgs.append(f"action from {a.get('story')!r} dropped (story, kind, country or text missing)"); continue
        if wwscore.numbers_supported(what, [{"fact": " ".join(x["t"] for x in s["items"][:15])}]):
            msgs.append(f"action from {a['story']} dropped (numbers not in its headlines)"); continue
        date = str(a.get("date", ""))[:10]
        newest = _date(s["stats"]["newest"])
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or date > today or days(date, today) > 30: date = newest
        tk = wwstories.tokens(what)
        if any(o["country"] == code and o["kind"] == kind and abs(days(o["date"], date)) <= 10 and
               wwstories.similar(tk, wwstories.tokens(o["what"])) >= 0.4 for o in log["actions"] + new):
            msgs.append(f"action '{what[:50]}' already logged"); continue
        lead = wwstories.pick_titles(s, 1)[0]
        n = 1
        while any(x["id"] == f"a-{date}-{code.lower()}-{n}" for x in log["actions"] + new): n += 1
        rec = {"id": f"a-{date}-{code.lower()}-{n}", "date": date, "country": code, "kind": kind, "what": what,
               "url": lead["u"], "outlet": lead.get("s", "")[:60], "hotspots": [h for h in a.get("hotspots") or [] if h in ids][:3],
               "scheduled": a.get("scheduled") if a.get("scheduled") in (True, False) else None,
               "program": str(a.get("program", ""))[:120], "source": "corpus", "added": today,
               "independent": s["stats"]["independent"], "countries_reporting": len(s["stats"]["countries"])}
        new.append(rec); story_action[a["story"]] = rec["id"]
    allacts = {a["id"]: a for a in log["actions"] + new}
    events = {e["id"]: e for e in log["events"]}
    pats = []
    for p in (ans.get("patterns") if isinstance(ans, dict) else None) or []:
        if not isinstance(p, dict): continue
        pid = re.sub(r"[^a-z0-9-]", "", str(p.get("id", "")).lower())[:40]
        refs = [story_action.get(r, r) for r in p.get("actions") or []]
        acts = [allacts[r] for r in dict.fromkeys(refs) if r in allacts]
        evs = [events[e] for e in dict.fromkeys(p.get("events") or []) if e in events]
        countries = sorted({a["country"] for a in acts})
        if not pid or len(acts) < 3 or len(countries) < 2:
            msgs.append(f"pattern {pid or '?'} dropped: needs 3 logged actions from 2 countries ({len(acts)}, {len(countries)})"); continue
        texts = {k: str(p.get(k, "")).strip()[:360] for k in ("points_to", "unsaid", "plain_explanation")}
        title = str(p.get("title", "")).strip()[:90]
        if not title or not texts["points_to"] or not texts["plain_explanation"]:
            msgs.append(f"pattern {pid} dropped: title, points_to and plain_explanation are required"); continue
        facts = [{"fact": a["what"] + " " + a["date"]} for a in acts] + [{"fact": e["what"] + " " + e["date"]} for e in evs]
        if any(wwscore.numbers_supported(t, facts) for t in [title] + list(texts.values())):
            msgs.append(f"pattern {pid} dropped: numbers not in its actions or events"); continue
        support = p.get("support") if p.get("support") in SUPPORT else "weak"
        sudden = [a for a in acts if a.get("scheduled") is False]
        if support == "strong" and not (len(acts) >= 5 and len(countries) >= 3 and sudden):
            msgs.append(f"pattern {pid}: 'strong' needs 5 actions in 3 countries incl. 1 not scheduled; set to moderate"); support = "moderate"
        if support == "moderate" and all(a.get("scheduled") is True for a in acts):
            msgs.append(f"pattern {pid}: every action was scheduled long before; set to weak"); support = "weak"
        dates = sorted(a["date"] for a in acts)
        pats.append({"id": pid, "title": title, **texts, "support": support,
                     "confirm": [str(x)[:140] for x in (p.get("confirm") or [])[:3]], "refute": [str(x)[:140] for x in (p.get("refute") or [])[:3]],
                     "hotspots": [h for h in p.get("hotspots") or [] if h in ids][:4], "countries": countries,
                     "window": {"from": dates[0], "to": dates[-1]},
                     "actions": [public_action(a) for a in sorted(acts, key=lambda a: a["date"])],
                     "events": [{k: e.get(k, "") for k in ("id", "date", "kind", "what", "url")} for e in sorted(evs, key=lambda e: e["date"])]})
        if len(pats) >= 6: break
    order = {s: i for i, s in enumerate(reversed(SUPPORT))}
    pats.sort(key=lambda p: (order[p["support"]], -len(p["actions"])))
    raw = str((ans or {}).get("summary", "")).strip()
    if len(raw) > 600: raw = raw[:600].rsplit(". ", 1)[0].rstrip(".") + "."
    summary, cut = wwscore.drop_unsupported_sentences(raw, [{"fact": a["what"] + " " + a["date"]} for a in allacts.values()] +
                                                      [{"fact": e["what"] + " " + e["date"]} for e in events.values()])
    if cut: msgs.append(f"{cut} summary sentence(s) with unbacked numbers removed")
    return new, pats, summary

def public_action(a):
    return {"id": a["id"], "date": a["date"], "country": a["country"], "country_name": cname(a["country"]), "kind": a["kind"],
            "kind_label": KINDS.get(a["kind"], a["kind"]), "what": a["what"], "url": a.get("url", ""), "outlet": a.get("outlet", ""),
            "scheduled": a.get("scheduled"), "program": a.get("program", ""), "hotspots": a.get("hotspots") or []}

def tripwires(feed, log, today):
    """Each action tied to a hotspot in the last 30 days is a warning sign there (not counted in the tension index)."""
    ids = {c["id"] for c in feed["conflicts"]}
    keep = [tw for tw in feed.get("tripwires") or [] if tw.get("source") != "action"]
    per = {}
    for a in sorted(log["actions"], key=lambda a: a["date"], reverse=True):
        age = days(a["date"], today)
        if a["kind"] == "reassurance" or age > 30 or age < 0: continue
        for h in a.get("hotspots") or []:
            if h not in ids or per.get(h, 0) >= 4: continue
            per[h] = per.get(h, 0) + 1
            home = a["country"]
            keep.append({"id": f"act-{a['id']}-{h}"[:90], "hotspot": h, "kind": "escalation",
                         "text": f"{cname(home)}: {a['what']}"[:110], "status": "tripped" if age <= 14 else "signs",
                         "since": a["date"], "last_seen": a["date"], "source": "action",
                         "why": "A government preparedness action tied to this hotspot (Actions, not words)" +
                                (f"; part of {a['program']}" if a.get("program") else ""),
                         "evidence": [{"date": a["date"], "outlet": a.get("outlet", ""), "country": cname(home), "url": a.get("url", ""),
                                       "region": wwdata.REGION_OF.get(home, "intl"), "tag": "non-party", "key": f"action:{a['id']}",
                                       "media": "unrated", "fact": a["what"]}] if a.get("url") else []})
    feed["tripwires"] = sorted(keep, key=lambda tw: (tw["hotspot"], tw["id"]))
    return sum(per.values())

def counts(log, today):
    out = {}
    for a in log["actions"]:
        if a["kind"] == "reassurance": continue
        for win in (30, 90):
            if 0 <= days(a["date"], today) <= win:
                k = out.setdefault(f"last_{win}", {"actions": 0, "countries": set()})
                k["actions"] += 1; k["countries"].add(a["country"])
    return {k: {"actions": v["actions"], "countries": sorted(v["countries"])} for k, v in out.items()}

def context_lines(log, today, days_back=60):
    """For the daily analyst (context, not citable)."""
    rows = [f"{a['date']} {a['country']} [{a['kind']}{', scheduled' if a.get('scheduled') else ''}] {a['what']}"
            for a in sorted(log["actions"], key=lambda a: a["date"]) if 0 <= days(a["date"], today) <= days_back]
    return "\n".join(rows) or "(none logged)"

def hotspot_line(log, cid, today, days_back=30):
    acts = [a for a in log["actions"] if cid in (a.get("hotspots") or []) and a["kind"] != "reassurance" and 0 <= days(a["date"], today) <= days_back]
    if not acts: return ""
    cs = sorted({cname(a["country"]) for a in acts})
    return (f"Government preparedness actions tied to this hotspot in the last {days_back} days: {len(acts)} "
            f"({len(cs)} countr{'ies' if len(cs) != 1 else 'y'}: {', '.join(cs[:8])}); latest {max(a['date'] for a in acts)}.")

def stage(out, corpus_items, budget, model, system, today, root, log_fn):
    log = load_log(root)
    now = int(time.time())
    ev = add_events(log, out, corpus_items, today, now)
    stories, n_items = candidate_stories(corpus_items, log, now)
    prev = out.get("actions")
    new, pats, summary = [], None, None
    ans = wwapi.run([request(stories, log, out, prev, model, system, today)], budget)
    if ans:
        text = ans["actions"][0]
        try:
            blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
            parsed = json.loads(blocks[-1] if blocks else text[text.find("{"): text.rfind("}") + 1])
            msgs = []
            new, pats, summary = check(parsed, stories, log, out, today, msgs)
            for m in msgs: log_fn("actions: " + m)
        except ValueError as e:
            log_fn(f"actions: no JSON ({e}); yesterday's patterns kept")
    else:
        log_fn("actions didn't come back; yesterday's patterns kept")
    log["actions"] += new
    flags = tripwires(out, log, today)
    recent = [public_action(a) for a in sorted(log["actions"], key=lambda a: a["date"], reverse=True) if days(a["date"], today) <= 120][:40]
    if pats is None:
        result = dict(prev or {}, recent=recent, counts=counts(log, today))
    else:
        result = {"date": today, "summary": summary or "", "patterns": pats, "recent": recent, "counts": counts(log, today),
                  "read": {"headlines": n_items, "stories": len(stories), "logged_actions": len(log["actions"]), "logged_events": len(log["events"])},
                  "kinds": KINDS}
    out["actions"] = result
    save_log(root, log, today)
    return {"new_actions": len(new), "events": ev, "patterns": len(pats or []), "flags": flags}
