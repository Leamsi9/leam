import { test, expect } from "@playwright/test";
async function login(page: any) {
  await page.goto("/?view=routines");
  await page.locator("input[name=password]").fill("local-event-browser-only");
  await page.getByRole("button", { name: /Enter Leam/ }).click();
  await expect(
    page.getByRole("heading", { name: "Event-based routines" }),
  ).toBeVisible();
}
async function createRule(page: any, suffix: string) {
  const rules = page.getByRole("region", { name: "Event-based routines" });
  await rules.locator("summary").filter({ hasText: "New event rule" }).click();
  await rules
    .getByLabel("Reminder title", { exact: true })
    .fill("Pause " + suffix);
  await rules
    .getByLabel("Reminder text", { exact: true })
    .fill("Take one useful step");
  await rules
    .getByLabel("Event source", { exact: true })
    .fill("browser-" + suffix);
  await rules.getByLabel("Event type", { exact: true }).fill("check-in");
  await rules
    .getByRole("button", { name: "Create event rule", exact: true })
    .click();
  await expect(
    rules.getByRole("heading", { name: "Pause " + suffix, exact: true }),
  ).toBeVisible();
  return rules;
}
async function sendEvent(page: any, rules: any, suffix: string) {
  await rules
    .locator("summary")
    .filter({ hasText: "Send a test event" })
    .click();
  await rules
    .getByLabel("Input source", { exact: true })
    .fill("browser-" + suffix);
  await rules.getByLabel("Input type", { exact: true }).fill("check-in");
  await rules
    .getByLabel("Attributes JSON", { exact: true })
    .fill('{"place":"home"}');
  await rules.getByRole("button", { name: "Send event", exact: true }).click();
  await expect(rules.getByRole("status")).toContainText("Input saved");
}

test("real authenticated event intake creates persisted notification, pause/edit and source inspection at phone and desktop", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  const suffix = String(Date.now());
  const rules = await createRule(page, suffix);
  await sendEvent(page, rules, suffix);
  await expect(rules.getByRole("status")).toContainText(
    "1 reminder(s) created",
  );
  const note = page
    .getByRole("region", { name: "Event reminders" })
    .locator("article")
    .filter({ hasText: "Pause " + suffix });
  await expect(note).toContainText("Take one useful step");
  await page.reload();
  await expect(note).toBeVisible();
  await note.getByRole("button", { name: "Dismiss event reminder" }).click();
  await expect(note).toHaveCount(0);
  const card = rules.locator("article.card").filter({
    has: page.getByRole("heading", { name: "Pause " + suffix, exact: true }),
  });
  await card.getByRole("button", { name: "Pause event rule" }).click();
  await expect(
    card.getByRole("button", { name: "Resume event rule" }),
  ).toBeVisible();
  await card.getByRole("button", { name: "Edit event rule" }).click();
  await rules
    .getByLabel("Reminder text", { exact: true })
    .fill("Updated reminder");
  await rules.getByRole("button", { name: "Save event rule" }).click();
  await expect(card).toContainText("Updated reminder");
  await rules
    .locator("summary")
    .filter({ hasText: /Recent event inputs/ })
    .click();
  await rules
    .locator(".event-input summary")
    .filter({ hasText: "browser-" + suffix })
    .click();
  await expect(
    rules.locator(".event-input").filter({ hasText: "browser-" + suffix }),
  ).toContainText("owner-session");
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
});
test("uncertain event retry after a real committed response reuses exact input and produces one reminder", async ({
  page,
}) => {
  await login(page);
  const suffix = "retry-" + Date.now();
  const rules = await createRule(page, suffix);
  let first = true;
  const sent: any[] = [];
  await page.route("**/api/routine-events", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    sent.push(route.request().postDataJSON());
    const response = await route.fetch();
    if (first) {
      first = false;
      await route.abort("failed");
    } else await route.fulfill({ response });
  });
  await rules
    .locator("summary")
    .filter({ hasText: "Send a test event" })
    .click();
  await rules
    .getByLabel("Input source", { exact: true })
    .fill("browser-" + suffix);
  await rules.getByRole("button", { name: "Send event", exact: true }).click();
  await expect(
    rules.getByRole("button", { name: "Retry same event" }),
  ).toBeEnabled();
  await expect(
    rules.getByLabel("Input source", { exact: true }),
  ).toBeDisabled();
  await rules.getByRole("button", { name: "Retry same event" }).click();
  await expect(rules.getByRole("status")).toContainText(
    "1 reminder(s) created",
  );
  expect(sent).toHaveLength(2);
  expect(sent[0]).toEqual(sent[1]);
  const note = page
    .getByRole("region", { name: "Event reminders" })
    .locator("article")
    .filter({ hasText: "Pause " + suffix });
  await expect(note).toHaveCount(1);
  await page.reload();
  await expect(note).toHaveCount(1);
});

test("editing reminder text preserves an exact null field predicate", async ({
  page,
}) => {
  await login(page);
  const saved = await page.evaluate(async () => {
    const response = await fetch("/api/routine-event-rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: "Null-preservation review",
        message: "Before review",
        source: "review-null",
        eventType: "check-in",
        match: { field: "place", equals: null },
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  });
  await page.reload();
  const rules = page.getByRole("region", { name: "Event-based routines" });
  const card = rules
    .locator("article.card")
    .filter({ hasText: "Null-preservation review" });
  await card.getByRole("button", { name: "Edit event rule" }).click();
  await rules.getByLabel("Reminder text", { exact: true }).fill("After review");
  const written = page.waitForRequest(
    (request) =>
      request.method() === "PUT" &&
      request.url().endsWith("/routine-event-rules/" + saved.id),
  );
  await rules.getByRole("button", { name: "Save event rule" }).click();
  expect((await written).postDataJSON().match).toEqual({
    field: "place",
    equals: null,
  });
});
