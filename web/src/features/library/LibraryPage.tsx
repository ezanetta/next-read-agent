import { type KeyboardEvent, useEffect, useId, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import type { LibraryBook, PickedBook, ProfileSummary } from "../../api/client";
import { ApiError } from "../../api/errors";
import { useProfile, useProfileBooks } from "../../api/queries";
import { Cover } from "../../components/Cover";
import { ErrorState } from "../../components/ErrorState";
import { Layout } from "../../components/Layout";
import { PickedBadge, Stars } from "../../components/Stars";
import { ui } from "../../components/ui";
import type { ChooseState } from "../choose/ChoosePage";
import { presetTitle } from "../choose/SampleReaders";
import { AgentSeesPanel } from "./AgentSeesPanel";

type Shelf = LibraryBook["shelf"];
const SHELF_LABELS: Record<Shelf, string> = {
  read: "Read",
  to_read: "To read",
  currently_reading: "Reading",
};

export function libraryTitle(profile: ProfileSummary): string {
  switch (profile.source) {
    case "manual":
      return `${profile.read} books you picked`;
    case "sample":
      return presetTitle(profile);
    case "owner":
      return "The author's library";
    default:
      return "Your Goodreads library";
  }
}

/** Library books as picks, for "Edit list": the books start the picker again. */
function asPicks(books: readonly LibraryBook[]): PickedBook[] {
  return books
    .filter((b) => b.shelf === "read")
    .map((b, i) => ({
      source_id: `library-${i}-${b.title}`,
      title: b.title,
      authors: [b.author],
      cover_url: b.cover_url,
    }));
}

export function LibraryPage() {
  const { profileId = "" } = useParams();
  const profile = useProfile(profileId);
  const books = useProfileBooks(profileId);
  const navigate = useNavigate();
  const [shelf, setShelf] = useState<Shelf>("read");
  const tabsId = useId();
  const { hash } = useLocation();
  const loaded = Boolean(books.data);

  // Links from results point at a cited book (#book-...): scroll it into view.
  useEffect(() => {
    if (!loaded || !hash) return;
    document
      .getElementById(decodeURIComponent(hash.slice(1)))
      ?.scrollIntoView?.({ block: "center" });
  }, [loaded, hash]);

  const error = profile.error ?? books.error;
  if (error) {
    const gone = error instanceof ApiError && error.status === 404;
    return (
      <Layout step={1}>
        <ErrorState title={gone ? "This list has expired" : "The library couldn't load"}>
          {gone
            ? "Lists are kept for 3 days, and this one is gone or never existed. Pick your books again, or start from a sample reader."
            : "Check that the API is running, then try again."}
        </ErrorState>
      </Layout>
    );
  }
  if (!profile.data || !books.data) {
    return (
      <Layout step={1}>
        <p aria-live="polite" className="text-muted">
          Loading the library…
        </p>
      </Layout>
    );
  }

  const summary = profile.data;
  const all = books.data;
  const shelves = (Object.keys(SHELF_LABELS) as Shelf[]).filter(
    (s) => s !== "currently_reading" || all.some((b) => b.shelf === s),
  );
  const shown = all.filter((b) => b.shelf === shelf);

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    const index = shelves.indexOf(shelf);
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) return;
    event.preventDefault();
    const next = shelves[(index + step + shelves.length) % shelves.length] ?? "read";
    setShelf(next);
    document.getElementById(`${tabsId}-${next}`)?.focus();
  }

  return (
    <Layout step={1}>
      <div className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className={ui.eyebrow}>Your library</p>
          <h1 className="mt-2 text-4xl font-bold sm:text-6xl">{libraryTitle(summary)}</h1>
          {summary.description && summary.source === "sample" && (
            <p className="mt-2 text-muted">
              A sample reader · {summary.description} · {summary.read} books
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-3">
          {summary.source === "manual" && (
            <button
              type="button"
              className={ui.ghost}
              onClick={() =>
                navigate("/", { state: { picks: asPicks(all) } satisfies ChooseState })
              }
            >
              Edit list
            </button>
          )}
          <Link to={`/p/${summary.id}/ask`} className={ui.primary}>
            Ask for recommendations
          </Link>
        </div>
      </div>

      <div role="tablist" aria-label="Shelves" className="mt-10 flex gap-2 border-b border-border">
        {shelves.map((s) => {
          const count = all.filter((b) => b.shelf === s).length;
          const selected = s === shelf;
          return (
            <button
              key={s}
              id={`${tabsId}-${s}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${tabsId}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setShelf(s)}
              onKeyDown={onTabKey}
              className={`-mb-px border-b-2 px-5 py-3 font-semibold ${
                selected
                  ? "border-accent text-text"
                  : "border-transparent text-muted hover:text-text"
              }`}
            >
              {SHELF_LABELS[s]} · {count}
            </button>
          );
        })}
      </div>

      <div className="mt-8 grid gap-10 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div id={`${tabsId}-panel`} role="tabpanel" aria-labelledby={`${tabsId}-${shelf}`}>
          {shown.length === 0 ? (
            <p className="text-muted">No books on this shelf.</p>
          ) : (
            <ul className="grid grid-cols-2 gap-x-6 gap-y-10 sm:grid-cols-3 xl:grid-cols-4">
              {shown.map((book) => (
                <li key={`${book.title}|${book.author}`} id={bookAnchor(book.title)}>
                  <Cover title={book.title} author={book.author} url={book.cover_url} />
                  <p className="mt-3 font-semibold leading-snug">{book.title}</p>
                  <p className="text-sm text-muted">{book.author}</p>
                  <div className="mt-2">
                    {book.picked ? (
                      <PickedBadge />
                    ) : book.rating !== null ? (
                      <Stars rating={book.rating} />
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <AgentSeesPanel profile={summary} />
      </div>
    </Layout>
  );
}

/** The element id of a book in the library view, for links from results. */
export function bookAnchor(title: string): string {
  return `book-${title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")}`;
}
