# Components

Foundation components, vendored here from shadcn/ui as owned source and
never installed as a runtime npm dependency (ADR-0002 §10.5, §21.1). Each
component consumes only the semantic tokens defined in
`../styles/tokens.css` (via the Tailwind preset in `../tailwind.config.js`)
— no component hardcodes a color, spacing, or shadow value.

All components are re-exported from `./index.ts`, which is re-exported
from the package root `../index.ts`. Consumers should import from
`@kortex/design-system` (or the `@kortex/design-system/components`
subpath) — never reach into an individual file path.

To vendor an additional component, run from this directory:

```bash
npx shadcn@latest add <component>
```

against the `components.json` configuration at the `design-system`
package root, then re-export it from `./index.ts`.

---

## Button

**Purpose**: the single interactive trigger primitive for actions
(submit a form, open a dialog, navigate). Every other action-triggering
element in the desktop app should compose `Button` rather than a bare
`<button>`.

**Variants**

| Prop | Values |
|---|---|
| `variant` | `default` (primary action) · `destructive` (irreversible/dangerous action) · `outline` · `secondary` · `ghost` · `link` |
| `size` | `default` · `sm` · `lg` · `icon` |

**Usage**

```tsx
import { Button } from "@kortex/design-system";

<Button onClick={handleSave}>Save</Button>
<Button variant="destructive" onClick={handleDelete}>Delete</Button>
<Button variant="outline" size="sm">Cancel</Button>
<Button asChild>
  <a href="/docs">Read the docs</a>
</Button>
```

`asChild` (via Radix `Slot`) merges Button's styling and behavior onto a
single child element instead of rendering a wrapping `<button>` — use it
when the trigger must be a native link or another semantic element.

**Accessibility**: renders a native `<button>` by default (keyboard and
screen-reader accessible with no extra work); `disabled` sets the native
`disabled` attribute, which also prevents the click handler from firing.
Focus is visible via `focus-visible:ring-2` — never removed.

---

## Card

**Purpose**: a bounded content container for grouping related
information (a summary panel, a form section, a list item wrapper).

**Composition** (no variant prop — compose the sub-parts you need):

`Card` · `CardHeader` · `CardTitle` · `CardDescription` · `CardContent` ·
`CardFooter`

**Usage**

```tsx
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@kortex/design-system";

<Card>
  <CardHeader>
    <CardTitle>Team members</CardTitle>
    <CardDescription>Everyone with access to this workspace.</CardDescription>
  </CardHeader>
  <CardContent>3 members</CardContent>
  <CardFooter>
    <Button size="sm">Invite</Button>
  </CardFooter>
</Card>
```

**Accessibility**: `Card` and its sub-parts render plain `<div>`/`<h3>`/
`<p>` elements with no implicit ARIA role — supply a heading level
appropriate to the surrounding document outline if `CardTitle`'s default
`<h3>` doesn't fit.

---

## Input

**Purpose**: single-line text entry (email, search, form fields).

**Usage**

```tsx
import { Input, Label } from "@kortex/design-system";

<Label htmlFor="email">Email</Label>
<Input id="email" type="email" placeholder="you@company.com" />
```

`Input` forwards every native `<input>` prop (`type`, `disabled`,
`value`, `onChange`, `aria-*`, etc.) — it does not wrap the value in
component state.

**Accessibility**: always pair with a `Label` (via matching `id`/
`htmlFor`, see below) rather than a bare `placeholder`, which is not a
reliable accessible name. `disabled` is a native attribute.

---

## Label

**Purpose**: an accessible label for a form control, built on Radix
`@radix-ui/react-label`.

**Usage**

```tsx
<Label htmlFor="email">Email</Label>
<Input id="email" />
```

**Accessibility**: clicking the label focuses/activates its associated
control (native `<label>` behavior, preserved by the Radix primitive).
Always set `htmlFor` to the control's `id`.

---

## Badge

**Purpose**: a small status/category indicator (e.g. a record's state)
— not interactive, not a button substitute.

**Variants**

| Prop | Values |
|---|---|
| `variant` | `default` · `secondary` · `destructive` · `outline` |

**Usage**

```tsx
import { Badge } from "@kortex/design-system";

<Badge>Active</Badge>
<Badge variant="destructive">Overdue</Badge>
<Badge variant="outline">Draft</Badge>
```

**Accessibility**: renders a `<div>`; if a badge conveys status that
isn't otherwise present as text (e.g. a color-only dot), pair it with
visible text as shown above — never rely on color alone.

---

## Separator

**Purpose**: a visual (and, when non-decorative, semantic) divider
between content sections. Built on `@radix-ui/react-separator`.

**Usage**

```tsx
import { Separator } from "@kortex/design-system";

<Separator />                                   {/* horizontal, decorative */}
<Separator orientation="vertical" className="h-6" />
<Separator decorative={false} />                {/* announced as role="separator" */}
```

**Accessibility**: defaults to `decorative` (`role="none"`, hidden from
the accessibility tree) because most dividers are purely visual. Set
`decorative={false}` only when the divider carries real semantic
meaning between two regions.

---

## Dialog *(optional foundation component — implemented)*

**Purpose**: a modal overlay for focused tasks that interrupt the
current flow (confirmations, focused forms). Built on
`@radix-ui/react-dialog`.

**Composition**: `Dialog` · `DialogTrigger` · `DialogContent` ·
`DialogHeader` · `DialogTitle` · `DialogDescription` · `DialogFooter` ·
`DialogClose`

**Usage**

```tsx
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle, DialogTrigger,
} from "@kortex/design-system";

<Dialog>
  <DialogTrigger asChild>
    <Button variant="outline">Edit profile</Button>
  </DialogTrigger>
  <DialogContent>
    <DialogHeader>
      <DialogTitle>Edit profile</DialogTitle>
      <DialogDescription>Update your display name.</DialogDescription>
    </DialogHeader>
    <Input defaultValue="Kashan" />
    <DialogFooter>
      <Button type="submit">Save</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```

**Accessibility**: Radix manages focus trapping, `aria-modal`, labelling
via `DialogTitle`/`DialogDescription`, and Escape-to-close. Every
`DialogContent` renders a visible close control with an `sr-only`
"Close" label — do not remove it. This vendored build intentionally
ships without entrance/exit animation (see Animation note below).

---

## Dropdown Menu *(optional foundation component — implemented)*

**Purpose**: a menu of actions triggered from a control (e.g. a row's
"⋯" button). Built on `@radix-ui/react-dropdown-menu`.

**Composition**: `DropdownMenu` · `DropdownMenuTrigger` ·
`DropdownMenuContent` · `DropdownMenuItem` · `DropdownMenuCheckboxItem` ·
`DropdownMenuRadioGroup` · `DropdownMenuRadioItem` · `DropdownMenuLabel`
· `DropdownMenuSeparator` · `DropdownMenuShortcut` ·
`DropdownMenuGroup`

Nested sub-menus (`DropdownMenuSub`) are intentionally not vendored in
this pass — add them the same way (`npx shadcn@latest add
dropdown-menu` re-run, or hand-write following the same pattern) if a
future screen needs them.

**Usage**

```tsx
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@kortex/design-system";

<DropdownMenu>
  <DropdownMenuTrigger asChild>
    <Button variant="ghost" size="icon">⋯</Button>
  </DropdownMenuTrigger>
  <DropdownMenuContent>
    <DropdownMenuItem onSelect={handleRename}>Rename</DropdownMenuItem>
    <DropdownMenuItem onSelect={handleDelete}>Delete</DropdownMenuItem>
  </DropdownMenuContent>
</DropdownMenu>
```

**Accessibility**: implements the WAI-ARIA menu pattern — arrow-key
navigation, typeahead, and Escape-to-close are provided by Radix.

---

## Tooltip *(optional foundation component — implemented)*

**Purpose**: a short, supplementary hint shown on hover/focus of a
trigger. Never used to convey information required to complete a task
(it is not reliably reachable on touch input).

**Composition**: `TooltipProvider` (wrap once near the app root) ·
`Tooltip` · `TooltipTrigger` · `TooltipContent`

**Usage**

```tsx
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@kortex/design-system";

<TooltipProvider>
  <Tooltip>
    <TooltipTrigger asChild>
      <Button variant="ghost" size="icon">?</Button>
    </TooltipTrigger>
    <TooltipContent>Shortcuts: Cmd+K to search</TooltipContent>
  </Tooltip>
</TooltipProvider>
```

**Accessibility**: opens on both hover and keyboard focus (not hover
alone), and is dismissed on Escape — behavior provided by Radix.

---

## Animation note

Per the Phase 3 animation policy, `motion` (Motion.dev) is the only
approved animation runtime. `tailwindcss-animate` (the CSS-utility
plugin shadcn's own generator normally wires up for Dialog/Dropdown
enter/exit transitions) is a second animation dependency and was
deliberately **not** installed here — these vendored components ship
functionally complete but without entrance/exit motion. Add
purposeful transitions with `motion` in a later pass, at the call site
or by wrapping `DialogContent`/`DropdownMenuContent`, rather than by
introducing a CSS animation plugin.
