import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Input, Select } from "@ap/ui/field";
import { getArtifact, type Artifact, type ArtifactStats } from "../api";
import { ArtifactGrid } from "../components/artifacts/ArtifactGrid";
import { Lightbox } from "../components/artifacts/Lightbox";
import { useArtifacts } from "../components/artifacts/useArtifacts";
import { formatBytes } from "../lib/artifacts";
import { participantLabel } from "../lib/relay";
import { useTitle } from "../lib/title";

// The artifacts block's page (docs/design/23): everything the platform has
// made or kept, as a grid. Which slice you are looking at lives in the URL —
// kind, source, owner, tag, a search — so a filtered shelf is a link, and the
// open picture lives there too: `/artifacts/<id>` IS the lightbox, which is
// what makes a picture in a room something you can send somebody.

// How long the search box waits before it rewrites the URL. The list itself
// is server-filtered, so this is also how often a keystroke costs a request.
const SEARCH_SETTLE_MS = 250;

const KINDS = ["image", "file"];
const SOURCES = ["upload", "generated", "derived", "tool"];

/** The bytes bar: what the store holds against what it may hold. A meter
 * with a real label, so it says "12 MB of 500 MB" and not "2%". */
function UsageBar({ stats }: { stats: ArtifactStats }) {
  const pct = stats.total_cap > 0
    ? Math.min(100, Math.round((stats.bytes / stats.total_cap) * 100)) : 0;
  const label = `${formatBytes(stats.bytes)} of ${formatBytes(stats.total_cap)} used`;
  return (
    <div className="artifact-usage">
      <div className="artifact-usage-bar" role="meter" aria-label={label} title={label}
           aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
           aria-valuetext={`${pct}%`}
           data-warn={pct >= 90 ? "" : undefined}
           style={{ "--usage-pct": `${pct}%` } as CSSProperties}>
        <span className="artifact-usage-fill" aria-hidden="true" />
      </div>
      <span className="muted artifact-usage-label" aria-hidden="true">
        {stats.count} {stats.count === 1 ? "artifact" : "artifacts"} · {label}
      </span>
    </div>
  );
}

export default function Artifacts() {
  const { id } = useParams<{ id: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [term, setTerm] = useState(params.get("q") ?? "");

  const kind = params.get("kind") ?? "";
  const source = params.get("source") ?? "";
  const owner = params.get("owner") ?? "";
  const tag = params.get("tag") ?? "";
  const q = params.get("q") ?? "";
  const query = useMemo(() => ({ kind, source, owner, tag, q }), [kind, source, owner, tag, q]);
  const view = useArtifacts(query);

  // The open picture: the row from the grid when it is there, fetched on its
  // own when it is not — a deep link to a picture the current filter hides
  // still opens it.
  const inGrid = id ? view.artifacts.find((a) => a.id === id) ?? null : null;
  const shown = inGrid !== null;
  const [fetched, setFetched] = useState<Artifact | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    setFetched(null);
    setMissing(false);
    if (!id || shown) return;
    let on = true;
    getArtifact(id).then((a) => { if (on) setFetched(a); })
      .catch(() => { if (on) setMissing(true); });
    return () => { on = false; };
  }, [id, shown]);
  const open = inGrid ?? (fetched?.id === id ? fetched : null);

  useTitle(open?.name, "Artifacts");

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  }

  // The typed term is the truth on screen; the URL catches up once the typing
  // stops, so the back button steps between searches rather than letters.
  useEffect(() => {
    if ((params.get("q") ?? "") === term) return;
    const t = setTimeout(() => {
      const next = new URLSearchParams(params);
      if (term) next.set("q", term); else next.delete("q");
      setParams(next, { replace: true });
    }, SEARCH_SETTLE_MS);
    return () => clearTimeout(t);
  }, [term, params, setParams]);

  // The pickers offer what is actually on the shelf — an owner with nothing
  // here and a tag nobody used would be filters that can only empty it — plus
  // whatever the URL already names, so a link's filter is never shown as a
  // blank select because its rows have not landed yet.
  const owners = useMemo(() => [...new Set([...view.artifacts.map((a) => a.owner),
                                            ...(owner ? [owner] : [])])].sort(),
                         [view.artifacts, owner]);
  const tags = useMemo(() => [...new Set([...view.artifacts.flatMap((a) => a.tags),
                                          ...(tag ? [tag] : [])])].sort(),
                       [view.artifacts, tag]);

  const here = `/artifacts${params.toString() ? `?${params}` : ""}`;
  const openArtifact = (a: Artifact) =>
    navigate(`/artifacts/${a.id}${params.toString() ? `?${params}` : ""}`);
  const close = () => navigate(here, { replace: true });

  const filtered = !!(kind || source || owner || tag || q);

  return (
    <div className="page page-artifacts">
      <div className="ticket-head">
        <div>
          <h1>Artifacts</h1>
          <p className="muted">
            Everything the platform has made or kept: the pictures the artist drew, the
            files a run saved, the images agents wear. A <code>[[artifact:id]]</code> in a
            room is one of these.
          </p>
        </div>
        <div className="ticket-head-actions">
          <Input type="search" aria-label="Search artifacts"
                 placeholder="Search by name" value={term}
                 onChange={(e) => setTerm(e.target.value)} />
          <Link to="/studio" className="artifact-studio-link">Studio 🎨</Link>
        </div>
      </div>

      {view.error && <div className="error">{view.error}</div>}

      <div className="ticket-filters">
        <label className="muted">Kind{" "}
          <Select aria-label="Filter by kind" value={kind}
                  onChange={(e) => setFilter("kind", e.target.value)}>
            <option value="">all</option>
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </Select>
        </label>
        <label className="muted">Source{" "}
          <Select aria-label="Filter by source" value={source}
                  onChange={(e) => setFilter("source", e.target.value)}>
            <option value="">any</option>
            {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        </label>
        <label className="muted">Owner{" "}
          <Select aria-label="Filter by owner" value={owner}
                  onChange={(e) => setFilter("owner", e.target.value)}>
            <option value="">anyone</option>
            {owners.map((o) => <option key={o} value={o}>{participantLabel(o, view.me)}</option>)}
          </Select>
        </label>
        <label className="muted">Tag{" "}
          <Select aria-label="Filter by tag" value={tag}
                  onChange={(e) => setFilter("tag", e.target.value)}>
            <option value="">any</option>
            {tags.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
        </label>
      </div>

      {view.stats && <UsageBar stats={view.stats} />}

      {/* A shelf that could not be read is not an empty shelf: "nothing here"
          under an error banner is the page lying. The hook keeps retrying. */}
      {!view.loaded
        ? <p className="muted">Loading…</p>
        : view.error && view.artifacts.length === 0
          ? <p className="ticket-failed">The artifacts could not be loaded. Still trying…</p>
          : view.artifacts.length === 0
            ? (
              <p className="muted artifact-empty">
                Nothing here yet — try the <Link to="/studio">Studio</Link>
                {filtered && <> or <Link to="/artifacts">clear the filters</Link></>}.
              </p>
            )
            : <ArtifactGrid artifacts={view.artifacts} me={view.me} onOpen={openArtifact} />}

      {open && <Lightbox artifact={open} me={view.me} onClose={close} onDelete={view.remove} />}
      {id && missing && (
        <p className="error artifact-empty">
          There is no artifact <code>{id}</code> — it may have been deleted.{" "}
          <Link to="/artifacts">Back to the shelf</Link>.
        </p>
      )}
    </div>
  );
}
