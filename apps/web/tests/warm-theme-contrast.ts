import type {Page} from "@playwright/test";
/** Computed paint colours, alpha-composited through ancestor backgrounds.
 * Gradients/images/opacity are reported as unmeasured, never a passing claim. */
export async function contrastAudit(page:Page) {
  return page.evaluate(()=>{
    const canvas=document.createElement("canvas");canvas.width=canvas.height=1;
    const ctx=canvas.getContext("2d")!;
    function rgba(value:string) {ctx.clearRect(0,0,1,1);ctx.fillStyle=value;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].map((n,i)=>i===3?n/255:n);}
    function over(f:number[],b:number[]) {return [0,1,2].map(i=>f[i]*f[3]+b[i]*(1-f[3])).concat(1);}
    function lum(c:number[]) {return c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);}
    function ratio(a:number[],b:number[]) {const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
    function background(el:Element|null) {
      const chain:Element[]=[];while(el){chain.unshift(el);el=el.parentElement;}
      let color=[255,255,255,1];let reason="";
      for(const ancestor of chain){const style=getComputedStyle(ancestor);if(style.backgroundImage!=="none")reason="background image/gradient";if(Number(style.opacity)!==1)reason="ancestor opacity";color=over(rgba(style.backgroundColor),color);}
      return {color,reason};
    }
    function visible(el:Element) {
      for(let ancestor:Element|null=el;ancestor;ancestor=ancestor.parentElement){
        if(ancestor.matches("details:not([open])")&&!ancestor.querySelector(":scope > summary")?.contains(el))return false;
      }
      const rect=el.getBoundingClientRect(),s=getComputedStyle(el);return rect.width>0&&rect.height>0&&rect.bottom>0&&rect.top<innerHeight&&rect.right>0&&rect.left<innerWidth&&s.visibility==="visible"&&s.display!=="none"&&!el.closest("[hidden], [aria-hidden=true]");}
    const rows:any[]=[],unmeasured:any[]=[];const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){
      const text=walker.currentNode.textContent?.trim(),el=walker.currentNode.parentElement;
      if(!text||!el||!visible(el)||el.closest("script,style,option,button:disabled,input:disabled,select:disabled"))continue;
      const range=document.createRange();range.selectNodeContents(walker.currentNode);const rect=range.getBoundingClientRect();if(rect.bottom<=0||rect.top>=innerHeight||!rect.width)continue;
      const style=getComputedStyle(el),bg=background(el),size=parseFloat(style.fontSize),weight=parseInt(style.fontWeight)||400;
      const threshold=size>=24||(size>=18.66&&weight>=700)?3:4.5;
      const row={kind:"text",text:text.slice(0,90),selector:el.tagName.toLowerCase()+"."+el.className,foreground:style.color,background:bg.color,fontSize:size,fontWeight:weight,threshold,ratio:ratio(over(rgba(style.color),bg.color),bg.color)};
      if(bg.reason)unmeasured.push({...row,reason:bg.reason});else rows.push(row);
    }
    for(const el of document.querySelectorAll("input,textarea,select")){
      if(!visible(el)||(el as HTMLInputElement).disabled||["hidden","range","checkbox","radio"].includes((el as HTMLInputElement).type))continue;
      const style=getComputedStyle(el),inner=background(el),outer=background(el.parentElement);
      const border=over(rgba(style.borderTopColor),outer.color),borderRatio=parseFloat(style.borderTopWidth)>0?ratio(border,outer.color):1;
      let groupRatio=1;
      const group=el.closest(".composer");
      if(group){const s=getComputedStyle(group),bg=background(group.parentElement);if(parseFloat(s.borderTopWidth)>0&&!bg.reason)groupRatio=ratio(over(rgba(s.borderTopColor),bg.color),bg.color);}
      const row={kind:"control",text:el.getAttribute("aria-label")||(el as HTMLInputElement).placeholder||el.tagName,threshold:3,ratio:Math.max(borderRatio,groupRatio,ratio(inner.color,outer.color)),border:style.borderTopColor,background:outer.color};
      const reason=inner.reason||outer.reason||((el as HTMLInputElement).type==="date"?"native date-picker glyph paint is not exposed by computed styles":"");
      if(reason)unmeasured.push({...row,reason});else rows.push(row);
      const label={kind:"input-text",text:row.text,threshold:4.5,ratio:ratio(over(rgba(style.color),inner.color),inner.color)};
      if(inner.reason)unmeasured.push({...label,reason:inner.reason});else rows.push(label);
      if(el.matches(":focus-visible")){
        const focus={kind:"focus",text:row.text,threshold:3,ratio:parseFloat(style.outlineWidth)>0?ratio(over(rgba(style.outlineColor),outer.color),outer.color):1};
        if(outer.reason)unmeasured.push({...focus,reason:outer.reason});else rows.push(focus);
      }
      if((el as HTMLInputElement).placeholder){const p=getComputedStyle(el,"::placeholder"),alpha=Number(p.opacity);const ink=rgba(p.color);ink[3]*=Number.isFinite(alpha)?alpha:1;const row={kind:"placeholder",text:(el as HTMLInputElement).placeholder,threshold:4.5,ratio:ratio(over(ink,inner.color),inner.color)};if(inner.reason)unmeasured.push({...row,reason:inner.reason});else rows.push(row);}
    }
    for(const el of document.querySelectorAll("button svg,a svg")){
      if(!visible(el)||el.closest("button:disabled"))continue;
      const bg=background(el),style=getComputedStyle(el),control=el.closest("button,a");
      const ink=style.stroke!=="none"?style.stroke:style.color;
      const row={kind:"icon",text:control?.getAttribute("aria-label")||control?.textContent?.trim()||"control icon",threshold:3,ratio:ratio(over(rgba(ink),bg.color),bg.color)};
      if(bg.reason)unmeasured.push({...row,reason:bg.reason});else rows.push(row);
    }
    return {theme:document.documentElement.dataset.theme,rows,unmeasured,failures:rows.filter(row=>row.ratio+0.001<row.threshold),viewport:{width:innerWidth,height:innerHeight},overflow:document.documentElement.scrollWidth>innerWidth};
  });
}
