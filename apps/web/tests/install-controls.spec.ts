import { test, expect, type Page } from "@playwright/test";
import { navigate, settingsSection } from "./navigation";
async function open(page: Page) {
 await page.route("**/api/**", async route => { const p=new URL(route.request().url()).pathname; await route.fulfill({json: p==="/api/auth/status" ? {authenticated:true,configured:true} : {items:[],data:[],providers:[],profiles:[]} }); });
 await page.goto("/"); await navigate(page,"Settings"); await settingsSection(page,"App installation and offline");
}
for (const width of [390,1440]) test(`manual installation at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900}); await open(page);
 await expect(page.getByAltText("Leam robot icon")).toBeVisible();
 await page.getByRole("button",{name:"Install Leam",exact:true}).click();
 await expect(page.getByRole("region",{name:"Installation instructions"})).toBeVisible();
 await expect(page.getByText("Leam is installed.",{exact:false})).toHaveCount(0);
});
test("native dismissal is not installation; actual event confirms",async({page})=>{
 await open(page);
 await page.evaluate(()=>{const e=new Event("beforeinstallprompt",{cancelable:true}); Object.assign(e,{prompt:async()=>{(window as any).installCalls=((window as any).installCalls||0)+1},userChoice:Promise.resolve({outcome:"dismissed"})});window.dispatchEvent(e)});
 await page.getByRole("button",{name:"Install Leam",exact:true}).click();
 await expect(page.getByText("Installation dismissed.",{exact:false})).toBeVisible();
 expect(await page.evaluate(()=>(window as any).installCalls)).toBe(1);
 await page.evaluate(()=>window.dispatchEvent(new Event("appinstalled")));
 await expect(page.getByText("Leam is installed.",{exact:false})).toBeVisible();
 await expect(page.getByRole("button",{name:"Install Leam",exact:true})).toHaveCount(0);
});
test("iPhone gets Safari home screen instructions",async({page})=>{
 await page.addInitScript(()=>Object.defineProperty(navigator,"userAgent",{value:"iPhone"})); await open(page);
 await page.getByRole("button",{name:"Install Leam",exact:true}).click();
 await expect(page.getByRole("region",{name:"Installation instructions"})).toContainText("Safari");
 await expect(page.getByRole("region",{name:"Installation instructions"})).toContainText("Add to Home Screen");
});
test("manifest and favicons serve actual packaged raster sizes",async({request})=>{
 const manifest=await (await request.get("/manifest.webmanifest")).json();
 expect(manifest.icons).toHaveLength(3);
 for(const icon of manifest.icons){const response=await request.get(icon.src);expect(response.ok()).toBeTruthy();const png=await response.body(); const [w,h]=icon.sizes.split("x").map(Number); expect(png.readUInt32BE(16)).toBe(w);expect(png.readUInt32BE(20)).toBe(h);}
 expect(manifest.icons.some((i:any)=>i.purpose==="maskable")).toBeTruthy();
 const ico=await (await request.get("/favicon.ico")).body();expect(ico.readUInt16LE(2)).toBe(1);expect(ico.readUInt16LE(4)).toBe(2);
 const html=await (await request.get("/")).text();expect(html).toContain('rel="apple-touch-icon"');
});
