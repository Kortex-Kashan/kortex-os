import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

describe("Composer", () => {
  it("sends the trimmed text and clears the input on submit", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} />);

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "  Hello there  " } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).toHaveBeenCalledWith("Hello there");
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("submits on Enter and inserts a newline on Shift+Enter", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} />);
    const textarea = screen.getByLabelText("Message");

    fireEvent.change(textarea, { target: { value: "line one" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("line one");
  });

  it("does not send empty or whitespace-only input", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} />);

    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  // ---------------------------------------------------------------------------
  // Phase B: `disabled` (blocks everything) vs `sendDisabled` (blocks only
  // submission, typing stays usable) are now independent.
  // ---------------------------------------------------------------------------

  it("keeps the textarea usable while sendDisabled is set (a response is generating)", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} sendDisabled={true} />);

    const textarea = screen.getByLabelText("Message");
    expect(textarea).not.toBeDisabled();

    fireEvent.change(textarea, { target: { value: "typing my next question" } });
    expect(textarea).toHaveValue("typing my next question");
  });

  it("disables Send (but not typing) while sendDisabled is set", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} sendDisabled={true} />);

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "hello" } });

    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("does not submit on Enter while sendDisabled is set, and keeps the typed text", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} sendDisabled={true} />);
    const textarea = screen.getByLabelText("Message");

    fireEvent.change(textarea, { target: { value: "hello" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("hello");
  });

  it("re-enables Send once sendDisabled clears, without losing what was typed", () => {
    const onSend = vi.fn();
    const { rerender } = render(<Composer onSend={onSend} disabled={false} sendDisabled={true} />);

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "queued reply" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    rerender(<Composer onSend={onSend} disabled={false} sendDisabled={false} />);

    expect(screen.getByLabelText("Message")).toHaveValue("queued reply");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("queued reply");
  });

  it("blocks both typing and sending when disabled is set (e.g. PAUSED_FOR_APPROVAL)", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={true} />);

    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});
