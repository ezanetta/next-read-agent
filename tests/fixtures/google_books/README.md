# Google Books fixtures

| File | Origin |
| --- | --- |
| `volume_details.json` | The Volume example from Google's API docs (developers.google.com/books/docs/v1/using), lightly trimmed. |
| `search_results.json` | Hand-built in the documented response shape to cover the quirks: full/partial/year-only `publishedDate`, ISBN_10/ISBN_13/OTHER identifiers, `http:` thumbnails, HTML descriptions, missing `pageCount`/`description`/`categories`/`imageLinks`, an `infoLink`-only volume, a volume with almost no data, and a volume without a title (skipped). |
| `search_empty.json` | Documented behavior: `items` is omitted when nothing matches. |
| `error_429.json` | Recorded from the real API on 2026-11-13 (keyless request, daily quota exhausted). |
| `trevor_ampersand_query.json`, `trevor_intitle_main.json` | Recorded from the real API on 2026-11-18 (F-007): the validator's "&" query returning only reference works, and the fallback finding the book. |
| `seneca_search.json`, `seneca_fallback.json` | Reconstructed (F-012): titles and authors exactly as the live check of 2026-11-19 returned them for the validator's search and fallback, trimmed to the fields the adapter reads; ids and links are placeholders. Not raw recordings, because no live calls were allowed when the regression test was written. |

Real success responses could not be recorded on 2026-11-13 because keyless requests were rate-limited.
To re-record, run the live tests with `GOOGLE_BOOKS_API_KEY` set, or curl the API with a key.
