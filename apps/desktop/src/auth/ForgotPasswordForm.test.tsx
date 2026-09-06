import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ForgotPasswordForm } from "./ForgotPasswordForm";

const { requestPasswordResetMock } = vi.hoisted(() => ({ requestPasswordResetMock: vi.fn() }));

vi.mock("./authCapability", () => ({
  requestPasswordReset: requestPasswordResetMock,
}));

describe("ForgotPasswordForm", () => {
  it("submits the trimmed email and shows the generic response message", async () => {
    requestPasswordResetMock.mockResolvedValueOnce({
      ok: true,
      message: "If that email is registered, a password reset link has been sent.",
    });
    const onBack = vi.fn();
    const onGoToReset = vi.fn();
    render(<ForgotPasswordForm onBack={onBack} onGoToReset={onGoToReset} />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "  alice@example.com  " } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "If that email is registered, a password reset link has been sent.",
      );
    });
    expect(requestPasswordResetMock).toHaveBeenCalledWith({ email: "alice@example.com" });
  });

  it("shows the identical message regardless of whether the email exists — never distinguishing match from no-match in the UI", async () => {
    requestPasswordResetMock.mockResolvedValueOnce({ ok: true, message: "generic message" });
    render(<ForgotPasswordForm onBack={vi.fn()} onGoToReset={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "nobody@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("generic message"));
  });

  it("calls onBack when 'Back to sign in' is clicked", () => {
    const onBack = vi.fn();
    render(<ForgotPasswordForm onBack={onBack} onGoToReset={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(onBack).toHaveBeenCalled();
  });

  it("calls onGoToReset from the post-submit state", async () => {
    requestPasswordResetMock.mockResolvedValueOnce({ ok: true, message: "generic message" });
    const onGoToReset = vi.fn();
    render(<ForgotPasswordForm onBack={vi.fn()} onGoToReset={onGoToReset} />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() => screen.getByRole("button", { name: "I have a reset token" }));
    fireEvent.click(screen.getByRole("button", { name: "I have a reset token" }));
    expect(onGoToReset).toHaveBeenCalled();
  });
});
