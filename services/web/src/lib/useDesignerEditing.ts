import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { api, apiErrorMessage } from "../api";
import type { UiEditOp, UiWriteResult } from "./uiDesigner";

type Entry = { forward: UiEditOp[]; inverse: UiEditOp[] };
type Pending = { ops: UiEditOp[]; expected_version: number; request_id: string; reason: string; entry: Entry; kind: "edit" | "undo" | "redo" };

/** Single-writer local history. Every transition is still guarded by server CAS. */
export function useDesignerEditing(base: string, onRefresh: (version?: number, external?: boolean) => void, scope = base) {
  const generation = useRef(0);
  const head = useRef<number | null>(null);
  const history = useRef<{ undo: Entry[]; redo: Entry[] }>({ undo: [], redo: [] });
  const pending = useRef<Pending | null>(null);
  const events = useRef<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [message, setMessage] = useState("");
  const [, redraw] = useState(0);
  useLayoutEffect(() => {
    generation.current++; head.current = null; pending.current = null; events.current = [];
    history.current = { undo: [], redo: [] }; setBusy(false); setUncertain(false); setMessage(""); redraw((v) => v + 1);
  }, [scope]);
  const reset = useCallback((notice = "Document changed elsewhere. Reloaded the latest version; undo history was cleared.") => {
    history.current = { undo: [], redo: [] }; setMessage(notice); redraw((v) => v + 1);
  }, []);
  const pin = useCallback((version: number) => { head.current = Math.max(head.current ?? 0, version); }, []);
  const external = useCallback((version: number) => {
    if (head.current == null) { head.current = version; return true; }
    if (version <= head.current) return true;
    if (pending.current) { events.current.push(version); return false; }
    reset(); head.current = version; onRefresh(version, true); return true;
  }, [onRefresh, reset]);
  async function send(payload: Pending) {
    const session = generation.current;
    setBusy(true); setMessage("");
    try {
      const result = await api<UiWriteResult>(`${base}/edit`, { method: "POST", body: JSON.stringify({
        ops: payload.ops, expected_version: payload.expected_version, request_id: payload.request_id, reason: payload.reason,
      }) });
      if (session !== generation.current) return;
      head.current = result.version;
      if (payload.kind === "edit") { history.current.undo.push(payload.entry); history.current.redo = []; }
      else if (payload.kind === "undo") { history.current.undo.pop(); history.current.redo.push(payload.entry); }
      else { history.current.redo.pop(); history.current.undo.push(payload.entry); }
      pending.current = null; setUncertain(false);
      const newer = events.current.filter((v) => v > result.version); events.current = [];
      if (newer.length) { reset(); head.current = Math.max(...newer); onRefresh(head.current, true); }
      else onRefresh(result.version);
      if (result.warnings?.length) setMessage(`Saved with ${result.warnings.length} design warning(s).`);
      redraw((v) => v + 1);
    } catch (e) {
      if (session !== generation.current) return;
      // No response may mean the write committed. Keep the exact receipt for retry.
      const definite = e instanceof Error && /^4\d\d:/.test(e.message) && !e.message.startsWith("408:");
      if (!definite) { setUncertain(true); setMessage("The save could not be confirmed. Retry this save before making more changes."); }
      else {
        pending.current = null; const newer = events.current.filter((v) => v > (head.current ?? 0)); events.current = []; setUncertain(false);
        if (e instanceof Error && e.message.startsWith("409:")) { head.current = null; reset("Another edit arrived first. Reloaded the latest version; undo history was cleared."); onRefresh(undefined, true); }
        else if (newer.length) { reset(); head.current = Math.max(...newer); onRefresh(head.current, true); }
        else setMessage(apiErrorMessage(e, "Could not save changes."));
      }
    } finally { if (session === generation.current) setBusy(false); }
  }
  function commit(forward: UiEditOp[], inverse: UiEditOp[]) {
    if (pending.current || head.current == null || !forward.length) return;
    const payload: Pending = { ops: forward, expected_version: head.current, request_id: crypto.randomUUID(), reason: "edited in UI Designer", entry: { forward, inverse }, kind: "edit" };
    pending.current = payload; void send(payload);
  }
  function travel(kind: "undo" | "redo") {
    if (pending.current || head.current == null) return;
    const entry = history.current[kind].at(-1); if (!entry) return;
    const payload: Pending = { ops: kind === "undo" ? entry.inverse : entry.forward, expected_version: head.current, request_id: crypto.randomUUID(), reason: `${kind} in UI Designer`, entry, kind };
    pending.current = payload; void send(payload);
  }
  return { pin, external, reset, commit, undo: () => travel("undo"), redo: () => travel("redo"),
    retry: () => { if (pending.current && !busy) void send(pending.current); },
    busy, uncertain, message, canUndo: history.current.undo.length > 0, canRedo: history.current.redo.length > 0 };
}
