import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Spinner } from "./spinner";

describe("Spinner", () => {
  it("renders as an accessible loading status", () => {
    render(<Spinner />);
    const spinner = screen.getByRole("status", { name: "Loading" });

    expect(spinner).toHaveClass("animate-spin");
  });

  it("respects a custom size", () => {
    render(<Spinner size={32} />);
    const spinner = screen.getByRole("status", { name: "Loading" });

    expect(spinner).toHaveAttribute("width", "32");
    expect(spinner).toHaveAttribute("height", "32");
  });
});
