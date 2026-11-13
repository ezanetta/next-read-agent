# Google Books fixtures

| File | Origin |
| --- | --- |
| `volume_details.json` | The Volume example from Google's API docs (developers.google.com/books/docs/v1/using), lightly trimmed. |
| `search_results.json` | Hand-built in the documented response shape to cover the quirks: full/partial/year-only `publishedDate`, ISBN_10/ISBN_13/OTHER identifiers, `http:` thumbnails, HTML descriptions, missing `pageCount`/`description`/`categories`/`imageLinks`, an `infoLink`-only volume, a volume with almost no data, and a volume without a title (skipped). |
| `search_empty.json` | Documented behavior: `items` is omitted when nothing matches. |
| `error_429.json` | Recorded from the real API on 2026-11-13 (keyless request, daily quota exhausted). |

Real success responses could not be recorded on 2026-11-13 because keyless requests were rate-limited.
To re-record, run the live tests with `GOOGLE_BOOKS_API_KEY` set, or curl the API with a key.
