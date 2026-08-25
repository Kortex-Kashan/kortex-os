import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "./dialog";

describe("Dialog", () => {
  it("opens on trigger click and closes on close-button click", async () => {
    render(
      <Dialog>
        <DialogTrigger>Open settings</DialogTrigger>
        <DialogContent>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Manage your preferences.</DialogDescription>
        </DialogContent>
      </Dialog>,
    );

    expect(screen.queryByText("Settings")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Open settings"));
    expect(await screen.findByText("Settings")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Close"));
    expect(screen.queryByText("Settings")).not.toBeInTheDocument();
  });
});
