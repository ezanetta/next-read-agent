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

## D-011 · Hybrid work matching: Open Library work keys, then an LLM same-work check (2026-11-15)

- **Context:** Title matching can't see translations or retitled editions (F-001). Open Library groups editions under works; Google Books has no such concept. The spike (F-003) showed ISBN lookups group translations well but not always, and title search finds stray duplicate works.
- **Options:**
  - Open Library title + author search: rejected. Its top hit was right on 2 of 8 sides, and only when searching the work's original title.
  - Open Library by ISBN alone: rejected. Some works are split (El principito / The Little Prince), Google Books often ranks reprint ISBNs Open Library doesn't know, and 49 of 226 library rows have no ISBN.
  - LLM check alone: rejected. A non-deterministic, paid call for every author overlap, and harder to explain than an equal key.
  - Hybrid.
- **Choice:** For recommendations whose author already appears in the library: equal work keys (any matching edition against the author's library books) are a violation with `WORK_KEY` evidence and skip the LLM; otherwise the same-work judge decides. Recommendations by authors not in the library skip both.
- **Reason:** Equal keys are strong, cheap and deterministic evidence; different or missing keys prove nothing, and the judge covers those cases. The author gate keeps calls and unknowns rare: a translated duplicate is only a real risk for authors already read. Known gap: author names transliterated differently (Tolstói / Tolstoy) fail the surname gate.

## D-012 · The same-work judge leans towards "same work" (2026-11-15)

- **Context:** The judge can be wrong in two directions at different costs. A false "same" loses one good recommendation, which a retry can replace. A false "different" recommends a book the reader already has, the failure being fixed.
- **Options:** Trust a yes/no answer; ask for a confidence score; allow an explicit "unsure" and treat it as same.
- **Choice:** Answers are SAME, UNSURE or DIFFERENT. UNSURE counts as a collision, in code (`SameWorkVerdict.collides`) as well as in the prompt. DIFFERENT is only accepted from a reply that parses cleanly. Violations decided by the LLM carry `LLM_SAME` or `LLM_UNSURE` evidence and say so in the message, so evals can audit them. The judge is measured on a labeled set with an asymmetric assertion: no same-work pair may be judged different.
- **Judge failure:** an LLM error or unreadable reply is an Unknown under the `SAME_WORK` rule, not a violation and not a pass (D-002). The validator reports honestly; the product policy decides. **The future agent loop must not treat this Unknown as a pass:** the author is already in the library, so the book may be one the reader has read. It should retry the check or replace the book.

## D-013 · Work keys by ISBN only, cached by ISBN-13, with an explicit works command (2026-11-15)

- **Context:** Resolving 177 library ISBNs on every run would repeat about 180 requests to a free, volunteer-run service.
- **Options:** Resolve on every run; resolve on demand inside runs; resolve once with a command and cache.
- **Choice:** `works` resolves the library once and caches results in `data/work_keys.json`, keyed by ISBN-13, so the cache serves library books and recommendations and survives a new export. "Unknown to Open Library" is cached; failed lookups are not, so a rerun retries them; `--refresh` looks everything up again. Baseline runs read the cache and only look up new ISBNs. Requests carry a User-Agent with contact details from `OPEN_LIBRARY_CONTACT`, never hardcoded since the repo is public; without it, work-key matching is off and only the judge runs. At most 2 concurrent requests, a 10 s timeout and no retries.
- **Reason:** Polite usage and fast runs, coverage the reader can see ("160 resolved to a work, 15 unknown"), and contributors can run everything without an Open Library contact.

## D-014 · Agent acceptance policy: reject violations and unverified books (2026-11-17)

- **Context:** The validator reports violations and unknowns honestly (D-002); the agent needs a rule for which results it may return to the reader, and for what to do with the rest.
- **Options:** Accept only fully verified results; accept anything without violations; reject violations plus the unknowns that hide a likely violation.
- **Choice:** A pure function (`domain/acceptance.py`):
  - Reject any violation, any `SAME_WORK` unknown and any `NOT_FOUND` unknown. Accept other unknowns (missing page count or language).
  - Rejections the model can fix go back as an `is_error` tool result (`submission_feedback_v1`) that lists each problem by book number, names the accepted books to keep, and asks for replacements of the rejected ones only. A book that is really missing from the catalog (a `NOT_FOUND` violation, likely a hallucination) is one of these.
  - A `NOT_FOUND` unknown means the catalog lookup failed, an outage the model can't fix: the run stops with `AgentError` (`CATALOG_UNAVAILABLE`) saying the catalog couldn't be reached, carrying the validation.
- **Reason:**
  - `SAME_WORK`: the judge failed for an author already in the library, so the book may be one the reader has read, the failure this project exists to prevent (D-012).
  - `NOT_FOUND`: verifying that books exist is a core promise; showing a possibly hallucinated book is worse than an honest error. Feeding an outage back would spend iterations on something the model can't fix.
  - Missing page counts and languages are common and are not evidence of a broken rule; requiring full verification would reject many good books.
  - Naming the books to keep stops the model from replacing books that passed.
- **Revised (2026-11-17):** the first version accepted `NOT_FOUND` unknowns; changed after review.

## D-015 · A tool call cut off at MAX_TOKENS still runs (2026-11-17)

- **Context:** A reply can hit the token limit after starting a tool call. The loop only treats MAX_TOKENS specially when there are no tool calls.
- **Options:** Discard tool calls from a truncated reply; run them like any other call.
- **Choice:** Run them.
- **Reason:** Truncated arguments almost always fail argument validation and go back to the model as an error result, the same path as any malformed call, so the model can retry. Complete arguments are valid and validated like any other submission. A special case would add code without adding safety.

## D-016 · Known debt: agent.py depends on the concrete LLMSameWorkJudge (2026-11-17)

- **Context:** `AgentResult.calls` should include the same-work judge's LLM calls, but the judge reaches the agent as a `SameWorkJudge` port inside `WorkMatching`, and the port says nothing about calls.
- **Options:** A "records calls" Protocol checked at runtime (rejected: a structural check would also match `FakeSameWorkJudge`, whose `calls` holds something else); an `isinstance(judge, LLMSameWorkJudge)` check; the judge returning its call record with each verdict.
- **Choice for now:** the `isinstance` check in `agent._validate`, copying the calls the judge made during that validation.
- **Debt:** the agent depends on a concrete adapter class, and calls from any other judge implementation are silently missing. The cleaner fix is for `SameWorkVerdict` to carry the call that produced it (an optional `LLMCall`), so the validator can return the calls with its result and the agent needs no knowledge of the judge.

## D-017 · agent_v2: search for discovery; tool descriptions versioned with the prompt (2026-11-17)

- **Context:** F-005: following the `search_books` description, the model searched each book it already had in mind to confirm it existed, one search per turn, and ran out of turns. The validator already checks existence on submission.
- **Options:** Edit agent_v1 in place; a new version; remove the search tool.
- **Choice:** A new version, `agent_v2`, with agent_v1 kept unchanged for comparison. Search is for discovering candidates or resolving doubts about a title or author; existence is said to be checked automatically on submission; several searches go in the same turn. The system prompt's tool section says the same. Rules, the submit description, the argument schemas and the user prompt are reused from agent_v1.
- **Tool descriptions are part of the prompt version:** they moved from `tools.py` (names, argument models and execution only) into the prompt modules, and an `AgentPrompt` fixes system prompt, tools and user prompt together. The model reads tool descriptions like the system prompt, so changing one is a prompt change that evals must be able to compare.
- **Reason:** Search stays useful for discovery and for fixing a misremembered author (the "Las malas" case in F-005); removing it would lose that. Editing v1 in place would leave the first runs without a comparable baseline.

## D-018 · A last-turn notice, inside the message that closes the next-to-last turn (2026-11-17)

- **Context:** F-005: the slow run reached its last turn still searching. The model doesn't know how many turns it has left.
- **Options:** Count turns in the system prompt; a notice on every turn ("N turns left"); a notice once, when one turn is left. For placement: a separate user message; inside the message that closes the turn.
- **Choice:** Once, when one turn is left: the user message that closes the next-to-last turn ends with "This is your last turn. Call submit_recommendations now with your 3 best books: there are no turns left for searching." It goes after the tool results (or after the reminder, when the turn had no tool call), as a text part of the same message. It applies to every prompt version, and the trace marks it.
- **Reason:** One message at the moment it matters is cheaper and harder to ignore than a counter on every turn. The same message avoids two user messages in a row, which providers handle differently, and keeps tool results before text, which Claude requires. The notice is loop behavior, not prompt wording to compare, so it isn't versioned; runs after this change include it for agent_v1 too, which matters when comparing them with the first agent runs.
- **Not done:** forcing a submit-only tool choice on the last turn. `LLMClient` has no tool-choice option yet; worth adding if the notice proves not enough.
