import { useEffect, useRef, useState } from "react";
import { Plus, Sun } from "lucide-react";
import { api, type Data } from "./api";
import { RemovalDialog } from "./removal";
import { ItemChat } from "./item-chat";

type Props = { fail: (error: unknown) => void };
import {
  CommitmentCard,
  CommitmentForm,
  Capacities,
  type EditorDraft,
} from "./commitment-components";
export function Goals({ fail }: Props) {
  const [items, setItems] = useState<Data[]>([]),
    [all, setAll] = useState<Data[]>([]),
    [capacities, setCapacities] = useState<Data[]>([]),
    [title, setTitle] = useState(""),
    [day, setDay] = useState(""),
    [filter, setFilter] = useState(""),
    [editing, setEditing] = useState<Data | null>(null),
    [adding, setAdding] = useState(false),
    [busy, setBusy] = useState(false),
    [manage, setManage] = useState(false);
  const [removing, setRemoving] = useState<{
    kind: "commitment" | "capacity";
    id: string;
  } | null>(null);
  const editorDrafts = useRef<Record<string, EditorDraft>>({});
  const sequence = useRef(0);
  const currentDay = useRef(day);
  currentDay.current = day;
  async function load() {
    const requestedDay = currentDay.current;
    const n = ++sequence.current;
    try {
      const [today, cs, full] = await Promise.all([
        api("/today" + (requestedDay ? "?date=" + requestedDay : "")),
        api("/capacities"),
        api("/commitments"),
      ]);
      if (n === sequence.current) {
        setItems(today.items);
        setCapacities(cs.items);
        setAll(full.items);
        setFilter((previous) =>
          previous && !cs.items.some((item: Data) => item.id === previous)
            ? ""
            : previous,
        );
      }
    } catch (e) {
      if (n === sequence.current) fail(e);
    }
  }
  useEffect(() => {
    load();
    return () => {
      sequence.current++;
    };
  }, [day]);
  const shown = items.filter((i) => !filter || i.capacityId === filter);
  return (
    <section className="page">
      <h1>Goals</h1>
      <p className="intro">Your capacities, commitments and progress.</p>
      <details className="goals-controls">
        <summary>Progress date and filters</summary>
        <div className="actions today-controls">
          <label>
            Viewing date
            <input
              type="date"
              value={day}
              onChange={(e) => setDay(e.target.value)}
            />
          </label>
          <button className="secondary" onClick={() => setDay("")}>
            Go to today
          </button>
          <label>
            Capacity
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">All capacities</option>
              {capacities.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </details>
      {!manage && (
        <div className="goal-capacities">
          {capacities.map((capacity) => (
            <article className="card" key={capacity.id}>
              <h2>{capacity.name}</h2>
              <small>
                {
                  all.filter(
                    (item) =>
                      item.capacityId === capacity.id &&
                      item.status === "active",
                  ).length
                }{" "}
                active commitments
              </small>
              {capacity.note && <p>{capacity.note}</p>}
              <ItemChat
                kind="capacity"
                id={capacity.id}
                title={capacity.name}
                changed={load}
              />
            </article>
          ))}
        </div>
      )}
      <form
        className="quick-add"
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          try {
            await api("/commitments", "POST", { title });
            setTitle("");
            await load();
          } catch (e) {
            fail(e);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Plus size={20} />
        <input
          aria-label="New commitment"
          placeholder="Something you want to follow through on…"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
          disabled={busy}
        />
        <button className="secondary" disabled={busy || !title.trim()}>
          Add
        </button>
      </form>
      <button className="secondary" onClick={() => setAdding(true)}>
        Plan a commitment
      </button>
      <button
        className="secondary"
        aria-expanded={manage}
        onClick={() => setManage((v) => !v)}
      >
        Manage capacities
      </button>
      {manage && (
        <Capacities
          items={capacities}
          changed={load}
          fail={fail}
          remove={(id) => setRemoving({ kind: "capacity", id })}
        />
      )}
      <div className="section-label">
        YOUR COMMITMENTS{" "}
        <span>{shown.filter((i) => i.status === "active").length} active</span>
      </div>
      {shown.length ? (
        shown.map((item) => (
          <CommitmentCard
            key={item.id + item.date}
            item={item}
            capacity={capacities.find((c) => c.id === item.capacityId)}
            changed={load}
            edit={() => setEditing(item)}
            fail={fail}
          />
        ))
      ) : (
        <div className="empty-card">
          <Sun size={30} />
          <h3>A clear page.</h3>
          <p>Add a commitment or choose another date.</p>
        </div>
      )}
      <details className="card">
        <summary>All commitments · {all.length}</summary>
        {all.map((i) => (
          <div key={i.id}>
            <div className="manage-row">
              <span>
                {i.title} · {i.status}
              </span>
              <button className="secondary" onClick={() => setEditing(i)}>
                Edit {i.title}
              </button>
              <button
                className="secondary"
                aria-label={`Remove ${i.title}`}
                onClick={() => setRemoving({ kind: "commitment", id: i.id })}
              >
                Remove
              </button>
            </div>
            {!shown.some((item) => item.id === i.id) && (
              <ItemChat
                kind="commitment"
                id={i.id}
                title={i.title}
                changed={load}
              />
            )}
          </div>
        ))}
      </details>
      {(adding || editing) && (
        <CommitmentForm
          initial={editing}
          draft={editorDrafts.current[editing?.id || "new"]}
          preserve={(draft) => {
            editorDrafts.current[editing?.id || "new"] = draft;
          }}
          capacities={capacities}
          close={(saved) => {
            if (saved) delete editorDrafts.current[editing?.id || "new"];
            setAdding(false);
            setEditing(null);
          }}
          changed={load}
          remove={(id) => {
            setEditing(null);
            setRemoving({ kind: "commitment", id });
          }}
          fail={fail}
        />
      )}
      {removing && (
        <RemovalDialog
          kind={removing.kind}
          id={removing.id}
          close={() => {
            setRemoving(null);
            void load();
          }}
          changed={async () => {
            delete editorDrafts.current[removing.id];
            await load();
          }}
        />
      )}
    </section>
  );
}
