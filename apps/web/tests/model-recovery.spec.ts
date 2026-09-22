import { test, expect, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";

const run = "run-current";
const evidence = {
  reason: "progress_idle_timeout",
  retries_used: 1,
  max_retries: 2,
  elapsed_ms: 75000,
  retry_after_ms: 2000,
};
async function fixture(page: Page) {
  const state = {
    main: true,
    loseProposal: false,
    writes: [] as any[],
    proposals: [] as any[],
  };
  await page.addInitScript(() => {
    const streams: any[] = [];
    (window as any).EventSource = class extends EventTarget {
      constructor(public url: string) {
        super();
        if (url.includes("/companion/threads/")) streams.push(this);
      }
      close() {}
    };
    (window as any).recoveryEmit = (thread: string, items: any[]) => {
      const source = streams.findLast((item) =>
        item.url.includes(`/threads/${thread}/`),
      );
      source?.dispatchEvent(
        new MessageEvent("projection_update", {
          data: JSON.stringify({
            type: "projection_update",
            state: { thread_id: thread, items },
          }),
        }),
      );
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const body = method === "GET" ? undefined : route.request().postDataJSON();
    if (method !== "GET") state.writes.push({ path, body });
    let data: any = { items: [], data: [], threads: [], messages: [] };
    if (path === "/api/auth/status") data = { authenticated: true };
    if (path === "/api/companion/threads")
      data = {
        threads: [
          { thread_id: "a", title: "A" },
          { thread_id: "b", title: "B" },
        ],
      };
    if (path === "/api/companion/threads/a")
      data = {
        messages: [
          {
            message_id: "input",
            kind: "user",
            status: "submitted",
            turn_run_id: run,
            sequence: 1,
            content: "PRIVATE_USER_REQUEST",
          },
        ],
      };
    if (path === "/api/coding/main")
      data = {
        main: state.main
          ? { threadId: "main", revision: 3, name: "Main" }
          : null,
        bindingValid: state.main,
      };
    if (path === "/api/proposals") {
      if (method === "POST") {
        data = {
          id: body.requestId,
          ...body,
          state: "pending",
          thread_id: body.threadId,
          review: {},
          fingerprint: "fp",
        };
        state.proposals = [data];
        if (state.loseProposal) return route.abort("failed");
      } else data = { items: state.proposals, nextOffset: null };
    }
    if (/\/coding\/handoffs\/[^/]+$/.test(path))
      data = { state: "not_reviewed" };
    if (path.endsWith("/review"))
      data = {
        main: { threadId: "main", revision: 3, name: "Main" },
        previewToken: "review-exact",
        protocolIdentity: "validated",
      };
    if (path.endsWith("/start"))
      data = { state: "accepted", threadId: "main", taskStatus: "inProgress" };
    await route.fulfill({ json: data });
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "a");
  await expect(
    page.getByText("PRIVATE_USER_REQUEST", { exact: true }),
  ).toBeVisible();
  return state;
}
async function emit(
  page: Page,
  metadata: any = evidence,
  status = "running",
  runId = run,
  thread = "a",
) {
  await page.evaluate(
    ({ metadata, status, runId, thread }) =>
      (window as any).recoveryEmit(thread, [
        {
          work_summary: {
            run_id: runId,
            phase: "retrying",
            body: "Model recovery",
            model_recovery: metadata,
          },
        },
        { run_status: { run_id: runId, status } },
      ]),
    { metadata, status, runId, thread },
  );
}

test("structured retry is honest waiting feedback; max count does not invent exhaustion or resend", async ({
  page,
}) => {
  const state = await fixture(page);
  await emit(page);
  const recovery = page.getByLabel("Response recovery");
  await expect(recovery).toContainText("No model progress was received before the timeout");
  await expect(recovery).toContainText("Retry 1/2; recorded delay 2s");
  await emit(page, { ...evidence, retries_used: 2, elapsed_ms: 150000 });
  await expect(recovery).toContainText("Retry 2/2");
  await expect(recovery).not.toContainText(/exhausted|complete|credentials/i);
  await expect(
    page.getByRole("button", { name: "Prepare response retry" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Prepare coding diagnosis" }),
  ).toHaveCount(0);
  await page
    .getByRole("textbox", { name: "Message Leam" })
    .fill("My unsent draft");
  expect(state.writes).toEqual([]);
});

test("terminal failed response prepares a draft only and never overwrites an existing draft", async ({
  page,
}) => {
  const state = await fixture(page);
  await emit(page, evidence, "failed");
  const draft = page.getByRole("textbox", { name: "Message Leam" });
  await draft.fill("Keep my own draft");
  await page.getByRole("button", { name: "Prepare response retry" }).click();
  await expect(draft).toHaveValue("Keep my own draft");
  await draft.fill("");
  await page.getByRole("button", { name: "Prepare response retry" }).click();
  await expect(draft).toHaveValue("PRIVATE_USER_REQUEST");
  await expect(page.getByLabel("Response recovery")).toContainText(
    "nothing was resent",
  );
  expect(state.writes).toEqual([]);
});

test("unknown metadata is not interpreted and stale retry events cannot replace newer or terminal state", async ({
  page,
}) => {
  await fixture(page);
  await emit(page, evidence, "failed", "older-run");
  await expect(page.getByLabel("Response recovery")).toHaveCount(0);
  await emit(page, { ...evidence, reason: "credential_text_injection" });
  await expect(page.getByLabel("Response recovery")).toHaveCount(0);
  await emit(page, {
    ...evidence,
    reason: "attempt_time_limit",
    retries_used: 2,
    elapsed_ms: 600000,
  });
  await expect(page.getByLabel("Response recovery")).toContainText(
    "attempt reached its time limit",
  );
  await emit(page);
  await expect(page.getByLabel("Response recovery")).toContainText("Retry 2/2");
  await emit(
    page,
    { ...evidence, retries_used: 2, elapsed_ms: 600000 },
    "failed",
  );
  await emit(page);
  await expect(page.getByLabel("Response recovery")).toContainText(
    "Response failed",
  );
  await chooseConversation(page, "b");
  await emit(page);
  await expect(page.getByLabel("Response recovery")).toHaveCount(0);
});

test("diagnosis freezes sanitized evidence and uses original proposal identity after a lost receipt", async ({
  page,
}) => {
  const state = await fixture(page);
  await emit(page, evidence, "failed");
  state.loseProposal = true;
  const prepare = page.getByRole("button", {
    name: "Prepare coding diagnosis",
  });
  await prepare.click();
  await expect(page.getByLabel("Response recovery")).toContainText(
    "could not be confirmed",
  );
  state.loseProposal = false;
  await prepare.click();
  await expect(
    page.getByRole("button", { name: "Review diagnosis in Approvals" }),
  ).toBeVisible();
  const writes = state.writes.filter((row) => row.path === "/api/proposals");
  expect(writes).toHaveLength(2);
  expect(writes[0]).toEqual(writes[1]);
  expect(JSON.stringify(writes)).not.toContain("PRIVATE_USER_REQUEST");
  expect(writes[0].body.operation).toBe("coding.handoff");
  expect(JSON.parse(writes[0].body.input.context)).toMatchObject({
    threadId: "a",
    runId: run,
    model_recovery: evidence,
  });
  expect(state.writes.filter((row) => row.path.endsWith("/start"))).toEqual([]);
  await page
    .getByRole("button", { name: "Review diagnosis in Approvals" })
    .click();
  await page
    .locator("summary")
    .filter({ hasText: "Review coding task" })
    .click();
  await page.getByRole("button", { name: "Review coding task" }).click();
  await expect(
    page.getByRole("region", { name: "Reviewed coding task" }),
  ).toContainText("Main: Main · main");
  expect(state.writes.filter((row) => row.path.endsWith("/start"))).toEqual([]);
  await page.getByRole("button", { name: "Send to main", exact: true }).click();
  await expect
    .poll(() => state.writes.find((row) => row.path.endsWith("/start"))?.body)
    .toEqual({ previewToken: "review-exact", confirmed: true });
});

test("missing Main creates no diagnosis proposal and older terminal run cannot offer a retry of the current request", async ({
  page,
}) => {
  const state = await fixture(page);
  state.main = false;
  await emit(page, evidence, "failed");
  await page.getByRole("button", { name: "Prepare coding diagnosis" }).click();
  await expect(page.getByLabel("Response recovery")).toContainText(
    "Choose and connect your Main session",
  );
  expect(state.writes).toEqual([]);
  await emit(page, evidence, "running", "another-run");
  await expect(
    page.getByRole("button", { name: "Prepare response retry" }),
  ).toHaveCount(0);
});
