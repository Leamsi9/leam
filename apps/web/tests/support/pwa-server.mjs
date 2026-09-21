import http from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname } from "node:path";
const root = resolve("../../.artifacts/pwa-test"),
  counts = { assets: 0, navigations: 0 };
let generation = 0,
  corrupt = false;
const mime = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".html": "text/html",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".webmanifest": "application/manifest+json",
};
http
  .createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    res.setHeader("Cache-Control", "no-store");
    if (url.pathname === "/test/counts") {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(counts));
      return;
    }
    if (url.pathname === "/test/upgrade") {
      generation++;
      res.end("ok");
      return;
    }
    if (url.pathname === "/test/corrupt") {
      corrupt = url.searchParams.get("enabled") === "true";
      res.end("ok");
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      res.setHeader("Content-Type", "application/json");
      res.end(
        JSON.stringify(
          url.pathname === "/api/auth/status"
            ? { authenticated: true, configured: true }
            : url.pathname === "/api/private-fixture"
              ? { secret: "synthetic-private" }
              : { items: [], data: [] },
        ),
      );
      return;
    }
    const path = resolve(
      root,
      "." + (url.pathname === "/" ? "/index.html" : url.pathname),
    );
    if (!path.startsWith(root + "/")) {
      res.statusCode = 403;
      res.end();
      return;
    }
    try {
      let data = await readFile(path);
      if (url.pathname.startsWith("/assets/")) counts.assets++;
      if (req.headers["sec-fetch-mode"] === "navigate") counts.navigations++;
      if (url.pathname === "/sw.js")
        data = Buffer.from(
          data
            .toString()
            .replace(
              /const BUILD = "([^"]+)";/,
              (_, id) => `const BUILD = "${id}-fixture-${generation}";`,
            ),
        );
      if (corrupt && url.pathname === "/manifest.webmanifest")
        data = Buffer.from('{"name":"corrupt fixture"}');
      res.setHeader(
        "Content-Type",
        mime[extname(path)] || "application/octet-stream",
      );
      res.end(data);
    } catch {
      res.statusCode = 404;
      res.end("Missing fixture asset");
    }
  })
  .listen(46559, "127.0.0.1");
