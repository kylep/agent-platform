# UI Designer Phase 1 + desk-buddy import (design 46, T10 + T12-lite)

Worktree ~/gh/ap-ui-designer, branch `ui-designer` (= main 9bd7bb6). Kyle
(2026-11-16): "finish up the design ui tool. Once you're done, write a feature
wishlist and prompt i can feed to codex to flesh it out". Figma is read through
the Figma MCP in Kyle's Claude session (no PAT), so T11's token/executor/job
importer moves to the Codex wishlist; the converter is built here and reused.

| Unit | Tasks | Who | Owns | Test command | Est. weekly % |
|---|---|---|---|---|---|
| A | T10 backend: inspect, comments, history diff, export (PNG artifact), `ui_designer.events` + SSE + `#design` seed/cards, facade pins, broker actions, SDK | sonnet | `ui_designer/*`, `api/ui_designer.py`, `events.py`, `db.py` (additive), facade + tests, broker + test, `sdk/`, skill/docs | `pytest tests/test_ui_designer_api.py -q`; `pytest services/mcp-broker -q -k ui_designer`; facade `pytest -q` | 3 |
| B | T10 web: comments panel, compare (two versions), label, export button, SSE live refresh | sonnet | `services/web/src/pages/UiDesigner*.tsx`, `components/ui-designer/`, `lib/uiDesigner.ts` | `npm run build -w web \| tail -5` | 1.5 |
| C | Converter + desk-buddy import via Figma MCP | sonnet | `ui_designer/figma_convert.py`, `tests/test_figma_convert.py`, `tests/fixtures/figma/`, scratch import script | `pytest tests/test_figma_convert.py -q` | 2.5 |
| D | Figma-equivalent effects: stacked fills, angular/diamond gradients, multi/inner shadows, blur, backdrop blur, blend, stroke dash/cap/join/sides | sonnet | `ui_designer/schema.py`, `svg.py`, `layout.py`, `services/ui-renderer/`, `UI_DESIGNER_CHEATSHEET`, `tests/test_ui_designer_effects.py`, building-block doc | `pytest tests/test_ui_designer_schema.py tests/test_ui_designer_render.py tests/test_ui_designer_effects.py -q`; `pytest services/ui-renderer -q` | 1.5 |
| main | review, full suite, deploy, live check, wishlist + Codex prompt | main | — | — | 2 |

- **Full suites:** main, once after A–C, background, `| tail -40`.
- **Reviews:** one Codex astra review of the whole diff before deploy; fixes via SendMessage to the owning unit.
- **Gate:** 53% → ~62% weekly. Stop and write a handoff if Claude weekly passes 70%.

## Result (2026-11-16)
- Deployed helm rev 145 (backend ud-4bf8159; broker, web, facade, renderer ud-25dc85e).
- Live: inspect, comments, diff, label, export (artifact + #design card), SSE `design` events all verified on pai.
- desk-buddy v3 "figma import": 0 approximated, 0 unmapped; mean abs diff vs Figma 16/255 (remaining: one later hand-edit to the Play card, kerning).
- Codex astra review: 8 findings, all fixed; commit security hook: 4 findings, all fixed.
- Figma MCP on Starter ran out after 3 calls; the dump was rebuilt by replaying the original build scripts.
