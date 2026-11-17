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
- **Fix:** Not yet. `cites_library` should not count a citation of the recommended work itself (same title variants, or the library book it collides with).
- **How it was verified:** Not yet.

## F-005 · The agent searched to confirm books it already had in mind, and ran out of turns (2026-11-17)

- **What happened:** The agent (`agent_v1`, `gemini-3.5-flash-lite`) ran on the 5 baseline requests. 3 passed on the first submission. 2 failed with `iterations_exhausted` and 0 submissions, after about 30,000 tokens with nothing to show.
- **How it was detected:** The run summaries (iterations, submissions, about 20 output tokens per turn), then the `--verbose` turn traces of the two failing requests.
- **What the traces showed:**
  - The model never used search to discover books. It picked books from memory and searched each "title author" to confirm it existed, as the `search_books` description told it to ("confirm a book exists before recommending it"). The validator already checks existence on submission, so these searches were redundant.
  - Parallel versus sequential decided the outcome. The fast run made 3 searches in one turn (2 iterations, about 10,000 tokens). The slow run made one search per turn and only submitted on the last turn (6 iterations, about 30,000 tokens). Same behavior otherwise.
  - Search did help once: the model searched "Las malas" with the wrong author, got junk, and retried with the right one.
- **Fix:** `agent_v2` (D-017): search is described as a tool for discovering candidates or resolving doubts about a title or author, existence is said to be checked on submission, and several searches go in the same turn. A last-turn notice (D-018) tells the model to submit now when one turn is left, for every prompt version. `agent_v1` stays unchanged for comparison.
- **How it was verified:** Unit tests for the new descriptions, the prompt choice and the notice. Not yet re-checked with real runs.
