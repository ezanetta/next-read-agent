import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE_URL, type FilterName, type RecommendationEvent } from "./client";
import { toApiError } from "./errors";

export type RecommendationRequest = {
  profile_id: string;
  request: string | null;
  ignore_filters: FilterName[];
};

/** Stop the library from retrying: one POST is one run, and runs cost quota. */
class Fatal extends Error {}

/**
 * POST /recommendations and pass each Server-Sent Event to `onEvent`, typed from the API
 * schema. Resolves when the stream ends (after `done` or `failed`) or when `signal` aborts.
 * Rejects with an ApiError when the API refuses the request (HTTP 404, 422, 429...), and
 * never retries. The request isn't restarted when the tab is hidden (openWhenHidden).
 */
export async function streamRecommendation(
  body: RecommendationRequest,
  onEvent: (event: RecommendationEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let refused: Error | undefined;
  try {
    await fetchEventSource(`${API_BASE_URL}/recommendations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
      openWhenHidden: true,
      fetch: (input, init) => globalThis.fetch(input, init),
      async onopen(response) {
        const type = response.headers.get("content-type") ?? "";
        if (response.ok && type.startsWith("text/event-stream")) return;
        const payload: unknown = await response.json().catch(() => undefined);
        refused = toApiError(response, payload);
        throw new Fatal(refused.message);
      },
      onmessage(message) {
        if (!message.data) return;
        onEvent(JSON.parse(message.data) as RecommendationEvent);
      },
      onerror(error: unknown) {
        throw error instanceof Fatal ? error : new Fatal(String(error));
      },
    });
  } catch (error) {
    if (signal.aborted) return;
    throw refused ?? error;
  }
}
