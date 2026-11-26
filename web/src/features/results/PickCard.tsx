import { Link } from "react-router";
import type { RecommendedBook, Schemas } from "../../api/client";
import { Cover } from "../../components/Cover";
import { PickedBadge, Stars } from "../../components/Stars";
import { ui } from "../../components/ui";
import { languageName, splitSeries } from "../../lib/format";
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
    // Four rows: header, reason, citations, button. On wide screens the cards share them
    // through a subgrid, so the citation blocks and buttons line up across the three cards.
    <article
      aria-labelledby={`pick-${index}`}
      className="flex min-w-0 flex-col rounded-2xl border border-border bg-surface p-6 sm:p-8 lg:row-span-4 lg:grid lg:grid-cols-1 lg:grid-rows-subgrid lg:gap-y-0"
    >
      <div className="flex min-w-0 items-end gap-5 lg:self-start">
        <Cover title={book.title} url={book.cover_url} size="large" />
        <div className={ui.wrap}>
          <p className="text-sm font-semibold tracking-widest text-accent">PICK {index + 1}</p>
          <h2
            id={`pick-${index}`}
            className="mt-1 text-2xl leading-tight font-bold wrap-break-word hyphens-auto"
          >
            {book.title}
          </h2>
          <p className="mt-1 text-muted wrap-break-word">{book.author}</p>
          {edition(book) && <p className="mt-2 text-sm text-muted">{edition(book)}</p>}
        </div>
      </div>
      <p className="mt-6 flex-1 text-lg leading-relaxed">{book.reason}</p>
      {book.cited.length > 0 ? (
        <div className="mt-6 w-full min-w-0 border-t border-border pt-5 lg:self-start">
          <p className="text-sm text-muted">{because(book.cited)}</p>
          <ul className="mt-3 space-y-2">
            {book.cited.map((cited) => (
              <li key={cited.title} className="flex items-start gap-3">
                <Cover title={cited.title} url={cited.cover_url} size="strip" />
                {/* Title and badge flow as one line of text, so the badge follows the
                    last word instead of dropping below a long title. */}
                <p className={`${ui.wrap} self-center`}>
                  <Link
                    to={{ pathname: `/p/${profileId}`, hash: bookAnchor(cited.title) }}
                    className="mr-2 font-semibold underline-offset-4 hover:underline"
                  >
                    {splitSeries(cited.title).title}
                  </Link>
                  {cited.picked ? (
                    <PickedBadge />
                  ) : cited.rating !== null ? (
                    <Stars rating={cited.rating} />
                  ) : null}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div />
      )}
      {book.link ? (
        <a
          href={book.link}
          target="_blank"
          rel="noreferrer"
          className={`${ui.secondary} mt-6 w-full lg:self-end`}
        >
          View on Google Books
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      ) : (
        <div />
      )}
    </article>
  );
}
