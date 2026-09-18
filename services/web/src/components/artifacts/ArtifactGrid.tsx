import type { Artifact } from "../../api";
import { ArtifactCard } from "./ArtifactCard";

// The Studio's shelf (docs/design/23): every artifact as a tile, newest first,
// on the same responsive grid the report types and the apps use. Files are
// glyph tiles among the pictures rather than a second list, because a run's
// csv and the chart drawn from it belong side by side.

export function ArtifactGrid({ artifacts, me, onOpen }: {
  artifacts: Artifact[];
  me: string | null;
  onOpen: (a: Artifact) => void;
}) {
  return (
    <div className="artifact-grid">
      {artifacts.map((a) => (
        <ArtifactCard key={a.id} artifact={a} me={me} onOpen={onOpen} />
      ))}
    </div>
  );
}
