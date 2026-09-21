import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";
import { CodingHandoffCard } from "./coding-handoff";

const names: Record<string, string> = {
  "commitment.create": "new commitment",
  "commitment.edit": "commitment edit",
  "commitment.progress": "progress update",
  "capacity.create": "new capacity",
  "capacity.edit": "capacity edit",
  "memory.create": "new memory",
  "memory.edit": "memory correction",
  "memory.remove": "forget memory",
  "calendar.create": "calendar event",
  "calendar.edit": "event edit",
  "calendar.remove": "event removal",
};
const labels: Record<string, string> = {
  title: "Title",
  name: "Name",
  kind: "Kind",
  measure: "Measure",
  target: "Target",
  notes: "Notes",
  note: "Notes",
  record: "Record",
  status: "Status",
  capacityId: "Capacity",
  startDate: "First day",
  endDate: "Last day",
  timezone: "Timezone",
  reminderTime: "Reminder",
  reward: "Reward",
  text: "Memory",
  source: "Source",
  category: "Category",
  date: "Day",
  action: "Action",
  completed: "Completed",
  operation: "Progress action",
  value: "Amount",
};
function text(value: unknown) {
  return value === null || value === undefined ? "None" : String(value);
}
function Review({ item }: { item: Data }) {
  const r = item.review;
  if (item.operation === "calendar.create")
    return (
      <>
        <strong>{r.title}</strong>
        <p>
          {r.calendar.name} · {r.calendar.identity}
        </p>
        <p>
          {r.localStart} – {r.localEnd} ({r.schedule.timezone})
        </p>
        <p>No invitations will be sent.</p>
      </>
    );
  if (item.operation.startsWith("calendar."))
    return (
      <>
        <p>
          Current: {r.before.title} ·{" "}
          {new Date(r.before.start).toLocaleString()} –{" "}
          {new Date(r.before.end).toLocaleString()}
        </p>
        {r.after ? (
          <p>
            New: {r.after.title} · {r.after.localStart} – {r.after.localEnd} (
            {r.after.timezone})
          </p>
        ) : (
          <p>Remove this personal calendar event.</p>
        )}
        <p>No invitations will be sent.</p>
      </>
    );
  const after = r.after || {},
    before = r.before;
  if (item.operation === "memory.remove")
    return (
      <>
        <p>{before?.text}</p>
        <small>{before?.source}</small>
        <p>Remove this memory from Leam.</p>
      </>
    );
  if (item.operation === "commitment.progress")
    return (
      <>
        <h4>{before.title}</h4>
        <p>Progress for {before.date}</p>
        <p>
          Current amount: {before.value} ·{" "}
          {before.completed ? "Completed" : "Not completed"}
        </p>
        <p>
          {after.action}
          {after.value !== undefined ? ": " + after.value : ""}
        </p>
      </>
    );
  const fields = Object.keys(after).filter(
    (k) =>
      labels[k] &&
      (!before || JSON.stringify(before[k]) !== JSON.stringify(after[k])),
  );
  return (
    <>
      {before && <h4>{before.title || before.name || before.text}</h4>}
      <table className="proposal-fields">
        <thead>
          <tr>
            <th>Field</th>
            {before && <th>Current</th>}
            <th>Proposed</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((k) => (
            <tr key={k}>
              <th>{labels[k]}</th>
              {before && <td>{text(before[k])}</td>}
              <td>{text(after[k])}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {fields.length === 0 && <p>No field values change.</p>}
    </>
  );
}
export function ProposalList({
  threadId = "",
  memoryOnly = false,
  onChanged,
  fail,
}: {
  threadId?: string;
  memoryOnly?: boolean;
  onChanged?: () => void;
  fail: (e: unknown) => void;
}) {
  const [items, setItems] = useState<Data[]>([]),
    [busy, setBusy] = useState(""),
    [open, setOpen] = useState(false),
    [next, setNext] = useState<number | null>(null);
  const mounted = useRef(true),
    generation = useRef(0),
    operating = useRef(false),
    older = useRef(false);
  async function load(offset = 0) {
    const seq = ++generation.current;
    const r = await api(
      memoryOnly
        ? `/proposals/memory?offset=${offset}`
        : `/proposals?threadId=${encodeURIComponent(threadId)}&offset=${offset}&excludeMemory=true`,
    );
    if (!mounted.current || seq !== generation.current) return;
    if (offset) older.current = true;
    const incoming = memoryOnly
      ? r.items
      : r.items.filter((x: Data) => !x.operation.startsWith("memory."));
    setItems((current) =>
      older.current
        ? [
            ...new Map(
              [...current, ...incoming].map((x: Data) => [x.id, x]),
            ).values(),
          ]
        : incoming,
    );
    if (offset || !older.current) setNext(r.nextOffset ?? null);
  }
  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        if (!operating.current) await load();
      } catch (e) {
        if (mounted.current) fail(e);
      } finally {
        if (mounted.current) timer = setTimeout(tick, 5000);
      }
    };
    tick();
    return () => {
      mounted.current = false;
      generation.current++;
      clearTimeout(timer);
    };
  }, []);
  async function act(item: Data, action: string) {
    if (operating.current) return;
    operating.current = true;
    generation.current++;
    setBusy(item.id);
    try {
      const result = await api(`/proposals/${item.id}/${action}`, "POST", {});
      if (mounted.current) onChanged?.();
      if (mounted.current)
        setItems((current) =>
          current.map((x) => (x.id === item.id ? result : x)),
        );
    } catch (e) {
      if (mounted.current) fail(e);
    } finally {
      if (mounted.current) {
        await load().catch(fail);
        setBusy("");
      }
      operating.current = false;
    }
  }
  if (!items.length) return null;
  const cards = (
    <section aria-label="Suggested changes" className="settings-form">
      {items.map((item) =>
        item.operation === "coding.handoff" ? (
          <CodingHandoffCard
            key={item.id}
            item={item}
            fail={fail}
            decline={() => void act(item, "decline")}
          />
        ) : (
          <article className="card settings-form" key={item.id}>
            <h3>
              {item.state === "complete" ? "Saved:" : "Suggested:"}{" "}
              {names[item.operation] || "change"}
            </h3>
            <p>{item.reason}</p>
            <Review item={item} />
            {item.error && <p role="alert">{item.error}</p>}
            {item.state === "pending" && (
              <div className="actions">
                <button
                  className="primary"
                  disabled={!!busy}
                  onClick={() => act(item, "approve")}
                >
                  Approve this change
                </button>
                <button
                  className="secondary"
                  disabled={!!busy}
                  onClick={() => act(item, "decline")}
                >
                  Decline suggestion
                </button>
              </div>
            )}
            {item.state === "executing" && (
              <>
                <p>
                  This change was approved, but its outcome needs confirmation.
                </p>
                <button
                  className="secondary"
                  disabled={!!busy}
                  onClick={() => act(item, "approve")}
                >
                  Recover approved change
                </button>
              </>
            )}
            {item.state === "complete" && (
              <p role="status">
                {item.review?.approval?.mode === "automatic"
                  ? "Saved automatically under your memory preferences"
                  : "Change completed"}
              </p>
            )}
            {item.state === "declined" && <p>Suggestion declined</p>}
            {item.state === "conflict" && (
              <p>
                Change could not be completed. Review the current record and
                request a new suggestion.
              </p>
            )}
          </article>
        ),
      )}
      {next !== null && (
        <button
          className="secondary"
          disabled={!!busy}
          onClick={() => load(next).catch(fail)}
        >
          Earlier suggestions
        </button>
      )}
    </section>
  );
  if (memoryOnly) return cards;
  const pending = items.filter((item) => item.state === "pending").length;
  const uncertain = items.filter((item) => item.state === "executing").length;
  return (
    <details className="proposal-disclosure" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        Suggested changes · {pending ? `${pending} pending review` : `${items.length} recorded`}
        {uncertain > 0 && ` · ${uncertain} awaiting confirmation`}
      </summary>
      {open && cards}
    </details>
  );
}
