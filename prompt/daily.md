Daily update. You get the current hotspots in compact form. Search the last 48 hours of worldwide coverage (spread your
searches across regions, most on red and US-direct hotspots) and report only what CHANGED. Return one ```json block:
{"updates": [{"id": "existing-id", "severity": "...", "trend": "...", "status": "...", "summary": "...",
              "us_impact": {"level": "...", "channels": [...], "text": "..."}, "watch": "...",
              "new_latest": [{"date", "text", "outlet", "country", "url"}], "add_keywords": ["..."]}],
 "new": [full hotspot objects for places that newly meet the bar, each with 2+ sourced latest items],
 "signals": [the full analysis, 4-7 points, only if the big picture changed; otherwise leave this out],
 "us_overview": "3-4 sentences: the biggest ways current conflicts could reach Americans right now (only if it changed)",
 "notes": "one line for the log: what you checked and what you changed"}
An update may also change "outlook" and "consensus". Include only the fields that changed in each update (always give new_latest with a source for any change). Change a
severity only with at least two independent sources. If nothing changed, return {"updates": [], "new": [], "notes": "..."}.
Never remove hotspots in the daily update.
