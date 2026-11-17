# Storybook browser checks

Run these from the repository root after installing npm workspaces.

```sh
npm run build:storybook -w web
npm run test:storybook -w web
npm run test:storybook:visual -w web
```

Both test commands serve the static build on loopback port 6007. The first uses
your local Playwright Chromium (`npx playwright install chromium` if needed).
The second uses the Linux ARM64 Playwright Docker image matching the installed
Playwright version. The CI web job uses an ARM64 runner and runs the Docker
command, which includes the accessibility and interaction checks. On an x64
workstation, the visual command requires Docker with ARM64 emulation enabled.

The suite opens each distinct story in `packages/ui/story-coverage.json` in dark
and light themes. It checks for a visible render and browser errors, audits
WCAG 2.x A and AA rules, and compares screenshots in the pinned environment.
It also exercises keyboard, form, dialog, navigation, and theme-switching
behavior using the rendered components.

After an intentional visual change, rebuild and update the affected baselines
using the same environment as CI.

```sh
npm run build:storybook -w web
npm run test:storybook:visual -w web -- --update-snapshots --grep 'ui-button--'
npm run test:storybook:visual -w web
```

Review and commit the changed PNGs in `services/web/storybook-tests/snapshots`.
When a story is removed or renamed, delete its old baselines. Failed checks
write actual screenshots and diffs to `services/web/storybook-tests/test-results/browser`.
