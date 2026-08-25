import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBar } from "./StatusBar";

describe("StatusBar", () => {
  it("shows a version placeholder, local-first status, and a session indicator", () => {
    render(<StatusBar />);

    expect(screen.getByText(/^KORTEX OS v/)).toBeInTheDocument();
    expect(screen.getByText("Local-first")).toBeInTheDocument();
    expect(screen.getByText("Local session")).toBeInTheDocument();
  });
});
