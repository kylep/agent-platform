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
  Tailwind Preflight strips UA styling; use the primitives. The AST adoption
  gate rejects new violations; existing debt is tracked in a shrink-only baseline.
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
3. Register its meaningful states in `story-coverage.json`.
4. `npm run check:ui -w web && npm run build -w web && npm run test:ui -w web`
   — all clean.

## Change a token

Edit `packages/ui/src/tokens.css` only. Every component and both themes follow
automatically.

## Adoption and story coverage gates

Before changing UI, browse **Studio → Storybook** (or `/storybook/`) and reuse a
public `@ap/ui` export. Extend an existing primitive when its contract fits;
add a shared primitive when the behavior is reusable. App-specific layout and
domain composites can remain in the app, built from these primitives. Do not
introduce another UI library or import `packages/ui/src` internals from apps.

`npm run check:ui -w web` runs the complete design-system gate: tokens, checker
regressions, a fresh Storybook build, coverage, and browser tests. Docker must be
running for the visual checks. `npm run test:storybook -w web` is the fast native
browser loop after building Storybook; it runs rendering, accessibility and
interactions, **without** screenshot comparison. The existing web build and
`test:ui` application tests are still required before shipping UI changes.

The coverage command writes `.ap/design-system-report.json` and `.md`. CI posts
the table in its job summary and uploads the report and browser failure evidence.
The measures are deliberately separate:

- **Direct control adoption:** imported `@ap/ui` control JSX sites divided by
  those sites plus raw button/input/select/textarea/dialog sites. It scans console
  and app production source, recognizes aliases and namespace imports, and
  excludes library internals and test/story fixtures. This is a source-site
  metric, not rendered element counts or runtime usage. Local wrapper uses and
  dynamically created elements are not measured; their JSX implementations are.
- **Public component coverage:** every uppercase runtime export from every
  public package entry must be registered in `story-coverage.json`. Types,
  lowercase hooks and helpers do not count. A new subpath-only export counts too.
- **Declared state coverage:** each component lists meaningful states and real
  Storybook IDs. Include applicable variants, disabled, invalid, empty, loading,
  overflowing, and interactive cases. Do not invent irrelevant states to pad a
  count. Multiple components may share a composition story, such as Table/TH/TD.
  This measures the declared contract; code review still judges whether the
  contract covers the component's behavior, not every prop combination.
- **Browser checks:** each registered story renders and passes axe WCAG A/AA
  checks in both themes, plus screenshot comparison. Focused interaction tests
  cover keyboard controls, form/dialog flows, navigation, and theme switching.
  These tests are not a JavaScript line-coverage percentage.

### Existing debt and exceptions

`adoption-baseline.json` fingerprints the existing violations and their counts.
It is migration debt, not permission for new raw controls. Normal checks fail on
new or changed raw controls, alternate UI-library imports, private imports, and
stale baseline entries. CI also compares the baseline against the PR base or
previous main commit and rejects added signatures or increased counts.

After replacing old controls with primitives, prune only resolved entries:

```sh
node services/web/bin/ui-adoption.mjs --shrink-baseline
```

This command can only shrink the baseline. Do not regenerate it or edit it to
accommodate new violations. If a capability needs native markup, implement it
inside an owned shared primitive and give it stories/tests. There are no blanket
app exceptions. A genuinely necessary policy exception needs a documented,
reviewable change to the checker and a regression test; an inline suppression
comment does not bypass the gate.

### Updating the catalogue and screenshots

1. Add or extend a colocated CSF story and its state entries in
   `packages/ui/story-coverage.json`. Use realistic deterministic fixtures;
   dialogs and errors should be demonstrated visibly, not just named.
2. Add interaction assertions when behavior changes in
   `services/web/storybook-tests/storybook.spec.ts`.
3. Build Storybook and run `npm run check:ui -w web`. Missing state/story
   registrations and browser failures must be fixed before committing.
4. For an intentional appearance change, run
   `npm run test:storybook:visual -w web -- --update-snapshots`, inspect the changed
   images, and rerun without that flag. Commit the reviewed PNGs with the change.
   Never update snapshots just to silence an unexplained regression.

Visual baselines live in `services/web/storybook-tests/snapshots`. The wrapper
uses the installed Playwright version's Linux arm64 Docker image (matching CI's ARM runner) for both local
and CI runs, with fixed time, fonts loaded, and reduced animation. An upgrade to
Playwright requires a deliberate baseline review. The first image pull needs
network access; the test container itself runs without external networking.
