import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import "./notification-popup.css";

type Notice = {tag:string;body:string};
/** Only authenticated app mounts this; never fetches or infers notification content. */
export function NotificationPopup() {
  const [notice,setNotice]=useState<Notice|null>(null);
  const seen=useRef(new Set<string>());
  useEffect(()=>{
    if (!("serviceWorker" in navigator)) return;
    function receive(event:MessageEvent) {
      if (document.visibilityState !== "visible") return;
      const source=event.source as ServiceWorker|null;
      if (!source || typeof source.scriptURL !== "string") return;
      const worker=new URL(source.scriptURL);
      if (worker.origin !== location.origin || worker.pathname !== "/sw.js") return;
      const data=event.data;
      if (data?.type !== "leam:push-notice" || typeof data.tag !== "string" || !data.tag || data.tag.length>256 || typeof data.body !== "string") return;
      if (seen.current.has(data.tag)) return;
      seen.current.add(data.tag);
      if(seen.current.size>64) seen.current.delete(seen.current.values().next().value!);
      setNotice({tag:data.tag,body:data.body.slice(0,500)});
    }
    navigator.serviceWorker.addEventListener("message",receive);
    return ()=>navigator.serviceWorker.removeEventListener("message",receive);
  },[]);
  if(!notice)return null;
  return <aside className="notification-popup" aria-label="Leam notification">
    <img src="/leam-icon-192.png" alt="" width="40" height="40"/>
    <div><p role="status"><strong>Leam</strong><span>{notice.body}</span></p><a href="/?view=today" onClick={()=>setNotice(null)}>Open Today</a></div>
    <button className="icon-button" type="button" aria-label="Dismiss notification popup" onClick={()=>setNotice(null)}><X size={20}/></button>
  </aside>;
}
