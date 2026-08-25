import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Input } from "./input";

describe("Input", () => {
  it("reflects typed value via onChange", () => {
    render(<Input placeholder="Email" onChange={() => {}} />);
    const input = screen.getByPlaceholderText("Email") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "user@kortex.local" } });

    expect(input.value).toBe("user@kortex.local");
  });

  it("respects the disabled attribute", () => {
    render(<Input placeholder="Email" disabled />);
    expect(screen.getByPlaceholderText("Email")).toBeDisabled();
  });
});
