import { useRef, useState, type DragEvent } from "react";
import { Button } from "@ap/ui/button";
import { uploadArtifact, type Artifact } from "../../api";
import { fileGlyph, safeArtifactUrl } from "../../lib/artifacts";
import { MAX_UPLOAD_BYTES } from "../agents/ProfileImage";
import { errorDetail } from "./generate";
import { PickArtifactDialog } from "./PickArtifactDialog";

// The pictures a generation starts from (docs/design/23). Only the models
// that edit take any, so the strip is drawn either way but says so when the
// chosen model would ignore them — a reference that silently does nothing is
// worse than none. A dropped file becomes an artifact first and a reference
// second: the generate route takes ids, and a picture worth starting from is
// worth keeping.

/** The most references a request carries: what the edit endpoints accept. */
export const MAX_REFERENCES = 4;

export function ReferenceStrip({ references, enabled, modelLabel, result, busy, onAdd, onRemove, onError }: {
  references: Artifact[];
  /** Whether the chosen model takes references at all. */
  enabled: boolean;
  modelLabel: string | null;
  /** What is on the stage, so it can be pulled in with one click. */
  result: Artifact | null;
  busy: boolean;
  onAdd: (a: Artifact) => void;
  onRemove: (id: string) => void;
  onError: (message: string | null) => void;
}) {
  const [over, setOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [picking, setPicking] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  // `uploading` as the render sees it lags a tick behind; two drops in the
  // same tick would both read it false. The ref is the gate, the state the
  // look — the profile image's own shape.
  const inFlight = useRef(false);
  const full = references.length >= MAX_REFERENCES;
  const locked = !enabled || busy || full || uploading;
  const resultHeld = result !== null && references.some((r) => r.id === result.id);

  async function upload(file: File | undefined) {
    if (!file || locked || inFlight.current) return;
    // Refused here rather than by the API, as the profile image does: a
    // wrong file costs a round trip and, for a big one, a long wait.
    if (!file.type.startsWith("image/")) {
      onError(`${file.name} is not an image (${file.type || "unknown type"}).`);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      onError(`${file.name} is ${(file.size / 1024 / 1024).toFixed(1)} MiB; a reference can be at most 8 MiB.`);
      return;
    }
    inFlight.current = true;
    setUploading(true); onError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      onAdd(await uploadArtifact(form));
    } catch (err) {
      onError(errorDetail(err, "The file was not uploaded."));
    } finally {
      inFlight.current = false;
      setUploading(false);
    }
  }

  function drop(e: DragEvent<HTMLElement>) {
    e.preventDefault();
    setOver(false);
    upload(e.dataTransfer.files[0]);
  }

  return (
    <div className="reference-strip" data-disabled={enabled ? undefined : ""}>
      <div className="reference-strip-head">
        <span className="field-label">References</span>
        <span className="muted reference-strip-note">
          {!enabled
            ? `${modelLabel ?? "This model"} does not take references`
            : `${references.length} of ${MAX_REFERENCES}`}
        </span>
      </div>
      {references.length > 0 && (
        <ul className="reference-thumbs" aria-label="References">
          {references.map((r) => {
            const thumb = safeArtifactUrl(r.thumb_url);
            return (
              <li key={r.id} className="reference-thumb" title={r.name}>
                {thumb
                  ? <img src={thumb} alt={r.name} />
                  : <span className="artifact-glyph" aria-hidden="true">{fileGlyph(r.mime)}</span>}
                <Button variant="secondary" size="sm" className="reference-remove"
                        aria-label={`Remove ${r.name}`} disabled={busy}
                        onClick={() => onRemove(r.id)}>
                  ×
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="drop-zone" data-over={over && !locked ? "yes" : undefined}
           onDragOver={(e) => { e.preventDefault(); if (!locked) setOver(true); }}
           onDragLeave={() => setOver(false)}
           onDrop={drop}>
        <span>Drop an image here, or</span>
        <Button variant="secondary" size="sm" disabled={locked}
                onClick={() => { if (!inFlight.current) fileRef.current?.click(); }}>
          {uploading ? "Uploading…" : "Upload"}
        </Button>
        <Button variant="secondary" size="sm" disabled={locked}
                onClick={() => { if (!inFlight.current) setPicking(true); }}>
          Pick from artifacts
        </Button>
        {result && (
          <Button variant="secondary" size="sm" disabled={locked || resultHeld}
                  onClick={() => onAdd(result)}>
            Use result
          </Button>
        )}
        {/* A file picker cannot be styled, so it is kept off-screen and the
            button beside it opens it; cleared after each pick so the same
            file can be chosen twice in a row. */}
        <input ref={fileRef} type="file" accept="image/*" className="sr-only" tabIndex={-1}
               aria-label="Upload a reference image"
               onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
      </div>
      {picking && (
        <PickArtifactDialog title="Pick a reference" submitLabel="Use as reference"
                            busyLabel="Adding…" applying={false}
                            onUse={(a) => { onAdd(a); setPicking(false); }}
                            onCancel={() => setPicking(false)} />
      )}
    </div>
  );
}
