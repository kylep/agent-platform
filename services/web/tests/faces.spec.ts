import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { faceFor } from "../src/lib/face";
import FACES from "../src/lib/faces.json" with { type: "json" };

// An agent's face has to be the same face in the browser, in the roster an
// agent is handed in its prompt, and on a bridged connector — so the emoji
// list and the hash that indexes it are duplicated on purpose, in Python and
// in TypeScript. These two tests are what keeps a duplicate from becoming a
// fork: the first compares the lists, the second pins the arithmetic to
// values produced by the backend itself.

const RELAY_PY = new URL("../../backend/agentplatform/relay.py", import.meta.url);

test("faces.json is the backend's FACES tuple, verbatim", () => {
  const source = readFileSync(RELAY_PY, "utf8");
  // Non-greedy to the FIRST closing paren, with no end-of-line anchor: the
  // tuple is wrapped however black last felt like wrapping it, and an emoji
  // string can never contain a bracket.
  const tuple = /FACES\s*=\s*\(([\s\S]*?)\)/.exec(source);
  expect(tuple, "no FACES tuple in agentplatform/relay.py").not.toBeNull();
  const backend = [...tuple![1].matchAll(/"([^"]*)"/g)].map((m) => m[1]);
  expect(backend.length).toBeGreaterThan(8);
  expect(FACES).toEqual(backend);
});

test("faceFor reproduces the backend's hash", () => {
  // Golden values from `python -c "from agentplatform.relay import face_for"`.
  expect(faceFor("news")).toEqual({ emoji: "🎈", hue: 9 });
  expect(faceFor("health-monitor")).toEqual({ emoji: "🧭", hue: 109 });
});
