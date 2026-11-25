import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";
import { routes } from "../App";

type Handler = (request: Request) => Response | Promise<Response>;

export function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

/** Replace fetch with handlers keyed by "METHOD /path" (the path after the API base).
 * Unknown requests fail the test loudly. Returns the recorded requests. */
export function mockApi(handlers: Record<string, Handler>): Request[] {
  const requests: Request[] = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    requests.push(request.clone());
    const path = new URL(request.url).pathname;
    const handler = handlers[`${request.method} ${path}`];
    if (!handler) throw new Error(`unexpected request: ${request.method} ${path}`);
    return handler(request);
  });
  return requests;
}

/** Render the app at `path` with a fresh query client; returns the router to inspect
 * where navigation went. */
export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router };
}

export const PRESETS = [
  {
    id: "sample-classics",
    name: "A classics reader",
    source: "sample",
    description: "Classic novels",
    books: 55,
    read: 50,
    liked: 50,
    to_read: 5,
    ratings_implied: true,
    expires_at: null,
    top_authors: ["John Steinbeck", "George Eliot", "Victor Hugo"],
    cover_urls: [],
  },
];

export function hit(i: number) {
  return {
    source_id: `id${i}`,
    title: `Book ${i}`,
    authors: [`Author ${i}`],
    year: 2000,
    isbn13: null,
    cover_url: null,
  };
}

/** A Server-Sent Events response carrying these events, like POST /recommendations. */
export function sse(events: object[]): Response {
  const text = events
    .map(
      (event) => `event: ${(event as { type: string }).type}\ndata: ${JSON.stringify(event)}\n\n`,
    )
    .join("");
  return new Response(text, { headers: { "Content-Type": "text/event-stream" } });
}

export const RESULT = {
  profile_id: "p",
  request: "a short novel",
  books: [1, 2, 3].map((i) => ({
    title: `Pick ${i}`,
    author: `Author ${i}`,
    reason: `Like Piranesi, pick ${i} is quiet.`,
    link: `https://books.example/${i}`,
    cover_url: null,
    pages: 100 + i,
    language: "en",
    cited: [
      {
        title: "Piranesi",
        author: "Susanna Clarke",
        rating: null,
        picked: true,
        shelf: "read",
        cover_url: null,
      },
    ],
  })),
  trace: {
    prompt_id: "agent_v3",
    iterations: 2,
    submissions: 1,
    rejections: [],
    steps: [
      {
        iteration: 1,
        searches: [{ query: "quiet novel", outcome: "5 books: A", found: 5 }],
        submitted: [],
        outcome: "searched",
      },
      {
        iteration: 2,
        searches: [],
        submitted: ["Pick 1", "Pick 2", "Pick 3"],
        outcome: "accepted",
      },
    ],
    llm_calls: 3,
    input_tokens: 3500,
    output_tokens: 196,
    duration_s: 10.4,
  },
};

export const MANUAL = {
  ...PRESETS[0],
  id: "p",
  source: "manual",
  name: "Your picks",
  read: 6,
  books: 6,
  to_read: 0,
};
