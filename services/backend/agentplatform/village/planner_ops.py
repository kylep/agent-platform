"""Front-door validation for `edit_queue` (plan §5.2).

The closed op schema lives in contract.py; this module applies it per op so one
bad op never costs the rest, adds the whole-call caps, and refuses ops that name
steps which finished (or never existed) since the planner's `base_version`.
Valid ops are what commands.py journals; refusals go back in the next `wait`.
"""
from dataclasses import dataclass, field
from typing import Any

from agentplatform.village import contract as c

FINISHED = ("done", "failed", "skipped")
# ops that name an existing queue step, and the key that holds its id
STEP_REF = {"insert_after": "after", "replace": "step_id", "remove": "step_id",
            "clear_from": "step_id"}


@dataclass
class EditPlan:
    valid: list[dict[str, Any]] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)   # {index, op, reason}
    request_id: str | None = None
    base_version: int | None = None

    @property
    def ok(self) -> bool:
        return bool(self.valid)


def _refuse(plan: EditPlan, index: int, op: Any, reason: str) -> None:
    name = op.get("op") if isinstance(op, dict) else None
    plan.refused.append({"index": index, "op": name, "reason": reason})


def prepare_edit(args: Any, queue: list[dict[str, Any]] | None = None) -> EditPlan:
    """Validate one `edit_queue` call. `queue` is the character's current steps
    (dicts with `id` and `state`); without it staleness is not checked (the
    engine re-checks when it applies)."""
    plan = EditPlan()
    if not isinstance(args, dict):
        _refuse(plan, -1, None, "edit_queue needs request_id, base_version and ops")
        return plan
    rid, base, ops = args.get("request_id"), args.get("base_version"), args.get("ops")
    plan.request_id = rid if isinstance(rid, str) else None
    plan.base_version = base if isinstance(base, int) and not isinstance(base, bool) else None
    if not isinstance(ops, list):
        _refuse(plan, -1, None, "$.ops: must be array")
        return plan
    # Everything except the ops themselves (those are checked one by one).
    head = [e for e in c.check(c.ACTION_ARGS["edit_queue"], {**args, "ops": [{"op": "say", "line": "x"}]})
            if not e.startswith("$.ops")]
    if head:
        _refuse(plan, -1, None, "; ".join(head))
        return plan
    if not ops:
        _refuse(plan, -1, None, "$.ops: needs at least 1 item(s)")
        return plan
    cap = c.LIMITS["ops_per_edit"]
    if len(ops) > cap:
        for i in range(cap, len(ops)):
            _refuse(plan, i, ops[i], f"at most {cap} ops per call")
        ops = ops[:cap]
    by_id = {s.get("id"): s for s in (queue or []) if s.get("id")}
    steps_left = c.LIMITS["steps_per_edit"]
    for i, op in enumerate(ops):
        errs = c.check(c.OP_SCHEMA, op, f"ops[{i}]")
        if errs:
            _refuse(plan, i, op, "; ".join(errs))
            continue
        ref_key = STEP_REF.get(op["op"])
        if queue is not None and ref_key:
            step = by_id.get(op[ref_key])
            if step is None:
                _refuse(plan, i, op, f"no step called {op[ref_key]!r} in the list")
                continue
            if step.get("state") in FINISHED:
                _refuse(plan, i, op, "that step already finished")
                continue
        n = len(op.get("steps") or [])
        if n > steps_left:
            _refuse(plan, i, op, f"too many steps in one call (limit {c.LIMITS['steps_per_edit']})")
            continue
        steps_left -= n
        plan.valid.append(op)
    return plan
