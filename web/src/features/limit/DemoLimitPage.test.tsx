import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HOW_IT_WORKS_URL } from "../../lib/links";
import { renderApp } from "../../test/api";

describe("demo limit", () => {
  it("shows the retry time from the API, and no video button until one is set", () => {
    renderApp("/limit?retry=12000");
    expect(
      screen.getByRole("heading", { name: "The shelves are closed for today." }),
    ).toBeInTheDocument();
    expect(screen.getByText("Try again in 3 h 20 min")).toBeInTheDocument();
    expect(screen.queryByText("Watch a 2-minute demo")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read how it works" })).toHaveAttribute(
      "href",
      HOW_IT_WORKS_URL,
    );
    expect(screen.queryByText("Step 1 of 3")).not.toBeInTheDocument();
  });

  it("works without a retry time", () => {
    renderApp("/limit");
    expect(screen.queryByText(/Try again in/)).not.toBeInTheDocument();
  });

  it("unknown URLs get a clear page", () => {
    renderApp("/nowhere");
    expect(screen.getByRole("heading", { name: "This page doesn't exist" })).toBeInTheDocument();
  });
});
