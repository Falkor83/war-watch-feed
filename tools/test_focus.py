"""Tests for the focus updates (tools/focus.py) and story grouping (tools/wwstories.py), with a fake Claude API.
python3 -m unittest -v tools/test_focus.py"""
import json, os, shutil, sys, tempfile, time, unittest, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wwapi, wwboil, wwstories, focus
from test_feed import FakeSources, msg

CID = "us-israel-iran-war"
NOW = int(time.time())

def item(t, c, s, d, mins=30, l="en", v="rss", h=()):
    return {"k": f"{d}{abs(hash(t)) % 10**8}", "u": f"https://{d}/{abs(hash(t)) % 10**8}", "o": t, "t": t, "l": l, "s": s,
            "d": d, "c": c, "p": NOW - mins * 60, "v": v, "h": list(h), "x": ""}

HEADLINES = [
    item("US strikes Iranian missile sites near Qeshm Island, Pentagon says", "US", "AP", "apnews.com", 20),
    item("Pentagon says US hit Iranian missile sites near Qeshm", "GB", "BBC", "bbc.co.uk", 25),
    item("US strikes missile sites on Iran's Qeshm Island", "DE", "DW", "dw.com", 30),
    item("Iran says US strikes on Qeshm killed 12 civilians", "IR", "IRNA", "irna.ir", 35, v="gnews"),
    item("Iran's IRGC vows response after Qeshm strikes", "IR", "Tasnim", "tasnimnews.com", 40, v="gnews"),
    item("Tankers wait outside Strait of Hormuz as insurers raise rates", "IN", "The Hindu", "thehindu.com", 50),
    item("Tankers queue at Strait of Hormuz as war insurance rates climb", "SG", "Straits Times", "straitstimes.com", 55),
    item("Oman offers to host new Iran-US talks", "QA", "Al Jazeera", "aljazeera.com", 60),
    item("Oman offers to host fresh talks between Iran and the US", "FR", "France 24", "france24.com", 70),
    item("Netanyahu alleges foreign interference in Israeli election", "IN", "Hindustan Times", "hindustantimes.com", 45),
]

def answer(points, flag=None, headline="US strikes Qeshm missile sites; Oman offers talks"):
    return "```json\n" + json.dumps({"headline": headline, "direction": "escalating", "points": points,
                                      "watch": ["Iran's response"], "flag": flag or {"step_up": False}}) + "\n```"

class FakeAPI:
    def __init__(self, focus_text, boil_text=None):
        self.focus_text, self.boil_text, self.calls = focus_text, boil_text, []
    def __call__(self, method, url, body=None, timeout=900, raw=False):
        self.calls.append(body["model"])
        if "news-monitoring app" in body["system"]: return msg(self.focus_text, tin=6000, tout=2500)
        if self.boil_text is None: raise AssertionError("no ladder re-check expected")
        return msg(self.boil_text, tin=15000, tout=4000)

class StoryTests(unittest.TestCase):
    def test_same_event_in_other_words_is_one_story(self):
        st = wwstories.cluster(HEADLINES)
        sizes = sorted((len(s["items"]) for s in st), reverse=True)
        self.assertGreaterEqual(sizes[0], 3)                  # the Qeshm strike, AP + BBC + DW (+ Iran's version)
        tankers = [s for s in st if any("Tankers" in x["t"] for x in s["items"])]
        self.assertEqual(len(tankers), 1)
        self.assertEqual(len(wwstories.tokens("Israeli election") & wwstories.tokens("Israel elections")), 2)

    def test_sides_only_and_tiers(self):
        s = {"items": [HEADLINES[3], HEADLINES[4]]}
        st = wwstories.stats(s, {"US", "IL", "IR"})
        self.assertTrue(st["party_only"])
        self.assertEqual(wwstories.tier(HEADLINES[0]), "known")                    # one of the picked outlet feeds
        self.assertEqual(wwstories.tier(item("x y z words here", "XX", "Blog", "randomblog.example", v="gdelt")), "unrated")

class FocusTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        src = os.path.dirname(HERE)
        shutil.copy(os.path.join(HERE, "fixtures", "feed.json"), self.dir)
        for d in ("prompt", "config"): shutil.copytree(os.path.join(src, d), os.path.join(self.dir, d))
        os.makedirs(os.path.join(self.dir, "live"))
        json.dump({"generated": "x", "items": HEADLINES}, open(os.path.join(self.dir, "live", "headlines.json"), "w"))
        json.dump({"month": datetime.date.today().isoformat()[:7], "usd": 0.5}, open(os.path.join(self.dir, "live", "spend.json"), "w"))
        os.environ["ANTHROPIC_API_KEY"] = "test"
        self.saved = (focus.ROOT, focus.LIVE, focus.FEED, focus.CFG, focus.OUT, focus.FOCUS_BUDGET, wwapi.BUDGET, wwapi.http,
                      wwboil.Sources, focus.ROOT)
        focus.ROOT, focus.LIVE = self.dir, os.path.join(self.dir, "live")
        focus.FEED, focus.CFG = os.path.join(self.dir, "feed.json"), os.path.join(self.dir, "config", "focus.json")
        focus.OUT, focus.FOCUS_BUDGET = os.path.join(self.dir, ".focus_out.json"), os.path.join(self.dir, "focus_budget.json")
        wwboil.Sources = FakeSources
        self.argv = sys.argv
    def tearDown(self):
        (focus.ROOT, focus.LIVE, focus.FEED, focus.CFG, focus.OUT, focus.FOCUS_BUDGET, wwapi.BUDGET, wwapi.http,
         wwboil.Sources, _) = self.saved
        sys.argv = self.argv
        shutil.rmtree(self.dir)
    def feed(self): return json.load(open(focus.FEED))
    def run_focus(self, api, *args):
        wwapi.http = api; sys.argv = ["focus.py", *args]
        focus.main()
    def sids(self):
        """Story ids as the model would see them."""
        feed = self.feed(); c = next(x for x in feed["conflicts"] if x["id"] == CID)
        mine = focus.window_items(c, HEADLINES, feed["conflicts"], NOW - 6 * 3600)
        st = focus.make_stories(c, mine)
        find = lambda w: next(f"s{i + 1}" for i, s in enumerate(st) if any(w in x["t"] for x in s["items"]))
        return find("Pentagon"), find("Tankers"), find("Oman"), find("IRGC"), st

    def test_update_lands_in_the_feed_with_its_sources(self):
        strike, tankers, oman, irgc, _ = self.sids()
        api = FakeAPI(answer([
            {"text": "US forces struck missile sites near Qeshm, the Pentagon says.", "stories": [strike]},
            {"text": "Iran says 40 civilians died.", "stories": [irgc]},                         # 40 is in no headline
            {"text": "Oman offered to host new Iran-US talks.", "stories": [oman, "s99"]},
            {"text": "Made-up point.", "stories": ["s98"]}]))
        self.run_focus(api)
        feed = self.feed()
        f = next(x for x in feed["focus"] if x["hotspot"] == CID)
        self.assertEqual(f["status"], "active")
        self.assertEqual(len(f["points"]), 2)                                               # unbacked and uncited dropped
        p = f["points"][0]
        self.assertGreaterEqual(p["outlets"], 3); self.assertIn("GB", p["countries"]); self.assertFalse(p["party_only"])
        self.assertTrue(all(s["u"].startswith("https://") for s in p["sources"]))
        self.assertEqual(f["points"][1]["stories"], [oman])
        c = next(x for x in feed["conflicts"] if x["id"] == CID)
        self.assertEqual(c["status"], "US strikes Qeshm missile sites; Oman offers talks")
        self.assertEqual(c["latest"][0]["text"], "US forces struck missile sites near Qeshm, the Pentagon says.")
        self.assertEqual(api.calls, ["claude-haiku-5-5"])
        b = json.load(open(focus.FOCUS_BUDGET))
        self.assertGreater(b["usd"], 0); self.assertEqual(b["runs"][0]["what"], "focus update")
        # within 3 hours: nothing due, no call
        api2 = FakeAPI(answer([]))
        self.run_focus(api2)
        self.assertEqual(api2.calls, [])
        # --now: due anyway; the previous update goes into the prompt
        self.run_focus(FakeAPI(answer([{"text": "Tankers are waiting outside Hormuz.", "stories": [tankers]}],
                                      headline="Tankers wait at Hormuz")), "--now")
        f = next(x for x in self.feed()["focus"] if x["hotspot"] == CID)
        self.assertEqual(len(f["history"]), 2); self.assertEqual(f["headline"], "Tankers wait at Hormuz")

    def test_step_up_needs_independent_sources_and_rechecks_the_ladder(self):
        strike, tankers, oman, irgc, _ = self.sids()
        # only Iran's own media: no re-check (FakeAPI would fail on a second call)
        self.run_focus(FakeAPI(answer([{"text": "Iran's IRGC vowed a response.", "stories": [irgc]}],
                                      flag={"step_up": True, "why": "IRGC vows response", "stories": [irgc]})))
        self.assertFalse(any(b.get("recheck") for b in self.feed().get("boil") or []))
        boil = "```json\n" + json.dumps({"boil": [{"id": CID, "question": "Will the war widen?", "stage": 4, "likelihood": "unlikely",
            "likelihood_of": "a wider war", "direction": "rising", "summary": "US strikes on Qeshm.",
            "drivers": [{"text": "US strikes on Qeshm missile sites", "cites": ["h1"]}], "brakes": [], "watch": []}]}) + "\n```"
        api = FakeAPI(answer([{"text": "US forces struck missile sites near Qeshm.", "stories": [strike]}],
                             flag={"step_up": True, "why": "US strikes Iranian soil", "stories": [strike]}), boil)
        self.run_focus(api, "--now")
        self.assertEqual(api.calls, ["claude-haiku-5-5", "claude-opus-5-5"])
        b = next(x for x in self.feed()["boil"] if x["id"] == CID)
        self.assertTrue(b["recheck"]); self.assertTrue(b["headlines"])
        # within 12 hours no second re-check
        api2 = FakeAPI(answer([{"text": "US forces struck missile sites near Qeshm.", "stories": [strike]}],
                              flag={"step_up": True, "why": "again", "stories": [strike]}))
        self.run_focus(api2, "--now")
        self.assertEqual(api2.calls, ["claude-haiku-5-5"])

    def test_focus_ends_when_calm_for_48_hours(self):
        feed = self.feed()
        c = next(x for x in feed["conflicts"] if x["id"] == CID)
        c["severity"], c["trend"] = "yellow", "steady"
        feed["focus"] = [{"hotspot": CID, "status": "active", "calm_since": focus.iso(NOW - 49 * 3600), "history": []}]
        json.dump(feed, open(focus.FEED, "w"))
        self.run_focus(FakeAPI(answer([])))
        f = self.feed()["focus"][0]
        self.assertEqual(f["status"], "ended"); self.assertIn("calmed", f["ended_reason"])

    def test_apply_again_on_a_newer_feed_and_keep_for_the_daily_job(self):
        strike, *_ = self.sids()
        self.run_focus(FakeAPI(answer([{"text": "US forces struck missile sites near Qeshm.", "stories": [strike]}])))
        ours = self.feed()
        # the daily ratings pushed meanwhile: a new feed without our focus part; apply puts it back
        newer = json.load(open(os.path.join(HERE, "fixtures", "feed.json"))); newer["generated_at"] = "2000-01-01T00:00:00Z"
        json.dump(newer, open(focus.FEED, "w"))
        os.remove(focus.FOCUS_BUDGET)
        sys.argv = ["focus.py", "--apply"]; focus.main()
        again = self.feed()
        self.assertEqual(again["focus"][0]["headline"], ours["focus"][0]["headline"])
        self.assertEqual(json.load(open(focus.FOCUS_BUDGET))["usd"], json.load(open(focus.OUT))["spent"])
        # the daily job's feed (no focus) keeps the focus part of GitHub's newer feed
        theirs = os.path.join(self.dir, "theirs.json"); shutil.copy(focus.FEED, theirs)
        json.dump(newer, open(focus.FEED, "w"))
        sys.argv = ["focus.py", "--keep", theirs]; focus.main()
        self.assertEqual(self.feed()["focus"][0]["headline"], ours["focus"][0]["headline"])

if __name__ == "__main__":
    unittest.main()
