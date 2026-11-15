# Decisions

Significant design decisions: context, options considered, choice, and reason.

## D-001 · Compare title variants instead of dropping subtitles (2026-11-15)

- **Context:** Model replies, Goodreads and Google Books spell the same title differently ("Dune (Dune Chronicles, #1)", "Mistborn: The Final Empire", "The Final Empire").
- **Options:** Always drop the text after ":"; compare full, main and subtitle variants.
- **Choice:** Variants. Titles match on equal full titles, a main title equal to the other's full title, or a subtitle equal to the other's full title. Equal main titles with different subtitles never match.
- **Reason:** Dropping subtitles made "Mistborn: The Final Empire" and "Mistborn: The Well of Ascension" both "mistborn", so an unread sequel would be reported as already read.

## D-002 · Missing data is "unknown", never a pass (2026-11-15)

- **Context:** Catalog entries often lack a page count or language, so a filter can't always be checked.
- **Options:** Treat missing data as a pass; as a failure; as a separate "unknown" state.
- **Choice:** A third state, `UNKNOWN`. Results expose `passed` (no violations) and `fully_verified` (no violations and no unknowns).
- **Reason:** Silently passing hides unverified constraints. Which verdict counts is left to the evals, since what to do with missing data is still an open product question.

## D-003 · Edition-aware resolution with a language fallback (2026-11-15)

- **Context:** Page count and language depend on the edition, not the work.
- **Options:** Check the first search result; search for an edition that meets the filters.
- **Choice:** Search with the language filter first, then without it if no title and author match. Among matching editions, keep the one with the fewest failed filters, then the fewest unknowns.
- **Reason:** A book that exists in the requested language should pass, and a book that exists only in another language should fail the language rule, not look missing.

## D-004 · Require each reason to cite a read book (2026-11-15)

- **Context:** The product promise is a reason that cites the reader's own books.
- **Options:** Leave it to prompt instructions; check it in code.
- **Choice:** A `CITES_LIBRARY` rule: the reason must mention a title the reader has read, compared on normalized titles as whole words. Citing a to-read book doesn't count.
- **Reason:** Evals can count it like any other rule instead of trusting the prompt.

## D-005 · Interpret requests in a separate LLM call (2026-11-15)

- **Context:** The UI will be one free-text field, so filters must be extracted from text.
- **Options:** One call that interprets and recommends; a separate interpretation call.
- **Choice:** A separate short call (`request_parser_v1`, temperature 0) that returns a preference and filters.
- **Reason:** In a single reply, a misread filter would be invisible: the model would recommend for its misreading and validation would use the same misreading. A separate call makes the interpretation visible and testable. Cost: one extra request (about 440 input tokens).

## D-006 · "Under N pages" means at most N (2026-11-15)

- **Context:** "under 400" is strictly fewer than 400; `max_pages` is inclusive.
- **Options:** Map "under N" to N - 1 in the prompt; an extra "exclusive" field that code subtracts; treat it as N.
- **Choice:** Inclusive `max_pages = N` for "under", "less than", "at most" and "up to".
- **Reason:** Page counts vary between editions by far more than one page, so the boundary is noise, and keeping arithmetic out of the prompt removes a way for the model to be wrong.

## D-007 · Stop the run when interpretation fails (2026-11-15)

- **Context:** The interpreter can fail (LLM error, malformed reply, invalid language code).
- **Options:** Continue with explicit flags only and warn; stop before recommending.
- **Choice:** Stop: the result carries the error, no recommendation call is made, and the CLI exits 2.
- **Reason:** Continuing would silently drop constraints the reader typed and could still report PASSED, the same kind of silent failure as F-001.

## D-008 · Flags override interpreted filters, with sources recorded (2026-11-15)

- **Context:** Development needs explicit flags; the product needs free text. Both can be given at once.
- **Options:** Text wins; flags win; flags win field by field.
- **Choice:** Flags override field by field, and each filter records whether it came from the text or a flag.
- **Reason:** Explicit input is the more deliberate signal, and recorded sources let the CLI and the future UI chips show where each filter came from.

## D-009 · ISO 639-1 language filters, compared by primary subtag (2026-11-15)

- **Context:** The interpreter may answer "english" or "en-US"; Google Books returns codes like "pt-BR".
- **Options:** Accept any string; require two-letter codes and normalize catalog codes.
- **Choice:** `Filters.language` must match `^[a-z]{2}$`. The interpreter's codes are lowercased and reduced to the primary subtag before validation; the validator compares only the catalog code's primary subtag.
- **Reason:** An invalid code fails loudly instead of becoming a filter that matches nothing, and regional catalog codes still match.

## D-010 · No JSON repair for model replies (2026-11-15)

- **Context:** A baseline reply escaped accented characters as `\uXXXX` and produced an invalid escape, so it failed to parse (F-002).
- **Options:** Repair the JSON (fix escapes, use a lenient parser); keep strict parsing and ask the prompt to avoid escapes.
- **Choice:** Strict parsing stays. `baseline_v1` asks for accented and non-ASCII characters to be written directly, never as `\u` escapes.
- **Reason:** The agent will return recommendations through tool calling, where the provider delivers structured arguments, so repair code would only serve the baseline. Strict parsing keeps malformed replies visible as failures that evals can count.
