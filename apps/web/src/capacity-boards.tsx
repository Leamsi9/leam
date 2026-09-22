import { ItemChat } from "./item-chat";
import { LinkedResources } from "./resource-links";
import { type CanonicalChange } from "./canonical-change";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Plus, X, GripVertical, ChevronDown, ChevronUp } from "lucide-react";
import { api, ApiError, type Data } from "./api";
import { SemanticBadge, semanticTone } from "./semantic-badge";
import "./capacity-boards.css";

const columns = [
  ["todo", "To do"],
  ["in_progress", "In progress"],
  ["blocked", "Blocked"],
  ["completed", "Completed"],
  ["paused", "Paused"],
] as const;
const stageOf = (card: Data) =>
  ["completed", "paused"].includes(card.status)
    ? card.status
    : card.stage || "todo";
const stageLabel = (key: string) =>
  columns.find(([id]) => id === key)?.[1] || key;
const ownerLabel = (owner: string) => (owner === "leam" ? "Leam" : "You");
// Only a definite client rejection proves creation did not commit. Network,
// response-decoding, timeout and server failures can hide a successful write.
function unknownCreate(error: unknown) {
  return (
    !(error instanceof ApiError) ||
    !error.status ||
    error.status >= 500 ||
    error.status === 408
  );
}
function UnknownCreate({
  inspect,
  discard,
}: {
  inspect: () => void;
  discard: () => void;
}) {
  return (
    <div className="board-conflict" role="status">
      <p>
        Creation outcome unknown. It may already be saved. This draft cannot be
        submitted again. Inspect saved records before starting a separate
        creation.
      </p>
      <div className="actions">
        <button type="button" className="secondary" onClick={inspect}>
          Inspect saved boards and cards
        </button>
        <button type="button" className="secondary" onClick={discard}>
          Discard creation draft
        </button>
      </div>
      <small>Discarding this draft does not delete any saved record.</small>
    </div>
  );
}
const dates = ["startDate", "endDate", "dueDate"] as const;
const dateLabels = {
  startDate: "Planned start",
  endDate: "Planned end",
  dueDate: "Due date",
};
const fields = [
  "title",
  "notes",
  "capacityId",
  "owner",
  "priority",
  "startDate",
  "endDate",
  "dueDate",
  "kind",
  "measure",
  "target",
  "timezone",
  "reward",
  "reminderTime",
] as const;
function formFor(card: Data): Data {
  return {
    title: card.title || "",
    notes: card.notes || "",
    capacityId: card.capacityId || "",
    owner: card.owner || "user",
    priority: card.priority || "normal",
    startDate: card.startDate || "",
    endDate: card.endDate || "",
    dueDate: card.dueDate || "",
    kind: card.kind || "task",
    measure: card.measure || "boolean",
    target: String(card.target ?? 1),
    timezone: card.timezone || "Europe/London",
    reward: card.reward || "",
    reminderTime: card.reminderTime || "",
    column: stageOf(card),
  };
}
function Modal({
  title,
  children,
  close,
  busy,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.showModal();
    return () => {
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="board-dialog"
      aria-label={title}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) close();
      }}
    >
      <header>
        <h2>{title}</h2>
        <button
          className="icon-button"
          type="button"
          aria-label={`Close ${title}`}
          disabled={busy}
          onClick={close}
        >
          <X size={20} />
        </button>
      </header>
      {children}
    </dialog>
  );
}
function DateFields({
  form,
  set,
}: {
  form: Data;
  set: (key: string, value: string) => void;
}) {
  return (
    <div className="board-date-fields">
      {dates.map((key) => (
        <label key={key}>
          {dateLabels[key]}
          <input
            type="date"
            value={form[key] || ""}
            onChange={(e) => set(key, e.target.value)}
          />
        </label>
      ))}
    </div>
  );
}
function Tags({ card }: { card: Data }) {
  return (
    <div className="board-tags">
      <SemanticBadge value={stageOf(card)}>
        {stageLabel(stageOf(card))}
      </SemanticBadge>
      <SemanticBadge
        value={card.owner === "leam" ? "owner_leam" : "owner_user"}
      >
        {ownerLabel(card.owner)}
      </SemanticBadge>
      <SemanticBadge value={`priority_${card.priority || "normal"}`}>
        {card.priority === "high"
          ? "High priority"
          : card.priority === "low"
            ? "Low priority"
            : "Normal priority"}
      </SemanticBadge>
      {card.kind && card.kind !== "task" && (
        <SemanticBadge value="kind">{card.kind}</SemanticBadge>
      )}
    </div>
  );
}
function BoardSummary({ cards }: { cards: Data[] }) {
  const rows = columns.map(([id, label]) => {
    const matching = cards.filter((card) => stageOf(card) === id);
    return {
      id,
      label,
      total: matching.length,
      leam: matching.filter((card) => card.owner === "leam").length,
    };
  });
  return (
    <details className="board-summary">
      <summary>
        Board summary · {cards.length} {cards.length === 1 ? "card" : "cards"}
      </summary>
      <p>
        Loaded commitment cards in this board filter, including habits and
        goals. Counts exclude subtasks and daily progress.
      </p>
      {!!cards.length && (
        <div className="board-summary-chart" aria-hidden="true">
          {rows
            .filter((row) => row.total)
            .map((row) => (
              <span
                key={row.id}
                className="semantic-colour"
                data-tone={semanticTone(row.id)}
                style={{ flex: row.total }}
              />
            ))}
        </div>
      )}
      <table>
        <caption>Card counts by status and owner</caption>
        <thead>
          <tr>
            <th scope="col">Status</th>
            <th scope="col">You</th>
            <th scope="col">Leam</th>
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <th scope="row">
                <SemanticBadge value={row.id}>{row.label}</SemanticBadge>
              </th>
              <td>{row.total - row.leam}</td>
              <td>{row.leam}</td>
              <td>{row.total}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total cards</th>
            <td>{cards.filter((card) => card.owner !== "leam").length}</td>
            <td>{cards.filter((card) => card.owner === "leam").length}</td>
            <td>{cards.length}</td>
          </tr>
        </tfoot>
      </table>
    </details>
  );
}
// Stable display accent, never a status or a unique identity guarantee.
function capacityAccent(id: string) {
  let hash = 2166136261;
  for (const character of id)
    hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  return (hash >>> 0) % 6;
}
function CapacityGroup({
  id,
  name,
  count,
  initialOpen,
  children,
}: {
  id: string;
  name: string;
  count: number;
  initialOpen: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <details
      className="capacity-group"
      data-capacity-accent={capacityAccent(id)}
      open={open}
      aria-label={`${name} capacity`}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span className="capacity-group-name">{name}</span>
        <span>
          {count} {count === 1 ? "card" : "cards"}
        </span>
      </summary>
      <div className="capacity-group-content">
        {count ? children : <p>No cards in this capacity yet.</p>}
      </div>
    </details>
  );
}
type CardOrdering = {
  all: Data[];
  peers: Data[];
  lane: string | null;
  changeStatus: (card: Data, lane: string) => void;
  disabled: boolean;
  move: (card: Data, beforeId: string | null, lane: string | null) => void;
  marker: { id: string; after: boolean } | null;
  mark: (value: { id: string; after: boolean } | null) => void;
};
function subtaskProgress(nodes: Data[] = []): {
  total: number;
  completed: number;
} {
  return nodes.reduce<{ total: number; completed: number }>(
    (count, node) => {
      const child = subtaskProgress(node.children || []);
      return {
        total: count.total + 1 + child.total,
        completed:
          count.completed +
          Number(node.status === "completed") +
          child.completed,
      };
    },
    { total: 0, completed: 0 },
  );
}
function newSubtask(parentId: string | null = null): Data {
  return {
    id: crypto.randomUUID(),
    parentId,
    action: "add",
    title: "",
    notes: "",
    owner: "user",
    status: "todo",
  };
}
function Card({
  card,
  capacity,
  open,
  ordering,
  changed,
}: {
  card: Data;
  capacity?: Data;
  open: (section?: "subtasks" | "add-subtask") => void;
  ordering?: CardOrdering;
  changed: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const progress = subtaskProgress(card.subtasks);
  const drag = useRef<number | null>(null);
  const dragBoard = useRef<{ element: HTMLElement; snap: string } | null>(null);
  function restoreDragScroll() {
    const active = dragBoard.current;
    if (active) active.element.style.scrollSnapType = active.snap;
    dragBoard.current = null;
  }
  useEffect(() => restoreDragScroll, []);
  const index = ordering?.peers.findIndex((item) => item.id === card.id) ?? -1;
  function adjacent(step: number) {
    if (
      !ordering ||
      ordering.disabled ||
      index + step < 0 ||
      index + step >= ordering.peers.length
    )
      return;
    const before =
      step < 0
        ? ordering.peers[index - 1].id
        : ordering.peers[index + 2]?.id || null;
    ordering.move(card, before, ordering.lane);
  }
  function hit(x: number, y: number) {
    const pointed = document.elementFromPoint(x, y);
    const column = pointed?.closest<HTMLElement>("[data-board-lane]");
    if (
      column &&
      column.dataset.boardCapacity !== (card.capacityId || "unassigned")
    )
      return null;
    const lane = column?.dataset.boardLane || ordering?.lane || null;
    const element = pointed?.closest<HTMLElement>("[data-order-card]");
    const id = element?.dataset.orderCard;
    if (id === card.id) return null;
    if (element && ordering?.all.some((item) => item.id === id)) {
      const bounds = element.getBoundingClientRect();
      return { id: id!, after: y >= bounds.y + bounds.height / 2, lane };
    }
    return column ? { id: "", after: true, lane } : null;
  }
  function drop(x: number, y: number) {
    const target = hit(x, y);
    cancel();
    if (!target || !ordering) return;
    const peers = ordering.all.filter(
      (item) =>
        item.id !== card.id && (!target.lane || stageOf(item) === target.lane),
    );
    const destination = peers.findIndex((item) => item.id === target.id);
    ordering.move(
      card,
      target.id
        ? target.after
          ? peers[destination + 1]?.id || null
          : target.id
        : null,
      target.lane,
    );
  }
  function cancel() {
    restoreDragScroll();
    drag.current = null;
    ordering?.mark(null);
  }
  return (
    <article
      className="board-card semantic-colour"
      data-tone={semanticTone(stageOf(card))}
      data-order-card={card.id}
      draggable={!!ordering && !ordering.disabled}
      onDragStart={(event) => {
        if (!ordering || ordering.disabled) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.setData(
          "application/x-leam-board-card",
          JSON.stringify({
            id: card.id,
            capacityId: card.capacityId || "unassigned",
          }),
        );
        event.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={(event) => {
        if (ordering && !ordering.disabled) {
          event.preventDefault();
          ordering.mark(hit(event.clientX, event.clientY));
        }
      }}
      onDrop={(event) => {
        if (!ordering || ordering.disabled) return;
        event.preventDefault();
        event.stopPropagation();
        try {
          const source = JSON.parse(
            event.dataTransfer.getData("application/x-leam-board-card"),
          );
          const moving = ordering.all.find((item) => item.id === source.id);
          if (
            !moving ||
            source.capacityId !== (card.capacityId || "unassigned")
          )
            return;
          const bounds = event.currentTarget.getBoundingClientRect(),
            after = event.clientY >= bounds.y + bounds.height / 2;
          const peers = ordering.all.filter(
            (item) =>
              item.id !== moving.id &&
              (!ordering.lane || stageOf(item) === ordering.lane),
          );
          const index = peers.findIndex((item) => item.id === card.id);
          ordering.move(
            moving,
            after ? peers[index + 1]?.id || null : card.id,
            ordering.lane,
          );
        } catch {
          /* Foreign drag data grants no action. */
        }
        ordering.mark(null);
      }}
      onDragEnd={cancel}
      data-order-drop={
        ordering && ordering.marker && ordering.marker.id === card.id
          ? ordering.marker.after
            ? "after"
            : "before"
          : undefined
      }
    >
      <div className="board-card-compact">
        <button
          className="board-card-title"
          onClick={() => open()}
          aria-label={`Edit ${card.title}`}
        >
          <span>{card.title}</span>
          <small>
            {ownerLabel(card.owner)} · {stageLabel(stageOf(card))}
            {progress.total > 0 &&
              ` · ${progress.completed}/${progress.total} subtasks`}
          </small>
        </button>
        {ordering && (
          <button
            type="button"
            className="icon-button board-card-drag"
            disabled={ordering.disabled}
            title="Drag to reorder or move to another status column"
            aria-label={`Move ${card.title}; use arrow keys up or down`}
            aria-keyshortcuts="ArrowUp ArrowDown"
            onKeyDown={(event) => {
              if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                event.preventDefault();
                adjacent(event.key === "ArrowUp" ? -1 : 1);
              }
            }}
            onPointerDown={(event) => {
              if (!event.isPrimary || event.button !== 0 || ordering.disabled)
                return;
              restoreDragScroll();
              const board =
                event.currentTarget.closest<HTMLElement>(".board-kanban");
              if (board) {
                dragBoard.current = {
                  element: board,
                  snap: board.style.scrollSnapType,
                };
                board.style.scrollSnapType = "none";
              }
              drag.current = event.pointerId;
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              if (drag.current === event.pointerId) {
                const board =
                    event.currentTarget.closest<HTMLElement>(".board-kanban"),
                  bounds = board?.getBoundingClientRect();
                if (board && bounds) {
                  if (event.clientX > bounds.right - 35) board.scrollLeft += 18;
                  else if (event.clientX < bounds.left + 35)
                    board.scrollLeft -= 18;
                }
                ordering.mark(hit(event.clientX, event.clientY));
              }
            }}
            onPointerUp={(event) => {
              if (drag.current !== event.pointerId) return;
              drop(event.clientX, event.clientY);
            }}
            onPointerCancel={cancel}
            onLostPointerCapture={cancel}
          >
            <GripVertical size={18} aria-hidden="true" />
          </button>
        )}
        <button
          type="button"
          className="icon-button"
          aria-label={`${expanded ? "Collapse" : "Expand"} details for ${card.title}`}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? (
            <ChevronUp size={18} aria-hidden="true" />
          ) : (
            <ChevronDown size={18} aria-hidden="true" />
          )}
        </button>
      </div>
      {expanded && (
        <div className="board-card-expanded">
          {ordering && (
            <label>
              Status for {card.title}
              <select
                aria-label={`Status for ${card.title}`}
                value={stageOf(card)}
                disabled={ordering.disabled}
                onChange={(event) =>
                  ordering.changeStatus(card, event.target.value)
                }
              >
                {columns.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
              <small>Saves immediately.</small>
            </label>
          )}
          <small
            className="capacity-label"
            data-capacity-accent={capacityAccent(
              card.capacityId || "unassigned",
            )}
          >
            {capacity?.name || "Unassigned"}
          </small>
          <Tags card={card} />
          <LinkedResources targetType="commitment" targetId={card.id} />
          <ItemChat
            kind="commitment"
            id={card.id}
            title={card.title}
            changed={changed}
          />
          {card.notes && <p>{card.notes}</p>}
          {card.dueDate && <small>Due {card.dueDate}</small>}
          <div className="board-subtask-actions">
            <button type="button" className="secondary" onClick={() => open()}>
              Edit task details
            </button>
            <button
              type="button"
              className="secondary"
              disabled={progress.total >= 32}
              onClick={() => open("add-subtask")}
            >
              Add subtask
            </button>
            {progress.total > 0 && (
              <button
                type="button"
                className="secondary"
                onClick={() => open("subtasks")}
              >
                Edit subtasks · {progress.completed}/{progress.total} complete
              </button>
            )}
          </div>
          {ordering && (
            <div className="board-card-moves">
              <button
                type="button"
                className="secondary"
                disabled={ordering.disabled || index <= 0}
                onClick={() => adjacent(-1)}
              >
                Move earlier
              </button>
              <button
                type="button"
                className="secondary"
                disabled={
                  ordering.disabled || index === ordering.peers.length - 1
                }
                onClick={() => adjacent(1)}
              >
                Move later
              </button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
function Timeline({
  cards,
  capacities,
  open,
  changed,
}: {
  cards: Data[];
  capacities: Data[];
  open: (card: Data, section?: "subtasks" | "add-subtask") => void;
  changed: () => void;
}) {
  const scheduled = cards.filter((card) => dates.some((key) => card[key]));
  const unscheduled = cards.filter((card) => !dates.some((key) => card[key]));
  const allDates = scheduled
    .flatMap((card) =>
      dates.flatMap((key) => (card[key] ? [card[key] as string] : [])),
    )
    .sort();
  const first = allDates[0],
    last = allDates.at(-1);
  // ISO civil dates use UTC for arithmetic only; never infer a local appointment time.
  const day = (date: string) => Date.parse(date + "T00:00:00Z") / 86400000;
  const span = first && last ? Math.max(1, day(last) - day(first)) : 1;
  const position = (date: string) =>
    `${((day(date) - day(first)) / span) * 100}%`;
  return (
    <section aria-label="Gantt date timeline" className="board-timeline">
      <p>
        Explicit planned dates and deadlines only. A single date is a marker,
        not an inferred duration. Bar colour follows card status; a diamond
        marks the due date.
      </p>
      {scheduled.length ? (
        <>
          <div className="board-timeline-axis">
            <span>{first}</span>
            <span>{last}</span>
          </div>
          {scheduled.map((card) => (
            <article
              key={card.id}
              className="board-timeline-row semantic-colour"
              data-tone={semanticTone(stageOf(card))}
            >
              <button className="board-card-title" onClick={() => open(card)}>
                {card.title}
              </button>
              <Tags card={card} />
              <LinkedResources targetType="commitment" targetId={card.id} />
              <ItemChat
                kind="commitment"
                id={card.id}
                title={card.title}
                changed={changed}
              />
              <div className="board-timeline-track" aria-hidden="true">
                {card.startDate && card.endDate && (
                  <span
                    className="board-timeline-bar"
                    style={{
                      left: position(card.startDate),
                      width: `${((day(card.endDate) - day(card.startDate)) / span) * 100}%`,
                    }}
                  />
                )}
                {dates.map(
                  (key) =>
                    card[key] && (
                      <span
                        key={key}
                        className={`board-timeline-marker ${key}`}
                        style={{ left: position(card[key]) }}
                      />
                    ),
                )}
              </div>
              <div className="board-date-summary">
                {dates.map(
                  (key) =>
                    card[key] && (
                      <span key={key}>
                        {dateLabels[key]}: {card[key]}
                      </span>
                    ),
                )}
              </div>
            </article>
          ))}
        </>
      ) : (
        <p>No dates set for these cards.</p>
      )}
      <h3>Unscheduled ({unscheduled.length})</h3>
      <div className="board-list">
        {unscheduled.map((card) => (
          <Card
            key={card.id}
            card={card}
            capacity={capacities.find((c) => c.id === card.capacityId)}
            open={(section) => open(card, section)}
            changed={changed}
          />
        ))}
      </div>
    </section>
  );
}

export function CapacityBoards({
  active,
  refreshVersion = 0,
  onChanged,
}: {
  active: boolean;
  refreshVersion?: number;
  onChanged?: (change: CanonicalChange) => void;
}) {
  const [cards, setCards] = useState<Data[]>([]),
    [capacities, setCapacities] = useState<Data[]>([]);
  const [orders, setOrders] = useState<Record<string, Data>>({});
  const [orderNotice, setOrderNotice] = useState("");
  const [orderAttempt, setOrderAttempt] = useState<Data | null>(null);
  const [orderBusy, setOrderBusy] = useState(false);
  const orderingBusy = useRef(false);
  const [dropMarker, setDropMarker] = useState<{
    id: string;
    after: boolean;
  } | null>(null);
  const [board, setBoard] = useState("all"),
    [view, setView] = useState("list");
  const [loading, setLoading] = useState(false),
    [loaded, setLoaded] = useState(false),
    [error, setError] = useState("");
  const [editing, setEditing] = useState<Data | null>(null),
    [newBoard, setNewBoard] = useState<string | null>(null);
  const boardDraft = useRef<{ generation: string; draft?: Data } | null>(null);
  const drafts = useRef(
      new Map<string, { generation: string; draft?: Data }>(),
    ),
    sequence = useRef(0);
  function openCard(card: Data, section?: "subtasks" | "add-subtask") {
    const session = drafts.current.get(card.id) || {
      generation: crypto.randomUUID(),
    };
    drafts.current.set(card.id, session);
    setEditing({
      ...card,
      editorGeneration: session.generation,
      editorSection: section,
    });
  }
  function openBoard() {
    boardDraft.current ||= { generation: crypto.randomUUID() };
    setNewBoard(boardDraft.current.generation);
  }
  function currentCard() {
    return (
      !!editing &&
      drafts.current.get(editing.id)?.generation === editing.editorGeneration
    );
  }
  function closeCard() {
    setEditing((previous) =>
      previous?.editorGeneration === editing?.editorGeneration
        ? null
        : previous,
    );
  }
  function discardCard() {
    if (!currentCard()) return;
    drafts.current.delete(editing!.id);
    closeCard();
  }
  const readVersion = useRef(-1);
  async function load(preserveOrderFeedback = false) {
    const current = ++sequence.current;
    const requestedVersion = refreshVersion;
    setLoading(true);
    setError("");
    try {
      const [cs, bs] = await Promise.all([
        api("/commitments"),
        api("/capacities"),
      ]);
      if (current !== sequence.current) return;
      readVersion.current = requestedVersion;
      setCards(cs.items);
      setOrders((previous) =>
        Object.fromEntries(
          Object.entries(cs.boardOrders || {}).map(([key, value]) => {
            const next = value as Data;
            return [
              key,
              previous[key]?.revision > next.revision ? previous[key] : next,
            ];
          }),
        ),
      );
      if (!orderingBusy.current && !preserveOrderFeedback) {
        setOrderAttempt(null);
        setOrderNotice("");
      }
      setCapacities(bs.items);
      setLoaded(true);
      setBoard((previous) =>
        previous === "all" ||
        previous === "unassigned" ||
        bs.items.some((b: Data) => b.id === previous)
          ? previous
          : "all",
      );
    } catch (e) {
      if (current === sequence.current)
        setError(String(e instanceof Error ? e.message : e));
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (active && readVersion.current !== refreshVersion) void load(true);
    return () => {
      ++sequence.current;
    };
  }, [active, refreshVersion]);
  const shown = cards.filter(
    (card) =>
      board === "all" ||
      (board === "unassigned" ? !card.capacityId : card.capacityId === board),
  );
  const chosen = capacities.find((c) => c.id === board);
  const groups = (
    board === "all"
      ? [
          ...capacities.map((capacity) => ({
            id: capacity.id as string,
            name: capacity.name as string,
          })),
          ...[
            ...new Set(
              cards
                .map((card) => card.capacityId)
                .filter(
                  (id) =>
                    id && !capacities.some((capacity) => capacity.id === id),
                ),
            ),
          ].map((id) => ({ id: id as string, name: "Capacity unavailable" })),
          ...(cards.some((card) => !card.capacityId) || !capacities.length
            ? [{ id: "unassigned", name: "Unassigned" }]
            : []),
        ]
      : [{ id: board, name: chosen?.name || "Unassigned" }]
  )
    .map((group) => {
      const positions = new Map<string, number>(
        (orders[group.id]?.ids || []).map((id: string, i: number) => [id, i]),
      );
      return {
        ...group,
        cards: shown
          .filter((card) => (card.capacityId || "unassigned") === group.id)
          .sort(
            (a, b) =>
              (positions.get(a.id) ?? Number.MAX_SAFE_INTEGER) -
                (positions.get(b.id) ?? Number.MAX_SAFE_INTEGER) ||
              a.title.localeCompare(b.title) ||
              a.id.localeCompare(b.id),
          ),
      };
    })
    .sort(
      (a, b) =>
        Number(!a.cards.length) - Number(!b.cards.length) ||
        a.name.localeCompare(b.name),
    );
  function received(card: Data) {
    ++sequence.current;
    setLoading(false);
    onChanged?.({
      kind: "commitment",
      record: card as { id: string; revision: number },
    });
    // Keep confirmed status feedback while refreshing its canonical order token.
    void load(true);
    setCards((previous) =>
      previous.some((c) => c.id === card.id)
        ? previous.map((c) =>
            c.id === card.id && c.revision <= card.revision ? card : c,
          )
        : [...previous, card],
    );
  }
  async function submitOrder(body: Data) {
    if (orderingBusy.current) return;
    orderingBusy.current = true;
    setOrderBusy(true);
    setOrderAttempt(body);
    setOrderNotice("");
    ++sequence.current;
    setLoading(false);
    try {
      const result = await api("/commitments/order", "PUT", body);
      const key = result.capacityId || "unassigned";
      setOrders((previous) =>
        previous[key]?.revision > result.revision
          ? previous
          : { ...previous, [key]: result },
      );
      setOrderAttempt(null);
      setOrderNotice("Card order saved. Status and priority are unchanged.");
    } catch (error) {
      const conflict = error instanceof ApiError && error.status === 409;
      setOrderAttempt({ ...body, conflict });
      setOrderNotice(
        conflict
          ? "Cards or order changed. Refresh boards before moving again."
          : "Order was not confirmed. Refresh to inspect it, or retry this same move.",
      );
    } finally {
      orderingBusy.current = false;
      setOrderBusy(false);
    }
  }
  async function changeCardStatus(card: Data, lane: string) {
    if (orderingBusy.current || orderAttempt || lane === stageOf(card)) return;
    orderingBusy.current = true;
    setOrderBusy(true);
    setOrderNotice("");
    try {
      const saved = await api(`/commitments/${card.id}`, "PATCH", {
        revision: card.revision,
        ...(["completed", "paused"].includes(lane)
          ? { status: lane }
          : { status: "active", stage: lane }),
      });
      received(saved);
      setOrderNotice(`Status saved: ${stageLabel(lane)}.`);
    } catch (error) {
      setOrderNotice(
        `Status was not confirmed. Refresh before retrying. ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      orderingBusy.current = false;
      setOrderBusy(false);
    }
  }
  async function crossColumn(
    card: Data,
    beforeId: string | null,
    lane: string,
  ) {
    orderingBusy.current = true;
    setOrderBusy(true);
    setOrderNotice("");
    let savedStatus = false;
    try {
      const saved = await api(`/commitments/${card.id}`, "PATCH", {
        revision: card.revision,
        ...(["completed", "paused"].includes(lane)
          ? { status: lane }
          : { status: "active", stage: lane }),
      });
      savedStatus = true;
      received(saved);
      const listing = await api("/commitments"),
        order = listing.boardOrders[card.capacityId || "unassigned"];
      const body = {
        requestId: crypto.randomUUID(),
        capacityId: card.capacityId || null,
        revision: order.revision,
        membershipToken: order.membershipToken,
        cardId: card.id,
        beforeId,
        lane,
      };
      orderingBusy.current = false;
      await submitOrder(body);
      setOrderNotice((previous) =>
        previous.startsWith("Card order saved")
          ? `Status and card order saved: ${stageLabel(lane)}.`
          : `Status saved: ${stageLabel(lane)}. ${previous}`,
      );
    } catch (error) {
      setOrderNotice(
        `${savedStatus ? `Status saved: ${stageLabel(lane)}; order was not saved.` : "Status move was not confirmed; refresh before retrying."} ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      orderingBusy.current = false;
      setOrderBusy(false);
    }
  }
  function moveCard(card: Data, beforeId: string | null, lane: string | null) {
    if (orderAttempt || orderingBusy.current) return;
    if (lane && lane !== stageOf(card)) {
      void crossColumn(card, beforeId, lane);
      return;
    }
    const order = orders[card.capacityId || "unassigned"];
    if (!order?.canReorder) return;
    void submitOrder({
      requestId: crypto.randomUUID(),
      capacityId: card.capacityId || null,
      revision: order.revision,
      membershipToken: order.membershipToken,
      cardId: card.id,
      beforeId,
      lane,
    });
  }
  const chatChanged = () => {
    onChanged?.({ kind: "domain", source: "inspect" });
    void load(true);
  };
  const renderCard = (
    card: Data,
    peers: Data[],
    lane: string | null = null,
  ) => (
    <Card
      key={card.id}
      card={card}
      capacity={capacities.find((c) => c.id === card.capacityId)}
      open={(section) => openCard(card, section)}
      changed={chatChanged}
      ordering={{
        all: cards
          .filter(
            (item) =>
              (item.capacityId || "unassigned") ===
              (card.capacityId || "unassigned"),
          )
          .sort(
            (a, b) =>
              (orders[card.capacityId || "unassigned"]?.ids.indexOf(a.id) ??
                0) -
              (orders[card.capacityId || "unassigned"]?.ids.indexOf(b.id) ?? 0),
          ),
        changeStatus: (item, lane) => void changeCardStatus(item, lane),
        peers,
        lane,
        disabled:
          loading ||
          orderBusy ||
          !!orderAttempt ||
          !orders[card.capacityId || "unassigned"]?.canReorder,
        move: moveCard,
        marker: dropMarker,
        mark: setDropMarker,
      }}
    />
  );
  return (
    <section
      hidden={!active}
      className="capacity-boards today-page-body"
      aria-label="Capacity boards"
    >
      <div className="board-heading">
        <div>
          <h2>Your boards</h2>
          <p>Capacities and commitments, shared with Goals. All dates.</p>
        </div>
        <button className="secondary" onClick={openBoard}>
          <Plus size={18} /> New board
        </button>
      </div>
      <div className="board-toolbar">
        <label>
          Board
          <select value={board} onChange={(e) => setBoard(e.target.value)}>
            <option value="all">All boards</option>
            <option value="unassigned">Unassigned</option>
            {capacities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <div className="board-views" role="group" aria-label="Board view">
          {[
            ["list", "List"],
            ["kanban", "Kanban"],
            ["timeline", "Gantt"],
          ].map(([id, label]) => (
            <button
              key={id}
              className="secondary"
              aria-pressed={view === id}
              onClick={() => setView(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          className="secondary"
          disabled={loading || orderBusy}
          onClick={() => void load()}
        >
          Refresh boards
        </button>
        <button
          disabled={!loaded}
          onClick={() =>
            openCard({
              id: "new",
              capacityId: chosen?.id || null,
              status: "active",
            })
          }
        >
          <Plus size={18} /> New card
        </button>
      </div>
      {chosen?.note && <p>{chosen.note}</p>}
      {groups.some((group) => orders[group.id]?.canReorder === false) && (
        <p role="status">
          Manual ordering supports up to 1,000 cards per capacity. All cards
          remain available.
        </p>
      )}
      <p className="board-help">
        Drag a card (or its grip on touch) to reorder or change its status
        column. Assigning to Leam does not start work. Board status is separate
        from daily progress in Plan.
      </p>
      {orderNotice && (
        <p role="status">
          {orderNotice}{" "}
          {orderAttempt && !orderAttempt.conflict && (
            <button
              type="button"
              className="secondary"
              disabled={orderBusy}
              onClick={() => {
                const { conflict, ...sameRequest } = orderAttempt;
                void submitOrder(sameRequest);
              }}
            >
              Retry same move
            </button>
          )}
        </p>
      )}
      {orderBusy && <p role="status">Saving card order…</p>}
      {error && (
        <p role="alert">
          {error} {loaded && "Previously loaded cards remain visible."}
        </p>
      )}
      {loading && <p role="status">Refreshing boards…</p>}
      {loaded && !shown.length && <p>No cards in this board yet.</p>}
      {loaded && <BoardSummary cards={shown} />}
      <p className="capacity-colour-legend">
        Capacity accents label groups and may repeat. Status badges and card
        borders show progress.
      </p>
      {loaded &&
        groups.map((group, index) => (
          <CapacityGroup
            key={`${board}:${group.id}`}
            id={group.id}
            name={group.name}
            count={group.cards.length}
            initialOpen={
              board !== "all" || (index < 2 && group.cards.length > 0)
            }
          >
            {group.id !== "unassigned" && (
              <ItemChat
                kind="capacity"
                id={group.id}
                title={group.name}
                changed={chatChanged}
              />
            )}
            {view === "list" && (
              <div className="board-list">
                {group.cards.map((card) => renderCard(card, group.cards))}
              </div>
            )}
            {view === "kanban" && (
              <div className="board-kanban">
                {columns.map(([id, label]) => (
                  <section
                    key={id}
                    className="board-column semantic-colour"
                    data-tone={semanticTone(id)}
                    aria-label={label}
                    data-board-lane={id}
                    data-board-capacity={group.id}
                    onDragOver={(event) => {
                      if (!orderBusy && !orderAttempt) event.preventDefault();
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      if (orderBusy || orderAttempt) return;
                      try {
                        const source = JSON.parse(
                          event.dataTransfer.getData(
                            "application/x-leam-board-card",
                          ),
                        );
                        const card = group.cards.find(
                          (item) => item.id === source.id,
                        );
                        if (card && source.capacityId === group.id)
                          moveCard(card, null, id);
                      } catch {
                        /* Ignore unrelated drops. */
                      }
                    }}
                  >
                    <h3>
                      {label}{" "}
                      <span>
                        {group.cards.filter((c) => stageOf(c) === id).length}
                      </span>
                    </h3>
                    {group.cards
                      .filter((c) => stageOf(c) === id)
                      .map((card) =>
                        renderCard(
                          card,
                          group.cards.filter((item) => stageOf(item) === id),
                          id,
                        ),
                      )}
                    {!group.cards.some((c) => stageOf(c) === id) && (
                      <small>No cards</small>
                    )}
                  </section>
                ))}
              </div>
            )}
            {view === "timeline" && (
              <Timeline
                cards={group.cards}
                capacities={capacities}
                open={openCard}
                changed={chatChanged}
              />
            )}
          </CapacityGroup>
        ))}
      {active && editing && (
        <CardEditor
          key={editing.editorGeneration}
          initial={editing}
          capacities={capacities}
          draft={drafts.current.get(editing.id)?.draft}
          preserve={(draft) => {
            if (currentCard())
              drafts.current.set(editing.id, {
                generation: editing.editorGeneration,
                draft,
              });
          }}
          close={closeCard}
          received={(card) => {
            // A confirmed older editor's receipt still invalidates other views;
            // revision merging cannot replace a newer card or close its editor.
            received(card);
          }}
          inspect={() => {
            if (!currentCard()) return;
            closeCard();
            setBoard("all");
            onChanged?.({ kind: "domain", source: "inspect" });
            void load();
          }}
          discard={discardCard}
          saved={(card, addSubtask) => {
            if (!currentCard()) return;
            discardCard();
            if (addSubtask) openCard(card, "add-subtask");
          }}
        />
      )}
      {active && newBoard && (
        <BoardCreator
          key={newBoard}
          draft={boardDraft.current?.draft}
          preserve={(draft) => {
            if (boardDraft.current?.generation === newBoard)
              boardDraft.current.draft = draft;
          }}
          inspect={() => {
            if (boardDraft.current?.generation !== newBoard) return;
            setNewBoard((current) => (current === newBoard ? null : current));
            setBoard("all");
            onChanged?.({ kind: "domain", source: "inspect" });
            void load();
          }}
          discard={() => {
            if (boardDraft.current?.generation !== newBoard) return;
            boardDraft.current = null;
            setNewBoard((current) => (current === newBoard ? null : current));
          }}
          close={() =>
            setNewBoard((current) => (current === newBoard ? null : current))
          }
          created={(b) => {
            onChanged?.({
              kind: "capacity",
              record: b as { id: string; revision: number },
            });
            ++sequence.current;
            setLoading(false);
            setCapacities((previous) =>
              previous.some((c) => c.id === b.id)
                ? previous.map((c) =>
                    c.id === b.id && c.revision <= b.revision ? b : c,
                  )
                : [...previous, b],
            );
            if (boardDraft.current?.generation !== newBoard) return;
            boardDraft.current = null;
            setBoard(b.id);
            setNewBoard((current) => (current === newBoard ? null : current));
          }}
        />
      )}
    </section>
  );
}

function BoardCreator({
  close,
  created,
  draft,
  preserve,
  inspect,
  discard,
}: {
  close: () => void;
  created: (board: Data) => void;
  draft?: Data;
  preserve: (draft: Data) => void;
  inspect: () => void;
  discard: () => void;
}) {
  const [name, setName] = useState(draft?.name || ""),
    [note, setNote] = useState(draft?.note || ""),
    [uncertain, setUncertain] = useState(!!draft?.uncertain),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    preserve({ name, note, uncertain });
  }, [name, note, uncertain]);
  return (
    <Modal
      title="New board"
      close={() => {
        preserve({ name, note, uncertain });
        close();
      }}
      busy={busy}
    >
      {uncertain && !busy && (
        <UnknownCreate
          inspect={() => {
            preserve({ name, note, uncertain });
            inspect();
          }}
          discard={discard}
        />
      )}
      <form
        className="board-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy || uncertain) return;
          setBusy(true);
          setError("");
          preserve({ name, note, uncertain: true });
          try {
            created(
              await api("/capacities", "POST", {
                name: name.trim(),
                note,
                record: "",
              }),
            );
          } catch (e) {
            setError(String(e instanceof Error ? e.message : e));
            const unknown = unknownCreate(e);
            setUncertain(unknown);
            preserve({ name, note, uncertain: unknown });
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset disabled={busy || uncertain}>
          <label>
            Board name
            <input
              required
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            Board note
            <textarea
              maxLength={10000}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          {error && <p role="alert">{error}</p>}
          <button disabled={busy || uncertain || !name.trim()}>
            Create board
          </button>
        </fieldset>
      </form>
    </Modal>
  );
}

function CardEditor({
  initial,
  capacities,
  draft,
  preserve,
  close,
  received,
  saved,
  inspect,
  discard,
}: {
  initial: Data;
  capacities: Data[];
  draft?: Data;
  preserve: (draft: Data) => void;
  close: () => void;
  received: (card: Data) => void;
  saved: (card: Data, addSubtask?: boolean) => void;
  inspect: () => void;
  discard: () => void;
}) {
  const [base, setBase] = useState<Data>(draft?.base || initial),
    [form, setForm] = useState<Data>(draft?.form || formFor(initial));
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [conflict, setConflict] = useState(false),
    [uncertain, setUncertain] = useState(!!draft?.uncertain);
  const [statusReceipt, setStatusReceipt] = useState("");
  const [detailsReceipt, setDetailsReceipt] = useState("");
  const [pane, setPane] = useState<"details" | "subtasks">(
    initial.editorSection ? "subtasks" : "details",
  );
  const [sub, setSub] = useState<Data | null>(
      draft?.sub ||
        (initial.editorSection === "add-subtask" &&
        subtaskProgress(initial.subtasks).total < 32
          ? newSubtask()
          : null),
    ),
    [remove, setRemove] = useState<Data | null>(null);
  const isNew = base.id === "new";
  const subtaskSection = useRef<HTMLElement>(null);
  const progress = subtaskProgress(base.subtasks);
  useEffect(() => {
    if (initial.editorSection && !isNew) {
      subtaskSection.current?.scrollIntoView({ block: "start" });
      subtaskSection.current?.querySelector<HTMLInputElement>("input")?.focus();
    }
  }, []);
  useEffect(() => {
    preserve({ base, form, sub, uncertain });
  }, [base, form, sub, uncertain]);
  function set(key: string, value: string) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }
  async function run(
    operation: () => Promise<Data>,
    after: (result: Data) => void,
  ) {
    if (busy || conflict || uncertain) return;
    if (isNew) preserve({ base, form, sub, uncertain: true });
    setBusy(true);
    setError("");
    try {
      const result = await operation();
      received(result);
      setBase(result);
      after(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (isNew) {
        const unknown = unknownCreate(e);
        setUncertain(unknown);
        preserve({ base, form, sub, uncertain: unknown });
      } else if (e instanceof ApiError && e.status === 409) setConflict(true);
    } finally {
      setBusy(false);
    }
  }
  async function reload() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await api("/commitments");
      const latest = result.items.find((c: Data) => c.id === base.id);
      if (!latest) throw new Error("This card no longer exists.");
      setBase(latest);
      setForm(formFor(latest));
      setSub(null);
      setRemove(null);
      received(latest);
      setConflict(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  function saveStatus(column: string) {
    if (isNew) {
      set("column", column);
      return;
    }
    if (column === stageOf(base)) return;
    setStatusReceipt("");
    void run(
      () =>
        api(`/commitments/${base.id}`, "PATCH", {
          revision: base.revision,
          ...(["completed", "paused"].includes(column)
            ? { status: column }
            : { status: "active", stage: column }),
        }),
      (result) => {
        setForm((previous) => ({ ...previous, column: stageOf(result) }));
        setStatusReceipt(
          `Status saved: ${stageLabel(stageOf(result))}. Other edits still need Save card.`,
        );
      },
    );
  }
  function save(addSubtask = false) {
    const before = formFor(base),
      body: Data = isNew ? {} : { revision: base.revision };
    for (const key of fields)
      if (isNew || form[key] !== before[key])
        body[key] =
          key === "capacityId" ||
          key === "reminderTime" ||
          dates.includes(key as (typeof dates)[number])
            ? form[key] || null
            : key === "target"
              ? Number(form[key])
              : form[key];
    if (isNew || form.column !== before.column)
      Object.assign(
        body,
        ["completed", "paused"].includes(form.column)
          ? { status: form.column }
          : { status: "active", stage: form.column },
      );
    if (!isNew && Object.keys(body).length === 1) {
      if (sub)
        setDetailsReceipt(
          "Task details are up to date. Your subtask draft is retained.",
        );
      else saved(base, addSubtask);
      return;
    }
    void run(
      () =>
        api(
          isNew ? "/commitments" : `/commitments/${base.id}`,
          isNew ? "POST" : "PATCH",
          body,
        ),
      (result) => {
        if (!isNew && sub) {
          setForm(formFor(result));
          setDetailsReceipt(
            "Task details saved. Your subtask draft is retained separately.",
          );
        } else saved(result, addSubtask);
      },
    );
  }
  function updateSub(action: string, node: Data) {
    const body: Data = { revision: base.revision, action, subtaskId: node.id };
    if (action !== "remove") {
      for (const key of ["title", "notes", "owner", "status", ...dates])
        body[key] = dates.includes(key as (typeof dates)[number])
          ? node[key] || null
          : node[key];
      if (action === "add") body.parentId = node.parentId || null;
    }
    void run(
      () => api(`/commitments/${base.id}/subtasks`, "POST", body),
      () => {
        setSub(null);
        setRemove(null);
      },
    );
  }
  function newSub(parentId: string | null = null) {
    setPane("subtasks");
    setRemove(null);
    setSub(newSubtask(parentId));
  }
  function tree(nodes: Data[], depth = 1): ReactNode {
    return (
      <ul className="board-subtasks">
        {nodes.map((node) => (
          <li key={node.id}>
            <div>
              <strong>{node.title}</strong>
              <div className="board-tags">
                <SemanticBadge
                  value={node.owner === "leam" ? "owner_leam" : "owner_user"}
                >
                  {ownerLabel(node.owner)}
                </SemanticBadge>
                <SemanticBadge value={node.status}>
                  {stageLabel(node.status)}
                </SemanticBadge>
              </div>
              {node.notes && <p>{node.notes}</p>}
              <div className="board-date-summary">
                {dates.map(
                  (key) =>
                    node[key] && (
                      <span key={key}>
                        {dateLabels[key]}: {node[key]}
                      </span>
                    ),
                )}
              </div>
              <div className="board-subtask-actions">
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || conflict || !!sub}
                  onClick={() => {
                    setRemove(null);
                    setSub({ ...node, action: "edit" });
                  }}
                  aria-label={`Edit subtask ${node.title}`}
                >
                  Edit
                </button>
                {depth < 3 && (
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy || conflict || !!sub || progress.total >= 32}
                    onClick={() => newSub(node.id)}
                    aria-label={`Add child to ${node.title}`}
                  >
                    Add child
                  </button>
                )}
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || conflict || !!sub}
                  onClick={() => {
                    setSub(null);
                    setRemove(node);
                  }}
                  aria-label={`Remove subtask ${node.title}`}
                >
                  Remove
                </button>
              </div>
            </div>
            {!!node.children?.length && tree(node.children, depth + 1)}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <Modal
      title={isNew ? "New card" : "Card details"}
      close={close}
      busy={busy}
    >
      {error && <p role="alert">{error}</p>}
      {uncertain && !busy && (
        <UnknownCreate inspect={inspect} discard={discard} />
      )}
      {conflict && (
        <div className="board-conflict">
          <p>
            Your draft is retained. Load the latest card to replace this draft
            before editing again.
          </p>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => void reload()}
          >
            Load latest and replace draft
          </button>
        </div>
      )}
      {!isNew && (
        <div
          className="board-editor-sections"
          role="group"
          aria-label="Card editor section"
        >
          <button
            type="button"
            className="secondary"
            aria-pressed={pane === "details"}
            onClick={() => setPane("details")}
          >
            Task details
          </button>
          <button
            type="button"
            className="secondary"
            aria-pressed={pane === "subtasks"}
            onClick={() => setPane("subtasks")}
          >
            Subtasks{sub ? " · draft retained" : ` · ${progress.total}`}
          </button>
        </div>
      )}
      <form
        hidden={pane !== "details"}
        aria-label="Task details"
        className="board-form"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        <fieldset disabled={busy || conflict || uncertain}>
          <label>
            Card title
            <input
              required
              maxLength={500}
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
            />
          </label>
          <label>
            Notes
            <textarea
              maxLength={20000}
              value={form.notes}
              onChange={(e) => set("notes", e.target.value)}
            />
          </label>
          <div className="board-form-grid">
            <label>
              Capacity board
              <select
                value={form.capacityId}
                onChange={(e) => set("capacityId", e.target.value)}
              >
                <option value="">Unassigned</option>
                {capacities.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Owner
              <select
                value={form.owner}
                onChange={(e) => set("owner", e.target.value)}
              >
                <option value="user">You</option>
                <option value="leam">Leam</option>
              </select>
            </label>
            <label>
              Card status
              <select
                disabled={busy || conflict || uncertain}
                value={form.column}
                onChange={(e) => saveStatus(e.target.value)}
              >
                {columns.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            {!isNew && (
              <small>
                Saved status: {stageLabel(stageOf(base))}. Status changes save
                immediately.
              </small>
            )}
            {statusReceipt && <p role="status">{statusReceipt}</p>}
            <label>
              Priority
              <select
                value={form.priority}
                onChange={(e) => set("priority", e.target.value)}
              >
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
              </select>
            </label>
          </div>
          <DateFields form={form} set={set} />
          <details className="board-extra-fields">
            <summary>Type, progress and reminders</summary>
            <div className="board-form-grid">
              <label>
                Card type
                <select
                  value={form.kind}
                  onChange={(e) => set("kind", e.target.value)}
                >
                  <option value="task">Task</option>
                  <option value="habit">Habit</option>
                  <option value="goal">Goal</option>
                </select>
              </label>
              <label>
                Progress measure
                <select
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
                    required
                    min="0"
                    max="1000000"
                    step="any"
                    value={form.target}
                    onChange={(e) => set("target", e.target.value)}
                  />
                </label>
              )}
              <label>
                Timezone
                <input
                  required
                  value={form.timezone}
                  placeholder="Europe/London"
                  onChange={(e) => set("timezone", e.target.value)}
                />
              </label>
              <label>
                Daily reminder time
                <input
                  type="time"
                  value={form.reminderTime}
                  onChange={(e) => set("reminderTime", e.target.value)}
                />
              </label>
            </div>
            <label>
              Reward
              <textarea
                maxLength={2000}
                value={form.reward}
                onChange={(e) => set("reward", e.target.value)}
              />
            </label>
            <p className="board-help">
              A reminder time creates an in-app reminder for active cards within
              their planned dates, unless daily progress is complete. It uses
              this timezone, not the due date. Device push requires separate
              notification setup; Leam must be running.
            </p>
          </details>
          <p className="board-help">
            Leam ownership does not start execution. Completing a card changes
            its lifecycle, not its daily progress log.
          </p>
          {sub && (
            <p className="board-help">
              Your subtask draft is retained separately. Saving task details
              does not create or change a subtask.
            </p>
          )}
          {detailsReceipt && <p role="status">{detailsReceipt}</p>}
          <div className="actions">
            <button disabled={!form.title.trim()}>Save card</button>
            {isNew && (
              <button
                type="button"
                className="secondary"
                disabled={!form.title.trim()}
                onClick={(event) => {
                  if (event.currentTarget.form?.reportValidity()) save(true);
                }}
              >
                Save and add subtasks
              </button>
            )}
          </div>
        </fieldset>
      </form>
      {!isNew && (
        <section
          hidden={pane !== "subtasks"}
          ref={subtaskSection}
          className="board-subtask-section"
          aria-label="Subtasks"
        >
          <h3>
            Subtasks · {progress.completed}/{progress.total} complete
          </h3>
          <p>
            Each subtask has its own status. Completing one does not complete
            its parent.
          </p>
          {tree(base.subtasks || [])}
          <button
            className="secondary"
            disabled={busy || conflict || !!sub || progress.total >= 32}
            onClick={() => newSub()}
          >
            Add subtask
          </button>
          {progress.total >= 32 && (
            <p className="board-help">
              This card has the maximum of 32 subtasks.
            </p>
          )}
          {remove && (
            <div className="board-remove-confirm">
              <p>Remove “{remove.title}” and all its children?</p>
              <button
                disabled={busy || conflict}
                onClick={() => updateSub("remove", remove)}
              >
                Confirm remove subtask
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => setRemove(null)}
              >
                Cancel removal
              </button>
            </div>
          )}
          {sub && (
            <form
              className="board-form board-subtask-form"
              aria-label="Subtask editor"
              onSubmit={(e) => {
                e.preventDefault();
                updateSub(sub.action, sub);
              }}
            >
              <fieldset disabled={busy || conflict || uncertain}>
                <h4>{sub.action === "add" ? "Add subtask" : "Edit subtask"}</h4>
                <label>
                  Subtask title
                  <input
                    autoFocus
                    required
                    maxLength={200}
                    value={sub.title}
                    onChange={(e) => setSub({ ...sub, title: e.target.value })}
                  />
                </label>
                <label>
                  Subtask notes
                  <textarea
                    maxLength={500}
                    value={sub.notes || ""}
                    onChange={(e) => setSub({ ...sub, notes: e.target.value })}
                  />
                </label>
                <div className="board-form-grid">
                  <label>
                    Subtask owner
                    <select
                      value={sub.owner}
                      onChange={(e) =>
                        setSub({ ...sub, owner: e.target.value })
                      }
                    >
                      <option value="user">You</option>
                      <option value="leam">Leam</option>
                    </select>
                  </label>
                  <label>
                    Subtask status
                    <select
                      value={sub.status}
                      onChange={(e) =>
                        setSub({ ...sub, status: e.target.value })
                      }
                    >
                      {columns
                        .filter(([id]) => id !== "paused")
                        .map(([id, label]) => (
                          <option key={id} value={id}>
                            {label}
                          </option>
                        ))}
                    </select>
                  </label>
                </div>
                <DateFields
                  form={sub}
                  set={(key, value) => setSub({ ...sub, [key]: value })}
                />
                <div className="actions">
                  <button disabled={!sub.title.trim()}>Save subtask</button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setSub(null)}
                  >
                    Cancel subtask edit
                  </button>
                </div>
              </fieldset>
            </form>
          )}
        </section>
      )}
    </Modal>
  );
}
