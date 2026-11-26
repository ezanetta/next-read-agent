import { describe, expect, it } from "vitest";
import { splitSeries } from "./format";

describe("splitSeries", () => {
  it("splits one or more series off a Goodreads title", () => {
    expect(splitSeries("Guards! Guards! (Discworld #8, City Watch #1)")).toEqual({
      title: "Guards! Guards!",
      series: "Discworld #8, City Watch #1",
    });
    expect(splitSeries("Dune (Dune #1)")).toEqual({ title: "Dune", series: "Dune #1" });
    expect(splitSeries("Mort (Discworld, #4; Death, #1)")).toEqual({
      title: "Mort",
      series: "Discworld, #4; Death, #1",
    });
    expect(splitSeries("Novella (Series #0.5)")).toEqual({
      title: "Novella",
      series: "Series #0.5",
    });
  });

  it("keeps parentheses that aren't a series", () => {
    for (const title of [
      "Cien años de soledad (Spanish Edition)",
      "The Stand (Complete and Uncut)",
      "1984",
      "(#1)",
      "Middle (Series #2) of a title",
    ]) {
      expect(splitSeries(title)).toEqual({ title, series: null });
    }
  });
});
