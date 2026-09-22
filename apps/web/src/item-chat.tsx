import "./contextual-chat.css";
import { useEffect, useState } from "react";
import { api } from "./api";
import { Companion } from "./companion";

type Props = {
  kind: "commitment" | "capacity";
  id: string;
  title: string;
  changed: () => void;
};

/** Closed item panels own no network connection, microphone or model turn. */
export function ItemChat(props: Props) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="contextual-chat item-chat"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Chat about {props.title}</summary>
      {open && <ItemConversation key={props.kind + props.id} {...props} />}
    </details>
  );
}

function ItemConversation({ kind, id, title, changed }: Props) {
  const [thread, setThread] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const fail = (value: unknown) =>
    setError(value instanceof Error ? value.message : String(value));
  useEffect(() => {
    let alive = true;
    setError("");
    const endpoint = `/${kind === "capacity" ? "capacities" : "commitments"}/${encodeURIComponent(id)}/chat`;
    void api(endpoint, "POST", {})
      .then((result) => {
        if (alive) setThread(result.threadId);
      })
      .catch((error) => {
        if (alive) fail(error);
      });
    return () => {
      alive = false;
    };
  }, [kind, id, retry]);
  return (
    <div
      className="contextual-chat-body"
      role="region"
      aria-label={`Chat about ${title}`}
    >
      {error && (
        <p role="alert">
          {error}{" "}
          {!thread && (
            <button
              className="secondary"
              onClick={() => setRetry((value) => value + 1)}
            >
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
          fail={fail}
        />
      ) : (
        !error && <p role="status">Opening your chat…</p>
      )}
    </div>
  );
}
