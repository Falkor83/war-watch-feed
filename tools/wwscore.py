"""
War Watch scoring: everything the app shows as a measure is computed here by fixed rules, never by the model.

- signals: evidence stats from the cited notes (independent sources, non-party regions, freshness, contested),
  a 0-5 evidence meter, a confidence cap, the numbers check and the signal lifecycle;
- frames ("where outlets differ") and tripwires (escalation and easing signs, with evidence rules and decay);
- the world tension index (0-100) and its parts, history.json, the change log and the one-line week summary.
Standard library only.
"""
import datetime, math, re
from wwdata import COUNTRIES, REGION_OF, REGION_NAMES, country_name

def d(s):
    try: return datetime.date.fromisoformat(str(s)[:10])
    except ValueError: return None

def days_between(a, b):
    da, db = d(a), d(b)
    return (db - da).days if da and db else 999

# ---------- numbers check ----------
_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?:\s?(%|percent|per cent|million|billion|bn|m|k))?", re.I)
_MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"

def numbers_in(text):
    """Numbers that make a claim (casualties, prices, counts, percentages), not dates or ordinary small words.
    Percentages and millions/billions keep their unit: '12%' is only backed by a 12%."""
    t = re.sub(_MONTHS + r"\s+\d{1,2}(st|nd|rd|th)?(,?\s+\d{4})?", " ", str(text), flags=re.I)
    t = re.sub(r"\b\d{1,2}(st|nd|rd|th)?\s+" + _MONTHS + r"(\s+\d{4})?", " ", t, flags=re.I)
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)                 # years
    t = re.sub(r"\b\d{1,2}:\d{2}\b", " ", t)                # times
    out = set()
    for m in _NUM.finditer(t):
        n = m.group(1).replace(",", "").rstrip(".")
        unit = (m.group(2) or "").lower()
        if not n or (n in ("0", "1", "2", "3") and not unit): continue   # one, two, three: too common to check
        unit = {"percent": "%", "per cent": "%", "bn": "billion", "m": "million", "k": "", "": ""}.get(unit, unit)
        out.add(n + unit)
    return out

def numbers_supported(text, notes):
    """The claim numbers in the text that no cited note backs (empty set = all backed)."""
    have = set()
    for n in notes:
        have |= numbers_in(n["fact"])
        have |= {x for x in re.findall(r"\d+(?:\.\d+)?", n["fact"].replace(",", ""))}
    def backed(x):
        if x in have: return True
        if x[-1:].isdigit(): return x.split(".")[0] in have
        return False
    return {x for x in numbers_in(text) if not backed(x)}

def drop_unsupported_sentences(text, notes):
    """Remove sentences whose numbers no cited note backs. Returns (text, removed_count)."""
    parts = re.split(r"(?<=[.!?])\s+", str(text).strip())
    kept = [p for p in parts if not numbers_supported(p, notes)]
    return " ".join(kept), len(parts) - len(kept)

# ---------- evidence ----------
def evidence_stats(for_notes, against_notes, today):
    keys_all = {n["key"] for n in for_notes}
    nonparty = [n for n in for_notes if n["tag"] == "non-party"]
    keys_np = {n["key"] for n in nonparty}
    press_keys = {n["key"] for n in for_notes if n["tag"] == "party-press"} - keys_np
    regions_np = {n["region"] for n in nonparty if n["region"] not in ("unknown", "")}
    regions_all = {n["region"] for n in for_notes if n["region"] not in ("unknown", "")}
    newest = min((days_between(n["date"], today) for n in for_notes), default=999)
    contested = any(n["tag"] in ("non-party", "party-press") for n in against_notes)
    dots = min(5, len(keys_np) + len(press_keys) // 2)
    if len(regions_np) < 2: dots = min(dots, 2)
    if newest > 3: dots -= 1
    if contested: dots -= 1
    dots = max(0, dots)
    label = "Strong" if dots >= 5 else "Solid" if dots >= 3 else "Thin" if dots == 2 else "Weak"
    homes = []
    for n in for_notes:
        if n["home"] in COUNTRIES and n["home"] != "UN" and n["home"] not in homes: homes.append(n["home"])
    big = ["north-america", "europe", "russia", "china", "gulf", "middle-east", "south-asia", "africa", "latin-america"]
    return {"keys_all": len(keys_all), "keys_nonparty": len(keys_np), "regions_np": len(regions_np),
            "newest_days": newest if newest < 999 else None, "contested": contested, "dots": dots, "label": label,
            "regions": sorted(regions_all), "not_heard": [r for r in big if r not in regions_all][:4],
            "countries_iso": homes}

def confidence_cap(stats, model_conf):
    if stats["keys_nonparty"] >= 3 and stats["regions_np"] >= 3 and (stats["newest_days"] or 99) <= 2 and not stats["contested"]:
        cap = "high"
    elif stats["keys_all"] >= 2 and stats["regions_np"] >= 2: cap = "medium"
    else: cap = "low"
    order = ["low", "medium", "high"]
    m = model_conf if model_conf in order else "medium"
    return order[min(order.index(m), order.index(cap))]

def source_ref(n):
    return {"date": n["date"], "outlet": n["outlet"], "country": country_name(n["home"]) if n["home"] else "",
            "url": n["url"], "region": n["region"], "tag": n["tag"], "key": n["key"], "media": n["media"],
            "fact": n.get("fact", "")[:300]}

def score_signal(g, notes_by_id, prev_by_id, today, ids, log):
    """A model signal -> a checked, scored signal, or None (reason logged)."""
    if not isinstance(g, dict): return None
    sid = re.sub(r"[^a-z0-9-]+", "-", str(g.get("id", "")).lower()).strip("-")[:60] or None
    title = str(g.get("title", ""))[:140]
    fors = [notes_by_id[x] for x in (g.get("for") or []) if x in notes_by_id]
    against = [notes_by_id[x] for x in (g.get("against") or []) if x in notes_by_id]
    if not sid or len(title) < 8: log("signal without id/title dropped"); return None
    stats = evidence_stats(fors, against, today)
    fresh = any(days_between(n["date"], today) <= 7 for n in fors)
    if stats["keys_all"] < 2 or stats["regions_np"] < 2 or not fresh:
        log(f"signal {sid}: dropped ({stats['keys_all']} independent sources, {stats['regions_np']} non-party regions, fresh={fresh})")
        return None
    if numbers_supported(title, fors): log(f"signal {sid}: title has a number no note backs; dropped"); return None
    claim, c1 = drop_unsupported_sentences(str(g.get("claim", ""))[:400], fors)
    detail, c2 = drop_unsupported_sentences(str(g.get("detail", ""))[:1200], fors)
    if c1 or c2: log(f"signal {sid}: removed {c1 + c2} sentence(s) with numbers no note backs")
    if len(claim) < 20 and len(detail) < 60: log(f"signal {sid}: nothing left after checks"); return None
    direction = g.get("direction") if g.get("direction") in ("escalating", "easing", "mixed", "steady") else "mixed"
    prev = prev_by_id.get(sid)
    status = "new"
    if prev:
        ps = prev.get("stats") or {}
        if prev.get("direction") != direction: status = "flipped"
        elif stats["keys_all"] >= ps.get("keys_all", 0) + 2 or stats["regions_np"] >= ps.get("regions_np", 0) + 1: status = "stronger"
        elif stats["keys_all"] <= ps.get("keys_all", 0) - 2 or stats["regions_np"] <= ps.get("regions_np", 0) - 1: status = "weaker"
        else: status = "same"
    conf = confidence_cap(stats, str(g.get("confidence", "medium")).lower())
    srcs = sorted((source_ref(n) for n in fors), key=lambda s: s["date"], reverse=True)
    return {"id": sid, "title": title, "claim": claim, "detail": detail or claim, "direction": direction,
            "confidence": conf, "status": status, "first_seen": (prev or {}).get("first_seen", today),
            "last_confirmed": today, "stats": stats,
            "against": [source_ref(n) for n in against][:4],
            "other_reading": str(g.get("other_reading", ""))[:300], "would_change_view": str(g.get("would_change_view", ""))[:300],
            "tripwires": [str(x)[:60] for x in (g.get("tripwires") or [])][:4],
            # schema 1 fields, still read by app 0.1.0: countries are now counted, not claimed
            "countries": [country_name(c) for c in stats["countries_iso"]][:12],
            "conflicts": [x for x in (g.get("hotspots") or g.get("conflicts") or []) if x in ids],
            "sources": srcs[:8]}

def carry_signal(old, today):
    """Yesterday's signal with no new evidence today: kept as 'fading' for up to 14 days."""
    age = days_between(old.get("last_confirmed", old.get("first_seen", today)), today)
    if age > 14: return None
    s = dict(old); s["status"] = "fading" if age >= 7 else old.get("status", "same")
    return s

# ---------- frames ----------
def check_frame(f, notes_by_id, ids, today):
    if not isinstance(f, dict) or f.get("hotspot") not in ids: return None
    versions = []
    for v in f.get("versions") or []:
        if not isinstance(v, dict): continue
        region = str(v.get("region", ""))
        ns = [notes_by_id[x] for x in (v.get("notes") or []) if x in notes_by_id]
        ns = [n for n in ns if n["region"] == region] or ([n for n in ns] if region not in REGION_NAMES else [])
        if not ns or not v.get("says"): continue
        if region not in REGION_NAMES: region = ns[0]["region"]
        versions.append({"region": region, "region_name": REGION_NAMES.get(region, region), "says": str(v["says"])[:140],
                         "party": any(n["tag"] == "party-gov" for n in ns), "sources": [source_ref(n) for n in ns][:3]})
    if len({v["region"] for v in versions}) < 2: return None
    return {"id": re.sub(r"[^a-z0-9-]+", "-", str(f.get("id", "")).lower()).strip("-")[:60] or "frame",
            "date": today, "hotspot": f["hotspot"], "topic": str(f.get("topic", ""))[:120], "agreed": str(f.get("agreed", ""))[:240],
            "versions": versions[:4], "unresolved": str(f.get("unresolved", ""))[:240]}

# ---------- tripwires ----------
TW_STATUS = ("not_seen", "signs", "tripped")

def tripwire_update(tw, upd, notes_by_id, today, log):
    """Apply one model update under the evidence rules: signs needs 1 note; tripped needs 2 independence keys with at
    least one not a party's own account (a party's claim counts only when it is against its own interest)."""
    want = upd.get("status")
    ns = [notes_by_id[x] for x in (upd.get("notes") or []) if x in notes_by_id]
    if want not in TW_STATUS or not ns: return False
    if want == "tripped":
        keys = {n["key"] for n in ns}
        independent = any(n["tag"] == "non-party" or (n["tag"] == "party-gov" and n.get("against_interest")) for n in ns)
        if len(keys) < 2 or not independent:
            log(f"tripwire {tw['id']}: 'tripped' needs 2 independent sources incl. 1 not from a party; kept as signs")
            want = "signs"
    if want == "not_seen": return False                     # only time clears a tripwire
    if want != tw["status"]: tw["since"] = today
    tw["status"] = want
    tw["last_seen"] = today
    tw["why"] = str(upd.get("why", ""))[:240]
    tw["evidence"] = [source_ref(n) for n in ns][:4]
    return True

def tripwire_decay(tw, today):
    age = days_between(tw.get("last_seen", tw.get("since", today)), today)
    if tw["status"] == "signs" and age > 7: tw["status"] = "not_seen"; tw["since"] = today
    if tw["status"] == "tripped" and age > 14: tw["status"] = "not_seen"; tw["since"] = today; tw["was_tripped"] = tw.get("last_seen")

def tripwire_weight(tw, today):
    if tw["status"] == "signs": return 0.4
    if tw["status"] == "tripped":
        return 1.0 if days_between(tw.get("last_seen", today), today) <= 7 else 0.5
    return 0.0

# ---------- tension index ----------
def tension(conflicts, tripwires, lists, today, history):
    """World tension 0-100 from ratings (R), wider-war exposure (X) and tripwires (T). Coverage volume and tone parts
    join when the GDELT metrics exist. Computed here only; the model never sees it."""
    P = 0.0; E = 0.0
    nuclear, allies = set(lists.get("nuclear_states", [])), set(lists.get("us_treaty_allies", []))
    contrib = {}
    for c in conflicts:
        s = 3 if c["severity"] == "red" else 1
        t = {"escalating": 1.25, "steady": 1.0, "de-escalating": 0.75}.get(c["trend"], 1.0)
        P += s * t
        parties = {p for p in c.get("parties", []) if len(p) == 2}
        w = 1.0 if c["severity"] == "red" else 0.4
        e = w * ((1 if c.get("interstate") else 0) + (1 if parties & nuclear else 0) + (1 if (parties & allies) or "US" in parties else 0))
        E += e
        contrib[c["id"]] = s * t + 3 * e
    R = 100 * P / (P + 40)
    X = 100 * E / (E + 10)
    sev = {c["id"]: c["severity"] for c in conflicts}
    tripwires = [tw for tw in tripwires if tw.get("source") != "action"]      # preparedness actions: shown, not scored
    esc = [tw for tw in tripwires if tw["kind"] == "escalation" and tw["hotspot"] in sev]
    ease = [tw for tw in tripwires if tw["kind"] == "easing" and tw["hotspot"] in sev]
    parts = {"R": R}
    if any(c.get("parties") for c in conflicts): parts["X"] = X      # needs the parties lists (set on Sundays)
    if esc:
        wsum = sum(1 if sev[tw["hotspot"]] == "red" else 0.5 for tw in esc)
        hit = sum((1 if sev[tw["hotspot"]] == "red" else 0.5) * tripwire_weight(tw, today) for tw in esc)
        calm = sum((1 if sev[tw["hotspot"]] == "red" else 0.5) * tripwire_weight(tw, today) for tw in ease)
        parts["T"] = 100 * max(0.0, min(wsum, hit - 0.5 * calm)) / wsum
        for tw in esc: contrib[tw["hotspot"]] = contrib.get(tw["hotspot"], 0) + 4 * tripwire_weight(tw, today)
    weights = {"R": 0.30, "X": 0.20, "T": 0.20, "V": 0.15, "N": 0.15}
    def gti(keys):
        ws = sum(weights[k] for k in keys)
        return sum(weights[k] * parts[k] for k in keys) / ws
    keys = list(parts)
    value = gti(keys)
    loo = [gti([k for k in keys if k != drop]) for drop in keys] if len(keys) >= 3 else [value]
    band = ("Low" if value < 25 else "Guarded" if value < 45 else "Elevated" if value < 65 else "High" if value < 80 else "Severe")
    hist = [(h["date"], h["gti"]) for h in history if h.get("gti") is not None]
    week_ago = [g for dt, g in hist if days_between(dt, today) == 7] or [g for dt, g in hist if 6 <= days_between(dt, today) <= 9]
    change = round(value - week_ago[0]) if week_ago else None
    drivers = [k for k, _ in sorted(contrib.items(), key=lambda kv: -kv[1])[:3]]
    return {"value": round(value), "band": band, "range": [round(min(loo + [value])), round(max(loo + [value]))],
            "change_7d": change, "parts": {k: round(v) for k, v in parts.items()},
            "missing": [k for k in ("X", "T", "V", "N") if k not in parts], "drivers": drivers,
            "history": [[dt, g] for dt, g in hist[-55:]] + [[today, round(value)]]}

# ---------- history, changes, week line ----------
def history_row(feed, today):
    return {"date": today, "gti": (feed.get("tension") or {}).get("value"),
            "parts": (feed.get("tension") or {}).get("parts", {}),
            "hotspots": {c["id"]: [c["severity"], c["trend"], c["us_impact"]["level"], 1 if c.get("us_watch") else 0,
                                   sum(1 for tw in feed.get("tripwires", []) if tw["hotspot"] == c["id"] and tw["kind"] == "escalation" and tw["status"] == "tripped" and tw.get("source") != "action"),
                                   sum(1 for tw in feed.get("tripwires", []) if tw["hotspot"] == c["id"] and tw["status"] == "signs" and tw.get("source") != "action")]
                         for c in feed["conflicts"]},
            "names": {c["id"]: c.get("short_name") or c["name"] for c in feed["conflicts"]},
            "boil": {b["id"]: b["stage"] for b in feed.get("boil") or []},
            "signals": [{"id": s["id"], "direction": s["direction"], "keys": s["stats"]["keys_all"], "regions": s["stats"]["regions_np"]}
                        for s in feed.get("signals", []) if s.get("stats")]}

def add_history(history, row, keep=400):
    rows = [h for h in history if h.get("date") != row["date"]] + [row]
    return sorted(rows, key=lambda h: h["date"])[-keep:]

def changes(history, days=30):
    """What changed day by day, from history rows: turned red/yellow, trend changes, US level, new, ended."""
    out = []
    for prev, cur in zip(history, history[1:]):
        if days_between(cur["date"], history[-1]["date"]) > days: continue
        ph, ch, names = prev["hotspots"], cur["hotspots"], {**prev.get("names", {}), **cur.get("names", {})}
        for hid, v in ch.items():
            name = names.get(hid, hid)
            if hid not in ph: out.append({"date": cur["date"], "id": hid, "name": name, "kind": "new", "to": v[0]}); continue
            p = ph[hid]
            if p[0] != v[0]: out.append({"date": cur["date"], "id": hid, "name": name, "kind": "worse" if v[0] == "red" else "better", "what": f"turned {v[0]}"})
            elif p[1] != v[1]:
                kind = "worse" if v[1] == "escalating" else "better" if v[1] == "de-escalating" else "same"
                out.append({"date": cur["date"], "id": hid, "name": name, "kind": kind, "what": f"now {v[1]}"})
            if p[2] != v[2]: out.append({"date": cur["date"], "id": hid, "name": name, "kind": "us", "what": f"US impact {p[2]} -> {v[2]}"})
        for hid in ph:
            if hid not in ch: out.append({"date": cur["date"], "id": hid, "name": names.get(hid, hid), "kind": "ended", "what": "dropped from the map"})
    return sorted(out, key=lambda x: x["date"], reverse=True)[:80]

def spark(history, hid, n=28):
    """Last n days of a hotspot's severity: 2 red, 1 yellow, 0 not listed."""
    out = []
    for h in history[-n:]:
        v = h["hotspots"].get(hid)
        out.append(0 if not v else 2 if v[0] == "red" else 1)
    return out

def week_line(history, feed, today):
    """'2 hotspots turned red, 1 eased. 3 tripwires hit.' from the last 7 days of history."""
    week = [c for c in changes(history, days=7) if days_between(c["date"], today) <= 7]
    red = len({c["id"] for c in week if c.get("what") == "turned red"})
    calmer = len({c["id"] for c in week if c["kind"] == "better"})
    worse = len({c["id"] for c in week if c["kind"] == "worse" and c.get("what") != "turned red"})
    hit = sum(1 for tw in feed.get("tripwires", []) if tw["kind"] == "escalation" and tw["status"] == "tripped" and days_between(tw.get("since", today), today) <= 7)
    easing = sum(1 for tw in feed.get("tripwires", []) if tw["kind"] == "easing" and tw["status"] in ("signs", "tripped") and days_between(tw.get("since", today), today) <= 7)
    bits = []
    if red: bits.append(f"{red} hotspot{'s' if red > 1 else ''} turned red")
    if worse: bits.append(f"{worse} got worse")
    if calmer: bits.append(f"{calmer} eased")
    if hit: bits.append(f"{hit} warning sign{'s' if hit > 1 else ''} hit")
    if easing: bits.append(f"{easing} easing sign{'s' if easing > 1 else ''}")
    if len(history) < 3: return "History starts building today; week-over-week changes appear after a few days."
    return (", ".join(bits) + " this week.").capitalize() if bits else "No rating changes this week."
