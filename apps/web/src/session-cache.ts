/** Private display snapshots: bounded memory only, never an action authority. */
import type { Data } from "./api";
let generation = 0;
let storageReadable = true;
const snapshots = new Map<string, { value: Data; size: number }>();
const MAX_BYTES = 8 * 1024 * 1024;
let bytes = 0;
export const sessionGeneration = () => generation;
export function cachedChat(key: string): Data | null {
  const entry = snapshots.get(key);
  if (!entry) return null;
  snapshots.delete(key);
  snapshots.set(key, entry);
  return entry.value;
}
export function rememberChat(key: string, value: Data) {
  const copy = { ...value };
  // Keep pages and their cursor coherent; retain the previous bounded view if
  // an expanded transcript exceeds the cache limit instead of skipping pages.
  if (
    ["messages", "turns"].some(
      (field) => Array.isArray(copy[field]) && copy[field].length > 200,
    )
  )
    return;
  if (Array.isArray(copy.threads) && copy.threads.length > 200) return;
  const size = JSON.stringify(copy).length * 2;
  if (size > MAX_BYTES / 2) return;
  bytes -= snapshots.get(key)?.size || 0;
  snapshots.delete(key);
  snapshots.set(key, { value: copy, size });
  bytes += size;
  while (bytes > MAX_BYTES || snapshots.size > 12) {
    const oldest = snapshots.keys().next().value!;
    bytes -= snapshots.get(oldest)!.size;
    snapshots.delete(oldest);
  }
}
export function sessionValue<T>(key: string, fallback: T): T {
  if (!storageReadable) return fallback;
  try {
    return (
      JSON.parse(sessionStorage.getItem("leam-view:" + key) || "null") ??
      fallback
    );
  } catch {
    return fallback;
  }
}
export function rememberSession(key: string, value: unknown) {
  if (!storageReadable) return;
  try {
    sessionStorage.setItem("leam-view:" + key, JSON.stringify(value));
  } catch {
    /* Display continuity is optional if storage is unavailable. */
  }
}
export function clearPrivateSession() {
  generation++;
  snapshots.clear();
  bytes = 0;
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith("leam-")) sessionStorage.removeItem(key);
    }
    storageReadable = true;
  } catch {
    storageReadable = false;
    /* Revocation must still reach the UI when browser storage is blocked. */
  }
}
export function clearAcceptedDraft(kind: string, id: string, text: string) {
  const key = kind + ":draft:" + id;
  if (sessionValue<string | null>(key, null) === text) rememberSession(key, "");
}

export function forgetChat(key: string) {
  bytes -= snapshots.get(key)?.size || 0;
  snapshots.delete(key);
}

export function forgetChatPrefix(prefix: string) {
  for (const key of [...snapshots.keys()])
    if (key.startsWith(prefix)) forgetChat(key);
}
