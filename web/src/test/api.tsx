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
