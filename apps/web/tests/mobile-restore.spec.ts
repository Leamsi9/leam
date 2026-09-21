import { test, expect, Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const backup = '11111111-1111-4111-8111-111111111111';
const terminal = (id: string, operation = 'restore') => ({requestId:id,operation,state:operation === 'restore' ? 'ready_for_uat' : 'rolled_back',updatedAt:1700000000,message:'State verified by controlled service fixture.'});
async function fixture(page: Page, lost = false) {
  const posts: {path:string,body:any}[] = [], history: any[] = [];
  let receipt: any = null, signedIn = true;
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), path = url.pathname, method = route.request().method();
    if (!path.startsWith('/api/')) {
      const name = path === '/' ? 'index.html' : path.slice(1);
      if (!['index.html','recovery.js','restore.js','recovery.css'].includes(name)) return route.abort();
      return route.fulfill({body:await readFile(resolve('../../leam_api/recovery_assets',name)),contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html'});
    }
    const reply = (body: any, status = 200) => route.fulfill({status,json:body});
    if(path === '/api/auth/status') return reply({configured:true,authenticated:signedIn});
    if(path === '/api/auth/logout') {signedIn=false;return reply({ok:true});}
    if(path === '/api/services') return reply({services:[],checkedAt:1700000000});
    if(path === '/api/restores' && method === 'GET') return reply({available:true,backups:[{id:backup,createdAt:1700000000,bytes:2048}],history});
    if(path.endsWith('/preview') || path.endsWith('/rollback-preview')) return reply({previewToken:'a'.repeat(64),generationId:backup,warning:'Candidate services will stop. External actions and runtime permissions are not undone.'});
    if(method === 'POST' && (path === '/api/restores' || path.endsWith('/rollback'))) {
      const body = route.request().postDataJSON(); posts.push({path,body});
      receipt = terminal(body.requestId, path.endsWith('/rollback')?'rollback':'restore');
      if(receipt.operation === 'rollback') history.forEach(row => {row.state='rolled_back';});
      history.unshift(receipt);
      if(lost) return route.abort('failed');
      return reply({...receipt,state:'reserved'},202);
    }
    if(receipt && path === `/api/restores/${receipt.requestId}`) return reply(receipt);
    return reply({detail:'Missing controlled route'},404);
  });
  return {posts};
}
for (const width of [390,1440]) test(`restore and explicit rollback from independent UI at ${width}px`, async ({page}) => {
  await page.setViewportSize({width,height:900});
  const {posts} = await fixture(page);
  await page.goto('/');
  await page.getByText('Restore product state',{exact:true}).click();
  await page.getByRole('button',{name:'Review selected backup'}).click();
  await expect(page.getByRole('button',{name:'Confirm restore'})).toBeDisabled();
  await page.locator('#restore-confirmed').check();
  await page.getByRole('button',{name:'Confirm restore'}).click();
  await expect(page.locator('#restore-receipt')).toContainText('ready for your testing');
  expect(posts).toHaveLength(1);
  expect(posts[0].body).toMatchObject({backupId:backup,previewToken:'a'.repeat(64),confirmed:true});
  expect(posts[0].body.requestId).toMatch(/^[0-9a-f-]{36}$/);
  await page.getByRole('button',{name:'Review rollback'}).click();
  await page.locator('#restore-confirmed').check();
  await page.getByRole('button',{name:'Confirm rollback'}).click();
  await expect(page.locator('#restore-receipt')).toContainText('Rollback completed');
  expect(posts).toHaveLength(2);
  expect(posts[1].path).toBe(`/api/restores/${posts[0].body.requestId}/rollback`);
  expect(posts[1].body.requestId).not.toBe(posts[0].body.requestId);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test('lost response and reload reconcile receipt without repeating an operation', async ({page}) => {
  const {posts} = await fixture(page,true);
  await page.goto('/');
  await page.getByText('Restore product state',{exact:true}).click();
  await page.getByRole('button',{name:'Review selected backup'}).click();
  await page.locator('#restore-confirmed').check();
  await page.getByRole('button',{name:'Confirm restore'}).click();
  await expect(page.locator('#restore-receipt')).toContainText('ready for your testing');
  expect(posts).toHaveLength(1);
  // Model a reload that happened after dispatch and before receipt persistence.
  await page.evaluate(id=>sessionStorage.setItem('leam-recovery-pending-operation-v1',id),posts[0].body.requestId);
  await page.reload();
  await expect(page.locator('#restore-receipt')).toContainText('ready for your testing');
  expect(posts).toHaveLength(1);
});
test('late preview after sign-out cannot restore private review controls', async ({page}) => {
  await fixture(page);
  let release!:()=>void;
  const held=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/restores/preview',async route=>{await held;await route.fulfill({json:{previewToken:'a'.repeat(64),warning:'Private held preview'}});});
  await page.goto('/');
  await page.getByText('Restore product state',{exact:true}).click();
  await page.getByRole('button',{name:'Review selected backup'}).click();
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  release();
  await expect(page.locator('#auth')).toBeVisible();
  await expect(page.locator('#dashboard')).toBeHidden();
  await expect(page.locator('#restore-review')).toBeHidden();
});
test('definitive rejected request with absent receipt permits fresh review without replay', async ({page}) => {
  await fixture(page);
  let submitted = 0;
  await page.route('**/api/restores',async route => {
    if(route.request().method() !== 'POST') return route.fallback();
    submitted++; return route.fulfill({status:409,json:{detail:'Preview is stale; review again.'}});
  });
  await page.goto('/');
  await page.getByText('Restore product state',{exact:true}).click();
  await page.getByRole('button',{name:'Review selected backup'}).click();
  await page.locator('#restore-confirmed').check();
  await page.getByRole('button',{name:'Confirm restore'}).click();
  await expect(page.getByRole('button',{name:'Review selected backup'})).toBeEnabled();
  expect(submitted).toBe(1);
  expect(await page.evaluate(()=>sessionStorage.getItem('leam-recovery-pending-operation-v1'))).toBeNull();
});
test('backup selection cannot silently change while its preview is loading', async ({page}) => {
  await fixture(page);
  let release!:()=>void;
  const held=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/restores/preview',async route=>{await held;await route.fulfill({json:{previewToken:'a'.repeat(64),warning:'Preview for A'}});});
  await page.goto('/');
  await page.getByText('Restore product state',{exact:true}).click();
  await page.getByRole('button',{name:'Review selected backup'}).click();
  await expect(page.locator('#restore-backup')).toBeDisabled();
  await expect(page.getByRole('button',{name:'Refresh backups and receipts'})).toBeDisabled();
  await page.locator('#restore-backup').evaluate((node:HTMLSelectElement)=>{node.replaceChildren(new Option('Different backup','22222222-2222-4222-8222-222222222222'));});
  release();
  await expect(page.locator('#restore-notice')).toContainText('Backup selection changed');
  await expect(page.locator('#restore-review')).toBeHidden();
  await expect(page.getByRole('button',{name:'Review selected backup'})).toBeEnabled();
});
