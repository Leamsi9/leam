import {test,expect,Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
async function fixture(page:Page, lost=false){
  let state:any={held:false}, signedIn=true;
  const writes:any[]=[];
  await page.route('**/*',async route=>{
    const path=new URL(route.request().url()).pathname,method=route.request().method();
    const reply=(json:any,status=200)=>route.fulfill({json,status});
    if(!path.startsWith('/api/')){
      const name=path==='/'?'index.html':path.slice(1);
      if(!['index.html','recovery.js','restore.js','maintenance.js','recovery.css'].includes(name))return route.abort();
      return route.fulfill({body:await readFile(resolve('../../leam_api/recovery_assets',name)),contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html'});
    }
    if(path==='/api/auth/status')return reply({authenticated:signedIn,configured:true});
    if(path==='/api/auth/logout'){signedIn=false;return reply({ok:true});}
    if(path==='/api/services')return reply({services:[],checkedAt:1700000000});
    if(path==='/api/restores')return reply({available:false,backups:[],history:[]});
    if(path==='/api/maintenance')return reply({maintenance:state});
    if(method==='POST'&&path.startsWith('/api/maintenance/')){
      const body=route.request().postDataJSON();writes.push({path,body});
      if(path.endsWith('/prepare'))state={held:true,phase:writes.length===1?'draining':'held',requestId:body.requestId,version:1,createdAt:1700000000};
      else state={held:false};
      if(lost&&writes.length===1)return route.abort('failed');
      return reply({maintenance:state,idle:state.phase==='held'});
    }
    return reply({detail:'missing fixture'},404);
  });
  return writes;
}
for(const width of [390,1440])test(`maintenance is explicit, draining preserves identity and release is reviewed at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});const writes=await fixture(page);await page.goto('/');
  await expect(page.locator('#maintenance-notice')).toContainText('Normal operation');expect(writes).toHaveLength(0);
  await page.getByRole('button',{name:'Prepare maintenance',exact:true}).click();
  await expect(page.locator('#maintenance-notice')).toContainText('still active or uncertain');expect(writes).toHaveLength(1);
  await page.getByRole('button',{name:'Check accepted work',exact:true}).click();
  await expect(page.locator('#maintenance-notice')).toContainText('Maintenance held');expect(writes[1].body.requestId).toBe(writes[0].body.requestId);
  let allow=false,dialogs=0;page.on('dialog',async d=>{dialogs++;await (allow?d.accept():d.dismiss());});
  await page.getByRole('button',{name:'End maintenance',exact:true}).click();await expect.poll(()=>dialogs).toBe(1);await expect(page.getByRole('button',{name:'End maintenance',exact:true})).toBeEnabled();expect(writes).toHaveLength(2);
  allow=true;await page.getByRole('button',{name:'End maintenance',exact:true}).click();
  await expect(page.locator('#maintenance-notice')).toContainText('Normal operation');expect(writes).toHaveLength(3);
  expect(writes[2].body.requestId).toBe(writes[0].body.requestId);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('lost prepare response and reload recover held identity without automatic mutation',async({page})=>{
  const writes=await fixture(page,true);await page.goto('/');await page.getByRole('button',{name:'Prepare maintenance',exact:true}).click();
  await expect(page.locator('#maintenance-notice')).toContainText('no automatic retry');expect(writes).toHaveLength(1);
  await page.reload();await expect(page.locator('#maintenance-notice')).toContainText('New work is paused');expect(writes).toHaveLength(1);
  await page.getByRole('button',{name:'Check accepted work',exact:true}).click();await expect(page.locator('#maintenance-notice')).toContainText('Maintenance held');
  expect(writes[1].body.requestId).toBe(writes[0].body.requestId);
});
