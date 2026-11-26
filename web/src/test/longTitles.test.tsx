import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { saveResult } from "../features/results/resultStore";
import { hit, json, MANUAL, mockApi, PRESETS, RESULT, renderApp } from "./api";

// jsdom has no layout, so this checks the two CSS rules that keep a long single word
// inside its box (it can't measure overflow):
// 1. the text element wraps long words (wrap-break-word) or truncates them;
// 2. every element between it and its card that is a flex or grid item has min-w-0, so
//    the item can shrink below its longest word instead of pushing the card wider.
const LONG = "Metamorphosisandtransfigurationsofeverything";
const AUTHOR = "Supercalifragilisticexpialidocious";

function isFlexOrGrid(element: Element | null): boolean {
  return Array.from(element?.classList ?? []).some((c) =>
    /^(?:[a-z]+:)?(?:flex|grid|inline-flex)$/.test(c),
  );
}

function expectContained(text: HTMLElement, card: Element) {
  expect(
    text.closest(".wrap-break-word, .truncate"),
    `"${text.textContent}" neither wraps nor truncates`,
  ).not.toBeNull();
  for (let node: Element | null = text; node; node = node.parentElement) {
    if (isFlexOrGrid(node.parentElement)) {
      expect(node.classList, `<${node.tagName.toLowerCase()} class="${node.className}">`).toContain(
        "min-w-0",
      );
    }
    if (node === card) break;
  }
}

afterEach(() => sessionStorage.clear());

describe("long single-word titles and authors stay inside their boxes", () => {
  it("on result cards, including cited books", () => {
    const [first, ...rest] = RESULT.books;
    if (!first) throw new Error("no first pick");
    const cited = first.cited.map((c) => ({ ...c, title: LONG }));
    saveResult("run-long", {
      profileId: "p",
      request: "",
      result: { ...RESULT, books: [{ ...first, title: LONG, author: AUTHOR, cited }, ...rest] },
    });
    renderApp("/p/p/results/run-long");
    const card = screen.getAllByRole("article")[0];
    if (!card) throw new Error("no card");
    expectContained(within(card).getByRole("heading", { name: LONG }), card);
    expectContained(within(card).getByText(AUTHOR), card);
    expectContained(within(card).getByRole("link", { name: LONG }), card);
  });

  it("in the library grid", async () => {
    mockApi({
      "GET /profiles/m": () => json({ ...MANUAL, id: "m", read: 1, books: 1 }),
      "GET /profiles/m/books": () =>
        json({
          profile_id: "m",
          books: [
            {
              title: `${LONG} (Series #1)`,
              author: AUTHOR,
              year: null,
              shelf: "read",
              rating: null,
              picked: true,
              cover_url: null,
            },
          ],
        }),
    });
    renderApp("/p/m");
    const panel = await screen.findByRole("tabpanel");
    const item = within(panel).getByRole("listitem");
    expectContained(within(item).getByText(LONG, { selector: "p" }), item);
    expectContained(within(item).getByText(AUTHOR, { selector: "p" }), item);
    // The Goodreads series note becomes its own small line.
    expect(within(item).getByText("Series #1")).toHaveClass("text-xs");
    expect(within(item).queryByText(/\(Series #1\)/)).not.toBeInTheDocument();
  });

  it("in search results and the picked grid", async () => {
    mockApi({
      "GET /profiles/presets": () => json(PRESETS),
      "GET /books/search": () =>
        json({ query: "meta", books: [{ ...hit(1), title: LONG, authors: [AUTHOR] }] }),
    });
    const user = userEvent.setup();
    renderApp("/");
    await user.type(screen.getByLabelText("Search by title or author"), "meta");
    const result = (await screen.findByRole("button", { name: `Add ${LONG}` })).closest("li");
    if (!result) throw new Error("no search result");
    expectContained(within(result).getByText(LONG), result);
    expectContained(within(result).getByText(AUTHOR), result);

    await user.click(within(result).getByRole("button", { name: `Add ${LONG}` }));
    const picked = screen.getByRole("button", { name: `Remove ${LONG}` }).closest("li");
    if (!picked) throw new Error("no picked book");
    // Without a cover URL the title is on the colored cover itself.
    const onCover = within(picked).getByText(LONG);
    expect(onCover).toHaveClass("wrap-break-word");
  });
});
