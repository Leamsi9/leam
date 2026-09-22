import { LinkedResources } from "./resource-links";
import { Fragment, useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import { api, type Data } from "./api";
import { ItemChat } from "./item-chat";

type Props = { fail: (error: unknown) => void };
export type EditorDraft = { form: Data; revision?: number };
const empty = {
  title: "",
  kind: "task",
  measure: "boolean",
  target: 1,
  notes: "",
  status: "active",
  capacityId: null,
  startDate: null,
  endDate: null,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/London",
  reminderTime: null,
  reward: "",
};
export function CommitmentCard({
  item,
  capacity,
  changed,
  edit,
  fail,
  compact = false,
}: {
  compact?: boolean;
  item: Data;
  capacity?: Data;
  changed: () => Promise<void>;
  edit: () => void;
} & Props) {
  const [value, setValue] = useState(String(item.log.value)),
    [busy, setBusy] = useState(false),
    [history, setHistory] = useState<Data[] | null>(null);
  useEffect(() => {
    setValue(String(item.log.value));
  }, [item.log.revision]);
  const Details = compact ? "details" : Fragment;
  const done =
    item.kind === "task" ? item.status === "completed" : item.log.done;
  async function progress(operation: string) {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/commitments/${item.id}/progress/${item.date}`, "PUT", {
        operation,
        revision: item.log.revision,
        commitmentRevision: item.revision,
        ...(operation === "set" ? { value: Number(value) } : {}),
      });
      setHistory(null);
      await changed();
    } catch (e) {
      fail(e);
      await changed();
    } finally {
      setBusy(false);
    }
  }
  return (
    <article
      className={
        "card commitment-card " +
        (compact ? "today-commitment-card " : "") +
        (done ? "done" : "")
      }
    >
      <div className="commitment-heading">
        <button
          className="check-button"
          disabled={busy}
          aria-label={(done ? "Reopen " : "Complete ") + item.title}
          onClick={() => progress("toggle")}
        >
          {done && <Check size={18} />}
        </button>
        <div>
          <h3>{item.title}</h3>
          <small>
            {capacity?.name || "Personal"}
            {!compact && (
              <>
                {" "}
                · {item.kind} · {item.date}
              </>
            )}
          </small>
        </div>
        {!compact && (
          <button className="secondary" onClick={edit} disabled={busy}>
            Edit
          </button>
        )}
      </div>
      {item.measure !== "boolean" && (
        <form
          className="measurement"
          onSubmit={(e) => {
            e.preventDefault();
            progress("set");
          }}
        >
          <input
            type="number"
            min="0"
            max="1000000"
            step="any"
            aria-label={"Progress for " + item.title}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            disabled={busy}
            required
          />
          <span>
            / {item.target} {item.measure}
          </span>
          <button className="secondary" disabled={busy}>
            Save progress
          </button>
        </form>
      )}
      <Details
        {...(compact ? { className: "today-row-details" } : {})}
      >
        {compact && <summary>Commitment details</summary>}
        {compact && (
          <button className="secondary" onClick={edit} disabled={busy}>
            Edit
          </button>
        )}
        {item.notes && <p>{item.notes}</p>}
        {item.reward && (
          <p className="reward">Something to look forward to: {item.reward}</p>
        )}
        {item.reminderTime && (
          <small>
            In-app reminder {item.reminderTime} · {item.timezone}
          </small>
        )}
        <details
          onToggle={async (e) => {
            if (e.currentTarget.open && !history) {
              try {
                setHistory(
                  (await api("/commitments/" + item.id + "/history")).items,
                );
              } catch (e) {
                fail(e);
              }
            }
          }}
        >
          <summary>Progress history</summary>
          {history === null ? (
            <p>Loading…</p>
          ) : history.length ? (
            history.map((l) => (
              <p key={l.date}>
                {l.date} · {l.value} {l.measure || item.measure} ·{" "}
                {l.done ? "Done" : "In progress"}
              </p>
            ))
          ) : (
            <p>No progress recorded yet.</p>
          )}
        </details>
        <LinkedResources targetType="commitment" targetId={item.id} />
        <ItemChat
          kind="commitment"
          id={item.id}
          title={item.title}
          changed={changed}
        />
      </Details>
    </article>
  );
}

export function CommitmentForm({
  initial,
  draft,
  preserve,
  capacities,
  close,
  changed,
  fail,
  remove,
}: {
  initial: Data | null;
  draft?: EditorDraft;
  preserve: (draft: EditorDraft) => void;
  capacities: Data[];
  close: (saved?: boolean) => void;
  changed: () => Promise<void>;
  remove: (id: string) => void;
} & Props) {
  const [form, setForm] = useState<Data>(
      () =>
        draft?.form ||
        Object.fromEntries(
          Object.keys(empty).map((k) => [
            k,
            initial?.[k] ?? (empty as Data)[k],
          ]),
        ),
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const baseRevision = useRef(draft ? draft.revision : initial?.revision);
  const dialog = useRef<HTMLDialogElement>(null);
  const saving = useRef(false);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  const set = (key: string, value: any) => {
    const next = { ...form, [key]: value };
    preserve({ form: next, revision: baseRevision.current });
    setForm(next);
  };
  return (
    <dialog
      ref={dialog}
      className="editor-dialog card"
      aria-label="Commitment editor"
      onFocusCapture={(event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement) || target === event.currentTarget) return;
        const panel = event.currentTarget.getBoundingClientRect();
        const field = target.getBoundingClientRect();
        const header = event.currentTarget.querySelector("header");
        const top = header && !header.contains(target)
          ? header.getBoundingClientRect().bottom : panel.top;
        // Browsers can reveal a textarea's caret while leaving its control clipped.
        if (field.top < top + 8 || field.bottom > panel.bottom - 12)
          target.scrollIntoView({ block: "center", inline: "nearest" });
      }}
      style={{
        margin: "auto",
        width: "min(650px, calc(100vw - 32px))",
        border: "1px solid var(--line)",
      }}
      onCancel={(event) => {
        event.preventDefault();
        if (!saving.current) close();
      }}
    >
      <header
        className="actions"
        style={{
          position: "sticky",
          top: -1,
          background: "var(--paper)",
          zIndex: 1,
        }}
      >
        <h2 style={{ flex: 1 }}>
          {initial ? "Edit commitment" : "Plan a commitment"}
        </h2>
        <button
          type="button"
          className="secondary"
          aria-label="Close commitment editor"
          disabled={busy}
          style={{ minWidth: 44, minHeight: 44 }}
          onClick={() => close()}
        >
          Close
        </button>
      </header>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (saving.current) return;
          saving.current = true;
          setBusy(true);
          try {
            await api(
              initial ? "/commitments/" + initial.id : "/commitments",
              initial ? "PATCH" : "POST",
              {
                ...form,
                ...(initial ? { revision: baseRevision.current } : {}),
              },
            );
            await changed();
            close(true);
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
            fail(e);
          } finally {
            saving.current = false;
            setBusy(false);
          }
        }}
      >
        {error && (
          <div role="alert" className="error">
            <p>{error}</p>
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                await changed();
                close(true);
              }}
            >
              Discard this draft and reload
            </button>
          </div>
        )}
        <fieldset disabled={busy} className="form-grid">
          <label>
            Commitment title
            <input
              required
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
            />
          </label>
          <label>
            Kind
            <select
              aria-label="Kind"
              value={form.kind}
              onChange={(e) => set("kind", e.target.value)}
            >
              <option value="task">Task</option>
              <option value="habit">Habit</option>
              <option value="goal">Goal</option>
            </select>
          </label>
          <label>
            Measure
            <select
              aria-label="Measure"
              value={form.measure}
              onChange={(e) => set("measure", e.target.value)}
            >
              <option value="boolean">Done / not done</option>
              <option value="count">Count</option>
              <option value="minutes">Minutes</option>
            </select>
          </label>
          {form.measure !== "boolean" && (
            <label>
              Daily target
              <input
                type="number"
                min="0"
                max="1000000"
                step="any"
                value={form.target}
                onChange={(e) => set("target", Number(e.target.value))}
              />
            </label>
          )}
          <label>
            Capacity
            <select
              value={form.capacityId || ""}
              onChange={(e) => set("capacityId", e.target.value || null)}
            >
              <option value="">Personal</option>
              {capacities.map((c) => (
                <option value={c.id} key={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Status
            <select
              aria-label="Status"
              value={form.status}
              onChange={(e) => set("status", e.target.value)}
            >
              <option value="active">Active</option>
              <option value="paused">Paused</option>
              <option value="completed">Completed</option>
            </select>
          </label>
          {initial?.statusChangedAt && (
            <p className="muted">Status changed {new Date(initial.statusChangedAt * 1000).toLocaleString()}</p>
          )}
          {initial?.completedAt && (
            <p className="muted">Completed {new Date(initial.completedAt * 1000).toLocaleString()}</p>
          )}
          <label>
            Start date
            <input
              type="date"
              value={form.startDate || ""}
              onChange={(e) => set("startDate", e.target.value || null)}
            />
          </label>
          <label>
            End date
            <input
              type="date"
              value={form.endDate || ""}
              onChange={(e) => set("endDate", e.target.value || null)}
            />
          </label>
          <label>
            Reminder time
            <input
              type="time"
              value={form.reminderTime || ""}
              onChange={(e) => set("reminderTime", e.target.value || null)}
            />
          </label>
          <label>
            Timezone
            <input
              required
              value={form.timezone}
              onChange={(e) => set("timezone", e.target.value)}
            />
          </label>
          <label>
            Reward
            <input
              value={form.reward}
              onChange={(e) => set("reward", e.target.value)}
            />
          </label>
          <label>
            Notes
            <textarea
              aria-label="Commitment notes"
              value={form.notes}
              onChange={(e) => set("notes", e.target.value)}
            />
          </label>
          <div className="actions">
            <button className="primary">Save commitment</button>
            <button type="button" className="secondary" onClick={() => close()}>
              Cancel
            </button>
            {initial && (
              <button
                type="button"
                className="secondary"
                onClick={() => remove(initial.id)}
              >
                Remove commitment…
              </button>
            )}
          </div>
        </fieldset>
      </form>
    </dialog>
  );
}

export function Capacities({
  items,
  changed,
  fail,
  remove,
}: {
  items: Data[];
  changed: () => Promise<void>;
  remove: (id: string) => void;
} & Props) {
  const [form, setForm] = useState<Data>({ name: "", note: "", record: "" }),
    [editing, setEditing] = useState<Data | null>(null),
    [busy, setBusy] = useState(false);
  return (
    <fieldset className="card settings-form" disabled={busy}>
      <h3>Capacities</h3>
      <p>Areas of life you want to give attention to.</p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await api(
              editing ? "/capacities/" + editing.id : "/capacities",
              editing ? "PATCH" : "POST",
              { ...form, ...(editing ? { revision: editing.revision } : {}) },
            );
            setForm({ name: "", note: "", record: "" });
            setEditing(null);
            await changed();
          } catch (e) {
            fail(e);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          Capacity name
          <input
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </label>
        <label>
          Capacity note
          <input
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </label>
        <label>
          Personal record
          <input
            value={form.record}
            onChange={(e) => setForm({ ...form, record: e.target.value })}
          />
        </label>
        <button className="secondary">Save capacity</button>
      </form>
      {items.map((c) => (
        <div key={c.id}>
          <div className="manage-row">
            <span>
              {c.name}
              <small>{c.record}</small>
            </span>
            <button
              className="secondary"
              onClick={() => {
                setEditing(c);
                setForm({ name: c.name, note: c.note, record: c.record });
              }}
            >
              Edit {c.name}
            </button>
            <button className="secondary" onClick={() => remove(c.id)}>
              Remove {c.name}
            </button>
          </div>
          <ItemChat
            kind="capacity"
            id={c.id}
            title={c.name}
            changed={changed}
          />
        </div>
      ))}
    </fieldset>
  );
}
