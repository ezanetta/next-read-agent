import { useCallback, useEffect, useReducer, useRef } from "react";
import type { FilterName } from "../../api/client";
import { ApiError } from "../../api/errors";
import { streamRecommendation } from "../../api/stream";
import { initialProgress, progressReducer } from "./progress";

/** How long the UI waits for a final event: the server's run deadline (180 s by default,
 * RUN_DEADLINE_S) plus a margin, so the server normally reports first (R-004). */
export const CLIENT_TIMEOUT_MS = 200_000;

/** Runs one recommendation stream at a time for a profile. Starting again, or leaving the
 * page (unmount), aborts the current one, so the server cancels its run. A demo-limit
 * refusal (HTTP 429) is reported through `onDemoLimit` instead of as a failure.
 *
 * Every run ends in `done` or `failed` (R-004): a stream that closes without a final
 * event, or doesn't finish within CLIENT_TIMEOUT_MS, becomes a failure with a retry,
 * never an endless spinner. */
export function useRecommendationStream(
  profileId: string,
  onDemoLimit: (retryAfterSeconds?: number) => void,
  timeoutMs: number = CLIENT_TIMEOUT_MS,
) {
  const [progress, dispatch] = useReducer(progressReducer, initialProgress);
  const controller = useRef<AbortController | null>(null);
  const demoLimit = useRef(onDemoLimit);
  demoLimit.current = onDemoLimit;

  useEffect(() => () => controller.current?.abort(), []);

  const start = useCallback(
    async (request: string | null, ignoreFilters: FilterName[] = []) => {
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      let finished = false;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        current.abort();
      }, timeoutMs);
      dispatch({ type: "start" });
      try {
        await streamRecommendation(
          { profile_id: profileId, request, ignore_filters: ignoreFilters },
          (event) => {
            if (current.signal.aborted) return;
            if (event.type === "done" || event.type === "failed") finished = true;
            dispatch({ type: "event", event });
          },
          current.signal,
        );
      } catch (error) {
        if (current.signal.aborted && !timedOut) return;
        if (error instanceof ApiError && error.isDemoLimit) {
          demoLimit.current(error.retryAfterSeconds);
          return;
        }
        if (!timedOut) {
          const message = error instanceof Error ? error.message : String(error);
          dispatch({ type: "error", kind: "unavailable", message });
          return;
        }
      } finally {
        clearTimeout(timer);
      }
      if (timedOut && !finished) {
        const seconds = Math.round(timeoutMs / 1000);
        dispatch({ type: "error", kind: "timeout", message: `No answer within ${seconds} s` });
      } else if (!finished && !current.signal.aborted) {
        const message = "The stream ended without a final event";
        dispatch({ type: "error", kind: "connection", message });
      }
    },
    [profileId, timeoutMs],
  );

  const cancel = useCallback(() => controller.current?.abort(), []);
  return { progress, start, cancel };
}
