import * as React from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Input,
  Label,
  NavigationMenu,
  NavigationMenuContent,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
  NavigationMenuTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Separator,
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  Skeleton,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Toaster,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
  toast,
} from "@kortex/design-system";

/**
 * ADR-0002 §10.7: an in-app, dev-build-only component gallery instead of
 * Storybook. Excluded from production builds by the router (see
 * routes/index.tsx) rather than by anything in this file — this module
 * itself is plain, always-importable code so it stays trivially testable
 * and tree-shakeable via a dynamic import gated on `import.meta.env.DEV`.
 */

const SECTIONS = [
  { id: "buttons", label: "Buttons" },
  { id: "cards", label: "Cards" },
  { id: "inputs", label: "Inputs" },
  { id: "badges", label: "Badges" },
  { id: "dialog", label: "Dialog" },
  { id: "dropdown", label: "Dropdown Menu" },
  { id: "tooltip", label: "Tooltip" },
  { id: "select", label: "Select" },
  { id: "table", label: "Table" },
  { id: "navigation", label: "Navigation Menu" },
  { id: "command", label: "Command Palette" },
  { id: "toast", label: "Toast" },
  { id: "loading", label: "Loading States" },
] as const;

function GallerySection({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <Card id={id} className="scroll-mt-6">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-start gap-4">{children}</CardContent>
    </Card>
  );
}

const TABLE_ROWS = [
  { name: "Kashan", role: "Chief Architect", status: "Active" },
  { name: "Claude Code", role: "Implementation Agent", status: "Active" },
  { name: "Gemini", role: "Review Agent", status: "Idle" },
];

export function ComponentGallery() {
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [commandOpen, setCommandOpen] = React.useState(false);

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
    <TooltipProvider>
      <SidebarProvider>
        <div className="flex h-screen bg-background text-foreground">
          <Sidebar>
            <SidebarHeader>
              <div className="flex items-center justify-between px-2 py-1">
                <span className="text-body font-semibold">KORTEX DS</span>
                <SidebarTrigger />
              </div>
            </SidebarHeader>
            <SidebarContent>
              <SidebarGroup>
                <SidebarGroupLabel>Sections</SidebarGroupLabel>
                <SidebarMenu>
                  {SECTIONS.map((section) => (
                    <SidebarMenuItem key={section.id}>
                      {/* A real <a href="#id"> would collide with react-router's
                          createHashRouter (ADR-0002 §7.2): the whole URL hash is
                          the route path, so "#buttons" would navigate to a
                          nonexistent "/buttons" route instead of scrolling. */}
                      <SidebarMenuButton
                        onClick={() =>
                          document.getElementById(section.id)?.scrollIntoView({ block: "start" })
                        }
                      >
                        {section.label}
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroup>
            </SidebarContent>
            <SidebarFooter>
              <Button variant="outline" size="sm" onClick={() => setCommandOpen(true)}>
                Command palette (Ctrl+K)
              </Button>
            </SidebarFooter>
          </Sidebar>

          <div className="flex-1 overflow-auto">
            <header className="flex items-center justify-between border-b border-border p-4">
              <h1 className="text-heading">Design System Gallery</h1>
              <p className="text-caption text-muted-foreground">Dev-only — not shipped in production</p>
            </header>

            <main className="flex flex-col gap-6 p-6">
              <GallerySection id="buttons" title="Buttons" description="Variants and sizes.">
                <Button>Default</Button>
                <Button variant="destructive">Destructive</Button>
                <Button variant="outline">Outline</Button>
                <Button variant="secondary">Secondary</Button>
                <Button variant="ghost">Ghost</Button>
                <Button variant="link">Link</Button>
                <Button size="sm">Small</Button>
                <Button size="lg">Large</Button>
                <Button disabled>Disabled</Button>
              </GallerySection>

              <GallerySection id="cards" title="Cards" description="Bounded content container.">
                <Card className="w-72">
                  <CardHeader>
                    <CardTitle>Team members</CardTitle>
                    <CardDescription>Everyone with access to this workspace.</CardDescription>
                  </CardHeader>
                  <CardContent>3 members</CardContent>
                </Card>
              </GallerySection>

              <GallerySection id="inputs" title="Inputs" description="Text entry with labels and separators.">
                <div className="flex w-64 flex-col gap-2">
                  <Label htmlFor="gallery-email">Email</Label>
                  <Input id="gallery-email" placeholder="you@kortex.local" />
                  <Separator />
                  <Label htmlFor="gallery-disabled">Disabled</Label>
                  <Input id="gallery-disabled" disabled placeholder="Disabled" />
                </div>
              </GallerySection>

              <GallerySection id="badges" title="Badges" description="Status indicators.">
                <Badge>Default</Badge>
                <Badge variant="secondary">Secondary</Badge>
                <Badge variant="destructive">Destructive</Badge>
                <Badge variant="outline">Outline</Badge>
              </GallerySection>

              <GallerySection id="dialog" title="Dialog" description="Modal overlay for focused tasks.">
                <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                  <DialogTrigger asChild>
                    <Button variant="outline">Open dialog</Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Edit profile</DialogTitle>
                      <DialogDescription>Update your display name.</DialogDescription>
                    </DialogHeader>
                    <Input defaultValue="Kashan" />
                    <DialogFooter>
                      <Button onClick={() => setDialogOpen(false)}>Save</Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              </GallerySection>

              <GallerySection id="dropdown" title="Dropdown Menu" description="Action menu from a trigger.">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline">Open menu</Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuLabel>Row actions</DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem>Rename</DropdownMenuItem>
                    <DropdownMenuItem>Delete</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </GallerySection>

              <GallerySection id="tooltip" title="Tooltip" description="Supplementary hint on hover/focus.">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="outline">Hover me</Button>
                  </TooltipTrigger>
                  <TooltipContent>Shortcuts: Ctrl+K to search</TooltipContent>
                </Tooltip>
              </GallerySection>

              <GallerySection id="select" title="Select" description="Single-choice dropdown.">
                <Select defaultValue="member">
                  <SelectTrigger className="w-48">
                    <SelectValue placeholder="Choose a role" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Admin</SelectItem>
                    <SelectItem value="member">Member</SelectItem>
                    <SelectItem value="viewer">Viewer</SelectItem>
                  </SelectContent>
                </Select>
              </GallerySection>

              <GallerySection id="table" title="Table" description="Tabular data.">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Role</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {TABLE_ROWS.map((row) => (
                      <TableRow key={row.name}>
                        <TableCell>{row.name}</TableCell>
                        <TableCell>{row.role}</TableCell>
                        <TableCell>
                          <Badge variant={row.status === "Active" ? "default" : "secondary"}>
                            {row.status}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </GallerySection>

              <GallerySection id="navigation" title="Navigation Menu" description="Top-level app navigation.">
                <NavigationMenu>
                  <NavigationMenuList>
                    <NavigationMenuItem>
                      <NavigationMenuTrigger>Modules</NavigationMenuTrigger>
                      <NavigationMenuContent>
                        <ul className="grid w-48 gap-1 p-2">
                          <li>
                            <NavigationMenuLink className="block rounded-sm px-2 py-1.5 text-body hover:bg-accent hover:text-accent-foreground">
                              Finance
                            </NavigationMenuLink>
                          </li>
                          <li>
                            <NavigationMenuLink className="block rounded-sm px-2 py-1.5 text-body hover:bg-accent hover:text-accent-foreground">
                              HR &amp; Payroll
                            </NavigationMenuLink>
                          </li>
                        </ul>
                      </NavigationMenuContent>
                    </NavigationMenuItem>
                    <NavigationMenuItem>
                      <NavigationMenuLink className="inline-flex h-9 items-center rounded-md px-4 py-2 text-body font-medium hover:bg-accent hover:text-accent-foreground">
                        Settings
                      </NavigationMenuLink>
                    </NavigationMenuItem>
                  </NavigationMenuList>
                </NavigationMenu>
              </GallerySection>

              <GallerySection id="command" title="Command Palette" description="Ctrl+K searchable action list.">
                <Button variant="outline" onClick={() => setCommandOpen(true)}>
                  Open command palette
                </Button>
                <div className="w-full max-w-md rounded-md border border-border">
                  <Command>
                    <CommandInput placeholder="Inline preview — type to filter" />
                    <CommandList>
                      <CommandEmpty>No results found.</CommandEmpty>
                      <CommandGroup heading="Actions">
                        <CommandItem>
                          Rename file
                          <CommandShortcut>⌘R</CommandShortcut>
                        </CommandItem>
                        <CommandItem>
                          Delete file
                          <CommandShortcut>⌘D</CommandShortcut>
                        </CommandItem>
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </div>
              </GallerySection>

              <GallerySection id="toast" title="Toast" description="Transient, non-blocking notifications.">
                <Button
                  variant="outline"
                  onClick={() =>
                    toast({ title: "Saved", description: "Your changes were saved." })
                  }
                >
                  Show toast
                </Button>
                <Button
                  variant="destructive"
                  onClick={() =>
                    toast({ variant: "destructive", title: "Error", description: "Something went wrong." })
                  }
                >
                  Show destructive toast
                </Button>
              </GallerySection>

              <GallerySection id="loading" title="Loading States" description="Skeleton and spinner.">
                <div className="flex flex-col gap-2">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-20 w-48" />
                </div>
                <div className="flex items-center gap-3">
                  <Spinner />
                  <Spinner size={24} />
                  <Spinner size={32} />
                </div>
              </GallerySection>
            </main>
          </div>
        </div>

        <CommandDialog open={commandOpen} onOpenChange={setCommandOpen}>
          <CommandInput placeholder="Type a command or search..." />
          <CommandList>
            <CommandEmpty>No results found.</CommandEmpty>
            <CommandGroup heading="Navigate">
              {SECTIONS.map((section) => (
                <CommandItem
                  key={section.id}
                  onSelect={() => {
                    setCommandOpen(false);
                    document.getElementById(section.id)?.scrollIntoView({ block: "start" });
                  }}
                >
                  {section.label}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </CommandDialog>

        <Toaster />
      </SidebarProvider>
    </TooltipProvider>
  );
}
