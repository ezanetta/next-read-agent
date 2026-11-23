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
