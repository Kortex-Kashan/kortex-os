import * as React from "react";
import { useNavigate } from "react-router-dom";
import {
  Badge,
  Button,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@kortex/design-system";
import { useUiStore } from "@/stores/uiStore";

import { SearchIcon, UserIcon } from "./icons";
import { NAV_GROUPS } from "./navigation/navConfig";

export function TopBar() {
  const [commandOpen, setCommandOpen] = React.useState(false);
  const { theme, toggleTheme } = useUiStore();
  const navigate = useNavigate();

  React.useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setCommandOpen((open) => !open);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-card px-4">
      <div className="flex items-center gap-3">
        <span className="text-body font-semibold">KORTEX OS</span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="gap-2 text-muted-foreground"
              onClick={() => setCommandOpen(true)}
            >
              <SearchIcon className="size-4" />
              Search
              <kbd className="rounded border border-border px-1 text-caption">Ctrl K</kbd>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Open the command palette</TooltipContent>
        </Tooltip>
      </div>

      <div className="flex items-center gap-3">
        <Badge variant="secondary" className="gap-1.5">
          <span className="size-1.5 rounded-full bg-primary" aria-hidden="true" />
          System nominal
        </Badge>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="User menu">
              <UserIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>Guest</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={toggleTheme}>
              {theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            </DropdownMenuItem>
            <DropdownMenuItem disabled>Profile</DropdownMenuItem>
            <DropdownMenuItem disabled>Sign out</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <CommandDialog open={commandOpen} onOpenChange={setCommandOpen}>
        <CommandInput placeholder="Type a command or search..." />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          <CommandGroup heading="Actions">
            <CommandItem
              onSelect={() => {
                toggleTheme();
                setCommandOpen(false);
              }}
            >
              Toggle theme
            </CommandItem>
            {import.meta.env.DEV && (
              <CommandItem
                onSelect={() => {
                  setCommandOpen(false);
                  navigate("/dev/components");
                }}
              >
                Open design system gallery
              </CommandItem>
            )}
          </CommandGroup>
          {NAV_GROUPS.map((group) => (
            <CommandGroup key={group.id} heading={group.label}>
              {group.items.map((item) => (
                <CommandItem key={item.id} disabled>
                  {item.label}
                  <CommandShortcut>Soon</CommandShortcut>
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
        </CommandList>
      </CommandDialog>
    </header>
  );
}
