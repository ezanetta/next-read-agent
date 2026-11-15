# Findings

Real-world failures: what happened, how it was detected, the fix, and how it was verified.

## F-001 · Translated title of a read book recommended and passed validation (2026-11-15)

- **What happened:** A baseline run with `--max-pages 200 --language en` recommended *The Metamorphosis* (Franz Kafka) in English. The reader had read *La metamorfosis* in Spanish. The model's own reason said the reader had read it, and the validator reported PASSED: titles in different languages never match.
- **How it was detected:** Manual review of the CLI output of the first real run.
- **Fix:** Partial so far.
  - Prompt (`baseline_v1`): a book counts as read in any language or edition; translations, other editions and retitled versions must never be recommended; a language filter means "a new book available in that language", never "re-read a listed book".
  - Validator: work-level matching (grouping translations and editions under one work) is the planned fix and not implemented yet.
- **How it was verified:** Unit tests check the new prompt rules. `test_known_failure_translated_title_of_a_read_book` reproduces the case and is marked `xfail(strict=True)`, so the validator gap stays visible until work-level matching makes it pass. Not yet re-checked with a real run.
