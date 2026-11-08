# Checking the outlooks (track record)

Each OUTLOOK below is a dated call the analysis made earlier ("X happening by DATE: likely"). Its date has passed.
Decide what happened, using today's notes and headlines given with it and, where they don't settle it, a web search
or two (at most 2 per call). Judge only the call as written, by its check-by date:

- "happened": published reporting shows it happened by the date.
- "did_not": published reporting shows it clearly did not happen by the date (the talks ended without a deal, the
  deadline passed and officials said so). Silence is not evidence: no news is "unclear".
- "partly": a clearly stated part happened and another clearly didn't.
- "unclear": you can't tell from what you found. This is the right answer whenever you are unsure.

Rules:
- Every verdict other than "unclear" needs at least one URL you saw in a search result or that was given here,
  in "evidence". No URL, no verdict.
- "why" is one or two plain sentences, attributed ("Reuters reports...", "The UN said..."), no alarm words.
- Don't re-argue the original likelihood; only say what happened.

Return only this JSON:
{"results": [{"id": "o...", "result": "happened|did_not|partly|unclear", "why": "...", "evidence": ["https://..."]}]}
