import { navigate, settingsSection } from "./navigation";
import { test, expect } from "@playwright/test";

test("provider settings save a secret without returning it to the page", async ({
  page,
}) => {
  const saved: any[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path === "/api/imports") body = { items: [] };
    if (path === "/api/settings/providers") {
      if (route.request().method() === "POST") {
        saved.push(route.request().postDataJSON());
        body = { saved: true };
      } else
        body = {
          providers: [
            {
              id: "openai",
              description: "OpenAI API",
              adapter: "open_ai_completions",
              default_model: "test-model",
              accepts_api_key: true,
              active: false,
            },
          ],
        };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Settings");
  await settingsSection(page, "Model and reasoning");
  await page.getByLabel("Model provider").selectOption("openai");
  await page.getByLabel("API key").fill("secret-test-value");
  await page.getByRole("button", { name: "Save provider" }).click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].apiKey).toBe("secret-test-value");
  await expect(page.getByLabel("API key")).toHaveValue("");
  await expect(page.getByText("Provider saved.")).toBeVisible();
});

test("memory editing is locked while a save is in flight", async ({ page }) => {
  let release!: () => void;
  const wait = new Promise<void>((r) => (release = r));
  let entered!: () => void;
  const started = new Promise<void>((r) => (entered = r));
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/memory" && route.request().method() === "POST") {
      entered();
      await wait;
      await route.fulfill({ json: { id: "new" } });
      return;
    }
    const body =
      p === "/api/auth/status"
        ? { authenticated: true }
        : p === "/api/memory"
          ? {
              items: [
                {
                  id: "old",
                  revision: 1,
                  text: "Existing memory",
                  source: "Me",
                },
              ],
            }
          : p === "/api/settings/providers"
            ? { providers: [] }
            : { data: [], items: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Settings");
  await settingsSection(page, "Memory");
  await page.getByLabel("Memory", { exact: true }).fill("First draft");
  await page.getByRole("button", { name: "Remember this" }).click();
  await started;
  await expect(
    page.getByRole("button", { name: "Edit", exact: true }),
  ).toBeDisabled();
  await expect(page.getByLabel("Memory", { exact: true })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Remember this" }),
  ).toBeDisabled();
  release();
  await expect(page.getByLabel("Memory", { exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByLabel("Memory", { exact: true })).toHaveValue(
    "Existing memory",
  );
});

test("reminder status distinguishes a stale heartbeat from running", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === "/api/auth/status"
        ? { authenticated: true }
        : path === "/api/notifications/status"
          ? { healthy: false, error: null, lastCheck: 1700000000 }
          : path === "/api/settings/providers"
            ? { providers: [] }
            : { data: [], items: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Settings");
  await settingsSection(page, "Reminders");
  await expect(page.getByText(/Scheduler heartbeat is stale/)).toBeVisible();
  await expect(page.getByText(/Scheduler running/)).toHaveCount(0);
});

test("subscription model and effort settings use the account catalog and survive reload", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let active: any = null;
  const saved: any[] = [];
  // Shape follows Codex App Server 0.155.1 model/list, without account data.
  const models = [
    {
      model: "gpt-5.6-sol",
      displayName: "Sol",
      defaultReasoningEffort: "medium",
      supportedReasoningEfforts: [
        { reasoningEffort: "medium" },
        { reasoningEffort: "high" },
      ],
    },
    {
      model: "gpt-5.6-astra",
      displayName: "Astra",
      defaultReasoningEffort: "high",
      supportedReasoningEfforts: [
        { reasoningEffort: "high" },
        { reasoningEffort: "xhigh" },
      ],
    },
  ];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/settings/providers")
      body = {
        active,
        providers: [
          {
            id: "openai_codex",
            adapter: "open_ai_codex",
            default_model: "gpt-5.5",
            description: "ChatGPT subscription",
            active: !!active,
            accepts_api_key: false,
          },
        ],
      };
    if (path === "/api/settings/models") body = { models };
    if (path === "/api/settings/providers/active") {
      const data = route.request().postDataJSON();
      saved.push(data);
      active = {
        provider_id: data.providerId,
        model: data.model,
        reasoning_effort: data.reasoningEffort,
      };
      body = { saved: true };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Settings");
  await settingsSection(page, "Model and reasoning");
  await expect(page.getByLabel("Model provider")).toHaveValue("openai_codex");
  await expect(page.getByLabel("Companion model", { exact: true })).toHaveValue(
    "gpt-5.6-sol",
  );
  await expect(page.getByLabel("Reasoning effort", { exact: true })).toHaveValue("medium");
  await page
    .getByLabel("Companion model", { exact: true })
    .selectOption("gpt-5.6-astra");
  await expect(page.getByLabel("Reasoning effort", { exact: true })).toHaveValue("high");
  await expect(
    page.getByLabel("Reasoning effort", { exact: true }).getByRole("option"),
  ).toHaveCount(2);
  await page.getByLabel("Reasoning effort", { exact: true }).selectOption("xhigh");
  await page
    .getByRole("button", { name: "Use this model", exact: true })
    .click();
  await expect
    .poll(() => saved)
    .toEqual([
      {
        providerId: "openai_codex",
        model: "gpt-5.6-astra",
        reasoningEffort: "xhigh",
      },
    ]);
  await page.reload();
  await navigate(page, "Settings");
  await settingsSection(page, "Model and reasoning");
  await expect(page.getByLabel("Companion model", { exact: true })).toHaveValue(
    "gpt-5.6-astra",
  );
  await expect(page.getByLabel("Reasoning effort", { exact: true })).toHaveValue("xhigh");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
