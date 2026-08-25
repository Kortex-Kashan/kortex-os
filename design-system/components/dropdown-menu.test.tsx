import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./dropdown-menu";

describe("DropdownMenu", () => {
  it("opens on trigger click and fires onSelect for an item", async () => {
    const onSelect = vi.fn();
    render(
      <DropdownMenu>
        <DropdownMenuTrigger>Actions</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={onSelect}>Rename</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    // Radix's DropdownMenuTrigger opens on pointerdown (not click) for mouse
    // input, so the interaction must be simulated at that level in jsdom.
    fireEvent.pointerDown(screen.getByText("Actions"), { button: 0, pointerId: 1 });
    const item = await screen.findByText("Rename");
    fireEvent.click(item);

    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});
