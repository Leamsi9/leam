import { useEffect, useState } from "react";
import { Heart, Pencil, Trash2 } from "lucide-react";
import { api, type Data } from "./api";
import { Companion } from "./companion";
import { ActivityRefresh, RecordedActivity } from "./overview-summary";
import "./wellbeing.css";
import "./contextual-chat.css";

const questions = [
  [
    "mood",
    "How are you feeling?",
    ["Very low", "Low", "Mixed", "Good", "Very good"],
  ],
  [
    "energy",
    "How much energy do you have?",
    ["Very little", "Low", "Some", "Plenty", "Lots"],
  ],
  [
    "stress",
    "How pressured do you feel?",
    ["Not at all", "A little", "Somewhat", "Quite", "Very"],
  ],
] as const;
const empty = () =>
  ({
    requestId: crypto.randomUUID(),
    mood: null,
    energy: null,
    stress: null,
    notes: "",
  }) as Data;
export function Wellbeing({
  day,
  timezone,
  activity,
  refresh,
  refreshing = false,
}: {
  day: string;
  timezone: string;
  activity?: Data;
  refresh?: () => void;
  refreshing?: boolean;
}) {
  const [draft, setDraft] = useState<Data>(empty);
  const [items, setItems] = useState<Data[]>([]);
  const [editing, setEditing] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [thread, setThread] = useState("");
  const [chatOpen, setChatOpen] = useState(false);
  const [partial, setPartial] = useState(false);
  const fail = (e: unknown) =>
    setError(e instanceof Error ? e.message : String(e));
  async function load() {
    const data = await api(`/wellbeing?day=${encodeURIComponent(day)}`);
    setItems(data.items);
    setPartial(data.partial);
  }
  useEffect(() => {
    let active = true;
    void api(`/wellbeing?day=${day}`)
      .then((data) => {
        if (active) {
          setItems(data.items);
          setPartial(data.partial);
        }
      })
      .catch((e) => {
        if (active) fail(e);
      });
    return () => {
      active = false;
    };
  }, [day]);
  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(
        editing ? `/wellbeing/${editing.id}` : "/wellbeing",
        editing ? "PATCH" : "POST",
        {
          ...draft,
          day,
          timezone,
          ...(editing ? { revision: editing.revision } : {}),
        },
      );
      setDraft(empty());
      setEditing(null);
      setNotice("Your check-in is saved.");
      await load();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function remove(row: Data) {
    if (
      !window.confirm(
        "Delete this check-in? Any text already shared in chat remains in that conversation.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await api(`/wellbeing/${row.id}`, "DELETE", { revision: row.revision });
      if (editing?.id === row.id) {
        setEditing(null);
        setDraft(empty());
      }
      await load();
      setNotice("Check-in deleted.");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function openChat() {
    setChatOpen(true);
    if (thread) return;
    try {
      const result = await api("/wellbeing/chat", "POST", { day });
      setThread(result.threadId);
    } catch (e) {
      fail(e);
      setChatOpen(false);
    }
  }
  return (
    <div className="wellbeing-page">
      <header>
        <h2>
          <Heart aria-hidden="true" size={24} /> A moment for you
        </h2>
        <p>
          A space for how your day feels to you. Share as much or as little as
          you like — every question is optional.
        </p>
      </header>
      {error && (
        <p role="alert">
          {error}{" "}
          <button
            className="secondary"
            onClick={() =>
              void load()
                .then(() => setError(""))
                .catch(fail)
            }
          >
            Reload check-ins
          </button>
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <details className="card wellbeing-checkin" open>
        <summary>
          {editing ? "Edit check-in" : "How are you today?"} · {day}
        </summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {questions.map(([key, title, labels]) => (
            <fieldset key={key} disabled={busy}>
              <legend>{title}</legend>
              <div className="wellbeing-options">
                <label>
                  <input
                    type="radio"
                    name={key}
                    checked={draft[key] === null}
                    onChange={() => setDraft({ ...draft, [key]: null })}
                  />
                  Skip
                </label>
                {labels.map((label, index) => (
                  <label key={label}>
                    <input
                      type="radio"
                      name={key}
                      checked={draft[key] === index + 1}
                      onChange={() => setDraft({ ...draft, [key]: index + 1 })}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
          <label>
            Anything you want to note?
            <textarea
              maxLength={2000}
              value={draft.notes}
              onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
              placeholder="What is taking up space, or helping?"
              disabled={busy}
            />
          </label>
          <div className="row">
            <button
              disabled={
                busy ||
                (!draft.notes.trim() &&
                  questions.every(([key]) => draft[key] === null))
              }
            >
              {busy ? "Saving…" : editing ? "Save changes" : "Save check-in"}
            </button>
            {editing && (
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setEditing(null);
                  setDraft(empty());
                }}
              >
                Cancel edit
              </button>
            )}
          </div>
        </form>
      </details>
      <details
        className="card contextual-chat"
        onToggle={(e) => {
          if (e.currentTarget.open) void openChat();
          else setChatOpen(false);
        }}
      >
        <summary>Talk it through</summary>
        {chatOpen && (
          <div className="contextual-chat-body">
            {thread ? (
              <Companion key={thread} fixedThread={thread} fail={fail} />
            ) : (
              <p role="status">Opening your wellbeing conversation…</p>
            )}
          </div>
        )}
      </details>
      <details className="card" open>
        <summary>
          Your check-ins · {items.length}
          {partial ? "+" : ""}
        </summary>
        {!items.length && (
          <p>No check-ins saved for this date. You can check in whenever you like.</p>
        )}
        {items.map((row) => (
          <article className="wellbeing-entry" key={row.id}>
            <time dateTime={new Date(row.createdAt * 1000).toISOString()}>
              {new Date(row.createdAt * 1000).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                timeZone: row.timezone,
              })}{" "}
              · {row.timezone}
            </time>
            <dl>
              {questions.map(
                ([key, title, labels]) =>
                  row[key] !== null && (
                    <div key={key}>
                      <dt>{title}</dt>
                      <dd>{labels[row[key] - 1]}</dd>
                    </div>
                  ),
              )}
            </dl>
            {row.notes && <p className="wellbeing-notes">{row.notes}</p>}
            <div className="row">
              <button
                className="secondary"
                disabled={busy}
                onClick={() => {
                  setEditing(row);
                  setDraft({
                    requestId: crypto.randomUUID(),
                    mood: row.mood,
                    energy: row.energy,
                    stress: row.stress,
                    notes: row.notes,
                  });
                }}
              >
                <Pencil size={16} /> Edit
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void remove(row)}
              >
                <Trash2 size={16} /> Delete
              </button>
            </div>
          </article>
        ))}
      </details>
      <details className="card">
        <summary>Activity is context, not a verdict</summary>
        {refresh && <ActivityRefresh refresh={refresh} refreshing={refreshing} label="Refresh my activity" />}
        <RecordedActivity activity={activity} day={day} userOnly />
        <p>
          Only activity assigned to you appears here. Your tasks and daily focus can help a conversation about your day.
          They do not tell Leam how you feel, and no wellbeing score is inferred
          from getting things done. Check-ins are shared with this day’s
          wellbeing chat when you send a message.
        </p>
      </details>
    </div>
  );
}
