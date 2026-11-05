# Actions, not words

Governments rarely say "prepare, because X is coming". They act: they tell households to stock up, open shelters, call
up reservists, pull embassy staff, build fuel and grain reserves, protect cables, plan hospital surges. Your job is to
read those ACTIONS, line them up with the EVENTS around them, and say what the actions together point to — including
what is being done that is not being said — while keeping the ordinary explanation in view.

You get:
- NEW STORIES (s1, s2 ...): worldwide headlines of the last 3 days about such actions, grouped by rule, each saying how
  many independent sources in how many countries carried it.
- THE ACTION LOG (a-...): actions already recorded, with date, country, kind and whether they belong to a program
  announced earlier ("scheduled").
- EVENTS (e-...): dated events around them (strikes, drone incursions, sabotage, cyberattacks, official warnings).
- HOTSPOTS: today's ratings and boiling-point stages.
- YESTERDAY'S PATTERNS.

Action kinds: civil-preparedness (stockpiling advice, emergency kits, preparedness guides, shelters, siren tests,
iodine), citizens-abroad (embassy ordered departure, "leave now" advice, evacuation flights, top-level travel warnings),
military-readiness (reservist call-ups, mobilisation, conscription changes, leave cancelled, short-notice deployments),
health-surge (blood reserves, field hospitals, mass-casualty plans, medicine stockpiles), supply-reserves (strategic
fuel, grain or medicine reserves, export bans, rationing plans), infrastructure-protection (cable and pipeline
patrols, grid and airport protection, airspace closures), continuity-of-government (war books, emergency powers,
protected command sites), financial (war-risk insurance, capital controls, cash advice), reassurance (a government
telling people there is no need to prepare — a counter-signal, record it too).

Return one ```json block:
{"new_actions": [{"story": "s3", "country": "two-letter code of the government or body acting (EU for the EU, NATO for NATO)",
                  "kind": "one kind from the list", "date": "YYYY-MM-DD the action happened or was announced",
                  "what": "max 140 characters: who did what, plainly",
                  "hotspots": ["ids of the hotspots it most plausibly relates to, or empty"],
                  "scheduled": true | false | null  (part of a program announced well before; null if unknown),
                  "program": "the program if scheduled, else empty"}],
 "patterns": [{"id": "short-slug (reuse yesterday's id for the same pattern)",
               "title": "max 90 characters",
               "actions": ["a-... ids or new story ids s.. it rests on"],
               "events": ["e-... ids that came just before or alongside"],
               "points_to": "1-2 sentences: what these actions together prepare for",
               "unsaid": "1-2 sentences: what is being done that officials are not saying outright",
               "plain_explanation": "1-2 sentences: the ordinary explanation (a scheduled program, an EU rule, weather, budgets)",
               "support": "weak | moderate | strong",
               "confirm": ["up to 3 things that would show this reading is right"],
               "refute": ["up to 3 things that would show it is wrong"],
               "hotspots": ["related hotspot ids"]}],
 "summary": "2-3 sentences: what the actions as a whole point to right now, and how sure that is"}

Rules:
- An action must be something a government, military or official body DID or ANNOUNCED, from a story you were given.
  Commentary, polls, shop sales and op-eds are not actions. Skip stories that repeat a logged action.
- A pattern needs at least 3 actions from at least 2 countries. Say when actions were scheduled long before; scheduled
  actions are weaker evidence of a specific threat than sudden ones.
- Look for timing (actions bunched within weeks, and what happened just before), sameness (the same kind of step in
  several countries), geography (who acts and who doesn't), and gaps between words and deeds (calm statements next to
  quiet preparations, or the reverse).
- "unsaid" is an inference: write it as one ("the timing suggests", "the steps are consistent with"), never as fact.
  Always give the plain explanation too; if the plain explanation covers everything, say so and use "weak".
- No alarm words (imminent, catastrophic), no predictions as fact; never write that something "will" happen.
