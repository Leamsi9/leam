import { useEffect, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { api, type Data } from "./api";
import {
  CommitmentCard,
  CommitmentForm,
  type EditorDraft,
} from "./commitment-components";
import { RemovalDialog } from "./removal";
import { ReminderInbox } from "./reminders";
import { AgendaChat } from "./agenda-chat";
import { HideCalendarEvent, HiddenCalendarEvents } from "./calendar-visibility";
import { syncAgendaCalendars, syncAgendaMail } from "./agenda-sync";

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
const sectionDefaults = {
  focus: true,
  schedule: true,
  email: true,
  commitments: true,
};
type SectionName = keyof typeof sectionDefaults;
const sectionPreference = "leam:today-sections:v1";
function readSections() {
  const result = { ...sectionDefaults };
  try {
    const saved = JSON.parse(localStorage.getItem(sectionPreference) || "{}");
    for (const key of Object.keys(result) as SectionName[])
      if (typeof saved?.[key] === "boolean") result[key] = saved[key];
  } catch {
    /* Storage is optional; no private content is persisted here. */
  }
  return result;
}
export function Today({ fail }: { fail: (error: unknown) => void }) {
  const [date, setDate] = useState(localDay);
  const [sections, setSections] = useState(readSections);
  const sectionState = useRef(sections);
  function setSection(name: SectionName, open: boolean) {
    if (sectionState.current[name] === open) return;
    const next = { ...sectionState.current, [name]: open };
    sectionState.current = next;
    setSections(next);
    try {
      localStorage.setItem(sectionPreference, JSON.stringify(next));
    } catch {
      /* Collapsing still works when browser storage is unavailable. */
    }
  }
  const [timezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/London",
  );
  const [savedAgenda, setAgenda] = useState<Data | null>(null);
  const agenda = savedAgenda?.date === date ? savedAgenda : null;
  const [capacities, setCapacities] = useState<Data[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncNotice, setSyncNotice] = useState("");
  const [mailSyncing, setMailSyncing] = useState(false);
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
  async function load(more = false) {
    const requestedDate = currentDay.current;
    const offset = more ? agenda?.nextOffset : 0;
    if (more && offset == null) return;
    const n = ++sequence.current;
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
      if (active.current && n === sequence.current)
        setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (active.current && n === sequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    active.current = true;
    setSyncing(false);
    setSyncNotice("");
    setMailNotice("");
    setMailSyncing(false);
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
    let alive = true;
    void api("/capacities")
      .then((result) => {
        if (alive) setCapacities(result.items);
      })
      .catch(fail);
    return () => {
      alive = false;
    };
  }, []);
  async function sync(kind: "calendar" | "mail" = "calendar") {
    if (syncRequest.current || !agenda) return;
    const controller = new AbortController(),
      requestedDate = date;
    syncRequest.current = controller;
    setSyncing(true);
    setMailSyncing(kind === "mail");
    const setNotice = kind === "mail" ? setMailNotice : setSyncNotice;
    setNotice("");
    const current = () =>
      active.current &&
      currentDay.current === requestedDate &&
      syncRequest.current === controller;
    try {
      const notice =
        kind === "mail"
          ? await syncAgendaMail(controller.signal)
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
        setMailSyncing(false);
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
          setSection("focus", true);
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
      className="agenda-triage"
      aria-label={`Plan ${item.title || item.subject}`}
    >
      {(
        [
          ["focus", "Focus"],
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
          {value === "focus" && item.triage.disposition === "focus"
            ? "Remove from focus"
            : label}
        </button>
      ))}
    </div>
  );
  const commitment = (item: Data) => (
    <div key={item.key} className="agenda-commitment">
      <CommitmentCard
        item={item}
        capacity={capacities.find((c) => c.id === item.capacityId)}
        changed={load}
        edit={() => setEditor(item)}
        fail={fail}
      />
      {choices(item)}
    </div>
  );
  const event = (item: Data) => (
    <article className="card agenda-event" key={item.key}>
      <div>
        <CalendarDays size={18} />
        <time>
          {item.allDay
            ? "All day"
            : `${new Date(item.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: timezone })}–${new Date(item.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: timezone })}`}
        </time>
      </div>
      <h3>{item.title}</h3>
      {item.url && (
        <a href={item.url} target="_blank" rel="noreferrer">
          Open calendar event
        </a>
      )}
      {choices(item)}
      <HideCalendarEvent item={item} changed={load} />
    </article>
  );
  const commitments: Data[] = agenda?.commitments || [],
    emails: Data[] = agenda?.emails || [],
    events: Data[] = agenda?.events || [];
  const done = (item: Data) =>
    item.log !== undefined &&
    (item.kind === "task" ? item.status === "completed" : item.log.done);
  const completed = commitments.filter(done);
  const focus = [...events, ...emails, ...commitments].filter(
    (item) => !done(item) && item.triage.disposition === "focus",
  );
  const remaining = commitments.filter(
    (item) => !done(item) && item.triage.disposition === "none",
  );
  const deferred = [...events, ...emails, ...commitments].filter(
    (item) =>
      !done(item) && ["later", "dismissed"].includes(item.triage.disposition),
  );
  const email = (item: Data) => (
    <article className="card agenda-email" key={item.key}>
      <small>
        {item.from} · {new Date(item.receivedAt).toLocaleDateString()}
        {item.unread ? " · Unread" : ""}
      </small>
      <h3>{item.subject || "(No subject)"}</h3>
      {item.snippet && <p>{item.snippet}</p>}
      {item.url && (
        <a href={item.url} target="_blank" rel="noreferrer">
          Open email in Gmail
        </a>
      )}
      {choices(item)}
    </article>
  );
  const render = (item: Data) =>
    item.eventId !== undefined
      ? event(item)
      : item.log !== undefined
        ? commitment(item)
        : email(item);
  return (
    <section className="page daily-agenda">
      <header className="agenda-heading">
        <div>
          <h1>Today</h1>
          <small>{timezone}</small>
        </div>
        <button className="secondary" onClick={() => setEditor(null)}>
          <Plus size={18} /> Add commitment
        </button>
      </header>
      <div className="agenda-date">
        <button
          className="icon-button"
          aria-label="Previous day"
          onClick={() => setDate((value) => adjacent(value, -1))}
        >
          <ChevronLeft size={20} />
        </button>
        <label>
          Viewing date
          <input
            type="date"
            value={date}
            onChange={(e) => {
              if (e.target.value) setDate(e.target.value);
            }}
          />
        </label>
        <button
          className="icon-button"
          aria-label="Next day"
          onClick={() => setDate((value) => adjacent(value, 1))}
        >
          <ChevronRight size={20} />
        </button>
        <button
          className="secondary"
          aria-label="Go to today"
          onClick={() => setDate(localDay())}
        >
          Today
        </button>
      </div>
      <div className="agenda-toolbar">
        <AgendaChat
          key={date + timezone}
          date={date}
          timezone={timezone}
          changed={() => void load()}
        />
        <button
          className="secondary"
          disabled={loading}
          onClick={() => void load()}
        >
          Refresh saved view
        </button>
      </div>
      {error && (
        <p role="alert">
          {error} {agenda && "Your previous saved view is still shown."}
        </p>
      )}
      {loading && !agenda && <p role="status">Loading your day…</p>}
      {agenda && (
        <>
          <section
            aria-label="Daily focus"
            className="agenda-focus"
            ref={focusSection}
            tabIndex={-1}
          >
            <details
              className="agenda-section"
              open={sections.focus}
              onToggle={(e) => setSection("focus", e.currentTarget.open)}
            >
              <summary
                onClick={(e) => {
                  e.preventDefault();
                  setSection("focus", !sectionState.current.focus);
                }}
              >
                <h2>
                  Focus{" "}
                  <span className="agenda-focus-count">{focus.length}</span>
                </h2>
                <time dateTime={date}>{date}</time>
              </summary>
              <p className="agenda-hint">
                Your priorities for this day. Complete a commitment or open an
                event or email to take the next step.
              </p>
              {focusNotice && (
                <p className="agenda-focus-notice" role="status">
                  {focusNotice}
                </p>
              )}
              {focus.length ? (
                focus.map(render)
              ) : (
                <p className="agenda-empty">
                  Choose Focus on a commitment, event or actionable email to
                  bring it here.
                </p>
              )}
              {agenda.nextOffset != null && (
                <p className="agenda-hint">
                  Showing loaded priorities. More items may be available below.
                </p>
              )}
            </details>
          </section>
          <section aria-label="Daily schedule">
            <details
              className="agenda-section"
              open={sections.schedule}
              onToggle={(e) => setSection("schedule", e.currentTarget.open)}
            >
              <summary
                onClick={(e) => {
                  e.preventDefault();
                  setSection("schedule", !sectionState.current.schedule);
                }}
              >
                <h2>
                  Schedule{" "}
                  <span>
                    {
                      events.filter(
                        (item) => item.triage.disposition === "none",
                      ).length
                    }
                  </span>
                </h2>
              </summary>
              <div className="agenda-section-heading">
                <button
                  className="secondary"
                  disabled={syncing || !agenda}
                  onClick={() => void sync()}
                >
                  {syncing && !mailSyncing
                    ? "Syncing calendars…"
                    : "Sync calendars"}
                </button>
                <button
                  className="secondary"
                  onClick={() => navigate("calendar")}
                >
                  Calendars & sync
                </button>
              </div>
              {syncNotice && <p role="status">{syncNotice}</p>}
              {events
                .filter((item) => item.triage.disposition === "none")
                .map(event)}
              {!events.length && (
                <p>
                  {agenda.total.events > events.length
                    ? "More calendar events are available on the next agenda pages."
                    : agenda.sources.calendar.state === "current"
                      ? "No saved events for this day."
                      : "Calendar coverage is incomplete for this day."}
                </p>
              )}
              {agenda.total.events > events.length &&
                agenda.nextOffset != null && (
                  <button
                    className="secondary"
                    disabled={loading}
                    onClick={() => void load(true)}
                  >
                    Load more calendar items
                  </button>
                )}
              <details className="agenda-sources">
                <summary>
                  Calendar sources ·{" "}
                  {String(agenda.sources.calendar.state).replaceAll("_", " ")}
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
                      Calendar list has not been synchronized. Open Calendars &
                      sync.
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
            </details>
          </section>
          <HiddenCalendarEvents
            changed={load}
            version={agenda.hiddenEventCount || 0}
          />
          <section aria-label="Recent email inbox">
            <details
              className="agenda-section"
              open={sections.email}
              onToggle={(e) => setSection("email", e.currentTarget.open)}
            >
              <summary
                onClick={(e) => {
                  e.preventDefault();
                  setSection("email", !sectionState.current.email);
                }}
              >
                <h2>
                  Recent inbox{" "}
                  <span>
                    {
                      emails.filter(
                        (item) => item.triage.disposition === "none",
                      ).length
                    }
                  </span>
                </h2>
              </summary>
              <div className="agenda-section-heading">
                <button
                  className="secondary"
                  disabled={
                    syncing ||
                    !(agenda.sources.email.accounts || []).some(
                      (account: Data) =>
                        account.granted === true &&
                        ["never_synced", "ready", "stale", "error"].includes(
                          account.state,
                        ),
                    )
                  }
                  onClick={() => void sync("mail")}
                >
                  {mailSyncing ? "Syncing mail…" : "Sync mail"}
                </button>
                <button
                  className="secondary"
                  onClick={() => navigate("settings")}
                >
                  Email settings
                </button>
              </div>
              {mailNotice && <p role="status">{mailNotice}</p>}
              {agenda.sources.email.state === "not_connected" ? (
                <p className="agenda-hint">
                  Email is not connected to Leam. Enable read-only Gmail access
                  in Email settings. Calendar account access does not grant
                  mailbox access.
                </p>
              ) : (
                <>
                  <p className="agenda-hint">
                    Saved inbox messages from the last 30 days, independent of
                    this agenda date. Focus and Later apply only to your
                    selected day.
                  </p>
                  {emails
                    .filter((item) => item.triage.disposition === "none")
                    .map(email)}
                  {!emails.length && (
                    <p>
                      {(agenda.total.emails || 0) > 0
                        ? "More emails are available on the next agenda pages."
                        : agenda.sources.email.state === "ready"
                          ? "No messages in the saved recent inbox view."
                          : "Email coverage is not current. Check account status below."}
                    </p>
                  )}
                  {(agenda.total.emails || 0) > emails.length &&
                    agenda.nextOffset != null && (
                      <button
                        className="secondary"
                        disabled={loading}
                        onClick={() => void load(true)}
                      >
                        Load more email items
                      </button>
                    )}
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
                  {agenda.sources.email.truncated && (
                    <p className="agenda-hint">
                      This is a limited recent inbox view; additional messages
                      remain in Gmail.
                    </p>
                  )}
                </>
              )}
            </details>
          </section>
          <section aria-label="Eligible commitments">
            <details
              className="agenda-section"
              open={sections.commitments}
              onToggle={(e) => setSection("commitments", e.currentTarget.open)}
            >
              <summary
                onClick={(e) => {
                  e.preventDefault();
                  setSection("commitments", !sectionState.current.commitments);
                }}
              >
                <h2>
                  Commitments <span>{remaining.length}</span>
                </h2>
              </summary>

              <p className="agenda-hint">
                Eligible for this date. Focus chooses your daily priorities; it
                does not change the commitment.
              </p>
              {remaining.length ? (
                remaining.map(commitment)
              ) : (
                <p>No other commitments in this loaded view.</p>
              )}
            </details>
          </section>
          {!!completed.length && (
            <details className="agenda-completed">
              <summary>Completed · {completed.length}</summary>
              {completed.map(commitment)}
            </details>
          )}
          {!!deferred.length && (
            <details className="agenda-deferred">
              <summary>Later or hidden for today · {deferred.length}</summary>
              {deferred.map(render)}
            </details>
          )}
          {agenda.nextOffset != null && (
            <button
              className="secondary"
              disabled={loading}
              onClick={() => void load(true)}
            >
              Load more agenda items
            </button>
          )}
          {commitments.length + events.length + emails.length <
            agenda.total.commitments +
              agenda.total.events +
              (agenda.total.emails || 0) && (
            <p className="agenda-hint">
              This view is partial. Load more items where available and check
              calendar coverage above.
            </p>
          )}
        </>
      )}
      <details className="agenda-notifications">
        <summary>Reminders</summary>
        <ReminderInbox fail={fail} changed={load} />
      </details>
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
          changed={load}
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
          changed={load}
        />
      )}
    </section>
  );
}
