import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { json, mockApi, PRESETS, RESULT, renderApp, sse } from "./test/api";

const CLASSICS = PRESETS[0];
const BOOKS = {
  profile_id: "sample-classics",
  books: [
    {
      title: "Piranesi",
      author: "Susanna Clarke",
      year: null,
      shelf: "read",
      rating: null,
      picked: true,
      cover_url: null,
    },
  ],
};

describe("the main flow, with the API mocked", () => {
  it("goes from a sample reader to the library, asks, and lands on the results", async () => {
    mockApi({
      "GET /profiles/presets": () => json(PRESETS),
      "GET /profiles/sample-classics": () => json(CLASSICS),
      "GET /profiles/sample-classics/books": () => json(BOOKS),
      "POST /recommendations": () =>
        sse([
          { type: "started", run_id: "run-9", prompt_id: "agent_v3" },
          {
            type: "interpreted",
            preference: "something quiet",
            sources: {},
            filters: { language: null, max_pages: null, min_pages: null },
          },
          { type: "done", result: { ...RESULT, profile_id: "sample-classics" } },
        ]),
    });
    const user = userEvent.setup();
    const { router } = renderApp("/");

    await user.click(await screen.findByRole("link", { name: /Classics/ }));
    expect(await screen.findByRole("heading", { name: "Classics" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit list" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Ask for recommendations" }));
    await user.type(await screen.findByLabelText("Your request"), "something quiet{Enter}");

    expect(
      await screen.findByRole("heading", { name: "Your next three reads" }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/p/sample-classics/results/run-9");

    await user.click(screen.getAllByRole("link", { name: "Piranesi" })[0] as HTMLElement);
    await waitFor(() => expect(router.state.location.hash).toBe("#book-piranesi"));
    expect(await screen.findByRole("heading", { name: "Classics" })).toBeInTheDocument();
  });
});
