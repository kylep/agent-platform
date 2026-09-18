import { useState, type CSSProperties } from "react";
import type { RelayFace } from "../../api";
import { safeArtifactUrl } from "../../lib/artifacts";
import { faceFor } from "../../lib/face";
import { agentName } from "../../lib/relay";

// Agents are people here: each one wears an emoji on a disc tinted by its own
// hue. The hue is derived identity, not a palette choice, so it rides in as a
// custom property rather than a design token — the tokens are what the disc
// sits on, the hue is who is sitting there.
//
// An agent with a picture (docs/design/23) wears it inside the same disc, so
// every place a face is drawn — the room, the board, the wiki, presence —
// shows it with no change of its own. The emoji stays underneath: a picture
// that fails to load is the emoji again, never an empty ring.

export function Face({ participant, face, size = 30, thinking = false }: {
  participant: string;
  // The API sends a face for agents (their own icon wins over the derived
  // emoji); everything else — humans, bridged users — is derived client-side
  // from the same hash, so a face is never missing.
  face?: RelayFace | null;
  size?: number;
  thinking?: boolean;
}) {
  const f: RelayFace = face ?? faceFor(agentName(participant) ?? participant);
  // The URL that failed, not a flag: a face re-pointed at a new picture gets
  // to try again, and a face that keeps its broken one stays an emoji.
  const [failed, setFailed] = useState<string | null>(null);
  // Only a picture the artifacts routes serve is worn; anything else on the
  // face is not a request this page makes.
  const url = safeArtifactUrl(f.image_url);
  const image = url && url !== failed ? url : null;
  const style = {
    "--face-hue": f.hue, width: size, height: size, fontSize: Math.round(size * 0.52),
  } as CSSProperties;
  return (
    // aria-hidden: the author's name is always rendered beside the face, and a
    // screen reader announcing "balloon" before every message is noise.
    <span className={`relay-face${thinking ? " thinking" : ""}`} style={style} aria-hidden="true">
      {image
        ? <img src={image} alt="" loading="lazy" onError={() => setFailed(image)} />
        : f.emoji}
    </span>
  );
}
