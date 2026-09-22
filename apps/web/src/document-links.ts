import { defaultUrlTransform } from "react-markdown";

/** Workspace links remain downloads; this never changes runtime path authority. */
export function documentLink(url: string, thread: string): string {
  if (!url.startsWith("/workspace/")) return defaultUrlTransform(url);
  let path: string;
  try {
    path = decodeURIComponent(url);
  } catch {
    return "";
  }
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      thread,
    ) ||
    path.length > 2048 ||
    /[\\\u0000-\u001f\u007f?#]/.test(path) ||
    path
      .split("/")
      .slice(1)
      .some((part) => !part || part === "." || part === "..")
  )
    return "";
  return `/api/companion/threads/${encodeURIComponent(thread)}/files/content?path=${encodeURIComponent(path)}`;
}
