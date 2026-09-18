import * as RadixDialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { ConfirmDialog } from "@ap/ui/dialog";
import type { Artifact } from "../../api";
import { fileGlyph, formatBytes, provenanceLine, safeArtifactUrl } from "../../lib/artifacts";
import { Provenance } from "./Provenance";

// The picture, large, with everything known about it beside it (docs/design/23).
// Radix underneath for the parts a lightbox has to get right and nobody
// remembers to — the focus trap, escape, the aria wiring — and the platform's
// own box rather than @ap/ui's dialog shell, which is sized for a form. The
// title is the name and the description is the provenance line, so the
// dialog announces "a-dragon.png, gpt-image-1 · 3 s · $0.04" rather than
// "dialog".
//
// A file has no picture to show; it gets its glyph and a download, since the
// content route serves anything but the four rasters as an attachment.

export function Lightbox({ artifact: a, me, onClose, onDelete }: {
  artifact: Artifact;
  me: string | null;
  onClose: () => void;
  // Absent where deleting is not offered (a card in a room) — then the
  // button is not drawn at all rather than drawn and dead.
  onDelete?: (a: Artifact) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Off the artifacts routes there is no picture and no download: the row is
  // shown, its bytes are not fetched.
  const content = safeArtifactUrl(a.content_url);

  async function remove() {
    if (!onDelete) return;
    setDeleting(true);
    try {
      await onDelete(a);
      setConfirming(false);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete this artifact.");
      setConfirming(false);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <RadixDialog.Root open onOpenChange={(o) => { if (!o) onClose(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="artifact-lightbox-overlay" />
        <RadixDialog.Content className="artifact-lightbox">
          <div className="artifact-lightbox-head">
            <RadixDialog.Title className="artifact-lightbox-title" title={a.name}>{a.name}</RadixDialog.Title>
            <RadixDialog.Description className="muted artifact-lightbox-desc">
              {provenanceLine(a)}
            </RadixDialog.Description>
            <RadixDialog.Close asChild>
              <button type="button" className="artifact-lightbox-close" aria-label="Close">✕</button>
            </RadixDialog.Close>
          </div>
          <div className="artifact-lightbox-body">
            <div className="artifact-lightbox-stage">
              {a.kind === "image" && content
                ? <img src={content} alt={a.name} />
                : (
                  <div className="artifact-lightbox-file">
                    <span className="artifact-glyph" aria-hidden="true">{fileGlyph(a.mime)}</span>
                    <span className="muted">{a.mime} · {formatBytes(a.size)}</span>
                  </div>
                )}
            </div>
            <aside className="artifact-lightbox-side" aria-label="Provenance">
              <Provenance artifact={a} me={me} />
              {error && <div className="error">{error}</div>}
              <div className="artifact-lightbox-actions">
                <Link to={`/studio?ref=${a.id}`} className="artifact-lightbox-studio">
                  Open in Studio ↗
                </Link>
                {content && <a href={content} download={a.name}>Download</a>}
                {onDelete && (
                  <Button variant="danger" size="sm" onClick={() => setConfirming(true)}
                          disabled={deleting}>
                    Delete
                  </Button>
                )}
              </div>
            </aside>
          </div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
      <ConfirmDialog open={confirming} title="Delete this artifact?" confirmLabel="Delete"
                     onConfirm={remove} onCancel={() => setConfirming(false)}>
        <p><code>{a.name}</code> stops being served everywhere it is shown — every card
          that names it becomes "artifact not found". A face wearing it goes back to
          its emoji.</p>
      </ConfirmDialog>
    </RadixDialog.Root>
  );
}
