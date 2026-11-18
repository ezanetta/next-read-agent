# Backlog

Ideas worth doing later, with the evidence that motivates them.

## B-001 · An author exclusion as a hard filter

- **Motivation:** eval case `fantasy-no-sanderson` ("... but nothing from Brandon Sanderson", recommend_v1). The request parser keeps the exclusion in the soft preference (`Filters` has no field for it), so nothing checks it: a Sanderson book would pass validation. The `cozy-no-christie` parser case has the same shape.
- **Idea:** `Filters.exclude_authors: list[str]`, extracted by a new request parser version and checked by the validator with the same surname matching as the library check (`authors_match`).
- **Before building it:** measure with the eval how often the model ignores an exclusion it was given as a preference. If it's rare, the soft preference may be enough.
