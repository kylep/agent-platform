# 40 — Human accounts and groups

Status: **design, reviewed, awaiting Kyle's go** (2026-10-03). Branch
`worktree-users-and-google-login`. Reviewed by Fable 5.1, Sonnet 5.5, Codex
gpt-6-sol and gpt-6-astra; their findings and what changed are in "Review
log". Google sign-in was dropped (see "Rejected").

## Goal

The platform has one human: the admin. Milestone 1 lets other people have an
account that can sign in and see that they are signed in, and **nothing
else**. Access is granted later, per group. The admin keeps its username
(`admin`), its password and all of its access.

Out of scope: making the platform multi-user. Agents, runs, Relay, Apps,
memories and "Kyle" assumptions stay single-user. This milestone is a gate in
front of them, not a rewrite of them.

## Decisions (Kyle, 2026-10-03)

| # | Decision |
|---|---|
| D1 | The signed-out page offers **Sign in** and **Register**. |
| D2 | Register = username, password, confirm password. Any non-empty password. The LAN is the protection (no rate limit, no complexity rules). |
| D3 | Passwords stored salted and hashed (argon2id, as today). |
| D4 | After registering, the user is signed in and lands on **their profile page**. |
| D5 | The admin can switch registration off; on by default. |
| D6 | Users change their own password: current, new, confirm. Forgotten ⇒ admin resets. |
| D7 | Admin sees, deletes and resets passwords for accounts. Reset = a modal with new + confirm. |
| D8 | **Groups** — "group" in code *and* UI, because `role` already means the permission tier. A group has a name (editable) and a hidden uuid. |
| D9 | A user has at most one group; none is the default. Groups grant nothing yet. |
| D10 | Deleting a group puts its users back to none. A group id that resolves to nothing fails **shut** to none and is cleared. |
| D11 | Two kinds of platform user: **system** users (defined in code — `admin`, `qa`; listed, not modifiable) and **state** users (database rows, CRUD-able). `qa` may migrate to state later. |
| D12 | No disable/enable. |
| D13 | Sessions last 30 days; a password change or reset signs out the account's other sessions; delete signs out everywhere. |
| D14 | Admin is outside groups and can do anything. Its 8-char minimum on its own password change stays. |

## Current state (verified in code, 2026-10-03)

- `principals(id, name unique, role, password_hash)`. Rows: `admin`
  (`/api/setup`), `qa` (`qaprincipal.py`, role `reader`). API keys are
  `api_keys`, whose principal string is the key's *name* (free text today,
  `api/apikeys.py:44`); agent identities are `sa:<agent>` / `agent:<agent>`.
- `POST /api/login {principal="admin", password}` sets `ap_session` = signed
  `{"principal": name}`. **No expiry, no server record**; logout only deletes
  the cookie. No `Secure` flag. The web UI has **no sign-out control**.
- Authorization is an explicit per-endpoint allow-list of roles
  (`require_role`); admin passes all. A role named in no list gets 403 from
  every `require_role` route.
- **~25 call sites only test `authenticate(request) is not None`**; `tail.py`
  and `live_invocations._interactive` parse the cookie themselves;
  `/api/setup-state` lists secrets to any authenticated caller.
- **Holes found in review, present on `main` today:**
  - `POST /api/projects` (`api/scopes.py:222`) has **no auth at all**.
  - The help routes (`api/help.py`) need no auth and serve docs and tool
    descriptions to anyone.
  - `/api/change-password` takes any admin credential — an admin API key, so
    also the MCP facade — and isn't in the facade's `EXCLUDED_PATHS`.
- Migration = `create_all` + `_ensure_columns` (nullable `ADD COLUMN`) +
  bespoke idempotent `_ensure_*` DDL under a postgres advisory lock.
- The MCP facade exposes API routes as tools from OpenAPI minus
  `EXCLUDED_PATHS`; `sdk/` is regenerated from the spec, and CI fails on
  drift.

## Design

### Data model

`principals` stays the one identity table, so every `Principal.name` lookup
keeps working. Additions:

```
principals            (existing; new nullable columns)
  id            str32 uuid hex      existing PK — sessions bind to THIS
  name          str unique          admin | qa | user:<username>
  role          str                 admin | reader | user   (permission tier)
  password_hash str|null            argon2id (salt + params in the string)
  group_id      str32|null          → user_groups.id ON DELETE SET NULL
  created_at    timestamptz|null    "member since"

user_groups
  id            str32 uuid4 hex     PK
  name          str(64)             unique on lower(name)
  created_at    timestamptz

login_sessions
  id_hash       str64 PK            sha256 of the random id the cookie holds
  principal_id  str32 index         → principals.id ON DELETE CASCADE
  created_at, expires_at, revoked_at|null

platform_settings
  key str(64) PK, value JSON, updated_at      ('registration_open')
```

- **Kinds**: *system* = name in `SYSTEM_PRINCIPALS = ("admin", "qa")`, one
  constant in `auth.py`. *State* = `role == "user"`. There is no `origin`
  column; there's one source of truth for each. Any other row is shown as
  system (read-only).
- **Username**: one constant, `USERNAME_RE = ^[a-z0-9][a-z0-9_-]{0,63}$`,
  used by both register **and** login (`Creds.principal`; `admin` and `qa`
  still match). Input is lowercased. Stored only as the principal name
  `user:<username>`, so uniqueness is the existing `principals.name`
  constraint. Reserved at register: `SYSTEM_PRINCIPALS` (UX only — the prefix
  already prevents collision).
- **API-key names may not start with `user:`** (`apikeys.py` validation). The
  migration logs any existing key that does; there should be none.
- **Passwords**: `argon2.PasswordHasher()` defaults, any non-empty string ≤
  1024 chars. No rehash-on-login path.
- **Group FK**: `_ensure_accounts_ddl` (idempotent, `SchemaMark`
  `accounts-v1`, postgres only) first nulls dangling `group_id`s, then adds
  `FOREIGN KEY ... ON DELETE SET NULL`, plus the `login_sessions` FK with
  `ON DELETE CASCADE` and the `lower(name)` unique index on `user_groups`.
  Group resolution is a LEFT JOIN; a miss reads as none (sqlite, or anything
  the FK didn't catch). Only the rare dangling case writes — it clears the
  id — so reads stay reads otherwise.

### Sessions

- Cookie `ap_session` = itsdangerous-signed `{"sid": <32-byte urlsafe>}`;
  only `sha256(sid)` is stored. Signature is checked before any DB read.
- One async `resolve_session(app, cookie) -> (Principal, sid) | None`: **one
  query** joining `login_sessions` ⋈ `principals` on id, filtering
  `revoked_at IS NULL AND expires_at > now()`. It replaces the sync
  `validate_session_cookie` (deleted) everywhere: `authenticate`, `tail.py`,
  and `live_invocations._interactive`, which now checks
  `request.state.auth_kind == "session"`.
- `max_age` 30 days absolute, `HttpOnly`, `SameSite=Lax`, `Secure` behind
  `AP_SESSION_COOKIE_SECURE` (**default off**; on would break `http://pai:8090`).
- Old-format cookies are refused ⇒ everyone, admin included, signs in once
  after deploy.
- **Revocation and races.** Sessions bind to `principals.id`, so a deleted
  and re-registered username never inherits old sessions. Login verifies the
  password, then in one transaction locks the row (`SELECT ... FOR UPDATE`
  on postgres), re-checks that `password_hash` still equals the hash it
  verified, and inserts the session. Reset, change and delete take the same
  row lock. An in-flight login can therefore never land a session after a
  reset.
- Logout revokes this session. A password change (user `/api/me/password`
  **and** admin `/api/change-password`) revokes the account's *other*
  sessions. Admin reset revokes all of them; delete cascades.
- **Long-lived streams.** `tail` and the SSE feeds (relay, tickets, wiki,
  quota, artifacts, workbench) re-run `resolve_session` at most every 60 s
  while open, and close on a miss. One shared helper, `still_signed_in(app,
  cookie)`, holds a 60-second cache.
- Pruning: a `SessionPruner` in `pruning.py` (daily) deletes rows that
  expired or were revoked more than 7 days ago.

### The fence (central deny)

`authenticate()` resolves the caller as today. If the result has
`role == "user"` — whatever `auth_kind` it came in as — and the path is not
in `USER_PATHS`, it raises **403**. `API_KEY_ROLES` keeps excluding `user`,
so no key can carry it.

```
USER_PATHS = /api/me, /api/me/password, /api/logout, /api/setup-state
```

- `setup-state` returns `secrets: []` to a `user`.
- `tail` closes with 4403 for a `user`.
- **Optional-auth routes** (webhooks try the platform identity first, then
  the per-path secret): a request carrying a `user` cookie now gets 403
  before the secret is checked. Acceptable — webhook callers are external
  services, not browsers — and pinned by a test so it's a known behavior.
- **Pre-existing holes closed in this milestone:** `POST /api/projects` →
  `require_admin`; help routes → any non-`user` authenticated role (the
  console needs them for `reader`/`qa`); `/api/change-password` → admin
  *browser session* only, and added to the facade's `EXCLUDED_PATHS`.
- When groups gain grants later, they widen `USER_PATHS` per group; the
  fence stays the one place that decides.

### API

| Method & path | Who | Notes |
|---|---|---|
| `POST /api/login {principal, password}` | anyone | Tries `name`, then `user:<name>`. One 401 for every failure, with the existing dummy-hash timing. |
| `GET /api/register` | anyone | `{open: bool}` — the login page hides the tab when closed. |
| `POST /api/register {username, password, confirm}` | anyone, while open | 403 closed, 409 taken/reserved, 422 mismatch/empty/bad name. Signs in. |
| `POST /api/logout` | session | Revokes it. |
| `GET /api/me` | session only | `{id, username, role, kind: system\|state, group: {id,name}\|null, created_at}` |
| `POST /api/me/password {current, new, confirm}` | state-user session | 403 wrong current, 422 mismatch/empty. |
| `POST /api/change-password` | admin session | Unchanged contract; now session-only + revokes others. |
| `GET /api/users` | admin session | `UserOut` model only (never the hash): `{id, username, kind, group, created_at}`. |
| `POST /api/users/{id}/password {password, confirm}` | admin session | State users only (404 otherwise); revokes all. |
| `PUT /api/users/{id}/group {group_id\|null}` | admin session | 422 unknown group. |
| `DELETE /api/users/{id}` | admin session | State users only. |
| `GET/POST /api/groups`, `PATCH/DELETE /api/groups/{id}` | admin session | Name only; 409 duplicate (case-insensitive); list includes member count. |
| `GET/PUT /api/settings/registration` | admin session | `{open: bool}`. |

- Accounts are addressed by **principal id**.
- "Admin session" = `require_admin` **and** `auth_kind == "session"`. An
  admin API key, the MCP facade and agents can't manage people or the admin
  password.
- Every route above goes into the facade's `EXCLUDED_PATHS`. `sdk/` is
  regenerated once, at the end of the backend phase.

### Web

- **`api.ts`**: `isAuthCall` adds `/api/me`, `/api/register` and
  `/api/logout`. A 401 from those never hard-navigates.
- **`Gate`**: `/login`, `/setup` and `/profile` handle their own state. On
  every other path, Gate fetches `/api/me` alongside `/api/setup-state`:
  - 401 ⇒ redirect to `/login`, with no reload loop.
  - `role == "user"` ⇒ redirect to `/profile`.
  - Anything else ⇒ render as today.
- **`/login`**: tabs *Sign in* (username, password) and *Register* (username,
  password, confirm; hidden when closed). Errors are shown inline. The admin
  lands on `/`; users land on `/profile`.
- **`/profile`**: standalone auth-page layout with no sidebar. Shows
  username, group (or "None"), member since, the change-password form, and
  **Sign out**. For the admin it links to Settings for its own password.
- **Sidebar** (admin): *Users* and *Groups* under Settings; *Profile* and
  *Sign out* at the bottom.
- **`/settings/users`**: one table.
  - System rows: a "system" chip and no actions.
  - State rows: a group dropdown, *Reset password* (modal: new, confirm,
    submit) and *Delete* (confirm dialog).
  - A registration toggle in the header.
- **`/settings/groups`**: list with member counts, create, inline rename,
  and delete (the confirm says how many users drop to none).
- Built from `@ap/ui` primitives only.

### Tests

**Backend**, `tests/test_accounts.py` (sqlite):
- register, login, logout, me
- old-format cookie refused; expired and revoked sessions refused
- password change revokes others but not self, for both user and admin
- reset and delete revoke
- login racing a reset never yields a live session; the row-lock path is
  exercised via the hash re-check
- digit-leading username registers **and** logs in; reserved and duplicate
  usernames refused; registration closed ⇒ 403
- group CRUD, duplicate name, delete ⇒ none
- a dangling `group_id` reads as none and is cleared
- admin API key refused on people routes and on `/api/change-password`
- `user:`-prefixed key name refused
- webhook with a `user` cookie ⇒ 403 (pinned)
- tail and an SSE feed close after the session is revoked (60 s cache
  patched to 0)

**The route walk** iterates `app.openapi()["paths"]`. The first draft walked
`app.routes`, silently skipped every route nested in `_IncludedRouter`, and
passed with the fence removed. Two assertions:
1. A `user` session gets **no 2xx anywhere** outside `USER_PATHS`.
2. An anonymous caller gets a 2xx **only** on `PUBLIC_PATHS`, an explicit
   list in the test (login, register, setup-state, health, …). This is the
   assertion that catches holes like `POST /api/projects`.

Plus the websocket tail. **Both assertions must be shown to fail** — with the
fence disabled, and with `/api/projects` reverted — before the test counts.

**Facade**: the existing spec-pinning test plus the new exclusions.

**Web**, Playwright against `mock-api.ts`:
- anonymous `/login` makes no reload loop
- register → profile
- a user deep-linking to `/agents` lands on `/profile`
- the admin's reset modal
- sign out

## Rejected

- **Google OAuth.** It needs an `https://` redirect URI on a public-suffix
  domain. That's workable without public A records (local DNS plus a private
  CA or a DNS-01 cert), but every device would need the name and the trust,
  for a LAN-only platform. Revisit if the platform is ever exposed.
- **A separate `accounts` table.** Every authorization path keys on
  `principals`; a second identity table means a join or a sync at each one.
- **A no-access role without the fence.** About 25 handlers only ask "is
  anyone signed in".
- **`origin`/`username`/`name_key` columns, `last_login_at`,
  `password_changed_at`, rehash-on-login.** Each was a second source of
  truth or unused in M1 (cut in review).
- **Registration rate limiting.** D2: the LAN is the protection. The cost of
  an argon2 hash per request is an accepted risk.

## Review log (2026-10-03)

| Finding | From | Change |
|---|---|---|
| Anonymous `/api/me` 401 ⇒ reload loop, so nobody can sign in | Fable, Sonnet, astra | `isAuthCall` + Gate rules |
| Login regex rejects digit-leading usernames that register allows | all four | one `USERNAME_RE` |
| tail still uses the sync, DB-less validator, so revoked sessions keep tailing | Sonnet, sol, astra | `resolve_session` everywhere; streams revalidate every 60 s |
| Admin password change doesn't revoke; admin API key/MCP can rotate it | Sonnet, Fable, sol | session-only, revokes, facade-excluded |
| `POST /api/projects` unauthenticated; the route walk accepted it because anon also got 2xx | astra (verified) | `require_admin` + `PUBLIC_PATHS` assertion |
| Help routes public | sol | non-`user` auth required |
| Login racing a reset; sessions keyed by reusable name | astra | bind to principal id, row lock + hash re-check |
| FK is possible via bespoke DDL; no-FK self-heal turns reads into writes | astra, Sonnet | FK `ON DELETE SET NULL` + LEFT JOIN, write only on a dangling id |
| `user:` prefix collides with free-text API-key names | sol | key names can't start with `user:` |
| Fence changes optional-auth webhook behavior | Fable | accepted and pinned by a test |
| `GET /api/users` shape unspecified (hash leak risk) | Fable | `UserOut` |
| Redundant columns and over-engineering | Fable, Sonnet | cut (see Rejected) |
| Fence bypass by mixing a cookie with a bearer token | Sonnet | not a hole: a cookie that resolves wins, `user` is never a key role, and the fence keys on role, not kind |
| `platform_settings` is a table for one bool | Sonnet | kept: 4 lines, and group grants will want settings next |

## Build plan

Execution follows `docs/design/40-token-plan.md` (models, briefs, test
commands, budgets, stop conditions).

**Phase A — backend**
1. Models, `_ensure_accounts_ddl`, `platform_settings`, API-key prefix rule.
2. `resolve_session`, login (lock + re-check), logout, the old-cookie
   refusal, `SessionPruner`.
3. The fence, plus fixes to setup-state, tail, live_invocations, projects,
   help and change-password, plus stream revalidation.
4. The route-walk test, shown failing both ways, then passing.
5. Register, `/api/me`, `/api/me/password`.
6. Users, groups, and registration-setting routes.
7. Facade exclusions, SDK regen, the full backend suite once.

**Phase B — web**
8. `api.ts` + Gate + `/login` tabs + `/profile` + sidebar sign-out.
9. Users and Groups pages, reset modal, registration toggle.
10. Playwright specs; one dark-mode visual check at the end.

**Phase C — ship**
11. One review of the whole diff.
12. Merge to `main`, deploy.
13. A scripted live check (curl: register, login, the fence on a route
    sample, logout replay, anonymous `POST /api/projects` ⇒ 401).
14. Kyle signs in again.
