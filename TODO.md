# Operator TODO

- [ ] Remove the restore-drill scratch database named `restoredrill` on the
      postgres pod (left by the 2026-08-24 design-15 backup restore drill;
      Claude's hooks block database removal, so this one is yours):
      `kubectl -n agent-platform exec -it ap-postgresql-0 -- psql -U postgres`
      then drop the database named restoredrill.
- [ ] Decide whether pai gets `agents_edit` (self-hosting loop for its own
      definition). Explicitly undecided 2026-08-24 — nobody holds the RBAC
      tools until you grant them in the UI.
