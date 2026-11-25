# Backlog

Ideas worth doing later, with the evidence that motivates them.

## B-001 · An author exclusion as a hard filter

- **Motivation:** eval case `fantasy-no-sanderson` ("... but nothing from Brandon Sanderson", recommend_v1). The request parser keeps the exclusion in the soft preference (`Filters` has no field for it), so nothing checks it: a Sanderson book would pass validation. The `cozy-no-christie` parser case has the same shape.
- **Idea:** `Filters.exclude_authors: list[str]`, extracted by a new request parser version and checked by the validator with the same surname matching as the library check (`authors_match`).
- **Before building it:** measure with the eval how often the model ignores an exclusion it was given as a preference. If it's rare, the soft preference may be enough.

## B-002 · Give the judge the cited library books' descriptions

- **Motivation:** judge_v2's tuning calibration (D-035), *The Book of Joan*: the reason called a cited library book, a short story collection, a dystopia. The judge sees only the cited books' title, author, rating and shelf, so it couldn't catch the misdescription and graded the connection as real. `reason_quality` asks whether the shared element is real on both sides, but the judge sees evidence for only one side (the recommended book's catalog description).
- **Idea:** add each cited book's description (and categories) to `CitedBook`, looked up in the catalog through the matcher, since the Goodreads export has no descriptions. Show the same on the labeling card. "No description available" when the book isn't in the catalog. It would be a new judge version, calibrated again.
- **Before building it:** count how many tuning and held-out disagreements involve a claim about a cited book, and check the catalog match rate for library books. Longer prompts also cost tokens on a small model.

## B-003 · A second library-retrieval version: precision and variety

- **Motivation:** agent_v4 met its quality and cost thresholds but missed the diversity goal (F-022). The model cites what retrieval returns (83%), and retrieval returns hub books, ignores negation and fills the list when nothing is close (F-021).
- **Ideas:**
  - **A similarity threshold:** show no related books, or fewer, when nothing is close (history-nonfiction).
  - **MMR (maximal marginal relevance):** pick the 8 for relevance and variety, so the obvious reference and the hub books don't fill every list. Possibly vary across runs of the same request.
  - **Remove exclusions from the query before embedding:** "nothing from Brandon Sanderson", "not about space". The parser could return the exclusions separately (related to B-001).
  - **Normalize the query text:** compare the request and the preference after whitespace and punctuation cleanup, so parser noise doesn't change the query (F-021).
- **Before building it:** it's a new prompt or retrieval version (agent_v5), with its own thresholds set in advance and a full round. Its numbers aren't comparable to agent_v4's, which stay as recorded. Consider measuring within-case repetition too, since D-041's measures didn't count it.
