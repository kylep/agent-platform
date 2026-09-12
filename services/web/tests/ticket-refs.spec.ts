import { expect, test } from "@playwright/test";
import { canMove, stateLabel, ticketRefs } from "../src/lib/tickets";

// The two pure rules the rest of the UI is built on, tested where they live
// rather than through a page: both of them are mirrors of backend rules
// (`tickets.find_ticket_refs`, `tickets.can_move`), and a mirror that drifts is
// worse than no mirror.

test("a ticket key in prose is a reference; one in a code fence is not", () => {
  const body = "Fixes OPS-12 and `OPS-13`.\n```\nlog: OPS-99 failed\n```\nSee GEN-4.";
  const refs = ticketRefs(body, ["OPS", "GEN"]);
  expect(refs.map((r) => r.key)).toEqual(["OPS-12", "OPS-13", "GEN-4"]);
  // the offsets still point into the ORIGINAL text, fences and all
  for (const r of refs) expect(body.slice(r.start, r.end)).toBe(r.key);
});

test("an unknown prefix is text, and lowercase is a word", () => {
  expect(ticketRefs("XYZ-1 and ops-12 and OPS-12", ["OPS"]).map((r) => r.key))
    .toEqual(["OPS-12"]);
  // an unterminated fence swallows the rest, exactly as the backend's does
  expect(ticketRefs("OPS-1\n```\nOPS-2", ["OPS"]).map((r) => r.key)).toEqual(["OPS-1"]);
});

test("closed work only reopens, and a state never moves to itself", () => {
  expect(canMove("open", "in_progress")).toBe(true);
  expect(canMove("open", "open")).toBe(false);
  expect(canMove("done", "open")).toBe(true);
  expect(canMove("done", "in_progress")).toBe(false);
  expect(canMove("cancelled", "done")).toBe(false);
  expect(canMove("open", "nonsense")).toBe(false);
  expect(stateLabel("in_progress")).toBe("in progress");
});
