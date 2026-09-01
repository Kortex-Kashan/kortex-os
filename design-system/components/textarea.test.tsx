import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Textarea } from "./textarea";

describe("Textarea", () => {
  it("reflects typed value via onChange", () => {
    render(<Textarea placeholder="Message" onChange={() => {}} />);
    const textarea = screen.getByPlaceholderText("Message") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "Hello there" } });

    expect(textarea.value).toBe("Hello there");
  });

  it("respects the disabled attribute", () => {
    render(<Textarea placeholder="Message" disabled />);
    expect(screen.getByPlaceholderText("Message")).toBeDisabled();
  });
});
