// The production build replaces these values with its complete public shell.
const BUILD = "__LEAM_SHELL_BUILD__";
const SHELL = ["/", "/icon.svg", "/manifest.webmanifest"]; // __LEAM_SHELL_ASSETS__
const INTEGRITIES = {}; // __LEAM_SHELL_INTEGRITIES__
const CACHE = "leam-shell-" + BUILD;
const immutable = (path) => /^\/assets\/[^/]+\.(?:js|css)$/.test(path);
self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // Fetch the entire build together. Failed installs never replace the active worker.
      try {
        await cache.addAll(
          SHELL.map(
            (url) =>
              new Request(url, {
                cache: "reload",
                credentials: "omit",
                integrity: INTEGRITIES[url] || "",
              }),
          ),
        );
      } catch (error) {
        await caches.delete(CACHE);
        throw error;
      }
    })(),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = (await caches.keys()).filter((key) =>
        key.startsWith("leam-shell-"),
      );
      const previous = keys.filter((key) => key !== CACHE).slice(-1);
      await Promise.all(
        keys
          .filter((key) => key !== CACHE && !previous.includes(key))
          .map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});
self.addEventListener("message", (event) => {
  if (event.data?.type === "LEAM_ACTIVATE_UPDATE")
    event.waitUntil(self.skipWaiting());
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (
    url.origin !== self.location.origin ||
    event.request.method !== "GET" ||
    url.pathname.startsWith("/api/")
  )
    return;
  if (event.request.mode === "navigate") {
    event.respondWith(
      (async () => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4000);
        try {
          const response = await fetch(event.request, {
            signal: controller.signal,
            cache: "no-cache",
          });
          if (response.ok) return response;
          return (await (await caches.open(CACHE)).match("/")) || response;
        } catch {
          return (
            (await (await caches.open(CACHE)).match("/")) ||
            new Response("Reconnect to load Leam.", { status: 503 })
          );
        } finally {
          clearTimeout(timer);
        }
      })(),
    );
    return;
  }
  if (
    immutable(url.pathname) ||
    (SHELL.includes(url.pathname) && url.pathname !== "/")
  ) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE);
        const cached = await cache.match(event.request);
        if (cached) return cached;
        // Unlisted assets belong to another release. Fetch them, but never mix them
        // into this build's offline fallback; the next worker precaches its own set.
        return fetch(event.request);
      })(),
    );
  }
});

// Push payloads are encrypted in transit. Always display a visible notification.
self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {}
  event.waitUntil(
    self.registration.showNotification("Leam", {
      body:
        typeof payload.body === "string"
          ? payload.body
          : "You have a reminder. Open Leam to review it.",
      icon: "/leam-icon-192.png",
      badge: "/favicon-32.png",
      tag: typeof payload.tag === "string" ? payload.tag : "leam-reminder",
      data: { url: "/?view=today" },
    }),
  );
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    (async () => {
      const url = new URL("/?view=today", self.location.origin).href;
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      const existing = windows.find(
        (client) => new URL(client.url).origin === self.location.origin,
      );
      if (existing) {
        await existing.navigate(url);
        return existing.focus();
      }
      return self.clients.openWindow(url);
    })(),
  );
});
