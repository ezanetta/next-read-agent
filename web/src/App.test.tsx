import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HOW_IT_WORKS_URL } from "./lib/links";
import { json, mockApi, PRESETS, renderApp } from "./test/api";

describe("app shell", () => {
  it("renders the header with the logo and the how-it-works link", () => {
    mockApi({ "GET /profiles/presets": () => json(PRESETS) });
    renderApp("/");
    expect(screen.getByRole("link", { name: "Next Read, home" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "How it works" })).toHaveAttribute(
      "href",
      HOW_IT_WORKS_URL,
    );
    expect(screen.getByText("Step 1 of 3")).toBeInTheDocument();
  });
});
