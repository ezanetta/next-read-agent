import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RESULT, renderApp } from "../../test/api";
import { saveResult } from "./resultStore";

describe("results", () => {
  it("shows three picks with reasons, cited books and the agent's work", () => {
    saveResult("run-1", { profileId: "p", request: "a short novel", result: RESULT });
    renderApp("/p/p/results/run-1");
    expect(screen.getByText("“a short novel”")).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(3);
    const first = screen.getAllByRole("article")[0];
    if (!first) throw new Error("no first pick");
    expect(within(first).getByText("PICK 1")).toBeInTheDocument();
    expect(within(first).getByText("101 pages · English edition")).toBeInTheDocument();
    expect(within(first).getByText("Because you picked")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "Piranesi" })).toHaveAttribute(
      "href",
      "/p/p#book-piranesi",
    );
    // The badge flows inline after the cited title, not on a line of its own.
    expect(within(first).getByText("Picked").parentElement).toBe(
      within(first).getByRole("link", { name: "Piranesi" }).parentElement,
    );
    expect(within(first).getByRole("link", { name: /View on Google Books/ })).toHaveAttribute(
      "href",
      "https://books.example/1",
    );
    expect(screen.getByText("3,696")).toBeInTheDocument();
    expect(screen.getByText("Accepted on the first try, no rejections.")).toBeInTheDocument();
    expect(screen.getByText("quiet novel")).toBeInTheDocument();
  });

  it("explains results that are no longer stored", () => {
    renderApp("/p/p/results/unknown");
    expect(screen.getByRole("heading", { name: "These results are gone" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ask again" })).toHaveAttribute("href", "/p/p/ask");
  });
});
