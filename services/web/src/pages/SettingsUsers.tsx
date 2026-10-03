import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  apiErrorMessage, deleteUser, getRegistration, listGroups, listUsers, resetUserPassword,
  setRegistration, setUserGroup, type GroupRow, type UserRow,
} from "../api";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { ConfirmDialog, FormDialog } from "@ap/ui/dialog";
import { Input, Select } from "@ap/ui/field";
import { Table, TD, TH } from "@ap/ui/table";

// People (docs/design/40). System rows (admin, qa) are read-only; state rows
// carry a group, a password reset and a delete.
export default function SettingsUsers() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [groups, setGroups] = useState<GroupRow[]>([]);
  const [open, setOpen] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetFor, setResetFor] = useState<UserRow | null>(null);
  const [deleteFor, setDeleteFor] = useState<UserRow | null>(null);
  const [pw, setPw] = useState("");
  const [pwConfirm, setPwConfirm] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);

  const load = useCallback(() => {
    listUsers().then(setUsers).catch((e) => setError(apiErrorMessage(e, "Could not load users.")));
    listGroups().then(setGroups).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    getRegistration().then((r) => setOpen(r.open)).catch(() => {});
  }, [load]);

  async function toggleRegistration() {
    if (open === null) return;
    setError(null);
    try {
      setOpen((await setRegistration(!open)).open);
    } catch (e) {
      setError(apiErrorMessage(e, "Could not change registration."));
    }
  }

  async function changeGroup(u: UserRow, groupId: string) {
    setError(null);
    try {
      await setUserGroup(u.id, groupId || null);
      load();
    } catch (e) {
      setError(apiErrorMessage(e, "Could not change group."));
    }
  }

  function closeReset() {
    setResetFor(null); setPw(""); setPwConfirm(""); setDialogError(null);
  }

  async function submitReset() {
    if (!resetFor) return;
    if (pw !== pwConfirm) { setDialogError("Passwords do not match."); return; }
    try {
      await resetUserPassword(resetFor.id, pw, pwConfirm);
      closeReset();
    } catch (e) {
      setDialogError(apiErrorMessage(e, "Could not reset password."));
    }
  }

  async function confirmDelete() {
    if (!deleteFor) return;
    try {
      await deleteUser(deleteFor.id);
      setDeleteFor(null);
      load();
    } catch (e) {
      setDeleteFor(null);
      setError(apiErrorMessage(e, "Could not delete user."));
    }
  }

  return (
    <div className="page-wide">
      <div className="row-actions" style={{ justifyContent: "space-between" }}>
        <h1>Users</h1>
        <div className="row-actions">
          <Chip variant={open ? "ok" : "neutral"}>registration {open ? "open" : "closed"}</Chip>
          <Button variant="secondary" size="sm" disabled={open === null} onClick={toggleRegistration}>
            {open ? "Close registration" : "Open registration"}
          </Button>
        </div>
      </div>
      <p className="muted">
        People who signed in with a username. Put them in a <Link to="/settings/groups">group</Link>.
      </p>
      {error && <div className="error">{error}</div>}
      <Table>
        <thead>
          <tr><TH>Username</TH><TH>Kind</TH><TH>Group</TH><TH>Member since</TH><TH></TH></tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <TD>{u.username}</TD>
              <TD>{u.kind === "system" ? <Chip>system</Chip> : <Chip variant="accent">user</Chip>}</TD>
              <TD>
                {u.kind === "state" ? (
                  <Select aria-label={`Group for ${u.username}`} value={u.group?.id ?? ""}
                          onChange={(e) => changeGroup(u, e.target.value)}>
                    <option value="">None</option>
                    {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                  </Select>
                ) : <span className="text-muted">—</span>}
              </TD>
              <TD className="text-muted whitespace-nowrap">
                {u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}
              </TD>
              <TD>
                {u.kind === "state" && (
                  <div className="row-actions">
                    <Button variant="secondary" size="sm" onClick={() => setResetFor(u)}>Reset password</Button>
                    <Button variant="secondary" size="sm" onClick={() => setDeleteFor(u)}>Delete</Button>
                  </div>
                )}
              </TD>
            </tr>
          ))}
          {users.length === 0 && <tr><TD colSpan={5} className="text-muted">No users.</TD></tr>}
        </tbody>
      </Table>

      <FormDialog open={resetFor !== null} title={`Reset password for ${resetFor?.username ?? ""}`}
                  submitLabel="Reset password" disabled={!pw || !pwConfirm}
                  onSubmit={submitReset} onCancel={closeReset}>
        <div className="form-col">
          <Input type="password" aria-label="New password" placeholder="New password"
                 autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
          <Input type="password" aria-label="Confirm new password" placeholder="Confirm new password"
                 autoComplete="new-password" value={pwConfirm}
                 onChange={(e) => setPwConfirm(e.target.value)} />
          <span className="muted">Signs them out everywhere.</span>
          {dialogError && <div className="error">{dialogError}</div>}
        </div>
      </FormDialog>

      <ConfirmDialog open={deleteFor !== null} title={`Delete ${deleteFor?.username ?? "user"}?`}
                     confirmLabel="Delete user" onConfirm={confirmDelete}
                     onCancel={() => setDeleteFor(null)}>
        This removes the account and signs it out everywhere. The username can be registered again.
      </ConfirmDialog>
    </div>
  );
}
