import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

// The login form names a principal (docs/design/25): `admin` is the default so
// the one-user install types nothing new, and a QA or any other named
// principal signs in through the same form. What matters is the WRITE — the
// body `POST /api/login` gets — and that the password keeps the focus, since
// the username is already filled in.

test("login sends {principal, password} with admin prefilled", async ({ page }) => {
  await mockApi(page);
  const posted = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/api/login"));
  await page.goto("/login");

  const username = page.getByLabel("Username");
  await expect(username).toHaveValue("admin");
  await expect(page.getByLabel("Password")).toBeFocused();

  await page.keyboard.type("hunter2");
  await page.getByRole("button", { name: "Log in" }).click();
  expect((await posted).postDataJSON()).toEqual({ principal: "admin", password: "hunter2" });
  await expect(page).toHaveURL(/\/$/);
});

test("a named principal replaces admin and a 401 reads as one message", async ({ page }) => {
  await mockApi(page);
  await page.goto("/login");

  await page.getByLabel("Username").fill("qa");
  await page.getByLabel("Password").fill("wrong");
  const posted = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/api/login"));
  await page.getByRole("button", { name: "Log in" }).click();
  expect((await posted).postDataJSON()).toEqual({ principal: "qa", password: "wrong" });
  // One 401 for a bad name and a bad password alike — the form must not tell
  // a guesser which principals exist.
  await expect(page.getByText("Invalid username or password.")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});
