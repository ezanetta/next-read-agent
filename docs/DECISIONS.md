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
- **Amended (2026-11-18):** the request parser reads its reply with `json.loads(strict=False)`, which only allows control characters such as a literal newline inside strings (F-010). It's a standard parser option, not repair, and it doesn't apply to the baseline, whose parsing stays strict.

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
- **Revised by D-028 (2026-11-19):** the notice now goes out with 2 turns left, and the last turn forces `submit_recommendations`.

## D-019 · A title + author lookup before reporting a book as not found (2026-11-18)

- **Context:** F-007: a real book was rejected as `NOT_FOUND` because Google Books ranked only reference works for the validator's free-text query. Since D-014, `NOT_FOUND` blocks acceptance, so a retrieval miss rejects a real book.
- **Options:** Search more results (cost on every lookup, and the book may still rank lower); clean the query only; a second, stricter lookup only when the first finds no match.
- **Choice:** Both a cleaner query and a fallback.
  - The adapter sends "&" as "and" in every query, the direct cause in F-007.
  - `BookSearch.find(title, author)` is a stricter lookup. For Google Books it's `intitle:"<core of the title>" inauthor:<surname>`, where the core is the title before the first ":", "&", "(", ";" or "[". The validator calls it only when the free-text search (with and without the language filter) finds no matching edition; its candidates go through the same title and author matching.
  - Each checked recommendation records `found_by`: `search` or `fallback`.
- **Reason:** One extra request, only for books that would otherwise be rejected, makes "not found" depend on two different retrieval strategies instead of one ranking. The core of the title, not the full title, because a phrase search for "... and Other Stories" missed the edition titled "... & Other Stories" (F-007). The fielded syntax is Google-specific, so it lives behind the port. `found_by` lets evals count how often the fallback rescues a real book, and whether it ever lets a wrong edition through.

## D-020 · One repair turn after a rejected last-turn submission (2026-11-18)

- **Context:** F-007: the model submitted on its last turn after the last-turn notice (D-018), the submission was rejected, and no turn was left to fix it. The notice and the feedback path didn't fit together.
- **Options:** Send the last-turn notice with 2 turns left (costs every run a turn, and a model that ignores it fails the same way); one repair turn only when a submission on the last turn is rejected.
- **Choice:** One repair turn. `MAX_ITERATIONS = 6` regular turns, `REPAIR_TURNS = 1`, effective cap `MAX_TURNS = 7`. The repair turn is granted only when a submission on the last regular turn is rejected; the rejection feedback then ends with a repair notice (keep the accepted books, replace the rejected ones, submit now). A rejection on an earlier turn adds nothing, since a turn is left. A rejection or a search in the repair turn ends the run. A catalog outage still stops the run at once (D-014). Results, errors and the trace record whether the repair turn was used.
- **Reason:** Every rejection gets one chance to be fixed, at a bounded cost (at most one extra request), without taking a turn away from runs that never need it. Recording its use lets evals see how often runs depend on it.
- **Revised by D-028 (2026-11-19):** the repair turn forces `submit_recommendations`, so a search can no longer use it up.

## D-021 · Evals: run once, score many times (2026-11-18)

- **Context:** Eval runs cost free-tier quota (about 500 requests a day). The acceptance policy has already changed once (D-014), and the phase-2 LLM judge will need the same runs.
- **Options:** Score while running and store verdicts; store everything a run produced and score in a separate step.
- **Choice:** Separate steps. `evals run` saves one complete record per run: identity (case set and its hash, configuration, prompt versions, git commit, library hash), the interpreted request, the outcome, the final and rejected validations, the trace and every LLM call. `evals report` only reads records and applies the acceptance policy at report time.
- **Reason:** A policy change or a new metric rescores old runs with no LLM calls; the judge can read the stored recommendations, reasons and resolved books later.
- **Caveat:** For the agent, rescoring judges the final validation only; the run's path (rejections, resubmissions) followed the policy in force when it ran.
- **Configuration identity:** system, prompt version, model and thinking level. The git commit is recorded per run, and the report warns about mixed commits, but **changes that affect results outside the prompt version (validator, loop, tools) should start a fresh results directory** rather than rely on that warning: those runs aren't comparable with older ones under the same configuration name.
- **Amended (2026-11-21): a harness version in the identity.** Remembering to pass a fresh `--results` directory is error-prone, so the configuration identity now includes `HARNESS_VERSION` (in `evals/records.py`), a single constant bumped whenever the loop, the validator, the tools or an adapter change in a way that affects results. It appears in the folder name (`...-h2`), so new runs go to a new folder automatically and the report shows each harness version as its own configuration. Round 1 is harness 1: records written before the field existed load as version 1 and keep their original folder names. `--results` stays for manual use. Prompt changes don't need a bump: prompts have their own versions.

## D-022 · Several runs per case (2026-11-18)

- **Context:** The agent runs at the model's default temperature and its behavior varies between runs (parallel versus one search per turn, F-006, F-007).
- **Options:** One run per case; several runs per case.
- **Choice:** `--runs N` per case (3 by default), reported as accepted/runs per case and as rates per configuration.
- **Reason:** One run per case would turn a coin flip into a verdict. Three runs fit the free tier for 13 cases and show which cases are unstable.

## D-023 · Frozen case sets, enforced by hash (2026-11-18)

- **Context:** Results are only comparable if the cases didn't change between runs.
- **Options:** A convention ("don't edit"); enforcement.
- **Choice:** Each record stores the case file's SHA-256. Once a case set has results, the runner (and the parser eval) refuse to add runs if the file's hash differs, and point to a new version file (`recommend_v2`).
- **Reason:** A silent edit to a case would mix incomparable runs in one report; a hash catches it at no cost.

## D-024 · Eval results stay private (2026-11-18)

- **Context:** Records and profile snapshots contain the reader's whole library, and recommendations derived from it.
- **Choice:** Results live in `data/evals/` (gitignored with the rest of `data/`). Only aggregate tables may be published later, after review. Case files contain no personal data and are versioned in the repo.
- **Reason:** The repo is public; the library is personal data.

## D-025 · Known limitation: the parser eval can't see a lost negation (2026-11-18)

- **Context:** The parser eval checks that terms survive in the preference ("Sanderson", "Christie", "space"). A preference that keeps the term but drops the negation ("nothing from Brandon Sanderson" becoming "Sanderson") passes.
- **Options:** Negation terms in the labels ("nothing", "not"); a human reading; an LLM judge.
- **Choice:** No negation terms: they're fragile across languages and phrasings. The parser report prints every interpreted preference, passing or not, so all of them can be read; the phase-2 judge will check that soft constraints, negations included, keep their meaning.
- **Reason:** A wrong automated check is worse than an honest blind spot with a human in the loop until the judge exists.

## D-026 · A min_pages filter, and one policy for contradictory bounds (2026-11-19)

- **Context:** "más de 600 páginas" or "between 200 and 400" had nowhere to go: `Filters` had only a maximum, so minimums lived in the soft preference, where v1 even dropped them (F-009), and nothing checked them.
- **Choice:**
  - `Filters.min_pages`, inclusive like `max_pages` (D-006): "more than 600" gives 600, since page counts vary between editions far more than one page. It merges like the other filters (a flag overrides the text), the validator checks it on the resolved edition with the same edition choice and unknown handling as `max_pages` (rule `MIN_PAGES`), and the CLI takes `--min-pages`.
  - `request_parser_v3` extracts explicit lower bounds; vague or approximate lengths ("long", "around 700") stay in the preference. It extracts bounds literally, even when they conflict.
  - Prompt versions that state the minimum: `baseline_v2` (user prompt only) and `agent_v3` (its user prompt is `baseline_v2`'s). System prompts and tool descriptions don't change. These, and `request_parser_v3`, are the new defaults.
- **Contradictory bounds: one policy, detected in code.** A minimum above the maximum fails the request with kind `contradiction` and an error naming the conflict and the source of each bound, whether both bounds come from the text ("more than 500 pages but under 300") or one from the text and one from a flag. The run stops before recommending; the UI will show it as a clarification question.
- **Options considered for contradictions:** a prompt rule telling the model to drop both bounds and keep the text in the preference (rejected: it gave two policies for the same conflict, sent the recommender an impossible request with nothing validated, and asked the model to compare numbers); one policy in code (chosen).
- **Reason:** The model's job is to extract what the reader said; comparing two numbers is code's job, and code does it the same way every time. A clear error is honest about a request that can't be met as stated.
- **Evaluation:** parser case set `request_parser_v2` labels `min_pages`. An expected contradiction is labeled with `expect_contradiction: true` and the literal bounds (min 500, max 300), and scores as correct when the interpretation fails with kind `contradiction` and the extracted fields match the label.

## D-027 · The Google Books adapter retries once; the LLM adapter doesn't (2026-11-19)

- **Context:** F-012: transient Google Books 503s, about 1.6% of requests, stopped 10% of agent runs, because a run makes about 10 Google Books requests and one failed validation lookup stops it (D-014). The adapter had no retries, following the rule set for the LLM adapter.
- **Options:** Keep failing on the first error; retry inside the validator; retry in the adapter.
- **Choice:** The Google Books adapter retries every request once, after 0.5 s plus up to 0.25 s of random jitter, on a 5xx answer or a network error (timeout, connection failure). 4xx answers, 429 included, are not retried. A second failure is reported as before.
- **Why this differs from the LLM adapter:** the "no retries in the adapter" rule was for the LLM: a Gemini request costs quota and tokens, a retry can repeat work, and rate limits need policy (the eval's `PacedLLM` waits `retry_after` once and stops on the daily quota, D-021). Google Books requests are read-only, idempotent and free beyond the daily quota, so one quick retry is safe and cheap. The jitter keeps parallel validation lookups from retrying in step. Open Library keeps no retries: a failed work-key lookup only falls back to the same-work judge, it never stops a run (D-013).
- **Reason:** Retrying where the error happens fixes it for every caller (validation, the agent's search tool, the UI later) instead of each one handling it.

## D-028 · Force submit on the last turn and the repair turn; notice with 2 turns left (2026-11-19)

- **Context:** F-012: all 4 `iterations_exhausted` runs were late submissions. The model searched one book per turn, submitted on the last turn, and the repair turn wasn't enough: one run ignored the last-turn notice and never submitted; another searched during the repair turn, which ends the run.
- **Options:**
  - Keep the last-turn notice (D-018): the evidence shows it's ignored.
  - Notice with 2 turns left: rejected in D-020 because it costs every run a turn and a model that ignores it fails the same way. That reason assumed late submission was rare; F-012 shows it is what fails runs.
  - Force the tool through the API: the next step D-018 recorded.
- **Choice:** Both.
  - `LLMClient.complete` takes an optional `ToolChoice` (forced call to one of the named tools; None lets the model choose). Gemini maps it to function calling mode ANY with `allowed_function_names`; Claude's `tool_choice` maps as `{"type": "tool", "name": ...}` for one tool, `{"type": "any"}` for all, and a subset is sent as only those tools.
  - The last regular turn and the repair turn force `submit_recommendations`. If a model returns another tool call anyway, the loop doesn't run it and answers with an error result.
  - The notice goes out with 2 turns left: "Call submit_recommendations now ... if a book is rejected, you can still fix it on your last turn."
  - The trace records the notice (`submit_notice`) and the forced tool (`forced_tool`).
- **Reason:** A forced call removes the failure mode the notice couldn't: the turn that must submit can't be spent searching. The earlier notice gives a rejected submission a regular turn to be fixed before the forced one.
- **Comparability:** this changes the loop, and D-027 changed the Google Books adapter that both systems use, so new runs go in a fresh results directory, per D-021.

## D-029 · Combined volumes and " - " subtitles count as editions; translations don't (2026-11-21)

- **Context:** F-012: *Sobre la Brevedad de la Vida* (Séneca) was rejected as `not_found` although both validator lookups returned 10 Séneca editions. The exact title existed only inside combined volumes ("Sobre la Brevedad de la Vida - Sobre la Felicidad", "Sobre la felicidad. Sobre la brevedad de la vida"), and the standalone editions used other translations of the Latin title ("De la brevedad de la vida").
- **Options:** (a) also split titles into main title and subtitle on " - "; (b) match a title against each part of a combined volume; (c) when the author matches but no title does, ask the same-work judge.
- **Choice:** (a) and (b), not (c).
  - (a) The main title / subtitle split happens at the first ":" or spaced " - ", but not at ". ", which would split "Mr. Mercedes".
  - (b) A title matches a combined volume when it equals one of its parts, split on " - ", ". ", "; " or " / ". One-word parts shorter than 4 letters are dropped as abbreviations ("Mr", "Dr"), and a title needs at least two parts left to count as a volume.
  - Not (c): existence must stay verified by catalog data. The same-work judge decides whether two existing books are the same work; it must not decide whether a book exists.
- **Side effects, accepted:**
  - A combined volume's page count is the whole volume's, so it can wrongly fail a `max_pages` filter (or pass a `min_pages` one) for a short work inside it. Edition choice still prefers an edition that meets the filters when there is one.
  - On the library side, a read title inside a volume ("Ficciones; El Aleph" read) makes either title count as already read. That errs on the safe side: it can only reject a recommendation, never let a read book through.
- **Known gap:** different translations of the same original title ("Sobre la brevedad de la vida" vs "De la brevedad de la vida") still don't match for existence; the book is reported `not_found` if only other translations are in the catalog.
- **Verified:** a regression test with fixtures built from the live check's results (titles and authors as returned; see the fixtures README).
