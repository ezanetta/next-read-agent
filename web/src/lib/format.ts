import type { FilterName, Filters } from "../api/client";

const languages = new Intl.DisplayNames(["en"], { type: "language" });

/** "en" -> "English"; unknown codes are returned as they are. */
export function languageName(code: string): string {
  try {
    return languages.of(code) ?? code;
  } catch {
    return code;
  }
}

export type FilterChip = { name: FilterName; label: string };

/** The hard filters as chips, in the order the mockups show them. */
export function filterChips(filters: Filters): FilterChip[] {
  const chips: FilterChip[] = [];
  if (filters.language) chips.push({ name: "language", label: languageName(filters.language) });
  if (filters.min_pages)
    chips.push({ name: "min_pages", label: `At least ${filters.min_pages} pages` });
  if (filters.max_pages)
    chips.push({ name: "max_pages", label: `Up to ${filters.max_pages} pages` });
  return chips;
}

export function seconds(value: number): string {
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} s`;
}

/** "3 h 20 min", "12 min", "45 s": a wait as people say it. */
export function waitTime(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.round((totalSeconds % 3600) / 60);
  if (hours > 0) return minutes > 0 ? `${hours} h ${minutes} min` : `${hours} h`;
  if (minutes > 0) return `${minutes} min`;
  return `${Math.max(1, Math.round(totalSeconds))} s`;
}

// Goodreads appends the series to the title: "Guards! Guards! (Discworld #8, City Watch #1)".
// Only a trailing parenthesis with a "#number" counts, so "(Spanish Edition)" stays.
const SERIES = /^(.*\S)\s*\(([^()]*#\s*\d[^()]*)\)$/;

/** A title without its series note, and the series (null when there is none). */
export function splitSeries(title: string): { title: string; series: string | null } {
  const match = SERIES.exec(title.trim());
  if (!match?.[1] || !match[2]) return { title, series: null };
  return { title: match[1], series: match[2].trim() };
}
