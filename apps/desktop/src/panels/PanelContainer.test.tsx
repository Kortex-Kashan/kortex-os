import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PanelContainer } from "./PanelContainer";

describe("PanelContainer", () => {
  it("renders the title and content", () => {
    render(
      <PanelContainer title="Test Panel">
        <p>Panel body</p>
      </PanelContainer>,
    );

    expect(screen.getByText("Test Panel")).toBeInTheDocument();
    expect(screen.getByText("Panel body")).toBeInTheDocument();
  });

  it("renders no close button when onClose is omitted", () => {
    render(
      <PanelContainer title="Test Panel">
        <p>Panel body</p>
      </PanelContainer>,
    );

    expect(screen.queryByRole("button", { name: "Close Test Panel" })).not.toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(
      <PanelContainer title="Test Panel" onClose={onClose}>
        <p>Panel body</p>
      </PanelContainer>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Close Test Panel" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("collapses and expands content via the collapse toggle", () => {
    render(
      <PanelContainer title="Test Panel">
        <p>Panel body</p>
      </PanelContainer>,
    );

    expect(screen.getByText("Panel body")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse Test Panel" }));
    expect(screen.queryByText("Panel body")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand Test Panel" }));
    expect(screen.getByText("Panel body")).toBeInTheDocument();
  });

  it("starts collapsed when defaultCollapsed is true", () => {
    render(
      <PanelContainer title="Test Panel" defaultCollapsed>
        <p>Panel body</p>
      </PanelContainer>,
    );

    expect(screen.queryByText("Panel body")).not.toBeInTheDocument();
  });

  it("supports a controlled collapsed prop", () => {
    const onCollapsedChange = vi.fn();
    const { rerender } = render(
      <PanelContainer title="Test Panel" collapsed={false} onCollapsedChange={onCollapsedChange}>
        <p>Panel body</p>
      </PanelContainer>,
    );

    expect(screen.getByText("Panel body")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse Test Panel" }));
    expect(onCollapsedChange).toHaveBeenCalledWith(true);
    // Controlled: content stays visible until the parent flips the prop.
    expect(screen.getByText("Panel body")).toBeInTheDocument();

    rerender(
      <PanelContainer title="Test Panel" collapsed={true} onCollapsedChange={onCollapsedChange}>
        <p>Panel body</p>
      </PanelContainer>,
    );
    expect(screen.queryByText("Panel body")).not.toBeInTheDocument();
  });
});
