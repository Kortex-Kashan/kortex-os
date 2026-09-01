import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackendUnavailableScreen } from "./BackendUnavailableScreen";

const { useAuthMock, retryConnectionSpy } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
  retryConnectionSpy: vi.fn(),
}));

vi.mock("./AuthProvider", () => ({
  useAuth: useAuthMock,
}));

beforeEach(() => {
  retryConnectionSpy.mockReset();
  useAuthMock.mockReturnValue({
    state: { status: "BACKEND_UNAVAILABLE" },
    login: vi.fn(),
    logout: vi.fn(),
    bootstrap: vi.fn(),
    retryConnection: retryConnectionSpy,
    reportIpcResult: vi.fn(),
  });
});

describe("BackendUnavailableScreen", () => {
  it("accessibly announces that the backend is unreachable", () => {
    render(<BackendUnavailableScreen />);

    expect(screen.getByRole("alert")).toHaveTextContent(/kortex backend is not responding/i);
  });

  it("calls retryConnection() when the Retry button is clicked", () => {
    render(<BackendUnavailableScreen />);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(retryConnectionSpy).toHaveBeenCalledTimes(1);
  });
});
