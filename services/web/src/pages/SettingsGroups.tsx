import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage, createGroup, deleteGroup, listGroups, renameGroup, type GroupRow } from "../api";
import { Button } from "@ap/ui/button";
import { ConfirmDialog } from "@ap/ui/dialog";
import { Input } from "@ap/ui/field";
import { Table, TD, TH } from "@ap/ui/table";

// Groups (docs/design/40): a name and a member count for now; grants come later.
export default function SettingsGroups() {
  const [groups, setGroups] = useState<GroupRow[]>([]);
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [deleteFor, setDeleteFor] = useState<GroupRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listGroups().then(setGroups).catch((e) => setError(apiErrorMessage(e, "Could not load groups.")));
  }, []);
  useEffect(load, [load]);

  async function create() {
    setError(null);
    try {
      await createGroup(name.trim());
      setName("");
      load();
    } catch (e) {
      setError(apiErrorMessage(e, "Could not create group."));
    }
  }

  async function saveRename(g: GroupRow) {
    setError(null);
    try {
      await renameGroup(g.id, draft.trim());
      setEditing(null);
      load();
    } catch (e) {
      setError(apiErrorMessage(e, "Could not rename group."));
    }
  }

  async function confirmDelete() {
    if (!deleteFor) return;
    try {
      await deleteGroup(deleteFor.id);
      setDeleteFor(null);
      load();
    } catch (e) {
      setDeleteFor(null);
      setError(apiErrorMessage(e, "Could not delete group."));
    }
  }

  const n = deleteFor?.member_count ?? 0;
  return (
    <div className="page">
      <h1>Groups</h1>
      <p className="muted">Collections of users. Deleting a group leaves its members without one.</p>
      <form className="form-row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create(); }}>
        <Input aria-label="New group name" placeholder="Group name" value={name}
               onChange={(e) => setName(e.target.value)} />
        <Button type="submit" disabled={name.trim() === ""}>Create group</Button>
      </form>
      {error && <div className="error">{error}</div>}
      <Table>
        <thead><tr><TH>Name</TH><TH>Members</TH><TH></TH></tr></thead>
        <tbody>
          {groups.map((g) => (
            <tr key={g.id}>
              <TD>
                {editing === g.id ? (
                  <form className="row-actions"
                        onSubmit={(e) => { e.preventDefault(); saveRename(g); }}>
                    <Input aria-label={`Rename ${g.name}`} autoFocus value={draft}
                           onChange={(e) => setDraft(e.target.value)} />
                    <Button type="submit" size="sm" disabled={draft.trim() === ""}>Save</Button>
                    <Button type="button" variant="secondary" size="sm"
                            onClick={() => setEditing(null)}>Cancel</Button>
                  </form>
                ) : g.name}
              </TD>
              <TD>{g.member_count}</TD>
              <TD>
                {editing !== g.id && (
                  <div className="row-actions">
                    <Button variant="secondary" size="sm"
                            onClick={() => { setEditing(g.id); setDraft(g.name); }}>Rename</Button>
                    <Button variant="secondary" size="sm" onClick={() => setDeleteFor(g)}>Delete</Button>
                  </div>
                )}
              </TD>
            </tr>
          ))}
          {groups.length === 0 && <tr><TD colSpan={3} className="text-muted">No groups.</TD></tr>}
        </tbody>
      </Table>

      <ConfirmDialog open={deleteFor !== null} title={`Delete ${deleteFor?.name ?? "group"}?`}
                     confirmLabel="Delete group" onConfirm={confirmDelete}
                     onCancel={() => setDeleteFor(null)}>
        {n === 0
          ? "No users are in this group."
          : `${n} ${n === 1 ? "user" : "users"} will drop to None.`}
      </ConfirmDialog>
    </div>
  );
}
