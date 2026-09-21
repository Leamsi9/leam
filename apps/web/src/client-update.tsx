import { useEffect, useState } from "react";
function entry(document: Document) {
  return document
    .querySelector<HTMLScriptElement>('script[type="module"][src]')
    ?.getAttribute("src");
}
export function ClientUpdateNotice() {
  const [available, setAvailable] = useState(false),
    [reloading, setReloading] = useState(false);
  useEffect(() => {
    const current = entry(document);
    if (!current) return;
    let stopped = false,
      busy = false;
    const controller = new AbortController();
    async function check() {
      if (stopped || busy || document.hidden || !navigator.onLine) return;
      busy = true;
      try {
        const registration = await navigator.serviceWorker?.getRegistration();
        if (registration?.waiting && !stopped) setAvailable(true);
        void registration?.update().catch(() => {});
        const response = await fetch("/", {
          cache: "no-store",
          credentials: "omit",
          signal: controller.signal,
        });
        if (
          !response.ok ||
          !response.headers.get("content-type")?.includes("text/html")
        )
          return;
        const html = await response.text();
        if (html.length > 65536) return;
        const latest = entry(
          new DOMParser().parseFromString(html, "text/html"),
        );
        if (!stopped && latest && latest !== current) setAvailable(true);
      } catch {
        /* Restarts/offline never discard a draft or force a reload. */
      } finally {
        busy = false;
      }
    }
    const timer = setInterval(check, 30000);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    void check();
    return () => {
      stopped = true;
      controller.abort();
      clearInterval(timer);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);
  async function reload() {
    if (reloading) return;
    setReloading(true);
    try {
      const registration = await navigator.serviceWorker?.getRegistration();
      if (registration?.waiting) {
        await new Promise<void>((resolve) => {
          const done = () => {
            clearTimeout(timer);
            navigator.serviceWorker.removeEventListener(
              "controllerchange",
              done,
            );
            resolve();
          };
          const timer = setTimeout(done, 4000);
          navigator.serviceWorker.addEventListener("controllerchange", done);
          registration.waiting?.postMessage({ type: "LEAM_ACTIVATE_UPDATE" });
        });
      }
    } finally {
      location.reload();
    }
  }
  return available ? (
    <div className="notice" role="status">
      <strong>A new version of Leam is ready.</strong> Save or send any draft
      before reloading.
      <button
        type="button"
        className="secondary"
        disabled={reloading}
        onClick={() => void reload()}
      >
        {reloading ? "Reloading…" : "Reload Leam"}
      </button>
    </div>
  ) : null;
}
