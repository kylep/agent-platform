// The import attribute keeps this module loadable as real ESM (node, the
// playwright suite) as well as through the bundler.
import FACES from "./faces.json" with { type: "json" };
import { sha256Hex } from "./sha256";

// An agent's face is its identity in Relay, and it has to be the SAME face
// wherever it appears — this pane, the roster an agent is handed in its
// prompt, a bridged Discord message. The backend derives it in
// agentplatform.relay.face_for; this is that function, digit for digit, so a
// client can draw a face for a participant the API didn't send one for (a
// human, or an agent named only in a rail row). tests/faces.spec.ts reads the
// backend's FACES tuple off disk and fails if the two lists ever drift.

export type Face = { emoji: string; hue: number };

export function faceFor(name: string): Face {
  const h = sha256Hex(name);
  return {
    emoji: FACES[parseInt(h.slice(8, 16), 16) % FACES.length],
    hue: parseInt(h.slice(0, 8), 16) % 360,
  };
}
