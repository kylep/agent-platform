import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Input, Textarea } from "@ap/ui/field";
import {
  AppDataError, getPage, internalPath, isCount, pageHref, readView,
  type Column, type ColumnFormat, type ParamBinding, type PublishedPage, type Scalar,
  type V2Component, type ViewResult, type ViewRow,
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
// pages or a platform path. Each component reads its own view, so a view the
// viewer can't read or that broke fails that component, not the page.

function v2Label(column: Column): string {
  return column.label || column.field.charAt(0).toUpperCase() + column.field.slice(1).replaceAll("_", " ");
}

function v2Value(value: Scalar | undefined, format: ColumnFormat | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
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

function V2Cell({ row, column }: { row: ViewRow; column: Column }) {
  if (row.restricted.includes(column.field)) {
    return <span className="v2-restricted" title="You can't read this field">restricted</span>;
  }
  return <>{v2Value(row.values[column.field], column.format)}</>;
}

function viewError(error: unknown): string {
  if (error instanceof AppDataError && error.status === 403) return "You can't read this view.";
  if (error instanceof AppDataError && error.status === 503) return "This view no longer validates.";
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
      : <><strong>{isCount(result) ? result.count.toLocaleString() : "—"}</strong><AsOf result={result} /></>}
  </div>;
}

function V2Table({ appId, component }: { appId: string; component: Extract<V2Component, { kind: "table" }> }) {
  const { result, error, more, loadMore } = useView(appId, component);
  const rows = result && !isCount(result) ? result.rows : [];
  const link = component.row_link;
  return <section className="live-view-table">
    <h2>{component.label || "Records"}</h2>
    {!result && error ? <p className="error">{viewError(error)}</p>
      : !result ? <p className="muted">Loading records…</p>
      : rows.length ? <>
        <div className="table-scroll"><table><thead><tr>
          {component.columns.map((column) => <th key={column.field}>{v2Label(column)}</th>)}
        </tr></thead><tbody>{rows.map((row) => <tr key={row.id}>
          {component.columns.map((column, i) => <td key={column.field} data-label={v2Label(column)}>
            {i === 0 && link && !row.restricted.includes(column.field)
              ? <Link to={pageHref(appId, link.page, Object.fromEntries(Object.entries(link.params).map(
                  ([param, field]) => [param, field === "id" ? row.id : String(row.values[field] ?? "")])))}>
                <V2Cell row={row} column={column} /></Link>
              : <V2Cell row={row} column={column} />}
          </td>)}
        </tr>)}</tbody></table></div>
        {!isCount(result) && result.next_cursor &&
          <Button variant="secondary" onClick={loadMore} disabled={more}>{more ? "Loading…" : "Load more"}</Button>}
        {error ? <p className="error">{viewError(error)}</p> : null}
      </> : <p className="muted">No records yet.</p>}
    {result && <p className="muted"><AsOf result={result} /></p>}
  </section>;
}

function V2Detail({ appId, component }: { appId: string; component: Extract<V2Component, { kind: "detail" }> }) {
  const { result, error } = useView(appId, component);
  const row = result && !isCount(result) ? result.rows[0] : undefined;
  return <section className="v2-detail">
    <h2>{component.label || "Record"}</h2>
    {error ? <p className="error">{viewError(error)}</p>
      : !result ? <p className="muted">Loading record…</p>
      : row ? <dl>{component.fields.map((column) => <div key={column.field}>
          <dt>{v2Label(column)}</dt><dd><V2Cell row={row} column={column} /></dd>
        </div>)}</dl>
      : <p className="muted">Record not found.</p>}
    {result && <p className="muted"><AsOf result={result} /></p>}
  </section>;
}

function V2Text({ appId, component }: { appId: string; component: Extract<V2Component, { kind: "text" }> }) {
  const link = component.link;
  const to = !link ? null : "page" in link ? pageHref(appId, link.page)
    : internalPath(link.path) ? link.path : null;
  const body = to ? <Link to={to}>{component.text}</Link> : component.text;
  return component.style === "heading" ? <h2>{body}</h2> : <p>{body}</p>;
}

function V2Block({ appId, component }: { appId: string; component: V2Component }) {
  if (component.kind === "text") return <V2Text appId={appId} component={component} />;
  if (component.kind === "metric") return <V2Metric appId={appId} component={component} />;
  if (component.kind === "table") return <V2Table appId={appId} component={component} />;
  if (component.kind === "detail") return <V2Detail appId={appId} component={component} />;
  return null;
}

/** One published typed/v2 page. `embedded` drops the page chrome for the
 *  builder area, where the App's name is already the h1. */
export function TypedV2Page({ appId, page, embedded = false }: {
  appId: string; page: string; embedded?: boolean;
}) {
  const [published, setPublished] = useState<PublishedPage | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let active = true;
    setPublished(null); setError(null);
    getPage(appId, page).then((p) => { if (active) setPublished(p); })
      .catch((e) => { if (active) setError(e); });
    return () => { active = false; };
  }, [appId, page]);
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
    {!embedded && <p className="muted"><Link to="/apps">Apps</Link> / <Link
      to={`/apps/state/${encodeURIComponent(appId)}`}>{published.app_name}</Link> / {published.page}</p>}
    <div className="live-view-blocks">
      {published.definition.components.map((component, index) =>
        <V2Block key={index} appId={appId} component={component} />)}
    </div>
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
