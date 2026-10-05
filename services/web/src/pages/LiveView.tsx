import { createElement, useEffect, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Input, Textarea } from "@ap/ui/field";
import {
  AppDataError, getPage, internalPath, isCount, pageHref, readView, readRecordHistory,
  confirmPageAction, dispatchPageAction,
  type ActionField, type PageAction, type PageActionIntent,
  type Column, type ColumnFormat, type ParamBinding, type PublishedPage, type RecordValue,
  type RecordHistory, type V2Component, type ViewResult, type ViewRow,
} from "../lib/appData";

type Block = { kind: "heading" | "paragraph" | "metric" | "table" | "chat" | "action" | "link"; text: string; label: string; value: string;
  source: string | null; field: string | null; columns: string[];
  action_alias: string | null; href: string | null };
type PublishedView = {
  id: string;
  app_name: string;
  slug: string;
  published_version: number;
  definition: { renderer: "typed/v1"; title: string; blocks: Block[];
    reads: { alias: string; operation: string; channel_id?: string | null }[];
    actions: { alias: string; operation: "tickets.create@1" | "relay.channel.post@1"; channel: string }[] };
};

type ActionIntent = { intent_id: string; target: string; arguments: { title?: string; body: string } };
type ActionReceipt = { id: string; status: string; result: { ticket_key?: string; message_id?: string; reason?: string } | null };

function newIdempotencyKey(): string {
  // getRandomValues works on the platform's plain-HTTP LAN origin, unlike randomUUID.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function columnLabel(column: string): string {
  const labels: Record<string, string> = {
    distance_km: "Distance", started_at: "Started", n: "Results",
    verify_ok: "Verification", latest_close: "Latest close", change_pct: "Change",
  };
  return labels[column] ||
    column.charAt(0).toUpperCase() + column.slice(1).replaceAll("_", " ");
}

function tableValue(column: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (column === "distance_km") return `${value} km`;
  if (column === "change_pct") return `${value}%`;
  if (column === "verify_ok") return value === true ? "Passed" : "Failed";
  if (column === "started_at" || column === "created_at") {
    const date = new Date(String(value));
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }
  return String(value);
}

function TrustedAction({ viewId, label, alias, channel, operation }: {
  viewId: string; label: string; alias: string; channel: string;
  operation: "tickets.create@1" | "relay.channel.post@1";
}) {
  const ticket = operation === "tickets.create@1";
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [intent, setIntent] = useState<ActionIntent | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ActionReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function preview() {
    setBusy(true); setError(null);
    try {
      const created = await api<ActionIntent>(`/api/live-views/${encodeURIComponent(viewId)}/intents`, {
        method: "POST", body: JSON.stringify({ alias, arguments: ticket ? { title, body } : { body } }),
      });
      setIntent(created);
      setKey(newIdempotencyKey());
    } catch (e) { setError(e instanceof Error ? e.message : "Could not prepare action."); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!intent || !key) return;
    setBusy(true); setError(null);
    try {
      const result = await api<ActionReceipt>(`/api/live-views/${encodeURIComponent(viewId)}/calls`, {
        method: "POST", body: JSON.stringify({ intent_id: intent.intent_id, idempotency_key: key }),
      });
      setReceipt(result);
    } catch (e) {
      // Keep the same intent and key. Retrying cannot dispatch a second effect.
      setError(e instanceof Error ? e.message : "Outcome unavailable. Retry to get the receipt.");
    } finally { setBusy(false); }
  }
  return <section className="live-view-action">
    <h2>{label || (ticket ? "Create ticket" : "Post to Relay")}</h2>
    {receipt ? <p role="status">{receipt.status === "succeeded"
      ? ticket ? `Created ${receipt.result?.ticket_key ?? "ticket"}.` : "Message posted."
      : `Action ${receipt.status.replaceAll("_", " ")}: ${receipt.result?.reason ?? "Check the action receipt."}`}</p>
      : intent ? <div>
        <p>{ticket ? "Create a ticket in" : "Post this message to"} {intent.target}?</p>
        {intent.arguments.title && <p><strong>{intent.arguments.title}</strong></p>}
        {intent.arguments.body && <p>{intent.arguments.body}</p>}
        <Button onClick={confirm} disabled={busy}>{busy ? "Sending…" : "Confirm and send"}</Button>{" "}
        <Button variant="secondary" onClick={() => { setIntent(null); setKey(null); }} disabled={busy}>Edit</Button>
      </div> : <div>
        <p className="muted">{ticket ? `Creates a ticket in #${channel}.`
          : `Posts to internal Relay room #${channel}. Mentions cannot summon agents here.`}</p>
        {ticket && <label>Title<Input value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} /></label>}
        <label>{ticket ? "Details" : "Message"}<Textarea value={body} maxLength={ticket ? 4000 : 1000}
          onChange={(e) => setBody(e.target.value)} /></label>
        <Button onClick={preview} disabled={busy || (ticket ? !title.trim() : !body.trim() || body.includes("@"))}>
          {busy ? "Preparing…" : ticket ? "Review ticket" : "Review message"}</Button>
      </div>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}

// --- typed/v2: pages of a state App (docs/design/39) ------------------------
// Same trust rule as v1: every value is plain React text, and the only links
// are row links and text links the renderer builds itself, to this App's
// pages or a platform path. The one outbound exception is a `link: true` url
// field, which arrives as format "link". Each component reads its own view, so a view the
// viewer can't read or that broke fails that component, not the page.

function v2Label(column: Column): string {
  return column.label || column.field.charAt(0).toUpperCase() + column.field.slice(1).replaceAll("_", " ");
}

function v2Value(value: RecordValue | undefined, format: ColumnFormat | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    return value.map((item) => typeof item === "object" && item !== null
      ? JSON.stringify(item) : String(item)).join(" · ");
  }
  if (typeof value === "boolean" || format === "bool") return value === true ? "Yes" : "No";
  const n = typeof value === "number" ? value : Number(value);
  if (format === "int" && Number.isFinite(n)) return Math.round(n).toLocaleString();
  if (format === "number" && Number.isFinite(n)) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (format === "percent" && Number.isFinite(n)) {
    return n.toLocaleString(undefined, { style: "percent", maximumFractionDigits: 1 });
  }
  if (format === "date") {
    // A date field is a calendar day: read it in UTC so no timezone shifts it.
    const day = new Date(`${String(value).slice(0, 10)}T00:00:00Z`);
    return Number.isNaN(day.getTime()) ? String(value)
      : day.toLocaleDateString(undefined, { timeZone: "UTC", year: "numeric", month: "short", day: "numeric" });
  }
  if (format === "datetime") {
    const at = new Date(String(value));
    return Number.isNaN(at.getTime()) ? String(value) : at.toLocaleString();
  }
  return String(value);
}

function isWebUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && !url.username && !url.password;
  } catch {
    return false;
  }
}

function V2Cell({ row, column }: { row: ViewRow; column: Column }) {
  if (row.restricted.includes(column.field)) {
    return <span className="v2-restricted" title="You can't read this field">restricted</span>;
  }
  const value = row.values[column.field];
  if (column.format === "artifact" && typeof value === "string" &&
      /^[0-9a-f]{32}$/.test(value)) {
    return <a href={`/api/artifacts/${value}/content`} target="_blank"
      rel="noopener noreferrer">Open artifact</a>;
  }
  // `link` comes only from a `link: true` url field. The server already
  // refuses non-http(s) values; checking again keeps a stored odd value inert.
  if (column.format === "link" && typeof value === "string" && isWebUrl(value)) {
    return <a href={value} rel="noopener noreferrer" target="_blank">{value}</a>;
  }
  return <>{v2Value(value, column.format)}</>;
}

type SelectedAction = { action: PageAction; row: ViewRow | null };

function actionValues(fields: ActionField[], draft: Record<string, string>): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    const raw = draft[field.name];
    if (raw === undefined || raw === "") continue;
    if (field.type === "list") {
      let parsed: unknown;
      try { parsed = JSON.parse(raw); }
      catch { throw new Error(`${field.label || field.name} needs a valid JSON array.`); }
      if (!Array.isArray(parsed)) throw new Error(`${field.label || field.name} needs a JSON array.`);
      values[field.name] = parsed;
      continue;
    }
    values[field.name] = field.type === "bool" ? raw === "true"
      : field.type === "int" || field.type === "number" ? Number(raw) : raw;
  }
  return values;
}

function ActionFieldInput({ field, value, onChange }: { field: ActionField; value: string;
  onChange: (value: string) => void }) {
  const label = field.label || columnLabel(field.name);
  if (field.type === "enum" || field.type === "bool") {
    const options = field.type === "bool" ? ["true", "false"] : field.values || [];
    return <label>{label}<select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose…</option>{options.map((option) =>
        <option key={option} value={option}>{option}</option>)}
    </select></label>;
  }
  if (field.type === "text" || field.type === "list") return <label>{label}
    {field.type === "list" && <span className="muted">JSON array, up to {field.max_items || 50} items</span>}
    <Textarea value={value} maxLength={field.type === "list" ? 1_048_576 : field.max}
      onChange={(e) => onChange(e.target.value)} /></label>;
  const type = field.type === "date" ? "date" : field.type === "datetime" ? "datetime-local"
    : field.type === "int" || field.type === "number" ? "number" : "text";
  return <label>{label}<Input type={type} value={value}
    min={type === "number" ? field.min : undefined}
    max={type === "number" ? field.max : undefined}
    maxLength={type === "text" ? field.max : undefined}
    step={field.type === "int" ? 1 : field.type === "number" ? "any" : undefined}
    onChange={(e) => onChange(e.target.value)} /></label>;
}

function ActionPanel({ appId, page, selected, onClose, onDone }: {
  appId: string; page: string; selected: SelectedAction;
  onClose: () => void; onDone: (message: string) => void;
}) {
  const { action, row } = selected;
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [intent, setIntent] = useState<PageActionIntent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function prepare() {
    setBusy(true); setError(null);
    try {
      setIntent(await confirmPageAction(appId, page, action.name, row?.id || null,
        actionValues(action.editable_fields, draft)));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not prepare the action."); }
    finally { setBusy(false); }
  }
  async function finish() {
    if (!intent) return;
    setBusy(true); setError(null);
    try {
      const receipt = await dispatchPageAction(intent);
      onDone(receipt.deleted ? "Record deleted." : receipt.version ? "Record saved." : "Action complete.");
    } catch (e) {
      const message = e instanceof Error ? e.message : "Could not dispatch the action.";
      if (e instanceof AppDataError && e.status === 409) {
        setIntent(null);
        setError("The page or record changed. Review it and confirm again.");
      } else setError(message);
    } finally { setBusy(false); }
  }
  return <section className="v2-action-panel" role="dialog" aria-modal="true"
    aria-labelledby="v2-action-title">
    <h2 id="v2-action-title">{action.label}</h2>
    {intent ? <>
      <p>Review the exact change before continuing.</p>
      {intent.confirmation.current != null && <><h3>Current record</h3>
        <pre>{JSON.stringify(intent.confirmation.current, null, 2)}</pre></>}
      {intent.confirmation.resulting_values != null && <><h3>Resulting values</h3>
        <pre>{JSON.stringify(intent.confirmation.resulting_values, null, 2)}</pre></>}
      {intent.confirmation.delete_plan != null && <><h3>Delete plan</h3>
        <pre>{JSON.stringify(intent.confirmation.delete_plan, null, 2)}</pre></>}
      <Button onClick={finish} disabled={busy}>{busy ? "Saving…" : "Confirm action"}</Button>{" "}
      <Button variant="secondary" onClick={() => setIntent(null)} disabled={busy}>Edit</Button>
    </> : <>
      {action.kind === "delete" || action.destructive ? <p>Review the affected records before deleting.</p>
        : action.editable_fields.map((field) => <ActionFieldInput key={field.name}
          field={field} value={draft[field.name] || ""}
          onChange={(value) => setDraft({ ...draft, [field.name]: value })} />)}
      <Button onClick={prepare} disabled={busy}>{busy ? "Preparing…" : "Review change"}</Button>
    </>}{" "}
    <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}

function viewError(error: unknown): string {
  if (error instanceof AppDataError && error.status === 403) return "You can't read this view.";
  if (error instanceof AppDataError && error.status === 503) {
    return "This view is disabled because its source or tool binding changed.";
  }
  return `This view is unavailable: ${error instanceof Error ? error.message : "unknown error"}`;
}

function AsOf({ result }: { result: ViewResult }) {
  return <span className="v2-as-of">
    As of {new Date(result.as_of).toLocaleString()}
    {result.stale && <> <Chip variant="warn">stale</Chip></>}
  </span>;
}

function resolveParams(params: Record<string, ParamBinding> | undefined,
                       query: URLSearchParams): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, binding] of Object.entries(params ?? {})) {
    const value = typeof binding === "string" ? binding : query.get(binding.query);
    if (value !== null) out[name] = value;
  }
  return out;
}

type ViewState = { result: ViewResult | null; error: unknown; more: boolean };

function useView(appId: string, component: Exclude<V2Component, { kind: "text" }>) {
  const [query] = useSearchParams();
  const params = resolveParams(component.params, query);
  const key = JSON.stringify(params);
  const limit = component.kind === "table" ? component.limit : component.kind === "detail" ? 1 : undefined;
  const [state, setState] = useState<ViewState>({ result: null, error: null, more: false });
  useEffect(() => {
    let active = true;
    setState({ result: null, error: null, more: false });
    readView(appId, component.view, JSON.parse(key), { limit })
      .then((result) => { if (active) setState({ result, error: null, more: false }); })
      .catch((error) => { if (active) setState({ result: null, error, more: false }); });
    return () => { active = false; };
  }, [appId, component.view, key, limit]);
  async function loadMore() {
    const current = state.result;
    if (!current || isCount(current) || !current.next_cursor) return;
    setState({ ...state, more: true });
    try {
      const next = await readView(appId, component.view, params, { limit, cursor: current.next_cursor });
      if (isCount(next)) throw new AppDataError(503, "view changed shape");
      setState({ result: { ...next, rows: [...current.rows, ...next.rows] }, error: null, more: false });
    } catch (error) { setState({ result: current, error, more: false }); }
  }
  return { ...state, loadMore };
}

function V2Metric({ appId, component }: { appId: string; component: Extract<V2Component, { kind: "metric" }> }) {
  const { result, error } = useView(appId, component);
  return <div className="live-view-metric">
    <span className="muted">{component.label}</span>
    {error ? <span className="error">{viewError(error)}</span>
      : !result ? <span className="muted">Loading…</span>
      : <><strong>{isCount(result)
          ? ("count" in result ? result.count : result.value)?.toLocaleString() ?? "—"
          : "—"}</strong><AsOf result={result} /></>}
  </div>;
}

function V2Chart({ appId, component }: { appId: string; component: Extract<V2Component, { kind: "chart" }> }) {
  const { result, error } = useView(appId, component);
  const rows = result && !isCount(result) ? result.rows : [];
  const values = rows.map((row) => Number(row.values[component.y]));
  const maximum = Math.max(1, ...values.filter((n) => Number.isFinite(n) && n >= 0));
  return <section className="v2-chart">
    <h2>{component.label || "Chart"}</h2>
    {error ? <p role="alert" className="error">{viewError(error)}</p>
      : !result ? <p className="muted">Loading chart…</p>
      : rows.length === 0 ? <p className="muted">No data yet.</p>
      : <div className="v2-bars" role="img" aria-label={component.label || "Bar chart"}>
        {rows.map((row, i) => {
          const n = values[i];
          const value = Number.isFinite(n) && n >= 0 ? n : 0;
          const label = String(row.values[component.x] ?? "");
          const shortLabel = /^\d{4}-\d{2}-\d{2}$/.test(label)
            ? new Date(`${label}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })
            : label;
          return <div key={`${row.id}-${i}`} className="v2-bar-item"
            title={`${label}: ${value.toLocaleString()} ${component.unit || ""}`}>
            <span className="v2-bar-number">{value.toLocaleString()}</span>
            <span className="v2-bar-track"><span style={{ height: `${Math.max(2, value / maximum * 100)}%` }} /></span>
            <span className="v2-bar-label">{shortLabel}</span>
          </div>;
        })}
      </div>}
    {result && <AsOf result={result} />}
  </section>;
}

function V2Calendar({ appId, component }: { appId: string; component: Extract<V2Component, { kind: "calendar" }> }) {
  const { result, error } = useView(appId, component);
  const rows = result && !isCount(result) ? result.rows : [];
  const values = rows.map((row) => Number(row.values[component.value]));
  const maximum = Math.max(1, ...values.filter((n) => Number.isFinite(n) && n >= 0));
  return <section className="v2-calendar">
    <h2>{component.label || "Calendar"}</h2>
    {error ? <p role="alert" className="error">{viewError(error)}</p>
      : !result ? <p className="muted">Loading calendar…</p>
      : rows.length === 0 ? <p className="muted">No data yet.</p>
      : <div className="v2-day-grid" role="img" aria-label={component.label || "Activity calendar"}>
        {rows.map((row, i) => {
          const n = values[i];
          const value = Number.isFinite(n) && n >= 0 ? n : 0;
          const day = String(row.values[component.day] ?? "").slice(0, 10);
          const style = { opacity: value ? 0.25 + 0.75 * Math.sqrt(value / maximum) : undefined };
          const title = `${day}: ${value.toLocaleString()} ${component.unit || ""}`;
          return component.day_link && day
            ? <Link key={`${row.id}-${i}`} className="v2-day" style={style} title={title}
                aria-label={`Open ${day}`}
                to={pageHref(appId, component.day_link.page, { [component.day_link.param]: day })} />
            : <span key={`${row.id}-${i}`} className="v2-day" style={style} title={title} />;
        })}
      </div>}
    {result && <AsOf result={result} />}
  </section>;
}

function V2StatRow({ appId, component }: { appId: string; component: Extract<V2Component, { kind: "stat_row" }> }) {
  const { result, error } = useView(appId, component);
  const row = result && !isCount(result) ? result.rows[0] : null;
  return <section className="v2-stat-row">
    {component.label && <h2>{component.label}</h2>}
    {error ? <p role="alert" className="error">{viewError(error)}</p>
      : !result ? <p className="muted">Loading stats…</p>
      : row ? <div className="v2-stat-items">{component.columns.map((column) =>
          <div key={column.field}><span className="muted">{v2Label(column)}</span>
            <strong><V2Cell row={row} column={column} /></strong></div>)}</div>
        : <p className="muted">No data yet.</p>}
    {result && <AsOf result={result} />}
  </section>;
}

function V2Table({ appId, component, actions, onAction }: { appId: string;
  component: Extract<V2Component, { kind: "table" }>;
  actions: PageAction[]; onAction: (action: PageAction, row: ViewRow | null) => void }) {
  const { result, error, more, loadMore } = useView(appId, component);
  const rows = result && !isCount(result) ? result.rows : [];
  const link = component.row_link;
  const available = actions.filter((action) => component.actions?.includes(action.name));
  return <section className="live-view-table">
    <h2>{component.label || "Records"}</h2>
    {available.filter((action) => action.kind === "create").map((action) =>
      <Button key={action.name} onClick={() => onAction(action, null)}>{action.label}</Button>)}
    {!result && error ? <p className="error">{viewError(error)}</p>
      : !result ? <p className="muted">Loading records…</p>
      : rows.length ? <>
        <div className="table-scroll"><table><thead><tr>
          {component.columns.map((column) => <th key={column.field}>{v2Label(column)}</th>)}
          {available.some((action) => action.kind !== "create") && <th>Actions</th>}
        </tr></thead><tbody>{rows.map((row) => <tr key={row.id}>
          {component.columns.map((column, i) => <td key={column.field} data-label={v2Label(column)}>
            {i === 0 && link && !row.restricted.includes(column.field) &&
              Object.values(link.params).every((field) => field === "id" ||
                (!row.restricted.includes(field) && row.values[field] != null))
              ? <Link to={pageHref(appId, link.page, Object.fromEntries(Object.entries(link.params).map(
                  ([param, field]) => [param, field === "id" ? row.id : String(row.values[field] ?? "")])))}>
                <V2Cell row={row} column={column} /></Link>
              : <V2Cell row={row} column={column} />}
          </td>)}
          {available.some((action) => action.kind !== "create") && <td data-label="Actions">
            {available.filter((action) => action.kind !== "create").map((action) =>
              <Button key={action.name} variant="secondary"
                onClick={() => onAction(action, row)}>{action.label}</Button>)}
          </td>}
        </tr>)}</tbody></table></div>
        {!isCount(result) && result.next_cursor &&
          <Button variant="secondary" onClick={loadMore} disabled={more}>{more ? "Loading…" : "Load more"}</Button>}
        {error ? <p className="error">{viewError(error)}</p> : null}
      </> : <p className="muted">No records yet.</p>}
    {result && <p className="muted"><AsOf result={result} /></p>}
  </section>;
}

function V2Detail({ appId, component, actions, onAction }: { appId: string;
  component: Extract<V2Component, { kind: "detail" }>;
  actions: PageAction[]; onAction: (action: PageAction, row: ViewRow | null) => void }) {
  const { result, error } = useView(appId, component);
  const row = result && !isCount(result) ? result.rows[0] : undefined;
  const available = actions.filter((action) => component.actions?.includes(action.name));
  return <section className="v2-detail">
    <h2>{component.label || "Record"}</h2>
    {error ? <p className="error">{viewError(error)}</p>
      : !result ? <p className="muted">Loading record…</p>
      : row ? <dl>{component.fields.map((column) => <div key={column.field}>
          <dt>{v2Label(column)}</dt><dd><V2Cell row={row} column={column} /></dd>
        </div>)}</dl>
      : <p className="muted">Record not found.</p>}
    {available.filter((action) => action.kind === "create" || row).map((action) =>
      <Button key={action.name} variant="secondary"
        onClick={() => onAction(action, action.kind === "create" ? null : row || null)}>
        {action.label}</Button>)}
    {row && component.history && <V2History key={row.id} appId={appId}
      collection={component.history.collection} recordId={row.id}
      fields={component.fields} />}
    {result && <p className="muted"><AsOf result={result} /></p>}
  </section>;
}

function V2History({ appId, collection, recordId, fields }: { appId: string;
  collection: string; recordId: string; fields: Column[] }) {
  const [page, setPage] = useState<RecordHistory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function load(beforeVersion?: number) {
    setBusy(true); setError(null);
    try {
      const next = await readRecordHistory(appId, collection, recordId, beforeVersion);
      setPage(beforeVersion && page ? {
        versions: [...page.versions, ...next.versions],
        next_before_version: next.next_before_version,
      } : next);
    } catch (e) { setError(e instanceof Error ? e.message : "History is unavailable."); }
    finally { setBusy(false); }
  }
  return <div className="v2-detail-history">
    <Button variant="secondary" onClick={() => page ? setPage(null) : void load()}
      disabled={busy} aria-expanded={page !== null}>
      {page ? "Hide history" : "Show history"}
    </Button>
    {error && <p role="alert" className="error">{error}</p>}
    {page && <div role="region" aria-label="Record history">{page.versions.map(({ version, record }) =>
      <details key={version}>
        <summary>Version {version}</summary>
        <dl>{fields.map((column) => <div key={column.field}>
          <dt>{v2Label(column)}</dt><dd><V2Cell row={record} column={column} /></dd>
        </div>)}</dl>
      </details>)}
      {page.next_before_version && <Button variant="secondary" disabled={busy}
        onClick={() => void load(page.next_before_version || undefined)}>
        {busy ? "Loading…" : "Older versions"}
      </Button>}
    </div>}
  </div>;
}

function V2Text({ appId, component }: { appId: string; component: Extract<V2Component, { kind: "text" }> }) {
  const link = component.link;
  const to = !link ? null : "page" in link ? pageHref(appId, link.page)
    : internalPath(link.path) ? link.path : null;
  const body = to ? <Link to={to}>{component.text}</Link> : component.text;
  return component.style === "heading" ? <h2>{body}</h2> : <p>{body}</p>;
}

function V2Block({ appId, component, actions, onAction }: { appId: string;
  component: V2Component; actions: PageAction[];
  onAction: (action: PageAction, row: ViewRow | null) => void }) {
  if (component.kind === "text") return <V2Text appId={appId} component={component} />;
  if (component.kind === "metric") return <V2Metric appId={appId} component={component} />;
  if (component.kind === "chart") return <V2Chart appId={appId} component={component} />;
  if (component.kind === "calendar") return <V2Calendar appId={appId} component={component} />;
  if (component.kind === "stat_row") return <V2StatRow appId={appId} component={component} />;
  if (component.kind === "table") return <V2Table appId={appId} component={component}
    actions={actions} onAction={onAction} />;
  if (component.kind === "detail") return <V2Detail appId={appId} component={component}
    actions={actions} onAction={onAction} />;
  return null;
}

const layoutTags = new Set(["section", "div", "header", "footer", "article", "aside",
  "h1", "h2", "h3", "p", "span", "strong", "em", "small", "ul", "ol", "li", "hr", "br"]);
const layoutClasses = new Set(["ap-layout", "ap-stack", "ap-grid", "ap-card",
  "ap-hero", "ap-muted"]);

function V2Layout({ layout, components, renderBlock }: { layout: string;
  components: V2Component[]; renderBlock: (component: V2Component, index: number) => ReactNode }) {
  // The server validates the same small HTML vocabulary. Reconstruct React
  // nodes from text and allowlisted tags rather than interpreting authored
  // HTML, attributes, scripts or event handlers.
  const document = new DOMParser().parseFromString(layout, "text/html");
  const slots = new Map(components.map((component, index) => [component.slot, index]));
  function render(node: Node, key: string): ReactNode {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    const element = node as Element;
    const tag = element.tagName.toLowerCase();
    if (tag === "ap-view") {
      const index = slots.get(element.getAttribute("name") || "");
      return index === undefined ? null : renderBlock(components[index], index);
    }
    if (!layoutTags.has(tag)) return null;
    const className = (element.getAttribute("class") || "").split(/\s+/)
      .filter((name) => layoutClasses.has(name)).join(" ");
    const children = Array.from(element.childNodes).map((child, index) =>
      render(child, `${key}-${index}`));
    return createElement(tag, { key, className: className || undefined }, ...children);
  }
  return <div className="ap-layout">{Array.from(document.body.childNodes).map((node, index) =>
    render(node, `root-${index}`))}</div>;
}

/** One published typed/v2 page. `embedded` drops the page chrome for the
 *  builder area, where the App's name is already the h1. */
export function TypedV2Page({ appId, page, embedded = false }: {
  appId: string; page: string; embedded?: boolean;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [queryDraft, setQueryDraft] = useState<Record<string, string>>({});
  const [published, setPublished] = useState<PublishedPage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<SelectedAction | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setPublished(null); setError(null);
    getPage(appId, page).then((p) => { if (active) setPublished(p); })
      .catch((e) => { if (active) setError(e); });
    return () => { active = false; };
  }, [appId, page]);
  useEffect(() => {
    setQueryDraft(Object.fromEntries(searchParams.entries()));
  }, [searchParams]);
  const Title = embedded ? "h2" : "h1";
  if (error) {
    const status = error instanceof AppDataError ? error.status : 0;
    const detail = error instanceof Error ? error.message : "";
    const [title, explain] = status === 403 ? ["No access", "You can't read this page."]
      : status === 404 ? ["Page not found", "This App has no published page by that name."]
      : status === 503 ? ["Page unavailable", "This page no longer matches its App's definitions, so it isn't shown."]
      : ["Page unavailable", "The page couldn't be loaded."];
    return <div className={embedded ? undefined : "page"}>
      <Title>{title}</Title>
      <p className="error">{explain}{detail && <> ({detail})</>}</p>
      {!embedded && <Link to={`/apps/state/${encodeURIComponent(appId)}`}>Back to the App</Link>}
    </div>;
  }
  if (!published) return <div className={embedded ? undefined : "page"}><p className="muted">Loading page…</p></div>;
  return <div className={embedded ? undefined : "page"}>
    <div className="page-header"><Title>{published.definition.title}</Title></div>
    {notice && <p role="status">{notice}</p>}
    {!embedded && <p className="muted"><Link to="/apps">Apps</Link> / <Link
      to={`/apps/state/${encodeURIComponent(appId)}`}>{published.app_name}</Link> / {published.page}</p>}
    {published.definition.params && <form className="live-view-controls" onSubmit={(event) => {
      event.preventDefault();
      const next = new URLSearchParams(searchParams);
      for (const name of Object.keys(published.definition.params || {})) {
        const value = queryDraft[name]?.trim();
        if (value) next.set(name, value); else next.delete(name);
      }
      setSearchParams(next);
    }}>{Object.entries(published.definition.params).map(([name, spec]) =>
      <label key={name}>{name.charAt(0).toUpperCase() + name.slice(1).replaceAll("_", " ")}
        <Input type={spec.type === "date" ? "date" : "search"}
          value={queryDraft[name] ?? ""} maxLength={spec.type === "date" ? undefined : 200}
          onChange={(event) => setQueryDraft({ ...queryDraft, [name]: event.target.value })} />
      </label>)}<Button type="submit">Apply</Button></form>}
    {(() => {
      const renderBlock = (component: V2Component, index: number) =>
        <V2Block key={`${index}-${refresh}`} appId={appId} component={component}
          actions={published.definition.actions || []}
          onAction={(action, row) => { setNotice(null); setSelected({ action, row }); }} />;
      return published.definition.layout
        ? <V2Layout layout={published.definition.layout}
            components={published.definition.components} renderBlock={renderBlock} />
        : <div className="live-view-blocks">
            {published.definition.components.map(renderBlock)}
          </div>;
    })()}
    {selected && <ActionPanel key={`${selected.action.name}-${selected.row?.id || "new"}`}
      appId={appId} page={page} selected={selected} onClose={() => setSelected(null)}
      onDone={(message) => { setSelected(null); setNotice(message); setRefresh((n) => n + 1); }} />}
  </div>;
}

// Values are plain React text. No authored HTML, CSS, script or URL is ever
// interpreted by this first private-data renderer.
export default function LiveViewPage() {
  const { appId, page } = useParams();
  return appId && page ? <TypedV2Page appId={appId} page={page} /> : <TypedV1Page />;
}

function TypedV1Page() {
  const { id } = useParams();
  const [view, setView] = useState<PublishedView | null>(null);
  const [readData, setReadData] = useState<Record<string, Record<string, unknown>>>({});
  const [readError, setReadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [snapshot, setSnapshot] = useState<{ id: string; resource_uri: string } | null>(null);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  useEffect(() => {
    if (!id) return;
    api<PublishedView>(`/api/live-views/${encodeURIComponent(id)}`)
      .then(setView).catch((e) => setError(e instanceof Error ? e.message : "Page unavailable."));
  }, [id]);
  useEffect(() => {
    if (!view?.definition.reads.length) return;
    let active = true;
    Promise.all(view.definition.reads.map(async ({ alias }) => [alias, await api<Record<string, unknown>>(
      `/api/live-views/${encodeURIComponent(view.id)}/data/${encodeURIComponent(alias)}`)] as const))
      .then((pairs) => { if (active) setReadData(Object.fromEntries(pairs)); })
      .catch((e) => { if (active) setReadError(e instanceof Error ? e.message : "Live data unavailable."); });
    return () => { active = false; };
  }, [view, refresh]);
  async function capture() {
    if (!view?.definition.reads.length) return;
    setSnapshotBusy(true); setSnapshotError(null);
    try {
      setSnapshot(await api<{ id: string; resource_uri: string }>(
        `/api/live-views/${encodeURIComponent(view.id)}/snapshots`, {
          method: "POST", body: JSON.stringify({}),
        }));
    } catch (e) { setSnapshotError(e instanceof Error ? e.message : "Snapshot unavailable."); }
    finally { setSnapshotBusy(false); }
  }
  if (error) return <div className="page"><h1>Page unavailable</h1><p className="error">{error}</p><Link to="/apps">Apps</Link></div>;
  if (!view) return <div className="page"><p className="muted">Loading page…</p></div>;
  return (
    <div className="page">
      <div className="page-header"><h1>{view.definition.title}</h1></div>
      <p className="muted"><Link to="/apps">Apps</Link> / {view.app_name} / {view.slug}</p>
      {view.definition.reads.length > 0 && <div className="live-view-controls">
        <Button variant="secondary" onClick={() => setRefresh((n) => n + 1)}>Refresh data</Button>{" "}
        {view.definition.reads.every((read) => read.operation !== "relay.channel.read@1") &&
          <Button variant="secondary" onClick={capture} disabled={snapshotBusy}>
            {snapshotBusy ? "Capturing…" : "Save snapshot"}
          </Button>}
        {snapshot && <span role="status">Saved snapshot: <code>{snapshot.resource_uri}</code></span>}
        {snapshotError && <span role="alert" className="error">{snapshotError}</span>}
      </div>}
      {readError && <p className="error">Live data unavailable: {readError}</p>}
      <div className="live-view-blocks">
        {view.definition.blocks.map((block, index) => {
          if (block.kind === "heading") return <h2 key={index}>{block.text}</h2>;
          if (block.kind === "paragraph") return <p key={index}>{block.text}</p>;
          if (block.kind === "link") return <p key={index}><a href={block.href || "#"}>{block.label || "Open app"} →</a></p>;
          if (block.kind === "action") {
            const action = view.definition.actions.find((a) => a.alias === block.action_alias);
            return action ? <TrustedAction key={index} viewId={view.id} label={block.label}
              alias={action.alias} channel={action.channel} operation={action.operation} /> : null;
          }
          if (block.kind === "chat") {
            const data = block.source ? readData[block.source] : null;
            const rows = Array.isArray(data?.rows) ? data.rows as Record<string, unknown>[] : [];
            return <section className="live-view-chat" key={index}>
              <h2>{block.label || "Recent conversation"}</h2>
              {!data && !readError ? <p className="muted">Loading conversation…</p>
                : rows.length ? <ol>{rows.map((row, i) => <li key={i}>
                  <div className="live-view-chat-meta"><strong>{String(row.author || "Unknown")}</strong>
                    <time dateTime={String(row.created_at || "")}>{tableValue("created_at", row.created_at)}</time>
                  </div>
                  <p>{String(row.body || "")}</p>
                </li>)}</ol> : !readError ? <p className="muted">No messages yet.</p> : null}
            </section>;
          }
          if (block.kind === "table") {
            const data = block.source ? readData[block.source] : null;
            const rows = Array.isArray(data?.rows) ? data.rows as Record<string, unknown>[] : [];
            const columns = block.columns?.length ? block.columns : rows[0] ? Object.keys(rows[0]) : [];
            return <section className="live-view-table" key={index}>
              <h2>{block.label || "Recent rows"}</h2>
              {!data && !readError ? <p className="muted">Loading rows…</p>
                : rows.length ? <div className="table-scroll"><table><thead><tr>
                {columns.map((column) => <th key={column}>{columnLabel(column)}</th>)}
              </tr></thead><tbody>{rows.map((row, i) => <tr key={i}>
                {columns.map((column) => <td key={column} data-label={columnLabel(column)}>
                  {tableValue(column, row[column])}</td>)}
              </tr>)}</tbody></table></div>
                : !readError ? <p className="muted">No rows yet.</p> : null}
            </section>;
          }
          const dynamic = block.source && block.field
            ? readData[block.source]?.[block.field] : null;
          return <div className="live-view-metric" key={index}>
            <span className="muted">{block.label}</span>
            <strong>{block.source ? (dynamic === null || dynamic === undefined ? "—" : String(dynamic)) : block.value}</strong>
          </div>;
        })}
      </div>
    </div>
  );
}
