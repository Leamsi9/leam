import { useState, type ReactNode } from "react";
import { Check, ChevronDown, RefreshCw } from "lucide-react";
import type { Data } from "./api";
import { rememberSession, sessionValue } from "./session-cache";

/** Layout preferences only. Canonical records remain owned by their domain. */
export function OverviewCard({
  id,
  title,
  icon,
  className = "",
  children,
}: {
  id: string;
  title: string;
  icon?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const key = `today:overview:collapsed:${id}`;
  const [closed, setClosed] = useState(() => sessionValue(key, false));
  return (
    <details
      className={`overview-card ${className}`}
      open={!closed}
      onToggle={(event) => {
        const next = !event.currentTarget.open;
        if (next !== closed) {
          setClosed(next);
          rememberSession(key, next);
        }
      }}
    >
      <summary>
        {icon}
        <h2>{title}</h2>
        <ChevronDown
          size={18}
          className="overview-disclosure"
          aria-hidden="true"
        />
      </summary>
      <div className="overview-card-body">{children}</div>
    </details>
  );
}

/** A completion status without a selected-day log is not dated evidence. */
export function recordedAccomplishments(
  agenda: Data | null,
  date: string,
): Data[] {
  if (!agenda || agenda.date !== date) return [];
  if (agenda.accomplishments?.date === date) return agenda.accomplishments.items || [];
  const seen = new Set<string>();
  return (agenda.commitments || []).filter((item: Data) => {
    if (
      !item.id ||
      seen.has(item.id) ||
      item.log?.done !== true ||
      item.log?.date !== date ||
      !Number.isSafeInteger(item.log?.revision) ||
      item.log.revision < 1
    )
      return false;
    seen.add(item.id);
    return true;
  });
}

/** Praise only recorded achievements, attributing them to their saved owner. */
function personalProgress(items: Data[], activity: Data[]): string {
  const completed = items.find((item) => item.owner === "user");
  if (completed) return `You completed “${completed.title}”. Nicely done.`;
  const step = activity.find((item) => item.action === "subtask_completed" && item.owner === "user");
  if (step) return `You finished “${step.title}”${step.parentTitle ? ` towards “${step.parentTitle}”` : ""}. That step deserves a little celebration.`;
  const started = activity.find((item) => ["started", "subtask_started"].includes(item.action) && item.owner === "user");
  if (started) return `You got started on “${started.title}”${started.parentTitle ? ` within “${started.parentTitle}”` : ""}. A step forward worth noticing.`;
  const delegated = items.find((item) => item.owner === "leam");
  if (delegated) return `Leam completed “${delegated.title}” for you.`;
  const recorded = items[0];
  if (recorded) return `“${recorded.title}” is recorded complete. A moment worth celebrating.`;
  const progress = activity[0];
  if (progress) return progress.action === "subtask_completed"
    ? `“${progress.title}” is recorded complete${progress.parentTitle ? ` within “${progress.parentTitle}”` : ""}. A step worth recognising.`
    : `Progress on “${progress.title}” is recorded here.`;
  return "Nothing recorded here for this day. You can take it at your own pace.";
}

export function Celebration({
  agenda,
  date,
  refresh,
  refreshing = false,
}: {
  agenda: Data | null;
  date: string;
  refresh?: () => void;
  refreshing?: boolean;
}) {
  const items = recordedAccomplishments(agenda, date);
  const completeEvidence = agenda?.accomplishments?.date === date;
  const partial = !completeEvidence && (!!agenda?.partial || agenda?.nextOffset != null);
  const activity: Data[] = completeEvidence ? agenda.accomplishments.activity || [] : [];
  const undated = completeEvidence ? agenda.accomplishments.undatedCompleted || 0 : 0;
  return (
    <OverviewCard
      id="celebration"
      title="Worth celebrating"
      className="overview-celebration"
      icon={<span aria-hidden="true">{items.length || activity.some(item => item.action === "subtask_completed") ? "🎉" : "🌱"}</span>}
    >
      {refresh && <ActivityRefresh refresh={refresh} refreshing={refreshing} label="Refresh celebrations" />}
      <p className="overview-celebration-intro">
        {agenda ? personalProgress(items, activity) : "Your recorded progress is loading."}
      </p>
      {!!items.length && <p className="overview-task-basis">{items.length} {items.length === 1 ? "commitment" : "commitments"} recorded complete for {date}.</p>}
      {!!items.length && (
        <ul>
          {items.map((item) => (
            <li key={item.id}>
              <Check size={16} aria-hidden="true" />
              <span>
                <span>{item.title}</span>
                <small>
                  {item.owner === "leam"
                    ? "Leam"
                    : item.owner === "user"
                      ? "You"
                      : "Owner unspecified"}{" "}
                  · {item.kind === "habit" ? "Daily habit" : "Commitment"}
                  {item.completionEvidence === "user_requested_backfill" && " · Date assigned at your request"}
                </small>
              </span>
            </li>
          ))}
        </ul>
      )}
      <RecordedActivity activity={completeEvidence ? agenda.accomplishments : undefined} day={date} showCompleted={false} />
      <small>
        {partial
          ? "Based on the loaded part of this day; more recorded completions may be available."
          : "Based on saved daily progress and dated task completion records."}
      </small>
      {undated > 0 && <small>{undated} other completed {undated === 1 ? "task has" : "tasks have"} no recorded completion date, so {undated === 1 ? "it is" : "they are"} not assigned to this day.</small>}
    </OverviewCard>
  );
}

/** Same canonical status precedence used by Boards; blocked/paused stay explicit. */
export function capacityTaskCounts(capacities: Data[], commitments: Data[]) {
  const groups = capacities.map((capacity) => ({
    id: capacity.id,
    name: capacity.name,
    todo: 0,
    in_progress: 0,
    completed: 0,
    blocked: 0,
    paused: 0,
  }));
  const lookup = new Map(groups.map((group) => [group.id, group]));
  for (const card of commitments) {
    let group = lookup.get(card.capacityId);
    if (!group) {
      const id = card.capacityId || "unassigned";
      group = lookup.get(id);
      if (!group) {
        group = {
          id,
          name: card.capacityId
            ? "Unavailable capacity"
            : "Personal · no capacity",
          todo: 0,
          in_progress: 0,
          completed: 0,
          blocked: 0,
          paused: 0,
        };
        groups.push(group);
        lookup.set(id, group);
      }
    }
    const stage = ["completed", "paused"].includes(card.status)
      ? card.status
      : card.stage || "todo";
    if (
      stage in group &&
      typeof group[stage as keyof typeof group] === "number"
    )
      (group as any)[stage]++;
  }
  return groups;
}

export function TaskCapacitySummary({
  capacities,
  commitments,
}: {
  capacities: Data[];
  commitments: Data[] | null;
}) {
  if (!commitments)
    return (
      <p>Task counts are unavailable until the canonical task list loads.</p>
    );
  const groups = capacityTaskCounts(capacities, commitments);
  return (
    <>
      <p className="overview-task-basis">
        Current commitment cards, across all dates. Subtasks stay within their
        parent card.
      </p>
      {groups.length ? (
        <ul className="overview-capacity-counts">
          {groups.map((group) => (
            <li key={group.id}>
              <strong>{group.name}</strong>
              <div>
                <span className="overview-count" data-state="todo">
                  {group.todo} to do
                </span>
                <span className="overview-count" data-state="in_progress">
                  {group.in_progress} in progress
                </span>
                <span className="overview-count" data-state="completed">
                  {group.completed} done
                </span>
                {!!group.blocked && (
                  <span className="overview-count" data-state="blocked">
                    {group.blocked} blocked
                  </span>
                )}
                {!!group.paused && (
                  <span className="overview-count" data-state="paused">
                    {group.paused} paused
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p>No task cards yet.</p>
      )}
    </>
  );
}

/** The same selected-day canonical projection powers celebration and wellbeing. */
export function RecordedActivity({activity, day, showCompleted = true, userOnly = false}: {activity?: Data; day: string; showCompleted?: boolean; userOnly?: boolean}) {
  if (!activity || activity.date !== day) return <p>Recorded activity is loading.</p>;
  const owned = (rows: Data[]) => userOnly ? rows.filter(item => item.owner === "user") : rows;
  const groups = [
    ...(showCompleted ? [{label: "Completed commitments", rows: owned(activity.items || [])}] : []),
    {label: "Tasks moved into progress", rows: owned(activity.activity || []).filter((item: Data) => item.action === "started")},
    {label: "Subtasks moved into progress", rows: owned(activity.activity || []).filter((item: Data) => item.action === "subtask_started")},
    {label: "Completed subtasks", rows: owned(activity.activity || []).filter((item: Data) => item.action === "subtask_completed")},
  ];
  return <div className="recorded-activity">{groups.map(group => <section key={group.label} aria-label={group.label}>
    <p><strong>{group.rows.length}</strong> {group.label.toLowerCase()}</p>
    {!!group.rows.length && <ul>{group.rows.map((item: Data) => <li key={item.id}><span aria-hidden="true">{String(item.action || "").endsWith("started") ? "🌱" : "✓"} </span><span>{item.title}</span>{item.parentTitle && <small> · {item.parentTitle}</small>}<small> · {item.owner === "user" ? "You" : item.owner === "leam" ? "Leam" : "Owner unspecified"}</small>{(item.source === "user_requested_backfill" || item.completionEvidence === "user_requested_backfill") && <small> · Date assigned at your request</small>}</li>)}</ul>}
  </section>)}</div>;
}

export function ActivityRefresh({refresh, refreshing, label}: {refresh: () => void; refreshing: boolean; label: string}) {
  return <button type="button" className="secondary" onClick={refresh} disabled={refreshing} aria-label={label}>
    <RefreshCw size={16} aria-hidden="true" /> {refreshing ? "Refreshing…" : "Refresh"}
  </button>;
}
