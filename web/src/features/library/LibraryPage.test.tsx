import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { COVER_POLL_MS } from "../../api/queries";
import { json, mockApi, PRESETS, renderApp } from "../../test/api";

const book = (title: string, shelf: string, rating: number | null, picked: boolean) => ({
  title,
  author: "Someone",
  year: null,
  shelf,
  rating,
  picked,
  cover_url: null,
});

describe("library view", () => {
  it("shows picked books as Picked, never with stars, and edits the list", async () => {
    mockApi({
      "GET /profiles/m": () =>
        json({ ...PRESETS[0], id: "m", source: "manual", read: 2, books: 2, to_read: 0 }),
      "GET /profiles/m/books": () =>
        json({
          profile_id: "m",
          books: [book("Piranesi", "read", null, true), book("Dune", "read", null, true)],
        }),
      "GET /profiles/presets": () => json(PRESETS),
    });
    const user = userEvent.setup();
    const { router } = renderApp("/p/m");
    expect(await screen.findByRole("heading", { name: "2 books you picked" })).toBeInTheDocument();
    const shelf = screen.getByRole("tabpanel");
    expect(within(shelf).getAllByText("Picked")).toHaveLength(2);
    expect(within(shelf).queryByRole("img", { name: /Rated/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ask for recommendations" })).toHaveAttribute(
      "href",
      "/p/m/ask",
    );

    await user.click(screen.getByRole("button", { name: "Edit list" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(await screen.findByRole("button", { name: "Remove Piranesi" })).toBeInTheDocument();
  });

  it("shows real ratings as stars and switches shelves by keyboard", async () => {
    mockApi({
      "GET /profiles/g": () =>
        json({
          ...PRESETS[0],
          id: "g",
          source: "goodreads",
          ratings_implied: false,
          read: 1,
          to_read: 1,
        }),
      "GET /profiles/g/books": () =>
        json({
          profile_id: "g",
          books: [book("East of Eden", "read", 5, false), book("Later", "to_read", null, false)],
        }),
    });
    const user = userEvent.setup();
    renderApp("/p/g");
    expect(await screen.findByRole("img", { name: "Rated 5 out of 5" })).toBeInTheDocument();
    expect(screen.queryByText("Picked")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Read · 1" }));
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "To read · 1" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("Later", { selector: "p" })).toBeInTheDocument();
  });

  it("explains an expired list", async () => {
    mockApi({
      "GET /profiles/old": () =>
        json({ error: "not_found", message: "profile not found" }, { status: 404 }),
      "GET /profiles/old/books": () =>
        json({ error: "not_found", message: "profile not found" }, { status: 404 }),
    });
    renderApp("/p/old");
    expect(
      await screen.findByRole("heading", { name: "This list has expired" }),
    ).toBeInTheDocument();
  });
});

describe("covers that arrive after an upload", () => {
  afterEach(() => vi.useRealTimers());

  it("refetches while covers are pending, shows them, and then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const pages = [
      { covers_pending: 2, cover_url: null },
      { covers_pending: 0, cover_url: "https://covers.openlibrary.org/b/id/7-M.jpg" },
    ];
    let calls = 0;
    mockApi({
      "GET /profiles/g": () =>
        json({ ...PRESETS[0], id: "g", source: "goodreads", ratings_implied: false, read: 1 }),
      "GET /profiles/g/books": () => {
        const page = pages[Math.min(calls, pages.length - 1)];
        calls += 1;
        return json({
          profile_id: "g",
          covers_pending: page?.covers_pending,
          books: [{ ...book("East of Eden", "read", 5, false), cover_url: page?.cover_url }],
        });
      },
    });
    renderApp("/p/g");
    expect(await screen.findByText(/Finding covers… 2 left/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();

    await vi.advanceTimersByTimeAsync(COVER_POLL_MS);
    await waitFor(() =>
      expect(document.querySelector("img")).toHaveAttribute(
        "src",
        "https://covers.openlibrary.org/b/id/7-M.jpg",
      ),
    );
    expect(screen.queryByText(/Finding covers/)).not.toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(COVER_POLL_MS * 3);
    expect(calls).toBe(2); // done: no more polling
  });
});
