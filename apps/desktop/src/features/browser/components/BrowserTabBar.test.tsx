import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BrowserTab } from "../hooks/useBrowserTabs";
import { BrowserTabBar } from "./BrowserTabBar";

function makeTab(overrides: Partial<BrowserTab> = {}): BrowserTab {
  return {
    id: "browser-surface-1",
    state: { surfaceId: "browser-surface-1", url: "https://example.com/", loading: false, canGoBack: false, canGoForward: false },
    ...overrides,
  };
}

describe("BrowserTabBar", () => {
  it("renders one tab per real BrowserSurfaceId it was given, never a placeholder", () => {
    const tabs = [makeTab({ id: "a" }), makeTab({ id: "b", state: { surfaceId: "b", url: "https://other.example/", loading: false, canGoBack: false, canGoForward: false } })];
    render(<BrowserTabBar tabs={tabs} activeTabId="a" disabled={false} onSwitch={vi.fn()} onClose={vi.fn()} onNewTab={vi.fn()} />);

    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.getByText("example.com")).toBeInTheDocument();
    expect(screen.getByText("other.example")).toBeInTheDocument();
  });

  it("marks the active tab as selected", () => {
    const tabs = [makeTab({ id: "a" }), makeTab({ id: "b" })];
    render(<BrowserTabBar tabs={tabs} activeTabId="b" disabled={false} onSwitch={vi.fn()} onClose={vi.fn()} onNewTab={vi.fn()} />);

    const [tabA, tabB] = screen.getAllByRole("tab");
    expect(tabA).toHaveAttribute("aria-selected", "false");
    expect(tabB).toHaveAttribute("aria-selected", "true");
  });

  it("shows a new-tab placeholder label before a surface's first state is known", () => {
    render(
      <BrowserTabBar
        tabs={[{ id: "a", state: null }]}
        activeTabId="a"
        disabled={false}
        onSwitch={vi.fn()}
        onClose={vi.fn()}
        onNewTab={vi.fn()}
      />,
    );
    expect(screen.getByText("New Tab")).toBeInTheDocument();
  });

  it("calls onSwitch with the clicked tab's real surface id", () => {
    const onSwitch = vi.fn();
    const tabs = [
      makeTab({ id: "a" }),
      makeTab({ id: "b", state: { surfaceId: "b", url: "https://other.example/", loading: false, canGoBack: false, canGoForward: false } }),
    ];
    render(<BrowserTabBar tabs={tabs} activeTabId="a" disabled={false} onSwitch={onSwitch} onClose={vi.fn()} onNewTab={vi.fn()} />);

    fireEvent.click(screen.getByText("other.example"));
    expect(onSwitch).toHaveBeenCalledWith("b");
  });

  it("calls onClose with the closed tab's id, not onSwitch", () => {
    const onSwitch = vi.fn();
    const onClose = vi.fn();
    render(
      <BrowserTabBar tabs={[makeTab({ id: "a" })]} activeTabId="a" disabled={false} onSwitch={onSwitch} onClose={onClose} onNewTab={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledWith("a");
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("calls onNewTab when the + button is clicked", () => {
    const onNewTab = vi.fn();
    render(<BrowserTabBar tabs={[]} activeTabId={null} disabled={false} onSwitch={vi.fn()} onClose={vi.fn()} onNewTab={onNewTab} />);

    fireEvent.click(screen.getByRole("button", { name: "New tab" }));
    expect(onNewTab).toHaveBeenCalledTimes(1);
  });

  it("disables tab and new-tab interaction while busy", () => {
    render(
      <BrowserTabBar tabs={[makeTab({ id: "a" })]} activeTabId="a" disabled onSwitch={vi.fn()} onClose={vi.fn()} onNewTab={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "New tab" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /close/i })).toBeDisabled();
  });
});
