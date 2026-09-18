import { Link } from "react-router-dom";
import { Button } from "@ap/ui/button";
import type { Artifact } from "../../api";
import { fileGlyph, safeArtifactUrl } from "../../lib/artifacts";

// The strip across the bottom (docs/design/23): the last pictures the store
// took in, live off the shared feed, so something an agent just made lands
// here while the reader is still typing. A thumb is a button that fills the
// stage; the shelf itself is a page away.

export function RecentStrip({ artifacts, current, loaded, onOpen }: {
  artifacts: Artifact[];
  /** The id on the stage, so its thumb is marked. */
  current: string | null;
  loaded: boolean;
  onOpen: (a: Artifact) => void;
}) {
  return (
    <section className="recent-strip" aria-label="Recent">
      <div className="recent-strip-head">
        <h2>Recent</h2>
        <Link to="/artifacts">Browse all</Link>
      </div>
      {!loaded
        ? <p className="muted">Loading…</p>
        : artifacts.length === 0
          ? <p className="muted">Nothing yet — the first picture made here lands on this strip.</p>
          : (
            <ul className="recent-thumbs">
              {artifacts.map((a) => {
                const thumb = safeArtifactUrl(a.thumb_url);
                return (
                  <li key={a.id}>
                    <Button variant="secondary" size="bare" className="recent-thumb" title={a.name}
                            aria-label={`Open ${a.name}`}
                            aria-current={a.id === current ? "true" : undefined}
                            onClick={() => onOpen(a)}>
                      {thumb
                        ? <img src={thumb} alt="" loading="lazy" />
                        : <span className="artifact-glyph" aria-hidden="true">{fileGlyph(a.mime)}</span>}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
    </section>
  );
}
