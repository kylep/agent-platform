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

The catalogue is currently empty. Workbench handles Git without handing
developer agents a GitHub credential; Studio's Codex artist uses its runtime's
built-in image generator. Project conversation lookup is provided in scoped
run context. Those are not skills an agent needs to select.

**Lives in:** git, one folder per skill:

```
skills/<name>/
  SKILL.md      # YAML frontmatter + usage instructions
  references/   # optional non-executable guidance when the skill needs it
```

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
