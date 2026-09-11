import type { CSSProperties } from "react";
import type { RelayFace } from "../../api";
import { faceFor } from "../../lib/face";
import { agentName } from "../../lib/relay";

// Agents are people here: each one wears an emoji on a disc tinted by its own
// hue. The hue is derived identity, not a palette choice, so it rides in as a
// custom property rather than a design token — the tokens are what the disc
// sits on, the hue is who is sitting there.

export function Face({ participant, face, size = 30, thinking = false }: {
  participant: string;
  // The API sends a face for agents (their own icon wins over the derived
  // emoji); everything else — humans, bridged users — is derived client-side
  // from the same hash, so a face is never missing.
  face?: RelayFace | null;
  size?: number;
  thinking?: boolean;
}) {
  const f = face ?? faceFor(agentName(participant) ?? participant);
  const style = {
    "--face-hue": f.hue, width: size, height: size, fontSize: Math.round(size * 0.52),
  } as CSSProperties;
  return (
    // aria-hidden: the author's name is always rendered beside the face, and a
    // screen reader announcing "balloon" before every message is noise.
    <span className={`relay-face${thinking ? " thinking" : ""}`} style={style} aria-hidden="true">
      {f.emoji}
    </span>
  );
}
