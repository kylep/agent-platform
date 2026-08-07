"""linear tool: canned Linear operations + a raw GraphQL escape hatch.

Everything is a POST to /graphql; a personal API key goes in Authorization
with NO Bearer prefix. Linear returns HTTP 200 even for failed operations —
the errors array is passed through for the model to read.

Ported from the retired `linear` skill (curl recipes → trusted code).
"""
import json
import os
import re
import sys
import urllib.request

URL = "https://api.linear.app/graphql"
IDENT = re.compile(r"^([A-Za-z][A-Za-z0-9]*)-(\d+)$")


def gql(query: str, variables: dict | None = None) -> dict:
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        print("linear-api-key secret is not configured", file=sys.stderr)
        raise SystemExit(2)
    req = urllib.request.Request(
        URL, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def resolve_issue(identifier: str) -> dict:
    """ENG-123 → the issue node (id, team id) or exit with a clear error."""
    m = IDENT.match(identifier.strip())
    if not m:
        print(f"issue must be an identifier like ENG-123, got {identifier!r}", file=sys.stderr)
        raise SystemExit(2)
    key, number = m.group(1).upper(), int(m.group(2))
    d = gql("""query($key: String!, $number: Float!) {
        issues(filter: {team: {key: {eq: $key}}, number: {eq: $number}}, first: 1) {
          nodes { id identifier title team { id } state { name } } } }""",
        {"key": key, "number": number})
    nodes = (d.get("data") or {}).get("issues", {}).get("nodes", [])
    if not nodes:
        print(f"no issue {key}-{number} found (or the key can't see it)", file=sys.stderr)
        raise SystemExit(2)
    return nodes[0]


def act(args: dict) -> dict:
    action = args["action"]
    if action == "teams":
        return gql("""query { teams { nodes { id key name
            states { nodes { id name type } } } } }""")
    if action == "search":
        filters = ["{title: {containsIgnoreCase: $q}}", "{description: {containsIgnoreCase: $q}}"]
        team = ", team: {key: {eq: $team}}" if args.get("team_key") else ""
        q = f"""query($q: String!, $first: Int!{', $team: String!' if team else ''}) {{
            issues(filter: {{or: [{', '.join(filters)}]{team}}}, first: $first) {{
              nodes {{ identifier title state {{ name }} assignee {{ name }} url updatedAt }} }} }}"""
        variables = {"q": args.get("query") or "", "first": int(args.get("limit") or 20)}
        if team:
            variables["team"] = args["team_key"].upper()
        return gql(q, variables)
    if action == "create":
        if not args.get("team_key") or not args.get("title"):
            print("create needs team_key and title", file=sys.stderr)
            raise SystemExit(2)
        teams = gql("query($key: String!) { teams(filter: {key: {eq: $key}}) { nodes { id } } }",
                    {"key": args["team_key"].upper()})
        nodes = (teams.get("data") or {}).get("teams", {}).get("nodes", [])
        if not nodes:
            print(f"no team with key {args['team_key']!r}", file=sys.stderr)
            raise SystemExit(2)
        return gql("""mutation($input: IssueCreateInput!) {
            issueCreate(input: $input) { success issue { id identifier url } } }""",
            {"input": {"teamId": nodes[0]["id"], "title": args["title"],
                       "description": args.get("description") or None}})
    if action == "update":
        issue = resolve_issue(args.get("issue") or "")
        update: dict = {}
        if args.get("state"):
            states = gql("""query($team: ID) { workflowStates(filter: {team: {id: {eq: $team}}}) {
                nodes { id name } } }""", {"team": issue["team"]["id"]})
            match = [s for s in (states.get("data") or {}).get("workflowStates", {}).get("nodes", [])
                     if s["name"].lower() == args["state"].lower()]
            if not match:
                print(f"team has no workflow state named {args['state']!r}", file=sys.stderr)
                raise SystemExit(2)
            update["stateId"] = match[0]["id"]
        if args.get("title"):
            update["title"] = args["title"]
        if args.get("description"):
            update["description"] = args["description"]
        if not update:
            print("update needs at least one of state/title/description", file=sys.stderr)
            raise SystemExit(2)
        return gql("""mutation($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
              success issue { identifier state { name } title } } }""",
            {"id": issue["id"], "input": update})
    if action == "comment":
        issue = resolve_issue(args.get("issue") or "")
        if not args.get("body"):
            print("comment needs body", file=sys.stderr)
            raise SystemExit(2)
        return gql("""mutation($input: CommentCreateInput!) {
            commentCreate(input: $input) { success comment { id } } }""",
            {"input": {"issueId": issue["id"], "body": args["body"]}})
    if action == "raw_graphql":
        if not args.get("query"):
            print("raw_graphql needs query", file=sys.stderr)
            raise SystemExit(2)
        return gql(args["query"], args.get("variables") or {})
    print(f"unknown action {action!r}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    print(json.dumps(act(json.load(sys.stdin))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
