import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders the desktop shell with its workspace empty state", () => {
    render(<App />);
    expect(screen.getByText("KORTEX OS")).toBeInTheDocument();
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });
});
