import { useId, useState } from "react";
import type { PickedBook, SearchHit } from "../../api/client";
import { MIN_QUERY, useSearchBooks } from "../../api/queries";
import { Cover } from "../../components/Cover";
import { useDebounce } from "../../lib/useDebounce";

export function toPick(hit: SearchHit): PickedBook {
  return {
    source_id: hit.source_id,
    title: hit.title,
    authors: hit.authors,
    isbn13: hit.isbn13,
    year: hit.year,
    cover_url: hit.cover_url,
  };
}

/** Search by title or author, with debounced autocomplete and an Add button per book. */
export function BookSearch({
  pickedIds,
  onAdd,
  full,
}: {
  pickedIds: ReadonlySet<string>;
  onAdd: (book: PickedBook) => void;
  full: boolean;
}) {
  const [query, setQuery] = useState("");
  const debounced = useDebounce(query, 300);
  const search = useSearchBooks(debounced);
  const inputId = useId();
  const statusId = useId();
  const active = debounced.trim().length >= MIN_QUERY;
  const hits = active ? (search.data ?? []) : [];

  return (
    <div>
      <label htmlFor={inputId} className="text-sm font-semibold text-muted">
        Search by title or author
      </label>
      <input
        id={inputId}
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="e.g. Piranesi"
        autoComplete="off"
        aria-describedby={statusId}
        className="mt-2 w-full rounded-xl border border-border-strong bg-bg px-5 py-4 text-lg text-text placeholder:text-muted focus:border-accent focus:outline-none"
      />
      <p id={statusId} aria-live="polite" className="sr-only">
        {active && search.isSuccess ? `${hits.length} books found` : ""}
      </p>
      {active && search.isError && (
        <p role="alert" className="mt-3 text-sm text-muted">
          Book search isn't available right now. Try again in a moment.
        </p>
      )}
      {hits.length > 0 && (
        <ul className="mt-2 divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface-2">
          {hits.map((hit) => {
            const added = pickedIds.has(hit.source_id);
            return (
              <li key={hit.source_id} className="flex items-center gap-4 px-4 py-3">
                <Cover title={hit.title} url={hit.cover_url} size="thumb" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold">{hit.title}</p>
                  <p className="truncate text-sm text-muted">{hit.authors.join(", ")}</p>
                </div>
                <button
                  type="button"
                  disabled={added || full}
                  onClick={() => onAdd(toPick(hit))}
                  aria-label={added ? `${hit.title} added` : `Add ${hit.title}`}
                  className="rounded-lg border border-border px-4 py-2 text-sm font-semibold text-text hover:bg-border disabled:text-muted disabled:hover:bg-transparent"
                >
                  {added ? "Added" : "Add"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
