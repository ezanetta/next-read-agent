import { ui } from "../../components/ui";
import type { FailureKind } from "./progress";

const COPY: Record<FailureKind, { title: string; body: string }> = {
  interpretation: {
    title: "That request didn't come through",
    body: "The agent couldn't read your request, often because the model was busy. Try again, or say it another way.",
  },
  unavailable: {
    title: "The book services are busy",
    body: "The model or the book catalog didn't answer in time. Nothing is wrong with your request: try again in a minute.",
  },
  agent: {
    title: "No three books passed every check",
    body: "The agent ran out of turns before three picks passed the checks. Try again, or loosen the request.",
  },
  demo_limit: { title: "Demo limit reached", body: "Today's recommendations are taken." },
  connection: {
    title: "The connection was lost",
    body: "The connection to the agent closed before it finished, maybe because the server restarted. Try again.",
  },
  timeout: {
    title: "This is taking too long",
    body: "The agent didn't finish in time, so we stopped waiting. Try again in a minute.",
  },
};

export function FailurePanel({
  kind,
  message,
  onRetry,
}: {
  kind: FailureKind;
  message: string;
  onRetry: () => void;
}) {
  const copy = COPY[kind];
  return (
    <section role="alert" className={`${ui.panel} p-6 sm:p-8`}>
      <h2 className="text-xl font-bold">{copy.title}</h2>
      <p className="mt-2 text-muted">{copy.body}</p>
      <div className="mt-5 flex flex-wrap items-center gap-4">
        <button type="button" onClick={onRetry} className={ui.primary}>
          Try again
        </button>
      </div>
      <details className="mt-5 text-sm text-muted">
        <summary className="cursor-pointer">Technical details</summary>
        <p className="mt-2 break-words">{message}</p>
      </details>
    </section>
  );
}
