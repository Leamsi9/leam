import {test,expect} from "@playwright/test";
import {mkdirSync,writeFileSync} from "node:fs";
import {warmFixture,openSurface,surfaces} from "./warm-theme-fixture";
import {contrastAudit} from "./warm-theme-contrast";
const key="leam.theme.v1";
const theme=(page:any)=>page.locator("html");
async function preference(page:any,value:string){await page.addInitScript(({key,value}:any)=>localStorage.setItem(key,value),{key,value});}

test("default System follows OS and explicit choice persists over reload",async({page})=>{
 await page.emulateMedia({colorScheme:"dark"});await warmFixture(page);await openSurface(page,"settings");
 await expect(theme(page)).toHaveAttribute("data-theme","dark");await expect(theme(page)).toHaveAttribute("data-theme-preference","system");
 const select=page.getByRole("combobox",{name:"Colour theme",exact:true});await expect(select).toHaveValue("system");
 await select.selectOption("light");await expect(theme(page)).toHaveAttribute("data-theme","light");
 expect(await page.evaluate(key=>localStorage.getItem(key),key)).toBe("light");
 await page.reload();await expect(select).toHaveValue("light");await expect(theme(page)).toHaveAttribute("data-theme","light");
 await page.emulateMedia({colorScheme:"light"});await select.selectOption("dark");await expect(theme(page)).toHaveAttribute("data-theme","dark");
 await select.selectOption("system");await expect(theme(page)).toHaveAttribute("data-theme","light");
 await page.emulateMedia({colorScheme:"dark"});await expect(theme(page)).toHaveAttribute("data-theme","dark");
});

test("invalid stored preference falls back to System",async({page})=>{
 await preference(page,"not-a-theme");await page.emulateMedia({colorScheme:"dark"});await warmFixture(page);await openSurface(page,"settings");
 await expect(theme(page)).toHaveAttribute("data-theme-preference","system");await expect(theme(page)).toHaveAttribute("data-theme","dark");
});

test("storage denial preserves usable controls and an in-memory preference",async({page})=>{
 await page.addInitScript(()=>{Storage.prototype.getItem=function(){throw new DOMException("Blocked","SecurityError");};Storage.prototype.setItem=function(){throw new DOMException("Blocked","SecurityError");};});
 await page.emulateMedia({colorScheme:"light"});await warmFixture(page);await openSurface(page,"settings");
 await expect(theme(page)).toHaveAttribute("data-theme","light");
 await page.getByRole("combobox",{name:"Colour theme",exact:true}).selectOption("dark");
 await expect(theme(page)).toHaveAttribute("data-theme","dark");await expect(page.getByRole("heading",{name:"Settings",exact:true})).toBeVisible();
});

test("other-tab storage updates preference without reload",async({page})=>{
 await warmFixture(page);await openSurface(page,"settings");
 const other=await page.context().newPage();await warmFixture(other);await openSurface(other,"settings");
 await other.getByRole("combobox",{name:"Colour theme",exact:true}).selectOption("dark");
 await expect(theme(page)).toHaveAttribute("data-theme","dark");await expect(page.getByRole("combobox",{name:"Colour theme",exact:true})).toHaveValue("dark");
 await page.emulateMedia({colorScheme:"light"});
 await other.evaluate(key=>localStorage.removeItem(key),key);
 await expect(theme(page)).toHaveAttribute("data-theme-preference","system");await expect(theme(page)).toHaveAttribute("data-theme","light");
 await other.close();
});

test("prepaint bootstrap sets saved theme before application module",async({page})=>{
 await preference(page,"dark");await warmFixture(page);
 await page.route("**/assets/*.js",route=>route.abort());await page.goto("/");
 await expect(theme(page)).toHaveAttribute("data-theme","dark");
 expect(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme)).toContain("dark");
});

test("reduced motion does not run lengthy theme transitions",async({page})=>{
 await page.emulateMedia({reducedMotion:"reduce"});await warmFixture(page);await openSurface(page,"settings");
 await page.getByRole("combobox",{name:"Colour theme",exact:true}).selectOption("dark");
 const long=await page.evaluate(()=>[...document.querySelectorAll("body,button,input,select,.card,.settings-section")].filter(el=>{
 const s=getComputedStyle(el);return [...s.animationDuration.split(","),...s.transitionDuration.split(",")].some(value=>parseFloat(value)*(value.trim().endsWith("ms")?1:1000)>10);
 }).map(el=>el.tagName+"."+el.className));expect(long).toEqual([]);
});

for(const mode of ["light","dark"])for(const surface of surfaces)test(`rendered contrast ${mode} ${surface}`,async({page})=>{
 await page.setViewportSize({width:390,height:844});await preference(page,mode);await warmFixture(page);await openSurface(page,surface);
 await expect(theme(page)).toHaveAttribute("data-theme",mode);
 const initial=await contrastAudit(page);
 if(surface==="boards") await page.locator(".board-card").first().scrollIntoViewIfNeeded();
 if(surface==="settings") await page.getByRole("combobox",{name:"Colour theme",exact:true}).scrollIntoViewIfNeeded();
 const secondary=await contrastAudit(page);
 const audit={...initial,rows:[...initial.rows,...secondary.rows],failures:[...initial.failures,...secondary.failures],unmeasured:[...initial.unmeasured,...secondary.unmeasured]};const dir=process.env.LEAM_THEME_EVIDENCE||"/tmp/leam-build-control/warm-theme-qa";mkdirSync(dir,{recursive:true});writeFileSync(`${dir}/contrast-${mode}-${surface}.json`,JSON.stringify(audit,null,2));
 expect(audit.rows.length).toBeGreaterThan(3);expect(audit.overflow).toBe(false);
 expect(audit.failures,JSON.stringify(audit.failures,null,2)).toEqual([]);
});

test("appearance remains keyboard operable with visible focus",async({page})=>{
 await warmFixture(page);await openSurface(page,"settings");
 const select=page.getByRole("combobox",{name:"Colour theme",exact:true});await select.focus();
 await expect(select).toBeFocused();
 const focus=await select.evaluate(el=>{const s=getComputedStyle(el);return {style:s.outlineStyle,width:parseFloat(s.outlineWidth),color:s.outlineColor};});
 expect(focus.style).not.toBe("none");expect(focus.width).toBeGreaterThanOrEqual(2);
 const measured=await contrastAudit(page);const ring=measured.rows.find(row=>row.kind==="focus");expect(ring).toBeTruthy();expect(ring?.ratio).toBeGreaterThanOrEqual(3);
 await page.keyboard.press("Home");await page.keyboard.press("ArrowDown");await page.keyboard.press("Enter");
 await expect(select).toHaveValue("light");await expect(theme(page)).toHaveAttribute("data-theme","light");
});
