import {test,expect} from "@playwright/test";
for(const theme of ["light","dark"])for(const width of [390,1440])test(`soft navigation colours keep contrast and focus ${theme} ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900});
 await page.addInitScript(theme=>{localStorage.setItem("leam.theme.v1",theme);(window as any).EventSource=class extends EventTarget{close(){}};},theme);
 await page.route("**/api/**",route=>{const u=new URL(route.request().url());let json:any={items:[],data:[],threads:[],messages:[],providers:[]};if(u.pathname==="/api/auth/status")json={authenticated:true};if(u.pathname==="/api/agenda")json={date:u.searchParams.get("date"),commitments:[],events:[],emails:[],total:{commitments:0,events:0,emails:0},sources:{calendar:{accounts:[],state:"not_connected"},email:{accounts:[],state:"not_connected"}}};return route.fulfill({json});});
 await page.goto("/?view=today#today/wellbeing/2026-09-22");
 await expect(page.locator("html")).toHaveAttribute("data-theme",theme);
 const wellbeing=page.locator('.today-navigation [data-page="wellbeing"]');await expect(wellbeing).toHaveAttribute("aria-current","page");
 const selected=page.locator('.sidebar nav:visible button[data-destination="today"]');await selected.focus();
 await expect(selected).toBeFocused();expect(await selected.evaluate(el=>getComputedStyle(el).outlineWidth)).toBe("3px");
 expect(await selected.evaluate(el=>getComputedStyle(el).boxShadow)).not.toBe("none");
 const inspect=async()=>page.evaluate(()=>{
  function rgb(s:string){return (s.match(/[\d.]+/g)||[]).slice(0,3).map(Number);}
  function lum(c:number[]){return c.map(n=>{n/=255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;}).reduce((s,n,i)=>s+n*[.2126,.7152,.0722][i],0);}
  return [...document.querySelectorAll('[data-destination],.today-navigation [data-page="wellbeing"]')].filter(e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0).map(e=>{const s=getComputedStyle(e),a=lum(rgb(s.color)),b=lum(rgb(s.backgroundColor));return {name:e.textContent?.trim(),bg:s.backgroundColor,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
 });
 const rows=await inspect();expect(new Set(rows.map(r=>r.bg)).size).toBeGreaterThanOrEqual(5);expect(rows.filter(r=>r.ratio<4.5)).toEqual([]);
 const rose=await wellbeing.evaluate(el=>{const s=getComputedStyle(el);return {background:s.backgroundColor,ink:s.color,rose:s.getPropertyValue("--semantic-rose-surface").trim()};});expect(rose.rose).not.toBe("");
 await page.screenshot({path:info.outputPath(`nav-${theme}-${width}.png`)});
 if(width===390){await page.locator('.mobile-navigation button[data-destination="more"]').click();await expect(page.getByRole("dialog",{name:"More from Leam"})).toBeVisible();expect((await inspect()).filter(r=>r.ratio<4.5)).toEqual([]);}
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
