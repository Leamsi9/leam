import { useTodayActivityEvents } from "./today-activity-events";
import { Wellbeing } from "./wellbeing";
import { InboxPage } from "./inbox";
import { InboxBadge, useInboxStatus } from "./inbox-status";
import { useEffect, useRef, useState } from "react";
import {
  Heart,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  ListChecks,
  Inbox,
  MessageCircle,
  MoreHorizontal,
  LayoutGrid,
} from "lucide-react";
import { api, type Data } from "./api";
import { sessionValue, rememberSession } from "./session-cache";
import {
  CommitmentCard,
  CommitmentForm,
  type EditorDraft,
} from "./commitment-components";
import { RemovalDialog } from "./removal";
import { ReminderInbox } from "./reminders";
import { ChatDialog } from "./chat-dialog";
import { RoutineInbox } from "./routines";
import "./today.css";
import { TodayOverview } from "./today-overview";
import { AgendaChat } from "./agenda-chat";
import { MailBrowser } from "./mail-browser";
import { CapacityBoards } from "./capacity-boards";
import { type CanonicalChange } from "./canonical-change";
import {
  TodayTabOrder,
  useTodayTabOrder,
  useTodayTabDrag,
  type TodayPage,
} from "./today-tab-order";
import { EmailTriage } from "./email-triage";
import { HideCalendarEvent, HiddenCalendarEvents } from "./calendar-visibility";
import {
  rebuildAgendaMail,
  syncAgendaCalendars,
  syncAgendaMail,
} from "./agenda-sync";

function localDay(value = new Date()) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}
function adjacent(date: string, step: number) {
  const value = new Date(date + "T12:00:00");
  value.setDate(value.getDate() + step);
  return localDay(value);
}
function navigate(view: string) {
  window.dispatchEvent(new CustomEvent("leam:navigate", { detail: view }));
}
const pages = [
  { id: "plan", label: "Overview", icon: ListChecks },
  { id: "schedule", label: "Schedule", icon: CalendarDays },
  { id: "inbox", label: "Inbox", icon: Inbox },
  { id: "chat", label: "Chat", icon: MessageCircle },
  { id: "boards", label: "Boards", icon: LayoutGrid },
  { id: "wellbeing", label: "Wellbeing", icon: Heart },
] as const;
type TodayRoute = { page: TodayPage; date: string };
function validRoute(value: unknown): value is TodayRoute {
  if (!value || typeof value !== "object") return false;
  const route = value as TodayRoute;
  return pages.some(page => page.id === route.page) && typeof route.date === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(route.date) &&
    !Number.isNaN(Date.parse(route.date + "T12:00:00Z")) &&
    new Date(route.date + "T12:00:00Z").toISOString().slice(0, 10) === route.date;
}
function savedRoute(): TodayRoute | null {
  const value = sessionValue<unknown>("today:route", null);
  return validRoute(value) ? value : null;
}
function readLocation(): TodayRoute | null {
  if (location.hash === "#today/inbox") return { page: "inbox", date: savedRoute()?.date || localDay() };
  const match = location.hash.match(
    /^#today\/(plan|schedule|inbox|chat|boards|wellbeing)\/(\d{4}-\d{2}-\d{2})$/,
  );
  const value = match ? { page: match[1], date: match[2] } : null;
  return validRoute(value) ? value : null;
}
export function Today({ fail }: { fail: (error: unknown) => void }) {
  const inboxStatus = useInboxStatus();
  const inboxUnread = inboxStatus?.unreadCount || 0;
  const [route, setRoute] = useState(
    () => readLocation() || savedRoute() || { page: "plan" as TodayPage, date: localDay() },
  );
  const { date, page } = route;
  const [chatDates, setChatDates] = useState<string[]>(() => {
    const saved = sessionValue<unknown>("today:chatDates", []);
    return Array.isArray(saved) ? saved.filter(date => validRoute({ page: "chat", date })).slice(0, 5) : [];
  });
  useEffect(() => {
    rememberSession("today:route", route);
    if (page === "chat") setChatDates(previous => {
      const next = [date, ...previous.filter(item => item !== date)].slice(0, 5);
      rememberSession("today:chatDates", next);
      return next;
    });
  }, [date, page]);
  const previousChatDate = chatDates.find(item => item !== date);
  const tabOrder = useTodayTabOrder();
  const tabDrag = useTodayTabDrag(tabOrder);
  function visit(nextPage: TodayPage, nextDate = date) {
    const hash = `#today/${nextPage}/${nextDate}`;
    if (location.hash !== hash) history.pushState(history.state, "", hash);
    setRoute({ page: nextPage, date: nextDate });
  }
  function setDate(next: string | ((current: string) => string)) {
    visit(page, typeof next === "function" ? next(date) : next);
  }
  useEffect(() => {
    if (!location.hash.startsWith("#today/"))
      history.replaceState(
        history.state,
        "",
        `#today/${route.page}/${route.date}`,
      );
    const restore = () => {
      const next = readLocation();
      if (next) setRoute(next);
    };
    window.addEventListener("popstate", restore);
    window.addEventListener("hashchange", restore);
    return () => {
      window.removeEventListener("popstate", restore);
      window.removeEventListener("hashchange", restore);
    };
  }, []);
  const [remindersOpen, setRemindersOpen] = useState(false);
  const [timezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/London",
  );
  const [savedAgenda, setAgenda] = useState<Data | null>(null);
  const agenda = savedAgenda?.date === date ? savedAgenda : null;
  const [capacities, setCapacities] = useState<Data[]>([]);
  const [capacitiesKnown, setCapacitiesKnown] = useState(false);
  const [overviewCommitments, setOverviewCommitments] = useState<Data[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncNotice, setSyncNotice] = useState("");
  const [mailOperation, setMailOperation] = useState<"sync" | "rebuild" | null>(
    null,
  );
  const [mailNotice, setMailNotice] = useState("");
  const syncRequest = useRef<AbortController | null>(null);
  const [error, setError] = useState("");
  const [focusNotice, setFocusNotice] = useState("");
  const focusSection = useRef<HTMLElement | null>(null);
  const [busy, setBusy] = useState<string[]>([]);
  const [editor, setEditor] = useState<Data | null | undefined>(undefined);
  const [removing, setRemoving] = useState<string | null>(null);
  const drafts = useRef<Record<string, EditorDraft>>({});
  const sequence = useRef(0),
    active = useRef(true),
    currentDay = useRef(date),
    pending = useRef(new Set<string>());
  currentDay.current = date;
  const capacitySequence = useRef(0);
  const dirty = useRef({ agenda: false, capacities: true });
  const acknowledged = useRef(new Map<string, number>());
  const [changeVersion, setChangeVersion] = useState(0);
  const [boardVersion, setBoardVersion] = useState(0);
  function invalidate(change: CanonicalChange, fromBoard = false) {
    if (change.kind !== "domain") {
      const key = `${change.kind}:${change.record.id}`;
      if ((acknowledged.current.get(key) || 0) >= change.record.revision)
        return;
      acknowledged.current.set(key, change.record.revision);
    }
    // Fence reads issued before this receipt immediately, including hidden pages.
    ++sequence.current;
    dirty.current.agenda = true;
    // The Overview task summary shares these canonical records with Boards.
    ++capacitySequence.current;
    dirty.current.capacities = true;
    if (!fromBoard) setBoardVersion((value) => value + 1);
    setChangeVersion((value) => value + 1);
  }
  useTodayActivityEvents(() => invalidate({ kind: "domain", source: "inspect" }));
  async function canonicalChanged() {
    invalidate({ kind: "domain", source: "confirmed" });
  }
  async function loadCapacities() {
    const n = ++capacitySequence.current;
    dirty.current.capacities = false;
    try {
      const [result, cards] = await Promise.allSettled([api("/capacities"), api("/commitments")]);
      if (!active.current || n !== capacitySequence.current) return;
      if (result.status === "fulfilled") {
        setCapacities(result.value.items);
        setCapacitiesKnown(true);
      } else {
        setCapacitiesKnown(false);
        dirty.current.capacities = true;
        fail(result.reason);
      }
      if (cards.status === "fulfilled") setOverviewCommitments(cards.value.items);
      else {
        setOverviewCommitments(null);
        dirty.current.capacities = true;
        fail(cards.reason);
      }
    } catch (e) {
      if (active.current && n === capacitySequence.current) {
        dirty.current.capacities = true;
        setOverviewCommitments(null);
        fail(e);
      }
    }
  }
  async function load(more = false) {
    if (page === "plan" && dirty.current.capacities) void loadCapacities();
    const requestedDate = currentDay.current;
    const offset = more ? agenda?.nextOffset : 0;
    if (more && offset == null) return;
    const n = ++sequence.current;
    dirty.current.agenda = false;
    setLoading(true);
    setError("");
    try {
      const next = await api(
        `/agenda?date=${requestedDate}&timezone=${encodeURIComponent(timezone)}&offset=${offset}`,
      );
      if (
        !active.current ||
        n !== sequence.current ||
        requestedDate !== currentDay.current
      )
        return;
      setAgenda((previous) =>
        more && previous && previous.date === next.date
          ? {
              ...next,
              commitments: [
                ...new Map(
                  [...previous.commitments, ...next.commitments].map((item) => [
                    item.key,
                    item,
                  ]),
                ).values(),
              ],
              emails: [
                ...new Map(
                  [...(previous.emails || []), ...(next.emails || [])].map(
                    (item) => [item.key, item],
                  ),
                ).values(),
              ],
              events: [
                ...new Map(
                  [...previous.events, ...next.events].map((item) => [
                    item.key,
                    item,
                  ]),
                ).values(),
              ],
            }
          : next,
      );
    } catch (e) {
      if (active.current && n === sequence.current) {
        dirty.current.agenda = true;
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (active.current && n === sequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    active.current = true;
    setSyncing(false);
    setSyncNotice("");
    setMailNotice("");
    setMailOperation(null);
    setFocusNotice("");
    setAgenda(null);
    void load();
    return () => {
      active.current = false;
      syncRequest.current?.abort();
      syncRequest.current = null;
      sequence.current++;
    };
  }, [date, timezone]);
  useEffect(() => {
    // Local navigation consumes invalidations, not full rereads of clean pages.
    // One timer coalesces receipts emitted together; no hidden-page polling.
    const timer = setTimeout(() => {
      if (["plan", "schedule", "inbox", "wellbeing"].includes(page) && dirty.current.agenda)
        void load();
      if (page === "plan" && dirty.current.capacities) void loadCapacities();
    }, 0);
    return () => clearTimeout(timer);
  }, [page, changeVersion]);
  useEffect(
    () => () => {
      ++capacitySequence.current;
    },
    [],
  );
  async function sync(kind: "calendar" | "mail" | "rebuild" = "calendar") {
    if (syncRequest.current || !agenda) return;
    if (
      kind === "rebuild" &&
      !window.confirm(
        "Rebuild Leam's action inbox? This resets saved mail classifications and your local mail review choices, then reads up to 100 recent messages per connected Gmail account. Gmail is unchanged.",
      )
    )
      return;
    const controller = new AbortController(),
      requestedDate = date;
    syncRequest.current = controller;
    setSyncing(true);
    setMailOperation(
      kind === "calendar" ? null : kind === "mail" ? "sync" : "rebuild",
    );
    const setNotice = kind !== "calendar" ? setMailNotice : setSyncNotice;
    setNotice("");
    const current = () =>
      active.current &&
      currentDay.current === requestedDate &&
      syncRequest.current === controller;
    try {
      const notice =
        kind === "mail"
          ? await syncAgendaMail(controller.signal)
          : kind === "rebuild"
            ? await rebuildAgendaMail(controller.signal)
            : await syncAgendaCalendars(agenda.window, controller.signal);
      if (current()) {
        setNotice(notice);
        await load();
      }
    } catch (e) {
      if (current()) {
        setNotice(e instanceof Error ? e.message : String(e));
        await load();
      }
    } finally {
      if (current()) {
        syncRequest.current = null;
        setSyncing(false);
        setMailOperation(null);
      }
    }
  }
  async function triage(item: Data, disposition: string) {
    const requestedDate = date,
      key = date + ":" + item.key;
    if (pending.current.has(key)) return;
    pending.current.add(key);
    setError("");
    setFocusNotice("");
    setBusy([...pending.current]);
    try {
      const receipt = await api("/agenda/triage", "PUT", {
        date,
        timezone,
        key: item.key,
        revision: item.triage.revision,
        disposition,
      });
      if (active.current && currentDay.current === requestedDate) {
        setAgenda(
          (previous) =>
            previous && {
              ...previous,
              commitments: previous.commitments.map((value: Data) =>
                value.key === item.key &&
                value.triage.revision <= receipt.revision
                  ? { ...value, triage: receipt }
                  : value,
              ),
              emails: (previous.emails || []).map((value: Data) =>
                value.key === item.key &&
                value.triage.revision <= receipt.revision
                  ? { ...value, triage: receipt }
                  : value,
              ),
              events: previous.events.map((value: Data) =>
                value.key === item.key &&
                value.triage.revision <= receipt.revision
                  ? { ...value, triage: receipt }
                  : value,
              ),
            },
        );
        const title = item.title || item.subject || "Item";
        setFocusNotice(
          disposition === "focus"
            ? `${title} added to Focus for ${requestedDate}.`
            : item.triage.disposition === "focus"
              ? `${title} removed from Focus for ${requestedDate}.`
              : `${title}: daily plan saved for ${requestedDate}.`,
        );
        if (disposition === "focus") {
          visit("plan", requestedDate);
          requestAnimationFrame(() => {
            if (!active.current || currentDay.current !== requestedDate) return;
            focusSection.current?.scrollIntoView({ block: "start" });
            focusSection.current?.focus({ preventScroll: true });
          });
        }
        // Reload canonical priority-first pagination; never retry the decision.
        await load();
      }
    } catch (e) {
      if (active.current && currentDay.current === requestedDate) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      pending.current.delete(key);
      if (active.current) setBusy([...pending.current]);
    }
  }
  const choices = (item: Data) => (
    <div
      className="today-row-actions"
      aria-label={`Plan ${item.title || item.subject}`}
    >
      <button
        className="secondary today-focus-action"
        disabled={busy.includes(date + ":" + item.key)}
        aria-pressed={item.triage.disposition === "focus"}
        onClick={() =>
          void triage(
            item,
            item.triage.disposition === "focus" ? "none" : "focus",
          )
        }
      >
        {item.triage.disposition === "focus" ? "Remove from focus" : "Focus"}
      </button>
      <details className="today-row-menu">
        <summary aria-label={`More actions for ${item.title || item.subject}`}>
          <MoreHorizontal size={18} />
        </summary>
        <div className="today-row-menu-body">
          {(
            [
              ["later", "Later today"],
              ["dismissed", "Hide for today"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              className="secondary"
              disabled={busy.includes(date + ":" + item.key)}
              aria-pressed={item.triage.disposition === value}
              onClick={() =>
                void triage(
                  item,
                  item.triage.disposition === value ? "none" : value,
                )
              }
            >
              {label}
            </button>
          ))}
        </div>
      </details>
    </div>
  );
  const commitment = (item: Data) => (
    <div key={item.key} className="agenda-commitment">
      <CommitmentCard
        compact
        item={item}
        capacity={capacities.find((c) => c.id === item.capacityId)}
        changed={canonicalChanged}
        edit={() => setEditor(item)}
        fail={fail}
      />
      {choices(item)}
    </div>
  );
  const event = (item: Data) => (
    <article className="today-row agenda-event" key={item.key}>
      <div className="today-event-time">
        <CalendarDays size={16} />
        <time>
          {item.allDay
            ? "All day"
            : `${new Date(item.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: timezone })}–${new Date(item.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: timezone })}`}
        </time>
      </div>
      <div className="today-row-main">
        <h3>{item.title}</h3>
        {item.triage.disposition === "focus" && <small>In Focus</small>}
        {item.triage.disposition === "later" && (
          <small>Later today · Calendar time unchanged</small>
        )}
        {item.url && (
          <a
            className="today-source-link"
            href={item.url}
            target="_blank"
            rel="noreferrer"
          >
            Open calendar event
          </a>
        )}
      </div>
      {choices(item)}
      <details className="today-row-details">
        <summary>Event details</summary>
        <HideCalendarEvent item={item} changed={load} />
      </details>
    </article>
  );
  const commitments: Data[] = agenda?.commitments || [],
    emails: Data[] = (agenda?.emails || []).filter(
      (item: Data) => item.actionability?.state === "action",
    ),
    events: Data[] = (agenda?.events || [])
      .filter((item: Data) => !item.visibility?.hidden)
      .sort(
        (a: Data, b: Data) =>
          Number(Boolean(b.allDay)) - Number(Boolean(a.allDay)) ||
          Date.parse(a.start) - Date.parse(b.start),
      );
  const done = (item: Data) =>
    item.log !== undefined &&
    (item.kind === "task" ? item.status === "completed" : item.log.done);
  const focus = [...events, ...emails, ...commitments].filter(
    (item) => !done(item) && item.triage.disposition === "focus",
  );
  const email = (item: Data) => (
    <article className="today-row agenda-email" key={item.key}>
      <div className="today-row-main">
        <small>
          {item.from}
          {item.unread ? " · Unread" : ""}
        </small>
        <h3>{item.subject || "(No subject)"}</h3>
        {item.actionability?.action && (
          <p className="agenda-email-action">{item.actionability.action}</p>
        )}
        {item.url && (
          <a
            className="today-source-link"
            href={item.url}
            target="_blank"
            rel="noreferrer"
          >
            Open email in Gmail
          </a>
        )}
      </div>
      {choices(item)}
      <details className="today-row-details">
        <summary>Message details</summary>
        <small>
          {new Date(item.receivedAt).toLocaleDateString()}
          {item.actionability?.reviewedBy === "user"
            ? " · You added this · Local triage decision"
            : " · Suggested action"}
        </small>
        {item.actionability?.reason && <p>{item.actionability.reason}</p>}
        {item.snippet && <p>{item.snippet}</p>}
      </details>
    </article>
  );
  const render = (item: Data) =>
    item.eventId !== undefined
      ? event(item)
      : item.log !== undefined
        ? commitment(item)
        : email(item);
  const mailAvailable = (agenda?.sources.email.accounts || []).some(
    (account: Data) =>
      account.granted === true &&
      ["never_synced", "ready", "stale", "error"].includes(account.state),
  );
  return (
    <section className={`page daily-agenda today-app today-page-${page}`}>
      <div className="today-app-chrome">
        <header className="today-app-header">
          <h1>Today</h1>
          <div className="today-date-controls">
            <button
              className="icon-button"
              aria-label="Previous day"
              onClick={() => setDate((value) => adjacent(value, -1))}
            >
              <ChevronLeft size={18} />
            </button>
            <input
              aria-label="Viewing date"
              type="date"
              value={date}
              onChange={(e) => {
                if (e.target.value) setDate(e.target.value);
              }}
            />
            <button
              className="icon-button"
              aria-label="Next day"
              onClick={() => setDate((value) => adjacent(value, 1))}
            >
              <ChevronRight size={18} />
            </button>
          </div>
          {previousChatDate && <button type="button" className="secondary" onClick={() => visit("chat", previousChatDate)}
            aria-label={`Resume Today chat from ${previousChatDate}`} title="Return to the earlier conversation; each date has its own chat.">
            Chat · {previousChatDate}
          </button>}
          <ChatDialog label="Day options" icon={<MoreHorizontal size={20} />}>
            <p>
              {date} · {timezone}
            </p>
            <div className="actions">
              <button
                className="secondary"
                aria-label="Go to today"
                onClick={() => setDate(localDay())}
              >
                Go to today
              </button>
              <button
                className="secondary"
                disabled={loading}
                onClick={() => void load()}
              >
                Refresh saved view
              </button>
            </div>
            <TodayTabOrder {...tabOrder} />
          </ChatDialog>
        </header>
        <nav className="today-navigation" aria-label="Today pages">
          {tabOrder.order
            .map((value) => pages.find((item) => item.id === value)!)
            .map(({ id, label, icon: Icon }) => (
              <a
                key={id}
                data-page={id}
                {...tabDrag.props(id)}
                data-dragging={tabDrag.source === id || undefined}
                data-drop-target={tabDrag.target === id && tabDrag.source !== id || undefined}
                href={`#today/${id}/${date}`}
                aria-current={page === id ? "page" : undefined}
                onClick={(e) => {
                  if (
                    !e.ctrlKey &&
                    !e.metaKey &&
                    !e.shiftKey &&
                    !e.altKey &&
                    e.button === 0
                  ) {
                    e.preventDefault();
                    visit(id);
                  }
                }}
              >
                <Icon size={18} />
                <span>{label}{id === "inbox" && <InboxBadge count={inboxUnread} />}</span>
              </a>
            ))}
        </nav>
        <span className="today-tab-order-status" role="status">{tabOrder.notice}</span>
      </div>
      {error && (
        <p role="alert">
          {error} {agenda && "Your previous saved view is still shown."}
        </p>
      )}
      <MailBrowser active={page === "inbox"} />
      <CapacityBoards
        active={page === "boards"}
        refreshVersion={boardVersion}
        onChanged={(change) => invalidate(change, true)}
      />
      {page !== "chat" && page !== "boards" && (
        <div className="today-page-body" key={page}>
          {page === "wellbeing" && <Wellbeing key={date} day={date} timezone={timezone} activity={agenda?.accomplishments} refresh={() => void load()} refreshing={loading} />}
          {page === "inbox" && <InboxPage changed={() => void canonicalChanged()} mail={emails} accounts={agenda?.sources?.email?.accounts || []} mailActions={choices}
            controls={<button className="secondary" disabled={syncing || !mailAvailable} onClick={() => void sync("mail")}>
              {mailOperation === "sync" ? "Syncing mail…" : "Sync mail"}</button>} />}
          {loading && !agenda && <p role="status">Loading your day…</p>}
          {page === "plan" && <TodayOverview
            checks={<AgendaChat date={date} timezone={timezone} active={false} checksActive changed={() => void load()} canonicalChanged={canonicalChanged} />}
            agenda={agenda} date={date} timezone={timezone}
            boardCount={capacitiesKnown ? capacities.length : null}
            capacities={capacities} commitments={overviewCommitments}
            inboxUnread={inboxStatus?.unreadCount ?? null}
            focusCount={focus.length} loading={loading} navigate={visit} refresh={() => void load()}
            inspect={(item, kind) => kind === "task" || kind === "habit" ? setEditor(item) : visit(kind === "appointment" ? "schedule" : "inbox")}
            chooseFocus={(item) => void triage(item, "focus")}
            busy={(item) => busy.includes(date + ":" + item.key)}
            add={() => setEditor(null)}
            focus={(
                  <section
                    aria-label="Daily focus"
                    className="today-focus-list"
                    ref={focusSection}
                    tabIndex={-1}
                  >
                    <div className="today-section-title">
                      <h2>
                        Focus <span>{focus.length}</span>
                      </h2>
                      <time dateTime={date}>{date}</time>
                    </div>
                    {focusNotice && (
                      <p className="agenda-focus-notice" role="status">
                        {focusNotice}
                      </p>
                    )}
                    {!agenda ? <p className="today-empty">Your saved Focus is unavailable. Try refreshing the saved view.</p> : focus.length ? (
                      <div className="today-list">{focus.map(render)}</div>
                    ) : (
                      <div className="today-empty">
                        <h3>Your day, with a little breathing room.</h3>
                        <p>
                          Choose Focus on a commitment, event or actionable
                          email to bring it here.
                        </p>
                        <div className="actions">
                          <button
                            className="secondary"
                            onClick={() => visit("schedule")}
                          >
                            Browse schedule
                          </button>
                          <button
                            className="secondary"
                            onClick={() => visit("chat")}
                          >
                            Plan with Leam
                          </button>
                        </div>
                      </div>
                    )}
                    {agenda?.nextOffset != null && (
                      <p className="agenda-hint">
                        Showing loaded priorities. More items may be available
                        below.
                      </p>
                    )}
                  </section>
            )}
          />}
          {agenda && (
            <>
              {page === "schedule" && (
                <>
                  <div className="today-page-intro">
                    <div>
                      <small className="today-eyebrow">Your time</small>
                      <h2>The shape of your day.</h2>
                      <p>
                        Calendar appointments, in {timezone}. Focus and Later
                        keep their original time.
                      </p>
                    </div>
                    <button
                      className="secondary"
                      disabled={syncing}
                      onClick={() => void sync()}
                    >
                      {syncing && !mailOperation
                        ? "Syncing calendars…"
                        : "Sync calendars"}
                    </button>
                  </div>
                  {syncNotice && <p role="status">{syncNotice}</p>}
                  <section aria-label="Daily schedule">
                    <div className="today-section-title">
                      <h2>
                        Schedule <span>{events.length}</span>
                      </h2>
                      <small>
                        {String(agenda.sources.calendar.state).replaceAll(
                          "_",
                          " ",
                        )}
                      </small>
                    </div>
                    <div className="today-list today-timeline">
                      {events.map(event)}
                    </div>
                    {!events.length && (
                      <p className="today-empty">
                        {agenda.total.events > events.length
                          ? "More calendar events are available on the next agenda pages."
                          : agenda.sources.calendar.state === "current"
                            ? "No saved events for this day."
                            : "Calendar coverage is incomplete for this day."}
                      </p>
                    )}
                  </section>
                  <details className="agenda-sources">
                    <summary>
                      Calendar sources ·{" "}
                      {String(agenda.sources.calendar.state).replaceAll(
                        "_",
                        " ",
                      )}
                    </summary>
                    {!(agenda.sources.calendar.accounts || []).length && (
                      <p>
                        Connect Google or Microsoft in Settings to add your
                        calendars.
                      </p>
                    )}
                    {(agenda.sources.calendar.accounts || [])
                      .filter((account: Data) => !account.calendarsListedAt)
                      .map((account: Data) => (
                        <p key={account.id}>
                          Calendar list has not been synchronized. Open
                          Calendars & sync.
                        </p>
                      ))}
                    {(agenda.sources.calendar.accounts || [])
                      .filter((account: Data) => account.error)
                      .map((account: Data) => (
                        <p key={account.id} role="status">
                          {account.provider} · {account.error}
                        </p>
                      ))}
                    {(agenda.sources.calendar.snapshots || []).map(
                      (source: Data) => (
                        <p key={source.calendarId}>
                          <strong>{source.name}</strong> · {source.coverage} day
                          coverage ·{" "}
                          {source.syncedAt
                            ? `Last synced ${new Date(source.syncedAt * 1000).toLocaleString()}`
                            : "Not synchronized"}
                          {source.error && (
                            <span role="status"> · {source.error}</span>
                          )}
                        </p>
                      ),
                    )}
                  </details>

                  <button
                    className="secondary"
                    onClick={() => navigate("calendar")}
                  >
                    Calendars & sync
                  </button>
                  <HiddenCalendarEvents
                    changed={load}
                    version={agenda.hiddenEventCount || 0}
                  />
                </>
              )}
              {page === "inbox" && (
                <>
                  {mailNotice && <p role="status">{mailNotice}</p>}
                  {agenda.nextOffset != null && (agenda.total.emails || 0) > emails.length && <p role="status">More actionable mail is on the next agenda page. Use Load more agenda items below.</p>}
                  <details className="today-secondary-section">
                    <summary>Review saved mail</summary>
                    {agenda.sources.email.state === "not_connected" ? (
                      <p className="today-empty">Connect Gmail in Email settings to include mail. Your Leam notes remain available above.</p>
                    ) : <EmailTriage key={date + timezone} classification={agenda.sources.email.classification}
                        pendingFallback={(agenda.emails || []).filter((item: Data) => !item.actionability || item.actionability.pending).length}
                        changed={load} />}
                  </details>
                  <details className="today-secondary-section">
                    <summary>Inbox settings & sources</summary>
                    <p className="agenda-hint">
                      Saved mail from the last 30 days, independent of this
                      agenda date. Rebuild resets local classifications and
                      review choices, reads up to 100 recent messages per
                      connected Gmail account for up to 20 clear actions, and
                      never changes Gmail.
                    </p>
                    <div className="actions">
                      <button
                        className="secondary"
                        disabled={syncing || !mailAvailable}
                        onClick={() => void sync("rebuild")}
                      >
                        {mailOperation === "rebuild"
                          ? "Rebuilding…"
                          : "Rebuild action inbox"}
                      </button>
                      <button
                        className="secondary"
                        onClick={() => navigate("settings")}
                      >
                        Email settings
                      </button>
                    </div>
                    <details className="agenda-sources">
                      <summary>
                        Email sources · {agenda.sources.email.state}
                      </summary>
                      {(agenda.sources.email.accounts || []).map(
                        (account: Data) => (
                          <p key={account.accountId}>
                            <strong>{account.identity}</strong> ·{" "}
                            {account.granted
                              ? "Read-only access granted"
                              : "Email consent not granted"}{" "}
                            · {account.state}
                            {account.syncedAt
                              ? ` · Last synced ${new Date(account.syncedAt * 1000).toLocaleString()}`
                              : " · Not synchronized"}
                            {account.stale ? " · Saved data is stale" : ""}
                            {account.truncated
                              ? " · More messages exist outside this bounded view"
                              : ""}
                            {account.error ? ` · ${account.error}` : ""}
                          </p>
                        ),
                      )}
                    </details>
                  </details>
                  {agenda.sources.email.truncated && (
                    <p className="agenda-hint">
                      This is a limited recent inbox view; additional messages
                      remain in Gmail.
                    </p>
                  )}
                </>
              )}
              {agenda.nextOffset != null && (
                <button
                  className="secondary today-load-more"
                  disabled={loading}
                  onClick={() => void load(true)}
                >
                  Load more agenda items
                </button>
              )}
              {(agenda.partial || agenda.nextOffset != null) && (
                <p className="today-coverage">
                  This saved view is partial. Load more items where available
                  and check source coverage.
                </p>
              )}
            </>
          )}
          {page === "plan" && (
            <details
              className="today-secondary-section"
              open={remindersOpen}
              onToggle={(e) => setRemindersOpen(e.currentTarget.open)}
            >
              <summary>Reminders</summary>
              {remindersOpen && (
                <>
                  <ReminderInbox fail={fail} changed={canonicalChanged} />
                  <RoutineInbox fail={fail} />
                </>
              )}
            </details>
          )}
        </div>
      )}
      <AgendaChat
        date={date}
        timezone={timezone}
        active={page === "chat"}
        checksActive={false}
        changed={() => void load()}
        canonicalChanged={canonicalChanged}
      />
      {editor !== undefined && (
        <CommitmentForm
          initial={editor}
          draft={drafts.current[editor?.id || "new"]}
          preserve={(draft) => {
            drafts.current[editor?.id || "new"] = draft;
          }}
          capacities={capacities}
          close={(saved) => {
            if (saved) delete drafts.current[editor?.id || "new"];
            setEditor(undefined);
          }}
          changed={canonicalChanged}
          remove={(id) => {
            setEditor(undefined);
            setRemoving(id);
          }}
          fail={fail}
        />
      )}
      {removing && (
        <RemovalDialog
          kind="commitment"
          id={removing}
          close={() => {
            setRemoving(null);
            void load();
          }}
          changed={canonicalChanged}
        />
      )}
    </section>
  );
}
