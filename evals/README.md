# Evals

Frozen case sets for the eval harness (`src/book_agent/evals/`). A case set can't change once it has results: changes go in a new version (D-023).

| File | What it holds |
| --- | --- |
| `cases/recommend_v1.json` | 13 end-to-end recommendation cases for the baseline and the agent: no request, flags only, tone, genre plus filters, a reference book, exclusions, Spanish and English. |
| `cases/request_parser_v1.json`, `cases/request_parser_v2.json` | Labeled requests for the request parser eval: 23 in v1, 27 in v2, which adds `min_pages` labels (D-026). |

Results are written to `data/evals/` and stay private (D-024). The commands and how to read the results are in the main [README](../README.md#running-the-evals).
