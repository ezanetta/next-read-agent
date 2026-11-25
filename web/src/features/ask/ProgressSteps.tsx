import type { Schemas } from "../../api/client";
import { seconds } from "../../lib/format";
import { type Progress, type StepStatus, steps } from "./progress";

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "done") {
    return (
      <span
        aria-hidden="true"
        className="grid size-8 shrink-0 place-items-center rounded-full bg-success text-text"
      >
        ✓
      </span>
    );
  }
  if (status === "active") {
    return (
      <span
        aria-hidden="true"
        className="grid size-8 shrink-0 place-items-center rounded-full border-2 border-accent"
      >
        <span className="size-3 animate-pulse rounded-full bg-accent" />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span
        aria-hidden="true"
        className="grid size-8 shrink-0 place-items-center rounded-full border-2 border-border-strong text-muted"
      >
        !
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      className="size-8 shrink-0 rounded-full border-2 border-border-strong"
    />
  );
}

const STATUS_LABEL: Record<StepStatus, string> = {
  done: "done",
  active: "in progress",
  pending: "not started",
  failed: "stopped",
};

function SearchList({ searches }: { searches: Schemas["SearchStep"][] }) {
  return (
    <ul className="mt-3 space-y-2">
      {searches.map((search, i) => (
        <li
          // Searches have no id; the same query can repeat in a later turn.
          // biome-ignore lint/suspicious/noArrayIndexKey: order is stable and append-only
          key={i}
          className="flex flex-col gap-1 rounded-lg bg-bg px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
        >
          <code className="font-mono text-sm break-words text-text">{search.query}</code>
          <span className="shrink-0 text-sm text-muted">
            {search.found === 0
              ? "no books found"
              : `${search.found} book${search.found === 1 ? "" : "s"} found`}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ProgressSteps({ progress, elapsed }: { progress: Progress; elapsed: number }) {
  const list = steps(progress);
  const running = progress.status === "running";
  return (
    <section
      aria-labelledby="progress-heading"
      className="rounded-2xl border border-border bg-surface p-5 sm:p-9"
    >
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="progress-heading" className="text-xl font-bold sm:text-2xl">
          {running ? "The agent is working" : progress.status === "done" ? "Done" : "Stopped"}
        </h2>
        <p className="text-sm text-muted">
          {seconds(elapsed)}
          <span className="hidden sm:inline"> · usually 10 to 40 s</span>
        </p>
      </div>
      <ol className="mt-6 space-y-6" aria-live="polite">
        {list.map((step) => (
          <li key={step.id} className="flex gap-4">
            <StepIcon status={step.status} />
            <div className="min-w-0 flex-1">
              <p
                className={`font-semibold ${step.status === "active" ? "text-accent" : step.status === "pending" ? "text-muted" : "text-text"}`}
              >
                {step.title}
                <span className="sr-only">, {STATUS_LABEL[step.status]}</span>
              </p>
              {step.detail && <p className="mt-1 text-muted">{step.detail}</p>}
              {step.id === "searched" && progress.searches.length > 0 && (
                <SearchList searches={progress.searches} />
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
