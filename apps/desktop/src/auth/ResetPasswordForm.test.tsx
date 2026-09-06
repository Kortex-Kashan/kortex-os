import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ResetPasswordForm } from "./ResetPasswordForm";

const { resetPasswordMock } = vi.hoisted(() => ({ resetPasswordMock: vi.fn() }));

vi.mock("./authCapability", () => ({
  resetPassword: resetPasswordMock,
}));

describe("ResetPasswordForm", () => {
  it("submits the trimmed token and new password, then calls onResetSuccess", async () => {
    resetPasswordMock.mockResolvedValueOnce({ ok: true });
    const onResetSuccess = vi.fn();
    render(<ResetPasswordForm onBack={vi.fn()} onResetSuccess={onResetSuccess} />);

    fireEvent.change(screen.getByLabelText("Reset token"), { target: { value: "  the-token  " } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brand-new-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Reset password" }));

    await waitFor(() => expect(onResetSuccess).toHaveBeenCalled());
    expect(resetPasswordMock).toHaveBeenCalledWith({ token: "the-token", newPassword: "brand-new-password" });
  });

  it("shows the backend's error message and clears the password on failure, keeping the token", async () => {
    resetPasswordMock.mockResolvedValueOnce({
      ok: false,
      kind: "INVALID_TOKEN",
      message: "This password reset link is invalid or has expired.",
    });
    render(<ResetPasswordForm onBack={vi.fn()} onResetSuccess={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Reset token"), { target: { value: "bad-token" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brand-new-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Reset password" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("This password reset link is invalid or has expired.");
    });
    expect(screen.getByLabelText("New password")).toHaveValue("");
    expect(screen.getByLabelText("Reset token")).toHaveValue("bad-token");
  });

  it("calls onBack when 'Back to sign in' is clicked", () => {
    const onBack = vi.fn();
    render(<ResetPasswordForm onBack={onBack} onResetSuccess={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(onBack).toHaveBeenCalled();
  });
});
