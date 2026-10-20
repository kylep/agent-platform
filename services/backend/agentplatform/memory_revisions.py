"""Optional compare-and-replace contract; tokens identify current source state.

Call check_revision only while holding the row lock in the write transaction.
Missing-row inserts must also handle the unique-key race without overwriting.
This deliberately reuses retrieval's source hash, without creating history.
"""
from typing import Annotated

from pydantic import Field

from agentplatform.persona_memory import source_hash

ExpectedRevision = Annotated[str, Field(pattern=r'^(absent|[0-9a-f]{64})$')]


def conflict(expected, current):
    return {'code': 'memory_revision_conflict', 'expected_revision': expected,
            'current_revision': source_hash(current) if current is not None else None}


def check_revision(expected, current):
    return (expected is None or
            (expected == 'absent' and current is None) or
            (current is not None and expected == source_hash(current)))
