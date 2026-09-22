import { useSyncExternalStore } from "react";
import { api } from "./api";

type Status = {
  total: number; unreadCount: number; throughSequence: number;
  automationDelivery?: { queued: number; retrying: number; failed: number; paused: boolean;
    failures: Array<{ eventId: number; attempts: number; nextTry: number; error: string }> };
};
let snapshot: Status | null = null;
let generation = 0;
let controller: AbortController | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());
export function inboxChanged() {
  window.dispatchEvent(new Event("leam:inbox-changed"));
}
async function refresh() {
  clearTimeout(timer);
  if (document.hidden || !listeners.size) return;
  const version = ++generation;
  controller?.abort();
  controller = new AbortController();
  try {
    const value = await api(
      "/inbox/status",
      "GET",
      undefined,
      controller.signal,
    );
    if (
      version === generation &&
      listeners.size &&
      Number.isInteger(value.unreadCount)
    ) {
      snapshot = value as Status;
      emit();
    }
  } catch {
    /* The Inbox page exposes errors; badges confer no execution authority. */
  } finally {
    if (version === generation && listeners.size && !document.hidden)
      timer = setTimeout(() => void refresh(), 30000);
  }
}
function activity() {
  clearTimeout(timer);
  ++generation;
  controller?.abort();
  if (!document.hidden) void refresh();
}
function revoke() {
  ++generation;
  controller?.abort();
  clearTimeout(timer);
  snapshot = null;
  emit();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    document.addEventListener("visibilitychange", activity);
    window.addEventListener("leam:inbox-changed", activity);
    window.addEventListener("leam:auth-lost", revoke);
    void refresh();
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      revoke();
      document.removeEventListener("visibilitychange", activity);
      window.removeEventListener("leam:inbox-changed", activity);
      window.removeEventListener("leam:auth-lost", revoke);
    }
  };
}
export function useInboxStatus() {
  return useSyncExternalStore(subscribe, () => snapshot);
}
export function InboxBadge({ count }: { count: number }) {
  return count > 0 ? (
    <span
      className="inbox-unread-dot"
      role="status"
      aria-label={`${count} unread Leam notes`}
      title={`${count} unread Leam notes`}
    />
  ) : null;
}
