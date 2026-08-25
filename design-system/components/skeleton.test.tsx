import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Skeleton } from "./skeleton";

describe("Skeleton", () => {
  it("renders a pulsing placeholder block", () => {
    render(<Skeleton data-testid="skeleton" className="h-4 w-32" />);
    const skeleton = screen.getByTestId("skeleton");

    expect(skeleton).toHaveClass("animate-pulse");
    expect(skeleton).toHaveClass("h-4", "w-32");
  });
});
