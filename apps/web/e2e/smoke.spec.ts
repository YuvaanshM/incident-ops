import { expect, test } from "@playwright/test";

test("renders the Phase 1 shell", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Incident Ops" }),
  ).toBeVisible();
});
