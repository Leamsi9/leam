import { test, expect, type Page } from "@playwright/test";
async function fixture(page:Page, pending=false, uncertain=false) {
 const key="email:"+"a".repeat(64), state={posts:[] as any[],receipt:null as any};
 await page.addInitScript(()=>{(window as any).EventSource=class extends EventTarget{close(){}};});
 await page.route("**/api/**",async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;
  let body:any={items:[],accounts:[],messages:[],threads:[],providers:[]};
  if(p==="/api/auth/status")body={authenticated:true};
  if(p==="/api/inbox"||p==="/api/inbox/status")body={items:[],unreadCount:0,total:0,throughSequence:0};
  if(p==="/api/inbox-mail/read")body={readKeys:[],removedKeys:[]};
  if(p==="/api/capacities")body={items:[{id:"work-id",name:"Work",revision:1}]};
  if(p==="/api/agenda")body={date:url.searchParams.get("date"),commitments:[],events:[],emails:[{key,id:"message",accountId:"account",subject:"Please review",from:"Colleague",receivedAt:"2026-09-22T10:00:00Z",actionability:{state:"action",action:"Review"},triage:{disposition:"none",revision:0}}],total:{emails:1,events:0,commitments:0},sources:{calendar:{accounts:[],state:"not_connected"},email:{accounts:[],state:"ready",classification:{counts:{action:1}}}}};
  if(p==="/api/inbox-mail/task") {
   if(request.method()==="POST") {
    state.posts.push(request.postDataJSON()); state.receipt={state:pending?"pending":"complete",proposalId:"proposal",commitmentId:pending?null:"task",draft:request.postDataJSON()};
    if(uncertain){uncertain=false;return route.abort("failed");}
   }
   body=state.receipt||{state:"new",draft:{title:"Review",capacityId:null}};
  }
  await route.fulfill({json:body});
 });
 await page.goto("/?view=today#today/inbox/2026-09-22");
 return state;
}
async function open(page:Page){await page.locator(".inbox-mail-card > details > summary").click();await page.getByRole("button",{name:"Convert to task",exact:true}).click();return page.getByRole("dialog",{name:"Convert email to task"});}
for(const width of [390,1440])test(`email conversion edits title and labelled capacity at ${width}`,async({page})=>{
 await page.setViewportSize({width,height:844});const state=await fixture(page);const dialog=await open(page);
 await expect(dialog.getByLabel("Task title")).toHaveValue("Review");await dialog.getByLabel("Task title").fill("Write a useful response");await dialog.getByRole("combobox",{name:"Capacity",exact:true}).selectOption({label:"Work"});
 await dialog.getByRole("button",{name:"Create task",exact:true}).click();await expect(dialog.getByText("Task created.",{exact:false})).toBeVisible();
 expect(state.posts).toEqual([{key:"email:"+"a".repeat(64),title:"Write a useful response",capacityId:"work-id"}]);await expect(dialog.getByRole("link",{name:"Open task"})).toHaveAttribute("href","/?view=goals&commitment=task");
 await page.reload();const reopened=await open(page);await expect(reopened.getByRole("link",{name:"Open task"})).toBeVisible();expect(state.posts).toHaveLength(1);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test("manual approval and uncertain delivery preserve existing request without autosend",async({page})=>{
 const state=await fixture(page,true,true);const dialog=await open(page);await dialog.getByRole("button",{name:"Create task",exact:true}).click();await expect(dialog.getByRole("alert")).toContainText("uncertain");
 await dialog.getByRole("button",{name:"Check saved request"}).click();await expect(dialog.getByText("Task awaits approval.",{exact:false})).toBeVisible();await expect(dialog.getByRole("link",{name:"Open Approvals"})).toBeVisible();expect(state.posts).toHaveLength(1);
 await page.reload();const reopened=await open(page);await expect(reopened.getByText("Task awaits approval.",{exact:false})).toBeVisible();expect(state.posts).toHaveLength(1);
});
