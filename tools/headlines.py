"""
War Watch worldwide headlines, every hour.

Collects headlines from ~220 outlets' world/international feeds (34 languages), Google News "World" pages in 36
country editions, and GDELT (65 languages, searched per hotspot through its English translations). Non-English
headlines are translated to English by Claude Haiku (Batch API, half price; each headline once). The result,
headlines.json on the `live` branch, feeds the app's live headlines and the daily analysis.

Nothing here judges what a headline means: the app's matcher (and the daily job's analysis) do that. This job keeps
anything that names a hotspot's places, sides or groups, or uses words of war, security or sabotage, so borderline
headlines reach the app too. Standard library only.
"""
import json, os, re, sys, time, gzip, hashlib, datetime, email.utils, urllib.request, urllib.parse, urllib.error
import concurrent.futures as cf
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wwapi, wwdata
from wwapi import step, ok, warn, err, save_json, norm_url

ROOT = wwapi.ROOT
LIVE = os.environ.get("WW_LIVE_DIR", os.path.join(ROOT, "live"))
MODEL = os.environ.get("WW_TRANSLATE_MODEL", "claude-haiku-5-5")
SUB_CAP = float(os.environ.get("WW_HEADLINES_CAP_USD", "5"))       # translation's own monthly limit, inside War Watch's cap
WAIT_MIN = float(os.environ.get("WW_TRANSLATE_WAIT_MIN", "15"))
MAX_TRANSLATE = int(os.environ.get("WW_MAX_TRANSLATE", "2500"))       # per run, so a flood can't run up a bill
WINDOW_H = 48
MAX_ITEMS = 12000
POLL_S = 20
GNEWS_GAP = 1.0
GDELT_BUDGET_S = float(os.environ.get("WW_GDELT_BUDGET_S", "720"))  # at most 12 minutes of GDELT a run; the rest next hour
GDELT_GAP = 12.0                                                       # seconds between GDELT calls (it answers 429 below ~10 s)
UA = "Mozilla/5.0 (compatible; WarWatchBot/0.3; +https://github.com/Falkor83/war-watch-feed)"
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

GDELT_LANGS = {"english": "en", "arabic": "ar", "french": "fr", "german": "de", "spanish": "es", "portuguese": "pt",
               "russian": "ru", "ukrainian": "uk", "chinese": "zh", "japanese": "ja", "korean": "ko", "turkish": "tr",
               "persian": "fa", "urdu": "ur", "hindi": "hi", "bengali": "bn", "indonesian": "id", "malay": "ms",
               "vietnamese": "vi", "thai": "th", "hebrew": "he", "italian": "it", "polish": "pl", "dutch": "nl",
               "swedish": "sv", "norwegian": "no", "danish": "da", "finnish": "fi", "czech": "cs", "slovak": "sk",
               "hungarian": "hu", "romanian": "ro", "bulgarian": "bg", "serbian": "sr", "croatian": "hr", "greek": "el",
               "armenian": "hy", "georgian": "ka", "azerbaijani": "az", "kazakh": "kk", "uzbek": "uz", "pashto": "ps",
               "swahili": "sw", "hausa": "ha", "amharic": "am", "somali": "so", "tamil": "ta", "telugu": "te",
               "marathi": "mr", "gujarati": "gu", "punjabi": "pa", "nepali": "ne", "sinhala": "si", "burmese": "my",
               "khmer": "km", "lao": "lo", "tagalog": "tl", "albanian": "sq", "macedonian": "mk", "slovenian": "sl",
               "lithuanian": "lt", "latvian": "lv", "estonian": "et", "catalan": "ca", "galician": "gl", "basque": "eu",
               "afrikaans": "af", "belarusian": "be", "bosnian": "bs", "mongolian": "mn", "tigrinya": "ti"}
TLD_HOME = {"uk": "GB", "fr": "FR", "de": "DE", "es": "ES", "it": "IT", "pl": "PL", "ua": "UA", "ru": "RU", "tr": "TR",
            "il": "IL", "jp": "JP", "kr": "KR", "tw": "TW", "hk": "HK", "cn": "CN", "in": "IN", "pk": "PK", "ng": "NG",
            "za": "ZA", "ke": "KE", "ph": "PH", "au": "AU", "ca": "CA", "sg": "SG", "mx": "MX", "co": "CO", "br": "BR",
            "ar": "AR", "eg": "EG", "sa": "SA", "lb": "LB", "ae": "AE", "id": "ID", "vn": "VN", "th": "TH", "nl": "NL",
            "be": "BE", "ch": "CH", "at": "AT", "se": "SE", "no": "NO", "dk": "DK", "fi": "FI", "ro": "RO", "bg": "BG",
            "gr": "GR", "cz": "CZ", "sk": "SK", "hu": "HU", "pt": "PT", "ie": "IE", "nz": "NZ", "my": "MY", "bd": "BD",
            "lk": "LK", "np": "NP", "af": "AF", "ir": "IR", "iq": "IQ", "sy": "SY", "jo": "JO", "qa": "QA", "kw": "KW",
            "om": "OM", "ye": "YE", "et": "ET", "so": "SO", "sd": "SD", "ug": "UG", "tz": "TZ", "rw": "RW", "cd": "CD",
            "gh": "GH", "sn": "SN", "ml": "ML", "bf": "BF", "ne": "NE", "td": "TD", "cm": "CM", "mz": "MZ", "md": "MD",
            "ge": "GE", "am": "AM", "az": "AZ", "kz": "KZ", "by": "BY", "lt": "LT", "lv": "LV", "ee": "EE", "rs": "RS",
            "hr": "HR", "ba": "BA", "ve": "VE", "cu": "CU", "ht": "HT", "ec": "EC", "pe": "PE", "cl": "CL", "mm": "MM"}

# Words of war, security, crisis and sabotage (English; non-English headlines are checked after translation).
SECURITY = re.compile(r"\b(war|wars|warfare|military|militar\w*|army|armies|troops?|soldiers?|forces|navy|naval|air ?force|"
                      r"missiles?|rockets?|drones?|uav|shelling|shells?|artillery|bomb\w*|strikes?|struck|airstrikes?|"
                      r"attack\w*|raids?|clash\w*|fighting|fighters|offensive|invasion|invade\w*|occup\w*|ceasefire|"
                      r"truce|peace talks|talks|negotiat\w*|sanction\w*|border|frontier|nuclear|uranium|warhead\w*|"
                      r"mobiliz\w*|mobilis\w*|conscript\w*|reservists?|drills?|exercises?|deploy\w*|evacuat\w*|"
                      r"embassy|airspace|blockade|militia\w*|rebels?|insurgen\w*|militants?|jihadi\w*|terror\w*|"
                      r"gunmen|hostages?|kidnap\w*|abduct\w*|killed|kills|dead|deaths?|casualt\w*|wounded|massacre|"
                      r"refugees?|displaced|coup|junta|martial law|state of emergency|ultimatum|threat\w*|warns?|"
                      r"escalat\w*|tensions?|provocation\w*|incursion\w*|sabotage\w*|cyber\w*|hack\w*|ransomware|"
                      r"malware|spy|spies|espionage|defen[cs]e|security|weapons?|arms|ammunition|warship\w*|"
                      r"submarines?|fighter jets?|tanks?|siege|annex\w*|separatist\w*|genocide|ethnic cleansing)\b", re.I)

# AI with catastrophic potential (Matt, 2026-11-04): AI building AI, weapons uplift, cyber, loss of control, military AI.
AI_TERMS = re.compile(r"(\b(AI|A\.I\.|AGI|ASI|LLMs?)\b|(?i:\b(artificial intelligence|machine learning|large language models?|"
                      r"frontier (AI|models?)|superintelligen\w*|chatbots?|OpenAI|Anthropic|DeepMind|xAI|ChatGPT|Claude|Gemini|"
                      r"GPT-\d\w*|DeepSeek|Qwen|Mistral AI|Meta AI|AI Safety Institute|AI Security Institute|CAISI|AISI|"
                      r"autonomous weapons?|killer robots?|"
                      r"\u4eba\u5de5\u667a\u80fd|\u4eba\u5de5\u77e5\u80fd|\uc778\uacf5\uc9c0\ub2a5|"
                      r"intelligence artificielle|inteligencia artificial|intelig\u00eancia artificial|k\u00fcnstliche intelligenz|"
                      r"\u0438\u0441\u043a\u0443\u0441\u0441\u0442\u0432\u0435\u043d\u043d\w+ \u0438\u043d\u0442\u0435\u043b\u043b\u0435\u043a\u0442\w*)\b))")
AI_RISK = re.compile(r"\b(safety|unsafe|risks?|risky|danger\w*|threat\w*|catastroph\w*|existential|extinction|doom\w*|"
                     r"bio\w*weapons?|biological|pathogens?|virus\w*|pandemic\w*|chemical|nuclear|radiological|"
                     r"cyber\w*|hack\w*|exploit\w*|vulnerabilit\w*|zero-day|malware|ransomware|critical infrastructure|power grids?|"
                     r"weapons?|military|militar\w*|pentagon|defen[cs]e|warfare|drones?|targeting|kill chain|"
                     r"decepti\w*|deceiv\w*|scheming|sabotag\w*|blackmail\w*|rogue|loss of control|out of control|"
                     r"control problem|self-(improv|replicat|exfiltrat)\w*|recursive\w*|automat\w* (AI )?research|AI R&D|"
                     r"intelligence explosion|superhuman|smarter (AI|models?|than)|on (its|their) own|"
                     r"(build|train|improv|design|creat)\w* (itself|themselves|its own|their own|smarter|better AI)|"
                     r"alignment|misalign\w*|jailbreak\w*|kill switch|red[- ]team\w*|safety tests?|"
                     r"incidents?|treaty|moratorium|export controls?|warn\w*|whistleblow\w*|"
                     r"superintelligen\w*|AGI|ASI|godfather|arms race|national security|espionage|stolen weights|"
                     r"model weights|deepfakes?|disinformation)\b", re.I)

def is_ai_risk(text): return bool(AI_TERMS.search(text) and AI_RISK.search(text))

def now_ts(): return int(time.time())
def iso(ts): return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

# ---------- fetching ----------
def fetch(url, timeout=25, browser=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA_BROWSER if browser else UA, "Accept": "*/*",
                                               "Accept-Encoding": "gzip", "Accept-Language": "en,*;q=0.5"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(8_000_000)
        if body[:2] == b"\x1f\x8b" or r.headers.get("Content-Encoding", "") == "gzip":
            body = gzip.decompress(body)
        return body

def local(tag): return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""

def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    for a, b in (("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"), ("&lt;", "<"), ("&gt;", ">")): s = s.replace(a, b)
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))) if int(m.group(1)) < 0x110000 else " ", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_date(s):
    s = str(s or "").strip()
    if not s: return 0
    try:
        if re.fullmatch(r"\d{8}T\d{6}Z", s):                       # GDELT seendate
            return int(datetime.datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc).timestamp())
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
            if d.tzinfo is None: d = d.replace(tzinfo=datetime.timezone.utc)
            return int(d.timestamp())
        d = email.utils.parsedate_to_datetime(s)
        if d.tzinfo is None: d = d.replace(tzinfo=datetime.timezone.utc)
        return int(d.timestamp())
    except (ValueError, TypeError, OverflowError, IndexError):
        return 0

def parse_feed(body):
    """RSS, RDF or Atom -> [{title, link, date, summary, source, source_url}]. Falls back to a forgiving scan when the
    XML is not well-formed (many news feeds aren't)."""
    text = body.decode("utf-8", errors="replace").lstrip("﻿ \r\n\t") if isinstance(body, bytes) else body
    items = []
    try:
        root = ET.fromstring(text.encode("utf-8"))
        for el in root.iter():
            if local(el.tag) not in ("item", "entry"): continue
            it = {"title": "", "link": "", "date": "", "summary": "", "source": "", "source_url": ""}
            for ch in el:
                n = local(ch.tag)
                if n == "title" and not it["title"]: it["title"] = strip_html("".join(ch.itertext()))
                elif n == "link" and not it["link"]:
                    it["link"] = (ch.get("href") if ch.get("rel") in (None, "alternate") and ch.get("href") else (ch.text or "")).strip()
                elif n in ("pubdate", "published", "updated", "date", "issued") and not it["date"]: it["date"] = (ch.text or "").strip()
                elif n in ("description", "summary") and not it["summary"]: it["summary"] = strip_html("".join(ch.itertext()))[:300]
                elif n == "source": it["source"] = strip_html(ch.text or ""); it["source_url"] = ch.get("url", "")
                elif n == "guid" and not it["link"] and (ch.text or "").startswith("http"): it["link"] = ch.text.strip()
            if it["title"] and it["link"]: items.append(it)
        return items
    except ET.ParseError:
        pass
    for m in re.finditer(r"<(item|entry)[\s>](.*?)</\1>", text, re.S | re.I):
        blk = m.group(2)
        def tag(name):
            mm = re.search(rf"<{name}[^>]*>(.*?)</{name}>", blk, re.S | re.I)
            if not mm: return ""
            v = mm.group(1).strip()
            cd = re.fullmatch(r"<!\[CDATA\[(.*?)\]\]>", v, re.S)
            return cd.group(1) if cd else v
        link = tag("link")
        if not link:
            mm = re.search(r'<link[^>]+href="([^"]+)"', blk, re.I)
            link = mm.group(1) if mm else tag("guid")
        it = {"title": strip_html(tag("title")), "link": strip_html(link), "date": tag("pubDate") or tag("dc:date") or tag("published") or tag("updated"),
              "summary": strip_html(tag("description") or tag("summary"))[:300], "source": strip_html(tag("source")), "source_url": ""}
        if it["title"] and it["link"].startswith("http"): items.append(it)
    return items

# ---------- language ----------
_NONLATIN = re.compile(r"[^\x00-\x7FÀ-ɏ‐-›€]")
_EN_WORDS = re.compile(r"\b(the|of|and|to|for|with|after|says?|said|over|from|is|are|was|has|have|by|its|amid)\b", re.I)

def needs_translation(title, lang):
    """English feeds carry the odd Korean or Arabic headline; Spanish feeds carry English ones."""
    t = title or ""
    nonlatin = len(_NONLATIN.findall(t)) / max(1, len(t))
    if nonlatin > 0.25: return True
    if lang == "en": return False
    return len(set(w.lower() for w in _EN_WORDS.findall(t))) < 2

# ---------- items ----------
def key_for(url): return hashlib.sha1(norm_url(url).encode()).hexdigest()[:14]

def make_item(title, url, outlet, domain, home, lang, published, via, hints=(), summary=""):
    title = strip_html(title)[:300]
    if len(title) < 12 or not url.startswith(("http://", "https://")): return None
    tr = needs_translation(title, lang)
    return {"k": key_for(url), "u": url[:600], "o": title, "t": "" if tr else title, "l": lang if tr else "en",
            "s": outlet[:60], "d": domain[:60], "c": home, "p": published, "v": via, "h": sorted(set(hints)),
            "x": "" if tr or needs_translation(summary, lang) else summary[:240]}

def domain_home(domain, fallback):
    o = wwdata.outlet_for("https://" + domain) if domain else None
    if o: return o["home"], o["name"]
    tld = domain.rsplit(".", 1)[-1] if domain else ""
    if domain.endswith((".co.uk", ".org.uk")): return "GB", ""
    return TLD_HOME.get(tld, fallback), ""

def collect_rss(sources):
    def one(src):
        try:
            try: body = fetch(src["url"])
            except urllib.error.HTTPError as e:
                if e.code != 403: raise
                body = fetch(src["url"], browser=True)
            out = []
            for it in parse_feed(body):
                dom = wwdata.host(it["link"])
                x = make_item(it["title"], it["link"], src["name"], dom, src["home"], src["lang"], parse_date(it["date"]), "rss",
                              hints=src.get("hints") or (), summary=it["summary"])
                if x: out.append(x)
            return src["name"], out, ""
        except Exception as e:
            return src["name"], [], f"{type(e).__name__}: {str(e)[:80]}"
    got, bad = [], []
    with cf.ThreadPoolExecutor(24) as ex:
        for name, items, e in ex.map(one, sources):
            if e or not items: bad.append(f"{name} ({e or 'empty'})")
            got += items
    return got, len(sources) - len(bad), bad

def collect_gnews(editions):
    got, okn, bad = [], 0, []
    for ed in editions:
        url = f"https://news.google.com/rss/headlines/section/topic/WORLD?hl={ed['hl']}&gl={ed['gl']}&ceid={ed['ceid']}"
        try:
            items = parse_feed(fetch(url))
            okn += 1
        except Exception as e:
            bad.append(f"{ed['ceid']} ({type(e).__name__})"); continue
        for it in items:
            title, outlet = it["title"], it["source"]
            if outlet and title.endswith(" - " + outlet): title = title[: -len(outlet) - 3]
            dom = wwdata.host(it["source_url"]) if it["source_url"] else ""
            home, reg_name = domain_home(dom, ed["gl"])
            x = make_item(title, it["link"], reg_name or outlet or dom or "Google News", dom, home, ed["lang"],
                          parse_date(it["date"]), "gnews")
            if x: got.append(x)
        time.sleep(GNEWS_GAP)
    return got, okn, bad

def collect_search(queries):
    """Google News searches: one outlet each (site:, for wires and institutes without a usable feed) or one topic
    (AI risk, in several languages). Site searches count as picked outlets, like the feeds."""
    got, okn, bad = [], 0, []
    for q in queries:
        ed = q.get("ed") or "en-US|US|US:en"
        hl, gl, ceid = ed.split("|")
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q["q"] + " when:" + q.get("when", "2d")) +
               f"&hl={hl}&gl={gl}&ceid={ceid}")
        try:
            items = parse_feed(fetch(url))
            okn += 1
        except Exception as e:
            bad.append(f"{q.get('name') or q['q'][:30]} ({type(e).__name__})"); time.sleep(GNEWS_GAP); continue
        for it in items:
            title, outlet = it["title"], it["source"]
            if outlet and title.endswith(" - " + outlet): title = title[: -len(outlet) - 3]
            dom = wwdata.host(it["source_url"]) if it["source_url"] else ""
            home, reg_name = domain_home(dom, q.get("home") or gl)
            if q.get("home") and q.get("site"): home = q["home"]
            x = make_item(title, it["link"], q.get("name") if q.get("site") else (reg_name or outlet or dom or "Google News"),
                          dom, home, q.get("lang") or hl.split("-")[0], parse_date(it["date"]), "site" if q.get("site") else "gsearch",
                          hints=q.get("hints") or ())
            if x: got.append(x)
        time.sleep(GNEWS_GAP)
    return got, okn, bad

def gdelt_terms(c):
    terms = [t for t in (c.get("anchors") or []) + (c.get("keywords") or []) if len(t) >= 4 and '"' not in t and t.lower() not in
             ("midterms", "election", "cyberattack")]
    seen, out = set(), []
    for t in terms:
        if t.lower() in seen: continue
        seen.add(t.lower()); out.append(f'"{t}"' if (" " in t or "-" in t) else t)
    return out[:8]

def gdelt_url(query, timespan="4h", maxrecords=100):
    return ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + urllib.parse.quote(query) +
            f"&mode=artlist&format=json&maxrecords={maxrecords}&timespan={timespan}&sort=datedesc")

def gdelt_get(url, last=[0.0]):
    """One paced GDELT call; None when GDELT is busy (it answers 429 or plain text)."""
    for attempt in range(2):
        wait = last[0] + GDELT_GAP - time.time()
        if wait > 0: time.sleep(wait)
        try:
            body = fetch(url, timeout=40)
            last[0] = time.time()
            t = body.decode("utf-8", errors="replace").strip()
            if not t.startswith("{"): raise ValueError(t[:60])
            return json.loads(t)
        except urllib.error.HTTPError as e:
            last[0] = time.time()
            if e.code == 429 and attempt == 0: time.sleep(25); continue
            return None
        except Exception:
            last[0] = time.time()
            if attempt == 0: time.sleep(10); continue
            return None
    return None

def collect_gdelt(feed, timespan="1d", per_run=12):
    queries = []
    for c in feed.get("conflicts", []):
        terms = gdelt_terms(c)
        if terms: queries.append((c["id"], terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"))
    queries.append(("cyber", "(cyberattack OR hackers OR ransomware OR sabotage) (water OR power OR grid OR hospital OR pipeline "
                             "OR refrigeration OR telecom OR railway)"))
    queries.append(("", "(airstrike OR missile OR shelling OR \"drone attack\" OR offensive OR ceasefire OR troops OR clashes "
                        "OR mobilization OR ultimatum)"))
    # GDELT is slow when busy: a time budget, starting at a different hotspot each hour so all get their turn
    hot, extra = queries[:-2], queries[-2:]
    shift = (int(time.time() // 3600) * per_run) % max(1, len(hot))
    queries = extra + (hot[shift:] + hot[:shift])[:per_run]            # each hotspot every ~3 hours, with a 1-day window
    got, okn, bad, t0 = [], 0, [], time.time()
    for hid, q in queries:
        if time.time() - t0 > GDELT_BUDGET_S: bad.append(f"{hid or 'general'} (out of time)"); continue
        data = gdelt_get(gdelt_url(q, timespan))
        if data is None: bad.append(hid or "general"); continue
        okn += 1
        for a in data.get("articles") or []:
            dom = str(a.get("domain", "")).lower().removeprefix("www.")
            home = wwdata.iso(a.get("sourcecountry")) or domain_home(dom, "")[0]
            reg = wwdata.outlet_for("https://" + dom) if dom else None
            lang = GDELT_LANGS.get(str(a.get("language", "")).lower(), "xx")
            x = make_item(a.get("title", ""), str(a.get("url", "")), reg["name"] if reg else dom, dom, home, lang,
                          parse_date(a.get("seendate")), "gdelt", hints=[hid] if hid else [])
            if x: got.append(x)
    return got, okn, len(queries), bad

# ---------- relevance ----------
def term_regex(feed):
    """Every hotspot's places, sides, groups and leaders, plus country names: a loose net for 'could be about a hotspot'."""
    terms = set()
    for c in feed.get("conflicts", []):
        for t in (c.get("anchors") or []) + (c.get("keywords") or []) + (c.get("context") or []) + (c.get("countries") or []) + \
                 [p for p in c.get("parties") or [] if len(p) > 3] + [c.get("short_name", "")]:
            t = str(t).strip()
            if len(t) >= 4 and t.lower() not in ("midterms", "election", "elections"): terms.add(t.lower())
    for x in feed.get("cyber", []):
        for p in x.get("places") or []:
            if len(p.get("name", "")) >= 4: terms.add(p["name"].lower())
    # Country names alone no longer qualify a headline (sport, trade and culture news crowded out the window); with a
    # security word they still do, through SECURITY.
    terms -= {n.lower() for n in wwdata.COUNTRIES.values()}
    parts = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(parts) + r")", re.I)

def relevant(item, terms):
    """Kept for the app and the ratings: hinted, about security, about a hotspot by name, or AI with catastrophic risk.
    Tags AI-risk headlines with the hint 'ai-risk' (feeds marked topic 'ai' need a risk word; 'ai-safety' ones don't)."""
    text = item["t"] + " " + item.get("x", "")
    hints = item.get("h") or []
    if "ai-safety" in hints or is_ai_risk(text) or ("ai-risk" in hints and AI_RISK.search(text)):
        item["h"] = sorted((set(hints) - {"ai-safety"}) | {"ai-risk"})
        return True
    if "ai-risk" in hints: item["h"] = [h for h in hints if h != "ai-risk"]
    if item["h"]: return True
    return bool(SECURITY.search(text) or terms.search(text))

# ---------- translation ----------
SYSTEM = ("You translate news headlines into plain English for a news-monitoring app. Each input line is 'N<TAB>headline'. "
          "For each, output exactly one line 'N|xx|English headline', where xx is the two-letter code of the original language. "
          "Translate faithfully: keep names, numbers, quotes and the outlet's framing (don't soften or sharpen it), use the usual "
          "English spelling of places and people, and add nothing. Output only those lines.")

def translate_requests(items):
    """100 headlines per request, numbered 1..100 inside it (short numbers cost fewer tokens than keys)."""
    reqs, chunk = [], 100
    for i in range(0, len(items), chunk):
        part = items[i:i + chunk]
        reqs.append({"custom_id": f"t{i // chunk}", "params": {
            "model": MODEL, "max_tokens": 8000, "system": SYSTEM,
            # Haiku 5.5 thinks by default; thinking tokens are billed and count against max_tokens. Translation needs none.
            "thinking": {"type": "disabled"}, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": "\n".join(f"{n + 1}\t{x['o']}" for n, x in enumerate(part))}]}})
    return reqs

_LINE = re.compile(r"^\s*(\d{1,3})\s*\|\s*([a-zA-Z]{2,3})\s*\|\s*(.+?)\s*$")

def parse_translations(text, keys=None):
    """'N|xx|English' lines -> {key: (lang, english)}; keys[N-1] is the headline key (or N itself without keys)."""
    out = {}
    for line in str(text).splitlines():
        m = _LINE.match(line)
        if not m: continue
        n, lang, en = int(m.group(1)), m.group(2).lower(), m.group(3).strip().strip('"')
        if keys is not None:
            if not 1 <= n <= len(keys): continue
            k = keys[n - 1]
        else: k = str(n)
        if en and len(en) < 400: out[k] = (lang[:3], en)
    return out

def load_spend():
    month = datetime.date.today().isoformat()[:7]
    try: s = json.load(open(os.path.join(LIVE, "spend.json"), encoding="utf-8"))
    except Exception: s = {}
    if s.get("month") != month: s = {"month": month, "usd": 0.0, "titles": 0, "runs": []}
    s["cap"] = SUB_CAP
    return s

def book(spend, usage, titles):
    usd = wwapi.usage_usd(MODEL, usage, batch=True)
    spend["usd"] = round(spend["usd"] + usd, 4)
    spend["titles"] = spend.get("titles", 0) + titles
    spend["runs"] = (spend.get("runs") or [])[-60:] + [{"at": wwapi.now_iso(), "titles": titles, "in": usage.get("input_tokens", 0),
                                                        "out": usage.get("output_tokens", 0), "usd": round(usd, 4)}]
    return usd

def save_progress(state, spend):
    """Pending batches and money spent are written at once, so a crash later in the run can't lose them."""
    save_json(os.path.join(LIVE, "spend.json"), spend)
    save_json(os.path.join(LIVE, "state.json"), {**load_json(os.path.join(LIVE, "state.json"), {}),
                                                 "pending": state.get("pending", []), "tr": state.get("tr", {})}, indent=None)

def collect_batch(b, spend, cache, keys_by_req):
    """Read a finished batch's results into the translation cache and book what it cost."""
    lines = wwapi.http("GET", b["results_url"], raw=True, timeout=300).splitlines()
    usage, n, notes = wwapi.empty_usage(), 0, {}
    for line in lines:
        if not line.strip(): continue
        res = json.loads(line)
        out = res.get("result") or {}
        if out.get("type") != "succeeded":
            notes[out.get("type", "?")] = notes.get(out.get("type", "?"), 0) + 1; continue
        msg = out["message"]
        wwapi.add_usage(usage, msg.get("usage"))
        text = "".join(x.get("text", "") for x in msg.get("content") or [] if x.get("type") == "text")
        keys = keys_by_req.get(res.get("custom_id"), [])
        got = parse_translations(text, keys)
        for k, (lang, en) in got.items():
            cache[k] = {"en": en, "l": lang, "at": now_ts()}; n += 1
        if len(got) < 0.9 * max(1, len(keys)):
            stop = msg.get("stop_reason")
            notes[f"short:{stop}"] = notes.get(f"short:{stop}", 0) + 1
            bad = [l for l in text.splitlines() if l.strip() and not _LINE.match(l)][:2]
            print(f"      {res.get('custom_id')}: {len(got)} of {len(keys)} parsed, stop={stop}, out={msg.get('usage', {}).get('output_tokens')}; "
                  f"unparsed: {bad!r}"[:400])
    if notes: warn(f"translation batch notes: {notes}")
    usd = book(spend, usage, n)
    ok(f"translations: {n} headlines from batch {b['id']}, about ${usd:.3f} (month ${spend['usd']:.2f} of ${SUB_CAP:.0f})")
    return n

def translate(queue, state, spend, budget_total):
    """Finish earlier batches, then send this run's untranslated headlines as a new batch and wait a while for it."""
    cache = state.setdefault("tr", {})
    pending = state.setdefault("pending", [])
    still = []
    for p in pending:
        try:
            b = wwapi.http("GET", f"{wwapi.API}/messages/batches/{p['id']}")
            if b.get("processing_status") == "ended": collect_batch(b, spend, cache, p.get("reqs", {}))
            elif now_ts() - p["at"] < 24 * 3600: still.append(p)
            else: warn(f"batch {p['id']} still not done after a day; dropped")
        except wwapi.CallFailed as e:
            warn(f"couldn't check batch {p['id']}: {e}"); still.append(p)
    state["pending"] = still
    in_flight = {k for p in still for k in p.get("keys", [])}
    todo = [x for x in queue if x["k"] not in cache and x["k"] not in in_flight][:MAX_TRANSLATE]
    if not todo: return
    if spend["usd"] >= SUB_CAP:
        warn(f"translation paused: ${spend['usd']:.2f} of its ${SUB_CAP:.0f} monthly limit used ({len(todo)} non-English headlines skipped)"); return
    if budget_total >= wwapi.MONTHLY_CAP_USD - 1:
        warn(f"translation paused: War Watch is at ${budget_total:.2f} of ${wwapi.MONTHLY_CAP_USD:.0f} this month"); return
    reqs = translate_requests(todo)
    keys_by_req = {f"t{i}": [x["k"] for x in todo[i * 100:(i + 1) * 100]] for i in range(len(reqs))}
    try:
        b = wwapi.http("POST", wwapi.API + "/messages/batches", {"requests": reqs})
    except wwapi.CallFailed as e:
        warn(f"couldn't send translations: {e}"); return
    step(f"translating {len(todo)} headlines (batch {b.get('id')})")
    p = {"id": b["id"], "at": now_ts(), "keys": [x["k"] for x in todo], "reqs": keys_by_req}
    save_progress({**state, "pending": state["pending"] + [p]}, spend)
    deadline = time.time() + WAIT_MIN * 60
    while b.get("processing_status") != "ended" and time.time() < deadline:
        time.sleep(POLL_S)
        try: b = wwapi.http("GET", f"{wwapi.API}/messages/batches/{b['id']}")
        except wwapi.CallFailed as e: warn(f"batch check failed: {e}")
    if b.get("processing_status") == "ended":
        collect_batch(b, spend, cache, keys_by_req); save_progress(state, spend)
    else:
        warn(f"batch {b['id']} not done in {WAIT_MIN:.0f} min; its translations land next hour")
        state["pending"].append(p)

# ---------- main ----------
def load_json(path, default):
    try: return json.load(open(path, encoding="utf-8"))
    except Exception: return default

def merge(old_items, new_items, cutoff):
    by = {}
    for x in old_items + new_items:                       # newer copies (and GDELT hints) win, hints add up
        if (x.get("p") or 0) and x["p"] < cutoff: continue
        prev = by.get(x["k"])
        if prev:
            x = {**prev, **{k: v for k, v in x.items() if v}, "h": sorted(set(prev.get("h", [])) | set(x.get("h", [])))}
            if prev.get("p"): x["p"] = min(prev["p"], x.get("p") or prev["p"])
        by[x["k"]] = x
    return list(by.values())

def main():
    os.makedirs(LIVE, exist_ok=True)
    feed = load_json(os.path.join(ROOT, "feed.json"), {})
    sources = load_json(os.path.join(ROOT, "config", "sources.json"), {"rss": [], "gnews": []})
    state = load_json(os.path.join(LIVE, "state.json"), {"tr": {}, "pending": []})
    spend = load_spend()
    budget = load_json(os.path.join(ROOT, "budget.json"), {})
    month = datetime.date.today().isoformat()[:7]
    analysis_usd = budget.get("usd", 0.0) if budget.get("month") == month else 0.0
    t0 = now_ts()
    cutoff = t0 - WINDOW_H * 3600

    step(f"collecting: {len(sources['rss'])} outlet feeds, {len(sources['gnews'])} Google News editions, GDELT per hotspot")
    rss, rss_ok, rss_bad = collect_rss(sources["rss"])
    ok(f"outlets: {len(rss)} headlines from {rss_ok} of {len(sources['rss'])} feeds")
    if rss_bad: print("      not answering: " + "; ".join(rss_bad[:40]))
    gn, gn_ok, gn_bad = collect_gnews(sources["gnews"])
    ok(f"Google News: {len(gn)} headlines from {gn_ok} of {len(sources['gnews'])} editions" + (f" (failed: {', '.join(gn_bad)})" if gn_bad else ""))
    se, se_ok, se_bad = collect_search(sources.get("search") or [])
    ok(f"Google News searches: {len(se)} headlines from {se_ok} of {len(sources.get('search') or [])} searches" +
       (f" (failed: {', '.join(se_bad[:20])})" if se_bad else ""))
    gd, gd_ok, gd_n, gd_bad = collect_gdelt(feed, os.environ.get("WW_GDELT_TIMESPAN", "1d"))
    ok(f"GDELT: {len(gd)} headlines from {gd_ok} of {gd_n} searches" + (f" (busy for: {', '.join(gd_bad[:12])})" if gd_bad else ""))

    fresh = [x for x in rss + gn + se + gd if not x["p"] or x["p"] >= cutoff]
    for x in fresh:
        if not x["p"]: x["p"] = t0                          # undated items: first seen now
        if x["p"] > t0 + 600: x["p"] = t0
    items = merge(state.get("items") or [], fresh, cutoff)

    # translation: cached ones first, then the rest by batch
    cache = state.setdefault("tr", {})
    queue = sorted([x for x in items if not x["t"] and x["k"] not in cache], key=lambda x: (not x["h"], -x["p"]))
    translate(queue, state, spend, analysis_usd + spend["usd"])
    for x in items:
        if not x["t"] and x["k"] in cache:
            x["t"] = cache[x["k"]]["en"]
            if x["l"] in ("xx", "", None): x["l"] = cache[x["k"]]["l"]

    terms = term_regex(feed)
    shown = [x for x in items if x["t"] and relevant(x, terms)]
    shown.sort(key=lambda x: -x["p"])
    shown = shown[:MAX_ITEMS]
    waiting = sum(1 for x in items if not x["t"])

    # forget translations of headlines that left the window
    keep = {x["k"] for x in items}
    state["tr"] = {k: v for k, v in cache.items() if k in keep or v.get("at", 0) > cutoff}
    langs = sorted({x["l"] for x in shown})
    out = {"generated": iso(t0), "window_hours": WINDOW_H,
           "stats": {"items": len(shown), "outlets": len({x["d"] or x["s"] for x in shown}),
                     "countries": len({x["c"] for x in shown if x["c"]}), "languages": len(langs),
                     "translated": sum(1 for x in shown if x["l"] != "en"), "waiting_translation": waiting,
                     "feeds_ok": rss_ok, "feeds": len(sources["rss"]), "gnews_ok": gn_ok, "gnews": len(sources["gnews"]),
                     "search_ok": se_ok, "search": len(sources.get("search") or []),
                     "ai_risk": sum(1 for x in shown if "ai-risk" in (x.get("h") or [])),
                     "gdelt_ok": gd_ok, "gdelt": gd_n},
           "languages": langs,
           "items": [{k: v for k, v in (("u", x["u"]), ("t", x["t"]), ("o", x["o"] if x["l"] != "en" else ""), ("l", x["l"]),
                                         ("s", x["s"]), ("d", x["d"]), ("c", x["c"]), ("p", x["p"]), ("v", x["v"]),
                                         ("h", x["h"]), ("x", x.get("x", ""))) if v not in ("", [], None)} for x in shown]}
    save_json(os.path.join(LIVE, "headlines.json"), out, indent=None)
    # the full window (also the irrelevant and untranslated ones) so the next run doesn't fetch-and-translate twice
    save_json(os.path.join(LIVE, "state.json"), {"tr": state["tr"], "pending": state.get("pending", []),
                                                 "items": [x for x in items if x["p"] >= cutoff]}, indent=None)
    save_json(os.path.join(LIVE, "spend.json"), spend)
    st = out["stats"]
    ok(f"headlines.json: {st['items']} headlines from {st['outlets']} outlets in {st['countries']} countries, "
       f"{st['languages']} languages ({st['translated']} translated; {waiting} waiting for translation)")
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as s:
        s.write(f"### War Watch headlines\n\n{st['items']} headlines, {st['outlets']} outlets, {st['countries']} countries, "
                f"{st['languages']} languages ({st['translated']} translated). Feeds {rss_ok}/{len(sources['rss'])}, "
                f"Google News {gn_ok}/{len(sources['gnews'])}, GDELT {gd_ok}/{gd_n}. Translation this month: ${spend['usd']:.2f} of ${SUB_CAP:.0f}.\n")

if __name__ == "__main__":
    main()
