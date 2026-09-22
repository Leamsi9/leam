import { QueuePause } from "./backlog-pause";
import { BacklogTriage } from "./backlog-triage";
import { BacklogEditor, DeletedBacklog } from "./backlog-editor";
import { FeatureCard, FeatureCardContext } from "./feature-card";
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { GripVertical, RefreshCw, ChevronUp, ChevronDown } from "lucide-react";
import { api, ApiError, type Data } from "./api";
import { SemanticBadge } from "./semantic-badge";
import "./backlog.css";

const stages = ["queued", "in_progress", "handover", "blocked"] as const;
type Stage = (typeof stages)[number];
const labels: Record<Stage, string> = {
  handover: "Ready to deploy",
  in_progress: "In progress",
  queued: "Queued",
  blocked: "Blocked",
};
function stage(item: Data): Stage {
  const value =
    item.deliveryLane ??
    ((item.blockers ?? []).length
      ? "blocked"
      : (item.deliveryState ?? "queued"));
  // Display projection only: legacy states and persisted domain records stay intact.
  if (
    value === "ready" ||
    (value === "in_progress" && opaqueId(item.worker) === "Unassigned")
  )
    return "queued";
  return stages.includes(value) ? value : "queued";
}
function opaqueId(value: unknown): string {
  return typeof value === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(value)
    ? value
    : "Unassigned";
}
type CardProps = {
  item: Data;
  changed: () => Promise<unknown>;
  stale: boolean;
  expanded: boolean;
  toggle: () => void;
  disabled: boolean;
  first: boolean;
  last: boolean;
  move: (direction: number) => void;
  startDrag: (event: PointerEvent<HTMLButtonElement>, item: Data) => void;
  moveDrag: (event: PointerEvent<HTMLButtonElement>) => void;
  endDrag: (
    event: PointerEvent<HTMLButtonElement>,
    cancelled?: boolean,
  ) => void;
  target: boolean;
};
function BacklogCard({
  item,
  changed,
  stale,
  expanded,
  toggle,
  disabled,
  first,
  last,
  move,
  startDrag,
  moveDrag,
  endDrag,
  target,
}: CardProps) {
  const subtasks: Data[] = item.subtasks ?? [];
  const priority = ["high", "normal", "low"].includes(item.priority)
    ? item.priority
    : "normal";
  return (
    <FeatureCard
      feature={item.feature}
      className={`backlog-card${target ? " backlog-drop-target" : ""}`}
      aria-label={item.title}
      data-backlog-feature={item.feature}
    >
      <button
        type="button"
        className="icon-button backlog-drag"
        aria-label={`Drag ${item.title} within ${labels[stage(item)]}`}
        disabled={disabled}
        onPointerDown={(event) => startDrag(event, item)}
        onPointerMove={moveDrag}
        onPointerUp={(event) => endDrag(event)}
        onPointerCancel={(event) => endDrag(event, true)}
        onLostPointerCapture={(event) => endDrag(event, true)}
        onKeyDown={(event) => {
          if (event.key === "Escape")
            endDrag(event as unknown as PointerEvent<HTMLButtonElement>, true);
        }}
      >
        <GripVertical size={20} aria-hidden="true" />
      </button>
      {["queued", "ready"].includes(item.deliveryState) && <QueuePause items={[item]} changed={changed} />}
      <details open={expanded}>
        <summary
          onClick={(event) => {
            event.preventDefault();
            toggle();
          }}
          aria-label={`Details and subtasks for ${item.title} (${subtasks.length})`}
        >
          <h3>{item.title}</h3>
          <span className="backlog-cues">
            <SemanticBadge value={stage(item)}>
              Stage: {labels[stage(item)]}
            </SemanticBadge>
            <span>{item.percent}% estimated</span>
            {expanded ? (
              <ChevronUp size={16} aria-hidden="true" />
            ) : (
              <ChevronDown size={16} aria-hidden="true" />
            )}
          </span>
        </summary>
        <div
          className="backlog-move"
          role="group"
          aria-label={`Priority order for ${item.title}`}
        >
          <button
            type="button"
            className="secondary"
            disabled={disabled || first}
            onClick={() => move(-1)}
          >
            Move up
          </button>
          <button
            type="button"
            className="secondary"
            disabled={disabled || last}
            onClick={() => move(1)}
          >
            Move down
          </button>
        </div>
        <div className="backlog-badges">
          <SemanticBadge value={`priority_${priority}`}>
            Priority: {priority}
          </SemanticBadge>
          <span>Build priority: {item.rank ?? "Unscheduled"}</span>
        </div>
        <p className="backlog-stage-meaning">
          {stage(item) === "in_progress"
            ? "Assigned work recorded as in progress; live worker activity is not monitored here."
            : stage(item) === "handover"
              ? "Implementation handed over for deployment. Deployment, QA and your acceptance are still separate."
              : stage(item) === "blocked"
                ? "Waiting on a dependency or a recorded blocker."
                : item.deliveryState === "in_progress" ||
                    item.deliveryLane === "in_progress"
                  ? "No assigned worker is recorded; this is queued, not active work."
                  : opaqueId(item.worker) === "Unassigned"
                    ? "Queued and unassigned."
                    : "Queued; a stored assignment does not mean work is active."}
        </p>
        <dl className="backlog-assignment">
          <div>
            <dt>Owner</dt>
            <dd>{opaqueId(item.owner)}</dd>
          </div>
          <div>
            <dt>Worker</dt>
            <dd>{opaqueId(item.worker)}</dd>
          </div>
        </dl>
        <p>
          <strong>Next action: </strong>
          {item.nextAction || "Coordinator to select next action"}
        </p>
        <p>{item.currentStep}</p>
        <BacklogEditor item={item} disabled={disabled} changed={changed} />
        <p>
          <strong>{item.percent}% estimated to deployment</strong> · Independent
          of subtask count; QA and UAT are separate.
        </p>
        {stage(item) === "blocked" &&
          item.deliveryState &&
          item.deliveryState !== "blocked" && (
            <p>
              Assigned stage: {labels[item.deliveryState as Stage] ?? "Queued"}.
              Blockers or unfinished dependencies take precedence.
            </p>
          )}
        {(item.blockers ?? []).length > 0 && (
          <div>
            <strong>Blockers</strong>
            <ul>
              {item.blockers.map((blocker: string, index: number) => (
                <li key={index}>{blocker}</li>
              ))}
            </ul>
          </div>
        )}
        {(item.dependencies ?? []).length > 0 && (
          <div>
            <strong>Dependencies</strong>
            <ul>
              {item.dependencies.map((dependency: string) => (
                <li key={dependency}>{dependency}</li>
              ))}
            </ul>
            <p>
              The coordinator verifies dependency receipts before selecting
              queued work.
            </p>
          </div>
        )}
        {subtasks.length ? (
          <ul className="backlog-subtasks" aria-label="Subtasks">
            {subtasks.map((task) => (
              <li key={task.id}>
                <SemanticBadge
                  value={task.state === "done" ? "completed" : task.state}
                >
                  {(
                    {
                      todo: "To do",
                      in_progress: "In progress",
                      done: "Done",
                      blocked: "Blocked",
                    } as Record<string, string>
                  )[task.state] ?? "To do"}
                </SemanticBadge>{" "}
                {task.title}
                {task.blocker && <p>Blocker: {task.blocker}</p>}
              </li>
            ))}
          </ul>
        ) : (
          <p>No subtasks recorded.</p>
        )}
        <p>
          {stale
            ? "Assessment stale — needs reassessment"
            : "Assessment fresh — activity is not inferred"}
        </p>
        <small>
          Last assessed {new Date(item.assessedAt).toLocaleString()}
        </small>
        <FeatureCardContext item={item} />
      </details>
    </FeatureCard>
  );
}

type Props = { fail: (error: unknown) => void };
type OrderRequest = {
  requestId: string;
  revision: number;
  expectedRevisions: Record<string, number>;
  lane: Stage | "all";
  features: string[];
};
type Drag = {
  pointerId: number;
  feature: string;
  lane: Stage;
  x: number;
  y: number;
  moved: boolean;
  target: string | null;
  after: boolean;
  snapshot: Data;
};
export function BacklogPanel({ fail }: Props) {
  const [data, setData] = useState<Data | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState(false);
  const [view, setView] = useState<"list" | "kanban">("list");
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set([new URLSearchParams(location.search).get("feature") || ""]),
  );
  const [saving, setSaving] = useState(false);
  const [retry, setRetry] = useState(false);
  const [notice, setNotice] = useState("");
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const pending = useRef<OrderRequest | null>(null);
  const busy = useRef(false);
  const alive = useRef(false);
  const epoch = useRef(0);
  const requests = useRef(new Set<AbortController>());
  const drag = useRef<Drag | null>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  async function request(path: string, method = "GET", body?: unknown) {
    const controller = new AbortController();
    requests.current.add(controller);
    try {
      return await api(path, method, body, controller.signal);
    } finally {
      requests.current.delete(controller);
    }
  }
  async function refresh() {
    const version = ++epoch.current;
    const result = await request("/backlog");
    if (alive.current && epoch.current === version) {
      setData(result);
      setError(false);
    }
    return result;
  }
  useEffect(() => {
    alive.current = true;
    let timer: ReturnType<typeof setTimeout>;
    const ticker = setInterval(() => setNow(Date.now()), 10000);
    async function poll() {
      if (!busy.current && !drag.current) {
        try {
          await refresh();
        } catch {
          if (alive.current) setError(true);
        }
      }
      if (alive.current) timer = setTimeout(poll, 15000);
    }
    void poll();
    return () => {
      alive.current = false;
      epoch.current++;
      clearTimeout(timer);
      clearInterval(ticker);
      requests.current.forEach((controller) => controller.abort());
    };
  }, []);
  async function save(order: OrderRequest) {
    if (busy.current) return;
    busy.current = true;
    restoreFocus.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    epoch.current++; // Fence display reads that started before the mutation.
    pending.current = order;
    setSaving(true);
    setRetry(false);
    setNotice("Saving order…");
    try {
      const receipt = await request("/backlog/order", "POST", order);
      if (!alive.current) return;
      pending.current = null;
      const current = await refresh();
      if (alive.current)
        setNotice(
          current.ordering?.revision === receipt.revision
            ? "Order saved"
            : "Saved receipt confirmed; latest order loaded",
        );
    } catch (e) {
      if (!alive.current) return;
      if (
        e instanceof ApiError &&
        e.status &&
        e.status >= 400 &&
        e.status < 500
      ) {
        pending.current = null;
        setNotice(
          e.status === 409
            ? "Backlog changed. Refresh, then choose the order again."
            : "Could not save order. Refresh before trying again.",
        );
        try {
          await refresh();
        } catch {
          if (alive.current) setError(true);
        }
      } else {
        setRetry(!!pending.current);
        setNotice(
          pending.current
            ? "Order confirmation unavailable. Retry the same save to check it safely."
            : "Order saved, but the latest view could not be loaded. Refresh before reordering.",
        );
        setError(true);
      }
    } finally {
      busy.current = false;
      if (alive.current) {
        setSaving(false);
        setTimeout(() => {
          const element = restoreFocus.current;
          if (
            !alive.current ||
            !element?.isConnected ||
            (document.activeElement !== document.body &&
              document.activeElement !== element)
          )
            return;
          if (element instanceof HTMLButtonElement && element.disabled)
            element.closest("article")?.querySelector("summary")?.focus();
          else element.focus();
        }, 0);
      }
    }
  }
  function reorder(current: Data, lane: Stage | "all", features: string[]) {
    if (
      busy.current ||
      pending.current ||
      !Number.isSafeInteger(current.ordering?.revision)
    )
      return;
    const original = current.items
      .filter((item: Data) => lane === "all" || stage(item) === lane)
      .map((item: Data) => item.feature);
    if (original.join("|") === features.join("|")) return;
    // A display lane can combine legacy server states. Replace only that lane's
    // slots in the global rank, keeping every other lane's position unchanged.
    let slot = 0;
    const globalFeatures =
      lane === "all"
        ? features
        : current.items.map((item: Data) =>
            stage(item) === lane ? features[slot++] : item.feature,
          );
    void save({
      requestId: crypto.randomUUID(),
      revision: current.ordering.revision,
      expectedRevisions: Object.fromEntries(
        current.items.map((item: Data) => [item.feature, item.revision]),
      ),
      lane: "all",
      features: globalFeatures,
    });
  }
  function startDrag(event: PointerEvent<HTMLButtonElement>, item: Data) {
    if (
      !event.isPrimary ||
      event.button !== 0 ||
      busy.current ||
      pending.current ||
      error ||
      !data
    )
      return;
    epoch.current++; // Do not replace the drag snapshot with an older in-flight poll.
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      feature: item.feature,
      lane: stage(item),
      x: event.clientX,
      y: event.clientY,
      moved: false,
      target: null,
      after: false,
      snapshot: data,
    };
  }
  function moveDrag(event: PointerEvent<HTMLButtonElement>) {
    const value = drag.current;
    if (!value || value.pointerId !== event.pointerId) return;
    if (
      Math.hypot(event.clientX - value.x, event.clientY - value.y) < 6 &&
      !value.moved
    )
      return;
    value.moved = true;
    const card = document
      .elementFromPoint(event.clientX, event.clientY)
      ?.closest<HTMLElement>("[data-backlog-feature]");
    const target = value.snapshot.items.find(
      (item: Data) => item.feature === card?.dataset.backlogFeature,
    );
    value.target =
      target &&
      target.feature !== value.feature &&
      (view === "list" || stage(target) === value.lane)
        ? target.feature
        : null;
    value.after =
      !!card &&
      event.clientY >
        card.getBoundingClientRect().top +
          card.getBoundingClientRect().height / 2;
    setDropTarget(value.target);
  }
  function endDrag(event: PointerEvent<HTMLButtonElement>, cancelled = false) {
    const value = drag.current;
    drag.current = null;
    setDropTarget(null);
    if (
      event.pointerId !== undefined &&
      event.currentTarget.hasPointerCapture(event.pointerId)
    )
      event.currentTarget.releasePointerCapture(event.pointerId);
    if (!cancelled && value?.moved && value.target) {
      const features = value.snapshot.items
        .filter(
          (item: Data) =>
            (view === "list" || stage(item) === value.lane) &&
            item.feature !== value.feature,
        )
        .map((item: Data) => item.feature);
      features.splice(
        features.indexOf(value.target) + (value.after ? 1 : 0),
        0,
        value.feature,
      );
      reorder(value.snapshot, view === "list" ? "all" : value.lane, features);
    }
  }
  const items: Data[] = data?.items ?? [];
  const renderCard = (item: Data) => {
    const laneItems =
      view === "list"
        ? items
        : items.filter((other) => stage(other) === stage(item));
    const index = laneItems.findIndex(
      (other) => other.feature === item.feature,
    );
    return (
      <BacklogCard
        key={item.feature}
        item={item}
        changed={refresh}
        stale={
          item.stale ||
          now - Date.parse(item.assessedAt) >
            (data?.staleAfterSeconds ?? 1200) * 1000
        }
        expanded={expanded.has(item.feature)}
        toggle={() =>
          setExpanded((previous) => {
            const next = new Set(previous);
            if (next.has(item.feature)) next.delete(item.feature);
            else next.add(item.feature);
            return next;
          })
        }
        disabled={
          saving ||
          retry ||
          error ||
          !Number.isSafeInteger(data?.ordering?.revision)
        }
        first={index === 0}
        last={index === laneItems.length - 1}
        move={(direction) => {
          if (!data) return;
          const features = laneItems.map((other) => other.feature);
          [features[index], features[index + direction]] = [
            features[index + direction],
            features[index],
          ];
          reorder(data, view === "list" ? "all" : stage(item), features);
        }}
        startDrag={startDrag}
        moveDrag={moveDrag}
        endDrag={endDrag}
        target={dropTarget === item.feature}
      />
    );
  };
  return (
    <section className="backlog-panel" aria-label="Build backlog">
      <div className="backlog-heading">
        <h2>Backlog</h2>
        <div className="backlog-controls">
          <div role="group" aria-label="Backlog view">
            <button
              className="secondary"
              aria-pressed={view === "list"}
              onClick={() => setView("list")}
            >
              List
            </button>
            <button
              className="secondary"
              aria-pressed={view === "kanban"}
              onClick={() => setView("kanban")}
            >
              Kanban
            </button>
          </div>
          <button
            className="icon-button"
            aria-label="Reload saved assessments"
            title="Refresh"
            disabled={saving}
            onClick={() => {
              void refresh().catch((e) => {
                if (alive.current) {
                  setError(true);
                  fail(e);
                }
              });
            }}
          >
            <RefreshCw size={18} aria-hidden="true" />
          </button>
        </div>
      </div>
      <QueuePause items={items.filter(item => ["queued", "ready"].includes(item.deliveryState))} all changed={refresh} />
      <BacklogTriage changed={refresh} />
      <DeletedBacklog changed={refresh} />
      <details className="backlog-about">
        <summary>About ordering and progress</summary>
        <p>
          Drag cards or use Move up and Move down to set build priority. Lower
          numbers come first. The list and build queue share this order; Kanban
          shows the same priorities within each stage. Reviews preserve your
          saved order. Dragging never changes stages, blockers or progress.
        </p>
        <p>
          The coordinator selects the first unblocked priority when a worker is
          available. Blocked work keeps its place; dependencies and urgent
          recovery may require a documented exception. Deployed items belong in
          Updates.
        </p>
        <p>
          Queued includes planned and legacy Ready work. In progress requires a
          recorded worker assignment; it does not prove that a process is still
          running. Ready to deploy means implementation was handed over, not
          that deployment, QA or UAT passed. Blocked means dependencies or
          blockers prevent progress. Paused work returns to the unassigned
          queue.
        </p>
        <p>
          Percentages estimate deployment independently of subtasks. Fresh
          assessments, worker exit and finished subtasks do not prove
          deployment, QA or user UAT. The timer checks freshness only.
        </p>
        {data && (
          <p>
            {data.review?.reviewedAt
              ? `${data.review.mode === "evidence_delta" ? "Recorded evidence checked" : "Last full review"} ${new Date(data.review.reviewedAt * 1000).toLocaleString()}`
              : "No full backlog review recorded"}
            {(!data.review?.current ||
              now - data.review.reviewedAt * 1000 >
                data.staleAfterSeconds * 1000) &&
              " · Review due"}
          </p>
        )}
      </details>
      {notice && <p role="status">{notice}</p>}
      {retry && (
        <button
          className="secondary"
          disabled={saving}
          onClick={() => {
            if (pending.current) void save(pending.current);
          }}
        >
          Retry save
        </button>
      )}
      {error && (
        <p role="alert">
          Could not refresh backlog; showing the last available assessment.
        </p>
      )}
      {data && !items.length && (
        <p>
          No unfinished work has been recorded. Deployed items appear in
          Updates.
        </p>
      )}
      {!data && !error && <p>Loading backlog…</p>}
      {data &&
        (view === "list" ? (
          <div className="backlog-list" aria-label="Delivery priority list">
            {items.map(renderCard)}
          </div>
        ) : (
          <div className="backlog-kanban" aria-label="Delivery Kanban">
            {stages.map((value) => (
              <section
                className="backlog-lane"
                key={value}
                aria-label={`${labels[value]} work`}
              >
                <h3>
                  <SemanticBadge value={value}>{labels[value]}</SemanticBadge> (
                  {items.filter((item) => stage(item) === value).length})
                </h3>
                {items.filter((item) => stage(item) === value).map(renderCard)}
                {!items.some((item) => stage(item) === value) && (
                  <p>No tickets</p>
                )}
              </section>
            ))}
          </div>
        ))}
    </section>
  );
}
