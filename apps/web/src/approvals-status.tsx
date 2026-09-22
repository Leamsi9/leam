import { useEffect, useState } from "react";
import { api } from "./api";

export const approvalsChanged = () =>
  window.dispatchEvent(new Event("leam:approvals-changed"));
// Coalesce simultaneous desktop/mobile badge reads; no private durable cache.
const activeReads = new Map<
  string,
  Promise<{ unreadCount: number; pendingCount: number }>
>();
function read(threadId: string) {
  const path = `/proposals/status${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ""}`;
  if (!activeReads.has(path)) {
    const request = api(path)
      .then((value) => ({
        unreadCount: Number(value.unreadCount) || 0,
        pendingCount: Number(value.pendingCount) || 0,
      }))
      .finally(() => activeReads.delete(path));
    activeReads.set(path, request);
  }
  return activeReads.get(path)!;
}
export function useApprovalsStatus(threadId = "") {
  const [status, setStatus] = useState({ unreadCount: 0, pendingCount: 0 });
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pending = false;
    async function refresh() {
      if (pending || stopped || document.visibilityState !== "visible") return;
      clearTimeout(timer);
      pending = true;
      try {
        const result = await read(threadId);
        if (!stopped) setStatus(result);
      } catch {
        /* Badges cannot confer authority; the Approvals page reports failure. */
      } finally {
        pending = false;
        if (!stopped && document.visibilityState === "visible")
          timer = setTimeout(() => void refresh(), 15000);
      }
    }
    function visibility() {
      clearTimeout(timer);
      if (document.visibilityState === "visible") void refresh();
    }
    void refresh();
    window.addEventListener("leam:approvals-changed", refresh);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      stopped = true;
      clearTimeout(timer);
      window.removeEventListener("leam:approvals-changed", refresh);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [threadId]);
  return status;
}
export function ApprovalsBadge() {
  const { unreadCount } = useApprovalsStatus();
  return unreadCount > 0 ? (
    <span
      className="approvals-unread-dot"
      role="status"
      aria-label={`${unreadCount} unread approvals`}
      title={`${unreadCount} unread approvals`}
    />
  ) : null;
}
export function ApprovalsLink({ threadId }: { threadId: string }) {
  const { pendingCount } = useApprovalsStatus(threadId);
  return (
    <button
      type="button"
      className="secondary approvals-chat-link"
      onClick={() =>
        window.dispatchEvent(
          new CustomEvent("leam:navigate", { detail: "approvals" }),
        )
      }
    >
      Approvals{pendingCount > 0 ? ` · ${pendingCount} awaiting review` : ""}
    </button>
  );
}
