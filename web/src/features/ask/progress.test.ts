import { describe, expect, it } from "vitest";
import type { RecommendationEvent } from "../../api/client";
import { initialProgress, type Progress, progressReducer, steps } from "./progress";

const run = (events: object[]): Progress =>
  events.reduce<Progress>(
    (state, event) =>
      progressReducer(state, { type: "event", event: event as RecommendationEvent }),
    progressReducer(initialProgress, { type: "start" }),
  );

const interpreted = {
  type: "interpreted",
  preference: "short",
  sources: {},
  filters: { language: null, max_pages: null, min_pages: null },
};

describe("progress from the stream's events", () => {
  it("builds the steps in order, with a rejection and its replacement", () => {
    const state = run([
      { type: "started", run_id: "r1", prompt_id: "agent_v3" },
      interpreted,
      {
        type: "searching",
        iteration: 1,
        searches: [{ query: "q", outcome: "5 books: A", found: 5 }],
      },
      { type: "submitted", iteration: 2, titles: ["A", "B", "C"] },
      { type: "checking", iteration: 2 },
      {
        type: "rejected",
        iteration: 2,
        problems: [{ book: 1, rule: "already_read", message: "already read", unknown: false }],
      },
      { type: "submitted", iteration: 3, titles: ["D", "B", "C"] },
      { type: "checking", iteration: 3 },
    ]);
    expect(state.runId).toBe("r1");
    expect(steps(state).map((s) => [s.id, s.status])).toEqual([
      ["understood", "done"],
      ["searched", "done"],
      ["picked-0", "done"],
      ["rejected-0", "done"],
      ["picked-1", "done"],
      ["checking", "active"],
      ["results", "pending"],
    ]);
    expect(steps(state)[3]?.title).toBe("1 pick failed a check: replacing");
  });

  it("ends done or failed", () => {
    const done = run([interpreted, { type: "done", result: { books: [] } }]);
    expect(done.status).toBe("done");
    const failed = run([interpreted, { type: "failed", kind: "agent", message: "out of turns" }]);
    expect(failed.failure).toEqual({ kind: "agent", message: "out of turns" });
    expect(steps(failed).find((s) => s.id === "searched")?.status).toBe("failed");
  });
});
