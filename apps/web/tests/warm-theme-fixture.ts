import {expect, type Page} from "@playwright/test";
import {chooseConversation} from "./navigation";
// Response shapes reuse today-pages, today-board-order and companion-single-stop callers.
// Every API request is intercepted: captures contain synthetic information only.
export const sizes = [{width:390,height:844},{width:768,height:1024},{width:1440,height:900},{width:844,height:390}];
export const surfaces = ["today", "companion", "settings", "boards"] as const;
export type Surface = typeof surfaces[number];
export async function warmFixture(page:Page) {
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {close() {}};
  });
  const capacity = "10000000-0000-4000-8000-000000000001";
  const cards = [
    {id:"walk",title:"Take a restorative walk",stage:"todo",priority:"normal",owner:"user"},
    {id:"review",title:"Review the weekly plan",stage:"in_progress",priority:"high",owner:"user"},
    {id:"notes",title:"Prepare a short meeting summary",stage:"blocked",priority:"normal",owner:"leam"},
  ].map(card=>({...card,capacityId:capacity,revision:1,kind:"task",status:"active",notes:"A small next step with room to think.",subtasks:[],startDate:null,endDate:null,dueDate:null,measure:"boolean",target:1,timezone:"Europe/London",reward:"",reminderTime:null}));
  await page.route("**/api/**", async route=> {
    const u = new URL(route.request().url()), p=u.pathname;
    let body:any={items:[],data:[],threads:[],messages:[],models:[],providers:[]};
    if(p==="/api/auth/status") body={configured:true,authenticated:true};
    if(p==="/api/capacities") body={items:[{id:capacity,name:"Personal wellbeing",revision:1,note:"Steady progress, with space to recharge.",record:""}]};
    if(p==="/api/commitments") body={items:cards,boardOrders:{[capacity]:{capacityId:capacity,revision:1,membershipToken:"a".repeat(64),ids:cards.map(c=>c.id),canReorder:true}}};
    if(p==="/api/agenda") {
      const date=u.searchParams.get("date") || "2026-09-22";
      body={date,timezone:"Europe/London",nextOffset:null,partial:false,total:{commitments:3,events:1,emails:1},window:{start:date+"T00:00:00Z",end:date+"T23:59:59Z"},commitments:cards.map(c=>({...c,key:"commitment:"+c.id,date,log:{value:0,done:false,revision:0},triage:{disposition:c.id==="walk"?"focus":"none",revision:1}})),events:[{key:"event:calendar:review",eventId:"review",title:"A little time to reflect",start:date+"T15:00:00Z",end:date+"T15:30:00Z",triage:{disposition:"none",revision:0},visibility:{hidden:false,revision:0}}],emails:[{key:"email:account:message",subject:"Choose a time for our catch-up",from:"Alex",receivedAt:date+"T08:00:00Z",actionability:{state:"action",action:"Reply with your availability"},triage:{disposition:"none",revision:0}}],sources:{calendar:{state:"ready",accounts:[],snapshots:[]},email:{state:"ready",accounts:[],classification:{state:"ready",counts:{action:1,review:0,ignore:0,pending:0}}}}};
    }
    if(p==="/api/agenda/reconciliation") body={items:[],coverage:{enabledAt:1,scope:"Current conversations",state:"idle"}};
    if(p==="/api/agenda/chat") body={threadId:"warm-chat"};
    if(p==="/api/companion/threads") body={threads:[{thread_id:"warm-chat",title:"Making space for the day"}]};
    if(p==="/api/companion/threads/warm-chat") body={messages:[{message_id:"user-1",turn_run_id:"run-1",kind:"user",status:"submitted",sequence:1,content:"Help me make today feel manageable."},{message_id:"reply-1",turn_run_id:"run-1",kind:"assistant",status:"finalized",sequence:2,content:"Let's make a little room. Start with your walk, then choose one useful thing to finish.\n\nYou have time to review your plan this afternoon. Everything else can wait for a calmer moment."}]};
    if(p==="/api/settings/providers") body={providers:[]};
    if(p==="/api/approvals/policy") body={revision:1,todayRequiresApproval:false,goalsRequiresApproval:false,codingRequiresApproval:true};
    await route.fulfill({json:body});
  });
}
export async function openSurface(page:Page, surface:Surface) {
  await page.goto(surface==="boards"?"/?view=today#today/boards/2026-09-22":`/?view=${surface}`);
  if(surface==="companion") {await chooseConversation(page,"warm-chat");await expect(page.getByText("Let's make a little room.",{exact:false})).toBeVisible();}
  if(surface==="boards") await expect(page.getByRole("button",{name:"Edit Take a restorative walk",exact:true})).toBeVisible();
  if(surface==="settings") await expect(page.getByRole("heading",{name:"Settings",exact:true})).toBeVisible();
  if(surface==="today") await expect(page.getByRole("navigation",{name:"Today pages"})).toBeVisible();
  await page.evaluate(()=>document.fonts.ready);
}
