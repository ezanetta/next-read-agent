import { useEffect, useState } from "react";
import type { PickedBook } from "../../api/client";

export const MIN_PICKS = 5;
export const MAX_PICKS = 50;
const DRAFT_KEY = "next-read:picks";

/** The pick-5 rule: continue from 5 to 50 different books. */
export function canContinue(picks: readonly PickedBook[]): boolean {
  return picks.length >= MIN_PICKS && picks.length <= MAX_PICKS;
}

export function addPick(picks: readonly PickedBook[], book: PickedBook): PickedBook[] {
  if (picks.some((p) => p.source_id === book.source_id) || picks.length >= MAX_PICKS) {
    return [...picks];
  }
  return [...picks, book];
}

export function removePick(picks: readonly PickedBook[], sourceId: string): PickedBook[] {
  return picks.filter((p) => p.source_id !== sourceId);
}

function loadDraft(): PickedBook[] {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    return raw ? (JSON.parse(raw) as PickedBook[]) : [];
  } catch {
    return [];
  }
}

/** The picked books, kept in sessionStorage so a reload doesn't lose them. `initial`
 * (from "Edit list") replaces the draft. */
export function usePicks(initial?: PickedBook[]) {
  const [picks, setPicks] = useState<PickedBook[]>(() => initial ?? loadDraft());
  useEffect(() => {
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(picks));
    } catch {
      // Storage can be unavailable (private mode): the picks still work in memory.
    }
  }, [picks]);
  return {
    picks,
    add: (book: PickedBook) => setPicks((current) => addPick(current, book)),
    remove: (sourceId: string) => setPicks((current) => removePick(current, sourceId)),
    clear: () => setPicks([]),
    canContinue: canContinue(picks),
  };
}
