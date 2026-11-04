# War Watch ratings feed

`feed.json` is what the War Watch Android app downloads: the world's current wars and armed conflicts, each rated red
(active war / major escalation) or yellow (elevated), with what it could mean for the United States and the sources.

- Updated every morning by Claude with web search across outlets from many countries (`tools/update_feed.py daily`),
  and reviewed region by region on Sundays (`weekly`). Rules and prompts: `prompt/`.
- Every update is checked before it is saved (`tools/update_feed.py check`); a bad answer leaves the old feed in place.
- Spending is estimated from the API's own usage numbers in `budget.json` and capped per month.
- Public on purpose: it contains only public news summaries and links, nothing personal.
