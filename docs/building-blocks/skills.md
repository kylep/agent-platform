# Skills

**What:** reusable capabilities an agent opts into via its manifest `skills:`
list. The runner — the pod one agent run happens in, see the
[Glossary](glossary.md) — mounts each referenced skill into the pod
(`~/.claude/skills`), and the pod is granted the union of those skills'
secrets and nothing more.

A skill is *knowledge*: instructions (and optional helper scripts) the agent
reads and follows itself, so using one means the agent needs the underlying
access. A [tool](tools.md) is *execution* by the platform on the agent's
behalf. When a capability needs a credential the agent should never hold,
it wants to be a tool, not a skill.

**Lives in:** git, one folder per skill:

```
skills/<name>/
  SKILL.md      # YAML frontmatter + usage instructions
  *.sh, *.py    # optional helper scripts the instructions reference
```

**Frontmatter shape** (this is the shipped `git` skill):

```yaml
name: git
description: Clone, branch, commit, and push over HTTPS… (written as a when-to-use trigger)
icon: 🔀
secrets:
  - name: github-token
    state: verified      # present | verified   — what must be true of the secret
    severity: required   # required | optional  — required blocks the agent, optional degrades
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
