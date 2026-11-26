import { useState } from "react";
import type { TraceSummary } from "../../api/client";
import { seconds } from "../../lib/format";

function stepLine(step: TraceSummary["steps"][number]): { title: string; detail: string } {
  const searches = step.searches.length;
  if (step.submitted.length > 0) {
    const accepted = step.outcome === "accepted";
    return {
      title: `Turn ${step.iteration} · submitted ${step.submitted.length} books`,
      detail: accepted
        ? step.submitted.join(", ")
        : `${step.outcome}: ${step.submitted.join(", ")}`,
    };
  }
  if (searches > 0) {
    return {
      title: `Turn ${step.iteration} · searched ${searches} ${searches === 1 ? "book" : "books at once"}`,
      detail: step.searches.map((s) => s.query).join(", "),
    };
  }
  return { title: `Turn ${step.iteration}`, detail: step.outcome };
}

function wideScreen(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(min-width: 768px)").matches;
}

/** "What the agent did": open by default on wide screens, collapsed on phones. */
export function AgentDidPanel({ trace }: { trace: TraceSummary }) {
  const [open, setOpen] = useState(wideScreen);
  const searches = trace.steps.reduce((n, s) => n + s.searches.length, 0);
  const stats: [string, string][] = [
    [String(trace.iterations), trace.iterations === 1 ? "turn" : "turns"],
    [String(searches), searches === 1 ? "search" : "searches"],
    [String(trace.rejections.length), trace.rejections.length === 1 ? "rejection" : "rejections"],
    [String(trace.llm_calls), "model calls"],
    [(trace.input_tokens + trace.output_tokens).toLocaleString("en-US"), "tokens"],
    [seconds(trace.duration_s), "total time"],
  ];
  const firstTry = trace.rejections.length === 0;

  return (
    <details
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className="rounded-2xl border border-border bg-surface p-6 sm:p-8"
    >
      <summary className="cursor-pointer font-display text-xl font-bold">
        What the agent did
        <span className="font-sans font-normal text-muted"> · {seconds(trace.duration_s)}</span>
      </summary>
      <dl className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {stats.map(([value, label]) => (
          <div key={label} className="rounded-xl bg-bg p-4">
            <dd className="font-display text-2xl font-bold">{value}</dd>
            <dt className="text-sm text-muted">{label}</dt>
          </div>
        ))}
      </dl>
      <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.7fr)]">
        <section aria-labelledby="steps-heading">
          <h3 id="steps-heading" className="font-sans font-semibold text-muted">
            Steps
          </h3>
          <ol className="mt-3 list-decimal space-y-3 pl-5">
            {trace.steps.map((step) => {
              const line = stepLine(step);
              return (
                <li key={step.iteration} className="min-w-0">
                  <p className="font-semibold wrap-break-word">{line.title}</p>
                  <p className="text-muted">{line.detail}</p>
                </li>
              );
            })}
          </ol>
          <p className="mt-3 text-muted">
            {firstTry
              ? "Accepted on the first try, no rejections."
              : `${trace.rejections.length} submission${trace.rejections.length === 1 ? " was" : "s were"} rejected and fixed before the final picks.`}
          </p>
        </section>
        <section aria-labelledby="checked-heading">
          <h3 id="checked-heading" className="font-sans font-semibold text-muted">
            Checked by code, not by the model
          </h3>
          <ul className="mt-3 space-y-2">
            <li>✓ Each book exists in Google Books</li>
            <li>✓ None is in your library, in any edition or translation</li>
            <li>✓ It fits your filters</li>
            <li>✓ Each reason cites a book you've read</li>
          </ul>
        </section>
      </div>
    </details>
  );
}
