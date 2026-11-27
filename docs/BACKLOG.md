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

## B-004 · The same-work judge's calls through the Protocol

- **Motivation:** staff review R-013. The agent loop checks `isinstance(judge, LLMSameWorkJudge)` to copy the judge's LLM calls into the run (`agent.py`), so another `SameWorkJudge` would drop its calls from token and cost accounting.
- **Idea:** add the calls to the `SameWorkJudge` Protocol (or return them from `judge()`) and remove the `isinstance`.

## B-005 · One shelf view for the library and cited books

- **Motivation:** staff review R-014. `Shelf` and the status-to-shelf mapping are declared twice (`api/schemas.py`, `api/views.py`, `recommendation_stream.py`), and `cited_book` repeats the "picked books never show stars" rule of `library_books`.
- **Idea:** one `shelf_of(book)` and one profile-book view used by both.

## B-006 · Split `cli.py` and drop its import from the API package

- **Motivation:** staff review R-015. `cli.py` has 771 lines and 5 commands, and imports `utc_now` from `book_agent.api.services`.
- **Idea:** move `utc_now` out of the API package; split the CLI per command when it's next touched.

## B-007 · Bring ARCHITECTURE.md and CLAUDE.md up to date

- **Motivation:** staff review R-016. ARCHITECTURE.md lists 7 ports (no `CoverFinder`, `CoverCache`), says FastAPI "will use" `wiring.py`, and lists `agent_v1..v3`. The committed CLAUDE.md still plans a Goodbooks-10k catalog with pgvector and Langfuse (D-040 chose Open Library).
- **Idea:** update both, ideally in the deploy PR.

## B-008 · Pace all Open Library traffic as one stream

- **Motivation:** staff review R-017. Work-key lookups during validation run unpaced (up to 2 at a time), while cover lookups are paced at one a second: the two streams add up.
- **Idea:** one pacer for every Open Library call, or work-key lookups on the cover worker. Low priority until there's traffic.

## B-009 · Small frontend gaps

- **Motivation:** staff review R-018. `PickCard` imports `bookAnchor` from the library page; the upload says "up to 1 MB" without checking the size first; stored results are cast from `sessionStorage` without a version; the long-title test asserts Tailwind class names.
- **Idea:** move `bookAnchor` to `lib/`, check `file.size` before uploading, version the storage key, and replace class assertions if a visual test is added.
