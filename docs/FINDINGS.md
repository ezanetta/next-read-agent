# Findings

Real-world failures: what happened, how it was detected, the fix, and how it was verified.

## F-001 · Translated title of a read book recommended and passed validation (2026-11-15)

- **What happened:** A baseline run with `--max-pages 200 --language en` recommended *The Metamorphosis* (Franz Kafka) in English. The reader had read *La metamorfosis* in Spanish. The model's own reason said the reader had read it, and the validator reported PASSED: titles in different languages never match.
- **How it was detected:** Manual review of the CLI output of the first real run.
- **Fix:**
  - Prompt (`baseline_v1`): a book counts as read in any language or edition; translations, other editions and retitled versions must never be recommended; a language filter means "a new book available in that language", never "re-read a listed book".
  - Validator: work-level matching (D-011). For authors already in the library, equal Open Library work keys or the LLM same-work judge (D-012) detect translations and retitled editions.
- **How it was verified:** Unit tests check the prompt rules. The reproduction test, formerly `xfail(strict=True)`, now passes as `test_translated_title_of_a_read_book_is_already_read`, decided by the judge, with twins decided by work key and end to end through the baseline and CLI. The live test `test_same_work_live.py` measured the judge on 11 labeled real pairs (`gemini-3.5-flash-lite`, `same_work_v1`): 11 of 11 correct, with no same-work pair judged different and no different pair judged same. The set is small and uses well-known books, so evals should keep auditing `LLM_SAME` and `LLM_UNSURE` decisions.
- **Re-check with real runs (F-002):** the prompt rule alone did not work. *The Metamorphosis* was recommended twice more, once with a reason citing "La metamorfosis by the same author" as if they were different books. This is why the fix lives in the validator.
- **Verified with real data (2 baseline runs, Open Library contact set, library resolved with `works`):**
  - Run 1 (`--max-pages 200 --language en`): all 3 recommendations were already read and all 3 were caught. *The Metamorphosis* was caught by the judge (`LLM_SAME`), the other two by title.
  - Run 2 ("algo corto y liviano"): *The Metamorphosis and Other Stories*, an anthology containing the story the reader read, was judged UNSURE and counted as already read. A genuinely ambiguous case, handled as designed (D-012).
  - Both Kafka detections were decided by the judge, not the work key, as F-003 predicted: Google Books ranks a reprint ISBN Open Library doesn't know.
- **Status:** fixed and verified with real data.

## F-002 · First baseline runs: prompt-only exclusion is unreliable (2026-11-15)

- **What happened:** 5 real runs of the baseline with free-text requests (`gemini-3.5-flash-lite`, `baseline_v1`, `request_parser_v1`).
  - Request interpretation was correct in all 4 runs that completed.
  - All 4 completed runs FAILED validation, which is the baseline result, not a bug: **7 of 12 recommendations** were already read or on the to-read list. The validator caught 5. *The Metamorphosis* appeared twice and was not detected (translated title, F-001).
  - The prompt's translation rule was ignored: the model recommended *The Metamorphosis* citing "La metamorfosis by the same author", as if they were different books.
  - 1 fabricated citation: a reason cited *The Handmaid's Tale*, which is not in the library.
  - 1 reason leaked internal reasoning: "(wait, referenced via taste...)".
  - **1 of 5 runs was unparseable** ("algo corto y liviano en español"): the model escaped accented characters as `\uXXXX` and produced an invalid escape ("po\u00tico" instead of "po\u00e9tico").
  - The same reply contained a **hallucinated title in Spanish**: "El coronel en su laberinto" by Álvaro Mutis does not exist; it looks like a blend of two García Márquez titles.
- **How it was detected:** Validator violations in the CLI output (already read, to-read, `cites_library`), the parser's error for the unparseable reply, and manual review of the reasons and titles.
- **What it implies:**
  - **Prompt-only exclusion is unreliable.** Next steps: work-level matching in the validator, so translations and editions of read books are caught; then the agent loop, which feeds violations back to the model so it can replace rejected books.
  - `cites_library` caught the fabricated citation but passed the leaked reasoning, because it only checks that a read title is mentioned. Reason quality needs an LLM judge later.
  - A hallucinated title is the case the existence check (`NOT_FOUND`) is for, but this one never reached validation because the reply failed to parse.
- **Fix so far:** `baseline_v1` asks for accented and non-ASCII characters to be written directly, never as `\u` escapes. No JSON repair (D-010): the agent will return recommendations through tool calling.
- **How it was verified:** A unit test checks the new prompt line. Not yet re-checked with a real run.

## F-003 · Open Library groups translations by ISBN, but its title search is unreliable (2026-11-15)

- **What happened:** A spike before designing work-level matching (D-011). Four pairs, each side looked up by ISBN and by title + author: 28 Open Library requests and 3 Google Books searches in total.

  | Pair | Same work by ISBN? | Title + author search, top hit is the main work? |
  |---|---|---|
  | La metamorfosis / The Metamorphosis | yes, `OL498556W` | no on both sides |
  | Cien años de soledad / One Hundred Years of Solitude | yes, `OL274505W` | Spanish yes, English no |
  | El principito / The Little Prince | **no**: `OL19863207W` vs `OL10263W` | no on both sides |
  | Philosopher's / Sorcerer's Stone | yes, `OL82563W` | Philosopher's yes, Sorcerer's no |

  - ISBN lookups matched 3 of 4 pairs. The miss is a Spanish edition attached to a separate work: different keys don't prove different works.
  - Title search was right on 2 of 8 sides, only when searching the work's own (original) title. Translated and retitled titles find stray duplicate works. Sorting by edition count fixed none of the misses.
  - Library coverage: 177 of 226 rows have an ISBN (81 of 115 read, 94 of 109 to-read). The first real `works` run resolved 155 of 177 to a work; 20 are unknown to Open Library and 2 lookups failed and were retried.
  - Recommendation side: Google Books results mostly carry ISBNs (6 of 7 matching editions in 3 searches), but often reprints. Open Library knew 4 of 6: the only Kafka edition with an ISBN was a self-published reprint unknown to it, so in the real F-001 case the work key can't decide and the judge does.
- **How it was detected:** Manual spike against the live APIs, with a User-Agent naming the app (no contact details yet: that became `OPEN_LIBRARY_CONTACT`).
- **What it implies:** ISBN lookups only, never title search; a cache so the library is resolved once; and an LLM check for what keys can't decide (D-011, D-013).
- **How it was verified:** Unit tests use the response shapes seen in the spike; a live test checks that the Spanish and Bantam Kafka editions share `OL498556W`.

## F-004 · A reason that cites the recommended book itself passes `cites_library` (2026-11-15)

- **What happened:** In a real baseline run (F-001, Run 1), the model recommended *The Pearl* with a reason citing *The Pearl* as the book the reader liked. The book was already read and was caught as `ALREADY_READ`, but `cites_library` passed, because the reason does mention a read title.
- **How it was detected:** Manual review of the CLI output.
- **Fix:** `cites_library` no longer counts a citation of the recommended work itself: the same work by title variants, or the library book it collides with (by ISBN, title, work key or the judge, so a translation counts as itself too). A reason that cites only the book itself fails with "the reason cites only the recommended book itself". `agent_v1` and `agent_v2` also ask for this in the prompt.
- **How it was verified:** Unit tests: self-citation alone fails; self plus another read book counts the other; citing the translation it collides with fails; a reason with no read title keeps the original message.

## F-005 · The agent searched to confirm books it already had in mind, and ran out of turns (2026-11-17)

- **What happened:** The agent (`agent_v1`, `gemini-3.5-flash-lite`) ran on the 5 baseline requests. 3 passed on the first submission. 2 failed with `iterations_exhausted` and 0 submissions, after about 30,000 tokens with nothing to show.
- **How it was detected:** The run summaries (iterations, submissions, about 20 output tokens per turn), then the `--verbose` turn traces of the two failing requests.
- **What the traces showed:**
  - The model never used search to discover books. It picked books from memory and searched each "title author" to confirm it existed, as the `search_books` description told it to ("confirm a book exists before recommending it"). The validator already checks existence on submission, so these searches were redundant.
  - Parallel versus sequential decided the outcome. The fast run made 3 searches in one turn (2 iterations, about 10,000 tokens). The slow run made one search per turn and only submitted on the last turn (6 iterations, about 30,000 tokens). Same behavior otherwise.
  - Search did help once: the model searched "Las malas" with the wrong author, got junk, and retried with the right one.
- **Fix:** `agent_v2` (D-017): search is described as a tool for discovering candidates or resolving doubts about a title or author, existence is said to be checked on submission, and several searches go in the same turn. A last-turn notice (D-018) tells the model to submit now when one turn is left, for every prompt version. `agent_v1` stays unchanged for comparison.
- **How it was verified:** Unit tests for the new descriptions, the prompt choice and the notice. Re-checked with real runs in F-006: 5 of 5 passed on the first submission.
- **Revised by F-006:** confirmation searches are not pure waste. They also filter hallucinations before submitting, and done in parallel they cost less than a rejection round. The problem was searching sequentially, not searching.

## F-006 · agent_v2 passes all 5 requests; hard rules can't see quality (2026-11-17)

- **What happened:** `agent_v2` ran on the same 5 requests as F-005 (`gemini-3.5-flash-lite`).
  - **5 of 5 passed on the first submission**, in 2 to 4 iterations and 10,000 to 19,000 tokens. The last-turn notice (D-018) was never needed.
  - The model still searches "title author" to confirm books it already has in mind, but mostly in parallel now. One run still searched sequentially (4 turns).
  - Confirmation searches also filter hallucinations: the model searched "The empress and the salt maker", found nothing matching, and dropped it before submitting. A parallel search before submitting is cheaper than a rejection round.
  - Hard rules can't see quality. "algo liviano" (something light) got dark, disturbing books; some reasons are weak; *La metamorfosis* was cited in 4 of 5 runs. Every one of these passed validation.
  - No rejection happened in 10 agent runs (F-005 and F-006), so the feedback path (`submission_feedback_v1`) is still untested with real data.
- **How it was detected:** The agent command's summaries (iterations, submissions, tokens) and `--verbose` traces, and manual review of the recommendations and reasons.
- **What it implies:**
  - The F-005 fix worked; the remaining cost is sequential searching in some runs.
  - Mood fit, reason quality and citation variety need an LLM judge alongside the hard rules.
  - The feedback path needs a test with real data, for example a request or profile that makes violations likely, before relying on it.
- **Fix:** None needed for the agent loop. Quality judging is future work.
- **How it was verified:** 5 real runs with `agent_v2`.

## F-007 · First real rejection: no turn left to fix it, and the rejection was a false positive (2026-11-18)

- **What happened:** "más libros de Claire Keegan" with `agent_v2`. The model searched one book per turn for 5 turns, got the last-turn notice, and submitted on turn 6. The submission was rejected and the run failed with `iterations_exhausted`. Three problems:
  1. **Budget:** the last-turn notice (D-018) and the feedback path don't fit together. A submission on the last turn can be rejected, but no turn is left to fix it.
  2. **False positive in the validator:** 'The News from Ireland & Other Stories' by William Trevor was rejected as `not_found`. It's a real book; the model's own search on turn 5 returned it as the second result.
  3. **One search per turn appeared in 3 of 7 `agent_v2` runs** (F-006 plus 2 more), so parallel searching isn't reliable on flash-lite.
- **How it was detected:** The `--verbose` trace of the failing run; then live Google Books requests reproducing the validator's lookup.
- **Cause of the false positive:** the literal "&" in the search query, not the title matcher. The validator searches `"<title> <author>"`:

  | Query sent to Google Books | Book in the top 5? |
  |---|---|
  | `The News from Ireland & Other Stories William Trevor` (the validator's) | no: 5 reference works that mention it |
  | `The News from Ireland and Other Stories William Trevor` | yes, first |
  | `The News from Ireland William Trevor` | yes, second |
  | `intitle:"The News from Ireland" inauthor:Trevor` | yes, first |

  The matcher already treats "&" as "and" and would have matched the book; it never received it.
- **Known gap, not fixed:** the title variant "X & Other Stories" (or "X and Other Stories") versus "X". A fielded search also returns editions titled just 'The News from Ireland', which the matcher doesn't treat as the same book as '... & Other Stories': no ":" separates the two parts, so D-001's variants don't apply. Collections retitled this way can be missed by the existence check and by the library check.
- **What it implies:** A `NOT_FOUND` violation can be wrong when the catalog search ranks the book outside the top 5. Since D-014 makes it blocking, a false negative in retrieval rejects a real book; existence checks need a more robust lookup before "not found" is trusted. One-search-per-turn is left for evals to measure; no prompt change yet.
- **Fix:**
  - The Google Books adapter sends "&" as "and" in every query.
  - Before reporting a book as not found, the validator tries a stricter title + author lookup (`intitle:"<core of the title>" inauthor:<surname>`), and records `found_by` (search or fallback) so evals can count rescued books (D-019). A phrase search for the full title with "and" (`intitle:"The News from Ireland and Other Stories"`) missed the edition titled with "&"; the core of the title, before the first "&", found it first. So the fallback uses the core.
  - A submission rejected on the last regular turn gets one repair turn (D-020).
  - One search per turn: no change; evals will measure it.
- **How it was verified:** Live Google Books requests (the 4 queries above, plus 3 to record fixtures and choose the fallback query); no LLM calls. A regression test runs the validator with the real adapter on the recorded responses: the "&" query returning only reference works, and the fallback finding the book. Unit tests for the repair turn. Not yet re-checked with a real agent run.

## F-008 · "More Claire Keegan" answered with one Keegan book: an eval case for the judge (2026-11-18)

- **What happened:** The F-007 request, "más libros de Claire Keegan", rerun with `agent_v2` after the D-019 and D-020 fixes. It passed: 4 iterations, parallel searches, no rejection. It didn't exercise either fix: the model picked *After Rain* (William Trevor) instead of *The News from Ireland & Other Stories*, and nothing was rejected. Both fixes are covered by unit and regression tests instead.
  - The request asked for more Claire Keegan books, and the answer had only one, although the model found a second (*Walk the Blue Fields*) twice in its searches.
- **How it was detected:** Manual review of the CLI output and the `--verbose` trace.
- **What it implies:** Whether one book by the requested author, plus two similar authors, honors "more books by X" is a judgment call that hard rules can't make. Keep this request as an eval case for the LLM judge, with the trace's evidence that a second matching book was available.
- **Fix:** None; eval case.
- **How it was verified:** 1 real run.

## F-009 · The request parser drops soft constraints it can't turn into filters (2026-11-18)

- **What happened:** First run of the parser eval (`request_parser_v1`, 23 labeled requests, `gemini-3.5-flash-lite`). Filters were right in 23 of 23 cases, and all three negations ("nothing from Brandon Sanderson", "not about space", "nothing by Agatha Christie") were kept verbatim. Two soft constraints were dropped:
  - "un thriller, en inglés o en español" became "un thriller": two acceptable languages can't be a filter (`Filters` holds one), so they vanished.
  - "a romance between 200 and 400 pages" became "a romance": the minimum has no field, so it vanished along with the maximum that did become a filter.
- **How it was detected:** The parser eval's "dropped preference terms" section, and reading every interpreted preference in the report.
- **Cause:** One pattern: the parser strips anything that *looks* like a filter, even when it couldn't express it as one. The v1 prompt says to leave out "the parts used for language and max_pages", which the model reads as "anything about language or pages".
- **Fix:** `request_parser_v2`: text may leave the preference only when it became a filter; anything that can't be expressed as one (several languages, a minimum, "around N") stays, in the reader's own words. v2 is the default for the baseline and the agent; v1 stays frozen.
- **How it was verified:** Unit tests for the prompt rule. The parser eval is to be rerun with v2.

## F-010 · request_parser_v2: equal score, cheaper errors (2026-11-18)

- **What happened:** Parser eval with `request_parser_v2` (same 23 labels, `gemini-3.5-flash-lite`). Both F-009 cases were fixed ("en inglés o en español" and "between 200 and 400 pages" stayed in the preference), and all three negations were kept. Two new issues:
  1. **Invalid JSON on a correct interpretation:** for "A sci fi book that is not about space", the model put a literal newline inside the preference string (`"A\n sci fi book that is not about space"`), which strict JSON parsing rejects.
  2. **v2 sometimes repeats a filter in the preference:** "books in English under 250 pages" gave `max_pages` 250 and preference "books under 250 pages", so the label expecting no preference failed. The same happened in `at-most-300` and `history-nonfiction`, which still passed because their preference was expected anyway.
- **How it was detected:** The parser eval's errors and mismatch sections, and reading every interpreted preference.
- **Equal scores hid errors of different cost:** v1 and v2 both scored 21 of 23 fully correct, but v1's errors silently dropped constraints the reader asked for, while v2's mostly repeat a filter in the preference, which is harmless: the filter is still enforced, and the recommender reads a constraint twice. A single accuracy number would have called them equal. The eval's per-field breakdown and the printed preferences are what showed the difference.
- **Fix:**
  - The request parser reads its reply with `json.loads(strict=False)`, the standard option for control characters inside strings; nothing else is repaired (D-010 amended). Only the parser: the baseline's parsing stays strict, so its measured behavior doesn't change. That case's record was removed for a rerun.
  - The repeated filter is a known limitation of v2, not a reason for v3.
- **How it was verified:** A unit test with the exact raw reply; a test that the shared extractor and the baseline stay strict. The one case is to be rerun.

## F-011 · First eval table: the agent accepts 79%, the baseline 13% (2026-11-19)

- **What happened:** `recommend_v1`, 13 cases × 3 runs, `gemini-3.5-flash-lite`, `request_parser_v3`, current acceptance policy (D-014).

  | Configuration | Accepted | Requests per run | Input tokens per run | Latency per run (mean / p95) |
  |---|---|---|---|---|
  | `baseline_v2` | 5/39 (13%) | 2.0 | – | 11 s |
  | `agent_v3` | 31/39 (79%) | 6.5 | about 27,000 | 31 s / 65 s |

  - **Baseline final violations:** not_found 27, already_read 25, cites_library 18, duplicate 2, on_to_read 2, max_pages 1; 2 runs unparseable.
  - **Agent failures:** 4 `catalog_unavailable`, 4 `iterations_exhausted`. One search per turn in 20 of 39 runs; the repair turn was used 5 times; the fallback lookup rescued 2 books.
  - **Hard cases:** `history-nonfiction` 0/3 in both systems; `kafka-filters` 1/3 for the agent.
- **How it was detected:** `evals report` on the recorded runs.
- **What it implies:**
  - The agent's validate-and-repair loop is what makes recommendations acceptable: the baseline mostly recommends books it can't verify or the reader already has. The price is about 3× the requests and latency.
  - One search per turn is common (about half the runs), as F-007 suggested.
  - The 4 `catalog_unavailable` runs and the two hard cases need investigating before reading more into the numbers.
- **Fix:** None yet; investigation follows.
- **How it was verified:** The report above, from the stored records.

## F-012 · The 8 agent failures in F-011: transient 503s and late submission (2026-11-19)

- **What happened:** Investigation of the 8 failed `agent_v3` runs in F-011, from the stored records only, then 3 live Google Books requests for one lookup.
- **Cause 1, transient Google Books 503s (4 `catalog_unavailable` runs):** each run lost exactly one validation lookup to HTTP 503 "Service temporarily unavailable": no 429, no timeout. The agent's own searches hit two more. That's 6 failures in at least 370 Google Books requests during the agent's 20 minutes, about 1.6%, spread over 17 minutes rather than one outage. A run makes about 10 Google Books requests, the adapter never retried, and one failed validation lookup stops a run (D-014): a 1.6% request error rate became 10% of runs.
- **Cause 2, late submission (4 `iterations_exhausted` runs):** the model searched one book per turn, got the last-turn notice, submitted on turn 6, and then the one repair turn wasn't enough:
  - history-nonfiction run 1: the repair replaced a `not_found` book with one of 355 pages against a 300 limit, never checked.
  - history-nonfiction run 2: never submitted; it ignored the last-turn notice and kept searching.
  - history-nonfiction run 3: turn 6 submitted two hallucinated titles (caught as `not_found`); in the repair turn it searched instead of submitting, which ends the run.
  - kafka-filters run 1: the judge rejected a book on the to-read list in Spanish; the repair swapped in *The Metamorphosis*, already read (F-001), which it had searched on turn 2.
- **A likely false `not_found`:** history-nonfiction run 1's *Sobre la Brevedad de la Vida* (Séneca). Live check: both validator lookups returned 5 Séneca editions each, all matching on author, none on title. The exact title exists only inside combined volumes joined by " - " or ". " ("Sobre la Brevedad de la Vida - Sobre la Felicidad"), which D-001's variants don't split, and the standalone editions use other Spanish translations of the Latin title ("De la brevedad de la vida"). Not a retrieval miss this time: the matcher rejects real editions. A third request (the model's own query) got a 503 itself.
- **Baseline, for context:** kafka-filters recommended the reader's own short books in 2 of 3 runs, each justified by citing itself (F-001 and F-004 together); history-nonfiction failed on hallucinated titles.
- **Quality the rules don't see:** the "history" picks included *Meditaciones* and Séneca (philosophy), and all three history runs began by searching a novel.
- **How it was detected:** The report's per-case outcomes, the records' rejections and turn traces, and live Google Books requests for the Séneca lookup.
- **Fix:** Retry Google Books requests once on 5xx and network errors (D-027). Force `submit_recommendations` on the last turn and the repair turn, and move the notice to 2 turns left (D-028). The Séneca title matching: pending a decision.

## F-013 · Round 2 (harness 2): the agent accepts 85%; run-to-run noise is about 2 or 3 runs in 39 (2026-11-21)

- **What happened:** `recommend_v1`, 13 cases × 3 runs, same prompts as F-011 (`baseline_v2`, `agent_v3`, `request_parser_v3`), harness 2: Google Books retries (D-027), forced submit and the notice at 2 turns left (D-028), combined volumes in title matching (D-029).

  | | Round 1 (F-011) | Round 2 |
  |---|---|---|
  | Agent accepted | 31/39 (79%) | 33/39 (85%) |
  | Agent `catalog_unavailable` | 4 | 0 |
  | Agent `iterations_exhausted` | 4 | 2 |
  | Agent `llm_error` | 0 | 4 |
  | Agent history-nonfiction | 0/3 | 3/3 |
  | Agent repair turn used | 5 | 2 |
  | Agent p95 latency | 65 s | 39.5 s |
  | Agent one search per turn | 20 | 18 |
  | Baseline accepted | 5/39 (13%) | 8/39 (21%) |
  | Baseline final `not_found` | 27 | 18 |
  | Baseline unparseable | 2 | 5 |

- **How it was detected:** `evals report`, which shows harness 1 and harness 2 as separate configurations.
- **What it implies:**
  - The targeted fixes worked where they aimed: no more `catalog_unavailable` (retries), history-nonfiction fixed and fewer repair turns (forced submit), fewer `not_found` (matcher). One search per turn barely moved, as expected: nothing targeted it.
  - **Run-to-run noise:** the baseline's prompts didn't change, yet its accept count moved 5 → 8 and its unparseable replies 2 → 5. Part of the `not_found` drop is the matcher fix, but the rest is noise. A difference of 2 or 3 runs out of 39 isn't conclusive on its own; only larger differences, or ones tied to a specific failure kind that a change targeted, are.
  - A new failure kind, 4 `llm_error`, all in Spanish requests (argentine-novel 2, borges-stories-es 1, short-light-es 1), needs investigating before the agent's 85% is read as final.
- **Fix:** None yet; investigation follows.
- **How it was verified:** The report from the stored records of both rounds.

## F-014 · The 4 `llm_error` runs in F-013: transient Gemini 503s, clustered in time (2026-11-22)

- **What happened:** All 4 `llm_error` runs of round 2 failed on one agent request with HTTP 503 UNAVAILABLE, "This model is currently experiencing high demand". borges-stories-es run 3 failed on turn 4, argentine-novel runs 1 and 2 on turn 6 (forced submit), and short-light-es run 3 on turn 1. None had submitted yet. The request parser (66 calls) and the same-work judge (31 calls) never failed.
- **How it was detected:** The stored records only (errors, call lists, turn traces, start times), no live calls.
- **Cause:** Gemini capacity, not our changes:
  - The first 196 LLM requests (10:26–10:40 UTC) had no failures. All 4 fell between 10:40 and 10:49, 4 of 130 requests (3%).
  - **Spanish is a coincidence of order:** runs went case by case, and the last part of the session was mostly Spanish cases (about 77% of the window's requests). The English cases in the same window (scifi-not-space, litrpg) didn't fail. With that share, all 4 random failures landing on Spanish requests has a chance of about 35%.
  - **Forced turns:** 2 of 21 forced requests failed, against 2 of 169 unforced, but the two forced failures are argentine-novel's back-to-back runs within one minute, inside the cluster. A broken tool choice would fail every forced call with a 400, not a 503.
  - Case-by-case order let one short outage hit the same case twice.
- **Why it surfaced now:** Any Gemini error other than a 429 became an immediate `llm_error`: `PacedLLM` retries only rate limits and the adapter doesn't retry (D-027). Round 1 simply had no 503s. Without these 4 runs, round 2's agent would be 33/35.
- **Fix:** One retry on LLM 5xx in a wrapper applied in wiring (D-030), interleaved run order in the eval runner (D-031), and an acceptance rate that excludes infrastructure failures in the report (D-032).
- **How it was verified:** Error messages, call positions, turn traces and timestamps of all agent records in the round 2 folder.

## F-015 · judge_v1 grades fits_request on runs that have no request (2026-11-23)

- **What happened:** In the first full judge_v1 run over round 2 (183 books), 15 books weren't judged. 10 of them, a third of the 29 books from runs without request text (kafka-filters: 5, library-only: 5), returned a `fits_request` verdict anyway, and the strict parser rejected the reply ("fits_request given without a request"). Example: kafka-filters run 3 book 3 (baseline, *The Great War and Modern Memory*). The request has only flags (`language: en`, `max_pages: 200`), and the judge wrote "388 pages, which violates the strict maximum filter of 200 pages", treating the filters as the request. The other 5 errors: `fits_request` missing with a request (3), a malformed JSON value (1), invalid JSON (1).
- **How it was detected:** The calibration report showed 19 of 20 tuning books judged. The stored judgment kept the raw reply.
- **Cause:** The only instruction to leave out `fits_request` was the system prompt's last line, after a JSON template that shows both criteria. library-only runs have no filters either and fail the same way, so the `<filters>` block isn't the only trigger: the model copies the template. The validator had already flagged the 388 pages (`max_pages`), so no rule failure was missed.
- **Fix:** judge_v2 ends the user prompt of a run without request text with a reminder to answer `reason_quality` only, and the system prompt shows the reduced reply shape (D-035). judge_v1 stays frozen. The parser stays strict.
- **How it was verified:** The 15 stored replies. Tests check the reminder appears only without a request. The live check is judge_v2's tuning calibration, which includes kafka-filters run 3 book 3.

## F-016 · judge_v2's tuning results looked better than they were: two unreadable replies, on two of the most discussed books (2026-11-23)

- **What happened:** judge_v2 on calibration_v1's tuning half scored:
  - `fits_request`: 13/16 exact (1 lenient, 2 stricter).
  - `reason_quality`: 9/18 exact (5 lenient, 4 stricter), every disagreement within one step.

  It read as an improvement on `fits_request`, a working form rule, and balanced, never-crossing errors on `reason_quality`. But 2 of the 20 books weren't judged: their replies were unreadable JSON, and the report counts only judged books.
  - **#38 *Poesía vertical*** (argentine-novel, baseline): `reason_quality` nested inside another `reason_quality` object. The reply said fits_request **no** (poetry, not the novel the request named) and reason_quality **yes** ("lyrical intensity and existential depth"). Labels: no and no.
  - **#10 *Historia mínima de México*** (history-nonfiction, agent): the key `justification` split by a line break (`"just\nification"`). The reply said fits_request **yes** and reason_quality **partial**. Labels: yes and no (after the D-036 correction).
- **With both counted**, from the replies:

  | Criterion | Pairs | Exact | Within one | Judge more lenient | Judge stricter |
  | --- | --- | --- | --- | --- | --- |
  | fits_request | 18 | 15/18 | 18/18 | 1 | 2 |
  | reason_quality | 20 | 9/20 | 19/20 | 7 | 4 |
  | reason_quality, yes vs partial or no | 20 | 10/20 | - | 6 | 4 |
  | reason_quality, no vs partial or yes | 20 | 18/20 | - | 2 | 0 |

  On the 18 parsed pairs the two-level views were 9/18 (5 lenient, 4 stricter) and 18/18.
- **What it implies:**
  - **Excluding the unreadable replies made the results look better than they were.** The form rule had not been measured on its one test book. The judge did confuse yes with no once (#38's reason links two poets only by mood, which v2 caps at partial). With both books counted, `reason_quality` leans lenient, 7 against 4.
  - **The missing judgments weren't random.** They were two of the most discussed books in the calibration. Unjudged books have to be counted and named, not dropped silently.
  - **The rest of the reasoning stands:** the remaining `reason_quality` disagreements sit on a fuzzy line between tone and concrete themes, or are cases where the judge applied the rubric more consistently than the labels (Levi, Benedetti). With 18 to 20 noisy pairs, more prompt iterations would risk overfitting the tuning labels.
- **How it was detected:** Investigating the report's "18 judged" of 20. The stored judgments keep the raw replies.
- **Cause:** Format, not criteria. The judge writes JSON as free text, and a small model at temperature 0 sometimes breaks it: F-015's pattern in v1, and now a split key and a nested object.
- **Fix:** judge_v3 answers through a forced tool call with a generated schema (D-037). The calibration report shows both two-level views of `reason_quality` (D-038).
- **How it was verified:** The two stored replies and labels, re-scored offline. judge_v3's tuning calibration is the live check.

## F-017 · judge_v3 passes the held-out thresholds, but the "no vs rest" threshold can't see a judge that never says no (2026-11-24)

- **What happened:** The single held-out look (D-034) with judge_v3 scored:

  | Measure | Result | Threshold |
  | --- | --- | --- |
  | `fits_request` exact | 12/16 (75%), 3 lenient, 1 stricter | at least 70%: pass |
  | `fits_request` within one step | 15/16 (94%) | at least 90%: pass |
  | `reason_quality`, "no vs partial or yes" | 18/20 (90%) | at least 85%: pass |
  | `reason_quality`, 3 levels | 9/20, 8 lenient, 3 stricter | none |
  | `reason_quality`, "yes vs partial or no" | 10/20 | none (known limitation) |

  Every book was judged. All three pre-registered thresholds pass.
- **But the "no vs partial or yes" threshold was a poor choice.** On the held-out half the judge never said no, and it missed both reasons the user labeled no. Bad reasons are rare, so a judge that never says no would also score 18/20 (90%). Both halves combined, recall on no is 1 of 5 and precision is 1 of 1: when the judge says no it is right, but it almost never says it.
- **The thresholds stay as recorded.** The judge passes formally, with this caveat. Changing a threshold after the look would be fitting it to the result, which is what D-034 set them in advance to prevent.
- **The class-imbalance lesson:** agreement on a two-level split is dominated by the common class. When one verdict is rare, overall agreement says almost nothing about it. A threshold on a rare class should be set on its recall (how many of the real cases the judge catches), alongside precision, not on overall agreement.
- **What it means for the eval report (judge scores of round 2):**
  - **`fits_request` is reliable:** 75% exact and 94% within one on unseen books, errors leaning slightly lenient.
  - **The share of bad reasons (`reason_quality` no) is a lower bound for both systems.** The judge catches about 1 in 5 of them. Both systems are judged by the same judge with the same blind spot, so the comparison between them holds. The absolute numbers don't.
  - **"yes" on `reason_quality` is unreliable:** 10/20 at the yes split, leaning lenient (8 against 3 on three levels). The share of good reasons should not be read as a measure of quality.
- **How it was detected:** Reading the held-out 3×3 table after the thresholds passed: the judge's "no" row was empty while the user's "no" column had 2 books.
- **Fix:** The calibration report shows recall and precision per verdict for each criterion, on both halves, so a rare class can't hide behind overall agreement again. judge_v3 is frozen as the judge with these caveats (D-039). The held-out half is not used for further iteration (D-034).
- **How it was verified:** Tests build a judge that never says no and check that its recall on no shows 0/2 even though split-at-no agreement stays high.

## F-018 · Goodbooks-10k spike: the catalog misses most of this reader's library and the eval's hard cases (2026-11-24)

- **What happened:** A spike before designing milestone B (a catalog with retrieval), to check the plan's assumption that Goodbooks-10k is a good base catalog. Dataset at commit `6dd165b` of `zygmuntz/goodbooks-10k`: `books.csv`, `book_tags.csv`, `tags.csv` and the 10,000 Goodreads XML files (`books_xml.zip`). Downloaded into `data/goodbooks/` (gitignored). No Gemini calls.
- **License:** The repository is CC BY-SA 4.0 (attribution, share-alike for anything derived). The metadata was extracted from the Goodreads API's XML. Goodreads' current terms forbid automated collection and redistribution of their content. **Rule: the dataset and anything derived from it are never committed.** A download script fetches the pinned files and checks their SHA-256.
- **What the dataset has without enrichment** (10,000 books):

  | Field | Complete | Note |
  | --- | --- | --- |
  | `goodreads_book_id`, `work_id`, title, authors, ratings | 100% | `work_id` groups editions (D-011) |
  | Original publication year | 100% | ends in 2017 |
  | Description (XML only) | 97% | median 855 characters, with HTML |
  | Page count (XML only) | 91% | of the edition in the dataset |
  | Language | 89% | `eng`/`en-*` 8,726; `spa` 20; other 170 |
  | ISBN-13 | 94% | exact in the XML; `books.csv` stores it as a float and loses digits |
  | Tags (top Goodreads shelves) | 100% | about 100 per book, noisy (`to-read`, `favorites`) |
  | Cover image | 67% | 3,332 are Goodreads' "no photo" placeholder |

- **Coverage of the reader's library (226 books):**

  | | Books | By Goodreads id or ISBN | Plus title + author |
  | --- | --- | --- | --- |
  | All | 226 | 39 (17%) | 80 (35%) |
  | Liked (4 or 5 stars) | 93 | 15 (16%) | 41 (44%) |
  | Read | 115 | 18 | 47 |
  | To-read | 109 | 21 | 33 |

  - Title + author also matches translations through `original_title` (*Rayuela* to *Hopscotch*, *La invención de Morel* to *The Invention of Morel*). 2 of its 41 matches are wrong (*Dune* matched *House Harkonnen*; *Guards! Guards!* matched its stage play), so the real total is about 78.
  - **87 of the 226 books (38%) were first published after 2017**, the dataset's last year. Most of the unmatched books are recent, Spanish or niche.
  - Only 2 matched books are Spanish editions. 48 library titles look Spanish, and 7 of them match.
- **Coverage of the eval cases** (rough candidate counts from tags, language, pages and authors; "strict" requires a tag's count to be at least 2% of the book's top tag):

  | Case | Candidates (loose / strict) | Comment |
  | --- | --- | --- |
  | library-only | 10,000 | |
  | kafka-filters (en, ≤200 pages) | 1,143 | |
  | short-and-light | 750 / 204 | humor, funny or light-reading tags, ≤250 pages |
  | dystopia-filters (en, ≤400 pages) | 460 / 171 | |
  | like-east-of-eden | 106 / 4 | family-saga tag; similarity needs embeddings, not tags |
  | fantasy-no-sanderson (600–800 pages) | 221 / 143 | 116 / 32 with a magic tag |
  | scifi-not-space | 2,816 / 997 | sci-fi without space tags |
  | history-nonfiction (≤300 pages) | 353 / 86 | |
  | borges-stories-es | 4 / 1 Spanish editions | 29 / 3 short-story collections in any language with Latin American tags |
  | short-light-es (spa, ≤250 pages) | 4 | |
  | argentine-novel | 10 books with Argentine tags | Only 3 are novels (*El túnel*, *Hopscotch*, *The Invention of Morel*), all on the reader's read or to-read shelves: **0 usable** |
  | more-claire-keegan | 0 by Keegan | 54 / 7 Irish literary fiction |
  | litrpg | 2 / 0 | both are comic fantasy, not litRPG |

  The four Spanish cases, the Argentine novel, Claire Keegan and litRPG are 7 of the 13 cases. The catalog has almost nothing for them.
- **Enrichment cost:**
  - **Google Books is mostly not needed to fill fields.** The XML already has pages, description, language and ISBN. Google Books would add a store link, and Spanish editions of English works, which the catalog lacks. The validator keeps checking existence live either way.
  - **Daily quota:** the default commonly reported for the Books API is 1,000 requests per day per project. The project's real limit is on its Quotas page in the Google Cloud console, which this spike couldn't check. At 1,000 per day, one ISBN lookup per book is 10 days for 10,000 books. If the real limit is higher, it's about an hour at a few requests per second.
  - **Embeddings:** about 2.7 million tokens for 10,000 books (title, authors, description without HTML, top 15 tags; about 270 tokens per book, 2,100 at most), under the 8,192-token input limit. Free-tier embedding limits are only shown in AI Studio. `batchEmbedContents` puts many texts in one request, which helps a requests-per-minute limit but not a tokens-per-minute limit. The Batch API supports embeddings, but its documented quotas cover paid tiers only, so free-tier support is unconfirmed. `gemini-embedding-001` and `gemini-embedding-2` have incompatible vector spaces: pick one and re-embed if it changes.
- **How it was detected:** Scripts over the downloaded files and the reader's export, using the project's own `titles_match` and `authors_match` for title matching. Live checks: GitHub (license, releases), Goodreads' terms, and the Gemini API docs.
- **What it implies:**
  - **Goodbooks-10k is a popularity snapshot of English books up to 2017.** For this reader it can serve as a backlist (popular English fiction, history, fantasy), but not as the whole catalog. Retrieval from it alone would make 7 of the 13 eval cases worse than today's live search.
  - **Live search stays a tool.** A catalog can add fast, filterable, similarity-based candidates. Recent, Spanish and niche books still need Google Books.
  - **"Catalog" may mean another source.** Open Library's data dumps are multilingual and not limited to 2017. Their license and description coverage are unverified: worth a second spike before committing to Goodbooks.
- **Fix:** None; this informs the milestone B design.
- **How it was verified:** The counts above, from local files only. The spike scripts are throwaway and not committed.

## F-019 · Open Library dumps spike: a license-clean, current catalog that covers this reader, with descriptions as the weak spot (2026-11-24)

- **What happened:** A second catalog spike after F-018, on Open Library's monthly dumps of 2026-10-31: works (4.1 GB compressed), editions (12.6 GB), authors (0.8 GB), reading log and ratings. The files were downloaded in parallel byte ranges (21.5 minutes) and processed in one pass each (7 minutes). Only small extracts were kept in `data/` (gitignored), and the dumps were deleted afterwards. No Gemini calls.
- **License:** Open Library's FAQ asks that catalog contributions be released under CC0 1.0. The licensing page says the Internet Archive "does not assert any new copyright", with a caveat about existing rights on some contributions. **A public demo can serve data derived from the catalog records.** Descriptions copied in from publishers are the one gray area worth an attribution line. Covers have no monthly dump and no clear license: link to the Covers API, never redistribute.
- **Size and completeness (whole dump):**
  - 41.7 million works. 4.7% have a description, 50% have subjects.
  - 56.9 million editions. 63% have a page count, 64% an ISBN, 87% a language, 97% a year.
  - Languages by edition: English 32.2 million, German 3.6 million, French 2.8 million, **Spanish 2.0 million**.
- **A manageable subset by popularity** (reading-log entries plus ratings per work; 13.2 million log rows and 1.1 million ratings over 3.4 million works):

  | Works with at least | Works | Description | Subjects | An edition with pages | An ISBN-13 | A Spanish edition |
  | --- | --- | --- | --- | --- | --- | --- |
  | 5 signals | 451,797 | 41% | 85% | 88% | 81% | 24,051 (5%) |
  | 20 signals | 76,964 | 62% | 88% | 92% | 88% | 10,209 (13%) |
  | 100 signals | 9,897 | 79% | 91% | 95% | 94% | 3,509 (35%) |

- **How current:** editions per publication year peak in 2021 (2.3 million), then fall: 2023 0.8 million, 2024 0.45 million, 2025 0.21 million, 2026 so far 77,000. Cataloging lags by a year or two, but recent books are there: 30,927 works in the ≥5 subset first appeared in 2020–2024. Goodbooks stops at 2017.
- **Coverage of the reader's library** (by ISBN through cached work keys, then title + author with the project's matchers; 2 or 3 of the 51 title matches are combined volumes, such as *Magician / Assassin's Apprentice*):

  | Popularity cutoff | All 226 books | Liked (93) |
  | --- | --- | --- |
  | none | 207 (92%) | 86 (92%) |
  | ≥5 | 182 (81%) | 81 (87%) |
  | ≥20 | 155 (69%) | 71 (76%) |
  | ≥100 | 97 (43%) | 43 (46%) |

  - Books first published after 2017: 75 of 87 match (Goodbooks: none). Spanish-looking titles: 41 of 48 (Goodbooks: 7).
  - 151 of the 207 matched works have a description, including 67 of the 93 liked books (72%).
- **Coverage of the eval cases** (works with ≥5 / ≥20 signals; from subjects, edition languages and median page counts; subjects are noisy, so the counts are rough):

  | Case | Candidates | Goodbooks (F-018) | Comment |
  | --- | --- | --- | --- |
  | kafka-filters (English edition, ≤200 pages) | 117,614 / 20,657 | 1,143 | |
  | short-and-light (≤250 pages, humor) | 9,897 / 2,228 | 204–750 | |
  | dystopia-filters (English, ≤400 pages) | 432 / 169 | 171–460 | |
  | like-east-of-eden (family saga) | 113 / 41 | 4–106 | similarity needs embeddings |
  | fantasy-no-sanderson (600–800 pages) | 632 / 275 | 143–221 | |
  | scifi-not-space | 10,430 / 2,629 | 997–2,816 | |
  | history-nonfiction (≤300 pages) | 14,866 / 1,642 | 86–353 | "history" subjects include self-help; needs a stricter filter |
  | borges-stories-es (Spanish edition + short stories) | 1,078 / 643 | 1–4 | includes translations, which is right for a Spanish request |
  | short-light-es (Spanish edition, ≤250 pages) | 11,214 / 4,399 | 4 | |
  | argentine-novel | 146 / 48 with Argentine subjects | 0 usable | *Sobre héroes y tumbas*, Mariana Enríquez and more, beyond the reader's shelves |
  | more-claire-keegan | 6 by Keegan | 0 | *Walk the Blue Fields*, *Antarctica* and *The Forester's Daughter* aren't in the library |
  | litrpg | 30 / 10 | 0 | mostly the *Dungeon Crawler Carl* series, which the reader knows; still thin |

- **How it was detected:** Scripts over the dumps and the reader's export, using `titles_match` and `authors_match` from `domain/matching.py`. Live checks: Open Library's dumps, licensing and FAQ pages.
- **What it implies:**
  - **Open Library is a viable catalog where Goodbooks wasn't:** a clean license for a public demo, books up to 2025, Spanish editions, and 69–81% of this reader's library at a ≥20 or ≥5 cutoff. Every Spanish case and the Argentine novel go from near zero to hundreds of candidates.
  - **Descriptions are the weak spot:** 62% at ≥20 signals. The rest would be embedded from title, author and subjects. Filling them from Google Books would take about 29,000 requests at ≥20, about 29 days of the 1,000-per-day quota: not worth it for the whole catalog.
  - **Embedding cost:** about 77,000 works × about 200 tokens is about 15 million tokens, about 8.5 hours at 30,000 tokens per minute, and over 1,000 requests per day unless texts are batched per request. A local multilingual embedding model would avoid the quota and is worth comparing.
  - **Page counts and languages are per edition:** a filter must ask "is there an edition that fits", which is what the validator already does with Google Books.
  - **The library extract is useful now:** Open Library descriptions for 151 library books, at no API cost, feed the proposed library retrieval design.
- **Fix:** None; this informs milestone B.
- **How it was verified:** The counts above, from the dump extracts. The extracts were deleted afterwards except a 240 KB file of the library's matched works (`data/openlibrary/library_works.json`). The spike scripts are throwaway and not committed.

## F-020 · The agent_v4 round lost 18 of 39 runs to Gemini overload, and the judge 18 of 63 books (2026-11-24)

- **What happened:** The agent_v4 round (harness 3, D-041) ended with 18 runs stopped by infrastructure: 16 `llm_error` and 2 `interpretation`. p95 run latency was 111 s. judge_v3 then failed on 18 of 63 books. Failures looked clustered by case: short-light-es lost all 3 runs.
- **Cause: Gemini flash-lite was overloaded, not the agent or retrieval:**
  - **Every failure is a server or network error after the one retry D-030 allows:** 17 HTTP 503 UNAVAILABLE ("This model is currently experiencing high demand") and 1 `RemoteProtocolError` (a dropped connection) during request interpretation. All 18 judge errors are also 503s after a retry.
  - **The failure rate grew over the session.** Runs 1, 2 and 3 of every case (interleaved, D-031) lost 4, 3 and 11 runs. 19% of all agent requests (44 of 226) failed on the first attempt, against 3% in F-014. 26 of those succeeded on the retry; 18 failed twice. In the judge run (15:29–15:39), 19 of the 45 successful calls also needed a retry.
  - **The service was slow, too:** successful calls averaged 6.4 s (p95 27 s, max 77 s), against 2.9 s (p95 4.3 s) in round 2. Calls per run didn't change: 6.4, against 6.3 in round 2. That explains the 111 s p95: slow calls and retry pauses, not more turns.
  - **Retrieval isn't involved:** 0 retrieval errors. The 31 runs with request text all got their 8 books, from cached query vectors.
  - **The case clustering is timing:** each case keeps the same position in every pass. short-light-es is the last case of each pass, and its runs started at 14:02, 14:13 and 14:26, inside the worst minutes.
- **How it was detected:** The report's outcomes, then the records' call lists (error statuses, retries, latencies, start times) and the stored judgments. No live calls.
- **What it implies:** D-041's count thresholds (distinct books cited, books cited in 3 or more cases) need a complete round. Half a round lowers them mechanically, and the surviving runs aren't a random sample of cases. They aren't evaluated until the round is complete.
- **Fix:** Rerun only the runs and judgments that an outage stopped, replacing them and backing up the old files (D-042). Running at a quieter time of day is the practical mitigation. One retry stays the policy.
- **How it was verified:** All 39 records and 63 judgments of the round, read offline.

## F-021 · Library retrieval: negation is ignored, irrelevant books still fill the list, and citations repeat within a case (2026-11-24)

- **What happened:** The per-case listing of retrieved and cited books (D-041) from the agent_v4 round shows three patterns. These are observations from the runs that completed, not an evaluation: D-041's thresholds wait for the complete round (F-020), and nothing changes yet.
- **Embeddings ignore negation:**
  - fantasy-no-sanderson ("... but nothing from Brandon Sanderson") retrieved 4 Sanderson books: *Mistborn: The Final Empire*, *The Way of Kings*, *The Well of Ascension*, *The Hero of Ages*. Four reasons then cited Sanderson books as references. No recommended book was by Sanderson, so the exclusion held for the picks, but the reasons lean on the author the reader asked to avoid.
  - scifi-not-space retrieved *Project Hail Mary* and *The Dark Forest*, both about space.
  - The vector for "not about X" lies close to X: the query's words drive similarity, and "not" barely moves it.
- **With nothing similar in the library, retrieval still returns 8 books:** for history-nonfiction, the list was unrelated fiction (*Ficciones*, *The Three-Body Problem*, *Bestiario*...). The agent ignored it: none of its 3 citations came from the list. Top-k has no similarity floor, so "least dissimilar" shows up as "most related".
- **Repetition within a case:** *Small Things Like These* is cited in 6 reasons for more-claire-keegan (plus *Foster* in 3), and *East of Eden* in 6 for like-east-of-eden. Retrieval puts the obvious reference first in every run, and the agent cites it every time. That's reasonable for one run and monotonous over three. D-041's cross-case reuse measure doesn't see it.
- **Two more observations (mine, for your review):**
  - **Hub books:** *Las cosas que perdimos en el fuego* and *Te di ojos y miraste las tinieblas* were retrieved in 7 of the 11 cases with retrieval, whatever the request. Generic descriptions sit close to many queries, so part of "the same favorites" problem moves into retrieval.
  - **Parser noise reaches the query:** fantasy-no-sanderson got two different lists for the same request. The parsed preference sometimes contains stray newlines (F-010's pattern). After whitespace collapsing, "magic system\n, but" became "system , but", which no longer matches the request text, so the query appended a near-duplicate preference and embedded a slightly different text.
- **How it was detected:** The report's "Retrieved and cited per case" section and the records' parsed preferences.
- **Fix:** None yet, by decision. Options for later, not applied: a similarity floor (or "no related books" when nothing is close), stripping negated clauses from the query, preferring variety across runs or against hub books, and normalizing the preference before comparing it with the text.
- **How it was verified:** The 21 completed records' `retrieved` and `cited_titles` fields, rendered by `citation_report`.
