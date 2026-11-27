import type {
  Filters,
  ProblemView,
  RecommendationEvent,
  RecommendationResult,
  Schemas,
} from "../../api/client";

type SearchStep = Schemas["SearchStep"];
/** The server's failure kinds, plus two the client detects itself (R-004): the stream
 * ended without a final event, or no final event came within the client's timeout. */
export type FailureKind = Schemas["FailedEvent"]["kind"] | "connection" | "timeout";

export type Submission = { titles: string[]; problems: ProblemView[] | null };

/** What the Ask screen shows, built only from the stream's events (D-043). */
export type Progress = {
  status: "idle" | "running" | "done" | "failed";
  runId: string | null;
  understood: {
    preference: string | null;
    filters: Filters;
    sources: Record<string, string>;
  } | null;
  searches: SearchStep[];
  submissions: Submission[];
  checking: boolean;
  result: RecommendationResult | null;
  failure: { kind: FailureKind; message: string } | null;
};

export const initialProgress: Progress = {
  status: "idle",
  runId: null,
  understood: null,
  searches: [],
  submissions: [],
  checking: false,
  result: null,
  failure: null,
};

export type ProgressAction =
  | { type: "start" }
  | { type: "event"; event: RecommendationEvent }
  | { type: "error"; kind: FailureKind; message: string };

export function progressReducer(state: Progress, action: ProgressAction): Progress {
  if (action.type === "start") return { ...initialProgress, status: "running" };
  if (action.type === "error") {
    return { ...state, status: "failed", checking: false, failure: action };
  }
  const event = action.event;
  switch (event.type) {
    case "started":
      return { ...state, status: "running", runId: event.run_id };
    case "interpreted":
      return {
        ...state,
        understood: {
          preference: event.preference,
          filters: event.filters,
          sources: event.sources,
        },
      };
    case "searching":
      return { ...state, searches: [...state.searches, ...event.searches] };
    case "submitted":
      return {
        ...state,
        submissions: [...state.submissions, { titles: event.titles, problems: null }],
      };
    case "checking":
      return { ...state, checking: true };
    case "rejected": {
      const submissions = [...state.submissions];
      const last = submissions.at(-1);
      if (last) submissions[submissions.length - 1] = { ...last, problems: event.problems };
      return { ...state, submissions, checking: false };
    }
    case "done":
      return { ...state, status: "done", checking: false, result: event.result };
    case "failed":
      return {
        ...state,
        status: "failed",
        checking: false,
        failure: { kind: event.kind, message: event.message },
      };
  }
}

export type StepStatus = "done" | "active" | "pending" | "failed";
export type Step = { id: string; title: string; detail?: string; status: StepStatus };

/** The steps the screen lists, from the progress so far. */
export function steps(progress: Progress): Step[] {
  const { understood, searches, submissions, checking, status } = progress;
  const last = submissions.at(-1);
  const failed = status === "failed";
  const list: Step[] = [
    {
      id: "understood",
      title: understood ? "Understood your request" : "Understanding your request",
      detail: understood ? understoodDetail(understood.filters) : undefined,
      status: understood ? "done" : failed ? "failed" : "active",
    },
  ];
  if (!understood) return list;
  list.push({
    id: "searched",
    title: searches.length > 0 ? "Searched Google Books" : "Searching Google Books",
    status: last ? "done" : failed ? "failed" : "active",
  });
  submissions.forEach((submission, index) => {
    const replaced = submission.problems?.length ?? 0;
    list.push({
      id: `picked-${index}`,
      title: index === 0 ? `Picked ${submission.titles.length} books` : `Picked replacements`,
      detail: submission.titles.join(" · "),
      status: "done",
    });
    if (submission.problems) {
      list.push({
        id: `rejected-${index}`,
        title: `${replaced} pick${replaced === 1 ? "" : "s"} failed a check: replacing`,
        detail: submission.problems.map((p) => p.message).join(" · "),
        status: "done",
      });
    }
  });
  if (last) {
    list.push({
      id: "checking",
      title: "Checking every pick",
      detail:
        "It exists · you haven't read it, in any language · it fits your filters · the reason cites your books",
      status: checking ? "active" : status === "done" ? "done" : failed ? "failed" : "pending",
    });
  }
  list.push({
    id: "results",
    title: "Results",
    detail: "If a pick fails a check, the agent replaces it and tries again",
    status: status === "done" ? "done" : "pending",
  });
  return list;
}

function understoodDetail(filters: Filters): string {
  const hard = [filters.language, filters.max_pages, filters.min_pages].some((f) => f != null);
  return hard
    ? "A wish to guide the picks, and hard filters checked in code"
    : "A wish to guide the picks; no hard filters";
}
