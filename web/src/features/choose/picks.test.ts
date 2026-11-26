import { describe, expect, it } from "vitest";
import type { PickedBook } from "../../api/client";
import { addPick, canContinue, MAX_PICKS, MIN_PICKS, removePick } from "./picks";

const book = (i: number): PickedBook => ({ source_id: `id${i}`, title: `Book ${i}`, authors: [] });
const books = (n: number) => Array.from({ length: n }, (_, i) => book(i));

describe("the pick-5 rule", () => {
  it("allows continuing from 5 to 50 books", () => {
    expect(canContinue(books(MIN_PICKS - 1))).toBe(false);
    expect(canContinue(books(MIN_PICKS))).toBe(true);
    expect(canContinue(books(MAX_PICKS))).toBe(true);
  });

  it("adds each book once and stops at 50", () => {
    expect(addPick([book(1)], book(1))).toHaveLength(1);
    expect(addPick(books(MAX_PICKS), book(99))).toHaveLength(MAX_PICKS);
    expect(removePick(books(3), "id1").map((b) => b.source_id)).toEqual(["id0", "id2"]);
  });
});
