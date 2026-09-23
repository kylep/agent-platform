"""memory tool: namespaced agent memory in the tool's own pg schema.

The namespace is TOOL_CALLER_AGENT — broker-verified identity, never a model
argument, which is the entire isolation boundary. Storage is the
tool_memory.memories table (created by the platform's init_db — the admin UI
reads the same table via the ORM; this tool's provisioned role has row
privileges on it). Semantics mirror the retired /api/memories agent surface:
key upsert = overwrite-in-place, term-AND search, newest first.
"""
import json
import os
import sys
import uuid

TABLE = "tool_memory.memories"


def view(row) -> dict:
    rid, key, content, tags, created, updated, team_id, project_id = row
    return {"id": rid, "key": key, "content": content,
            "tags": tags if isinstance(tags, list) else json.loads(tags or "[]"),
            "team_id": team_id, "project_id": project_id,
            "created_at": created.isoformat(), "updated_at": updated.isoformat()}


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
         project_id: str | None = None) -> dict:
    content = args.get("content")
    if not content:
        print("save needs content", file=sys.stderr)
        raise SystemExit(2)
    if "\x00" in content or "\x00" in (args.get("key") or ""):
        print("content/key must not contain NUL bytes", file=sys.stderr)
        raise SystemExit(2)
    from psycopg.types.json import Json
    key = args.get("key") or None
    tags = Json(args.get("tags") or [])
    if key:
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
        "RETURNING id, key, content, tags, created_at, updated_at, team_id, project_id",
        (uuid.uuid4().hex, agent, key, content, tags, team_id, project_id))
    return view(cur.fetchone())


def main() -> int:
    args = json.load(sys.stdin)
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
        scope = cur.fetchone() or (None, None)
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
            out = save(cur, agent, args, *scope)
            conn.commit()
            print(json.dumps(out))
        else:
            print(f"unknown action {args['action']!r}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
