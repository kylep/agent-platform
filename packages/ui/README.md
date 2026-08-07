# @ap/ui — the design system

`packages/ui` is the shared design system of the agent-platform repository: the
Terminal look (dark-default, developer-native), the owned UI primitives, and
the report-kit. It is consumed **as source** — an npm workspace package, no
build step — by the platform console (`services/web`) and by every app
frontend (`apps/*/frontend`, see `docs/design/11-apps-and-reports.md`).

This doc is the contract for anyone — human or agent — touching the UI.
Paths below are repository-relative, because the pieces live in two places:
the design system here, the console that consumes it in `services/web`.

## Where things live

- **Tokens** — `packages/ui/src/tokens.css`: the single source of truth.
  Tailwind v4 `@theme` primitives (ink ramp, terminal-cyan accent, status
  colors, radius, fonts) plus semantic `--ds-*` aliases. Dark values in
  `:root`, light in `[data-theme="light"]` — themes are a pure variable swap,
  no component changes. Chart series colors are `--ds-chart-1..8`.
- **Owned primitives** — `packages/ui/src/*.tsx`: shadcn-style, built with
  [cva](https://cva.style) (class-variance-authority, which expresses a
  component's variants as class sets) plus the local `cn()` helper
  (`src/cn.ts`, clsx + tailwind-merge), using Radix where interaction demands
  it. Button, Chip/StatusChip/ChipButton, Banner, Table/TH/TD,
  Input/Textarea/Select/CodeEditor, Stat/StatRow, ConfirmDialog. Each is
  exported by subpath in `package.json` — import as `@ap/ui/button`.
- **App structure** — `services/web/src/app.css`: layout, nav, and
  page-specific composites (chat, transcript, diff…) that aren't worth
  componentizing. Token vars only.
- **Stories** — colocated `*.stories.tsx` here; Storybook is the catalogue and
  ships with the console at `/storybook/`. `Foundations.stories.tsx` renders
  the live tokens.

## Rules

- **No raw hex / raw color values outside `tokens.css`** — use semantic
  utilities (`bg-canvas`, `text-muted`, `border-border`, `text-accent`, …) or
  `var(--ds-*)`. Enforced by `services/web/bin/check-no-raw-hex.mjs` and gated
  in CI.
- **Never render a raw `<button>`/`<input>`/`<select>`/`<textarea>`** —
  Tailwind Preflight strips UA styling; use the primitives. (The axe
  accessibility gate catches the worst of this, code review the rest.)
- **New reusable UI = a primitive + a story.** Page-specific layout goes in
  `app.css` with token vars.
- **Both themes must work.** If a color choice needs to differ per theme, add
  a `--ds-*` alias — don't branch in components.
- The Playwright smoke + axe suite is the backpressure: it must pass before
  any UI change ships.

## Commands

All npm scripts live in the `services/web` workspace, so run them from the
repository root with `-w web` (or from inside `services/web`):

```sh
npm run check:tokens -w web   # no-raw-hex gate
npm run build -w web          # type-check + vite build
npm run test:ui -w web        # Playwright smoke + axe
npm run storybook -w web      # the catalogue, locally
```

## Add a primitive

1. Author it in `packages/ui/src/<name>.tsx` following the cva + `cn`
   convention, and add a `"./<name>"` entry to this package's `exports`.
2. Add `<name>.stories.tsx` beside it.
3. `npm run check:tokens -w web && npm run build -w web && npm run test:ui -w web`
   — all clean.

## Change a token

Edit `packages/ui/src/tokens.css` only. Every component and both themes follow
automatically.
