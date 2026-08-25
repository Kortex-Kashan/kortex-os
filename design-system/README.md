# KORTEX Design System

Shared UI design tokens, component guidelines, TailwindCSS themes, icons, and visual assets for KORTEX applications.

Consumed as a pnpm workspace package (`@kortex/design-system`) — never
copy-pasted per app (ADR-0002 §10.1, §17 M4).

- `styles/tokens.css` — the source of truth for semantic color tokens
  (light `:root` and dark `.dark` CSS variables).
- `tailwind.config.js` — the Tailwind preset every consuming app
  extends; maps token CSS variables to Tailwind's color/radius/shadow/
  typography theme (ADR-0002 §10.2: "Tailwind config *is* the token
  layer").
- `themes/` — the light/dark theme registry.
- `tokens/` — non-CSS-variable tokens (currently: Motion.dev
  duration/easing values) that don't need runtime theme switching.
- `components/` — vendored shadcn/ui foundation components; see
  [`components/README.md`](components/README.md) for the full catalog,
  variants, usage examples, and accessibility notes.

Import from the package root or its `components`/`tokens` subpaths:

```ts
import { Button, Card, cn } from "@kortex/design-system";
import { motionTokens } from "@kortex/design-system/tokens";
import "@kortex/design-system/styles/tokens.css";
```
