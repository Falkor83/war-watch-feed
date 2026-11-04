Boiling point. You do not search. For each hotspot below you get: its current rating, yesterday's place on the
ladder, its warning signs (tripwires), a DATA panel computed by rule from public data (d1, d2...), worldwide HEADLINES
from the last 48 hours translated to English (h1, h2...; * marks an outlet from a country involved) and today's
checked research NOTES (n01...). Decide how close it is to tipping into open war between states, or, where states are
already fighting, into a clearly wider war (a new state joining, attacks on a new country's territory, nuclear use).

The ladder (stage):
1 Words: threats, accusations, rhetoric.
2 Posturing: drills, deployments, sanctions, expulsions, shows of force.
3 Mobilizing: troop build-ups, reserve call-ups, evacuations of citizens or embassies, airspace closures, ultimatums.
4 Brink: clashes between state forces, final deadlines, war footing, the last off-ramps closing.
5 Open war: sustained fighting between states (for a war already under way: the wider war in the question).

How to judge:
- Weigh what states DO (deployments, evacuations, airspace, call-ups, travel advice, money moving) above what they SAY.
  Rhetoric alone rarely moves the ladder.
- Check whether the data and the headlines point the same way. Headline volume can rise for reasons that are not
  escalation (an anniversary, an election, a summit); say so when the data shows talk words rising with escalation words.
- Don't take one side's word for it. A side's own officials and state media (marked * and party-gov) count as that
  side's claim; outside outlets carry more weight. If almost all coverage comes from one side or one region, say so in
  "sides".
- Betting-market prices and currency moves are weak evidence on their own; managed currencies say little.
- Base rates: most standoffs never become wars. Stage 4 and 5 need concrete events, not mood.
- The ladder moves at most one step from yesterday unless two cited items show a jump (give them in "stage_cites").

Return one ```json block:
{"boil": [{"id": "hotspot id",
  "question": "the question this rates, e.g. 'Will China and Taiwan's standoff become a shooting war?' or, for a war already under way, 'Will the US-Iran fighting widen into a regional war?'",
  "stage": 1-5, "stage_cites": ["only if moving two steps"],
  "likelihood": "one of: almost no chance, very unlikely, unlikely, roughly even chance, likely, very likely, almost certain",
  "likelihood_of": "what the likelihood is of, e.g. 'reaching stage 4 within 30 days' or 'open war within 30 days'",
  "direction": "rising|steady|easing",
  "summary": "2-3 calm sentences on where it stands and why; numbers only as they appear in what you cite",
  "drivers": [{"text": "one line pushing it up", "cites": ["d3", "h7"]}],      (at most 4)
  "brakes": [{"text": "one line holding it back", "cites": ["d5", "n04"]}],    (at most 3)
  "tip": "one sentence: the concrete event that would move it up a stage",
  "offramp": "one sentence: the concrete event that would move it down",
  "watch": ["up to 3 short things to watch next"],
  "agree": "one sentence: do the data and the headlines point the same way?",
  "sides": "one sentence: who is reporting this, and whether it is mostly one side's account"}]}

Every driver and brake needs at least one citation, and any number in it must appear in what it cites. Write
"X says" for claims by a side. Never write that something "will" happen; use the likelihood words only with the basis
shown in the drivers. No alarm words (imminent, inevitable, catastrophic) and no judgement words outside attribution.
