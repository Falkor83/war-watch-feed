# War Watch ratings feed

`feed.json` is what the War Watch Android app downloads: the world's current wars and armed conflicts (red = active war
or major escalation, yellow = elevated), cyberattacks and sabotage of infrastructure, what each could mean for the
United States, and an analysis of where worldwide reporting points. Schema 2.

- **Daily** (`tools/update_feed.py daily`, ~2:41 AM Pacific): Claude Sonnet 5.5 searches outlets worldwide and writes
  research notes; Claude Opus 5.5 reads only those notes and decides. **Sundays** (`weekly`): every region is
  re-researched (tripwires, parties, keywords, weekly verdicts), then the daily decision runs over all notes.
- **Python checks and scores everything** before the feed changes: notes may only carry URLs the search really
  returned; outlet, country and region come from `outlets.json`; reprints of one wire story or relays of one official
  statement count as one source; a warring side's own media is "X says", never agreement; figures must appear in a
  cited note; severity changes need two independent sources. The evidence meter, confidence, tripwire states and the
  world tension index are computed by rule (`tools/wwscore.py`), never written by the model.
- Files: `feed.json` (the app's feed), `history.json` (one row a day), `notes/` (each day's research notes, 120 days),
  `outlooks.json` (calls to check later), `outlets.json` (outlet registry), `config/lists.json`, `budget.json`.
- Spending: estimated from the API's own usage numbers in `budget.json` (Batch API, half price; about $25-30 a month),
  capped at $35 a month. Tests: `python3 -m unittest tools/test_feed.py` (fake API, no cost).
- Public on purpose: it contains only public news summaries and links, nothing personal.
