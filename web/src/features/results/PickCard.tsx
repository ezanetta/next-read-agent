import { Link } from "react-router";
import type { RecommendedBook, Schemas } from "../../api/client";
import { Cover } from "../../components/Cover";
import { PickedBadge, Stars } from "../../components/Stars";
import { ui } from "../../components/ui";
import { languageName } from "../../lib/format";
import { bookAnchor } from "../library/LibraryPage";

type Cited = Schemas["CitedLibraryBook"];

function because(cited: readonly Cited[]): string {
  if (cited.some((c) => c.picked)) return "Because you picked";
  if (cited.some((c) => (c.rating ?? 0) >= 4)) return "Because you liked";
  return "Because you read";
}

function edition(book: RecommendedBook): string {
  const parts = [
    book.pages ? `${book.pages} pages` : null,
    book.language ? `${languageName(book.language)} edition` : null,
  ];
  return parts.filter(Boolean).join(" · ");
}

export function PickCard({
  book,
  index,
  profileId,
}: {
  book: RecommendedBook;
  index: number;
  profileId: string;
}) {
  return (
    <article
      aria-labelledby={`pick-${index}`}
      className="flex flex-col rounded-2xl border border-border bg-surface p-6 sm:p-8"
    >
      <div className="flex items-end gap-5">
        <Cover title={book.title} url={book.cover_url} size="large" />
        <div className="min-w-0">
          <p className="text-sm font-semibold tracking-widest text-accent">PICK {index + 1}</p>
          <h2 id={`pick-${index}`} className="mt-1 text-2xl leading-tight font-bold">
            {book.title}
          </h2>
          <p className="mt-1 text-muted">{book.author}</p>
          {edition(book) && <p className="mt-2 text-sm text-muted">{edition(book)}</p>}
        </div>
      </div>
      <p className="mt-6 flex-1 text-lg leading-relaxed">{book.reason}</p>
      {book.cited.length > 0 && (
        <div className="mt-6 border-t border-border pt-5">
          <p className="text-sm text-muted">{because(book.cited)}</p>
          <ul className="mt-3 space-y-2">
            {book.cited.map((cited) => (
              <li key={cited.title} className="flex flex-wrap items-center gap-3">
                <Cover title={cited.title} url={cited.cover_url} size="strip" />
                <Link
                  to={{ pathname: `/p/${profileId}`, hash: bookAnchor(cited.title) }}
                  className="font-semibold underline-offset-4 hover:underline"
                >
                  {cited.title}
                </Link>
                {cited.picked ? (
                  <PickedBadge />
                ) : cited.rating !== null ? (
                  <Stars rating={cited.rating} />
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}
      {book.link && (
        <a
          href={book.link}
          target="_blank"
          rel="noreferrer"
          className={`${ui.secondary} mt-6 w-full`}
        >
          View on Google Books
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      )}
    </article>
  );
}
