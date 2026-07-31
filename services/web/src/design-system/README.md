# Design system — agent guide

The platform console's visual direction is **Terminal** (dark-default,
developer-native), sharing its foundations with pericak.com v4. This doc is
the contract for anyone — human or agent — touching the UI.

## Where things live

- **Tokens** — `tokens.css`: the single source of truth. Tailwind v4 `@theme`
  primitives (ink ramp, terminal-cyan accent, status colors, radius, fonts)
  plus semantic `--ds-*` aliases. Dark values in `:root`, light in
  `[data-theme="light"]` — themes are a pure variable swap, no component
  changes. Chart series colors are `--ds-chart-1..8`.
- **Owned primitives** — `src/ui/*.tsx`: shadcn-style (cva + `cn`, Radix where
  interaction demands it). Button, Chip/StatusChip/ChipButton, Banner,
  Table/TH/TD, Input/Textarea/Select/CodeEditor, Stat/StatRow, ConfirmDialog.
- **App structure** — `src/app.css`: layout, nav, and page-specific composites
  (chat, transcript, diff…) that aren't worth componentizing. Token vars only.
- **Stories** — colocated `*.stories.tsx`; Storybook is the catalogue, shipped
  with the site at `/storybook/`. `Foundations` renders the live tokens.

## Rules

- **No raw hex / raw color values outside `tokens.css`** — use semantic
  utilities (`bg-canvas`, `text-muted`, `border-border`, `text-accent`, …) or
  `var(--ds-*)`. Enforced by `bin/check-no-raw-hex.mjs` (`npm run
  check:tokens`, gated in CI).
- **Never render a raw `<button>`/`<input>`/`<select>`/`<textarea>`** —
  Preflight strips UA styling; use the primitives. (The axe gate catches the
  worst of this, the code review should catch the rest.)
- **New reusable UI = a primitive + a story.** Page-specific layout goes in
  `app.css` with token vars.
- **Both themes must work.** If a color choice needs to differ per theme, add
  a `--ds-*` alias — don't branch in components.
- The Playwright smoke + axe suite (`npm run test:ui`) is the backpressure:
  it must pass before any UI change ships.

## Add a primitive

1. Author it in `src/ui/<name>.tsx` following the cva + `cn` convention.
2. Add `<name>.stories.tsx` beside it.
3. `npm run check:tokens && npm run build && npm run test:ui` — all clean.

## Change a token

Edit `tokens.css` only. Every component and both themes follow automatically.
