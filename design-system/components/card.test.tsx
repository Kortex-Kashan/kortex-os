import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./card";

describe("Card", () => {
  it("renders header, title, description, and content together", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Team members</CardTitle>
          <CardDescription>Everyone with access to this workspace.</CardDescription>
        </CardHeader>
        <CardContent>3 members</CardContent>
      </Card>,
    );

    expect(screen.getByText("Team members")).toBeInTheDocument();
    expect(screen.getByText("Everyone with access to this workspace.")).toBeInTheDocument();
    expect(screen.getByText("3 members")).toBeInTheDocument();
  });
});
