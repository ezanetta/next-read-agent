import { Link } from "react-router";
import { HOW_IT_WORKS_URL } from "../lib/links";

export type Step = 1 | 2 | 3;

const STEPS: { step: Step; label: string }[] = [
  { step: 1, label: "Your books" },
  { step: 2, label: "Ask" },
  { step: 3, label: "Results" },
];

/** The top bar: logo, the three steps (desktop) or "Step n of 3" (mobile), and the
 * "How it works" link. `step` is omitted on screens outside the flow (demo limit). */
export function Header({ step }: { step?: Step }) {
  return (
    <header className="border-b border-border">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:h-20 sm:px-8">
        <Link to="/" className="flex items-center gap-3 rounded-md" aria-label="Next Read, home">
          <span
            aria-hidden="true"
            className="grid size-8 place-items-center rounded-md bg-accent font-display font-bold text-on-accent sm:size-9"
          >
            N
          </span>
          <span className="font-display text-xl font-bold sm:text-2xl">Next Read</span>
        </Link>
        {step !== undefined && (
          <>
            <ol className="hidden items-center gap-2 md:flex" aria-label="Progress">
              {STEPS.map(({ step: s, label }) => (
                <li
                  key={s}
                  aria-current={s === step ? "step" : undefined}
                  className={
                    s === step
                      ? "rounded-full bg-accent px-4 py-2 text-sm font-semibold text-on-accent"
                      : "rounded-full border border-border px-4 py-2 text-sm text-muted"
                  }
                >
                  {s} · {label}
                </li>
              ))}
            </ol>
            <p className="text-sm text-muted md:hidden">Step {step} of 3</p>
          </>
        )}
        <a
          href={HOW_IT_WORKS_URL}
          className={`text-sm font-medium text-accent underline underline-offset-4 ${step !== undefined ? "hidden md:block" : ""}`}
        >
          How it works
        </a>
      </div>
    </header>
  );
}
