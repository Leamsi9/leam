import { test, expect } from "@playwright/test";
async function fixture(page: any) {
  const posts: any[] = [];
  let release: (() => void) | undefined;
  let hold = false;
  let reject = false;
  let routines: any[] = [
    {
      id: "r1",
      title: "Evening check",
      message: "Pause for a moment",
      time: "18:00",
      timezone: "Europe/London",
      days: [0, 1, 2],
      enabled: true,
      revision: 1,
      nextDueAt: 1790000000,
    },
  ];
  await page.route("**/api/**", async (route: any) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: any = { items: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path === "/api/routines") body = { items: routines };
    if (path === "/api/routines/status")
      body = { healthy: true, lastCheck: Date.now() / 1000 };
    if (
      (path === "/api/commitments" && method === "POST") ||
      (path.startsWith("/api/routines") && ["POST", "PUT"].includes(method))
    ) {
      const value = route.request().postDataJSON();
      posts.push({ path, method, value });
      if (hold)
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (reject) {
        await route.fulfill({
          status: 409,
          json: { detail: "Please check the draft and retry" },
        });
        return;
      }
      if (path === "/api/routines")
        routines.push({
          ...value,
          id: "r2",
          revision: 1,
          nextDueAt: 1790000000,
        });
      if (path === "/api/routines/r1")
        routines = [{ ...routines[0], ...value, revision: 2 }];
      body = { saved: true };
    }
    await route.fulfill({ json: body });
  });
  return {
    posts,
    hold: () => {
      hold = true;
    },
    release: () => release?.(),
    reject: () => {
      reject = true;
    },
  };
}
test("native commitment dialog contains focus, closes with Escape/top Close and restores draft at 360px", async ({
  page,
}) => {
  await fixture(page);
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/?view=today");
  const opener = page.getByRole("button", {
    name: "Plan a commitment",
    exact: true,
  });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Commitment editor" });
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate((el) => el.tagName)).toBe("DIALOG");
  const top = page.getByRole("button", { name: "Close commitment editor" });
  await expect(top).toBeVisible();
  const box = await top.boundingBox();
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThan(740);
  await top.focus();
  await page.keyboard.press("Shift+Tab");
  expect(
    await dialog.evaluate((el) => el.contains(document.activeElement)),
  ).toBe(true);
  await page
    .getByLabel("Commitment title", { exact: true })
    .fill("Keep this planned commitment");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await opener.click();
  await expect(
    page.getByLabel("Commitment title", { exact: true }),
  ).toHaveValue("Keep this planned commitment");
  await top.click();
  await expect(opener).toBeFocused();
});
test("saving commitment cannot be dismissed or double-submitted; failed save keeps the draft", async ({
  page,
}) => {
  const f = await fixture(page);
  f.hold();
  f.reject();
  await page.goto("/?view=today");
  await page
    .getByRole("button", { name: "Plan a commitment", exact: true })
    .click();
  await page
    .getByLabel("Commitment title", { exact: true })
    .fill("Retain after failure");
  await page.getByRole("button", { name: "Save commitment" }).click();
  await expect.poll(() => f.posts.length).toBe(1);
  await expect(
    page.getByRole("button", { name: "Close commitment editor" }),
  ).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("dialog", { name: "Commitment editor" }),
  ).toBeVisible();
  f.release();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "check the draft",
  );
  await expect(
    page.getByLabel("Commitment title", { exact: true }),
  ).toHaveValue("Retain after failure");
  expect(f.posts).toHaveLength(1);
});
test("routine creation is collapsed, retains draft on collapse, and successful creation returns to list", async ({
  page,
}) => {
  const f = await fixture(page);
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/?view=routines");
  const editor = page.locator(".routine-editor");
  await expect(editor).not.toHaveAttribute("open", "");
  await expect(
    page.getByRole("heading", { name: "Evening check" }),
  ).toBeVisible();
  await editor.locator("summary").click();
  await page.getByLabel("Name", { exact: true }).fill("Daily pause");
  await page
    .getByLabel("Reminder message", { exact: true })
    .fill("Check priorities");
  await editor.locator("summary").click();
  await editor.locator("summary").click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(
    "Daily pause",
  );
  await page.getByRole("button", { name: "Create routine" }).click();
  await expect(editor).not.toHaveAttribute("open", "");
  await expect(
    page.getByRole("heading", { name: "Daily pause" }),
  ).toBeVisible();
  expect(f.posts[0].value).toMatchObject({
    title: "Daily pause",
    message: "Check priorities",
  });
  expect(
    await page
      .locator(".routines-page")
      .evaluate((el) => getComputedStyle(el).paddingLeft),
  ).not.toBe("0px");
});
test("edit opens the routine form and saves existing revision before closing", async ({
  page,
}) => {
  const f = await fixture(page);
  await page.goto("/?view=routines");
  await page.getByRole("button", { name: "Edit routine", exact: true }).click();
  await expect(page.locator(".routine-editor")).toHaveAttribute("open", "");
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(
    "Evening check",
  );
  await page.getByLabel("Name", { exact: true }).fill("Changed evening check");
  await page.getByRole("button", { name: "Save routine" }).click();
  await expect(page.locator(".routine-editor")).not.toHaveAttribute("open", "");
  expect(f.posts[0]).toMatchObject({
    path: "/api/routines/r1",
    method: "PUT",
    value: { title: "Changed evening check", revision: 1 },
  });
});
