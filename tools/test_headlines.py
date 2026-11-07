"""Tests for the hourly headlines job, offline (no network, fake API).  python3 -m unittest -v tools/test_headlines.py"""
import json, os, sys, tempfile, time, unittest, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wwapi, fixture_time, headlines as H

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Russian drones hit Odesa port overnight</title><link>https://example.ua/1</link><pubDate>Tue, 03 Nov 2026 22:00:00 GMT</pubDate>
<description>&lt;p&gt;Three ships damaged&lt;/p&gt;</description></item>
<item><title><![CDATA[Recipe of the week: apple pie]]></title><link>https://example.ua/2</link></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Taiwan reports 30 PLA aircraft near island</title>
<link rel="alternate" href="https://example.tw/a"/><updated>2026-11-04T01:00:00Z</updated></entry></feed>"""
BROKEN = b"""<rss><channel><item><title>Sudan: RSF shells El Obeid & kills 5</title><link>https://example.sd/x</link>
<pubDate>Tue, 03 Nov 2026 20:00:00 +0000</pubDate></item></channel>"""
GNEWS = b"""<rss><channel><item><title>Le Kremlin menace la Pologne - Le Monde</title><link>https://news.google.com/rss/articles/abc</link>
<pubDate>Wed, 04 Nov 2026 05:00:00 GMT</pubDate><source url="https://www.lemonde.fr">Le Monde</source></item></channel></rss>"""

class Parse(unittest.TestCase):
    def test_rss_atom_and_broken(self):
        r = H.parse_feed(RSS)
        self.assertEqual(r[0]["title"], "Russian drones hit Odesa port overnight")
        self.assertEqual(r[0]["summary"], "Three ships damaged")
        self.assertEqual(r[1]["title"], "Recipe of the week: apple pie")
        a = H.parse_feed(ATOM)
        self.assertEqual(a[0]["link"], "https://example.tw/a")
        b = H.parse_feed(BROKEN)                          # not well-formed: rescued by the forgiving scan
        self.assertEqual(b[0]["title"], "Sudan: RSF shells El Obeid & kills 5")
        g = H.parse_feed(GNEWS)
        self.assertEqual((g[0]["source"], g[0]["source_url"]), ("Le Monde", "https://www.lemonde.fr"))

    def test_dates(self):
        import calendar
        want = calendar.timegm((2026, 11, 4, 6, 15, 0))
        self.assertEqual(H.parse_date("20261104T061500Z"), want)
        self.assertEqual(H.parse_date("2026-11-04T06:15:00Z"), want)
        self.assertEqual(H.parse_date("2026-11-04T08:15:00+02:00"), want)
        self.assertEqual(H.parse_date("Wed, 04 Nov 2026 06:15:00 GMT"), want)
        self.assertEqual(H.parse_date("garbage"), 0)

    def test_language(self):
        self.assertFalse(H.needs_translation("Russian drones hit Odesa port", "en"))
        self.assertTrue(H.needs_translation("“나는 한곳에 머물지 않는다”…데뷔 30주년", "en"))      # Korean in an English feed
        self.assertTrue(H.needs_translation("Le Kremlin menace la Pologne", "fr"))
        self.assertFalse(H.needs_translation("Trump says the war is over and talks are on", "es"))  # English in a Spanish feed
        self.assertTrue(H.needs_translation("Demócratas ganan el control del Senado", "es"))

    def test_translations_parse(self):
        t = H.parse_translations('1|fr|The Kremlin threatens Poland\nnoise\n 2 | ar | Strikes on Gaza | day 3\n9|xx|out of range\n3|es|', ["k1", "k2", "k3"])
        self.assertEqual(t, {"k1": ("fr", "The Kremlin threatens Poland"), "k2": ("ar", "Strikes on Gaza | day 3")})

class Items(unittest.TestCase):
    def test_merge_keeps_hints_and_first_seen(self):
        a = H.make_item("Houthis fire missile at tanker", "https://x.com/1", "X", "x.com", "YE", "en", 1000, "rss")
        b = dict(H.make_item("Houthis fire missile at tanker", "https://x.com/1?utm_source=t", "X", "x.com", "YE", "en", 2000, "gdelt", hints=["yemen"]))
        m = H.merge([a], [b], 0)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["h"], ["yemen"]); self.assertEqual(m[0]["p"], 1000)
        self.assertEqual(H.merge([a], [], 1500), [])                   # left the window

    def test_relevance_is_loose(self):
        feed = fixture_time.feed()
        terms = H.term_regex(feed)
        def it(t, h=()): return {"t": t, "x": "", "h": list(h)}
        self.assertTrue(H.relevant(it("Ukraine's grid operator warns of long outages"), terms))
        self.assertTrue(H.relevant(it("Navy deploys second carrier group"), terms))       # security words alone
        self.assertTrue(H.relevant(it("Odd story with no place", ["korean-peninsula"]), terms))   # GDELT found it for a hotspot
        self.assertFalse(H.relevant(it("Recipe of the week: apple pie"), terms))
        self.assertFalse(H.relevant(it("Brazil beat Argentina in friendly"), terms))        # a country name alone isn't enough

    def test_ai_risk_tagging(self):
        feed = fixture_time.feed()
        terms = H.term_regex(feed)
        def it(t, h=()): return {"t": t, "x": "", "h": list(h)}
        x = it("Anthropic co-founder says AI could soon build smarter AI on its own")
        self.assertTrue(H.relevant(x, terms)); self.assertEqual(x["h"], ["ai-risk"])
        x = it("UK institute finds model gave bioweapon uplift in tests")              # 'model' alone isn't an AI word...
        self.assertFalse("ai-risk" in (x["h"] if H.relevant(x, terms) else []))
        x = it("New evaluation results for our latest model", ["ai-safety"])            # ...but a safety institute's feed keeps all
        self.assertTrue(H.relevant(x, terms)); self.assertEqual(x["h"], ["ai-risk"])
        x = it("OpenAI launches a shopping assistant", ["ai-risk"])                     # a lab's product news: dropped
        self.assertFalse(H.relevant(x, terms))
        self.assertFalse(H.relevant(it("AI race heats up as chip sales climb"), terms))

class Translate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); H.LIVE = self.dir
        os.environ["ANTHROPIC_API_KEY"] = "test"
        self.http = wwapi.http
        self.sent = []
        def fake(method, url, body=None, timeout=900, raw=False):
            if url.endswith("/messages/batches") and method == "POST":
                self.sent.append(body)
                return {"id": f"b{len(self.sent)}", "processing_status": "in_progress"}
            if "/messages/batches/" in url and method == "GET" and not url.endswith("/results"):
                bid = url.rsplit("/", 1)[1]
                return {"id": bid, "processing_status": "ended" if self.done else "in_progress", "results_url": f"https://r/{bid}/results"}
            if url.endswith("/results"):
                bid = url.split("/")[-2]; reqs = self.sent[int(bid[1:]) - 1]["requests"]
                lines = []
                for r in reqs:
                    nums = [l.split("\t")[0] for l in r["params"]["messages"][0]["content"].splitlines()]
                    titles = [l.split("\t")[1] for l in r["params"]["messages"][0]["content"].splitlines()]
                    text = "\n".join(f"{n}|fr|English {t.split()[-1]}" for n, t in zip(nums, titles))
                    lines.append(json.dumps({"custom_id": r["custom_id"], "result": {"type": "succeeded", "message": {
                        "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 2000, "output_tokens": 1000}}}}))
                return "\n".join(lines)
            raise AssertionError(url)
        wwapi.http = fake
        self.done = True
        H.WAIT_MIN = 0.01; H.POLL_S = 0
    def tearDown(self):
        wwapi.http = self.http; shutil.rmtree(self.dir)

    def q(self, n): return [{"k": f"k{i}", "o": f"Titre {i}", "t": "", "h": [], "p": 0} for i in range(n)]

    def test_translates_in_batches_and_books_cost(self):
        state, spend = {"tr": {}, "pending": []}, H.load_spend()
        H.translate(self.q(150), state, spend, 0)
        self.assertEqual(len(self.sent[0]["requests"]), 2)                 # 100 per request
        self.assertEqual(state["tr"]["k149"]["en"], "English 149")
        self.assertGreater(spend["usd"], 0); self.assertEqual(spend["titles"], 150)
        H.translate(self.q(150), state, spend, 0)                           # all cached: nothing sent again
        self.assertEqual(len(self.sent), 1)

    def test_slow_batch_lands_next_run(self):
        self.done = False
        state, spend = {"tr": {}, "pending": []}, H.load_spend()
        H.translate(self.q(5), state, spend, 0)
        self.assertEqual(len(state["pending"]), 1); self.assertEqual(state["tr"], {})
        H.translate(self.q(5), state, spend, 0)                             # still running: not sent twice
        self.assertEqual(len(self.sent), 1)
        self.done = True
        H.translate(self.q(5), state, spend, 0)
        self.assertEqual(state["pending"], []); self.assertEqual(len(state["tr"]), 5)

    def test_limits(self):
        state, spend = {"tr": {}, "pending": []}, H.load_spend()
        spend["usd"] = H.SUB_CAP
        H.translate(self.q(5), state, spend, 0)
        self.assertEqual(self.sent, [])
        spend["usd"] = 0
        H.translate(self.q(5), state, spend, wwapi.MONTHLY_CAP_USD)         # War Watch as a whole is at its cap
        self.assertEqual(self.sent, [])

class EndToEnd(unittest.TestCase):
    def test_main_offline(self):
        d = tempfile.mkdtemp()
        try:
            root = os.path.join(d, "root"); os.makedirs(os.path.join(root, "config"))
            fixture_time.write_feed(root)
            shutil.copy(os.path.join(os.path.dirname(HERE), "outlets.json"), root)
            json.dump({"rss": [{"name": "Example UA", "home": "UA", "lang": "en", "url": "https://example.ua/rss"},
                               {"name": "Example FR", "home": "FR", "lang": "fr", "url": "https://example.fr/rss"}],
                       "gnews": [{"hl": "fr", "gl": "FR", "ceid": "FR:fr", "lang": "fr"}],
                       "search": [{"name": "Reuters", "q": "site:reuters.com war", "site": True, "home": "GB", "lang": "en"},
                                  {"name": "AI risk", "q": "\"AI safety\"", "hints": ["ai-risk"]}]},
                      open(os.path.join(root, "config", "sources.json"), "w"))
            old = (H.ROOT, H.LIVE, H.fetch, H.gdelt_get, H.translate, H.now_ts)
            now = int(time.time())
            fr = f"""<rss><channel><item><title>Le Kremlin menace la Pologne de représailles</title><link>https://example.fr/k</link>
                     <pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now - 3600))}</pubDate></item></channel></rss>""".encode()
            ua = RSS.replace(b"Tue, 03 Nov 2026 22:00:00 GMT", time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now - 7200)).encode())
            gn_now = GNEWS.replace(b"Wed, 04 Nov 2026 05:00:00 GMT", time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now - 1800)).encode())
            H.ROOT, H.LIVE = root, os.path.join(d, "live")
            ai = f"""<rss><channel><item><title>Lab says its AI model tried to sabotage safety tests - Example Wire</title>
                     <link>https://news.google.com/a1</link><source url="https://wire.example">Example Wire</source>
                     <pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now - 900))}</pubDate></item></channel></rss>""".encode()
            site = f"""<rss><channel><item><title>Exclusive: Iran moves missiles to Qeshm - Reuters</title>
                     <link>https://news.google.com/r1</link><source url="https://www.reuters.com">Reuters</source>
                     <pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now - 900))}</pubDate></item></channel></rss>""".encode()
            def fake_fetch(url, timeout=25, browser=False):
                if "search?q=site%3Areuters" in url: return site
                if "search?q=" in url: return ai
                return {"https://example.ua/rss": ua, "https://example.fr/rss": fr}.get(url, gn_now)
            H.fetch = fake_fetch
            H.gdelt_get = lambda url, last=None: {"articles": [{"url": "https://ar.example/1", "title": "غارات جديدة على قطاع غزة اليوم", "language": "Arabic",
                                                                "sourcecountry": "Qatar", "domain": "ar.example", "seendate": time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now - 600))}]}
            def fake_translate(queue, state, spend, total):
                en = {"Le Kremlin menace la Pologne de représailles": "Kremlin threatens Poland with reprisals",
                      "Le Kremlin menace la Pologne": "Kremlin threatens Poland", "غارات جديدة على قطاع غزة اليوم": "New strikes on the Gaza Strip today"}
                for x in queue: state["tr"][x["k"]] = {"en": en.get(x["o"], "Odd text"), "l": "xx", "at": now}
            H.translate = fake_translate; H.GNEWS_GAP = 0
            H.main()
            out = json.load(open(os.path.join(d, "live", "headlines.json"), encoding="utf-8"))
            titles = [x["t"] for x in out["items"]]
            self.assertIn("Russian drones hit Odesa port overnight", titles)
            self.assertIn("Kremlin threatens Poland with reprisals", titles)
            self.assertNotIn("Recipe of the week: apple pie", titles)
            gaza = [x for x in out["items"] if x["v"] == "gdelt"][0]
            self.assertEqual((gaza["l"], gaza["c"], gaza["o"]), ("ar", "QA", "غارات جديدة على قطاع غزة اليوم"))
            self.assertTrue(gaza["h"])                                       # the hotspot GDELT found it for
            gn = [x for x in out["items"] if x["v"] == "gnews"][0]
            self.assertEqual((gn["s"], gn["c"]), ("Le Monde", "FR"))
            self.assertGreaterEqual(out["stats"]["languages"], 3)
            full = json.load(open(os.path.join(d, "live", "corpus.json"), encoding="utf-8"))
            self.assertEqual(len(full["items"]), len(out["items"]))           # all fit under the app's size limit here
            r = next(x for x in out["items"] if x["t"].startswith("Exclusive"))
            self.assertEqual((r["v"], r["s"], r["c"]), ("site", "Reuters", "GB"))
            a = next(x for x in out["items"] if "sabotage" in x["t"])
            self.assertEqual((a["v"], a["h"]), ("gsearch", ["ai-risk"])); self.assertEqual(out["stats"]["ai_risk"], 1)
            st = json.load(open(os.path.join(d, "live", "state.json"), encoding="utf-8"))
            self.assertTrue(any(x["o"].startswith("Recipe") for x in st["items"]))     # remembered, not shown
        finally:
            H.ROOT, H.LIVE, H.fetch, H.gdelt_get, H.translate, H.now_ts = old
            shutil.rmtree(d)

if __name__ == "__main__":
    unittest.main()
