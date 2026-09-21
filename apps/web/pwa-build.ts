import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { Plugin, ResolvedConfig } from "vite";

/** Stamp each deploy's worker with exactly the files emitted by that build. */
export function pwaShell(): Plugin {
  let config: ResolvedConfig;
  return {
    name: "leam-public-shell",
    apply: "build",
    configResolved(value) {
      config = value;
    },
    async writeBundle(_options, bundle) {
      const out = resolve(config.root, config.build.outDir);
      const template = await readFile(
        resolve(config.root, "public/sw.js"),
        "utf8",
      );
      const index = await readFile(resolve(out, "index.html"), "utf8");
      const files = [
        "/",
        "/icon.svg",
        "/icon-192.png",
        "/icon-512.png",
        "/manifest.webmanifest",
        ...Object.keys(bundle)
          .filter(
            (name) => name.startsWith("assets/") && /\.(js|css)$/.test(name),
          )
          .sort()
          .map((name) => "/" + name),
      ];
      const hash = createHash("sha256")
        .update(template)
        .update(index)
        .update(JSON.stringify(files));
      const integrities: Record<string, string> = {};
      for (const file of files) {
        const bytes = await readFile(
          resolve(out, file === "/" ? "index.html" : file.slice(1)),
        );
        hash.update(bytes);
        integrities[file] =
          "sha256-" + createHash("sha256").update(bytes).digest("base64");
      }
      const id = hash.digest("hex").slice(0, 24);
      const worker = template
        .replace('"__LEAM_SHELL_BUILD__"', JSON.stringify(id))
        .replace(
          /const SHELL = .*; \/\/ __LEAM_SHELL_ASSETS__/,
          "const SHELL = " + JSON.stringify(files) + ";",
        )
        .replace(
          /const INTEGRITIES = .*; \/\/ __LEAM_SHELL_INTEGRITIES__/,
          "const INTEGRITIES = " + JSON.stringify(integrities) + ";",
        );
      if (worker.includes("__LEAM_SHELL_"))
        throw new Error("PWA shell template was not fully stamped");
      await writeFile(resolve(out, "sw.js"), worker);
    },
  };
}
