"""Agent readiness (docs/design/10 phase 2). An agent's dependency set is
DERIVED — its manifest's direct `secrets:` plus each of its skills' declared
secrets — never restated. A `required` dependency that isn't in its demanded
state blocks the agent: runs are rejected before dispatch with the exact
reason, instead of launching a pod that fails confusingly at runtime.

Evaluation is pure (deps + a status map in, verdict out); the dispatcher layers
try-before-block re-verification on top, the agents API just evaluates."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Dep:
    secret: str
    skill: str | None      # None = declared directly in the agent's manifest
    state: str             # "present" | "verified"
    severity: str          # "required" | "optional"


def deps_for(manifest, skill_store) -> list[Dep]:
    """The derived dependency set. Direct manifest secrets are required-present
    (the agent asked for the binding by name; a missing secret means the pod
    silently gets nothing). Skill secrets carry the skill's declared strictness."""
    deps = [Dep(s, None, "present", "required") for s in manifest.secrets]
    for skill_name in manifest.skills:
        info = skill_store.get(skill_name)
        if info is None or info.skill is None:
            continue
        for s in info.skill.secrets:
            deps.append(Dep(s.name, skill_name, s.state, s.severity))
    return deps


def met(dep: Dep, status: str) -> bool:
    if dep.state == "verified":
        return status == "valid"
    return status != "missing"


def unmet_required(manifest, skill_store, statuses: dict[str, str]) -> list[tuple[Dep, str]]:
    """(dep, current status) for every required dependency not in its demanded
    state. Unknown secrets count as missing."""
    out = []
    for d in deps_for(manifest, skill_store):
        st = statuses.get(d.secret, "missing")
        if d.severity == "required" and not met(d, st):
            out.append((d, st))
    return out


def reason(dep: Dep, status: str) -> str:
    why = {"missing": "is not set",
           "invalid": "failed verification"}.get(status, "is not verified")
    if dep.skill:
        return f"blocked: skill `{dep.skill}` disabled — secret `{dep.secret}` {why}"
    return f"blocked: secret `{dep.secret}` {why}"


def blocking_reason(manifest, skill_store, statuses: dict[str, str]) -> str | None:
    bad = unmet_required(manifest, skill_store, statuses)
    return reason(*bad[0]) if bad else None
