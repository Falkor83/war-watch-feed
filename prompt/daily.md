Daily decision. You do not search. You get today's research notes (with IDs), the current hotspots, yesterday's
analysis points and the cyber/sabotage items. Each note already carries its region, its tag (non-party, party-press =
an independent outlet from a warring country, party-gov = a warring side's own officials or state media) and its
source_key (notes sharing a key are ONE source, e.g. reprints of one wire story). Python counts sources, regions and
evidence from the note IDs you cite, so cite note IDs only: never write a URL, outlet or country yourself.

Return one ```json block with these keys (leave a key out when you have nothing for it):

"updates": [{"id": "existing id", "notes": ["n03", "n07"], plus only the fields that changed:
  "severity", "trend", "status", "summary", "us_impact": {...}, "watch", "outlook", "consensus", "confidence",
  "reason", "type", "short_name", "parties", "add_keywords": [...],
  "tripwires": [{"kind": "escalation|easing", "text": "..."}]   <- only for hotspots that have no tripwires yet}]
  Every change needs the notes that show it. A severity or US-level change needs at least 2 different source_keys
  with at least one not tagged party-gov; Python refuses it otherwise.
"new": [full hotspot objects (see the rules) with "notes": [ids] instead of "latest"; 2+ different source_keys].
"watch_list": [{"id": "...", "why": "one line: how this could reach people in the US now"}]: at most 6 hotspots that
  most deserve watching for their concrete, current path to Americans at home (US forces fighting, attacks on US
  soil or infrastructure, nuclear risk, a real price or supply shock). Fewer is fine. These get a "!" on the globe.
"rank": all hotspot ids, worst first (severity, scale of harm, momentum, then US impact).
"brief": exactly 3 short lines for a busy reader: what changed since yesterday (name the hotspots), what matters
  most for the US, and one calm line on anything easing. No alarm words. If little changed, say so plainly.
"us_overview": 3-4 sentences, "us_points": 3-4 one-line bullets (fuel and prices, allies and troops, Asia, closer to
  home), only when they changed.
"signals": the 3-7 biggest points about where the world's reporting as a whole points. Each:
  {"id": "stable-kebab-id (reuse yesterday's id for the same point)", "title": "an observation, not a forecast",
   "claim": "one sentence", "detail": "2-3 sentences; party claims as 'X says'", "direction": "escalating|easing|mixed|steady",
   "confidence": "high|medium|low", "hotspots": ["ids"], "for": ["note ids"], "against": ["note ids that cut the other way"],
   "other_reading": "the strongest other way to read the same facts", "would_change_view": "what would change this",
   "tripwires": ["tripwire ids it bears on"]}
  A point counts only if independent sources from at least 2 regions not party to the fighting report it alike.
  Look for "against" notes on purpose. Include easing points when the notes support them.
"frames": up to 5 big events where regions tell it differently: {"id", "hotspot", "topic", "agreed": "what all
  agree on", "versions": [{"region": "north-america|europe|russia|china|east-asia|south-asia|southeast-asia|oceania|
  gulf|middle-east|iran|africa|latin-america|intl", "says": "under 12 words", "notes": ["ids from that region"]}],
  "unresolved": "what no independent source has confirmed"}.
"tripwire_updates": [{"id": "tw-...", "status": "signs|tripped", "notes": ["ids"], "why": "one line"}]: only when
  notes show it. "tripped" needs 2 different source_keys, at least one not a party's own account.
"new_tripwires": [{"hotspot": "id", "kind": "escalation|easing", "text": "observable, under 110 chars"}] for red or
  '!' hotspots missing one kind.
"events": [{"hotspot": "id", "label": "...", "date": "YYYY-MM-DD", "anchors": ["2-5 distinctive words"]}] for today's
  biggest events (helps the app group headlines).
"outlooks": [{"hotspot": "id", "text": "...", "likelihood": "a word from the list", "basis": ["note ids"],
  "check_by": "YYYY-MM-DD"}] for up to 5 red hotspots.
"cyber": cyberattacks and sabotage of infrastructure (new ones, or updates to listed ones; same id to update):
  {"id": "kebab-id", "title": "...", "date": "YYYY-MM-DD (when it happened)", "places": [{"name", "lat", "lon"}],
   "sector": "food|water|power|telecom|health|transport|government|finance|industry|defense",
   "method": "one line: how it was done", "summary": "2-3 sentences",
   "actor": {"name": "who is suspected, or 'unknown'", "confidence": "claimed|suspected|attributed|unknown"},
   "us_status": "hit_in_us|suspected_in_us|same_tech_in_us|no_known_link",
   "us_why": "1-2 sentences: has it reached the US, could the same thing work there, what US officials say",
   "what_to_watch": "one line", "notes": ["ids"]}
  Only from notes. Attribution exactly as strong as the sources say ("no group has claimed it").
"log": one line for the run log.
