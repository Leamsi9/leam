import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Companion } from "./companion";
import { TodayReconciliation } from "./today-reconciliation";

/** The shell retains bindings, never a hidden microphone or live conversation. */
export function AgendaChat({
  date,
  timezone,
  active,
  checksActive = false,
  changed,
  canonicalChanged,
}: {
  date: string;
  timezone: string;
  active: boolean;
  checksActive?: boolean;
  changed: () => void;
  canonicalChanged?: () => void;
}) {
  const bindings = useRef(new Map<string, Promise<string | null>>());
  const key = `${date}:${timezone}`;
  const [binding, setBinding] = useState<{
    key: string;
    threadId: string;
  } | null>(null);
  const thread = binding?.key === key ? binding.threadId : "";
  const currentThread = useRef(thread);
  currentThread.current = thread;
  const [checkVersion, setCheckVersion] = useState(0);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!active && !checksActive) return;
    let alive = true;
    setError("");
    let request = bindings.current.get(key);
    if (!request) {
      request = (
        active
          ? api("/agenda/chat", "POST", { date, timezone })
          : api(
              `/agenda/chat?date=${date}&timezone=${encodeURIComponent(timezone)}`,
            )
      ).then((result) => result.threadId as string | null);
      // Plan reads existing bindings only; a missing day must not reserve a chat.
      if (active) bindings.current.set(key, request);
      // Day bindings contain no transcript. Bound this shell's in-memory cache.
      if (bindings.current.size > 16)
        bindings.current.delete(bindings.current.keys().next().value!);
    }
    void request
      .then((threadId) => {
        if (alive) setBinding({ key, threadId: threadId || "" });
      })
      .catch((e) => {
        bindings.current.delete(key);
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [date, timezone, key, active, checksActive, attempt]);
  if (checksActive)
    return (
      <section aria-label="Today check administration">
        {error ? (
          <p role="alert">
            Today check status is unavailable.{" "}
            <button
              type="button"
              className="secondary"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Retry loading checks
            </button>
          </p>
        ) : thread ? (
          <TodayReconciliation
            key={thread}
            threadId={thread}
            refreshVersion={checkVersion}
            onChanged={canonicalChanged || changed}
          />
        ) : (
          <small>
            {binding?.key === key
              ? "No day conversation to check yet."
              : "Loading day checks…"}
          </small>
        )}
      </section>
    );
  if (!active) return null;
  return (
    <section className="today-chat-page" aria-label={`Chat about ${date}`}>
      {error && (
        <p role="alert">
          {error}
          {!thread && (
            <button onClick={() => setAttempt((value) => value + 1)}>
              Retry opening chat
            </button>
          )}
        </p>
      )}
      {thread ? (
        <Companion
          key={thread}
          fixedThread={thread}
          procedures
          onChanged={() => {
            changed();
            setCheckVersion((value) => value + 1);
          }}
          onExchangeActivity={(id) => {
            if (currentThread.current === id)
              setCheckVersion((value) => value + 1);
          }}
          fail={(e) => setError(e instanceof Error ? e.message : String(e))}
        />
      ) : (
        !error && <p role="status">Opening your day chat…</p>
      )}
    </section>
  );
}
