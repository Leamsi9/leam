import { test, expect, type Page } from '@playwright/test';
import { navigate } from './navigation';
const run='19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63';
async function setup(page: Page) {
  await page.setViewportSize({width:390,height:844});
  await page.addInitScript(()=>{
    const w=window as any; w.probe={starts:0,spoken:[],aborts:0}; w.streams={};
    localStorage.setItem('leam.voice.input.v1',JSON.stringify({language:'en-GB',pauseSeconds:1}));
    w.SpeechRecognition=class {
      onstart:any;onresult:any;onend:any;onerror:any;
      constructor(){w.capture=this;}
      start(){w.probe.starts++;this.onstart?.();}
      stop(){this.onend?.();}
      abort(){w.probe.aborts++;}
    };
    w.transcript=(text:string)=>w.capture.onresult?.({results:[{isFinal:true,0:{transcript:text}}]});
    w.SpeechSynthesisUtterance=class {text:string;constructor(text:string){this.text=text;}};
    Object.defineProperty(w,'speechSynthesis',{configurable:true,value:{getVoices:()=>[],cancel:()=>{},speak:(value:any)=>{w.probe.spoken.push(value.text);w.utterance=value;value.onstart?.();}}});
    w.EventSource=class extends EventTarget {constructor(url:string){super();const match=url.match(/companion\/threads\/([^/]+)\/events/);if(match)(w.streams[match[1]] ||= []).push(this);}closed=false;close(){this.closed=true;}};
  });
  const sends:{path:string;text:string}[]=[]; let lateReply=false;
  await page.route('**/api/**',async route=>{
    const p=new URL(route.request().url()).pathname;
    let body:any={items:[],data:[]};
    if(p==='/api/auth/status')body={authenticated:true};
    if(p==='/api/today'||p==='/api/commitments')body={items:['A','B'].map(id=>({id,title:`Item ${id}`,kind:'habit',revision:1,status:'active',measure:'boolean',target:1,log:{value:0,revision:0,done:false}}))};
    if(p==='/api/companion/system')body={available:true,activeModel:{model:'test',reasoning_effort:'medium'}};
    if(p.endsWith('/chat'))body={threadId:p.includes('/A/')?'item-a':'item-b'};
    if(p.startsWith('/api/companion/threads/'))body={messages:p.endsWith('/item-a')&&lateReply?[{message_id:'late-a',turn_run_id:run,kind:'assistant',status:'finalized',sequence:1,content:'Late item A reply'}]:[]};
    if(p.endsWith('/messages')){sends.push({path:p,text:route.request().postDataJSON().text});body={outcome:'submitted',run_id:run};}
    await route.fulfill({json:body});
  });
  await page.goto('/'); await navigate(page,'Goals');
  await page.getByText('Chat about Item A',{exact:true}).click();
  await expect(page.getByRole('region',{name:'Chat about Item A'}).getByLabel('Message Leam')).toBeVisible();
  await page.clock.install();
  return {sends,late:()=>{lateReply=true;}};
}
function panel(page:Page,id:string){return page.getByRole('region',{name:`Chat about Item ${id}`});}
async function openB(page:Page){
  await page.getByText('Chat about Item B',{exact:true}).click();
  await expect(panel(page,'B').getByLabel('Message Leam')).toBeVisible();
}

test('two open item chats transfer voice ownership only on explicit start and ignore late prior reply',async({page})=>{
  const {sends,late}=await setup(page);
  await panel(page,'A').getByRole('button',{name:'Conversation',exact:true}).click();
  await page.evaluate(()=>(window as any).transcript('Discuss item A'));
  await page.clock.fastForward(1100);
  await expect.poll(()=>sends.length).toBe(1);
  await expect(panel(page,'A').getByText('Waiting for reply…',{exact:true})).toBeVisible();
  await openB(page);
  await expect(panel(page,'A').getByText('Waiting for reply…',{exact:true})).toBeVisible();
  await panel(page,'B').getByRole('button',{name:'Conversation',exact:true}).click();
  await expect(panel(page,'B').getByText('Listening…',{exact:true})).toBeVisible();
  await expect(panel(page,'A').getByRole('button',{name:'End conversation',exact:true})).toHaveCount(0);
  const starts=await page.evaluate(()=>(window as any).probe.starts);
  late();
  await page.evaluate(run=>(window as any).streams['item-a'].filter((stream:any)=>!stream.closed).forEach((stream:any)=>stream.dispatchEvent(new MessageEvent('projection_update',{data:JSON.stringify({type:'projection_update',state:{thread_id:'item-a',items:[{text:{id:'late-a',run_id:run,body:'Late item A reply',finalized:true}},{run_status:{run_id:run,status:'completed'}}]}})}))),run);
  await expect(panel(page,'A').getByText('Late item A reply',{exact:true})).toBeVisible();
  await page.clock.fastForward(200);
  expect(await page.evaluate(()=>(window as any).probe.spoken)).toEqual([]);
  expect(await page.evaluate(()=>(window as any).probe.starts)).toBe(starts);
  await expect(panel(page,'B').getByText('Listening…',{exact:true})).toBeVisible();
  expect(sends).toHaveLength(1);
});

test('dictation and typed input stop the actually active item conversation, not the last mounted panel',async({page})=>{
  const {sends}=await setup(page);await openB(page);
  await panel(page,'A').getByRole('button',{name:'Conversation',exact:true}).click();
  await expect(panel(page,'A').getByText('Listening…',{exact:true})).toBeVisible();
  await panel(page,'B').getByRole('button',{name:'Dictate',exact:true}).click();
  await expect(panel(page,'A').getByRole('button',{name:'End conversation',exact:true})).toHaveCount(0);
  await panel(page,'A').getByRole('button',{name:'Conversation',exact:true}).click();
  await expect(panel(page,'A').getByText('Listening…',{exact:true})).toBeVisible();
  await panel(page,'B').getByLabel('Message Leam').fill('Typed item B');
  await expect(panel(page,'A').getByRole('button',{name:'End conversation',exact:true})).toHaveCount(0);
  await panel(page,'B').getByRole('button',{name:'Send to Leam',exact:true}).click();
  await expect.poll(()=>sends.length).toBe(1);expect(sends[0]).toEqual({path:'/api/companion/threads/item-b/messages',text:'Typed item B'});
});

test('Ear ownership stays with its item until another explicit Ear activation',async({page})=>{
  const {sends}=await setup(page);
  await panel(page,'A').getByRole('button',{name:'Active listening (5 minutes)',exact:true}).click();
  await openB(page);
  await expect(panel(page,'A').getByRole('button',{name:'Stop active listening',exact:true})).toBeVisible();
  await panel(page,'B').getByRole('button',{name:'Active listening (5 minutes)',exact:true}).click();
  await expect(panel(page,'A').getByRole('button',{name:'Stop active listening',exact:true})).toHaveCount(0);
  await expect(panel(page,'B').getByRole('button',{name:'Stop active listening',exact:true})).toBeVisible();
  await page.clock.fastForward(12000);
  expect(sends).toHaveLength(0);
  expect(await page.evaluate(()=>(window as any).probe.starts)).toBe(2);
});
