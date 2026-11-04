"""
War Watch shared data: countries, regions, the outlet registry and research-note clean-up.

The model writes research notes; this module decides, by rule, where each note comes from (country, region, media
type), whether it is a party's own account, and which "independence key" it shares with other notes (five reprints of
one AP story share one key). Standard library only.
"""
import json, os, re, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ISO-2 -> (English name, region). Regions: north-america, europe, russia, china, east-asia, south-asia,
# southeast-asia, oceania, gulf, middle-east, iran, africa, latin-america, intl.
_C = """
US United States|north-america; CA Canada|north-america;
GB United Kingdom|europe; IE Ireland|europe; FR France|europe; DE Germany|europe; NL Netherlands|europe;
BE Belgium|europe; LU Luxembourg|europe; CH Switzerland|europe; AT Austria|europe; IT Italy|europe; ES Spain|europe;
PT Portugal|europe; GR Greece|europe; CY Cyprus|europe; MT Malta|europe; DK Denmark|europe; NO Norway|europe;
SE Sweden|europe; FI Finland|europe; IS Iceland|europe; EE Estonia|europe; LV Latvia|europe; LT Lithuania|europe;
PL Poland|europe; CZ Czechia|europe; SK Slovakia|europe; HU Hungary|europe; RO Romania|europe; BG Bulgaria|europe;
MD Moldova|europe; UA Ukraine|europe; SI Slovenia|europe; HR Croatia|europe; BA Bosnia and Herzegovina|europe;
RS Serbia|europe; ME Montenegro|europe; MK North Macedonia|europe; AL Albania|europe; XK Kosovo|europe;
GE Georgia|europe; AM Armenia|europe; AZ Azerbaijan|europe;
RU Russia|russia; BY Belarus|russia;
CN China|china; HK Hong Kong|china; MO Macau|china;
JP Japan|east-asia; KR South Korea|east-asia; KP North Korea|east-asia; TW Taiwan|east-asia; MN Mongolia|east-asia;
IN India|south-asia; PK Pakistan|south-asia; BD Bangladesh|south-asia; LK Sri Lanka|south-asia; NP Nepal|south-asia;
AF Afghanistan|south-asia; BT Bhutan|south-asia; MV Maldives|south-asia;
KZ Kazakhstan|south-asia; UZ Uzbekistan|south-asia; TJ Tajikistan|south-asia; KG Kyrgyzstan|south-asia; TM Turkmenistan|south-asia;
MM Myanmar|southeast-asia; TH Thailand|southeast-asia; KH Cambodia|southeast-asia; LA Laos|southeast-asia;
VN Vietnam|southeast-asia; MY Malaysia|southeast-asia; SG Singapore|southeast-asia; ID Indonesia|southeast-asia;
PH Philippines|southeast-asia; BN Brunei|southeast-asia; TL Timor-Leste|southeast-asia;
AU Australia|oceania; NZ New Zealand|oceania; PG Papua New Guinea|oceania; FJ Fiji|oceania;
SA Saudi Arabia|gulf; AE United Arab Emirates|gulf; QA Qatar|gulf; KW Kuwait|gulf; BH Bahrain|gulf; OM Oman|gulf;
IL Israel|middle-east; PS Palestine|middle-east; LB Lebanon|middle-east; SY Syria|middle-east; JO Jordan|middle-east;
IQ Iraq|middle-east; TR Turkey|middle-east; EG Egypt|middle-east; YE Yemen|middle-east;
IR Iran|iran;
MA Morocco|africa; DZ Algeria|africa; TN Tunisia|africa; LY Libya|africa; SD Sudan|africa; SS South Sudan|africa;
ET Ethiopia|africa; ER Eritrea|africa; DJ Djibouti|africa; SO Somalia|africa; KE Kenya|africa; UG Uganda|africa;
TZ Tanzania|africa; RW Rwanda|africa; BI Burundi|africa; CD DR Congo|africa; CG Republic of the Congo|africa;
CF Central African Republic|africa; CM Cameroon|africa; TD Chad|africa; NE Niger|africa; NG Nigeria|africa;
ML Mali|africa; BF Burkina Faso|africa; SN Senegal|africa; GH Ghana|africa; CI Ivory Coast|africa; GN Guinea|africa;
SL Sierra Leone|africa; LR Liberia|africa; TG Togo|africa; BJ Benin|africa; MR Mauritania|africa; GM Gambia|africa;
GA Gabon|africa; GQ Equatorial Guinea|africa; AO Angola|africa; ZM Zambia|africa; ZW Zimbabwe|africa;
MZ Mozambique|africa; MW Malawi|africa; ZA South Africa|africa; NA Namibia|africa; BW Botswana|africa;
MG Madagascar|africa; MU Mauritius|africa;
MX Mexico|latin-america; GT Guatemala|latin-america; HN Honduras|latin-america; SV El Salvador|latin-america;
NI Nicaragua|latin-america; CR Costa Rica|latin-america; PA Panama|latin-america; CU Cuba|latin-america;
HT Haiti|latin-america; DO Dominican Republic|latin-america; JM Jamaica|latin-america; TT Trinidad and Tobago|latin-america;
PR Puerto Rico|latin-america; CO Colombia|latin-america; VE Venezuela|latin-america; EC Ecuador|latin-america;
PE Peru|latin-america; BO Bolivia|latin-america; BR Brazil|latin-america; PY Paraguay|latin-america;
UY Uruguay|latin-america; AR Argentina|latin-america; CL Chile|latin-america; GY Guyana|latin-america;
UN International|intl
"""
COUNTRIES, REGION_OF = {}, {}
for part in _C.replace("\n", " ").split(";"):
    part = part.strip()
    if not part: continue
    code, rest = part[:2], part[3:]
    name, region = rest.split("|")
    COUNTRIES[code] = name.strip(); REGION_OF[code] = region.strip()

REGION_NAMES = {"north-america": "N. America", "europe": "Europe", "russia": "Russia", "china": "China",
                "east-asia": "East Asia", "south-asia": "South & Central Asia", "southeast-asia": "SE Asia",
                "oceania": "Oceania", "gulf": "Gulf", "middle-east": "Middle East", "iran": "Iran",
                "africa": "Africa", "latin-america": "Latin America", "intl": "International bodies"}

_ALIASES = {"usa": "US", "u.s.": "US", "us": "US", "america": "US", "uk": "GB", "britain": "GB", "england": "GB",
            "great britain": "GB", "uae": "AE", "emirates": "AE", "south korea": "KR", "korea": "KR",
            "republic of korea": "KR", "north korea": "KP", "dprk": "KP", "drc": "CD", "dr congo": "CD",
            "democratic republic of the congo": "CD", "congo-kinshasa": "CD", "congo": "CG", "congo-brazzaville": "CG",
            "republic of congo": "CG", "ivory coast": "CI", "cote d'ivoire": "CI", "côte d'ivoire": "CI",
            "turkiye": "TR", "türkiye": "TR", "czech republic": "CZ", "holland": "NL", "burma": "MM",
            "palestinian territories": "PS", "gaza": "PS", "west bank": "PS", "vatican": "IT",
            "un": "UN", "united nations": "UN", "international": "UN", "global": "UN", "eu": "BE",
            "european union": "BE", "nato": "BE", "russian federation": "RU", "hongkong": "HK", "persia": "IR"}
_BYNAME = {v.lower(): k for k, v in COUNTRIES.items()} | _ALIASES

def iso(text):
    """Country text from a model or feed ('UK', 'Kenya (dateline)', 'Republic of the Congo') -> ISO-2 or ''."""
    t = str(text or "").strip()
    if re.fullmatch(r"[A-Z]{2}", t) and t in COUNTRIES: return t
    t = re.sub(r"\(.*?\)", "", t).strip().lower().rstrip(".")
    if t in _BYNAME: return _BYNAME[t]
    for name, code in sorted(_BYNAME.items(), key=lambda kv: -len(kv[0])):   # 'via allAfrica, Mozambique'
        if len(name) > 3 and re.search(r"\b" + re.escape(name) + r"\b", t): return code
    return ""

def country_name(code): return COUNTRIES.get(code, code)

# ---------- outlet registry ----------
_REG = None
def registry():
    global _REG
    if _REG is None:
        data = json.load(open(os.path.join(ROOT, "outlets.json"), encoding="utf-8"))
        by_domain = {}
        for o in data["outlets"]:
            for d in o["domains"]: by_domain[d.lower()] = o
        _REG = {"by_domain": by_domain, "blocked": set(d.lower() for d in data.get("blocked", [])), "raw": data}
    return _REG

def host(url):
    try: h = (urllib.parse.urlparse(str(url)).hostname or "").lower()
    except ValueError: return ""
    return h[4:] if h.startswith("www.") else h

def outlet_for(url):
    """The registry entry for a URL (longest matching domain suffix), or None."""
    h = host(url)
    reg = registry()["by_domain"]
    parts = h.split(".")
    for i in range(len(parts) - 1):
        o = reg.get(".".join(parts[i:]))
        if o: return o
    return None

def blocked(url):
    h = host(url)
    return any(h == b or h.endswith("." + b) for b in registry()["blocked"])

WIRES = {"reuters": "Reuters", "ap": "AP", "associated press": "AP", "afp": "AFP", "agence france-presse": "AFP",
         "dpa": "dpa", "efe": "EFE", "ansa": "ANSA", "ani": "ANI", "pti": "PTI", "ians": "IANS", "xinhua": "Xinhua",
         "tass": "TASS", "ria": "RIA Novosti", "ria novosti": "RIA Novosti", "interfax": "Interfax", "anadolu": "Anadolu",
         "yonhap": "Yonhap", "kyodo": "Kyodo", "jiji": "Jiji", "bernama": "Bernama", "antara": "Antara", "wam": "WAM",
         "spa": "SPA", "qna": "QNA", "irna": "IRNA", "tasnim": "Tasnim", "nan": "NAN", "bloomberg": "Bloomberg"}
_WIRE_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in WIRES), key=len, reverse=True)) + r")\b", re.I)

def wire_in(text):
    """'Reuters via gCaptain' / 'Arab News (AFP)' / 'AP (via PBS NewsHour)' -> the agency, else ''."""
    m = _WIRE_RE.search(str(text or ""))
    if not m: return ""
    w = m.group(1)
    if w.isupper() or w[0].isupper(): return WIRES[w.lower()]
    return ""

def norm_body(s):
    s = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())
    s = re.sub(r"\b(the|of|s|ministry|ministry of|spokesman|spokesperson|office|government|govt)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:60]

def note_source(n):
    """Where a note comes from, by rule: {outlet_id, outlet, home, region, media, wire}."""
    o = outlet_for(n.get("url"))
    if o:
        return {"outlet_id": o["id"], "outlet": o["name"], "home": o["home"], "region": REGION_OF.get(o["home"], "intl")
                if o.get("media") not in ("intl-org", "ngo") else "intl", "media": o.get("media", "private"),
                "wire": o["name"] if o.get("media") == "wire" else ""}
    home = iso(n.get("country"))
    media = "state-controlled" if n.get("state_media") else "unrated"
    oid = re.sub(r"[^a-z0-9]+", "-", host(n.get("url"))).strip("-") or "unknown"
    return {"outlet_id": oid, "outlet": str(n.get("outlet", ""))[:60] or host(n.get("url")), "home": home,
            "region": REGION_OF.get(home, "intl" if home == "UN" else "unknown"), "media": media, "wire": ""}

def parties_iso(parties):
    """A hotspot's parties: ISO-2 states plus armed-group names (kept as lower-case text)."""
    out = set()
    for p in parties or []:
        c = iso(p)
        out.add(c if c else str(p).strip().lower())
    return out

def party_tag(note, parties):
    """party-gov (a party's state media or officials: a claim), party-press (independent outlet from a party),
    non-party. Symmetric: US outlets are party press in a war the US fights."""
    claim = str(note.get("claim_by", ""))
    if claim.startswith("party:"): return "party-gov"            # the researcher marked it as a fighting side's claim
    if not parties: return "non-party"
    if note["media"] in ("state-controlled", "state-funded") and note["home"] in parties: return "party-gov"
    if note["home"] in parties: return "party-press"
    return "non-party"

def indep_key(note):
    """Notes sharing a key are one source: relays of one official statement, reprints of one wire story."""
    claim = str(note.get("claim_by", "")).strip()
    for pre in ("official:", "party:", "org:"):
        if claim.startswith(pre) and len(claim) > len(pre) + 1: return "claim:" + norm_body(claim[len(pre):])
    origin = str(note.get("origin", "own")).strip()
    if origin.startswith("wire:"):
        w = wire_in(origin[5:]) or origin[5:].strip()
        return "wire:" + w.lower()
    if origin.startswith("outlet:"): return "outlet:" + norm_body(origin[7:])
    w = note.get("wire") or wire_in(note.get("outlet_text", ""))
    if w: return "wire:" + w.lower()
    return "outlet:" + note["outlet_id"]

def clean_notes(raw, seen_urls, hotspot_parties, today, prefix="n"):
    """Model notes -> checked notes with IDs and rule-based fields. Drops invented URLs (not among the search tool's
    results, when those are known), blocked domains and future dates."""
    from wwapi import norm_url
    out, dropped = [], {"url": 0, "blocked": 0, "date": 0, "bad": 0}
    for n in raw or []:
        if not isinstance(n, dict) or not str(n.get("url", "")).startswith("https://") or not n.get("fact"):
            dropped["bad"] += 1; continue
        if seen_urls and norm_url(n["url"]) not in seen_urls: dropped["url"] += 1; continue
        if blocked(n["url"]): dropped["blocked"] += 1; continue
        date = str(n.get("date", ""))[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date): date = today
        if date > today: dropped["date"] += 1; continue
        src = note_source(n)
        note = {"id": f"{prefix}{len(out) + 1:02d}", "hotspot": str(n.get("hotspot", ""))[:80],
                "event": str(n.get("event", ""))[:80], "date": date, "fact": str(n["fact"])[:400],
                "url": str(n["url"])[:500], "outlet_text": str(n.get("outlet", ""))[:60],
                "origin": str(n.get("origin", "own"))[:60], "claim_by": str(n.get("claim_by", "reporters"))[:80],
                "against_interest": bool(n.get("against_interest", False)), "frame": str(n.get("frame", ""))[:100],
                "stance": n.get("stance") if n.get("stance") in ("escalation", "easing", "neutral") else "neutral",
                "tripwire": str(n.get("tripwire", ""))[:60], **src}
        if not note["wire"]: note["wire"] = wire_in(note["outlet_text"]) if wire_in(note["outlet_text"]) != note["outlet"] else ""
        if n.get("state_media") and note["media"] in ("unrated", "private"): note["media"] = "state-controlled"
        note["tag"] = party_tag(note, hotspot_parties.get(note["hotspot"], set()))
        note["key"] = indep_key(note)
        out.append(note)
    return out, dropped

def public_note(n):
    """What the app gets for a cited note."""
    return {k: n[k] for k in ("id", "date", "fact", "url", "outlet", "home", "region", "media", "tag", "key", "hotspot")}
