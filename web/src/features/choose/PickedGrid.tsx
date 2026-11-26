import type { PickedBook } from "../../api/client";
import { Cover } from "../../components/Cover";
import { ui } from "../../components/ui";
import { MIN_PICKS } from "./picks";

export function PickedGrid({
  picks,
  onRemove,
}: {
  picks: readonly PickedBook[];
  onRemove: (sourceId: string) => void;
}) {
  return (
    <section aria-labelledby="picked-heading" className="mt-8">
      <div className="flex items-baseline justify-between">
        <h3 id="picked-heading" className="font-sans text-base font-semibold">
          Picked
        </h3>
        <p className="text-sm text-muted">
          {picks.length} picked · minimum {MIN_PICKS}
        </p>
      </div>
      {picks.length === 0 ? (
        <p className="mt-3 rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted">
          Books you add show up here.
        </p>
      ) : (
        <ul className="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-6">
          {picks.map((book) => (
            <li key={book.source_id} className="relative min-w-0">
              <Cover title={book.title} author={book.authors?.[0]} url={book.cover_url} />
              <button
                type="button"
                onClick={() => onRemove(book.source_id)}
                aria-label={`Remove ${book.title}`}
                className="absolute top-2 right-2 grid size-7 place-items-center rounded-full bg-bg/90 text-sm font-bold text-text hover:bg-bg"
              >
                ×
              </button>
              {book.cover_url && (
                <p className={`${ui.wrap} mt-1 line-clamp-2 text-xs text-muted`}>{book.title}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
