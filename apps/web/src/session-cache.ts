/** Private display snapshots, never an action authority. */
import type { Data } from "./api";
let generation = 0;
let storageReadable = true;
const snapshots = new Map<string, { value: Data; size: number }>();
const MAX_BYTES = 8 * 1024 * 1024;
let bytes = 0;
const RELOAD_KEY = "leam-chat-display-v1";
const RELOAD_BYTES = 1024 * 1024;
const RELOAD_AGE = 10 * 60 * 1000;
let hydrated = false;
let persistTimer: ReturnType<typeof setTimeout> | null = null;
function hydrateCodingDisplay() {
  if (hydrated || !storageReadable) return;
  hydrated = true;
  try {
    const raw = sessionStorage.getItem(RELOAD_KEY);
    if (!raw || raw.length * 2 > RELOAD_BYTES) return;
    const saved = JSON.parse(raw);
    if (
      saved.version !== 1 ||
      !Number.isFinite(saved.savedAt) ||
      saved.savedAt > Date.now() ||
      Date.now() - saved.savedAt > RELOAD_AGE ||
      !Array.isArray(saved.entries) ||
      saved.entries.length > 4
    ) {
      sessionStorage.removeItem(RELOAD_KEY);
      return;
    }
    for (const entry of saved.entries) {
      if (!Array.isArray(entry) || entry.length !== 2) continue;
      const [key, value] = entry;
      if (
        typeof key !== "string" ||
        !key.startsWith("coding:") ||
        key.length > 300 ||
        !value ||
        typeof value !== "object" ||
        Array.isArray(value)
      )
        continue;
      if (key === "coding:threads") {
        if (
          !Array.isArray(value.threads) ||
          value.threads.length > 200 ||
          !value.threads.every((t: Data) => t && typeof t.id === "string")
        )
          continue;
      } else if (
        !Array.isArray(value.turns) ||
        value.turns.length > 200 ||
        !value.turns.every(
          (t: Data) => t && typeof t.id === "string" && Array.isArray(t.items),
        )
      )
        continue;
      // A reload always revalidates; a saved display cannot restore authority.
      const copy = { ...value, validatedAt: 0 };
      const size = JSON.stringify(copy).length * 2;
      snapshots.set(key, { value: copy, size });
      bytes += size;
    }
  } catch {
    /* A missing, corrupt or blocked cache must not block the app. */
  }
}
function persistCodingDisplay() {
  if (!storageReadable) return;
  try {
    const entries: [string, Data][] = [];
    let total = 128;
    for (const [key, entry] of [...snapshots].reverse()) {
      if (!key.startsWith("coding:") || entry.size > RELOAD_BYTES / 2) continue;
      const size = entry.size + JSON.stringify(key).length * 2 + 16;
      if (total + size <= RELOAD_BYTES) {
        entries.push([key, entry.value]);
        total += size;
      }
      if (entries.length === 4) break;
    }
    if (entries.length)
      sessionStorage.setItem(
        RELOAD_KEY,
        JSON.stringify({ version: 1, savedAt: Date.now(), entries }),
      );
    else sessionStorage.removeItem(RELOAD_KEY);
  } catch {
    /* Display continuity is optional when session storage is full. */
  }
}
function scheduleDisplayPersistence() {
  if (persistTimer !== null) return;
  persistTimer = setTimeout(() => {
    persistTimer = null;
    persistCodingDisplay();
  }, 1000);
}
window.addEventListener("pagehide", persistCodingDisplay);
export const sessionGeneration = () => generation;
export function cachedChat(key: string): Data | null {
  hydrateCodingDisplay();
  const entry = snapshots.get(key);
  if (!entry) return null;
  snapshots.delete(key);
  snapshots.set(key, entry);
  return entry.value;
}
export function rememberChat(key: string, value: Data) {
  hydrateCodingDisplay();
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
  if (key.startsWith("coding:")) scheduleDisplayPersistence();
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
  hydrated = true;
  if (persistTimer !== null) clearTimeout(persistTimer);
  persistTimer = null;
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
  if (key.startsWith("coding:")) persistCodingDisplay();
}

export function forgetChatPrefix(prefix: string) {
  for (const key of [...snapshots.keys()])
    if (key.startsWith(prefix)) forgetChat(key);
}
