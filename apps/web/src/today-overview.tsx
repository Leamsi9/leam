import {
  Celebration,
  OverviewCard,
  TaskCapacitySummary,
} from "./overview-summary";
import type { ReactNode } from "react";
import {
  ArrowUpRight,
  CalendarDays,
  Check,
  CircleHelp,
  Compass,
  Inbox,
  LayoutGrid,
  MessageCircle,
  Plus,
  Target,
} from "lucide-react";
import type { Data } from "./api";
import type { TodayPage } from "./today-tab-order";
import "./today-overview.css";
import { useTodayPriorities } from "./today-priorities";

type Suggestion = {
  item: Data;
  kind: "task" | "habit" | "appointment" | "email";
  reason: string;
  rank: number;
  time: number;
};
const excluded = new Set([
  "completed",
  "paused",
  "cancelled",
  "canceled",
  "declined",
  "ignored",
  "dismissed",
]);
function eligible(item: Data) {
  return (
    !excluded.has(item.status) &&
    !item.visibility?.hidden &&
    !item.hidden &&
    !item.log?.done &&
    [undefined, "none"].includes(item.triage?.disposition)
  );
}
/** Local, explainable shortlist of saved items; never mutates or invokes a model. */
export function overviewSuggestions(
  agenda: Data | null,
  date: string,
): Suggestion[] {
  if (!agenda) return [];
  const result: Suggestion[] = [];
  for (const item of (agenda.commitments || []).slice(0, 500)) {
    if (!eligible(item) || item.kind !== "task" || item.stage === "blocked") continue;
    const kind = "task";
    const due =
      typeof item.dueDate === "string" &&
      /^\d{4}-\d{2}-\d{2}$/.test(item.dueDate)
        ? item.dueDate
        : null;
    const time = due ? Date.parse(due + "T12:00:00Z") : Number.MAX_SAFE_INTEGER;
    if (due && due < date)
      result.push({
        item,
        kind,
        rank: 0,
        time,
        reason: `Overdue · due ${due}`,
      });
    else if (due === date)
      result.push({ item, kind, rank: 2, time, reason: "Due on this day" });
    else if (["urgent", "high"].includes(item.priority))
      result.push({
        item,
        kind,
        rank: 3,
        time,
        reason: `${item.priority === "urgent" ? "Urgent" : "High"} saved priority`,
      });
    else if (item.startDate === date)
      result.push({
        item,
        kind,
        rank: 5,
        time,
        reason: "Scheduled to start on this day",
      });
    else result.push({item, kind, rank: 7, time, reason: item.stage === "in_progress" ? "In progress" : "Pending task · no deadline recorded"});
  }
  const seen = new Set<string>();
  return result
    .sort(
      (a, b) =>
        a.rank - b.rank ||
        a.time - b.time ||
        String(a.item.key).localeCompare(String(b.item.key)),
    )
    .filter(({ item }) => {
      if (!item.key || seen.has(item.key)) return false;
      seen.add(item.key);
      return true;
    })
    .slice(0, 5);
}
function coverage(value: unknown) {
  return typeof value === "string" ? value.replaceAll("_", " ") : "unknown";
}
export function TodayOverview({
  agenda,
  date,
  timezone,
  boardCount,
  capacities,
  commitments,
  inboxUnread,
  focus,
  checks,
  focusCount,
  loading,
  refresh,
  navigate,
  inspect,
  chooseFocus,
  busy,
  add,
}: {
  agenda: Data | null;
  date: string;
  timezone: string;
  boardCount: number | null;
  capacities: Data[];
  commitments: Data[] | null;
  inboxUnread: number | null;
  focus: ReactNode;
  checks?: ReactNode;
  focusCount: number;
  loading: boolean;
  refresh: () => void;
  navigate: (page: TodayPage) => void;
  inspect: (item: Data, kind: Suggestion["kind"]) => void;
  chooseFocus: (item: Data) => void;
  busy: (item: Data) => boolean;
  add: () => void;
}) {
  const priorities = useTodayPriorities(date, timezone, capacities);
  const canonical = [...(agenda?.commitments || []), ...(agenda?.events || []), ...(agenda?.emails || [])];
  const saved = priorities.record?.state === "completed";
  const suggestions: Suggestion[] = saved
    ? (priorities.record?.suggestions || []).flatMap((choice: Data) => {
        if (choice.kind !== "task") return [];
        const item = choice.entityId ? choice.currentItem : canonical.find(item => item.key === choice.key);
        if (priorities.excludedKeys.includes(choice.key)) return [];
        return item && item.kind === "task" && eligible(item) && item.stage !== "blocked" && (choice.revision == null || choice.revision === item.revision)
          ? [{ item, kind: choice.kind, reason: choice.reason, rank: 0, time: 0 }] : [];
      })
    : overviewSuggestions(agenda, date).filter(choice => !priorities.excludedKeys.includes(choice.item.key));
  const missingSaved = saved && suggestions.length < (priorities.record?.suggestions || []).length;
  const formattedDay = new Date(date + "T12:00:00").toLocaleDateString(
    undefined,
    { weekday: "long", month: "long", day: "numeric" },
  );
  const events = (agenda?.events || []).filter(
    (item: Data) => !item.visibility?.hidden && !excluded.has(item.status),
  );
  const mail = (agenda?.emails || []).filter(
    (item: Data) => eligible(item) && item.actionability?.state === "action",
  );
  const observed =
    typeof agenda?.observedAt === "number"
      ? new Date(agenda.observedAt * 1000)
      : null;
  const partial = !!agenda?.partial || agenda?.nextOffset != null;
  const destinations = [
    {
      id: "schedule" as const,
      title: "Schedule",
      subtitle: "Make space in your day",
      icon: CalendarDays,
      tone: "blue",
      detail: agenda
        ? `${events.length} appointments loaded`
        : "Calendar view unavailable",
      note: `Calendar · ${coverage(agenda?.sources?.calendar?.state)}`,
    },
    {
      id: "inbox" as const,
      title: "Inbox",
      subtitle: "See what needs a reply",
      icon: Inbox,
      tone: "coral",
      detail:
        inboxUnread === null
          ? "Unread count unavailable"
          : `${inboxUnread} unread saved items`,
      note: agenda
        ? `${mail.length} additional actionable mail items loaded · mail ${coverage(agenda.sources?.email?.state)}`
        : "Mail coverage unknown",
    },
    {
      id: "boards" as const,
      title: "Tasks",
      subtitle: "See progress across your capacities",
      icon: LayoutGrid,
      tone: "purple",
      detail:
        boardCount === null
          ? "Capacity count unavailable"
          : `${boardCount} ${boardCount === 1 ? "capacity" : "capacities"}`,
      note: "All commitments, subtasks and statuses",
    },
    {
      id: "chat" as const,
      title: "Chat",
      subtitle: "Think it through with Leam",
      icon: MessageCircle,
      tone: "green",
      detail: "Priorities, trade-offs, or a fresh start",
      note: `Conversation for ${date}`,
    },
  ];
  return (
    <div className="today-overview">
      <Celebration agenda={agenda} date={date} refresh={refresh} refreshing={loading} />
      <OverviewCard
        id="welcome"
        title={formattedDay}
        className="overview-welcome"
        icon={<Compass size={18} aria-hidden="true" />}
      >
        <header className="overview-welcome-content">
          <div>
            <p className="overview-eyebrow">
              <Compass size={16} aria-hidden="true" /> {formattedDay}
            </p>
            <h2>A little clarity. A little breathing room.</h2>
            <p>Your priorities, with the rest of your day one step away.</p>
          </div>
          <button type="button" className="secondary" onClick={add}>
            <Plus size={17} aria-hidden="true" /> Add commitment
          </button>
        </header>
      </OverviewCard>
      <div className="overview-main-grid">
        <OverviewCard id="focus" title="Your Focus" className="overview-focus">
          {focus}
        </OverviewCard>
        <OverviewCard
          id="priorities"
          title="Suggested next priorities"
          className="overview-next"
          icon={<Target size={19} aria-hidden="true" />}
        >
          <section aria-label="Suggested next priorities">
            <header className="overview-section-heading">
              <span className="overview-symbol">
                <Target size={19} aria-hidden="true" />
              </span>
              <div>
                <p>
                  Up to five, outside your {focusCount ? "chosen " : ""}Focus.
                  You decide.
                </p>
              </div>
            </header>
            {priorities.controls}
            {missingSaved && <p role="status">Some saved suggestions changed or are outside this loaded view. Run triage again after refreshing the day.</p>}
            {suggestions.length ? (
              <ol className="overview-shortlist">
                {suggestions.map(({ item, kind, reason }, index) => (
                  <li key={item.key} data-kind={kind}>
                    <span className="overview-rank" aria-hidden="true">
                      {index + 1}
                    </span>
                    <button
                      type="button"
                      className="overview-item"
                      onClick={() => inspect(item, kind)}
                    >
                      <small>
                        {kind === "appointment"
                          ? "Calendar · appointment"
                          : kind === "email"
                            ? "Inbox · email action"
                            : `${kind === "habit" ? "Habit" : "Task"} · ${item.owner === "leam" ? "Leam" : item.owner === "user" ? "You" : "Owner unspecified"}`}
                      </small>
                      <strong>
                        {item.title || item.subject || "Untitled saved item"}
                      </strong>
                      <span>{reason}</span>
                    </button>
                    <button
                      type="button"
                      className="overview-focus-action"
                      disabled={busy(item) || item.focusEligible === false}
                      onClick={() => chooseFocus(item)}
                      aria-label={`Focus ${item.title || item.subject || "saved item"}`}
                      title={item.focusEligible === false ? "Outside this day’s scheduled window; open the task to adjust its dates" : "Add to your chosen Focus"}
                    >
                      <Plus size={18} aria-hidden="true" />
                    </button>
                    <button type="button" className="overview-exclude-action" disabled={!!priorities.excluding}
                      aria-label={`Exclude ${item.title || item.subject || "suggestion"} for this day`}
                      onClick={() => void priorities.exclude(item.key)} title="Exclude for this day only">×</button>
                  </li>
                ))}
              </ol>
            ) : (
              <div className="overview-quiet">
                <Check size={22} aria-hidden="true" />
                <p>
                  {!agenda
                    ? loading
                      ? "Your saved view is loading. You can still explore below."
                      : "Suggestions are unavailable until your saved view returns."
                    : "No additional priorities stand out in this saved view."}
                </p>
                {agenda && (
                  <small>
                    Your chosen Focus stays yours. Explore a section when you’re
                    ready.
                  </small>
                )}
              </div>
            )}
            <details className="overview-basis">
              <summary>
                <CircleHelp size={15} aria-hidden="true" /> Why these
                suggestions?
              </summary>
              <p>
                Tasks only, ordered from recorded deadlines, priority and scheduled starts. Email, appointments, habits and goals stay in their own sections. No model call, no new reminder, and no
                changes to your Focus.
              </p>
              <p>
                {partial
                  ? "Only loaded items are considered; more may be available below."
                  : agenda
                    ? "Based on the loaded saved view, not a live scan of your accounts."
                    : "No saved agenda data is available."}
              </p>
              <p>
                Calendar: {coverage(agenda?.sources?.calendar?.state)} · Mail:{" "}
                {coverage(agenda?.sources?.email?.state)}.{" "}
                {observed &&
                  `View read ${observed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}; source sync may be older.`}{" "}
                {timezone}.
              </p>
            </details>
          </section>
        </OverviewCard>
      </div>
      <section className="overview-spaces" aria-label="Explore your day">
        <div className="overview-section-heading">
          <div>
            <h2>The rest of your day</h2>
            <p>Different views. The same commitments and conversations.</p>
          </div>
        </div>
        <div className="overview-destinations">
          {destinations.map(
            ({ id, title, subtitle, icon: Icon, tone, detail, note }) => (
              <OverviewCard
                id={id}
                key={id}
                title={title}
                icon={<Icon size={21} aria-hidden="true" />}
                className={`overview-destination overview-tone-${tone}`}
              >
                <p>{subtitle}</p>
                <strong>{detail}</strong>
                {id === "boards" ? (
                  <TaskCapacitySummary
                    capacities={capacities}
                    commitments={commitments}
                  />
                ) : (
                  <small>{note}</small>
                )}
                <button
                  type="button"
                  className="secondary overview-open"
                  onClick={() => navigate(id)}
                  aria-label={`Open ${title}`}
                >
                  Open {title} <ArrowUpRight size={18} aria-hidden="true" />
                </button>
              </OverviewCard>
            ),
          )}
        </div>
      </section>
      <details className="overview-card overview-conversation-checks">
        <summary><CircleHelp size={20} aria-hidden="true" /><h2>Conversation check details</h2></summary>
        <div className="overview-card-body">
          <p>These checks look for commitments in completed Today chat exchanges. They are separate from priority triage. Failed checks do not confirm any changes; open a check to inspect or retry it.</p>
          {checks}
        </div>
      </details>
    </div>
  );
}
