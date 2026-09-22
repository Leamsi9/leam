import { useSyncExternalStore } from "react";
import { api } from "./api";

type ReadStatus = { total: number; unreadCount: number; unreadIds: string[] };
let snapshot: ReadStatus | null = null;
const listeners = new Set<() => void>();
let controller: AbortController | null = null;
let timer: ReturnType<typeof setInterval> | undefined;
let generation = 0;
const emit = () => listeners.forEach((listener) => listener());
export async function refreshResourceUnread() {
  const version = ++generation;
  controller?.abort();
  controller = new AbortController();
  try {
    const value = await api(
      "/artifacts/_status",
      "GET",
      undefined,
      controller.signal,
    );
    if (
      version === generation &&
      listeners.size &&
      Array.isArray(value.unreadIds) &&
      Number.isInteger(value.unreadCount)
    ) {
      snapshot = value as ReadStatus;
      emit();
    }
  } catch {
    /* Badge failures stay quiet; list/detail controls expose mutation errors. */
  }
}
function activity() {
  clearInterval(timer);
  if (!document.hidden) {
    void refreshResourceUnread();
    timer = setInterval(() => void refreshResourceUnread(), 30000);
  } else {
    ++generation;
    controller?.abort();
  }
}
function revoke() {
  ++generation;
  controller?.abort();
  snapshot = null;
  emit();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    document.addEventListener("visibilitychange", activity);
    window.addEventListener("leam:auth-lost", revoke);
    activity();
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearInterval(timer);
      ++generation;
      controller?.abort();
      snapshot = null;
      document.removeEventListener("visibilitychange", activity);
      window.removeEventListener("leam:auth-lost", revoke);
    }
  };
}
export function useResourceUnread() {
  return useSyncExternalStore(subscribe, () => snapshot);
}
export async function markResourcesRead(ids: string[]) {
  const value = await api("/artifacts/_read", "POST", { ids: [...ids] });
  ++generation;
  controller?.abort();
  if (
    listeners.size &&
    Array.isArray(value.unreadIds) &&
    Number.isInteger(value.unreadCount)
  ) {
    snapshot = value as ReadStatus;
    emit();
  }
  await refreshResourceUnread();
}
export function ResourcesBadge({ count }: { count: number }) {
  return count > 0 ? (
    <span
      className="resources-unread-dot"
      role="status"
      aria-label={`${count} unread resources`}
      title={`${count} unread resources`}
    />
  ) : null;
}
