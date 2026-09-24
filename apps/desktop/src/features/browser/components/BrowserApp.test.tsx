import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { createMock, navigateMock, reloadMock, queryMock, destroyMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
  navigateMock: vi.fn(),
  reloadMock: vi.fn(),
  queryMock: vi.fn(),
  destroyMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    createBrowserSurface: createMock,
    navigateBrowserSurface: navigateMock,
    reloadBrowserSurface: reloadMock,
    queryBrowserSurfaceState: queryMock,
    destroyBrowserSurface: destroyMock,
  };
});

import { BrowserApp } from "./BrowserApp";

beforeEach(() => {
  createMock.mockReset();
  navigateMock.mockReset();
  reloadMock.mockReset();
  queryMock.mockReset();
  destroyMock.mockReset();
});

describe("BrowserApp", () => {
  it("shows the Browser-B1 scope description and no active surface initially", () => {
    render(<BrowserApp />);

    expect(
      screen.getByText(/Browser runtime foundation \(Browser-B1\)/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Surface active")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open" })).toBeInTheDocument();
  });

  it("creates a surface and displays its state when Open is clicked", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValueOnce({ surfaceId: "browser-surface-1", url: "https://example.com/" });

    render(<BrowserApp />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    expect(await screen.findByText("Surface active")).toBeInTheDocument();
    expect(await screen.findByTestId("browser-surface-state")).toHaveTextContent("browser-surface-1");
    expect(createMock).toHaveBeenCalledWith("default", "https://example.com");
  });

  it("navigates the existing surface and refreshes displayed state", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock
      .mockResolvedValueOnce({ surfaceId: "browser-surface-1", url: "https://example.com/" })
      .mockResolvedValueOnce({ surfaceId: "browser-surface-1", url: "https://example.com/next" });
    navigateMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await screen.findByTestId("browser-surface-state");

    fireEvent.change(screen.getByTestId("browser-url-input"), {
      target: { value: "https://example.com/next" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Navigate" }));

    expect(await screen.findByText(/example\.com\/next/)).toBeInTheDocument();
    expect(navigateMock).toHaveBeenCalledWith("browser-surface-1", "https://example.com/next");
  });

  it("reloads the surface", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValue({ surfaceId: "browser-surface-1", url: "https://example.com/" });
    reloadMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await screen.findByTestId("browser-surface-state");

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));

    expect(reloadMock).toHaveBeenCalledWith("browser-surface-1");
  });

  it("destroys the surface and returns to the closed state when Close is clicked", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValueOnce({ surfaceId: "browser-surface-1", url: "https://example.com/" });
    destroyMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await screen.findByTestId("browser-surface-state");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(await screen.findByRole("button", { name: "Open" })).toBeInTheDocument();
    expect(destroyMock).toHaveBeenCalledWith("browser-surface-1");
  });

  it("shows a readable error message when create_surface fails", async () => {
    createMock.mockRejectedValueOnce({ kind: "platform", message: "invalid initial_url: relative URL" });

    render(<BrowserApp />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("invalid initial_url: relative URL");
  });

  it("destroys the surface on unmount so no orphaned runtime is left behind", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValueOnce({ surfaceId: "browser-surface-1", url: "https://example.com/" });

    const { unmount } = render(<BrowserApp />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await screen.findByTestId("browser-surface-state");

    unmount();

    expect(destroyMock).toHaveBeenCalledWith("browser-surface-1");
  });

  it("does not attempt to destroy anything on unmount if no surface was ever opened", () => {
    const { unmount } = render(<BrowserApp />);
    unmount();
    expect(destroyMock).not.toHaveBeenCalled();
  });
});
