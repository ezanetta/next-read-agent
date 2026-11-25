/** Shared class names, so buttons and panels look the same on every screen. */
export const ui = {
  primary:
    "inline-flex items-center justify-center rounded-xl bg-accent px-6 py-3 font-semibold text-on-accent transition hover:brightness-110 disabled:cursor-not-allowed disabled:bg-border-strong disabled:text-muted disabled:hover:brightness-100",
  secondary:
    "inline-flex items-center justify-center rounded-xl border border-border-strong bg-surface-2 px-5 py-3 font-semibold text-text transition hover:bg-border",
  ghost:
    "inline-flex items-center justify-center rounded-xl border border-border px-5 py-3 font-semibold text-text transition hover:bg-surface-2",
  panel: "rounded-2xl border border-border bg-surface",
  link: "font-medium text-accent underline underline-offset-4 hover:brightness-110",
  eyebrow: "text-sm font-semibold tracking-widest text-muted uppercase",
} as const;
