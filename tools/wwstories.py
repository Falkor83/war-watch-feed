"""
War Watch stories: worldwide headlines (already in English) grouped into stories, with who reported each by rule.

A story is a set of headlines about the same event: similar English wording (translated ones included), within a
couple of days. For each story Python counts the independent outlets (reprints of one wire story with near-identical
headlines count once), the countries and news regions, how many come from the sides in the fighting, and how many come
from outlets in the registry (known outlets) vs unrated sites. Standard library only.
"""
import re
import wwdata

STOP = set("""a an the of to in on for at by with from and or but as is are was were be been being has have had it its
this that these those after before over under into out up down off about than then so not no new says say said says
amid against between during while what who why how when where which will would could may might can us u.s. s
report reports reported official officials sources source source:  latest live update updates news""".split())

DEMONYM = {"israeli": "israel", "iranian": "iran", "russian": "russia", "ukrainian": "ukraine", "chinese": "china",
           "american": "us", "america": "us", "saudi": "saudi", "lebanese": "lebanon", "palestinian": "palestine",
           "syrian": "syria", "iraqi": "iraq", "yemeni": "yemen", "turkish": "turkey", "pakistani": "pakistan",
           "indian": "india", "afghan": "afghanistan", "sudanese": "sudan", "korean": "korea", "japanese": "japan",
           "taiwanese": "taiwan", "venezuelan": "venezuela", "cuban": "cuba", "mexican": "mexico", "european": "europe",
           "british": "britain", "uk": "britain", "french": "france", "german": "germany", "polish": "poland",
           "egyptian": "egypt", "qatari": "qatar", "emirati": "uae", "houthis": "houthi", "tehran": "iran",
           "moscow": "russia", "kyiv": "ukraine", "beijing": "china", "washington": "us", "elections": "election",
           "strikes": "strike", "struck": "strike", "striking": "strike", "attacks": "attack", "attacked": "attack",
           "killed": "kill", "kills": "kill", "talks": "talk"}

def tokens(title):
    words = re.findall(r"[a-z0-9][a-z0-9'-]+", str(title).lower().replace("’", "'"))
    out = set()
    for w in words:
        w = w.strip("'-")
        if w.endswith("'s"): w = w[:-2]
        if w in DEMONYM: w = DEMONYM[w]; out.add(w[:5] if len(w) >= 6 else w); continue
        if len(w) < 3 or w in STOP: continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"): w = w[:-1]
        if w in DEMONYM: w = DEMONYM[w]
        out.add(w[:5] if len(w) >= 6 else w)              # crude stem: insurer/insurance, minister/ministry
    return out

def tier(item):
    """'state' (state-controlled media in the outlet registry), 'known' (in the registry, or one of the outlet feeds
    picked for config/sources.json), 'unrated' (a site found only through Google News or GDELT)."""
    dom = item.get("d") or ""
    o = wwdata.outlet_for("https://" + dom) if dom else None
    if not o and item.get("u"): o = wwdata.outlet_for(item["u"])
    if o and o.get("media") in ("state-controlled", "state"): return "state"
    if o and o.get("media") == "unrated": return "unrated"
    return "known" if o or item.get("v") == "rss" else "unrated"

def similar(a, b):
    if not a or not b: return 0.0
    inter = len(a & b)
    if inter < 2: return 0.0
    return max(inter / len(a | b), inter / min(len(a), len(b)) * 0.75)

def cluster(items, threshold=0.42, max_gap_s=36 * 3600):
    """Greedy grouping, newest first. Each story keeps the union of its first three titles' words to match against."""
    stories = []
    for x in sorted(items, key=lambda x: -(x.get("p") or 0)):
        tk = tokens(x.get("t", ""))
        if len(tk) < 2: continue
        best, score = None, 0.0
        for s in stories:
            if abs((s["p"] or 0) - (x.get("p") or 0)) > max_gap_s: continue
            sc = similar(tk, s["tokens"])
            if sc > score: best, score = s, sc
        if best is not None and score >= threshold:
            best["items"].append(x)
            if len(best["items"]) <= 3: best["tokens"] |= tk
        else:
            stories.append({"items": [x], "tokens": set(tk), "p": x.get("p") or 0})
    return stories

def stats(story, parties=frozenset()):
    """Who reported a story. Near-identical headlines from different outlets (reprints) count as one source."""
    items = story["items"]
    groups = []                                   # reprint groups
    for x in items:
        tk = tokens(x.get("t", ""))
        for g in groups:
            if len(tk & g["tk"]) / max(1, len(tk | g["tk"])) >= 0.8: g["outlets"].add(x.get("d") or x.get("s")); break
        else:
            groups.append({"tk": tk, "outlets": {x.get("d") or x.get("s")}})
    outlets = {x.get("d") or x.get("s") for x in items}
    countries = {x.get("c") for x in items if x.get("c")}
    regions = {wwdata.REGION_OF.get(c, "unknown") for c in countries}
    outside = {x.get("d") or x.get("s") for x in items if x.get("c") and x.get("c") not in parties}
    out_regions = {wwdata.REGION_OF.get(x["c"], "unknown") for x in items if x.get("c") and x.get("c") not in parties}
    tiers = {}
    for x in items:
        tiers[tier(x)] = tiers.get(tier(x), 0) + 1
    return {"headlines": len(items), "outlets": len(outlets), "independent": min(len(groups), len(outlets)),
            "countries": sorted(countries), "regions": len(regions), "outside_outlets": len(outside),
            "outside_regions": len(out_regions - {"unknown"}), "party_only": bool(items) and not outside,
            "known": tiers.get("known", 0), "state": tiers.get("state", 0), "unrated": tiers.get("unrated", 0),
            "languages": sorted({x.get("l", "en") for x in items}), "newest": max(x.get("p") or 0 for x in items)}

def pick_titles(story, n=3):
    """The story's headline plus others from different countries (outside voices first)."""
    seen, out = set(), []
    for x in sorted(story["items"], key=lambda x: (tier(x) != "known", -(x.get("p") or 0))):
        if x.get("c") in seen: continue
        seen.add(x.get("c")); out.append(x)
        if len(out) >= n: break
    return out

def describe(st):
    s = (f"{st['independent']} independent source{'s' if st['independent'] != 1 else ''} from {len(st['countries'])} "
         f"countr{'ies' if len(st['countries']) != 1 else 'y'} ({', '.join(st['countries'][:8])}), {st['regions']} region{'s' if st['regions'] != 1 else ''}")
    if st["party_only"]: s += "; ONLY the sides' own media"
    if st["state"]: s += f"; {st['state']} state media"
    if st["unrated"] and st["unrated"] >= st["known"]: s += "; mostly unrated sites"
    return s
