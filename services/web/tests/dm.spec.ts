import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

// A DM is one conversation (QA-16). The agent's answers used to land inside the
// thread of the human's message, and the DM pane — which never opens a thread —
// hid every one of them. The fixture's reply is threaded the way that history
// is, so these prove the pane shows it without a click, wherever the DM is
// drawn; the channel case (`relay.spec.ts`) proves rooms still split threads.

const REPLY = "Quiet. One deploy, one review.";

test("an agent's DM reply is in the transcript on the agent page", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents/news?tab=conversations");
  const room = page.getByRole("region", { name: /^Channel/ });
  await expect(room).toContainText("What's my day look like?");
  await expect(room).toContainText(REPLY);
  // Inline, oldest-first: the answer follows the question.
  const bodies = await room.locator(".relay-message").allInnerTexts();
  expect(bodies.findIndex((t) => t.includes("What's my day"))).toBeLessThan(
    bodies.findIndex((t) => t.includes(REPLY)));
  // …and nothing is left behind a chip: there is no thread to open.
  await expect(room.getByRole("button", { name: /repl/ })).toHaveCount(0);
});

test("an agent's DM reply is in the transcript on Relay's dm side", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?kind=dm&channel=rd1");
  const room = page.getByRole("region", { name: /^Channel/ });
  await expect(room).toContainText(REPLY);
  // Relay has a thread pane beside it, but a DM must not offer one: a threaded
  // reply would then be drawn twice, inline and in the pane. No chip, and no
  // "reply in thread" on hover either.
  await page.locator(".relay-block", { hasText: REPLY }).hover();
  await expect(room.getByRole("button", { name: /repl/i })).toHaveCount(0);
});
