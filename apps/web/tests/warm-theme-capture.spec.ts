import {test} from "@playwright/test";
import {mkdirSync,writeFileSync} from "node:fs";
import {warmFixture,openSurface,sizes,surfaces} from "./warm-theme-fixture";
import {contrastAudit} from "./warm-theme-contrast";
const stage=process.env.LEAM_THEME_CAPTURE || "disabled";
for(const size of sizes) for(const surface of surfaces) test(`capture ${stage} ${surface} ${size.width}x${size.height}`,async({page})=>{
  test.skip(stage==="disabled","Explicit visual evidence collection only");
  await page.setViewportSize(size);
  if(stage!=="before") await page.addInitScript(mode=>localStorage.setItem("leam.theme.v1",mode),stage);
  await warmFixture(page);await openSurface(page,surface);
  const directory=process.env.LEAM_THEME_EVIDENCE || "/tmp/leam-build-control/warm-theme-qa";
  mkdirSync(directory,{recursive:true});
  const name=`${stage}-${surface}-${size.width}x${size.height}`;
  await page.screenshot({path:`${directory}/${name}.png`,fullPage:false});
  const metadata=await page.evaluate(()=>({url:location.href,width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,theme:document.documentElement.dataset.theme,assets:[...document.scripts].map(s=>s.src).filter(Boolean),background:getComputedStyle(document.body).backgroundColor}));
  writeFileSync(`${directory}/${name}.json`,JSON.stringify({...metadata,contrast:await contrastAudit(page)},null,2));
  if(surface==="boards") {
    await page.locator(".board-card").first().scrollIntoViewIfNeeded();
    await page.screenshot({path:`${directory}/${name}-cards.png`,fullPage:false});
    writeFileSync(`${directory}/${name}-cards.json`,JSON.stringify(await contrastAudit(page),null,2));
  }
});
