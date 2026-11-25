import type { ReactNode } from "react";
import { Header, type Step } from "./Header";

export function Layout({ step, children }: { step?: Step; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <Header step={step} />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-8 sm:py-14">{children}</main>
      <footer className="border-t border-border">
        <p className="mx-auto max-w-6xl px-4 py-6 text-sm text-muted sm:px-8">
          Demo with a limited number of runs per day.
        </p>
      </footer>
    </div>
  );
}
