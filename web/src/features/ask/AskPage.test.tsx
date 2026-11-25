import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { json, MANUAL, mockApi, RESULT, renderApp, sse } from "../../test/api";

const START = { type: "started", run_id: "run-1", prompt_id: "agent_v3" };
const INTERPRETED = {
  type: "interpreted",
  preference: "short, melancholic novel",
  sources: { language: "text" },
  filters: { language: "en", max_pages: 300, min_pages: null },
};

describe("asking, with live progress", () => {
  it("streams the steps, removes a filter by rerunning, and shows the results on done", async () => {
    const bodies: unknown[] = [];
    mockApi({
      "GET /profiles/p": () => json(MANUAL),
      "POST /recommendations": async (request) => {
        bodies.push(await request.json());
        return sse([
          START,
          INTERPRETED,
          {
            type: "searching",
            iteration: 1,
            searches: [{ query: "The Stranger Albert Camus", outcome: "5 books: X", found: 5 }],
          },
          { type: "submitted", iteration: 2, titles: ["The Stranger", "B", "C"] },
          { type: "checking", iteration: 2 },
          { type: "done", result: RESULT },
        ]);
      },
    });
    const user = userEvent.setup();
    const { router } = renderApp("/p/p/ask");
    expect(await screen.findByText("Using your 6 picked books")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Your request"), "a short, melancholic novel{Enter}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/p/p/results/run-1"));
    expect(bodies[0]).toEqual({
      profile_id: "p",
      request: "a short, melancholic novel",
      ignore_filters: [],
    });
    expect(JSON.parse(sessionStorage.getItem("next-read:result:run-1") ?? "{}").request).toBe(
      "a short, melancholic novel",
    );
  });

  it("reruns without a filter the user removed", async () => {
    const bodies: { ignore_filters: string[] }[] = [];
    mockApi({
      "GET /profiles/p": () => json(MANUAL),
      "POST /recommendations": async (request) => {
        bodies.push(await request.json());
        return sse([
          START,
          INTERPRETED,
          { type: "failed", kind: "agent", message: "out of turns" },
        ]);
      },
    });
    const user = userEvent.setup();
    renderApp("/p/p/ask");
    await user.type(await screen.findByLabelText("Your request"), "short in English{Enter}");
    await user.click(
      await screen.findByRole("button", { name: "Remove the filter English and ask again" }),
    );
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]?.ignore_filters).toEqual(["language"]);
  });

  it("shows a clear failure with a retry", async () => {
    mockApi({
      "GET /profiles/p": () => json(MANUAL),
      "POST /recommendations": () =>
        sse([START, { type: "failed", kind: "unavailable", message: "Gemini API error 503" }]),
    });
    const user = userEvent.setup();
    renderApp("/p/p/ask");
    await user.click(await screen.findByRole("button", { name: "Recommend" }));
    expect(
      await screen.findByRole("heading", { name: "The book services are busy" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Gemini API error 503")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("goes to the demo-limit screen on HTTP 429, with the retry time", async () => {
    mockApi({
      "GET /profiles/p": () => json(MANUAL),
      "POST /recommendations": () =>
        json({ error: "demo_limit", message: "limit", retry_after_s: 12000 }, { status: 429 }),
    });
    const user = userEvent.setup();
    const { router } = renderApp("/p/p/ask");
    await user.click(await screen.findByRole("button", { name: "Recommend" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/limit"));
    expect(router.state.location.search).toBe("?retry=12000");
  });

  it("goes to the demo-limit screen on a failed event of kind demo_limit", async () => {
    mockApi({
      "GET /profiles/p": () => json(MANUAL),
      "POST /recommendations": () =>
        sse([START, { type: "failed", kind: "demo_limit", message: "quota" }]),
    });
    const user = userEvent.setup();
    const { router } = renderApp("/p/p/ask");
    await user.click(await screen.findByRole("button", { name: "Recommend" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/limit"));
  });

  it("aborts the stream when the user leaves the page", async () => {
    let signal: AbortSignal | undefined;
    mockApi({ "GET /profiles/p": () => json(MANUAL), "GET /profiles/presets": () => json([]) });
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/recommendations")) {
        signal = init?.signal ?? undefined;
        const body = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(START)}\n\n`));
          },
        });
        return Promise.resolve(
          new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
        );
      }
      return original(input, init);
    });
    const user = userEvent.setup();
    const { router } = renderApp("/p/p/ask");
    await user.click(await screen.findByRole("button", { name: "Recommend" }));
    await waitFor(() => expect(signal).toBeDefined());
    expect(signal?.aborted).toBe(false);
    await router.navigate("/");
    await waitFor(() => expect(signal?.aborted).toBe(true));
  });
});
