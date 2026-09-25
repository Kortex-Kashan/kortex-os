import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BrowserToolbar, normalizeAddress } from "./BrowserToolbar";

describe("normalizeAddress", () => {
  it("accepts a well-formed https URL unchanged", () => {
    expect(normalizeAddress("https://example.com/path")).toBe("https://example.com/path");
  });

  it("accepts a well-formed http URL unchanged", () => {
    expect(normalizeAddress("http://example.com")).toBe("http://example.com/");
  });

  it("prepends https:// to a bare host", () => {
    expect(normalizeAddress("example.com")).toBe("https://example.com/");
  });

  it("rejects an empty or whitespace-only input", () => {
    expect(normalizeAddress("")).toBeNull();
    expect(normalizeAddress("   ")).toBeNull();
  });

  it("rejects a non-http(s) scheme rather than silently navigating to it", () => {
    // A typed address must never resolve to a scheme this app treats
    // specially elsewhere (custom deep-link schemes, tauri:// asset URLs,
    // etc.) — only ever real web content.
    expect(normalizeAddress("kortex-auth://callback")).toBeNull();
    expect(normalizeAddress("tauri://localhost")).toBeNull();
    expect(normalizeAddress("javascript:alert(1)")).toBeNull();
    expect(normalizeAddress("file:///etc/passwd")).toBeNull();
  });

  it("rejects text that cannot be parsed as a URL at all, rather than guessing (no search fallback)", () => {
    expect(normalizeAddress("not a url just text")).toBeNull();
  });
});

describe("BrowserToolbar", () => {
  function renderToolbar(overrides: Partial<React.ComponentProps<typeof BrowserToolbar>> = {}) {
    const onNavigate = vi.fn();
    const onBack = vi.fn();
    const onForward = vi.fn();
    const onReload = vi.fn();
    render(
      <BrowserToolbar
        url="https://example.com/"
        loading={false}
        canGoBack={false}
        canGoForward={false}
        disabled={false}
        onNavigate={onNavigate}
        onBack={onBack}
        onForward={onForward}
        onReload={onReload}
        {...overrides}
      />,
    );
    return { onNavigate, onBack, onForward, onReload };
  }

  it("disables Back/Forward when the active tab cannot go back/forward", () => {
    renderToolbar({ canGoBack: false, canGoForward: false });
    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Forward" })).toBeDisabled();
  });

  it("enables Back/Forward when the active tab reports it can", () => {
    renderToolbar({ canGoBack: true, canGoForward: true });
    expect(screen.getByRole("button", { name: "Back" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Forward" })).toBeEnabled();
  });

  it("invokes onBack/onForward/onReload when clicked", () => {
    const { onBack, onForward, onReload } = renderToolbar({ canGoBack: true, canGoForward: true });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(screen.getByRole("button", { name: "Forward" }));
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(onForward).toHaveBeenCalledTimes(1);
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("navigates to a normalized address on Enter", () => {
    const { onNavigate } = renderToolbar();
    const input = screen.getByTestId("browser-address-input");
    fireEvent.change(input, { target: { value: "example.org" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onNavigate).toHaveBeenCalledWith("https://example.org/");
  });

  it("navigates when Go is clicked", () => {
    const { onNavigate } = renderToolbar();
    fireEvent.change(screen.getByTestId("browser-address-input"), { target: { value: "https://example.net" } });
    fireEvent.click(screen.getByTestId("browser-navigate-button"));
    expect(onNavigate).toHaveBeenCalledWith("https://example.net/");
  });

  it("marks the address field invalid and does not navigate on an unparseable address", () => {
    const { onNavigate } = renderToolbar();
    const input = screen.getByTestId("browser-address-input");
    fireEvent.change(input, { target: { value: "javascript:alert(1)" } });
    fireEvent.click(screen.getByTestId("browser-navigate-button"));
    expect(onNavigate).not.toHaveBeenCalled();
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("reflects the active tab's real URL in the address field", () => {
    renderToolbar({ url: "https://kortex.example/dashboard" });
    expect(screen.getByTestId("browser-address-input")).toHaveValue("https://kortex.example/dashboard");
  });

  it("shows a loading indicator distinct from the static reload icon while loading", () => {
    const { container } = render(
      <BrowserToolbar
        url=""
        loading
        canGoBack={false}
        canGoForward={false}
        disabled={false}
        onNavigate={vi.fn()}
        onBack={vi.fn()}
        onForward={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
  });
});
