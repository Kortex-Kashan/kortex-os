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
import { useAuth } from "@/auth/AuthProvider";
import { useUiStore } from "@/stores/uiStore";
import { AiStudioIcon } from "@/workspace/icons";

import { Brand } from "./Brand";
import { SearchIcon, UserIcon } from "./icons";
import { NAV_GROUPS } from "./navigation/navConfig";

export function TopBar() {
  const [commandOpen, setCommandOpen] = React.useState(false);
  const { theme, toggleTheme, copilotOpen, toggleCopilot } = useUiStore();
  const auth = useAuth();
  const navigate = useNavigate();
  const identityLabel =
    auth.state.status === "AUTHENTICATED" && auth.state.identity
      ? auth.state.identity.principalId
      : "Signed in";

  React.useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setCommandOpen((open) => !open);
      } else if (event.key === "j" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        toggleCopilot();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [toggleCopilot]);

  return (
    <header className="relative z-40 flex h-14 shrink-0 items-center gap-4 border-b border-border/70 bg-background/85 px-4 backdrop-blur-xl">
      <Brand compact />

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="mx-auto w-full max-w-md justify-start gap-2 text-muted-foreground"
            onClick={() => setCommandOpen(true)}
          >
            <SearchIcon className="size-4 text-cyan" />
            <span className="flex-1 text-left">Search KORTEX or run a command</span>
            <kbd className="rounded border border-border px-1 text-caption">Ctrl K</kbd>
          </Button>
        </TooltipTrigger>
        <TooltipContent>Open the command palette</TooltipContent>
      </Tooltip>

      <div className="flex items-center gap-3">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant={copilotOpen ? "default" : "outline"}
              size="sm"
              className="gap-2 border-primary/40 hover:border-primary"
              onClick={toggleCopilot}
              aria-label="Toggle KORTEX AI Copilot"
            >
              <AiStudioIcon className="size-4 text-primary" aria-hidden="true" />
              <span className="hidden font-medium sm:inline">KORTEX AI</span>
              <kbd className="hidden rounded border border-border px-1 text-caption text-muted-foreground md:inline">
                Ctrl J
              </kbd>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Toggle persistent KORTEX AI Copilot (Ctrl+J)</TooltipContent>
        </Tooltip>

        <Badge variant="secondary" className="gap-1.5 border border-success/25 bg-success/10 text-success">
          <span className="size-1.5 rounded-full bg-success shadow-[0_0_8px_hsl(var(--success))]" aria-hidden="true" />
          System nominal
        </Badge>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="User menu">
              <UserIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>{identityLabel}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={toggleTheme}>
              {theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => navigate("/account")}>Account</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void auth.logout()}>Sign out</DropdownMenuItem>
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
            <CommandItem
              onSelect={() => {
                toggleCopilot();
                setCommandOpen(false);
              }}
            >
              Toggle KORTEX AI Copilot
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
