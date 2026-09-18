import { useState } from "react";
import { Link } from "react-router-dom";
import { Chip } from "@ap/ui/chip";
import type { Artifact } from "../../api";
import { fileGlyph, provenanceLine, safeArtifactUrl } from "../../lib/artifacts";
import { Face } from "../relay/Face";
import { Lightbox } from "./Lightbox";
import { useArtifact } from "./useArtifacts";

// One artifact as a card (docs/design/23): the thumb (or a file's glyph), the
// name, the owner's face and one line of provenance. The SAME component is a
// tile in the Studio's grid and a card in a room — `[[artifact:<id>]]` in
// prose, the `#art` event the API posts — so a picture reads as one object
// wherever it is met; the stylesheet decides which way it lies.
//
// Given a row, it draws it. Given only an id — a chip in a message — it asks
// for the row, and an id nobody has is a muted chip saying so rather than a
// card with a hole in it.

export function ArtifactCard({ id, artifact, me = null, onOpen }: {
  id?: string;
  artifact?: Artifact;
  me?: string | null;
  // Where the page owns the lightbox (the grid, whose URL names the open
  // one) — absent, the card opens its own.
  onOpen?: (a: Artifact) => void;
}) {
  const look = useArtifact(artifact ? null : (id ?? null));
  const row = artifact ?? look.artifact;
  if (!row) {
    return look.state === "loading" && !artifact
      ? <span className="artifact-card artifact-card-pending" aria-busy="true" />
      : <Chip className="artifact-missing" title={id}>artifact not found</Chip>;
  }
  return <Card artifact={row} me={me} onOpen={onOpen} />;
}

function Card({ artifact: a, me, onOpen }: {
  artifact: Artifact; me: string | null; onOpen?: (a: Artifact) => void;
}) {
  const [open, setOpen] = useState(false);
  // The picture that failed to load, so a broken thumb is the file glyph
  // rather than the browser's own broken-image icon.
  const [broken, setBroken] = useState(false);
  const thumb = a.kind === "image" && !broken ? safeArtifactUrl(a.thumb_url) : null;
  const prov = provenanceLine(a);

  function show() {
    if (onOpen) onOpen(a); else setOpen(true);
  }

  return (
    <div className={`artifact-card${a.kind === "file" ? " file" : ""}`} data-artifact-id={a.id}>
      <button type="button" className="artifact-card-thumb" onClick={show}
              aria-label={`Open ${a.name}`}>
        {thumb
          ? <img src={thumb} alt="" loading="lazy" onError={() => setBroken(true)} />
          : <span className="artifact-glyph" aria-hidden="true">{fileGlyph(a.mime)}</span>}
      </button>
      <div className="artifact-card-meta">
        <div className="artifact-card-name" title={a.name}>{a.name}</div>
        <div className="artifact-card-prov muted">
          <Face participant={a.owner} size={18} />
          <span className="artifact-card-provline" title={prov}>{prov}</span>
        </div>
        {/* Deliberately a link and not the button again: the button opens the
            picture here, the link is the page it lives on. */}
        <Link to={`/artifacts/${a.id}`} className="artifact-card-link"
              aria-label={`${a.name} in Artifacts`}>
          open ↗
        </Link>
      </div>
      {open && <Lightbox artifact={a} me={me} onClose={() => setOpen(false)} />}
    </div>
  );
}
