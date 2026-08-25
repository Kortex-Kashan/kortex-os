import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ComponentGallery } from "./ComponentGallery";

describe("ComponentGallery", () => {
  it("renders every design-system section without crashing", () => {
    render(<ComponentGallery />);

    expect(screen.getByText("Design System Gallery")).toBeInTheDocument();
    for (const title of [
      "Buttons",
      "Cards",
      "Inputs",
      "Badges",
      "Dialog",
      "Dropdown Menu",
      "Tooltip",
      "Select",
      "Table",
      "Navigation Menu",
      "Command Palette",
      "Toast",
      "Loading States",
    ]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
  });
});
