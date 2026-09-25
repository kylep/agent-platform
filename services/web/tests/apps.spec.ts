import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test("published App page is primary while the detailed interface stays linked", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/apps");
  const news = page.locator(".app-card").filter({ has: page.getByText("news", { exact: true }) });
  await expect(news.locator(".app-open")).toHaveAttribute("href", `/live-views/${"n1".repeat(16)}`);
  await expect(news.getByRole("link", { name: "Detailed app" })).toHaveAttribute("href", "/apps/news/");
  const tcms = page.locator(".app-card").filter({ has: page.getByText("tcms", { exact: true }) });
  await expect(tcms.locator(".app-open")).toHaveAttribute("href", "/apps/tcms/");
  expect(unmatched).toEqual([]);
});
