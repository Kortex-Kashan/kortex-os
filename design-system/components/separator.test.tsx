import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Separator } from "./separator";

describe("Separator", () => {
  it("renders as a decorative horizontal divider by default", () => {
    render(<Separator data-testid="sep" />);
    const separator = screen.getByTestId("sep");

    expect(separator).toHaveAttribute("data-orientation", "horizontal");
    expect(separator).toHaveAttribute("role", "none");
  });

  it("supports a vertical, non-decorative orientation", () => {
    render(<Separator data-testid="sep" orientation="vertical" decorative={false} />);
    const separator = screen.getByTestId("sep");

    expect(separator).toHaveAttribute("data-orientation", "vertical");
    expect(separator).toHaveAttribute("role", "separator");
  });
});
