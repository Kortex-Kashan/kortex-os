import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Toaster } from "./toaster";
import { toast } from "./use-toast";

describe("Toaster", () => {
  it("renders a toast raised via toast() and dismisses it on close", async () => {
    render(<Toaster />);

    act(() => {
      toast({ title: "Saved", description: "Your changes were saved." });
    });

    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.getByText("Your changes were saved.")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Close"));

    await waitFor(() => expect(screen.queryByText("Saved")).not.toBeInTheDocument());
  });
});
