"""memory tool: namespaced agent memory in the tool's own pg schema.

The namespace is TOOL_CALLER_AGENT — broker-verified identity, never a model
argument, which is the entire isolation boundary. Storage is the
tool_memory.memories table (created by the platform's init_db — the admin UI
reads the same table via the ORM; this tool's provisioned role has row
privileges on it). Semantics mirror the retired /api/memories agent surface:
key upsert = overwrite-in-place, term-AND search, newest first.
"""
import json
import hashlib
import os
import re
import sys
import uuid
from datetime import timezone

TABLE = "tool_memory.memories"
LOCAL_PERSONA_PROTOCOL = 1


def view(row) -> dict:
    rid, key, content, tags, created, updated, team_id, project_id = row
    out = {"id": rid, "key": key, "content": content,
            "tags": tags if isinstance(tags, list) else json.loads(tags or "[]"),
            "team_id": team_id, "project_id": project_id,
            "created_at": created.isoformat(), "updated_at": updated.isoformat()}
    # Same identity as retrieval's source_hash; no historical content is kept.
    state = {k: out[k] for k in ('id', 'key', 'content', 'tags', 'updated_at')}
    # asyncpg canonical readers return UTC; psycopg uses session TimeZone.
    # Normalize the hash only so both transports accept the same token.
    if updated.tzinfo is not None:
        state['updated_at'] = updated.astimezone(timezone.utc).isoformat()
    out['revision'] = hashlib.sha256(json.dumps(state, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return out


def read(cur, agent: str, q: str, limit: int,
         team_id: str | None = None, project_id: str | None = None) -> list[dict]:
    conds, params = ["agent = %s"], [agent]
    if team_id:
        conds.append("team_id = %s")
        params.append(team_id)
    if project_id:
        conds.append("project_id = %s")
        params.append(project_id)
    for term in (q or "").split():
        conds.append("(lower(content) LIKE %s OR lower(coalesce(key, '')) LIKE %s)")
        needle = f"%{term.lower()}%"
        params += [needle, needle]
    cur.execute(
        f"SELECT id, key, content, tags, created_at, updated_at, team_id, project_id FROM {TABLE} "
        f"WHERE {' AND '.join(conds)} ORDER BY updated_at DESC LIMIT %s",
        params + [max(1, min(int(limit or 50), 200))])
    return [view(r) for r in cur.fetchall()]


def save(cur, agent: str, args: dict, team_id: str | None = None,
         project_id: str | None = None, *, previous: list | None = None) -> dict:
    content = args.get("content")
    if not content:
        print("save needs content", file=sys.stderr)
        raise SystemExit(2)
    if "\x00" in content or "\x00" in (args.get("key") or ""):
        print("content/key must not contain NUL bytes", file=sys.stderr)
        raise SystemExit(2)
    from psycopg.types.json import Json
    key = args.get("key") or None
    expected = args.get('expected_revision')
    if expected is not None and (not key or not isinstance(expected, str) or
            not re.fullmatch(r'absent|[0-9a-f]{64}', expected)):
        print("expected_revision requires a key and 'absent' or a current 64-character revision", file=sys.stderr)
        raise SystemExit(2)
    tags = Json(args.get("tags") or [])
    def locked():
        cur.execute(f"SELECT id, key, content, tags, created_at, updated_at, team_id, project_id FROM {TABLE} "
                    "WHERE agent = %s AND key = %s FOR UPDATE", (agent, key))
        return cur.fetchone()
    def check(row):
        current = view(row) if row else None
        if previous is not None:
            previous[:] = [current]
        if expected is not None and expected != (current['revision'] if current else 'absent'):
            return {'ok': False, 'error': {'code': 'memory_revision_conflict',
                    'expected_revision': expected,
                    'current_revision': current['revision'] if current else None}}
    row = locked() if key else None
    error = check(row)
    if error:
        return error
    if row:
        current = view(row)
        if expected is not None and (current['content'], current['tags'], current['team_id'], current['project_id']) == (
                content, args.get('tags') or [], team_id, project_id):
            return current
    if row:
        cur.execute(
            f"UPDATE {TABLE} SET content = %s, tags = %s, team_id = %s, project_id = %s, updated_at = now() "
            "WHERE agent = %s AND key = %s "
            "RETURNING id, key, content, tags, created_at, updated_at, team_id, project_id",
            (content, tags, team_id, project_id, agent, key))
        row = cur.fetchone()
        if row:
            return view(row)
    cur.execute(
        f"INSERT INTO {TABLE} (id, agent, key, content, tags, team_id, project_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now()) "
        "ON CONFLICT (agent, key) WHERE key IS NOT NULL DO NOTHING "
        "RETURNING id, key, content, tags, created_at, updated_at, team_id, project_id",
        (uuid.uuid4().hex, agent, key, content, tags, team_id, project_id))
    row = cur.fetchone()
    if row:
        return view(row)
    # A concurrent insertion won the unique-key race. Conditional creation
    # conflicts; legacy unconditional saves can overwrite under the row lock.
    row = locked()
    error = check(row)
    if error:
        return error
    if row is None:
        raise RuntimeError('memory changed during insert; retry the save')
    cur.execute(f"UPDATE {TABLE} SET content=%s, tags=%s, team_id=%s, project_id=%s, updated_at=now() "
                "WHERE agent=%s AND key=%s RETURNING id,key,content,tags,created_at,updated_at,team_id,project_id",
                (content, tags, team_id, project_id, agent, key))
    return view(cur.fetchone())


def main() -> int:
    args = json.load(sys.stdin)
    if args.get('mode') == 'hybrid':
        print('hybrid retrieval requires the trusted broker/API path', file=sys.stderr)
        return 2
    agent = os.environ.get("TOOL_CALLER_AGENT", "").strip()
    if not agent:
        print("no verified caller identity — memory is namespaced and cannot "
              "run anonymously", file=sys.stderr)
        return 2
    url = os.environ.get("TOOL_DB_URL", "")
    if not url:
        print("memory database is not provisioned yet (tool-memory-db secret "
              "missing)", file=sys.stderr)
        return 2

    import psycopg
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT team_id, project_id FROM public.memory_run_scope(%s, %s)",
                    (os.environ.get("TOOL_RUN_ID", ""), agent))
        scope = cur.fetchone()
        if scope is None:
            print("memory run authority is unavailable", file=sys.stderr)
            return 2
        if args["action"] == "read":
            if ((args.get("current_team") and not scope[0]) or
                    (args.get("current_project") and not scope[1])):
                print("[]")
                return 0
            print(json.dumps(read(cur, agent, args.get("q") or "",
                                  args.get("limit") or 50,
                                  scope[0] if args.get("current_team") else None,
                                  scope[1] if args.get("current_project") else None)))
        elif args["action"] == "save":
            # Scope, locked precondition, mutation and provenance share one
            # transaction. Failed preconditions never record a mutation.
            previous = []
            out = save(cur, agent, args, *scope, previous=previous)
            if out.get('ok') is False:
                print(json.dumps(out))
                return 0
            before = previous[0]
            from psycopg.types.json import Jsonb
            fields = ("key", "content", "tags", "team_id", "project_id")
            # Capability sync may precede the backend rollout. Old backends
            # cannot create local sessions, so ordinary cluster writes may
            # continue until the additive provenance function is provisioned.
            cur.execute("SELECT to_regprocedure('public.local_persona_memory_mutation(text,text,text,jsonb,jsonb)')")
            if cur.fetchone()[0] and (before is None or any(before[k] != out[k] for k in fields)):
                cur.execute("SELECT public.local_persona_memory_mutation(%s,%s,%s,%s,%s)",
                            (os.environ.get("TOOL_RUN_ID", ""), agent, out["id"],
                             Jsonb({k: before[k] for k in fields}) if before else None,
                             Jsonb({k: out[k] for k in fields})))
            conn.commit()
            print(json.dumps(out))
        else:
            print(f"unknown action {args['action']!r}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
