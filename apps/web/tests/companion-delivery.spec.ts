import { test, expect, Page } from "@playwright/test";
import { chooseConversation } from "./navigation";
const ack = {
  outcome: "deferred_busy",
  thread_id: "a",
  accepted_message_ref: "msg:fixture",
  active_run_id: "11111111-1111-4111-8111-111111111111",
  status: "Running",
};
async function setup(page: Page, receipt: any = ack, saved = false) {
  const writes: any[] = [];
  await page.addInitScript(
    ({ saved }) => {
      class QuietEvents extends EventTarget {
        close() {}
        constructor(_url: string) {
          super();
        }
      }
      (window as any).EventSource = QuietEvents;
      if (saved)
        sessionStorage.setItem(
          "leam-companion-submission:a",
          JSON.stringify({
            thread: "a",
            id: "saved-request",
            text: "Already received follow-up",
          }),
        );
    },
    { saved },
  );
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {
      items: [],
      data: [],
      threads: [],
      models: [],
      providers: [],
    };
    if (path === "/api/attachments")
      body = { id: "11111111-1111-4111-8111-111111111122", filename: "evidence.txt", mimeType: "text/plain", sizeBytes: 8, sha256: "fixture", state: "uploaded" };
    if (path === "/api/auth/status")
      body = { configured: true, authenticated: true };
    if (path === "/api/companion/threads")
      body = {
        threads: [
          {
            thread_id: "a",
            title: "Fixture",
            created_at: "2026-09-21T01:00:00Z",
          },
        ],
      };
    if (path === "/api/companion/threads/a") body = { messages: [] };
    if (path.endsWith("/messages") && route.request().method() === "POST") {
      writes.push(route.request().postDataJSON());
      body = receipt;
    }
    if (path.includes("/submissions/")) body = { state: "recorded", receipt };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "a");
  return writes;
}
for (const width of [390, 1440])
  test(`accepted queued follow-up clears draft without resend at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const writes = await setup(page);
    await page
      .getByRole("textbox", { name: "Message Leam" })
      .fill("Follow up while busy");
    await page
      .getByRole("button", { name: "Send to Leam", exact: true })
      .click();
    await expect(
      page.getByText(/Follow-up received for the current response/),
    ).toBeVisible();
    await expect(
      page.getByRole("textbox", { name: "Message Leam" }),
    ).toHaveValue("");
    await expect(page.getByText(/Message delivery is uncertain/)).toHaveCount(
      0,
    );
    await expect(
      page.getByText(/saved message is awaiting confirmation/),
    ).toHaveCount(0);
    expect(writes).toHaveLength(1);
    expect(
      await page.evaluate(() =>
        sessionStorage.getItem("leam-companion-submission:a"),
      ),
    ).toBeNull();
  });
test("existing uncertain draft recovers from saved deferred receipt using only GET", async ({
  page,
}) => {
  const writes = await setup(page, ack, true);
  await expect(
    page.getByText(/Follow-up received for the current response/),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message Leam" })).toHaveValue(
    "",
  );
  expect(writes).toHaveLength(0);
});
test("genuinely rejected busy message remains an unsent draft without an automatic retry", async ({
  page,
}) => {
  const writes = await setup(page, {
    outcome: "rejected_busy",
    thread_id: "a",
    active_run_id: ack.active_run_id,
  });
  await page
    .getByRole("textbox", { name: "Message Leam" })
    .fill("Keep my draft");
  await page.getByRole("button", { name: "Send to Leam", exact: true }).click();
  await expect(page.getByText(/did not accept this message/)).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message Leam" })).toHaveValue(
    "Keep my draft",
  );
  expect(writes).toHaveLength(1);
  await expect(page.getByText(/Follow-up received/)).toHaveCount(0);
});

for (const width of [390, 1440])
  test(`attachment-only Companion submission uses uploaded ID and clears on receipt at ${width}`, async ({page}) => {
    await page.setViewportSize({width,height:900});
    const writes=await setup(page);
    await page.getByLabel("Attach files", {exact:true}).setInputFiles({name:"evidence.txt",mimeType:"text/plain",buffer:Buffer.from("evidence")});
    await expect(page.getByRole("list",{name:"Attached files"})).toContainText("evidence.txt");
    await page.getByRole("button",{name:"Send to Leam",exact:true}).click();
    await expect(page.getByRole("list",{name:"Attached files"})).toHaveCount(0);
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({text:"",attachmentIds:["11111111-1111-4111-8111-111111111122"]});
    await expect(page.getByText(/Follow-up received for the current response/)).toBeVisible();
  });
test("uncertain attachment dispatch keeps exact IDs for explicit retry", async ({page}) => {
  const writes=await setup(page,{outcome:"unknown"});
  await page.getByLabel("Attach files",{exact:true}).setInputFiles({name:"evidence.txt",mimeType:"text/plain",buffer:Buffer.from("evidence")});
  await expect(page.getByRole("list",{name:"Attached files"})).toContainText("evidence.txt");
  await page.getByRole("button",{name:"Send to Leam",exact:true}).click();
  await expect(page.getByRole("complementary", {name:"Message delivery recovery"})).toContainText("Receipt unavailable");
  await expect(page.getByRole("list",{name:"Attached files"})).toContainText("evidence.txt");
  await expect(page.getByRole("button",{name:"Remove evidence.txt"})).toBeDisabled();
  await page.getByRole("button",{name:"Retry saved message",exact:true}).click();
  await expect.poll(()=>writes.length).toBe(2);
  expect(writes[1]).toEqual(writes[0]);
});
