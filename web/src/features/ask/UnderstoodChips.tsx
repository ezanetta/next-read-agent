import type { FilterName } from "../../api/client";
import { filterChips } from "../../lib/format";
import type { Progress } from "./progress";

/** What the agent understood: the wish, and the hard filters as removable chips. Removing
 * one reruns the request without it (D-045). */
export function UnderstoodChips({
  understood,
  onRemoveFilter,
}: {
  understood: NonNullable<Progress["understood"]>;
  onRemoveFilter: (name: FilterName) => void;
}) {
  const chips = filterChips(understood.filters);
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted">Understood:</span>
      {understood.preference && (
        <span className="rounded-full bg-surface-2 px-3 py-1.5">
          Wish · {understood.preference}
        </span>
      )}
      {chips.length === 0 ? (
        <span className="rounded-full border border-dashed border-border-strong px-3 py-1.5 text-muted">
          No language or page filters
        </span>
      ) : (
        chips.map((chip) => (
          <span
            key={chip.name}
            className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 py-1 pr-1 pl-3 font-semibold text-accent"
          >
            {chip.label}
            <button
              type="button"
              onClick={() => onRemoveFilter(chip.name)}
              aria-label={`Remove the filter ${chip.label} and ask again`}
              className="grid size-6 place-items-center rounded-full hover:bg-accent/20"
            >
              ×
            </button>
          </span>
        ))
      )}
    </div>
  );
}
