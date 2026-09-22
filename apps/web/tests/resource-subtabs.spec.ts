import { test, expect } from '@playwright/test';
for (const width of [390,844]) test(`Resource groups share file filters and ordering ${width}`,async({page})=>{
 await page.setViewportSize({width,height:800});
 const queries: URLSearchParams[]=[];
 await page.addInitScript(()=>{(window as any).EventSource=class extends EventTarget {close(){}}});
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url());let body:any={items:[],threads:[],providers:[],models:[]};
  if(url.pathname==='/api/auth/status')body={authenticated:true,configured:true};
  if(url.pathname==='/api/artifacts/_status')body={unreadCount:0,unreadIds:[],total:0};
  if(url.pathname==='/api/artifacts'){queries.push(url.searchParams);body={items:[],total:0,nextCursor:null};}
  await route.fulfill({json:body});
 });
 await page.goto('/?view=resources');
 await expect(page.getByRole('button',{name:'Generated',exact:true})).toHaveAttribute('aria-pressed','true');
 await page.getByRole('combobox',{name:'File type',exact:true}).selectOption('html');
 await page.getByRole('combobox',{name:'Sort resources',exact:true}).selectOption('type');
 await expect.poll(()=>queries.at(-1)?.get('kind')).toBe('html');
 await expect.poll(()=>queries.at(-1)?.get('sort')).toBe('type');
 await expect(page.getByRole('option',{name:'Website',exact:true})).toHaveCount(1);
 await page.getByRole('button',{name:'Uploads',exact:true}).click();
 await expect.poll(()=>queries.at(-1)?.get('group')).toBe('uploads');
 expect(queries.at(-1)?.get('kind')).toBe('html');
 await page.getByRole('combobox',{name:'File type',exact:true}).selectOption('pdf');
 await page.getByRole('combobox',{name:'Order',exact:true}).selectOption('asc');
 await expect.poll(()=>queries.at(-1)?.get('order')).toBe('asc');
 expect(queries.at(-1)?.get('kind')).toBe('pdf');
 await page.getByRole('button',{name:'Generated',exact:true}).click();
 await expect.poll(()=>queries.at(-1)?.get('group')).toBe('generated');
 expect(queries.at(-1)?.get('kind')).toBe('pdf');
});
