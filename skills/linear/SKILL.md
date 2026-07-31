---
name: linear
description: Create, query, and update issues, projects, and comments in Linear via its GraphQL API. Use when asked to track a task or project, manage a to-do list, or otherwise interact with Linear.
icon: 📋
secrets:
  - name: linear-api-key
    state: present        # no read-only GET endpoint exists to verify against
    severity: required    # Linear IS this skill's whole purpose
---
# linear

Read and write Linear issues, projects, and comments through Linear's GraphQL
API. The API key is a bound secret (`linear-api-key`); treat it as sensitive.

Everything — reads included — is a single `POST` to
`https://api.linear.app/graphql` with a JSON body of `{"query": ..., "variables": ...}`.
For a **personal API key** (what this secret holds), the `Authorization`
header is the raw key with no `Bearer` prefix.

Always build the request body in a file with `jq` (never inline a query/variable
that might contain quotes or user text), and always check the response's
`errors` field — Linear returns HTTP 200 even when a query/mutation fails.

```bash
run_query() {  # $1 = GraphQL query/mutation, $2 = variables JSON (or '{}')
  jq -n --arg q "$1" --argjson v "$2" '{query: $q, variables: $v}' > /tmp/linear_req.json
  curl -s -X POST "https://api.linear.app/graphql" \
    -H "Authorization: $LINEAR_API_KEY" \
    -H "Content-Type: application/json" \
    -d @/tmp/linear_req.json
}
```

## Common operations

Who am I / sanity check:
```bash
run_query 'query { viewer { id name email } }' '{}'
```

List teams (you need a team's `id` to create issues in it):
```bash
run_query 'query { teams { nodes { id key name } } }' '{}'
```

Find issues (filter by team key, state, assignee, etc. — adjust the filter as needed):
```bash
run_query 'query($teamKey: String!) {
  issues(filter: { team: { key: { eq: $teamKey } } }, first: 50) {
    nodes { id identifier title state { name } assignee { name } url }
  }
}' '{"teamKey": "ENG"}'
```

Create an issue:
```bash
run_query 'mutation($teamId: String!, $title: String!, $desc: String) {
  issueCreate(input: { teamId: $teamId, title: $title, description: $desc }) {
    success
    issue { id identifier url }
  }
}' '{"teamId": "<team-id>", "title": "Fix the thing", "desc": "Details here."}'
```

List a team's workflow states (needed to move an issue between states — Linear
has no "todo/in-progress/done" enum, states are per-team objects):
```bash
run_query 'query($teamId: String!) {
  workflowStates(filter: { team: { id: { eq: $teamId } } }) {
    nodes { id name type }
  }
}' '{"teamId": "<team-id>"}'
```

Update an issue (e.g. move to a new state, reassign, change title):
```bash
run_query 'mutation($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue { id identifier state { name } }
  }
}' '{"id": "<issue-id>", "stateId": "<workflow-state-id>"}'
```

Add a comment to an issue:
```bash
run_query 'mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}' '{"issueId": "<issue-id>", "body": "Comment text here."}'
```

## Notes

- Look up ids (team, issue, workflow state, user) before mutating — Linear's
  mutations take ids, not names; resolve names via a query first.
- Paginate with `first`/`after` and the `pageInfo { hasNextPage endCursor }`
  field on any `nodes` connection; don't assume the first page is everything.
- A 200 response can still carry a top-level `errors` array — always inspect
  it and surface the message rather than assuming success.
- Rate limits apply (HTTP 429, or a rate-limit error in the GraphQL response);
  back off and retry once.
- Never echo `$LINEAR_API_KEY` into transcript output — it is a credential.
