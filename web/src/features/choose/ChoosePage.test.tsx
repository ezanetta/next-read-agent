import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { hit, json, mockApi, PRESETS, renderApp } from "../../test/api";

describe("choose a library", () => {
  it("searches, picks 5 books, and continues to the new profile", async () => {
    const requests = mockApi({
      "GET /profiles/presets": () => json(PRESETS),
      "GET /books/search": (r) => {
        const q = new URL(r.url).searchParams.get("q") ?? "";
        return json({ query: q, books: [0, 1, 2, 3, 4, 5].map(hit) });
      },
      "POST /profiles/manual": () =>
        json({ ...PRESETS[0], id: "new-profile", source: "manual" }, { status: 201 }),
      "GET /profiles/new-profile": () => json({ ...PRESETS[0], id: "new-profile" }),
      "GET /profiles/new-profile/books": () => json({ profile_id: "new-profile", books: [] }),
    });
    const user = userEvent.setup();
    const { router } = renderApp("/");

    expect(await screen.findByText("Classics")).toBeInTheDocument();
    expect(screen.getByText(/50 books/)).toBeInTheDocument();
    const continueButton = screen.getByRole("button", { name: "Pick 5 more books" });
    expect(continueButton).toBeDisabled();

    await user.type(screen.getByLabelText("Search by title or author"), "book");
    for (const i of [0, 1, 2, 3]) {
      await user.click(await screen.findByRole("button", { name: `Add Book ${i}` }));
    }
    expect(screen.getByRole("button", { name: "Book 0 added" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pick 1 more book" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Add Book 4" }));
    await user.click(screen.getByRole("button", { name: "Remove Book 4" }));
    await user.click(screen.getByRole("button", { name: "Add Book 4" }));

    await user.click(screen.getByRole("button", { name: "Continue with these 5 books" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/p/new-profile"));
    const created = requests.find((r) => r.method === "POST");
    if (!created) throw new Error("no profile was created");
    expect((await created.json()).books).toHaveLength(5);
  });
});
