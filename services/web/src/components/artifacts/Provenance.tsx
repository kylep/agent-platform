import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Chip } from "@ap/ui/chip";
import type { Artifact } from "../../api";
import { ARTIFACT_ID_RE, formatBytes, parentOf, promptOf } from "../../lib/artifacts";
import { participantLabel } from "../../lib/relay";
import { ago } from "../../lib/time";
import { Face } from "../relay/Face";

// Where a picture came from (docs/design/23): the row's own columns, then
// whatever its meta says about how it was made. A generated image's meta is
// its provenance — provider, model, prompt, params, seed, cost, references —
// and a derived one names its parent. The keys were chosen by whatever wrote
// the row, so every one is read defensively and shown only when it is there.

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="artifact-prov-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function ids(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && ARTIFACT_ID_RE.test(x)) : [];
}

export function Provenance({ artifact: a, me }: { artifact: Artifact; me: string | null }) {
  const meta = a.meta ?? {};
  const prompt = promptOf(a);
  const parent = parentOf(a);
  const references = ids(meta.reference_ids);
  const params = meta.params && typeof meta.params === "object"
    ? Object.entries(meta.params as Record<string, unknown>)
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
    : [];
  return (
    <dl className="artifact-prov">
      <Row label="owner">
        <span className="artifact-prov-who">
          <Face participant={a.owner} size={18} />
          {participantLabel(a.owner, me)}
        </span>
      </Row>
      <Row label="source">{a.source}</Row>
      {a.created_at && <Row label="created">{ago(a.created_at)}</Row>}
      <Row label="size">
        {formatBytes(a.size)}
        {a.width && a.height ? ` · ${a.width}×${a.height}` : ""}
        {" · "}<code>{a.mime}</code>
      </Row>
      {typeof meta.model === "string" && meta.model && (
        <Row label="model">
          {meta.model}
          {typeof meta.provider === "string" && meta.provider ? ` (${meta.provider})` : ""}
        </Row>
      )}
      {prompt && <Row label="prompt"><q>{prompt}</q></Row>}
      {params.length > 0 && (
        <Row label="params">
          {params.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}
        </Row>
      )}
      {(typeof meta.seed === "number" || typeof meta.seed === "string") && (
        <Row label="seed"><code>{String(meta.seed)}</code></Row>
      )}
      {(typeof meta.cost_usd === "number" || typeof meta.duration_ms === "number") && (
        <Row label={meta.billing === "codex_allowance" ? "usage" : "cost"}>
          {meta.billing === "codex_allowance" ? "Codex allowance" :
            (typeof meta.cost_usd === "number" ? `$${meta.cost_usd.toFixed(2)}` : "")}
          {typeof meta.cost_usd === "number" && typeof meta.duration_ms === "number" ? " · " : ""}
          {typeof meta.duration_ms === "number" ? `${(meta.duration_ms / 1000).toFixed(1)} s` : ""}
        </Row>
      )}
      {references.length > 0 && (
        <Row label="references">
          {references.map((id) => (
            <Link key={id} to={`/artifacts/${id}`} className="artifact-prov-ref">{id.slice(0, 8)}</Link>
          ))}
        </Row>
      )}
      {parent && (
        <Row label="derived from">
          <Link to={`/artifacts/${parent}`} className="artifact-prov-ref">{parent.slice(0, 8)}</Link>
        </Row>
      )}
      {a.run_id && <Row label="run"><Link to={`/runs/${a.run_id}`}>{a.run_id.slice(0, 8)} ↗</Link></Row>}
      {a.tags.length > 0 && (
        <Row label="tags">{a.tags.map((t) => <Chip key={t}>{t}</Chip>)}</Row>
      )}
      <Row label="sha256"><code title={a.sha256}>{a.sha256.slice(0, 12)}…</code></Row>
    </dl>
  );
}
