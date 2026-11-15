# Findings

Real-world failures: what happened, how it was detected, the fix, and how it was verified.

## F-001 · Translated title of a read book recommended and passed validation (2026-11-15)

- **What happened:** A baseline run with `--max-pages 200 --language en` recommended *The Metamorphosis* (Franz Kafka) in English. The reader had read *La metamorfosis* in Spanish. The model's own reason said the reader had read it, and the validator reported PASSED: titles in different languages never match.
- **How it was detected:** Manual review of the CLI output of the first real run.
- **Fix:** Partial so far.
  - Prompt (`baseline_v1`): a book counts as read in any language or edition; translations, other editions and retitled versions must never be recommended; a language filter means "a new book available in that language", never "re-read a listed book".
  - Validator: work-level matching (grouping translations and editions under one work) is the planned fix and not implemented yet.
- **How it was verified:** Unit tests check the new prompt rules. `test_known_failure_translated_title_of_a_read_book` reproduces the case and is marked `xfail(strict=True)`, so the validator gap stays visible until work-level matching makes it pass.
- **Re-check with real runs (F-002):** the prompt rule alone did not work. *The Metamorphosis* was recommended twice more, once with a reason citing "La metamorfosis by the same author" as if they were different books. Still open until work-level matching lands.

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
