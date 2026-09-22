import { useEffect, useRef } from "react";

const topics = new Set([
  "commitment.completed", "commitment.status_changed", "commitment.activity",
  "commitment.progress", "commitment.removal", "commitment.subtask_status_changed", "commitment.activity_backfill",
]);

/** Events invalidate canonical reads; their payloads never become accomplishment data. */
export function useTodayActivityEvents(changed: () => void) {
  const latest = useRef(changed);
  latest.current = changed;
  useEffect(() => {
    let stopped = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    function invalidate() {
      if (timer !== undefined || stopped) return;
      timer = setTimeout(() => {
        timer = undefined;
        if (!stopped && !document.hidden) latest.current();
      }, 100);
    }
    function open() {
      if (stopped || document.hidden || source) return;
      const current = new EventSource("/api/events");
      source = current;
      // Fresh canonical read covers reconnect/history gaps without replaying transcripts.
      current.onopen = () => { if (source === current) invalidate(); };
      current.onmessage = (event) => {
        if (source !== current || event.data.length > 65536) return;
        try {
          if (topics.has(JSON.parse(event.data).topic)) invalidate();
        } catch { /* Malformed unrelated events are not state. */ }
      };
    }
    function visibility() {
      if (document.hidden) {
        source?.close();
        source = null;
        clearTimeout(timer);
        timer = undefined;
      } else open();
    }
    open();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      stopped = true;
      source?.close();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);
}
