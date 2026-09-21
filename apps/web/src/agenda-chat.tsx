import { useEffect, useRef, useState } from "react";
import { MessageCircle, X } from "lucide-react";
import { api } from "./api";
import { Companion } from "./companion";

/** Opening establishes the day binding, never starts a model turn. */
export function AgendaChat({
  date,
  timezone,
  changed,
}: {
  date: string;
  timezone: string;
  changed: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="secondary"
        onClick={() => {
          setOpen(true);
          dialog.current?.showModal();
        }}
      >
        <MessageCircle size={18} /> Chat about this day
      </button>
      <dialog
        className="agenda-chat-dialog"
        ref={dialog}
        onClose={() => setOpen(false)}
        aria-label={`Chat about ${date}`}
      >
        <header>
          <div>
            <h2>Plan your day with Leam</h2>
            <small>
              {date} · {timezone}
            </small>
          </div>
          <button
            className="icon-button"
            aria-label="Close day chat"
            onClick={() => dialog.current?.close()}
          >
            <X size={20} />
          </button>
        </header>
        {open && (
          <DayConversation date={date} timezone={timezone} changed={changed} />
        )}
      </dialog>
    </>
  );
}
function DayConversation({
  date,
  timezone,
  changed,
}: {
  date: string;
  timezone: string;
  changed: () => void;
}) {
  const [thread, setThread] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    setError("");
    void api("/agenda/chat", "POST", { date, timezone })
      .then((result) => {
        if (alive) setThread(result.threadId);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [date, timezone, attempt]);
  return (
    <>
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
          onChanged={changed}
          fail={(e) => setError(e instanceof Error ? e.message : String(e))}
        />
      ) : (
        !error && <p role="status">Opening your day chat…</p>
      )}
    </>
  );
}
