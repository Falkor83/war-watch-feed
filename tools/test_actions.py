"""Tests for Actions, not words (tools/wwactions.py), with a fake Claude API.  python3 -m unittest -v tools/test_actions.py"""
import json, os, shutil, sys, tempfile, time, unittest, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wwapi, wwboil, wwscore, wwactions, fixture_time
from test_feed import msg

NOW = int(time.time())
TODAY = datetime.date.today().isoformat()

def item(t, c, s, d, mins=60, h=("action",), v="rss"):
    return {"k": f"{d}{abs(hash(t)) % 10**8}", "u": f"https://{d}/{abs(hash(t)) % 10**8}", "o": t, "t": t, "l": "en", "s": s,
            "d": d, "c": c, "p": NOW - mins * 60, "v": v, "h": list(h), "x": ""}

ITEMS = [
    item("Poland calls up 20,000 reservists for snap readiness drills", "PL", "PAP", "pap.pl", 90),
    item("Poland summons 20,000 reservists in surprise drill", "DE", "DW", "dw.com", 100),
    item("Poland calls up reservists for snap drills near Belarus border", "GB", "BBC", "bbc.co.uk", 110),
    item("Lithuania tells households to keep 72 hours of emergency supplies", "LT", "LRT", "lrt.lt", 120),
    item("Lithuania urges residents to stockpile 72 hours of supplies", "FR", "France 24", "france24.com", 130),
    item("Taylor Swift fans line up for tickets", "US", "Blog", "blog.example", 140, h=()),
]

def answer(extra_patterns=None):
    return "```json\n" + json.dumps({
        "new_actions": [
            {"story": "S_PL", "country": "PL", "kind": "military-readiness", "date": TODAY, "what": "Poland calls up 20,000 reservists for snap drills",
             "hotspots": ["russia-nato-eastern-flank-kaliningrad", "nope"], "scheduled": False, "program": ""},
            {"story": "S_LT", "country": "LT", "kind": "civil-preparedness", "date": TODAY, "what": "Lithuania tells households to keep 72 hours of supplies",
             "hotspots": ["russia-nato-eastern-flank-kaliningrad"], "scheduled": None},
            {"story": "S_LT", "country": "LT", "kind": "civil-preparedness", "date": TODAY, "what": "Lithuania tells households to keep 72 hours of supplies again",
             "hotspots": []},                                                                       # a repeat: dropped
            {"story": "S_PL", "country": "PL", "kind": "military-readiness", "what": "Poland calls up 50,000 reservists"}],  # 50,000 not in headlines
        "patterns": [
            {"id": "baltic-72h", "title": "Countries near Russia add civil and military readiness steps",
             "actions": ["a-2026-10-22-gb", "a-2026-10-09-lu", "S_PL", "S_LT"], "events": ["e-2026-10-24-streeting", "e-bogus"],
             "points_to": "Preparation for disruption from Russian hybrid attacks.", "unsaid": "The timing suggests readiness for a longer confrontation.",
             "plain_explanation": "Several were scheduled under the EU 72-hour strategy.", "support": "strong",
             "confirm": ["more call-ups"], "refute": ["campaigns stay routine"], "hotspots": ["russia-nato-eastern-flank-kaliningrad"]},
            {"id": "too-thin", "title": "One country only", "actions": ["a-2026-10-22-gb"], "points_to": "x", "plain_explanation": "y"},
            {"id": "no-plain", "title": "Missing the ordinary explanation", "actions": ["a-2026-10-22-gb", "a-2026-10-09-lu", "S_PL"],
             "points_to": "x", "plain_explanation": ""}] + (extra_patterns or []),
        "summary": "Preparation steps cluster near Russia. Officials cite 99 reasons."}) + "\n```"

class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        src = os.path.dirname(HERE)
        for d in ("prompt", "config"): shutil.copytree(os.path.join(src, d), os.path.join(self.dir, d))
        self.feed = fixture_time.feed()
        self.saved = (wwapi.BUDGET, wwapi.http, wwapi.USE_BATCH)
        wwapi.BUDGET = os.path.join(self.dir, "budget.json"); wwapi.USE_BATCH = False
        os.environ["ANTHROPIC_API_KEY"] = "test"
        wwboil._ASSIGNED.clear()
    def tearDown(self):
        wwapi.BUDGET, wwapi.http, wwapi.USE_BATCH = self.saved
        wwboil._ASSIGNED.clear(); shutil.rmtree(self.dir)

    def run_stage(self, text):
        stories, _ = wwactions.candidate_stories(ITEMS, wwactions.load_log(self.dir), NOW)
        sid = lambda w: next(f"s{i + 1}" for i, s in enumerate(stories) if any(w in x["t"] for x in s["items"]))
        text = text.replace("S_PL", sid("Poland")).replace("S_LT", sid("Lithuania"))
        seen = []
        def fake(m, u, body=None, timeout=900, raw=False):
            seen.append(body["messages"][0]["content"]); return msg(text, tin=20000, tout=6000)
        wwapi.http = fake
        budget = wwapi.load_budget(); logs = []
        r = wwactions.stage(self.feed, ITEMS, budget, "claude-opus-5-5", "sys", TODAY, self.dir, logs.append)
        return r, logs, seen[0], budget

    def test_seed_new_actions_patterns_and_warning_signs(self):
        r, logs, prompt, budget = self.run_stage(answer())
        seed = json.load(open(os.path.join(os.path.dirname(HERE), "config", "actions_seed.json"), encoding="utf-8"))
        for x in seed["actions"] + seed["events"]:                       # the seed is read (the log keeps 730 days)
            if wwscore.days_between(x["date"], TODAY) <= 720: self.assertIn(x["id"], prompt)
        self.assertNotIn("Taylor Swift", prompt)
        self.assertEqual(r["new_actions"], 2)
        log = json.load(open(os.path.join(self.dir, "actions.json")))
        pl = next(a for a in log["actions"] if a["country"] == "PL")
        self.assertEqual(pl["hotspots"], ["russia-nato-eastern-flank-kaliningrad"]); self.assertTrue(pl["url"].startswith("https://"))
        a = self.feed["actions"]
        self.assertEqual([p["id"] for p in a["patterns"]], ["baltic-72h"])
        p = a["patterns"][0]
        self.assertEqual(p["support"], "moderate")                    # 'strong' needs 5 actions in 3 countries
        self.assertEqual(p["countries"], ["GB", "LT", "LU", "PL"]); self.assertEqual([e["id"] for e in p["events"]], ["e-2026-10-24-streeting"])
        self.assertNotIn("99", a["summary"])                          # unbacked number dropped
        self.assertTrue(any("50,000" in l or "numbers" in l for l in logs))
        # warning signs on the hotspot: tripped, marked as actions, not scored in the tension index
        tws = [tw for tw in self.feed["tripwires"] if tw.get("source") == "action"]
        self.assertTrue(any(tw["hotspot"] == "russia-nato-eastern-flank-kaliningrad" and tw["status"] == "tripped" and "Poland" in tw["text"] for tw in tws))
        before = wwscore.tension(self.feed["conflicts"], [tw for tw in self.feed["tripwires"] if tw.get("source") != "action"], {}, TODAY, [])
        after = wwscore.tension(self.feed["conflicts"], self.feed["tripwires"], {}, TODAY, [])
        self.assertEqual(before["value"], after["value"])
        self.assertGreater(budget["usd"], 0)
        # a second day: the repeat isn't logged twice, the old signs are rebuilt, not duplicated
        n = len(tws)
        r2, _, prompt2, _ = self.run_stage(answer())
        self.assertEqual(r2["new_actions"], 0)
        self.assertEqual(len([tw for tw in self.feed["tripwires"] if tw.get("source") == "action"]), n)
        self.assertIn("baltic-72h", prompt2)                          # yesterday's patterns go back in

    def test_failure_keeps_patterns_and_still_flags(self):
        self.run_stage(answer())
        pats = self.feed["actions"]["patterns"]
        def boom(*a, **k): raise wwapi.CallFailed("down")
        wwapi.http = boom
        wwactions.stage(self.feed, ITEMS, wwapi.load_budget(), "claude-opus-5-5", "sys", TODAY, self.dir, lambda m: None)
        self.assertEqual(self.feed["actions"]["patterns"], pats)
        self.assertTrue(any(tw.get("source") == "action" for tw in self.feed["tripwires"]))

    def test_hotspot_line_for_the_boiling_point(self):
        log = wwactions.load_log(self.dir)
        log["actions"].append({"id": "a-x", "date": TODAY, "country": "PL", "kind": "military-readiness", "what": "x",
                               "hotspots": ["russia-nato-eastern-flank-kaliningrad"]})
        line = wwactions.hotspot_line(log, "russia-nato-eastern-flank-kaliningrad", TODAY)
        self.assertIn("Poland", line)
        self.assertEqual(wwactions.hotspot_line(log, "haiti-gang-war", TODAY), "")

if __name__ == "__main__":
    unittest.main()
