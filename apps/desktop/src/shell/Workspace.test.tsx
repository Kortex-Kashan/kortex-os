import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Workspace, WorkspaceEmptyState } from "./Workspace";

describe("Workspace", () => {
  it("renders whatever is mounted at its route outlet", () => {
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <Workspace />,
          children: [{ index: true, element: <WorkspaceEmptyState /> }],
        },
      ],
      { initialEntries: ["/"] },
    );
    render(<RouterProvider router={router} />);

    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });
});
