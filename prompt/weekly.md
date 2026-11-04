Sunday review of ONE region. You do not search. You get the region's hotspots (with their latest developments) and
the notes gathered today (with IDs; tags and source_keys as in the daily rules). Cite note IDs only.

Return one ```json block:
{"updates": [{"id": "existing id", "notes": ["ids"], any fields that changed (as in the daily rules), and for EVERY
   hotspot in the region also:
   "parties": [...], "interstate": true|false, "type": "...", "reason": "...", "short_name": "...",
   "anchors": [...], "context": [...], "exclude": [...],
   "tripwires": [{"id": "keep an existing id to keep it", "kind": "escalation|easing", "text": "observable, under 110 chars"}]
     (red: 2 escalation + 1 easing; yellow: 1 + 1),
   "week_verdict": {"verdict": "deteriorated|improved|unchanged", "why": "one line", "notes": ["ids"]}}],
 "new": [full hotspot objects with "notes": [ids] for places that newly meet the bar; 2+ different source_keys],
 "ended": [{"id": "...", "why": "dated reason", "notes": ["ids"]}] only for hotspots where nothing notable happened
   in the last ~60 days or a settlement has held; a hotspot you leave out simply stays as it is.}
Keep severity and trend calls to what this week's notes show; easing and "improved" are normal answers.
