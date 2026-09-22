import { test, expect, type Page } from "@playwright/test";
import { navigate } from "./navigation";

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNYUbMVAAOpAdoopthMAAAAAElFTkSuQmCC",
  "base64",
);
const rows = [
  {
    id: "weekly-report",
    title: "Weekly report",
    kind: "markdown",
    filename: "week.md",
    content:
      "# A useful report\n\n![Blocked remote](https://external.invalid/private-image)\n\n<script>window.escape = true</script>",
    bytes: 250,
  },
  {
    id: "garden-page",
    title: "Garden webpage",
    kind: "html",
    filename: "garden.html",
    bytes: 420,
  },
  {
    id: "flower-image",
    title: "Flower image",
    kind: "png",
    filename: "flower.png",
    bytes: png.length,
    previewUrl: "https://external.invalid/untrusted-thumbnail",
  },
  {
    id: "budget-sheet",
    title: "Budget spreadsheet",
    kind: "xlsx",
    filename: "budget.xlsx",
    bytes: 9000,
  },
  {
    id: "plain-notes",
    title: "Plain notes",
    kind: "text",
    filename: "notes.txt",
    content: "<script>Not executable</script>\nA plain note",
    bytes: 50,
  },
].map((item) => ({
  ...item,
  publishedAt: 1790000000,
  sha256: "a".repeat(64),
  source: { surface: "coding", threadId: "fixture-thread" },
}));
async function fixture(page: Page) {
  const state = {
    calls: [] as string[],
    writes: [] as string[],
    fail: false,
    holdMore: null as Promise<void> | null,
    remote: [] as string[],
  };
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("https://external.invalid/**", (route) => {
    state.remote.push(route.request().url());
    return route.abort();
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    state.calls.push(path + url.search);
    if (route.request().method() !== "GET") state.writes.push(path);
    if (path === "/api/auth/status")
      return route.fulfill({ json: { authenticated: true } });
    if (path === "/api/artifacts") {
      if (state.fail)
        return route.fulfill({
          status: 503,
          json: { detail: "Library temporarily unavailable" },
        });
      const query = url.searchParams.get("q") || "";
      const kind = url.searchParams.get("kind") || "";
      let items = rows.filter(
        (item) =>
          (!query ||
            (item.title + item.filename)
              .toLowerCase()
              .includes(query.toLowerCase())) &&
          (!kind ||
            (kind === "image"
              ? ["png", "jpeg", "webp"].includes(item.kind)
              : kind === "document"
                ? !["html", "png"].includes(item.kind)
                : item.kind === kind)),
      );
      const total = items.length;
      const cursor = url.searchParams.get("cursor");
      if (cursor && state.holdMore) await state.holdMore;
      items = cursor ? items.slice(1) : items.slice(0, 2);
      return route.fulfill({
        json: {
          items,
          nextCursor: !cursor && total > 2 ? "opaque-page-two" : null,
          total,
          storage: {
            bytes: 10000,
            maxBytes: 134217728,
            maxFileBytes: 10485760,
          },
        },
      });
    }
    const match = path.match(
      /^\/api\/artifacts\/([^/]+)(?:\/(preview|download))?$/,
    );
    if (match) {
      const item = rows.find((item) => item.id === match[1]);
      if (!item)
        return route.fulfill({
          status: 404,
          json: { detail: "Resource unavailable" },
        });
      if (match[2] === "preview" && item.kind === "png")
        return route.fulfill({ contentType: "image/png", body: png });
      if (match[2] === "preview" && item.kind === "html")
        return route.fulfill({
          contentType: "text/html",
          headers: {
            "Content-Security-Policy":
              "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts",
          },
          body: `<h1>Isolated garden</h1><button onclick="this.textContent='Interacted'">Try interaction</button><script>try { parent.document.body.dataset.escaped='yes'; } catch (_) { document.body.dataset.isolated='yes'; } fetch('https://external.invalid/probe').catch(()=>{});</script><img src="https://external.invalid/pixel">`,
        });
      if (match[2] === "download")
        return route.fulfill({
          contentType: "application/octet-stream",
          headers: {
            "Content-Disposition": `attachment; filename="${item.filename}"`,
          },
          body: item.content || "fixture binary download",
        });
      return route.fulfill({ json: item });
    }
    return route.fulfill({
      json: { items: [], data: [], threads: [], providers: [], models: [] },
    });
  });
  return state;
}

test("mobile More opens searchable filtered Resources with safe compact thumbnails", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await fixture(page);
  await page.goto("/?view=settings");
  await navigate(page, "Resources");
  await expect(
    page.getByRole("heading", { name: "Resources", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Open Weekly report" }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "File type", exact: true })
    .selectOption("image");
  await expect(
    page.getByRole("link", { name: "Open Flower image" }),
  ).toBeVisible();
  await expect(page.locator(".resource-thumbnail img")).toHaveAttribute(
    "src",
    "/api/artifacts/flower-image/preview",
  );
  await page
    .getByRole("searchbox", { name: "Search resources" })
    .fill("nothing matches");
  const reads = state.calls.filter((path) =>
    path.startsWith("/api/artifacts?"),
  ).length;
  await expect(
    page.getByRole("link", { name: "Open Flower image" }),
  ).toBeVisible();
  expect(
    state.calls.filter((path) => path.startsWith("/api/artifacts?")).length,
  ).toBe(reads);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "No matching resources" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(
    page.getByRole("link", { name: "Open Weekly report" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({ path: info.outputPath("resources-390.png") });
  expect(state.remote).toEqual([]);
  expect(state.writes).toEqual([]);
});

test("load more deduplicates IDs and keeps opaque cursor, while filter change fences old results", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/?view=resources");
  await page.getByRole("button", { name: "Load more resources" }).click();
  await expect(page.locator(".resource-card")).toHaveCount(5);
  expect(
    state.calls.some((path) => path.includes("cursor=opaque-page-two")),
  ).toBe(true);
  await page.getByRole("button", { name: "Refresh resources" }).click();
  await expect(page.locator(".resource-card")).toHaveCount(2);
  let release!: () => void;
  state.holdMore = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.getByRole("button", { name: "Load more resources" }).click();
  await expect(
    page.getByRole("button", { name: "Loading more…" }),
  ).toBeDisabled();
  await page
    .getByRole("combobox", { name: "File type", exact: true })
    .selectOption("image");
  await expect(
    page.getByRole("link", { name: "Open Flower image" }),
  ).toBeVisible();
  release();
  await expect(page.locator(".resource-card")).toHaveCount(1);
  await expect(
    page.getByRole("link", { name: "Open Weekly report" }),
  ).toHaveCount(0);
});

test("library load failure is visible and explicitly retryable", async ({
  page,
}) => {
  const state = await fixture(page);
  state.fail = true;
  await page.goto("/?view=resources");
  await expect(page.getByRole("alert")).toContainText(
    "Library temporarily unavailable",
  );
  state.fail = false;
  await page.getByRole("button", { name: "Retry resources" }).click();
  await expect(
    page.getByRole("link", { name: "Open Weekly report" }),
  ).toBeVisible();
});

test("HTML resources remain interactive inside their sandbox without remote resources or parent access", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/?artifact=garden-page");
  const iframe = page.locator("iframe");
  await expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
  await expect(
    page.getByText("Interactive design study", { exact: false }),
  ).toHaveCount(0);
  await expect(
    page
      .frameLocator("iframe")
      .getByRole("heading", { name: "Isolated garden" }),
  ).toBeVisible();
  await page
    .frameLocator("iframe")
    .getByRole("button", { name: "Try interaction" })
    .click();
  await expect(
    page.frameLocator("iframe").getByRole("button", { name: "Interacted" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.body.dataset.escaped),
  ).toBeUndefined();
  expect(state.remote).toEqual([]);
});

for (const kind of ["png", "text", "xlsx"] as const)
  test(`${kind} resource opens with appropriate viewer and original download`, async ({
    page,
  }) => {
    await fixture(page);
    const item = rows.find((row) => row.kind === kind)!;
    await page.goto(`/?artifact=${item.id}`);
    await expect(
      page.getByRole("heading", { name: item.title, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Download", exact: true }),
    ).toHaveAttribute("href", `/api/artifacts/${item.id}/download`);
    if (kind === "png")
      await expect(
        page.getByRole("img", { name: item.title, exact: true }),
      ).toHaveAttribute("src", `/api/artifacts/${item.id}/preview`);
    if (kind === "text")
      await expect(page.locator(".artifact-text")).toContainText(
        "<script>Not executable</script>",
      );
    if (kind === "xlsx") {
      await expect(
        page.getByRole("heading", { name: "Open on your device" }),
      ).toBeVisible();
      await expect(page.locator("iframe")).toHaveCount(0);
    }
    await page.getByRole("link", { name: "Back to Resources" }).click();
    await expect(
      page.getByRole("heading", { name: "Resources", exact: true }),
    ).toBeVisible();
  });
