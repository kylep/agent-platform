import { expect, test, type Page } from "@playwright/test";
import { mockAccounts, mockApi, type AccountsMock } from "./mock-api";

// Human accounts and groups (docs/design/40) against the stateful
// `mockAccounts` stand-in: the Gate rules, the login/register/profile pages
// and the admin's people screens.

const SHOTS = "test-results/accounts";

async function setup(page: Page, init: Partial<AccountsMock> = {}): Promise<AccountsMock> {
  await mockApi(page);
  return mockAccounts(page, init);
}
const admin = { username: "admin", role: "admin", group_id: null } as const;
const alice = { username: "alice", role: "user", group_id: "01".repeat(16) } as const;

test("anonymous visitors land on /login once, with no reload loop", async ({ page }) => {
  const st = await setup(page);
  let loads = 0;
  page.on("load", () => { loads++; });
  await page.goto("/agents");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("tab", { name: "Sign in" })).toBeVisible();
  await page.waitForTimeout(500);
  expect(loads).toBe(1);
  // /login resolves its own state: the redirect cost one /api/me, not a loop.
  expect(st.meHits).toBe(1);
});

test("the Register tab is hidden while registration is closed", async ({ page }) => {
  await setup(page, { open: false });
  await page.goto("/login");
  await expect(page.getByRole("tab", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Register" })).toHaveCount(0);
});

test("register signs in and lands on /profile", async ({ page }) => {
  const st = await setup(page);
  await page.goto("/login");
  await page.getByRole("tab", { name: "Register" }).click();
  await page.getByLabel("Choose a username").fill("Carol");
  await page.getByLabel("Choose a password").fill("s3cret");
  await page.getByLabel("Confirm password").fill("different");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Passwords do not match.")).toBeVisible();

  await page.getByLabel("Confirm password").fill("s3cret");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/profile$/);
  // The username is lowercased on the way out.
  expect(st.calls.find((c) => c.path === "/api/register")?.body)
    .toEqual({ username: "carol", password: "s3cret", confirm: "s3cret" });
  await expect(page.getByText("carol", { exact: true })).toBeVisible();
  await expect(page.getByText("None", { exact: true })).toBeVisible();
});

test("a taken username reads as an inline error", async ({ page }) => {
  await setup(page);
  await page.goto("/login");
  await page.getByRole("tab", { name: "Register" }).click();
  await page.getByLabel("Choose a username").fill("alice");
  await page.getByLabel("Choose a password").fill("pw");
  await page.getByLabel("Confirm password").fill("pw");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("username taken")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("the admin signs in to the console", async ({ page }) => {
  await setup(page);
  await page.goto("/login");
  await page.getByLabel("Password").fill("pw");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/$/);
});

test("a user deep-linking to /agents lands on /profile", async ({ page }) => {
  await setup(page, { who: alice });
  await page.goto("/agents");
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole("heading", { name: "Profile" })).toBeVisible();
  await expect(page.getByText("family")).toBeVisible();
  // No sidebar on the user's page.
  await expect(page.locator("nav.nav")).toHaveCount(0);
});

test("profile changes the password, and a wrong current one is an inline error", async ({ page }) => {
  const st = await setup(page, { who: alice });
  await page.goto("/profile");
  await page.getByLabel("Current password").fill("wrong");
  await page.getByLabel("New password", { exact: true }).fill("n3w");
  await page.getByLabel("Confirm new password").fill("n3w");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText("current password is wrong")).toBeVisible();

  await page.getByLabel("Current password").fill("old");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText("Password changed.")).toBeVisible();
  expect(st.calls.filter((c) => c.path === "/api/me/password").at(-1)?.body)
    .toEqual({ current: "old", new: "n3w", confirm: "n3w" });
});

test("sign out revokes the session and returns to /login", async ({ page }) => {
  const st = await setup(page, { who: alice });
  await page.goto("/profile");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(st.calls.some((c) => c.path === "/api/logout")).toBe(true);
  // Gone for good: the console bounces back to /login.
  await page.goto("/agents");
  await expect(page).toHaveURL(/\/login$/);
});

test("the admin sidebar carries Profile, Sign out and the Settings children", async ({ page }) => {
  const st = await setup(page, { who: admin });
  await page.goto("/settings");
  const nav = page.locator("nav.nav");
  await expect(nav.getByRole("link", { name: "Users" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Groups" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Profile" })).toBeVisible();
  await nav.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(st.who).toBeNull();
});

test("users: system rows are read-only, state rows carry the actions", async ({ page }) => {
  await setup(page, { who: admin });
  await page.goto("/settings/users");
  const row = (n: string) => page.getByRole("row").filter({ hasText: new RegExp(`^${n}`) });
  await expect(row("admin").getByText("system")).toBeVisible();
  await expect(row("admin").getByRole("button")).toHaveCount(0);
  await expect(row("qa").getByRole("combobox")).toHaveCount(0);
  await expect(row("alice").getByRole("button", { name: "Reset password" })).toBeVisible();
  await expect(row("alice").getByRole("button", { name: "Delete" })).toBeVisible();
});

test("users: the group dropdown writes the group", async ({ page }) => {
  const st = await setup(page, { who: admin });
  await page.goto("/settings/users");
  await page.getByLabel("Group for bob").selectOption({ label: "guests" });
  await expect.poll(() => st.calls.find((c) => c.method === "PUT" && c.path.endsWith("/group"))?.body)
    .toEqual({ group_id: "02".repeat(16) });
  await page.getByLabel("Group for alice").selectOption({ label: "None" });
  await expect.poll(() => st.calls.filter((c) => c.path.endsWith("/group")).at(-1)?.body)
    .toEqual({ group_id: null });
});

test("users: the reset modal wants matching passwords, then posts them", async ({ page }) => {
  const st = await setup(page, { who: admin });
  await page.goto("/settings/users");
  await page.getByRole("row").filter({ hasText: /^alice/ })
    .getByRole("button", { name: "Reset password" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Reset password for alice")).toBeVisible();
  await dialog.getByLabel("New password", { exact: true }).fill("abc");
  await dialog.getByLabel("Confirm new password").fill("abd");
  await dialog.getByRole("button", { name: "Reset password" }).click();
  await expect(dialog.getByText("Passwords do not match.")).toBeVisible();
  expect(st.calls.some((c) => c.path.endsWith("/password"))).toBe(false);

  await dialog.getByLabel("Confirm new password").fill("abc");
  await dialog.getByRole("button", { name: "Reset password" }).click();
  await expect(dialog).toHaveCount(0);
  const call = st.calls.find((c) => c.path === `/api/users/${"12".repeat(16)}/password`);
  expect(call?.body).toEqual({ password: "abc", confirm: "abc" });
});

test("users: delete asks first, then removes the row", async ({ page }) => {
  const st = await setup(page, { who: admin });
  await page.goto("/settings/users");
  await page.getByRole("row").filter({ hasText: /^bob/ }).getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  expect(st.calls.some((c) => c.method === "DELETE")).toBe(false);
  await page.getByRole("row").filter({ hasText: /^bob/ }).getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete user" }).click();
  await expect(page.getByRole("row").filter({ hasText: /^bob/ })).toHaveCount(0);
});

test("users: the registration toggle writes the setting", async ({ page }) => {
  const st = await setup(page, { who: admin });
  await page.goto("/settings/users");
  await page.getByRole("button", { name: "Close registration" }).click();
  await expect(page.getByRole("button", { name: "Open registration" })).toBeVisible();
  expect(st.calls.find((c) => c.path === "/api/settings/registration")?.body).toEqual({ open: false });
});

test("groups: create, rename inline, and delete naming who drops to None", async ({ page }) => {
  const st = await setup(page, { who: admin });
  await page.goto("/settings/groups");
  const row = (n: string) => page.getByRole("row").filter({ hasText: n });
  await expect(row("family")).toContainText("1");

  await page.getByLabel("New group name").fill("friends");
  await page.getByRole("button", { name: "Create group" }).click();
  await expect(row("friends")).toContainText("0");
  await page.getByLabel("New group name").fill("Family");
  await page.getByRole("button", { name: "Create group" }).click();
  await expect(page.getByText("group name taken")).toBeVisible();

  await row("guests").getByRole("button", { name: "Rename" }).click();
  await page.getByLabel("Rename guests").fill("visitors");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(row("visitors")).toBeVisible();
  expect(st.calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "visitors" });

  await row("family").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("dialog")).toContainText("1 user will drop to None.");
  await page.getByRole("button", { name: "Delete group" }).click();
  await expect(row("family")).toHaveCount(0);
  expect(st.users.find((u) => u.username === "alice")?.group_id).toBeNull();
});

test("dark-mode screenshots of /login, /profile and /settings/users", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await page.setViewportSize({ width: 1280, height: 800 });
  const st = await setup(page);
  await page.goto("/login");
  await expect(page.getByRole("tab", { name: "Register" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/login.png` });

  st.who = alice;
  await page.goto("/profile");
  await expect(page.getByRole("heading", { name: "Profile" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/profile.png` });

  st.who = admin;
  await page.goto("/settings/users");
  await expect(page.getByRole("row").filter({ hasText: /^alice/ })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/settings-users.png` });
});
