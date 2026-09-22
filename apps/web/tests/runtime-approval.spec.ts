import { test, expect, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";

const run = "7461168f-d5b4-40c5-9fd7-4bd0c277dafb";
const approval = "dadc3609-d7b8-45db-ad0d-776276de212a";
async function fixture(page: Page) {
  const state = {
    main: true,
    loseProposal: false,
    incomplete: false,
    loseDecision: false,
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
    if (path.endsWith("/approvals/" + approval)) {
      if (method === "POST") {
        if (state.loseDecision) return route.abort("failed");
        data = { outcome: "resumed", status: "Queued", run_id: run, event_cursor: 30 };
      } else data = { operation: "mcp-leam.leam_resource_save", arguments: '<img src=x onerror="window.injected=true">', argumentsComplete: !state.incomplete, fingerprint: "a".repeat(64) };
    }
    await route.fulfill({ json: data });
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "a");
  await expect(
    page.getByText("PRIVATE_USER_REQUEST", { exact: true }),
  ).toBeVisible();
  return state;
}

async function gate(page: Page) {
  await page.evaluate(({run,approval}) => (window as any).recoveryEmit("a", [
    {run_status:{run_id:run,status:"blocked_approval"}},
    {gate:{run_id:run,gate_kind:"approval",gate_ref:"gate:approval-"+approval}},
    {capability_activity:{turn_run_id:run,status:"started",capability_id:"old-operation"}}
  ]), {run,approval});
}
test("response-local approval has collapsed data and one-time scoped decision", async ({page}) => {
  const state = await fixture(page); await gate(page);
  const card=page.getByLabel("Tool approval");
  await expect(page.getByText("LEAM · Waiting for approval",{exact:true})).toBeVisible();
  await expect(card.getByRole("button",{name:"Approve once",exact:true})).toBeEnabled();
  await expect(card.locator("pre")).not.toBeVisible();
  await card.getByText("Review operation details",{exact:true}).click();
  await expect(card.locator("pre")).toContainText("<img");
  expect(await page.evaluate(()=>(window as any).injected)).toBeUndefined();
  await card.getByRole("button",{name:"Approve once",exact:true}).click();
  await expect(card).toHaveCount(0);
  const decisions=state.writes.filter(w=>w.path.endsWith("/approvals/"+approval));
  expect(decisions).toHaveLength(1); expect(decisions[0].body.decision).toBe("approved");
  expect(decisions[0].body.always).toBeUndefined();
});
test("missing arguments cannot approve; lost decline reuses UUID", async ({page}) => {
  const state=await fixture(page); state.incomplete=true; state.loseDecision=true; await gate(page);
  const card=page.getByLabel("Tool approval");
  await expect(card.getByRole("button",{name:"Approve once",exact:true})).toBeDisabled();
  await card.getByRole("button",{name:"Decline",exact:true}).click();
  await expect(card.getByRole("alert")).toBeVisible(); state.loseDecision=false;
  await card.getByRole("button",{name:"Decline",exact:true}).click();
  await expect(card).toHaveCount(0);
  const decisions=state.writes.filter(w=>w.path.endsWith("/approvals/"+approval));
  expect(decisions).toHaveLength(2); expect(decisions[0].body).toEqual(decisions[1].body);
});
test("typed retry phase wins over historical activity and generic running in the same projection", async ({page}) => {
  await fixture(page);
  await page.evaluate(run=>(window as any).recoveryEmit("a",[
    {work_summary:{run_id:run,phase:"retrying",body:"Retry",model_recovery:{reason:"progress_idle_timeout",retries_used:2,max_retries:2,elapsed_ms:100686,retry_after_ms:2000}}},
    {capability_activity:{turn_run_id:run,status:"started",capability_id:"old-saving-report"}},
    {run_status:{run_id:run,status:"running"}}
  ]),run);
  await expect(page.getByLabel("Live companion response")).toContainText("Retrying model request (2/2)");
  await page.evaluate(run=>(window as any).recoveryEmit("a", [
    {capability_activity:{turn_run_id:run,status:"started",capability_id:"old-saving-report"}},
    {run_status:{run_id:run,status:"running"}}
  ]),run);
  await expect(page.getByLabel("Live companion response")).toContainText("Retrying model request (2/2)");
  await page.evaluate(run=>(window as any).recoveryEmit("a",[
    {text:{run_id:run,body:"Fresh answer",finalized:false}}
  ]),run);
  await expect(page.getByLabel("Live companion response")).toContainText("Fresh answer");
  await expect(page.getByLabel("Live companion response")).not.toContainText("Retrying model request (2/2)");
});
