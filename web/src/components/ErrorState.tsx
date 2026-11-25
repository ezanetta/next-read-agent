import type { ReactNode } from "react";
import { Link } from "react-router";
import { ui } from "./ui";

/** A clear, recoverable error: what happened, and what to do next. */
export function ErrorState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div role="alert" className={`${ui.panel} mx-auto max-w-2xl p-8`}>
      <h1 className="text-2xl font-bold">{title}</h1>
      <div className="mt-3 text-muted">{children}</div>
      <div className="mt-6 flex flex-wrap gap-3">
        {action ?? (
          <Link to="/" className={ui.secondary}>
            Choose a library
          </Link>
        )}
      </div>
    </div>
  );
}
