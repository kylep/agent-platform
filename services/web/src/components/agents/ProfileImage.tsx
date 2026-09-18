import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { FormDialog } from "@ap/ui/dialog";
import { Textarea } from "@ap/ui/field";
import {
  artifactModels, generateArtifact, setAgentImage, uploadArtifact,
  type Artifact, type ImageModel, type RelayFace,
} from "../../api";
import { Face } from "../relay/Face";
import { defaultModel, errorDetail, geometryFor } from "../studio/generate";
import { ModelSelect } from "../studio/ModelSelect";
import { PickArtifactDialog } from "../studio/PickArtifactDialog";

// An agent's picture (docs/design/23). Deliberately NOT part of the editor's
// draft: the picture has its own route, like a webhook secret does, so
// changing it never rides along with a definition save and never lands in
// the change log as an edit. Every way of setting it — a file, a picture the
// store already holds, a fresh generation — ends in the same two steps: the
// image route, then a refetch, because the face the API derives from the
// artifact is the truth and the page should not guess at it.

/** The two read-only fields a row carries for its picture. */
export type AgentImage = { image_artifact_id?: string | null; face?: RelayFace | null };

// The house style the artist's own prompt names, so a portrait made here and
// one an agent makes for itself come out of the same mould.
export const HOUSE_STYLE = "flat, friendly avatar, square, centred, no text";

/** `Portrait of "<name>": <description>. <house style>` — the description's
 * own full stop is dropped first so the join reads as one sentence. */
export function portraitPrompt(name: string, description: string): string {
  const about = description.trim().replace(/[.\s]+$/, "");
  return about
    ? `Portrait of "${name}": ${about}. ${HOUSE_STYLE}`
    : `Portrait of "${name}". ${HOUSE_STYLE}`;
}

// The most a picture may weigh on its way up. The API has its own cap; this
// one saves the round trip (and the wait) for a file that can only be refused.
export const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;

export function ProfileImage({ name, description, image, onChanged }: {
  name: string;
  description: string;
  image: AgentImage;
  /** Refetch the row once the picture has changed. */
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  // `busy` as the render sees it lags a tick behind; two drops in the same
  // tick would both read it false. The ref is the gate, the state the look.
  const inFlight = useRef(false);

  async function apply(id: string | null) {
    await setAgentImage(name, id);
    await onChanged();
  }

  // One write at a time, whatever started it, and every refusal lands in
  // this section's Banner — including one for a picture picked in a dialog,
  // so the dialog can close without taking the reason with it.
  async function attempt(fn: () => Promise<void>): Promise<boolean> {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true); setError(null);
    try {
      await fn();
      return true;
    } catch (err) {
      setError(errorDetail(err, "The picture was not changed."));
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function upload(file: File | undefined) {
    if (!file || inFlight.current) return;
    // Refused here rather than by the API: a wrong file costs a round trip
    // and, for a big one, a long wait before the same answer.
    if (!file.type.startsWith("image/")) {
      setError(`${file.name} is not an image (${file.type || "unknown type"}).`);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(`${file.name} is ${(file.size / 1024 / 1024).toFixed(1)} MiB; a profile image can be at most 8 MiB.`);
      return;
    }
    const form = new FormData();
    form.append("file", file);
    return attempt(async () => apply((await uploadArtifact(form)).id));
  }

  // The picture a dialog picked or painted. The dialog stays up while the
  // write is out and goes away either way after: a refusal reads in the
  // Banner under the face, where it is not hidden behind an overlay.
  async function wear(a: Artifact) {
    await attempt(() => apply(a.id));
    setChoosing(false);
    setGenerating(false);
  }

  function drop(e: DragEvent<HTMLElement>) {
    e.preventDefault();
    setOver(false);
    upload(e.dataTransfer.files[0]);
  }

  return (
    <section className="profile-image" aria-label="Profile image">
      <Face participant={`agent:${name}`} face={image.face} size={96} />
      <div className="profile-image-actions">
        <div className="drop-zone" data-over={over ? "yes" : undefined}
             onDragOver={(e) => { e.preventDefault(); setOver(true); }}
             onDragLeave={() => setOver(false)}
             onDrop={drop}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
               strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 16V4m0 0l-4 4m4-4l4 4" />
            <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
          </svg>
          <span className="drop-zone-line">
            <span>Drop an image here, or</span>
            <Button variant="secondary" size="sm" disabled={busy}
                    onClick={() => fileRef.current?.click()}>
              {busy ? "Working…" : "Upload"}
            </Button>
          </span>
          {/* The one raw input in the app: a file picker cannot be styled, so
              it is kept off-screen and the button beside it opens it. Cleared
              after each pick so the same file can be chosen twice in a row. */}
          <input ref={fileRef} type="file" accept="image/*" className="sr-only" tabIndex={-1}
                 aria-label="Upload a profile image"
                 onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
        </div>
        <div className="profile-image-buttons">
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => setChoosing(true)}>
            Choose from artifacts
          </Button>
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => setGenerating(true)}>
            Generate
          </Button>
          {image.image_artifact_id && (
            <Button variant="secondary" size="sm" disabled={busy}
                    onClick={() => attempt(() => apply(null))}>
              Remove
            </Button>
          )}
        </div>
        <p className="muted profile-image-note">
          Worn everywhere the agent's face is drawn. A picture made here is an artifact
          of yours, so it is also in <Link to="/artifacts">Artifacts</Link> and #art.
        </p>
      </div>
      {error && <Banner variant="danger" className="profile-image-error">{error}</Banner>}

      {choosing && (
        <PickArtifactDialog title="Choose a picture" submitLabel="Use as profile image"
                            busyLabel="Setting…" applying={busy} onUse={wear}
                            onCancel={() => setChoosing(false)} />
      )}
      {generating && (
        <GenerateDialog name={name} description={description} applying={busy}
                        onUse={wear} onCancel={() => setGenerating(false)} />
      )}
    </section>
  );
}

type Stage = "form" | "busy" | "done";

// A new portrait. The generation is synchronous on the API's side and can
// take minutes, so the wait is shown as a count rather than a spinner, and
// the picture is shown before it is worn: nothing touches the agent until
// "Use" — a bad likeness costs its price, never the face.
//
// Closing does not stop a generation — the API is already painting and the
// picture is billed — so while one is out, Cancel, Escape and the overlay
// are ignored and the dialog says so; the result lands in Artifacts and #art
// either way. The "Use" write is the section's, as the picker's is.
function GenerateDialog({ name, description, applying, onUse, onCancel }: {
  name: string;
  description: string;
  applying: boolean;
  onUse: (a: Artifact) => void;
  onCancel: () => void;
}) {
  const [models, setModels] = useState<ImageModel[] | null>(null);
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState(() => portraitPrompt(name, description));
  const [stage, setStage] = useState<Stage>("form");
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    artifactModels()
      .then((ms) => {
        setModels(ms);
        setModel(defaultModel(ms)?.id ?? "");
      })
      .catch((err) => { setModels([]); setError(errorDetail(err, "Could not list the models.")); });
  }, []);

  useEffect(() => {
    if (stage !== "busy") return;
    setElapsed(0);
    const tick = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(tick);
  }, [stage]);

  const chosen = models?.find((m) => m.id === model) ?? null;
  const none = models !== null && !models.some((m) => m.configured);

  async function generate() {
    if (!chosen || !prompt.trim()) return;
    setStage("busy"); setError(null);
    try {
      const a = await generateArtifact({
        model: chosen.id, prompt: prompt.trim(), ...geometryFor(chosen), tags: ["face"],
      });
      setResult(a);
      setStage("done");
    } catch (err) {
      setError(errorDetail(err, "The picture was not generated."));
      setStage("form");
    }
  }

  const label = stage === "busy" ? `Generating… ${elapsed} s`
    : stage === "done" ? (applying ? "Setting…" : "Use as profile image")
    : "Generate";
  const held = stage === "busy" || applying;

  return (
    <FormDialog open title="Generate a portrait" submitLabel={label}
                disabled={held || (stage === "form" && (!chosen || !prompt.trim()))}
                onSubmit={stage === "done" ? () => { if (result) onUse(result); } : generate}
                onCancel={() => { if (!held) onCancel(); }}>
      {error && <Banner variant="danger">{error}</Banner>}
      {none && (
        <Banner variant="info">
          No image provider is configured — add a key under{" "}
          <Link to="/secrets">Settings → Secrets</Link>.
        </Banner>
      )}
      {stage === "busy" && (
        <div className="profile-image-stage">
          <div className="profile-image-shimmer" aria-hidden="true" />
          <p className="muted">
            Generating… {elapsed} s. A portrait can take a minute or two, and once started it
            is painted (and billed) whether or not you wait — it lands in Artifacts and #art.
          </p>
        </div>
      )}
      {stage === "done" && result && (
        <div className="profile-image-stage">
          <img className="profile-image-preview" src={result.content_url}
               alt={`Generated portrait: ${result.name}`} />
          <p className="muted">
            {result.name} —{" "}
            <Button variant="link" disabled={applying}
                    onClick={() => { setResult(null); setStage("form"); }}>
              try again
            </Button>
          </p>
        </div>
      )}
      <label className="field-label" htmlFor="profile-gen-model">Model</label>
      <ModelSelect id="profile-gen-model" models={models} value={model}
                   disabled={stage !== "form"} onChange={setModel} />
      <label className="field-label" htmlFor="profile-gen-prompt">Prompt</label>
      <Textarea id="profile-gen-prompt" rows={4} value={prompt} disabled={stage !== "form"}
                onChange={(e) => setPrompt(e.target.value)} />
    </FormDialog>
  );
}
