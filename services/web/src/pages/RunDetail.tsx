import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type RunDetailData, type RunEvent } from "../api";
import { isActiveState, stateChipClass } from "./Runs";

type ContentBlock = {
  type?: string;
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  id?: string;
  tool_use_id?: string;
  content?: unknown;
  is_error?: boolean;
};

// The most useful single argument to show inline for a tool call.
function toolArgSummary(name: string, input: Record<string, unknown> = {}): string {
  const pick = (k: string) => (typeof input[k] === "string" ? (input[k] as string) : "");
  const byTool: Record<string, string> = {
    Bash: pick("command"), WebFetch: pick("url"), WebSearch: pick("query"),
    Read: pick("file_path"), Glob: pick("pattern"), Grep: pick("pattern"),
  };
  const v = byTool[name] ?? "";
  if (v) return v;
  const s = JSON.stringify(input);
  return s === "{}" ? "" : s;
}

// A tool_result's content → plain text.
function resultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((b) => (b && typeof b === "object" && "text" in b ? String((b as ContentBlock).text ?? "") : JSON.stringify(b))).join("\n");
  }
  return content == null ? "" : JSON.stringify(content, null, 2);
}

type ToolResult = { text: string; isError: boolean };

function ToolCall({ name, input, result }: { name: string; input?: Record<string, unknown>; result?: ToolResult }) {
  const summary = toolArgSummary(name, input);
  return (
    <div className={`tcall${result?.isError ? " tcall-err" : ""}`}>
      <div className="tcall-head">
        <span className="tcall-name">{name}</span>
        {summary && <code className="tcall-arg">{summary}</code>}
      </div>
      {input && Object.keys(input).length > 0 && (
        <details className="tcall-input"><summary>arguments</summary>
          <pre>{JSON.stringify(input, null, 2)}</pre>
        </details>
      )}
      {result && (
        <pre className={`tcall-result${result.isError ? " err" : ""}`}>{result.text || "(no output)"}</pre>
      )}
    </div>
  );
}

function FinalResult({ frame }: { frame: RunEvent }) {
  const text = String(frame.result ?? "");
  const usage = (frame.usage as { input_tokens?: number; output_tokens?: number }) ?? {};
  const dur = typeof frame.duration_ms === "number" ? `${(frame.duration_ms / 1000).toFixed(1)}s` : null;
  const cost = typeof frame.total_cost_usd === "number" ? `$${frame.total_cost_usd.toFixed(3)}` : null;
  const err = frame.is_error === true || frame.subtype === "error_max_turns";
  return (
    <div className={`final${err ? " final-err" : ""}`}>
      <div className="final-label">{err ? "Ended with error" : "Final reply"}</div>
      {text ? <div className="final-text">{text}</div> : <div className="muted">(no reply text)</div>}
      <div className="final-meta muted">
        {[dur && `${dur}`, usage.input_tokens != null && `${usage.input_tokens}/${usage.output_tokens} tok`, cost,
          typeof frame.num_turns === "number" && `${frame.num_turns} turns`].filter(Boolean).join(" · ")}
      </div>
    </div>
  );
}

// Readable transcript: agent prose, tool calls paired with their results, a
// highlighted final reply, and background frames (system/lifecycle) collapsed.
function ReadableTranscript({ events }: { events: RunEvent[] }) {
  const results = new Map<string, ToolResult>();
  for (const f of events) {
    if (f.type !== "user") continue;
    const content = (f.message as { content?: ContentBlock[] } | undefined)?.content ?? [];
    for (const b of content) {
      if (b.type === "tool_result" && b.tool_use_id) {
        results.set(b.tool_use_id, { text: resultText(b.content), isError: b.is_error === true });
      }
    }
  }

  const rendered: React.ReactNode[] = [];
  let noise = 0;
  events.forEach((f, i) => {
    if (f.type === "assistant") {
      const content = (f.message as { content?: ContentBlock[] } | undefined)?.content;
      if (!Array.isArray(content)) return;
      content.forEach((b, j) => {
        if (b.type === "text" && b.text?.trim()) {
          rendered.push(<p key={`${i}-${j}`} className="transcript-text">{b.text}</p>);
        } else if (b.type === "tool_use" && b.name) {
          rendered.push(<ToolCall key={`${i}-${j}`} name={b.name} input={b.input}
            result={b.id ? results.get(b.id) : undefined} />);
        }
      });
    } else if (f.type === "result") {
      rendered.push(<FinalResult key={i} frame={f} />);
    } else if (f.type !== "user") {
      noise++;   // system / lifecycle / rate_limit_event — collapsed below
    }
  });

  return (
    <div className="transcript">
      {rendered.length === 0 && <p className="muted">No transcript events yet.</p>}
      {rendered}
      {noise > 0 && (
        <details className="transcript-noise"><summary>{noise} background events (system, lifecycle)</summary>
          <pre>{JSON.stringify(events.filter((f) => f.type !== "assistant" && f.type !== "result" && f.type !== "user"), null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function RawTranscript({ events }: { events: RunEvent[] }) {
  return (
    <div className="transcript">
      {events.map((f, i) => <pre key={i} className="transcript-frame dim">{JSON.stringify(f, null, 2)}</pre>)}
      {events.length === 0 && <p className="muted">No transcript events yet.</p>}
    </div>
  );
}

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<RunDetailData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [live, setLive] = useState(true);
  const [raw, setRaw] = useState(false);
  const [killing, setKilling] = useState(false);
  const [killError, setKillError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const refetchHeader = useCallback(() => {
    if (!id) return;
    api<RunDetailData>(`/api/runs/${id}`)
      .then((r) => { setRun(r); setLoadError(null); })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load run."));
  }, [id]);

  useEffect(() => { refetchHeader(); }, [refetchHeader]);

  useEffect(() => {
    if (!id) return;
    setEvents([]);
    setLive(true);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/runs/${id}/tail`);
    wsRef.current = ws;
    ws.onmessage = (e) => {
      const frame = JSON.parse(e.data) as RunEvent;
      setEvents((prev) => [...prev, frame]);
      if (frame.terminal) { setLive(false); refetchHeader(); }
    };
    ws.onclose = () => setLive(false);
    return () => ws.close();
  }, [id, refetchHeader]);

  async function kill() {
    if (!id) return;
    setKilling(true);
    setKillError(null);
    try {
      await api(`/api/runs/${id}/kill`, { method: "POST" });
    } catch (err) {
      setKillError(err instanceof Error ? err.message : "Kill failed.");
    } finally {
      setKilling(false);
    }
  }

  if (loadError && !run) return <div className="page"><div className="error">{loadError}</div></div>;
  if (!run) return <div className="page"><p className="muted">Loading…</p></div>;

  const active = isActiveState(run.state);

  return (
    <div className="page page-wide">
      <h1>Run {run.id.slice(0, 8)}</h1>

      <dl className="def-list">
        <dt>Agent</dt>
        <dd><Link to={`/agents/${encodeURIComponent(run.agent)}`}>{run.agent}</Link></dd>
        <dt>State</dt>
        <dd>
          <span className={`chip ${stateChipClass(run.state)}`}>{run.state}</span>
          {live && <span className="muted"> · streaming…</span>}
        </dd>
        <dt>Trigger</dt>
        <dd>
          {run.trigger}
          {run.trigger === "agent" && run.parent_run_id && (
            <> · invoked by <Link to={`/runs/${run.parent_run_id}`}>{run.parent_run_id.slice(0, 8)}</Link> (depth {run.depth})</>
          )}
        </dd>
        <dt>Created</dt>
        <dd>{new Date(run.created_at).toLocaleString()}</dd>
        <dt>Started</dt>
        <dd>{run.started_at ? new Date(run.started_at).toLocaleString() : "—"}</dd>
        <dt>Finished</dt>
        <dd>{run.finished_at ? new Date(run.finished_at).toLocaleString() : "—"}</dd>
        <dt>Tokens in / out</dt>
        <dd>{run.tokens_in ?? "—"} / {run.tokens_out ?? "—"}</dd>
        <dt>Tool calls</dt>
        <dd>{run.tool_calls ?? "—"}</dd>
        {run.secrets_granted && run.secrets_granted.length > 0 && (
          <>
            <dt>Secrets granted</dt>
            <dd className="muted">{run.secrets_granted.join(", ")}</dd>
          </>
        )}
        <dt>Exit code</dt>
        <dd>{run.exit_code ?? "—"}</dd>
        {run.error && (<><dt>Error</dt><dd className="error">{run.error}</dd></>)}
      </dl>

      <div className="secret-row-footer">
        <button onClick={kill} disabled={!active || killing}>{killing ? "Killing…" : "Kill"}</button>
        {killError && <span className="error">{killError}</span>}
      </div>

      <h2>Prompt</h2>
      <pre className="agent-md">{run.prompt}</pre>

      <div className="page-header">
        <h2>Transcript</h2>
        <button className="secondary" onClick={() => setRaw((v) => !v)}>
          {raw ? "Readable view" : "Raw JSON"}
        </button>
      </div>
      {raw ? <RawTranscript events={events} /> : <ReadableTranscript events={events} />}
    </div>
  );
}
