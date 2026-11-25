import { useCallback, useEffect, useReducer, useRef } from "react";
import type { FilterName } from "../../api/client";
import { ApiError } from "../../api/errors";
import { streamRecommendation } from "../../api/stream";
import { initialProgress, progressReducer } from "./progress";

/** Runs one recommendation stream at a time for a profile. Starting again, or leaving the
 * page (unmount), aborts the current one, so the server cancels its run. A demo-limit
 * refusal (HTTP 429) is reported through `onDemoLimit` instead of as a failure. */
export function useRecommendationStream(
  profileId: string,
  onDemoLimit: (retryAfterSeconds?: number) => void,
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
      dispatch({ type: "start" });
      try {
        await streamRecommendation(
          { profile_id: profileId, request, ignore_filters: ignoreFilters },
          (event) => {
            if (!current.signal.aborted) dispatch({ type: "event", event });
          },
          current.signal,
        );
      } catch (error) {
        if (current.signal.aborted) return;
        if (error instanceof ApiError && error.isDemoLimit) {
          demoLimit.current(error.retryAfterSeconds);
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        dispatch({ type: "error", kind: "unavailable", message });
      }
    },
    [profileId],
  );

  const cancel = useCallback(() => controller.current?.abort(), []);
  return { progress, start, cancel };
}
