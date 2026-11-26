import type { RecommendationResult } from "../../api/client";

/** A finished run, kept in sessionStorage so a reload of the results page keeps it (D-045). */
export type StoredResult = {
  profileId: string;
  request: string | null;
  result: RecommendationResult;
};

const key = (runId: string) => `next-read:result:${runId}`;

export function saveResult(runId: string, stored: StoredResult): void {
  try {
    sessionStorage.setItem(key(runId), JSON.stringify(stored));
  } catch {
    // Without storage the results still show now; only a reload loses them.
  }
}

export function loadResult(runId: string): StoredResult | null {
  try {
    const raw = sessionStorage.getItem(key(runId));
    return raw ? (JSON.parse(raw) as StoredResult) : null;
  } catch {
    return null;
  }
}
