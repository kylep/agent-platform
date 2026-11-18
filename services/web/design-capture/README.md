# Agent Platform site capture

This pipeline builds editable UI Designer documents from the console, the Running, Stockmarket, TCMS, and Judgment frontends, and selected Storybook foundation screens. It uses deterministic local fixtures for signed-in views, authentication states, app records, and other data-dependent pages. The route inventory includes sample parameters for detail screens and records redirects as aliases.

## Capture and convert

Run from the repository root with the repository's Node version and Python 3.12. Install the npm workspace dependencies, Playwright Chromium, and the backend's development dependencies on a fresh checkout.

```sh
npm ci
npx playwright install chromium
python3.12 -m venv services/backend/.venv
services/backend/.venv/bin/python -m pip install -e 'services/backend[dev]'
```

Build the inputs, check coverage, and run the browser capture.

```sh
npm run check:site-routes -w web
npm run build -w web
npm run build:storybook -w web
for app_name in running stockmarket tcms judgment; do
  npm run build -w "apps/$app_name/frontend"
done
npm run capture:site -w web
```

Playwright starts a local static server and supplies fixture responses. Each capture writes a scene JSON and a comparison PNG under `.ap/site-design-capture/captures/`. Unknown API reads, page exceptions, and missing screen-specific probes fail capture tests.

Convert the captured scenes and render each native page for review.

```sh
services/backend/.venv/bin/python services/web/design-capture/build_documents.py \
  --routes services/web/design-capture/routes.json \
  --captures .ap/site-design-capture/captures \
  --output .ap/site-design-capture/documents \
  --svg-dir .ap/site-design-capture/native-svg
```

The output manifest lists document filenames, slugs, and hashes, route-to-page IDs, image assets, and visual features that require approximation. The index provides route coverage by chapter. The converter validates document sizes and authored node budgets, lays out all pages, and renders them with the UI Designer SVG painter. The documents, SVGs, and browser captures under `.ap/` are local build outputs.

## Publish to UI Designer

After reviewing the native SVGs, run the publisher with the target platform URL in `AP_SITE_CAPTURE_URL` and an authorized credential in `AGENT_PLATFORM_ADMIN` or `AP_MCP_TOKEN`.

```sh
services/backend/.venv/bin/python services/web/design-capture/publish_documents.py \
  --documents .ap/site-design-capture/documents \
  --base-url "$AP_SITE_CAPTURE_URL"
```

The publisher creates the documents and places their referenced assets. It writes receipts beside the generated manifest. Keep the receipts and manifest together when retrying the same publication. The publisher refuses to overwrite existing documents outside that publication or documents edited since it ran.

Document writes have server-side idempotency receipts. Artifact uploads do not: losing an upload response can leave an extra fixture image in Artifacts on retry. A new capture edition needs new document slugs and its own output directory so existing design edits remain intact.

## Refresh a screen

Add a route or redirect to [routes.json](routes.json) when a frontend route changes. For a new screen, choose a representative route path and a page-specific heading or content probe. Add deterministic data through the fixture helpers when the screen needs API responses. Root paths are relative to the local capture server.

Use `npm run capture:site -w web -- -g SCREEN-SLUG` to recapture an individual screen. Run the full capture whenever a shared shell, stylesheet, or fixture changes before assembling the collection.

The extractor and converter have focused checks.

```sh
npm exec -w web -- playwright test --config design-capture/extract.playwright.config.ts
services/backend/.venv/bin/python -m pytest -c /dev/null -p no:cacheprovider \
  services/web/design-capture/test_build_documents.py \
  services/web/design-capture/test_publish_documents.py
```

## What is editable

Each captured screen is a native Designer page with editable frames, text, shapes, and paths. Reference colors and typography use the namespaced platform tokens when the captured styles match. Identical shell sections within a chapter can use native component instances. Images and small canvas artwork remain image layers. Links between captured routes in the same chapter can open their corresponding Designer frames in prototype mode.

The captured geometry represents the dark theme at a 1440 × 1000 desktop viewport. The platform tokens include light values for design iteration. The scene uses fixture data and captures the displayed state, including scrollable document content up to 6,000 px. Browser-specific inputs, font fallback, CSS effects, and some SVG features can render differently. The manifest records the differences detected during conversion. The native pages support visual editing and review. App state changes and API-backed interactions run in the web application.
