"""Tests for the War Watch feed job, with a fake Claude API (no network, no cost).  python3 -m unittest -v tools/test_feed.py"""
import json, os, shutil, sys, tempfile, unittest, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wwapi, wwdata, wwscore, wwboil

class FakeSources:
    """The boiling point's public data, offline."""
    def __init__(self, today): pass
    def us_advisories(self): return {"IR": {"level": 4, "title": "Iran - Level 4: Do Not Travel", "date": "2026-10-30", "url": "https://travel.state.gov/x"}}
    def uk_advice(self, code): return None
    def fx(self): return {"now": {"ils": 3.80}, "d7": {"ils": 3.70}, "d30": {"ils": 3.60}}
    def oil(self): return {"price": 81.4, "date": "2026-10-31", "change_7d": 6.2, "change_30d": 9.0}
    def outages(self, code): return {"alerts": 3, "critical": 1} if code == "IR" else None
    def markets(self, words): return []

TODAY = datetime.date.today().isoformat()

def note(i, hotspot, url, outlet, country, fact, **kw):
    return {"hotspot": hotspot, "event": kw.get("event", "e"), "date": kw.get("date", TODAY), "fact": fact, "outlet": outlet,
            "country": country, "url": url, "origin": kw.get("origin", "own"), "claim_by": kw.get("claim_by", "reporters"),
            "state_media": kw.get("state_media", False), "stance": kw.get("stance", "neutral"), "frame": "", "tripwire": ""}

class FakeAPI:
    """Answers /messages and the batch endpoints from canned replies, in order of request labels."""
    def __init__(self, replies, batch_ok=True):
        self.replies, self.batch_ok, self.calls, self.batches = replies, batch_ok, [], {}
    def reply_for(self, body):
        u = body["messages"][0]["content"]
        for key, rep in self.replies.items():
            if key in u or key in body["system"]: return rep
        raise AssertionError("no canned reply")
    def __call__(self, method, url, body=None, timeout=900, raw=False):
        self.calls.append((method, url))
        if url.endswith("/messages"): return self.reply_for(body)
        if url.endswith("/messages/batches") and method == "POST":
            if not self.batch_ok: raise wwapi.CallFailed("batches off")
            bid = f"b{len(self.batches)}"
            self.batches[bid] = [(r["custom_id"], self.reply_for(r["params"])) for r in body["requests"]]
            return {"id": bid, "processing_status": "in_progress"}
        if "/messages/batches/" in url and method == "GET" and not url.endswith("results"):
            bid = url.rsplit("/", 1)[1]
            return {"id": bid, "processing_status": "ended", "results_url": f"https://api/{bid}/results"}
        if url.endswith("/results"):
            bid = url.split("/")[-2]
            return "\n".join(json.dumps({"custom_id": cid, "result": {"type": "succeeded", "message": msg}}) for cid, msg in self.batches[bid])
        raise AssertionError(url)

def msg(text, urls=(), searches=0, tin=1000, tout=500):
    content = []
    if urls: content.append({"type": "web_search_tool_result", "tool_use_id": "x", "content": [{"type": "web_search_result", "url": u, "title": "t"} for u in urls]})
    content.append({"type": "text", "text": text})
    return {"content": content, "stop_reason": "end_turn",
            "usage": {"input_tokens": tin, "output_tokens": tout, "server_tool_use": {"web_search_requests": searches}}}

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        src = os.path.dirname(HERE)
        # a frozen copy of the feed, so tests never depend on what the live ratings say today
        shutil.copy(os.path.join(HERE, "fixtures", "feed.json"), self.dir)
        shutil.copy(os.path.join(src, "outlets.json"), self.dir)
        for d in ("prompt", "config"): shutil.copytree(os.path.join(src, d), os.path.join(self.dir, d))
        os.environ["ANTHROPIC_API_KEY"] = "test"
        import update_feed
        self.uf = update_feed
        self.paths = (update_feed.FEED, update_feed.HISTORY, update_feed.OUTLOOKS, update_feed.NOTES_DIR, wwapi.BUDGET, update_feed.ROOT, wwapi.ROOT)
        update_feed.FEED = os.path.join(self.dir, "feed.json"); update_feed.HISTORY = os.path.join(self.dir, "history.json")
        update_feed.OUTLOOKS = os.path.join(self.dir, "outlooks.json"); update_feed.NOTES_DIR = os.path.join(self.dir, "notes")
        update_feed.ROOT = self.dir; wwapi.BUDGET = os.path.join(self.dir, "budget.json")
        self.sleep = wwapi.time.sleep; wwapi.time.sleep = lambda s: None
        os.makedirs(os.path.join(self.dir, "live"))
        shutil.copy(os.path.join(HERE, "fixtures", "headlines.json"), os.path.join(self.dir, "live", "headlines.json"))
        json.dump({"month": TODAY[:7], "usd": 0.5}, open(os.path.join(self.dir, "live", "spend.json"), "w"))
        self.boil = (update_feed.LIVE, update_feed.BOIL_GDELT, wwboil.Sources)
        update_feed.LIVE = os.path.join(self.dir, "live"); update_feed.BOIL_GDELT = False; wwboil.Sources = FakeSources
        self.http = wwapi.http
    def tearDown(self):
        uf = self.uf
        uf.FEED, uf.HISTORY, uf.OUTLOOKS, uf.NOTES_DIR, wwapi.BUDGET, uf.ROOT, wwapi.ROOT = self.paths
        wwapi.time.sleep = self.sleep; wwapi.http = self.http
        uf.LIVE, uf.BOIL_GDELT, wwboil.Sources = self.boil
        uf.CORPUS.clear(); uf.CORPUS["items"] = []
        shutil.rmtree(self.dir)

class DataTests(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(wwdata.iso("UK"), "GB"); self.assertEqual(wwdata.iso("Kenya (dateline)"), "KE")
        self.assertEqual(wwdata.iso("Republic of the Congo"), "CG"); self.assertEqual(wwdata.iso("DR Congo"), "CD")
        self.assertEqual(wwdata.iso("International"), "UN"); self.assertEqual(wwdata.iso("Narnia"), "")

    def test_registry_and_keys(self):
        n = {"url": "https://www.themoscowtimes.com/2026/11/03/x", "country": "Netherlands", "outlet": "The Moscow Times"}
        src = wwdata.note_source(n)
        self.assertEqual((src["home"], src["region"], src["media"]), ("RU", "russia", "exile"))
        # five reprints of one AP story are one source
        keys = set()
        for host in ("bostonglobe.com", "pbs.org", "wtop.com", "washingtonpost.com", "ca.news.yahoo.com"):
            m = {"url": f"https://{host}/a", "country": "US", "outlet": "AP via X", "origin": "wire:AP"}
            m.update(wwdata.note_source(m)); keys.add(wwdata.indep_key(m))
        self.assertEqual(keys, {"wire:ap"})
        # relays of one ministry statement are one source
        a = {"claim_by": "party:Russia defence ministry", "origin": "own", "outlet_id": "a"}
        b = {"claim_by": "party:Russia's Defence Ministry", "origin": "own", "outlet_id": "b"}
        self.assertEqual(wwdata.indep_key(a), wwdata.indep_key(b))

    def test_party_tags_are_symmetric(self):
        parties = wwdata.parties_iso(["United States", "Israel", "Iran"])
        us = {"home": "US", "media": "private", "claim_by": "reporters"}
        ir = {"home": "IR", "media": "state-controlled", "claim_by": "reporters"}
        qa = {"home": "QA", "media": "state-funded", "claim_by": "reporters"}
        self.assertEqual(wwdata.party_tag(us, parties), "party-press")
        self.assertEqual(wwdata.party_tag(ir, parties), "party-gov")
        self.assertEqual(wwdata.party_tag(qa, parties), "non-party")

    def test_clean_notes_drops_invented_urls(self):
        raw = [note(1, "x", "https://www.bbc.com/news/real", "BBC", "UK", "Real fact here."),
               note(2, "x", "https://www.bbc.com/news/made-up", "BBC", "UK", "Invented.")]
        seen = {wwapi.norm_url("https://bbc.com/news/real/")}
        out, dropped = wwdata.clean_notes(raw, seen, {}, TODAY)
        self.assertEqual([n["fact"] for n in out], ["Real fact here."]); self.assertEqual(dropped["url"], 1)

class ScoreTests(unittest.TestCase):
    def mk(self, i, home, region, key, tag="non-party", fact="Strikes hit the port.", date=TODAY):
        return {"id": f"n{i}", "home": home, "region": region, "key": key, "tag": tag, "date": date, "fact": fact,
                "outlet": "o", "url": f"https://x/{i}", "media": "private"}

    def test_numbers_check(self):
        notes = [{"fact": "Officials said 31 people died on Nov 3, 2026, and oil rose 4%."}]
        self.assertEqual(wwscore.numbers_supported("31 people died and oil rose 4%.", notes), set())
        self.assertEqual(wwscore.numbers_supported("Oil hit $107 a barrel.", notes), {"107"})
        txt, removed = wwscore.drop_unsupported_sentences("31 died. Oil hit $107. Talks continue.", notes)
        self.assertEqual((txt, removed), ("31 died. Talks continue.", 1))

    def test_signal_needs_two_nonparty_regions(self):
        ns = {n["id"]: n for n in [self.mk(1, "US", "north-america", "wire:ap"), self.mk(2, "US", "north-america", "outlet:cnn")]}
        logs = []
        s = wwscore.score_signal({"id": "a", "title": "Two US outlets agree", "claim": "x" * 30, "for": ["n1", "n2"]}, ns, {}, TODAY, {"h"}, logs.append)
        self.assertIsNone(s)
        ns["n3"] = self.mk(3, "QA", "gulf", "outlet:aj")
        s = wwscore.score_signal({"id": "a", "title": "Outlets in two regions agree", "claim": "x" * 30, "for": ["n1", "n2", "n3"],
                                  "hotspots": ["h"], "confidence": "high"}, ns, {}, TODAY, {"h"}, logs.append)
        self.assertEqual((s["stats"]["keys_all"], s["stats"]["regions_np"], s["confidence"]), (3, 2, "medium"))
        self.assertEqual(s["countries"], ["United States", "Qatar"])

    def test_party_claims_never_trip_a_tripwire(self):
        tw = {"id": "tw-1", "status": "not_seen", "since": TODAY}
        ns = {"n1": self.mk(1, "RU", "russia", "claim:russia mod", "party-gov"), "n2": self.mk(2, "RU", "russia", "outlet:tass", "party-gov")}
        logs = []
        wwscore.tripwire_update(tw, {"status": "tripped", "notes": ["n1", "n2"]}, ns, TODAY, logs.append)
        self.assertEqual(tw["status"], "signs")
        ns["n3"] = self.mk(3, "GB", "europe", "outlet:bbc")
        wwscore.tripwire_update(tw, {"status": "tripped", "notes": ["n1", "n3"]}, ns, TODAY, logs.append)
        self.assertEqual(tw["status"], "tripped")

class JobTests(Base):
    def gather_text(self):
        f = [note(1, "us-israel-iran-war", "https://www.aljazeera.com/news/2026/11/3/a", "Al Jazeera", "Qatar", "A US strike hit Qeshm island; 4 people were killed."),
             note(2, "us-israel-iran-war", "https://www.bbc.com/news/qeshm", "BBC", "UK", "BBC reports a US strike on Qeshm island killing 4."),
             note(3, "us-israel-iran-war", "https://www.presstv.ir/x", "Press TV", "Iran", "Iran's state media says a US drone was downed.", state_media=True, claim_by="party:Iran military"),
             note(4, "russia-ukraine-war", "https://www.thehindu.com/a", "The Hindu", "India", "Talks in Abu Dhabi resumed on Monday.", stance="easing"),
             note(5, "russia-ukraine-war", "https://www.dw.com/en/b", "DW", "Germany", "Delegations met in Abu Dhabi for talks.", stance="easing"),
             note(6, "russia-ukraine-war", "https://www.dw.com/en/invented", "DW", "Germany", "Invented URL note."),
             note(7, "cyber: french-cold-chain-hack", "https://www.cisa.gov/news-events/alerts/x", "CISA", "US", "CISA urged operators to take refrigeration controllers off the internet."),
             note(8, "cyber: french-cold-chain-hack", "https://www.reuters.com/world/x", "Reuters", "UK", "The Pentagon is treating August freezer failures at bases as a suspected hack."),
             ] + [note(9 + i, "sudan-civil-war", f"https://www.dabangasudan.org/en/{i}", "Radio Dabanga", "Sudan", f"Fact {i} about El Fasher.") for i in range(4)]
        urls = [x["url"] for x in f if "invented" not in x["url"]]
        return msg("```json\n" + json.dumps({"findings": f}) + "\n```", urls, searches=12, tin=200000, tout=10000)

    def analysis_text(self):
        p = {"updates": [{"id": "us-israel-iran-war", "severity": "red", "trend": "escalating", "notes": ["n01", "n02", "n03"],
                          "status": "US strike on Qeshm island; Iran says it downed a drone"},
                         {"id": "russia-ukraine-war", "severity": "yellow", "notes": ["n04"], "trend": "de-escalating"},
                         {"id": "haiti-gang-war", "us_impact": {"level": "direct"}, "notes": ["n04"]}],
             "watch_list": [{"id": "us-israel-iran-war", "why": "US forces are fighting and Gulf shipping is at risk."}],
             "brief": ["The US struck Qeshm island; 4 killed per two outlets.", "Oil jumped 12% overnight.", "Ukraine talks resumed in Abu Dhabi."],
             "signals": [{"id": "qeshm", "title": "Outlets in two regions report a US strike on Qeshm", "claim": "A US strike hit Qeshm; 4 were killed.",
                          "detail": "Al Jazeera and the BBC report it. 500 troops were moved.", "direction": "escalating", "confidence": "high",
                          "hotspots": ["us-israel-iran-war"], "for": ["n01", "n02", "n03"], "against": []}],
             "frames": [{"id": "qeshm", "hotspot": "us-israel-iran-war", "topic": "Qeshm strike", "agreed": "A strike happened.",
                         "versions": [{"region": "gulf", "says": "four killed", "notes": ["n01"]}, {"region": "iran", "says": "drone downed", "notes": ["n03"]}]}],
             "cyber": [{"id": "french-cold-chain-hack", "title": "Hackers heated supermarket freezers in France", "date": "2026-10-12",
                        "places": [{"name": "France", "lat": 46.6, "lon": 2.4}, {"name": "Fort Huachuca", "lat": 31.6, "lon": -110.3}],
                        "sector": "food", "method": "Internet-exposed refrigeration controllers set to defrost", "summary": "s",
                        "actor": {"name": "unknown", "confidence": "unknown"}, "us_status": "suspected_in_us",
                        "us_why": "The Pentagon treats August freezer failures at US bases as a suspected hack.", "notes": ["n06", "n07"]}],
             "rank": ["us-israel-iran-war", "russia-ukraine-war", "sudan-civil-war", "black-sea-shipping-attacks", "yemen-houthi-saudi-war"],
             "log": "test"}
        return msg("```json\n" + json.dumps(p) + "\n```", tin=30000, tout=8000)

    def boil_text(self):
        b = {"boil": [{"id": "us-israel-iran-war", "question": "Will the US-Iran fighting widen into a regional war?", "stage": 4,
                       "likelihood": "roughly even chance", "likelihood_of": "a wider regional war within 30 days", "direction": "rising",
                       "summary": "Strikes continue and Iran says its southern airspace is closed. Oil rose 6.2% in a week. 900 ships are stuck.",
                       "drivers": [{"text": "Iran says airspace over the south is closed", "cites": ["h2"]},
                                   {"text": "Brent rose 6.2% in a week", "cites": ["d4"]},
                                   {"text": "Shipping insurers pulled 40% of cover", "cites": ["h1"]}],
                       "brakes": [{"text": "Mediators still talk", "cites": ["zz9"]}],
                       "tip": "A US warship hit in the Gulf", "offramp": "Both sides confirm a pause",
                       "watch": ["Gulf shipping"], "agree": "Data and headlines both point up.", "sides": "Mostly Gulf and Iranian outlets."},
                      {"id": "not-a-candidate", "stage": 5}]}
        return msg("```json\n" + json.dumps(b) + "\n```", tin=12000, tout=3000)

    def run_daily(self, batch=True):
        fake = FakeAPI({"Focus:": self.gather_text(), "Yesterday's ladder": self.boil_text(), "NOTES gathered today": self.analysis_text()}, batch_ok=batch)
        wwapi.http = fake
        sys.argv = ["x", "daily"]
        self.uf.main()
        return fake, json.load(open(self.uf.FEED, encoding="utf-8"))

    def test_daily_end_to_end(self):
        fake, feed = self.run_daily()
        self.assertEqual(feed["schema"], 2)
        iran = next(c for c in feed["conflicts"] if c["id"] == "us-israel-iran-war")
        self.assertIn("Qeshm", iran["status"])
        self.assertTrue(iran["us_watch"])
        self.assertEqual(sum(1 for c in feed["conflicts"] if c.get("us_watch")), 1)
        self.assertEqual(iran["rank"], 1)
        ru = next(c for c in feed["conflicts"] if c["id"] == "russia-ukraine-war")
        self.assertEqual(ru["severity"], "yellow", "a calmer call needs one cited note, not two")
        haiti = next(c for c in feed["conflicts"] if c["id"] == "haiti-gang-war")
        self.assertNotEqual(haiti["us_impact"]["level"], "direct", "moving up to direct needs 2 independent sources")
        self.assertEqual(ru["trend"], "de-escalating")
        self.assertFalse(any("invented" in d["url"] for c in feed["conflicts"] for d in c["latest"]))
        self.assertEqual(feed["brief"]["lines"], ["The US struck Qeshm island; 4 killed per two outlets.", "Ukraine talks resumed in Abu Dhabi."],
                         "the brief line with an unbacked 12% must be dropped")
        sig = next(s for s in feed["signals"] if s["id"] == "qeshm")
        self.assertNotIn("500", sig["detail"])
        self.assertEqual(sig["stats"]["keys_all"], 3); self.assertEqual(sig["stats"]["regions_np"], 2)
        self.assertIn("qeshm", [f["id"] for f in feed["frames"]])
        cy = next(x for x in feed["cyber"] if x["id"] == "french-cold-chain-hack")
        self.assertEqual(cy["us_status"], "suspected_in_us"); self.assertEqual(cy["advisories"][0]["url"], "https://www.cisa.gov/news-events/alerts/x")
        self.assertIn("tension", feed); self.assertTrue(os.path.exists(self.uf.HISTORY))
        b = json.load(open(wwapi.BUDGET))
        self.assertTrue(all(r["batch"] for r in b["runs"]))
        # Sonnet batch: 200k*$1 + 10k*$5 per M = 0.25, plus 12 searches 0.12
        self.assertAlmostEqual(b["runs"][0]["usd"], 0.37, places=2)
        self.assertTrue(any("/messages/batches" in u for _, u in fake.calls))
        # boiling point: checked by rule
        boil = {x["id"]: x for x in feed["boil"]}
        self.assertNotIn("not-a-candidate", boil)
        ir = boil["us-israel-iran-war"]
        self.assertEqual((ir["stage"], ir["stage_label"], ir["likelihood"]), (4, "Brink", "roughly even chance"))
        self.assertEqual([d["text"] for d in ir["drivers"]], ["Iran says airspace over the south is closed"] +
                         (["Brent rose 6.2% in a week"] if any(p["id"] == "d4" and p["kind"] == "oil" for p in ir["data"].get("oil", [])) else []))
        self.assertEqual(ir["brakes"], [], "a brake citing nothing real is dropped")
        self.assertNotIn("900", ir["summary"])
        self.assertTrue(ir["headlines"] and ir["headlines"][0]["u"].startswith("https://"))
        self.assertEqual(ir["data"]["advice_us"][0]["level"], 4)
        self.assertIn("who_reports", ir["data"])
        self.assertEqual(json.load(open(self.uf.HISTORY))["days"][-1]["boil"]["us-israel-iran-war"], 4)
        self.assertAlmostEqual(b.get("headlines_usd", 0), 0.5)

    def test_boil_ladder_moves_one_step(self):
        cands, panels = ["x"], {"x": [{"id": "d1", "kind": "coverage", "text": "Coverage at 3.1x the norm."}]}
        heads = {"x": [{"hid": "h1", "t": "Troops mass at the border", "u": "https://a"}]}
        log = []
        out = wwboil.check({"boil": [{"id": "x", "stage": 5, "likelihood": "likely", "drivers": [{"text": "Coverage 3.1x", "cites": ["d1"]}]}]},
                           cands, panels, heads, {}, {"x": {"stage": 2}}, TODAY, log)
        self.assertEqual(out[0]["stage"], 3)
        out = wwboil.check({"boil": [{"id": "x", "stage": 4, "likelihood": "imminent", "drivers": [{"text": "Coverage 5x", "cites": ["d1"]}],
                                      "brakes": [{"text": "Troops mass", "cites": ["h1"]}]}]}, cands, panels, heads, {}, {}, TODAY, log)
        self.assertEqual((out[0]["stage"], out[0]["likelihood"], out[0]["drivers"]), (4, "", []))
        self.assertEqual(wwboil.ratio([("2026%04d" % i, 1.0) for i in range(80)] + [("2026%04d" % (80 + i), 3.0) for i in range(7)])[0], 3.0)

    def test_weekly_sets_structure_and_never_drops_silently(self):
        review = {"updates": [{"id": "us-israel-iran-war", "notes": ["w01"], "parties": ["United States", "Israel", "Iran"], "interstate": True,
                               "type": "interstate war", "tripwires": [{"kind": "escalation", "text": "A US warship is hit in the Gulf"},
                                                                       {"kind": "easing", "text": "Both sides confirm a pause in strikes"}],
                               "week_verdict": {"verdict": "deteriorated", "why": "More strikes", "notes": ["w01"]}}],
                  "ended": [{"id": "haiti-gang-war", "why": "test without notes", "notes": []},
                            {"id": "ecuador-gang-conflict", "why": "Gangs agreed a truce in October", "notes": ["w04"]}]}
        fake = FakeAPI({"Region:": msg("```json\n" + json.dumps(review) + "\n```"), "Focus:": self.gather_text(), "Yesterday's ladder": self.boil_text(),
                        "NOTES gathered today": self.analysis_text()})
        wwapi.http = fake
        sys.argv = ["x", "weekly"]
        self.uf.main()
        feed = json.load(open(self.uf.FEED, encoding="utf-8"))
        ids = {c["id"] for c in feed["conflicts"]}
        self.assertIn("haiti-gang-war", ids, "a hotspot is only removed with a sourced 'ended'")
        self.assertNotIn("ecuador-gang-conflict", ids)
        iran = next(c for c in feed["conflicts"] if c["id"] == "us-israel-iran-war")
        self.assertEqual(iran["parties"], ["United States", "Israel", "Iran"]); self.assertEqual(iran["week_verdict"]["verdict"], "deteriorated")
        tws = [tw for tw in feed["tripwires"] if tw["hotspot"] == "us-israel-iran-war"]
        self.assertEqual(sorted(tw["kind"] for tw in tws), ["easing", "escalation"])
        self.assertIn("X", feed["tension"]["parts"]); self.assertIn("T", feed["tension"]["parts"])
        b = json.load(open(wwapi.BUDGET))
        self.assertEqual(sum(1 for r in b["runs"] if "notes" in r["what"]), 6)

    def test_direct_fallback_when_batches_fail(self):
        fake, feed = self.run_daily(batch=False)
        self.assertTrue(any(u.endswith("/messages") for _, u in fake.calls))
        b = json.load(open(wwapi.BUDGET))
        self.assertFalse(any(r.get("batch") for r in b["runs"]))

    def test_cap_pauses_and_fails(self):
        json.dump({"month": TODAY[:7], "usd": 99.0, "runs": []}, open(wwapi.BUDGET, "w"))
        sys.argv = ["x", "daily"]
        with self.assertRaises(SystemExit) as cm: self.uf.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("paused", json.load(open(self.uf.FEED)))

    def test_failed_call_is_booked(self):
        def boom(*a, **k):
            e = wwapi.CallFailed("network"); e.billable = True; raise e
        wwapi.http = boom
        b = wwapi.load_budget()
        out = wwapi.run([{"label": "x", "model": "claude-opus-5-5", "system": "s", "user": "u", "max_tokens": 10, "estimate": 0.3}], b, batch=False)
        self.assertEqual(out, {}); self.assertAlmostEqual(b["usd"], 0.3)

    def test_unknown_mode_refused(self):
        sys.argv = ["x", "dialy"]
        with self.assertRaises(SystemExit): self.uf.main()

if __name__ == "__main__":
    unittest.main()
