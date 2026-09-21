"""The `qa` principal (docs/design/25): the reader row the QA's browser logs
in as.

Its password is minted here, once, and lives in exactly one place — the k8s
secret behind the declared `qa-web-login` block — so the QA's row binds the
block, the pod gets `QA_WEB_USER` / `QA_WEB_PASSWORD` as env, and
`bin/ap-web-login` turns them into a Playwright storage state. Nobody types
it, nobody reads it back, and it is never returned or logged from here.

A seed in the API's lifespan rather than in `db.init_db`, because only the
API holds the secret store. Mark-gated like every other seed, and the mark is
the off-switch: a deleted row stays deleted until the mark is cleared.

THE STORED SECRET IS THE SOURCE OF TRUTH, not the row. The secret is the only
place the QA pod can read its password from, so a `qa` row whose password is
not in the secret is a row nobody can log in as — adopting it would be
useless. Hence, with the mark clear:
  - row + a stored password → ADOPTED, the secret untouched (a human may have
    set both, and the password is theirs);
  - row + no stored password → ROTATED at boot: a fresh password, the hash
    replaced, the secret written, and a WARNING in the log saying so;
  - no row → minted.
That is what makes the block's documented rotation — delete the secret's
value, clear the mark, restart — work."""
from __future__ import annotations

import logging
import secrets

from sqlalchemy import select, text

from agentplatform.api.auth import ph
from agentplatform.db import INIT_DB_LOCK_KEY, Principal, SchemaMark, utcnow

log = logging.getLogger("qaprincipal")

QA_PRINCIPAL = "qa"
QA_ROLE = "reader"
QA_MARK = "qa-principal-v1"
QA_SECRET = "qa-web-login"
QA_USER_KEY = "QA_WEB_USER"
QA_PASSWORD_KEY = "QA_WEB_PASSWORD"


async def ensure_qa_principal(session_factory, secret_store) -> str:
    """Seed the `qa` row and its secret; returns a one-line outcome (which,
    like the log line, never carries the password).

    The secret is written BEFORE the row commits: if the apiserver refuses the
    write, the transaction rolls back and the next boot starts over; if the
    commit fails after the write, the next boot finds no mark and no row (or a
    row whose hash matches the stored value — an adoption) and converges."""
    async with session_factory() as s:
        # Postgres only: sqlite is the test suite, single-process by nature.
        # The same transaction-scoped lock init_db holds, so this
        # check-then-write cannot interleave with a boot-time seed in another
        # service — and the API being one replica is a chart setting, not a
        # guarantee, so the lock is taken rather than assumed.
        if s.get_bind().dialect.name == "postgresql":
            await s.execute(text("SELECT pg_advisory_xact_lock(:k)")
                            .bindparams(k=INIT_DB_LOCK_KEY))
        if await s.get(SchemaMark, QA_MARK) is not None:
            return "qa principal: already seeded"
        row = (await s.execute(select(Principal).where(
            Principal.name == QA_PRINCIPAL))).scalar_one_or_none()
        stored = await secret_store.get(QA_SECRET) or {}
        if row is not None and stored.get(QA_PASSWORD_KEY):
            outcome = f"qa principal: adopted existing row (role {row.role})"
        else:
            password = secrets.token_urlsafe(24)
            await secret_store.set(QA_SECRET, {**stored, QA_USER_KEY: QA_PRINCIPAL,
                                               QA_PASSWORD_KEY: password})
            if row is None:
                s.add(Principal(name=QA_PRINCIPAL, role=QA_ROLE,
                                password_hash=ph.hash(password)))
                outcome = f"qa principal: minted (role {QA_ROLE}), secret {QA_SECRET} written"
            else:
                row.password_hash = ph.hash(password)
                outcome = (f"qa principal: row existed but {QA_SECRET} held no "
                           f"{QA_PASSWORD_KEY} — rotated: new hash on the row, "
                           "secret rewritten")
            del password
        s.add(SchemaMark(name=QA_MARK, applied_at=utcnow()))
        await s.commit()
    # Loud when a row was rewritten: an admin who set that row's password by
    # hand has just lost it, and this line is how they find out why.
    log.log(logging.WARNING if "rotated" in outcome else logging.INFO, outcome)
    return outcome
