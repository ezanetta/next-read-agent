import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";
import { routes } from "./App";
import { HOW_IT_WORKS_URL } from "./lib/links";

describe("app shell", () => {
  it("renders the header with the steps and the how-it-works link", () => {
    const router = createMemoryRouter(routes, { initialEntries: ["/"] });
    render(<RouterProvider router={router} />);
    expect(screen.getByRole("link", { name: "Next Read, home" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "How it works" })).toHaveAttribute(
      "href",
      HOW_IT_WORKS_URL,
    );
  });
});
