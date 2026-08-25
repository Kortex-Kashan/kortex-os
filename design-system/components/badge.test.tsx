import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge } from "./badge";

describe("Badge", () => {
  it("renders its label with the default variant", () => {
    render(<Badge>Active</Badge>);
    expect(screen.getByText("Active")).toHaveClass("bg-primary");
  });

  it("applies the outline variant class", () => {
    render(<Badge variant="outline">Draft</Badge>);
    expect(screen.getByText("Draft")).toHaveClass("text-foreground");
  });
});
