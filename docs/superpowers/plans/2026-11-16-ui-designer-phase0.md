# UI Designer Phase 0 build plan (design 46, T1–T9)

Worktree: ~/gh/ap-ui-designer, branch `ui-designer`. Backend venv:
`services/backend/.venv/bin/python -m pytest`. T0 has passed: both
runtimes can see `ImageContent` (§3.1). T0b, the desk-buddy census, waits
on Kyle's token. The schema can be adjusted later.

| Unit | Tasks | Who | Owns | Test command | Est. weekly % |
|---|---|---|---|---|---|
| U1 | T1 schema, ops, errors, grant constant | sonnet | `ui_designer/{schema,ops,errors}.py`, `agentspec.py`, tests | `pytest tests/test_ui_designer_schema.py tests/test_agentspec*.py -q` | 1.5 |
| U2 | T2 layout + text + fonts + benchmark | sonnet | `ui_designer/{layout,text,fonts}.py`, `fonts/` | `pytest tests/test_ui_designer_layout.py tests/test_ui_designer_fonts.py -q` | 2.5 |
| U3 | T3 store, tables, pruners | sonnet | `db.py` (additive), `ui_designer/store.py`, `pruning.py`, tests | `pytest tests/test_ui_designer_store.py -q` | 2 |
| U4 | T4 renderer service, painter, chart | sonnet | `services/ui-renderer/`, `ui_designer/{svg,render_client}.py`, chart template, values | `pytest services/ui-renderer tests/test_ui_designer_render.py -q`; `helm lint` | 2.5 |
| U5a | T5 auth seam: `require_ui_designer_access`, `place` checks, run-less snapshot refusal, `local_persona` ROUTES | opus (seam: authorization) | auth parts of `api/ui_designer.py`, `local_persona.py`, tests | `pytest tests/test_ui_designer_api.py -k "access or place or local" -q` | 1.5 |
| U5b | T5 the rest: routes, schemas, facade exclusions + counts, backup exclusion + export size check, helm values, SDK | sonnet | `api/{ui_designer,schemas,app}.py`, facade, `backup_export.py`, backup templates, `sdk/` | `pytest tests/test_ui_designer_api.py -q`; `cd services/mcp-facade && pytest -q`; SDK drift | 2 |
| U6 | T6 broker tool + T8 skill and docs | sonnet | `services/mcp-broker/broker.py`, its test, `skills/ui-designer/`, `docs/building-blocks/ui-designer.md` | `pytest services/mcp-broker -q -k ui_designer` | 1.5 |
| U7 | T7 web read-only | sonnet | `services/web/src/pages/UiDesigner*.tsx`, components, nav | `npm run build -w web \| tail -5` | 1.5 |
| main | T9 deploy + live check, grants | main | — | — | 2 |

Order: U1, then U2 ∥ U3, then U4, then U5a, then U5b, then U6 ∥ U7, then main (T9).

- **Full suites:** main runs the backend suite once after U5b and once before the deploy, in the background with `| tail -40`.
- **Reviews:** one Codex astra review of the whole diff before the deploy, with worst-case framing on auth, render and backups. Fixes go back to the implementing unit via SendMessage.
- **Gate:** the projected total is about 17% weekly (49% → ~66%). Kyle (2026-11-16): if we get close, write a handoff doc so Codex can finish the build. Stop at the next unit boundary once Claude weekly passes 70%, or if spend runs above 1.5× this estimate. The handoff goes to `docs/superpowers/plans/2026-11-16-ui-designer-codex-handoff.md`: it lists done and remaining units with their briefs, the branch state, the exact test commands, and the deploy steps.

## Progress
- [x] T0 image spike (2026-11-16)
- [ ] T0b desk-buddy census (needs Kyle's token)
