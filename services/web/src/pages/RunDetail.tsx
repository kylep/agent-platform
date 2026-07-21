import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type RunDetailData, type RunEvent } from "../api";
import { isActiveState, stateChipClass } from "./Runs";

type ContentBlock = {
  type?: string;
  text?: string;
  name?: string;
  input?: unknown;
};

function AssistantFrame({ frame }: { frame: RunEvent }) {
  const message = frame.message as { content?: ContentBlock[] } | undefined;
  const content = message?.content;
  if (!Array.isArray(content)) {
    return <pre className="transcript-frame dim">{JSON.stringify(frame, null, 2)}</pre>;
  }
  return (
    <>
      {content.map((block, i) => {
        if (block.type === "text") {
          return <p key={i} className="transcript-text">{block.text}</p>;
        }
        if (block.type === "tool_use") {
          return (
            <details key={i} className="transcript-tool">
              <summary>{block.name}</summary>
              <pre>{JSON.stringify(block.input, null, 2)}</pre>
            </details>
          );
        }
        return <pre key={i} className="transcript-frame dim">{JSON.stringify(block, null, 2)}</pre>;
      })}
    </>
  );
}

function TranscriptFrame({ frame, index }: { frame: RunEvent; index: number }) {
  if (frame.type === "assistant") {
    return <AssistantFrame frame={frame} />;
  }
  if (frame.type === "tool_use") {
    return (
      <details className="transcript-tool">
        <summary>{String(frame.name ?? "tool")}</summary>
        <pre>{JSON.stringify(frame.input, null, 2)}</pre>
      </details>
    );
  }
  return (
    <pre key={index} className="transcript-frame dim">
      {JSON.stringify(frame, null, 2)}
    </pre>
  );
}

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<RunDetailData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [live, setLive] = useState(true);
  const [killing, setKilling] = useState(false);
  const [killError, setKillError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const refetchHeader = useCallback(() => {
    if (!id) return;
    api<RunDetailData>(`/api/runs/${id}`)
      .then((r) => { setRun(r); setLoadError(null); })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load run."));
  }, [id]);

  useEffect(() => {
    refetchHeader();
  }, [refetchHeader]);

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
      if (frame.terminal) {
        setLive(false);
        refetchHeader();
      }
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
        <dd>{run.agent}</dd>
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
        {run.error && (
          <>
            <dt>Error</dt>
            <dd className="error">{run.error}</dd>
          </>
        )}
      </dl>

      <div className="secret-row-footer">
        <button onClick={kill} disabled={!active || killing}>
          {killing ? "Killing…" : "Kill"}
        </button>
        {killError && <span className="error">{killError}</span>}
      </div>

      <h2>Prompt</h2>
      <pre className="agent-md">{run.prompt}</pre>

      <h2>Transcript</h2>
      <div className="transcript">
        {events.map((frame, i) => (
          <TranscriptFrame key={i} frame={frame} index={i} />
        ))}
        {events.length === 0 && <p className="muted">No transcript events yet.</p>}
      </div>
    </div>
  );
}
