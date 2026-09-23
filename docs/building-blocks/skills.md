# Skills

**What:** optional, reusable workflows an agent opts into via its `skills:`
list. The runner — the pod one agent run happens in, see the
[Glossary](glossary.md) — mounts each referenced skill into the pod
(`~/.claude/skills`), and the pod is granted the union of those skills'
secrets and nothing more.

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
  *.sh, *.py    # optional helper scripts the instructions reference
```

**Frontmatter shape** (example for a future workflow):

```yaml
name: release-review
description: Review a release candidate against its changelog and test evidence.
icon: 🧩
secrets:
  - name: example-readonly-source
    state: verified      # present | verified
    severity: optional  # required | optional
```

A bare string in `secrets:` is shorthand for `{state: present, severity:
optional}`. The strictness lives here — on the skill — because the skill knows
how badly it needs its credential; agents never restate it (see
[agents.md](agents.md) readiness).

**How to add one:** the **New skill** wizard on the Skills page interviews you
(purpose, when-to-use, optional credential) and fires a coding agent that
authors the skill — scaffolding `secrets/<name>/` too when a new credential is
involved — as a pull request under Changes. Or write the folder by hand and
open a PR. Existing skills are editable in place on the Skills page; every
save opens a PR on the skill's deterministic `coder/skill-<name>` branch.
