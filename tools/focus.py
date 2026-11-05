"""
War Watch focus updates: a hotspot Matt asked to follow closely (config/focus.json) gets an update every few hours.

Claude (Haiku 5.5) reads the last hours of worldwide headlines about it (all languages, translated), grouped into
stories with who reported each counted by rule, and writes what changed. Python checks every citation and number,
attaches the outlets and countries behind each point, refreshes the hotspot's status line and latest developments in
feed.json (so the app shows it at once) and keeps a short history. When the update spots a concrete step up reported
by independent sources in two countries, Opus re-checks the boiling-point ladder for that hotspot (at most every 12 h).
A focus ends by itself when the hotspot calms: yellow, not escalating, ladder at Posturing or below for 48 hours.

  python3 tools/focus.py [--now]   work out due updates, save them to .focus_out.json and apply them to feed.json
  python3 tools/focus.py --apply   apply .focus_out.json again (after tools/push_main.sh reset to a newer main)
  python3 tools/focus.py --keep FILE   copy the focus parts of FILE (GitHub's newer feed.json) into feed.json
Costs are booked in focus_budget.json, which counts toward War Watch's monthly cap.
"""
import json, os, re, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wwapi, wwdata, wwscore, wwboil, wwstories
from wwapi import step, ok, warn, err, save_json

ROOT = wwapi.ROOT
LIVE = os.environ.get("WW_LIVE_DIR", os.path.join(ROOT, "live"))
FEED = os.path.join(ROOT, "feed.json")
CFG = os.path.join(ROOT, "config", "focus.json")
OUT = os.environ.get("WW_FOCUS_OUT", os.path.join(ROOT, ".focus_out.json"))
FOCUS_BUDGET = os.path.join(ROOT, "focus_budget.json")
MODEL = os.environ.get("WW_FOCUS_MODEL", "claude-haiku-5-5")
CHECK_MODEL = os.environ.get("WW_ANALYST_MODEL", "claude-opus-5-5")
SUB_CAP = float(os.environ.get("WW_FOCUS_CAP_USD", "3"))      # focus updates + ladder re-checks, inside War Watch's cap
CALM_HOURS = 48
RECHECK_HOURS = 12

SYSTEM = """You write a short update on one war hotspot for a news-monitoring app, from worldwide headlines of the last
few hours (translated to English). Each STORY (s1, s2 ...) groups headlines about one event and says, counted by rule,
how many independent sources in how many countries and regions reported it, and whether ONLY the sides' own media did.

Return one ```json block:
{"headline": "max 90 characters: where things stand right now",
 "direction": "escalating" | "steady" | "easing"   (compared with the previous update),
 "points": [{"text": "one or two plain sentences on what happened; attribute claims ('Iran says', 'US officials say')",
             "stories": ["s3", "s7"]}],
 "watch": ["up to 3 short things to watch in the next hours"],
 "flag": {"step_up": true or false, "why": "one sentence", "stories": ["s.."]}}

Rules:
- Use only stories about this hotspot: its fighting, attacks, diplomacy, nuclear sites, shipping and oil, and moves by
  the sides' governments about the war. Ignore stories that only mention a side (other elections, trade, sport).
- 2 to 5 points, most important first, about what is NEW since the previous update. If little changed, say so plainly
  in the headline and in one point.
- Cite only story ids you were given; every number you write must appear in a story you cite.
- A story reported ONLY by the sides' own media is that side's claim: say whose claim it is.
- Prefer stories confirmed by independent sources in several regions; when sides' accounts differ, give both.
- Plain words; no alarm words (imminent, catastrophic, devastating); never write that something "will" happen.
- "step_up" is true only for a concrete escalation event (strikes on a new country or on nuclear sites, deaths of US or
  allied forces, a strait or airspace closed, a mobilisation), not for threats or rhetoric."""

def now_ts(): return int(time.time())
def iso(ts): return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def ts_of(s):
    try: return int(datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except ValueError: return 0
def load(p, d):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return d

def extract(text):
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
    for cand in reversed(blocks):
        try: return json.loads(cand)
        except ValueError: pass
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i: return json.loads(text[i:j + 1])
    raise ValueError("no JSON in the answer")

def calm(c, b):
    return c["severity"] == "yellow" and c["trend"] != "escalating" and (b is None or b.get("stage", 0) <= 2)

def stamp(x): return x.get("p") or 0

def window_items(c, items, conflicts, since_ts):
    m = wwboil.CorpusMatcher(conflicts)
    return [x for x in items if stamp(x) >= since_ts and x.get("t") and c["id"] in m.assign(x)]

def make_stories(c, mine, limit=40):
    parties = {wwdata.iso(p) for p in c.get("parties") or []} - {""}
    stories = wwstories.cluster(mine)
    for s in stories: s["parties"] = parties; s["stats"] = wwstories.stats(s, parties)
    stories.sort(key=lambda s: (-(s["stats"]["independent"] + 2 * s["stats"]["outside_regions"]), -s["stats"]["newest"]))
    return stories[:limit]

def build_prompt(c, stories, prev, hours):
    lines = []
    for i, s in enumerate(stories):
        lines.append(f"s{i + 1}: [{wwstories.describe(s['stats'])}]\n" + "\n".join(
            f"   - ({x.get('c') or '?'}{'*' if x.get('c') in s['parties'] else ''} {x.get('s', '')[:40]}) {x['t'][:220]}"
            for x in wwstories.pick_titles(s, 3)))
    prev_txt = (f"Previous update ({prev['updated_at']}): {prev.get('headline', '')} | " +
                " / ".join(p["text"] for p in prev.get("points", [])[:4])) if prev and prev.get("updated_at") else "No previous update."
    return (f"Hotspot: {c['name']} (sides: {', '.join(c.get('parties') or [])}; rated {c['severity']}, {c['trend']}).\n"
            f"Daily rating's status line: {c.get('status', '')}\n{prev_txt}\n\n"
            f"STORIES from the last {hours} hours (* = outlet from a country involved):\n" + "\n".join(lines) +
            "\n\nReturn the JSON described in your instructions.")

def check(ans, stories, log):
    """The model's update -> what the app shows: valid citations, numbers backed, sources and counts attached."""
    by = {f"s{i + 1}": s for i, s in enumerate(stories)}
    def facts(ids): return [{"fact": " ".join(x["t"] for x in by[i]["items"][:15])} for i in ids if i in by]
    pts = []
    for p in (ans.get("points") or [])[:5]:
        if not isinstance(p, dict): continue
        ids = [i for i in dict.fromkeys(p.get("stories") or []) if i in by]
        text = str(p.get("text", "")).strip()[:320]
        if not ids or not text: log.append("a point without valid stories was dropped"); continue
        bad = wwscore.numbers_supported(text, facts(ids))
        if bad: log.append(f"a point with unbacked numbers {sorted(bad)} was dropped"); continue
        st = wwstories.stats({"items": [x for i in ids for x in by[i]["items"]]}, by[ids[0]]["parties"])
        srcs, seen = [], set()
        for i in ids:
            for x in wwstories.pick_titles(by[i], 4):
                if x["u"] in seen: continue
                seen.add(x["u"])
                srcs.append({k: v for k, v in (("t", x["t"]), ("o", x.get("o", "")), ("u", x["u"]), ("s", x.get("s", "")),
                                                ("c", x.get("c", "")), ("l", x.get("l", "en")), ("p", x.get("p", 0)),
                                                ("r", wwstories.tier(x))) if v not in ("", None)})
        pts.append({"text": text, "stories": ids, "sources": srcs[:5], "headlines": st["headlines"], "outlets": st["outlets"],
                    "independent": st["independent"], "countries": st["countries"], "regions": st["regions"],
                    "party_only": st["party_only"], "state": st["state"]})
    headline = str(ans.get("headline", "")).strip()[:90]
    if headline and wwscore.numbers_supported(headline, facts(list(by))): log.append("headline had unbacked numbers"); headline = ""
    flag = ans.get("flag") if isinstance(ans.get("flag"), dict) else {}
    fids = [i for i in flag.get("stories") or [] if i in by]
    step_up = bool(flag.get("step_up")) and any(
        by[i]["stats"]["outside_outlets"] >= 2 and len(set(by[i]["stats"]["countries"]) - by[i]["parties"]) >= 2 for i in fids)
    if flag.get("step_up") and not step_up: log.append("step-up flag not backed by independent outlets in 2 countries; ignored")
    return {"headline": headline,
            "direction": ans.get("direction") if ans.get("direction") in ("escalating", "steady", "easing") else "steady",
            "points": pts, "watch": [str(w)[:140] for w in (ans.get("watch") or [])[:3] if str(w).strip()],
            "step_up": step_up, "step_up_why": str(flag.get("why", ""))[:200] if step_up else ""}

def apply_to_hotspot(c, upd, feed_generated, today):
    """The app shows the hotspot's status line and latest developments: refresh them (a newer daily rating wins)."""
    if upd["headline"] and ts_of(upd["at"]) >= ts_of(feed_generated): c["status"] = upd["headline"][:120]
    have = {wwapi.norm_url(d["url"]) for d in c.get("latest") or [] if d.get("url")}
    have_txt = {d.get("text") for d in c.get("latest") or []}
    new = []
    for p in upd["points"][:3]:
        if p["text"] in have_txt: continue
        src = next((s for s in p["sources"] if wwapi.norm_url(s["u"]) not in have), None)
        if not src: continue
        home = src.get("c", "")
        new.append({"date": today, "text": p["text"][:400], "outlet": src.get("s", "")[:60],
                    "country": wwdata.country_name(home) if home else "", "url": src["u"],
                    "region": wwdata.REGION_OF.get(home, "unknown"),
                    "tag": "party-press" if p["party_only"] else "non-party", "state_media": src.get("r") == "state"})
        have.add(wwapi.norm_url(src["u"]))
    c["latest"] = (new + (c.get("latest") or []))[:8]

def recheck_ladder(feed, cid, items, budget):
    """Opus re-reads the ladder for this one hotspot. No GDELT (too slow for a quick check): the daily check's GDELT
    tiles are passed on as data points. Moves at most one step, like the daily check."""
    t = datetime.date.today().isoformat()
    c = next(x for x in feed["conflicts"] if x["id"] == cid)
    prev = {b["id"]: b for b in feed.get("boil") or []}
    pts, tiles, mine = wwboil.build_panel(c, items, wwboil.Sources(t), now_ts(), None, first_id=1, conflicts=feed["conflicts"])
    kinds = {x["kind"] for x in tiles}
    for old in (prev.get(cid) or {}).get("tiles") or []:
        if old.get("kind") in kinds or old.get("kind") not in ("coverage", "tone", "escalation_words", "talk_words"): continue
        did = f"d{len(pts) + 1}"
        pts.append({"id": did, "kind": old["kind"], "text": f"{old['label']} (from the daily check): {old['value']}; {old.get('note', '')}"})
        tiles.append(dict(old, id=did))
    parties = {wwdata.iso(p) for p in c.get("parties") or []} - {""}
    heads = [dict(h, hid=f"h{i + 1}", party=h.get("c") in parties) for i, h in enumerate(wwboil.pick_headlines(mine, parties))]
    system = open(os.path.join(ROOT, "prompt", "rules.md"), encoding="utf-8").read() + "\n\n" + \
             open(os.path.join(ROOT, "prompt", "boil.md"), encoding="utf-8").read()
    req = wwboil.boil_request([cid], feed, {cid: pts}, {cid: heads}, {}, prev, CHECK_MODEL, system, t)
    req["label"] = "boiling point re-check"
    ans = wwapi.run([req], budget, batch=False)
    if not ans: warn("the ladder re-check didn't come back"); return None
    try: parsed = extract(ans[req["label"]][0])
    except ValueError as e: warn(f"ladder re-check: {e}"); return None
    log = []
    got = wwboil.check(parsed, [cid], {cid: pts}, {cid: heads}, {}, prev, t, log)
    for line in log: warn("ladder re-check: " + line)
    if not got: return None
    item = wwboil.public(got[0], tiles, heads, [])
    old = prev.get(cid) or {}
    item.update(score=old.get("score", 0), history=old.get("history", []), recheck=iso(now_ts()))
    ok(f"ladder re-check: {cid} stage {item['stage']} ({item['stage_label']}), {item['likelihood'] or 'no likelihood'}, {item['direction']}")
    return item

# ---------- applying (also on top of a newer main) ----------
def load_focus_budget():
    month = datetime.date.today().isoformat()[:7]
    b = load(FOCUS_BUDGET, {})
    if b.get("month") != month: b = {"month": month, "usd": 0.0, "runs": []}
    return b

def apply(out):
    feed = load(FEED, None)
    if not feed: raise SystemExit("[ERROR] feed.json missing")
    state = {f["hotspot"]: f for f in feed.get("focus") or []}
    t = datetime.date.today().isoformat()
    for cid, st in out["state"].items():
        if ts_of(state.get(cid, {}).get("updated_at")) > ts_of(st.get("updated_at")) and st.get("status") != "ended":
            continue                                         # a newer update landed meanwhile
        state[cid] = st
        upd = out["updates"].get(cid)
        c = next((x for x in feed["conflicts"] if x["id"] == cid), None)
        if upd and c: apply_to_hotspot(c, upd, feed.get("generated_at", ""), t)
    for item in out.get("boil") or []:
        if any(x["id"] == item["id"] for x in feed["conflicts"]):
            feed["boil"] = [b for b in feed.get("boil") or [] if b["id"] != item["id"]] + [item]
            feed["boil"].sort(key=lambda b: (-b["stage"], b["direction"] != "rising", -b.get("score", 0)))
    feed["focus"] = list(state.values())
    save_json(FEED, feed)
    b = load_focus_budget()
    if out.get("month") == b["month"] and out.get("runs"):
        b["usd"] = round(b["usd"] + out["spent"], 4)
        b["runs"] = (b.get("runs") or [])[-80:] + out["runs"]
    b["cap"] = SUB_CAP
    save_json(FOCUS_BUDGET, b)
    ok(f"feed.json: focus on {', '.join(cid for cid in out['state'])} applied")

def keep(theirs_path):
    """The daily ratings job is about to push over a feed.json that focus updates changed meanwhile: keep those."""
    theirs, feed = load(theirs_path, {}), load(FEED, None)
    if not feed or not theirs.get("focus"): return
    feed["focus"] = theirs["focus"]
    mine = {b["id"]: b for b in feed.get("boil") or []}
    for b in theirs.get("boil") or []:
        if b.get("recheck") and ts_of(b["recheck"]) > ts_of(feed.get("generated_at")) and b["id"] in mine: mine[b["id"]] = b
    if feed.get("boil"): feed["boil"] = sorted(mine.values(), key=lambda b: (-b["stage"], b["direction"] != "rising", -b.get("score", 0)))
    save_json(FEED, feed)
    ok("kept the focus updates made while the ratings ran")

# ---------- working out the updates ----------
def compute(force):
    cfg = load(CFG, {"focus": []})
    feed = load(FEED, None)
    if not feed: raise SystemExit("[ERROR] feed.json missing")
    corpus = load(os.path.join(LIVE, "headlines.json"), {"items": []}) or {"items": []}
    items = corpus.get("items") or []
    run_budget = os.path.join(ROOT, ".focus_run_budget.json")
    if os.path.exists(run_budget): os.remove(run_budget)
    wwapi.BUDGET = run_budget                            # this run's costs; added to focus_budget.json by apply()
    budget = wwapi.load_budget()
    fb = load_focus_budget()
    month = fb["month"]
    main_b = load(os.path.join(ROOT, "budget.json"), {})
    hs = load(os.path.join(LIVE, "spend.json"), {})
    total = sum(x.get("usd", 0) for x in (main_b, hs) if x.get("month") == month) + fb["usd"]
    now, t = now_ts(), datetime.date.today().isoformat()
    state = {f["hotspot"]: f for f in feed.get("focus") or []}
    out = {"month": month, "state": {}, "updates": {}, "boil": [], "spent": 0.0, "runs": []}
    for fc in cfg.get("focus") or []:
        cid, every = fc["hotspot"], float(fc.get("every_hours", 3))
        st = dict(state.get(cid) or {"hotspot": cid, "since": iso(now), "history": []})
        st.update(every_hours=every, why=fc.get("why", st.get("why", "")), name=fc.get("name", st.get("name", cid)))
        if st.get("status") == "ended" and not fc.get("restart"): continue
        c = next((x for x in feed["conflicts"] if x["id"] == cid), None)
        if c is None:
            st.update(status="ended", ended_at=iso(now), ended_reason="the hotspot is no longer on the map")
            out["state"][cid] = st; continue
        b = next((x for x in feed.get("boil") or [] if x["id"] == cid), None)
        if calm(c, b):
            st.setdefault("calm_since", iso(now))
            if now - ts_of(st["calm_since"]) >= CALM_HOURS * 3600:
                st.update(status="ended", ended_at=iso(now),
                          ended_reason=f"it calmed: yellow, not escalating, ladder at Posturing or below for {CALM_HOURS} hours")
                ok(f"{cid}: focus ended ({st['ended_reason']})"); out["state"][cid] = st; continue
        else: st.pop("calm_since", None)
        last = ts_of(st.get("updated_at"))
        if not force and now - last < every * 3600 - 20 * 60:
            ok(f"{cid}: last update {round((now - last) / 60)} min ago; next due in {round((every * 3600 - (now - last)) / 60)} min"); continue
        if total + budget["usd"] >= wwapi.MONTHLY_CAP_USD - 1:
            warn(f"{cid}: War Watch is at ${total:.2f} of ${wwapi.MONTHLY_CAP_USD:.0f} this month; focus update skipped"); continue
        if fb["usd"] + budget["usd"] >= SUB_CAP:
            warn(f"{cid}: focus updates reached their ${SUB_CAP:.0f} monthly limit; skipped"); continue
        hours = max(3, min(12, round((now - last) / 3600))) if last else 6
        mine = window_items(c, items, feed["conflicts"], now - hours * 3600)
        if len(mine) < 15: hours = 12; mine = window_items(c, items, feed["conflicts"], now - hours * 3600)
        stories = make_stories(c, mine)
        step(f"{cid}: focus update from {len(mine)} headlines of the last {hours} h ({len(stories)} stories; corpus {corpus.get('generated', '?')})")
        if len(stories) < 2: warn(f"{cid}: too few headlines to update from"); continue
        req = {"label": "focus update", "model": MODEL, "system": SYSTEM, "user": build_prompt(c, stories, st, hours),
               "max_uses": 0, "max_tokens": 16000, "estimate": 0.02}
        ans = wwapi.run([req], budget, batch=False)
        if not ans: warn(f"{cid}: the update didn't come back"); continue
        try: parsed = extract(ans["focus update"][0])
        except ValueError as e: warn(f"{cid}: {e}"); continue
        log = []
        upd = check(parsed, stories, log)
        for line in log: warn(f"{cid}: {line}")
        if not upd["points"]: warn(f"{cid}: nothing in the update was backed; skipped"); continue
        upd["at"] = iso(now)
        st.update(status="active", updated_at=iso(now), next_at=iso(now + int(every * 3600)), headline=upd["headline"],
                  direction=upd["direction"], points=upd["points"], watch=upd["watch"], window_hours=hours,
                  headlines_read=len(mine), outlets_read=len({x.get("d") or x.get("s") for x in mine}),
                  countries_read=len({x.get("c") for x in mine if x.get("c")}), languages_read=len({x.get("l", "en") for x in mine}),
                  model="Claude Haiku 5.5")
        st["history"] = (st.get("history") or [])[-23:] + [{"at": iso(now), "headline": upd["headline"], "direction": upd["direction"]}]
        out["updates"][cid] = upd
        ok(f"{cid}: {upd['headline']} ({upd['direction']}; {len(upd['points'])} points)")
        if upd["step_up"]:
            if now - ts_of(st.get("ladder_checked_at")) < RECHECK_HOURS * 3600:
                ok(f"{cid}: possible step up, but the ladder was re-checked within {RECHECK_HOURS} h")
            elif fb["usd"] + budget["usd"] + 0.4 > SUB_CAP:
                warn(f"{cid}: possible step up; no money left this month for a ladder re-check")
            else:
                step(f"{cid}: possible step up ({upd['step_up_why']}); Opus re-checks the ladder")
                item = recheck_ladder(feed, cid, items, budget)
                if item:
                    out["boil"].append(item); st["ladder_checked_at"] = iso(now)
                    st["ladder_note"] = f"Re-checked after: {upd['step_up_why']}"
        out["state"][cid] = st
    out["spent"] = round(budget["usd"], 4)
    out["runs"] = budget.get("runs") or []
    if os.path.exists(run_budget): os.remove(run_budget)
    return out

def main():
    if "--keep" in sys.argv: keep(sys.argv[sys.argv.index("--keep") + 1]); return
    if "--apply" in sys.argv:
        out = load(OUT, None)
        if out and out["state"]: apply(out)
        return
    out = compute("--now" in sys.argv)
    save_json(OUT, out)
    if out["state"]: apply(out)
    else: ok("no focus update due")
    print(f"focus spend this run ${out['spent']:.3f}")

if __name__ == "__main__":
    main()
