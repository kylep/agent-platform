import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test("a local session room has no composer or room mutation controls", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/relay/channels/rc1", async route => {
    await route.fulfill({ json: { id:"rc1", kind:"group", name:null, title:"Laptop recording",
      home:"local_persona", dispatch_mode:"disabled", reply_mode:"linear", open:false, topic:"",
      participants:["user:kyle"], faces:{}, archived_at:null, unread_count:0, message_count:1,
      created_at:"2026-10-15T00:00:00Z", updated_at:"2026-10-15T00:00:00Z" } });
  });
  await page.goto("/relay?channel=rc1");
  await expect(page.getByText("local session recording · continue in your terminal")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Archive room", exact: true })).toHaveCount(0);
});

test("local session revocation is described as AP access, and activity stays readable", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/local-persona/sessions", route => route.fulfill({ json: [{
    id:"test-session", agent:"pai", harness:"codex", state:"active", device_label:"Laptop",
    conversation_id:"rc1", run_id:"r1", accepted_sequence:7,
    last_seen_at:"2026-10-15T12:00:00Z", expires_at:"2026-10-16T00:00:00Z",
  }] }));
  await page.route("**/api/local-persona/sessions/test-session/history?after=0", route => route.fulfill({ json: [{
    sequence:1, local_turn_id:"turn1", kind:"tool_result", payload:{ output:"synthetic tool result" },
  }] }));
  await page.goto("/settings/local-personas");
  await expect(page.getByText("Revoking AP access leaves the laptop process running.", { exact:false })).toBeVisible();
  await expect(page.getByRole("button", { name:"Revoke AP access" })).toBeVisible();
  await page.getByRole("button", { name:"View recorded activity" }).click();
  await page.getByText("1 · tool_result · turn turn1").click();
  await expect(page.getByText('"output": "synthetic tool result"', {exact:false})).toBeVisible();
});
