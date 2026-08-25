import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "./command";

describe("Command", () => {
  it("filters items as the query changes and shows the empty state", async () => {
    render(
      <Command>
        <CommandInput placeholder="Search commands" />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          <CommandGroup heading="Actions">
            <CommandItem>Rename file</CommandItem>
            <CommandItem>Delete file</CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>,
    );

    expect(screen.getByText("Rename file")).toBeInTheDocument();
    expect(screen.getByText("Delete file")).toBeInTheDocument();

    const input = screen.getByPlaceholderText("Search commands");
    fireEvent.change(input, { target: { value: "rename" } });

    expect(screen.getByText("Rename file")).toBeInTheDocument();
    expect(screen.queryByText("Delete file")).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: "zzz-no-match" } });

    expect(await screen.findByText("No results found.")).toBeInTheDocument();
  });
});
