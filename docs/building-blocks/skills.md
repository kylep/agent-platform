# Skills

**What:** optional, reusable workflows an agent opts into via its `skills:`
list. The runner — the pod one agent run happens in, see the
[Glossary](glossary.md) — mounts each referenced skill into the harness's
skills directory. Assigning a skill never grants a secret or Tool.

A skill teaches a repeatable task spanning several steps, often shared by
multiple agents. It is not where an agent's job or personality lives: those
belong in its [definition](agents.md). A tool or app owns its argument schema,
endpoint details, and execution; the platform owns always-on behavior and
execution profiles. A [tool](tools.md) executes on the agent's behalf. When a
capability needs a credential the agent should never hold, it wants to be a
tool, not a skill. A rule is an obligation, not an optional skill.

The legacy `skills/` catalogue is currently empty. The reviewed
[`agent-platform-coding` plugin](../agent-platform-coding-plugin.md) supplies
`platform-orientation`, `platform-change`, and `platform-regression`; the coder
and QA agents use different subsets. Workbench handles Git without handing
developer agents a GitHub credential; Studio's Codex artist uses its runtime's
built-in image generator. Project conversation lookup is provided in scoped
run context. Those are not skills an agent needs to select.

**Lives in:** git, one folder per skill:

```
skills/<name>/
  SKILL.md      # YAML frontmatter + usage instructions
  references/   # optional non-executable guidance when the skill needs it
```

The coding plugin lives under `plugins/agent-platform-coding/`. Its release
manifest and platform build pin all skill bytes; the runner copies only the
assigned `SKILL.md` files into Claude Code or Codex. A missing or changed
assigned skill blocks a run before the model starts. Developer hosts install
the same package through their local plugin marketplaces; package installation
does not add platform Tool or secret grants.

**Frontmatter shape** (example for a future workflow):

```yaml
name: release-review
description: Review a release candidate against its changelog and test evidence.
icon: 🧩
```

The frontmatter rejects `secrets:`. Bind credentials explicitly on the agent
or its Tool, where their scope and readiness are visible. Existing agent
assignments and secret bindings can be inspected in the agent editor.

**How to add one:** the **New skill** wizard on the Skills page interviews you
(purpose and when-to-use) and fires a coding agent that authors the skill as a
pull request under Changes. Or write the folder by hand and
open a PR. Existing skills are editable in place on the Skills page; every
save opens a PR on the skill's deterministic `coder/skill-<name>` branch.
