import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Banner } from "@ap/ui/banner";
import {
  artifactModels, artifactStats, generateArtifact, getArtifact,
  type Artifact, type ArtifactStats, type ImageModel,
} from "../api";
import { useArtifacts } from "../components/artifacts/useArtifacts";
import { Compose } from "../components/studio/Compose";
import {
  EMPTY_DRAFT, defaultModel, errorDetail, geometryFor, requestFor, type Draft,
} from "../components/studio/generate";
import { RecentStrip } from "../components/studio/RecentStrip";
import { MAX_REFERENCES } from "../components/studio/ReferenceStrip";
import { Stage } from "../components/studio/Stage";
import { useTitle } from "../lib/title";

// The Studio (docs/design/23): where a picture is asked for. Compose on the
// left, the stage on the right, the last pictures across the bottom. The
// stage is a URL — `/studio/<id>` — so a result can be sent to somebody and
// the back button steps between pictures; `/studio?ref=<id>` is the
// lightbox's "open in Studio", which arrives as a reference rather than on
// the stage, because the point of opening a picture here is to start from it.

// How many recent pictures the strip shows.
const RECENT = 24;

export default function Studio() {
  useTitle("Studio");
  const { id } = useParams<{ id: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();

  const [models, setModels] = useState<ImageModel[] | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [references, setReferences] = useState<Artifact[]>([]);
  const [staged, setStaged] = useState<Artifact | null>(null);
  const [missing, setMissing] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [stats, setStats] = useState<ArtifactStats | null>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);

  const query = useMemo(() => ({ kind: "image", limit: RECENT }), []);
  const view = useArtifacts(query);
  const chosen = models?.find((m) => m.id === draft.model) ?? null;

  useEffect(() => {
    artifactModels()
      .then((ms) => {
        setModels(ms);
        setDraft((d) => (d.model ? d : { ...d, model: defaultModel(ms)?.id ?? "" }));
      })
      .catch((err) => { setModels([]); setError(errorDetail(err, "Could not list the models.")); });
  }, []);

  // The store's sums arrive with the list and move with the feed; a
  // generation made here is asked for straight away rather than waiting
  // for its own frame to come back round.
  useEffect(() => { if (view.stats) setStats(view.stats); }, [view.stats]);
  const refreshStats = useCallback(() => artifactStats().then(setStats).catch(() => {}), []);

  // A change of model resets the geometry to that model's square and drops
  // a quality it does not speak, so the form never holds a value the
  // provider would reject.
  useEffect(() => {
    if (!chosen) return;
    const g = geometryFor(chosen);
    setDraft((d) => ({
      ...d, size: g.size ?? "", aspect: g.aspect ?? "",
      quality: chosen.qualities?.includes(d.quality) ? d.quality : "",
    }));
  }, [chosen]);

  useEffect(() => {
    if (!pending) return;
    setElapsed(0);
    const tick = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(tick);
  }, [pending]);

  // The stage follows the URL: the row from the strip when it is there,
  // fetched on its own when it is not — a link to a picture older than the
  // strip still opens it.
  const staging = staged?.id ?? null;
  // The strip as a ref, not a dependency: once the row is on the stage a
  // frame that re-lists the strip must not fetch it again.
  const strip = useRef(view.artifacts);
  strip.current = view.artifacts;
  useEffect(() => {
    setMissing(null);
    if (!id) { setStaged(null); return; }
    if (staging === id) return;
    const inStrip = strip.current.find((a) => a.id === id);
    if (inStrip) { setStaged(inStrip); return; }
    let on = true;
    getArtifact(id)
      .then((a) => { if (on) setStaged(a); })
      .catch(() => { if (on) { setStaged(null); setMissing(id); } });
    return () => { on = false; };
  }, [id, staging]);

  // `?ref=<id>` arrives as a reference, once, and is taken off the URL so a
  // reload does not add it twice.
  const ref = params.get("ref");
  useEffect(() => {
    if (!ref) return;
    let on = true;
    getArtifact(ref)
      .then((a) => { if (on) addReference(a); })
      .catch((err) => { if (on) setError(errorDetail(err, "That reference could not be loaded.")); })
      .finally(() => {
        if (!on) return;
        setParams((prev) => {
          const next = new URLSearchParams(prev);
          next.delete("ref");
          return next;
        }, { replace: true });
      });
    return () => { on = false; };
  }, [ref, setParams]);

  function addReference(a: Artifact) {
    setReferences((prev) =>
      prev.some((r) => r.id === a.id) || prev.length >= MAX_REFERENCES ? prev : [...prev, a]);
  }

  function onDraft(patch: Partial<Draft>) {
    setDraft((d) => ({ ...d, ...patch }));
  }

  async function generate() {
    if (!chosen || !draft.prompt.trim() || pending) return;
    setPending(true); setError(null); setNotice(null);
    try {
      const a = await generateArtifact(requestFor(chosen, draft, references));
      view.absorb([a]);
      setStaged(a);
      setMissing(null);
      navigate(`/studio/${a.id}`, { replace: true });
      refreshStats();
    } catch (err) {
      setError(errorDetail(err, "The picture was not generated."));
    } finally {
      setPending(false);
    }
  }

  function iterate(a: Artifact) {
    addReference(a);
    setStaged(null);
    navigate("/studio", { replace: true });
    promptRef.current?.focus();
  }

  async function remove(a: Artifact) {
    await view.remove(a);
    setReferences((prev) => prev.filter((r) => r.id !== a.id));
    setStaged(null);
    navigate("/studio", { replace: true });
    refreshStats();
  }

  function open(a: Artifact) {
    setStaged(a);
    setMissing(null);
    navigate(`/studio/${a.id}`);
  }

  return (
    <div className="page page-studio">
      <div className="page-header studio-head">
        <div>
          <h1>Studio</h1>
          <p className="muted studio-lede">
            Ask for a picture. What comes back is an artifact — kept, shown in #art,
            ready to be worn by an agent or started from again.
          </p>
        </div>
      </div>
      {notice && <Banner variant="ok">{notice}</Banner>}
      <div className="studio">
        <Compose models={models} chosen={chosen} draft={draft} onDraft={onDraft}
                 references={references} onAddReference={addReference}
                 onRemoveReference={(rid) => setReferences((prev) => prev.filter((r) => r.id !== rid))}
                 result={staged} pending={pending} elapsed={elapsed} stats={stats}
                 error={error} onError={setError} onGenerate={generate} promptRef={promptRef} />
        <Stage artifact={pending ? null : staged} pending={pending} elapsed={elapsed}
               modelLabel={chosen?.label ?? null} me={view.me} missing={missing}
               onIterate={iterate} onDelete={remove}
               onWorn={(agent) => setNotice(`Now the face of ${agent}.`)} />
      </div>
      <RecentStrip artifacts={view.artifacts.slice(0, RECENT)} current={staged?.id ?? null}
                   loaded={view.loaded} onOpen={open} />
    </div>
  );
}
