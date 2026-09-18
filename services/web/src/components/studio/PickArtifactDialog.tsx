import { useEffect, useRef, useState } from "react";
import { Banner } from "@ap/ui/banner";
import { FormDialog } from "@ap/ui/dialog";
import { listArtifacts, type Artifact } from "../../api";
import { ArtifactGrid } from "../artifacts/ArtifactGrid";
import { errorDetail } from "./generate";

// A picture the store already holds (docs/design/23): the one picker, for
// the Studio's reference strip and an agent's profile image alike. The grid
// is the Studio's own, so a tile here is the same tile there; clicking one
// selects it rather than opening the lightbox, since the point is to pick,
// not to inspect.
//
// Whatever is done with the pick is the caller's (`onUse` hands the row up
// and the caller reports on it); while the caller's write is out (`applying`),
// Cancel, Escape and the overlay are ignored — the @ap/ui dialog's Cancel
// cannot be disabled from outside, so the callback is the gate — and the
// dialog is closed by the caller when the write has an answer.

export function PickArtifactDialog({ title, submitLabel, busyLabel, applying, onUse, onCancel }: {
  title: string;
  submitLabel: string;
  /** The submit button's word while the caller's write is out. */
  busyLabel: string;
  applying: boolean;
  onUse: (a: Artifact) => void;
  onCancel: () => void;
}) {
  const [rows, setRows] = useState<Artifact[] | null>(null);
  const [picked, setPicked] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Whether tiles are hidden below the fold — no CSS can ask, so the box is
  // measured (the table primitive's trick) and the stylesheet fades the edge.
  const [more, setMore] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listArtifacts({ kind: "image" })
      .then(setRows)
      .catch((err) => { setRows([]); setError(errorDetail(err, "Could not list the artifacts.")); });
  }, []);

  function measure() {
    const el = box.current;
    if (el) setMore(el.scrollHeight - el.scrollTop - el.clientHeight > 1);
  }
  // Measured once the rows have drawn: the box has no height before that.
  useEffect(measure, [rows]);

  return (
    <FormDialog open title={title}
                submitLabel={applying ? busyLabel : submitLabel}
                disabled={applying || !picked}
                onSubmit={() => { if (picked) onUse(picked); }}
                onCancel={() => { if (!applying) onCancel(); }}>
      {error && <Banner variant="danger">{error}</Banner>}
      {rows === null
        ? <p>Loading…</p>
        : rows.length === 0
        ? <p>No images yet — upload one, or generate it.</p>
        : (
          <div className="profile-image-picker" ref={box} onScroll={measure}
               data-overflow={more ? "end" : undefined}>
            <ArtifactGrid artifacts={rows} me={null} onOpen={setPicked} />
          </div>
        )}
      <p className="profile-image-picked" aria-live="polite">
        {picked ? <>Selected: <code>{picked.name}</code></> : "Click a picture to select it."}
      </p>
    </FormDialog>
  );
}
