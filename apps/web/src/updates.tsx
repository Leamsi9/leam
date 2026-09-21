import { useEffect, useState } from "react";
import { api, type Data } from "./api";
import { TicketChat } from "./ticket-chat";

type Props = { fail: (error: unknown) => void };
const changed = () => window.dispatchEvent(new Event("leam-updates-changed"));

/** Mount only inside the authenticated application shell. */
export function useUpdatesUnread() {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let stopped = false;
    async function refresh() {
      try {
        const result = await api("/updates/status");
        if (!stopped) setCount(result.unreadCount);
      } catch {
        /* Main view reports errors; an unread badge must not toast repeatedly. */
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 15000);
    window.addEventListener("leam-updates-changed", refresh);
    return () => {
      stopped = true;
      clearInterval(timer);
      window.removeEventListener("leam-updates-changed", refresh);
    };
  }, []);
  return count;
}

export function UpdatesBadge() {
  const count = useUpdatesUnread();
  return count > 0 ? (
    <span
      role="status"
      aria-label={`${count} unread updates`}
      title={`${count} unread updates`}
      style={{
        display: "inline-block",
        width: 9,
        height: 9,
        borderRadius: "50%",
        background: "#f59e0b",
        marginInlineStart: 6,
      }}
    />
  ) : null;
}

function ReviewCard({
  item,
  busy,
  submit,
  markRead,
}: {
  item: Data;
  busy: boolean;
  submit: (state: string, details: string) => void;
  markRead: () => void;
}) {
  const [details, setDetails] = useState(item.uat.details || "");
  return (
    <article
      className="card"
      aria-label={item.title}
      data-unread={item.unread ? "true" : "false"}
      style={item.unread ? { border: "2px solid #f59e0b" } : undefined}
    >
      <h3>{item.title}</h3>
      {item.unread && (
        <div className="actions">
          <strong aria-label="Unread update" style={{ color: "#f59e0b" }}>
            Unread
          </strong>
          <button className="secondary" disabled={busy} onClick={markRead}>
            Mark as read
          </button>
        </div>
      )}
      <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
        {item.summary}
      </p>
      <ol
        aria-label="Deployment stages"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 8,
          listStyle: "none",
          padding: 0,
        }}
      >
        {["Deployed", "QA", "UAT", "Complete", "Fail"].map((stage) => (
          <li
            key={stage}
            aria-current={
              !item.superseded && item.stage === stage ? "step" : undefined
            }
            style={{
              padding: "6px 10px",
              borderRadius: 6,
              border: "1px solid currentColor",
              fontWeight: item.stage === stage ? 700 : 400,
              background:
                !item.superseded && item.stage === stage
                  ? stage === "Fail" ||
                    (stage === "QA" && item.qa.state === "failed")
                    ? "#7f1d1d"
                    : "#1e3a5f"
                  : "transparent",
              color:
                !item.superseded && item.stage === stage ? "white" : "inherit",
            }}
          >
            {stage}
          </li>
        ))}
      </ol>
      <p>
        <strong>
          {item.superseded
            ? "Earlier deployment"
            : item.completed
              ? "Complete · in production"
              : item.awaitingUserInput
                ? "Awaiting user input"
                : item.stage === "QA"
                  ? "QA failed — see summary below"
                  : item.stage === "UAT"
                    ? "Ready for your UAT"
                    : "Deployed · QA pending"}
        </strong>
      </p>
      {item.awaitingUserInput && (
        <p>
          Please share failure details here, in Coding, or in the ongoing chat.
        </p>
      )}
      <small>Deployed {new Date(item.deployedAt).toLocaleString()}</small>
      <details>
        <summary>Deployment identity</summary>
        <code style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
          {item.deploymentId}
        </code>
      </details>
      <details open={item.qa.state === "failed"}>
        <summary>QA: {item.qa.state}</summary>
        <strong>QA: {item.qa.state}</strong>
        {item.qa.details && (
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {item.qa.details}
          </p>
        )}
        {item.qa.updatedAt && (
          <small>
            Updated {new Date(item.qa.updatedAt * 1000).toLocaleString()}
          </small>
        )}
      </details>
      <div>
        <strong>Your UAT: {item.uat.state}</strong>
        {item.uat.details && (
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {item.uat.details}
          </p>
        )}
        {item.uat.updatedAt && (
          <small>
            Updated {new Date(item.uat.updatedAt * 1000).toLocaleString()}
          </small>
        )}
        {!item.superseded && (
          <>
            <details>
              <summary>Add test notes</summary>
              <label>
                Notes (optional)
                <textarea
                  maxLength={10000}
                  value={details}
                  disabled={busy}
                  onChange={(event) => setDetails(event.target.value)}
                />
              </label>
            </details>
            <div className="actions">
              <button
                className="primary"
                disabled={busy || item.qa.state !== "passed"}
                onClick={() => submit("passed", details)}
              >
                I tested this — pass UAT
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => submit("failed", details)}
              >
                Report UAT failure
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => submit("pending", details)}
              >
                Mark UAT pending
              </button>
            </div>
          </>
        )}
      </div>
      <TicketChat ticket={item} />
    </article>
  );
}

export function UpdatesPanel({ fail }: Props) {
  const [items, setItems] = useState<Data[]>([]);
  const [sequence, setSequence] = useState(0);
  const [unread, setUnread] = useState(0);
  const [cursor, setCursor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  async function load() {
    const result = await api("/updates");
    setItems(result.items);
    setSequence(result.sequence);
    setUnread(result.unreadCount);
    setCursor(result.nextCursor);
    changed();
  }
  useEffect(() => {
    void load().catch(fail);
  }, []);
  async function accept(item: Data, state: string, details: string) {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/updates/${item.id}/uat`, "POST", {
        revision: item.revision,
        deploymentId: item.deploymentId,
        state,
        details,
      });
      await load();
    } catch (error) {
      fail(error);
      await load().catch(fail);
    } finally {
      setBusy(false);
    }
  }
  async function markRead(item: Data) {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/updates/${item.id}/seen`, "POST", {
        sequence: item.sequence,
      });
      await load();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="Updates and acceptance">
      <h2>Updates</h2>
      <p>
        New features arrive here when deployed. QA records engineering checks;
        UAT records your own test. A feature is completed only when both pass
        for this deployment.
      </p>
      <p>
        Report failures here, in Coding, or in the ongoing chat. Only feedback
        saved here changes this acceptance record.
      </p>
      <div className="actions">
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void load().catch(fail)}
        >
          Refresh updates
        </button>
        <button
          className="secondary"
          disabled={busy || !unread}
          onClick={async () => {
            setBusy(true);
            try {
              await api("/updates/seen", "POST", { sequence });
              await load();
            } catch (error) {
              fail(error);
            } finally {
              setBusy(false);
            }
          }}
        >
          Mark all read{unread ? ` (${unread})` : ""}
        </button>
      </div>
      {!items.length && <p>No deployment updates have been published yet.</p>}
      {items
        .filter((item) => !item.superseded && item.stage !== "Complete")
        .map((item) => (
          <ReviewCard
            key={`${item.id}:${item.revision}`}
            item={item}
            busy={busy}
            submit={(state, details) => void accept(item, state, details)}
            markRead={() => void markRead(item)}
          />
        ))}
      <section aria-label="Changelog">
        <h2>Changelog</h2>
        <p>Completed work that passed QA and your UAT.</p>
        {!items.some(
          (item) => !item.superseded && item.stage === "Complete",
        ) && <p>No completed deployments yet.</p>}
        {items
          .filter((item) => !item.superseded && item.stage === "Complete")
          .map((item) => (
            <ReviewCard
              key={`${item.id}:${item.revision}`}
              item={item}
              busy={busy}
              submit={(state, details) => void accept(item, state, details)}
              markRead={() => void markRead(item)}
            />
          ))}
      </section>
      {cursor && (
        <button
          className="secondary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const result = await api(`/updates?before=${cursor}`);
              setItems((previous) => {
                const ids = new Set(previous.map((item) => item.id));
                return [
                  ...previous,
                  ...result.items.filter((item: Data) => !ids.has(item.id)),
                ];
              });
              setCursor(result.nextCursor);
            } catch (error) {
              fail(error);
            } finally {
              setBusy(false);
            }
          }}
        >
          Older updates
        </button>
      )}
    </section>
  );
}
