import { redirectLegacyInbox } from "./inbox-route";
import { ResourcesPage } from "./resources";
import { LazySettingsSection, SettingsActivityBoundary } from "./settings-lifecycle";
import { ChatDialog } from "./chat-dialog";
import { Plus } from "lucide-react";
import { ArtifactLink, ArtifactViewer } from "./artifacts";
import { MainCodingControl } from "./main-coding";
import { ApprovalsPanel } from "./approvals";
import { ApprovalPolicySettings } from "./approval-policy";
import { NotificationPopup } from "./notification-popup";
import { PlaybackPanel } from "./voice/playback-panel";
import { playback } from "./voice/playback";
import type { PlaybackTarget } from "./voice/playback-source";
import {
  AttachmentComposer,
  AttachmentList,
  useAttachmentDraft,
} from "./attachments";
import {
  cachedChat,
  rememberChat,
  sessionValue,
  rememberSession,
  clearPrivateSession,
  clearAcceptedDraft,
  forgetChatPrefix,
  sessionGeneration,
} from "./session-cache";
import {
  codingEchoes,
  saveCodingEchoes,
  unrepresentedEchoes,
  type CodingEcho,
} from "./coding-pending";
import React, { useEffect, useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  MessageCircle,
  Sun,
  CalendarDays,
  Repeat,
  Bell,
  ListTodo,
  Code2,
  Settings,
  ArrowUp,
  ArrowLeft,
  RefreshCw,
  Square,
  ChevronRight,
  LogOut,
  Shield,
  Circle,
} from "lucide-react";
import "./styles.css";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api, chatRead, type Data } from "./api";
import { useChatScroll } from "./chat-scroll";
import { ConversationList } from "./conversation-list";
import { CodingStreamProjection } from "./coding-stream";
import { CalendarView } from "./calendar";
import { Today } from "./today";
import { Goals } from "./goals";
import { ContextOverview } from "./context-overview";
import { RoutinesPanel } from "./routines";
import { UpdatesPanel } from "./updates";
import { Navigation } from "./navigation";
import { BacklogPanel } from "./backlog";
import { BackupSettings } from "./backups";
import { SharedStop } from "./shared-stop";
import { SharedRequest } from "./shared-request";
import { PwaSettings } from "./pwa-settings";
import { ClientUpdateNotice } from "./client-update";
import { SystemSettings } from "./system-settings";
import { AccountSettings } from "./accounts";
import { PushSettings } from "./push";
import { ReminderSettings } from "./reminders";
import { Companion, ModelSettings, MemorySettings } from "./companion";
import { ToolPermissionSettings } from "./tool-permissions";
import { VoiceComposer } from "./voice/composer";
import { ReadAloud } from "./voice/controls";
import { stopRecognition } from "./voice/speech";
import { ProcedureSettings } from "./procedures";
import { VoiceSettings } from "./voice/settings";
import { UsageApp, UsageSettings } from "./usage";
import { stopConversation, type Receipt } from "./voice/conversation";
import { AppearanceSettings } from "./appearance";
import "./theme.css";

function App() {
  const [auth, setAuth] = useState<Data | null>(null),
    [tab, setTab] = useState(() => redirectLegacyInbox(
      [
        "companion",
        "coding",
        "calendar",
        "today",
        "goals",
        "overview",
        "settings",
        "routines",
        "updates",
        "approvals",
        "backlog",
        "resources",
        "inbox",
        "usage",
        "tokenops",
      ].includes(new URLSearchParams(location.search).get("view") || "")
        ? new URLSearchParams(location.search).get("view")!
        : sessionValue("tab", "coding"),
    )),
    [error, setError] = useState("");
  const [online, setOnline] = useState(navigator.onLine);
  const [voiceReturn, setVoiceReturn] = useState(0);
  async function returnToVoiceChat(target: PlaybackTarget) {
    const playbackId = playback.snapshot().id;
    // Resolve native/shared metadata before mounting Coding so its submission
    // transport remains the one owned by the originating thread.
    if (target.module === "coding") {
      const result = await api(`/codex/threads/${encodeURIComponent(target.threadId)}`);
      if (playback.snapshot().id !== playbackId) return;
      if (result.thread?.id !== target.threadId) throw new Error("The original chat is unavailable.");
      rememberSession("coding:selected", result.thread);
    } else {
      rememberSession("companion:selected", target.threadId);
      rememberSession("companion:selectedItem", { thread_id: target.threadId });
    }
    rememberSession("tab", target.module);
    setVoiceReturn((version) => version + 1);
    setTab(target.module);
    const url = new URL(location.href);
    url.searchParams.set("view", target.module);
    history.replaceState(null, "", url);
  }
  useEffect(() => {
    const openCoding = (event: Event) => {
      const thread = (event as CustomEvent).detail;
      if (!thread?.id) return;
      rememberSession("coding:selected", thread);
      rememberSession("tab", "coding");
      setTab("coding");
    };
    window.addEventListener("leam:open-coding", openCoding);
    return () => window.removeEventListener("leam:open-coding", openCoding);
  }, []);
  useEffect(() => {
    const navigate = (event: Event) => {
      const view = redirectLegacyInbox((event as CustomEvent).detail);
      if (["today", "goals", "calendar", "settings", "overview", "approvals", "resources", "inbox", "usage", "tokenops"].includes(view)) {
        setTab(view);
        rememberSession("tab", view);
        const url = new URL(location.href);
        url.searchParams.set("view", view);
        history.replaceState(null, "", url);
      }
    };
    window.addEventListener("leam:navigate", navigate);
    return () => window.removeEventListener("leam:navigate", navigate);
  }, []);
  useEffect(() => {
    api("/auth/status")
      .then(setAuth)
      .catch((e) => setError(e.message));
    const lost = () => {
      setAuth({ authenticated: false, configured: true });
      stopConversation();
      stopRecognition();
    };
    window.addEventListener("leam:auth-lost", lost);
    const update = () => {
      setOnline(navigator.onLine);
      if (navigator.onLine)
        api("/auth/status")
          .then(setAuth)
          .catch((e) => setError(e.message));
    };
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("leam:auth-lost", lost);
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  const fail = (e: unknown) =>
    setError(e instanceof Error ? e.message : String(e));
  if (!auth)
    return (
      <main className="auth-layout">
        <section className="auth-panel">
          <h1>{online ? "Connecting to Leam" : "Reconnect to Leam"}</h1>
          <p>
            {online
              ? "Checking your session…"
              : "The app is available offline. Reconnect to verify your session and load your private data."}
          </p>
          <button onClick={() => location.reload()}>Try again</button>
        </section>
      </main>
    );
  if (!auth?.authenticated)
    return (
      <main className="auth-layout">
        <div className="auth-brand">
          <span className="brand">
            leam<span>·</span>
          </span>
          <h1>
            A little more
            <br />
            room to think.
          </h1>
          <p>
            Your priorities, your work, and a companion
            <br />
            to connect it all.
          </p>
        </div>
        <section className="auth-panel">
          <span className="eyebrow">YOUR PERSONAL SPACE</span>
          <h2>
            {auth?.configured ? "Welcome back." : "Make yourself at home."}
          </h2>
          <p>
            {auth?.configured
              ? "Sign in to continue where you left off."
              : "Pair this browser with your Leam installation."}
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setError("");
              const form = new FormData(e.currentTarget);
              try {
                setAuth(
                  await api(
                    auth?.configured ? "/auth/login" : "/auth/setup",
                    "POST",
                    auth?.configured
                      ? { password: form.get("password") }
                      : {
                          bootstrap: form.get("bootstrap"),
                          password: form.get("password"),
                        },
                  ),
                );
              } catch (e) {
                fail(e);
              }
            }}
          >
            {!auth?.configured && (
              <label>
                Pairing code
                <input name="bootstrap" required autoComplete="off" />
              </label>
            )}
            <label>
              {auth?.configured ? "Password" : "Choose a password"}
              <input
                name="password"
                type="password"
                required
                minLength={auth?.configured ? 1 : 14}
                autoComplete={
                  auth?.configured ? "current-password" : "new-password"
                }
              />
            </label>
            <button className="primary" disabled={!auth}>
              Enter Leam <ChevronRight size={18} />
            </button>
          </form>
          <small>
            <Shield size={14} /> Your session stays private to this browser.
          </small>
        </section>
      </main>
    );
  const artifactId = new URLSearchParams(location.search).get("artifact");
  if (artifactId) return <ArtifactViewer id={artifactId} />;
  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className="sidebar">
        <a className="brand" href="/">
          <img className="brand-icon" src="/leam-icon-192.png" alt="" width="34" height="34" />
          leam<span>·</span>
        </a>
        <p className="sidebar-caption">A place for what matters.</p>
        <Navigation
          tab={tab === "overview" ? "settings" : tab === "tokenops" ? "usage" : tab}
          onSelect={(id) => {
            setTab(id);
            rememberSession("tab", id);
            const url = new URL(location.href);
            url.searchParams.set("view", id);
            history.replaceState(null, "", url);
            setError("");
          }}
        />
        <div className="sidebar-footer">
          <Circle size={8} fill={online ? "var(--semantic-success-solid)" : "var(--semantic-warning-solid)"} />
          {online ? "Connected" : "Offline"}
          <span>Leam · Preview</span>
        </div>
      </aside>
      <main
        id="main-content"
        tabIndex={-1}
        className={`workspace ${["coding", "companion"].includes(tab) ? "chat-workspace" : ""}`}
      >
        <PlaybackPanel onReturnToChat={(target) => { void returnToVoiceChat(target).catch(fail); }} />
        <ClientUpdateNotice />
        <NotificationPopup />
        <header className="topbar">
          <span>YOUR SPACE / {tab.toUpperCase()}</span>
          <span>
            {new Date().toLocaleDateString(undefined, {
              weekday: "short",
              month: "short",
              day: "numeric",
            })}
          </span>
        </header>
        {error && (
          <div role="alert" className="error global-error">
            {error}
            <button onClick={() => setError("")}>Dismiss</button>
          </div>
        )}
        {!online && (
          <div className="notice">
            You’re offline. Reconnect before sending or changing anything.
          </div>
        )}
        {tab === "coding" ? (
          <Coding key={voiceReturn} fail={fail} />
        ) : tab === "today" ? (
          <Today fail={fail} />
        ) : tab === "goals" ? (
          <Goals fail={fail} />

        ) : tab === "routines" ? (
          <RoutinesPanel fail={fail} />
        ) : tab === "calendar" ? (
          <CalendarView fail={fail} />
        ) : tab === "resources" ? (
          <ResourcesPage />
        ) : tab === "backlog" ? (
          <BacklogPanel fail={fail} />
        ) : tab === "approvals" ? (
          <ApprovalsPanel />
        ) : tab === "updates" ? (
          <UpdatesPanel fail={fail} />
        ) : ["settings", "overview", "usage", "tokenops"].includes(tab) ? (
          null
        ) : (
          <Companion key={voiceReturn} fail={fail} />
        )}
        <UsageApp active={tab === "usage" || tab === "tokenops"} />
        <SettingsPage active={tab === "settings" || tab === "overview"} openOverview={tab === "overview"} fail={fail} signedOut={() => {
          clearPrivateSession();
          setAuth({ configured: true, authenticated: false });
        }} />
      </main>
    </div>
  );
}
function SettingsPage({
  active,
  openOverview,
  fail,
  signedOut,
}: {
  active: boolean;
  openOverview: boolean;
  fail: (error: unknown) => void;
  signedOut: () => void;
}) {
  const [visited, setVisited] = useState(active);
  useEffect(() => {
    if (active) setVisited(true);
  }, [active]);
  if (!visited && !active) return null;
  return (
    <SettingsActivityBoundary active={active}>
      <section className="page" hidden={!active} aria-label="Settings">
        <span className="eyebrow">MAKE IT YOURS</span>
        <h1>Settings</h1>
        <AppearanceSettings />
        <div className="card">
          <h3>Your connection</h3>
          <p>
            You are signed in to this Leam installation. Coding connects
            directly to Codex on your host.
          </p>
          <button
            className="secondary"
            onClick={() =>
              api("/auth/logout", "POST", {}).then(signedOut).catch(fail)
            }
          >
            <LogOut size={16} /> Sign out
          </button>
        </div>
        <LazySettingsSection title="App installation and offline">
          <PwaSettings />
        </LazySettingsSection>
        <LazySettingsSection title="Model and reasoning">
          <ModelSettings fail={fail} />
        </LazySettingsSection>
        <ContextOverview key={String(openOverview)} initiallyOpen={openOverview} />
        <UsageSettings />
        <LazySettingsSection title="Approval preferences">
          <ApprovalPolicySettings />
        </LazySettingsSection>
        <LazySettingsSection title="Companion permissions">
          <ToolPermissionSettings fail={fail} />
        </LazySettingsSection>
        <LazySettingsSection title="Voice and playback">
          <VoiceSettings />
        </LazySettingsSection>
        <LazySettingsSection title="Inside Leam">
          <SystemSettings fail={fail} />
        </LazySettingsSection>
        <LazySettingsSection title="Operating procedures">
          <ProcedureSettings />
        </LazySettingsSection>
        <LazySettingsSection title="Memory">
          <MemorySettings fail={fail} />
        </LazySettingsSection>
        <LazySettingsSection title="Backups and recovery">
          <BackupSettings fail={fail} />
        </LazySettingsSection>
        <LazySettingsSection title="Reminders">
          <ReminderSettings fail={fail} />
        </LazySettingsSection>
        <LazySettingsSection title="Phone notifications">
          <PushSettings fail={fail} />
        </LazySettingsSection>
        <LazySettingsSection
          title="Connected accounts"
          initiallyOpen={Boolean(
            new URLSearchParams(location.search).get("account"),
          )}
        >
          <AccountSettings fail={fail} />
        </LazySettingsSection>
      </section>
    </SettingsActivityBoundary>
  );
}
function Coding({ fail }: { fail: (e: unknown) => void }) {
  const [creatingSession, setCreatingSession] = useState(false);
  const [createError, setCreateError] = useState("");
  const [createdSession, setCreatedSession] = useState(0);
  const [optionsError, setOptionsError] = useState("");
  const createInFlight = useRef(false);
  const restored = useRef(sessionValue<Data | null>("coding:selected", null));
  const initialId = restored.current?.id || "";
  const initial = cachedChat("coding:" + initialId);
  const [threads, setThreads] = useState<Data[]>(
      () => cachedChat("coding:threads")?.threads || [],
    ),
    [thread, setThread] = useState<Data | null>(restored.current),
    [turns, setTurnsState] = useState<Data[]>(() => initial?.turns || []),
    [goal, setGoal] = useState<Data | null>(() => initial?.goal || null),
    [text, setText] = useState(() =>
      sessionValue("coding:draft:" + initialId, ""),
    ),
    [connected, setConnected] = useState(false),
    [busy, setBusy] = useState(false),
    [active, setActive] = useState(""),
    [requests, setRequests] = useState<Data[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [echoes, setEchoesState] = useState<CodingEcho[]>(() =>
      codingEchoes(initialId),
    ),
    [displayNotice, setDisplayNotice] = useState(
      initial?.turns?.length
        ? "Showing saved conversation while checking the connection."
        : "",
    ),
    [newWorkspace, setNewWorkspace] = useState("");
  const selected = useRef(initialId);
  const selectedShared = useRef(restored.current?.transport === "ide-owner");
  const [sharedBindingId, setSharedBindingId] = useState<
    string | null | undefined
  >();
  const sharedBinding = useRef<string | null | undefined>(undefined);
  const bindingAllows = (id: string) =>
    sharedBinding.current === undefined || sharedBinding.current === id;
  const missingBindingNotice =
    "This shared session is no longer configured. Its saved conversation remains read-only.";
  const submitting = useRef(false);
  const streamRevision = useRef(0);
  const validatedAt = useRef(initial?.validatedAt || 0);
  const projection = useRef(new CodingStreamProjection());
  const currentTurns = useRef(turns);
  const currentEchoes = useRef(echoes);
  function updateEchoes(
    id: string,
    update: CodingEcho[] | ((previous: CodingEcho[]) => CodingEcho[]),
  ) {
    const previous =
      selected.current === id ? currentEchoes.current : codingEchoes(id);
    const next = typeof update === "function" ? update(previous) : update;
    saveCodingEchoes(id, next);
    if (selected.current === id) {
      currentEchoes.current = next;
      setEchoesState(next);
    }
  }
  function setTurns(value: Data[] | ((previous: Data[]) => Data[])) {
    const next =
      typeof value === "function" ? value(currentTurns.current) : value;
    currentTurns.current = next;
    setTurnsState(next);
    const remaining = unrepresentedEchoes(currentEchoes.current, next);
    if (selected.current && remaining.length !== currentEchoes.current.length)
      updateEchoes(selected.current, remaining);
  }
  const submission = useRef<{
    id: string;
    text: string;
    attachmentIds?: string[];
    expectedTurnId?: string;
  } | null>(null);
  const files = useAttachmentDraft("coding:" + (thread?.id || ""));
  const [uploading, setUploading] = useState(false);
  const chatScroll = useChatScroll(thread?.id || "");
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [threadCursor, setThreadCursor] = useState<string | null>(
    () => cachedChat("coding:threads")?.nextCursor || null,
  );
  const latestThreadCursor = useRef(threadCursor);
  const [listing, setListing] = useState(false);
  const [listNotice, setListNotice] = useState("");
  const [listError, setListError] = useState("");
  const [listCheckedAt, setListCheckedAt] = useState<number | null>(null);
  const listFetchedAt = useRef(0);
  const listBusy = useRef(false),
    listRequest = useRef(0);
  async function load(more = false, reportError?: (e: unknown) => void) {
    if (listBusy.current || (more && !threadCursor)) return;
    listBusy.current = true;
    setListing(true);
    const request = ++listRequest.current;
    if (!more) {
      // The configured shared row is local metadata, independent of native IPC.
      void api("/codex/shared-thread")
        .then((result) => {
          if (
            request !== listRequest.current ||
            typeof result.configured !== "boolean"
          )
            return;
          if (result.configured && typeof result.thread?.id !== "string")
            return;
          const shared = result.configured ? result.thread : null;
          sharedBinding.current = shared?.id || null;
          setSharedBindingId(sharedBinding.current);
          if (selectedShared.current && !bindingAllows(selected.current)) {
            setConnected(false);
            setDisplayNotice(missingBindingNotice);
          }
          setThreads((previous) => {
            const rows = previous.filter(
              (item) =>
                item.transport !== "ide-owner" && item.id !== shared?.id,
            );
            if (shared) rows.unshift(shared);
            rememberChat("coding:threads", {
              threads: rows,
              nextCursor: latestThreadCursor.current,
            });
            return rows;
          });
        })
        .catch(() => {
          /* The ordinary catalog remains available on older servers. */
        });
    }
    try {
      const r = await api(
        "/codex/threads" +
          (more ? "?cursor=" + encodeURIComponent(threadCursor!) : ""),
      );
      if (request !== listRequest.current) return;
      setListError("");
      setOptionsError("");
      if (!more) listFetchedAt.current = Date.now();
      if (!more)
        setListNotice(
          [
            r.leamHandoffsUnavailable
              ? "Some handoff sessions could not be checked."
              : "",
            r.leamHandoffsTruncated
              ? "The latest 10 accepted handoffs are checked here. Older handoffs remain accessible from their linked proposal."
              : "",
          ]
            .filter(Boolean)
            .join(" "),
        );
      const receivedCursor = r.nextCursor || null;
      const next =
        more && receivedCursor === threadCursor ? null : receivedCursor;
      latestThreadCursor.current = next;
      setThreads((previous) => {
        const rows = more
          ? [
              ...new Map(
                [...previous, ...(r.data || [])].map((item) => [item.id, item]),
              ).values(),
            ]
          : r.data || [];
        const allowed = rows.filter(
          (item: Data) =>
            item.transport !== "ide-owner" || bindingAllows(item.id),
        );
        const shared = previous.find(
          (item) => item.transport === "ide-owner" && bindingAllows(item.id),
        );
        if (shared && !allowed.some((item: Data) => item.id === shared.id))
          allowed.unshift(shared);
        const chosen = previous.find((item) => item.id === selected.current);
        if (
          chosen &&
          (chosen.transport !== "ide-owner" || bindingAllows(chosen.id)) &&
          !allowed.some((item: Data) => item.id === chosen.id)
        )
          allowed.unshift(chosen);
        rememberChat("coding:threads", { threads: allowed, nextCursor: next });
        return allowed;
      });
      setThreadCursor(next);
    } catch (e) {
      if (request === listRequest.current) {
        setListError(e instanceof Error ? e.message : String(e));
        reportError?.(e);
      }
    } finally {
      if (request === listRequest.current) {
        listBusy.current = false;
        setListing(false);
        setListCheckedAt(Date.now());
      }
    }
  }
  function changedConversation(id: string, value: Data | null) {
    listRequest.current++;
    listBusy.current = false;
    setListing(false);
    setThreads((previous) => {
      const rows = value
        ? previous.map((item) =>
            item.id === id ? { ...item, ...value } : item,
          )
        : previous.filter((item) => item.id !== id);
      rememberChat("coding:threads", {
        threads: rows,
        nextCursor: threadCursor,
      });
      return rows;
    });
    if (!value) {
      // Native delete includes descendants; retire every Coding display snapshot
      // and selection before refreshing. Unrelated drafts remain in session storage.
      forgetChatPrefix("coding:");
      rememberSession("coding:draft:" + id, "");
      try {
        sessionStorage.removeItem("leam-submission:" + id);
      } catch {
        /* Deletion already succeeded. */
      }
      selected.current = "";
      setThread(null);
      setTurns([]);
      setText("");
      setActive("");
      setConnected(false);
      submission.current = null;
      updateEchoes(id, []);
      rememberSession("coding:selected", null);
      setThreads([]);
      latestThreadCursor.current = null;
      setThreadCursor(null);
      void load();
      return;
    }
    setThread((previous) => {
      if (previous?.id !== id) return previous;
      const next = { ...previous, ...value };
      rememberSession("coding:selected", {
        id: next.id,
        name: next.name,
        cwd: next.cwd,
        transport: next.transport,
      });
      return next;
    });
  }
  async function history(id: string, allowFresh = false) {
    const revision = streamRevision.current;
    const saved = cachedChat("coding:" + id);
    const fresh =
      allowFresh && saved && Date.now() - (saved.validatedAt || 0) < 5000;
    const r = fresh
      ? { data: [...(saved.turns || [])].reverse(), nextCursor: saved.cursor }
      : await chatRead(`/codex/threads/${id}/turns`, revision);
    if (selected.current === id && revision === streamRevision.current) {
      const current = await chatRead(`/codex/threads/${id}`, revision);
      if (selected.current === id && revision === streamRevision.current) {
        // Publish transcript and current execution state together. A final reply
        // must not reach voice playback while an earlier active flag remains.
        if (!fresh) validatedAt.current = Date.now();
        const next = selectedShared.current
          ? [...(r.data || [])].reverse()
          : projection.current.reconcile(
              [...(r.data || [])].reverse(),
              currentTurns.current,
            );
        if (
          selectedShared.current &&
          !current.connected &&
          !next.length &&
          currentTurns.current.length
        ) {
          setDisplayNotice(
            "Showing the last loaded conversation while the shared connection recovers.",
          );
        } else {
          setTurns(next);
          setCursor(r.nextCursor || null);
          setDisplayNotice(
            current.connected
              ? ""
              : "Showing the last loaded conversation while the connection recovers.",
          );
        }
        const bindingValid = !selectedShared.current || bindingAllows(id);
        if (!bindingValid) setDisplayNotice(missingBindingNotice);
        setConnected(Boolean(current.connected) && bindingValid);
        setThread((previous) =>
          previous?.id === id
            ? {
                ...previous,
                ...current.thread,
                generation: current.generation,
                truncated: current.truncated,
                connectionError: current.error,
              }
            : previous,
        );
        setActive(
          current.activeTurnId ||
            next.find((t: Data) => t.status === "inProgress")?.id ||
            "",
        );
      }
    }
  }
  async function choose(t: Data) {
    stopConversation();
    stopRecognition();
    const sameThread = selected.current === t.id;
    if (!sameThread) {
      streamRevision.current++;
      projection.current = new CodingStreamProjection();
    }
    selected.current = t.id;
    if (!sameThread) updateEchoes(t.id, codingEchoes(t.id));
    // An accepted handoff can be readable before native thread/list indexes it.
    // Keep its supplied identity visible while list refresh reconciles metadata.
    setThreads((previous) => {
      const rows = previous.some((item) => item.id === t.id)
        ? previous.map((item) => (item.id === t.id ? { ...item, ...t } : item))
        : [t, ...previous];
      rememberChat("coding:threads", {
        threads: rows,
        nextCursor: threadCursor,
      });
      return rows;
    });
    selectedShared.current = t.transport === "ide-owner";
    try {
      submission.current = JSON.parse(
        sessionStorage.getItem("leam-submission:" + t.id) || "null",
      );
    } catch {
      submission.current = null;
    }
    setText(
      sessionValue<string | null>("coding:draft:" + t.id, null) ??
        submission.current?.text ??
        "",
    );
    chatScroll.follow();
    rememberSession("coding:selected", {
      id: t.id,
      name: t.name,
      cwd: t.cwd,
      transport: t.transport,
    });
    const saved = cachedChat("coding:" + t.id);
    validatedAt.current = saved?.validatedAt || 0;
    setThread(t);
    setConnected(false);
    const savedTurns = saved?.turns || (sameThread ? currentTurns.current : []);
    setDisplayNotice(
      savedTurns.length
        ? "Showing saved conversation while checking the connection."
        : "",
    );
    setTurns(
      selectedShared.current
        ? savedTurns
        : projection.current.reconcile(savedTurns),
    );
    setActive("");
    setCursor(saved?.cursor || null);
    setGoal(saved?.goal || null);
    try {
      await history(t.id, true);
      const g = await chatRead(`/codex/threads/${t.id}/goal`);
      if (selected.current === t.id) setGoal(g.goal);
    } catch (e) {
      fail(e);
    }
  }
  useEffect(() => {
    load();
    if (restored.current) void choose(restored.current);
    return () => {
      selected.current = "";
      listRequest.current++;
      streamRevision.current++;
    };
  }, []);
  useEffect(() => {
    const id = thread?.id;
    if (!id) return;
    const shared = selectedShared.current;
    let closed = false,
      opened = false,
      lastSequence = 0;
    const stream = new EventSource(
      "/api/events" + (shared ? "" : "?thread_id=" + encodeURIComponent(id)),
    );
    const current = () => !closed && selected.current === id;
    const requests = () =>
      api("/codex/requests")
        .then((r) => {
          if (current()) setRequests(r.items);
        })
        .catch(fail);
    const schedule = (delay = 1000) => {
      if (refreshTimer.current) return;
      refreshTimer.current = setTimeout(() => {
        refreshTimer.current = null;
        if (current()) history(id).catch(fail);
      }, delay);
    };
    stream.onmessage = (e) => {
      if (!current()) return;
      let event: Data;
      try {
        event = JSON.parse(e.data);
      } catch {
        fail(
          new Error(
            "Could not read a Coding event. Refresh this conversation.",
          ),
        );
        return;
      }
      if (typeof event.id === "number") {
        if (event.id <= lastSequence) return;
        lastSequence = event.id;
      }
      if (event.topic === "codex.shared") {
        if (!shared || event.payload?.threadId !== id) return;
        streamRevision.current++;
        if (!event.payload.connected) setConnected(false);
        if (!refreshTimer.current)
          refreshTimer.current = setTimeout(() => {
            refreshTimer.current = null;
            if (!current()) return;
            history(id).catch(fail);
            void requests();
            api(`/codex/threads/${id}/goal`)
              .then((r) => {
                if (current()) setGoal(r.goal);
              })
              .catch(fail);
          }, 150);
        return;
      }
      if (event.topic === "codex.connection") {
        if (shared) return;
        streamRevision.current++;
        setConnected(false);
        setActive("");
        return;
      }
      if (event.topic !== "codex" || shared) return;
      const packet = event.payload,
        p = packet?.params || {};
      if (p.threadId !== id) return;
      if (
        [
          "turn/started",
          "turn/completed",
          "item/started",
          "item/completed",
          "item/agentMessage/delta",
        ].includes(packet.method)
      ) {
        const result = projection.current.receive(packet.method, p);
        if (result === "unknown") schedule();
        if (result === "applied") {
          streamRevision.current++;
          validatedAt.current = 0;
          if (packet.method === "turn/completed") {
            // A terminal event can carry no items. Wait for full history and
            // current execution metadata before making a reply voice-final.
            if (refreshTimer.current) clearTimeout(refreshTimer.current);
            refreshTimer.current = null;
            history(id).catch(fail);
          } else {
            setTurns(projection.current.reconcile(currentTurns.current));
            if (packet.method === "turn/started") setActive(p.turn.id);
          }
        }
      }
      if (packet.id !== undefined || packet.method === "serverRequest/resolved")
        void requests();
    };
    stream.onopen = () => {
      if (!current()) return;
      // Keep event-built partials on reconnect. SSE replays missed sequence IDs;
      // an overlapping snapshot never becomes a delta's starting text.
      streamRevision.current++;
      const useFresh = Boolean(restored.current) && !opened;
      opened = true;
      history(id, useFresh).catch(fail);
      void requests();
    };
    stream.onerror = () => {
      if (!current()) return;
      setConnected(false);
      if (currentTurns.current.length)
        setDisplayNotice(
          "Showing the last loaded conversation while the connection recovers.",
        );
    };
    return () => {
      closed = true;
      stream.close();
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    };
  }, [thread?.id]);
  useEffect(() => {
    if (thread && selected.current === thread.id)
      rememberChat("coding:" + thread.id, {
        turns,
        cursor,
        goal,
        validatedAt: validatedAt.current,
      });
  }, [thread, turns, cursor, goal]);
  useEffect(() => {
    if (thread && selected.current === thread.id)
      rememberSession("coding:draft:" + thread.id, text);
  }, [thread, text]);
  async function submitText(value: string): Promise<Receipt> {
    if (
      !thread ||
      (!value.trim() && !files.ids.length) ||
      uploading ||
      submitting.current ||
      !connected ||
      (thread.transport === "ide-owner" && !bindingAllows(thread.id))
    )
      throw new Error(
        "Connect this session and wait for the current submission before sending.",
      );
    submitting.current = true;
    const threadId = thread.id;
    const authGeneration = sessionGeneration();
    let attemptId = "";
    setBusy(true);
    try {
      if (submission.current && submission.current.text !== value)
        throw new Error(
          "The last message has an uncertain outcome. Inspect the conversation before editing and resubmitting.",
        );
      submission.current ||= {
        id: crypto.randomUUID(),
        text: value,
        attachmentIds: files.ids,
      };
      setText(value);
      const attempt = submission.current;
      attemptId = attempt.id;
      updateEchoes(threadId, (previous) => [
        ...previous.filter((echo) => echo.id !== attempt.id),
        {
          id: attempt.id,
          text: attempt.text,
          state: "sending",
        },
      ]);
      chatScroll.follow();
      sessionStorage.setItem(
        "leam-submission:" + threadId,
        JSON.stringify(attempt),
      );
      let pending = await api(`/codex/submissions/${attempt.id}`);
      if (pending.state === "pending")
        pending = await api(
          `/codex/threads/${threadId}/submissions/${attempt.id}/reconcile`,
          "POST",
          {
            text: attempt.text,
            ...(attempt.expectedTurnId
              ? { expectedTurnId: attempt.expectedTurnId }
              : {}),
            ...(attempt.attachmentIds?.length
              ? { attachmentIds: attempt.attachmentIds }
              : {}),
          },
        );
      if (pending.state === "pending")
        throw new Error(
          "Your previous submission may already be running. No matching delivery receipt was found; inspect the conversation before setting this draft aside.",
        );
      // Capture exact displayed assistant text before dispatch. Only a known
      // current turn permits automatic readout after shared steering.
      const baselines = new Map(currentTurns.current.filter((turn) => turn.id === active).map((turn) => [turn.id,
        (turn.items || []).filter((item: Data) => item.type === "agentMessage" && typeof item.id === "string" && typeof item.text === "string")
          .map((item: Data) => ({ messageId: item.id, text: item.text })),
      ]));
      const r =
        pending.state === "complete"
          ? pending.result
          : await api(`/codex/threads/${threadId}/turns`, "POST", {
              text: attempt.text,
              requestId: attempt.id,
              ...(attempt.expectedTurnId
                ? { expectedTurnId: attempt.expectedTurnId }
                : {}),
              ...(attempt.attachmentIds?.length
                ? { attachmentIds: attempt.attachmentIds }
                : {}),
              ...(thread.transport === "ide-owner"
                ? { generation: thread.generation }
                : {}),
            });
      if (
        typeof r.turn?.id !== "string" ||
        !r.turn.id.trim() ||
        r.turn.id.length > 256
      )
        throw new Error(
          "Delivery has no matching turn receipt. Check the conversation before retrying.",
        );
      sessionStorage.removeItem("leam-submission:" + threadId);
      updateEchoes(threadId, (previous) =>
        unrepresentedEchoes(
          previous.map((echo) =>
            echo.id === attempt.id
              ? {
                  ...echo,
                  state: "accepted",
                  turnId: r.turn.id,
                }
              : echo,
          ),
          selected.current === threadId ? currentTurns.current : [],
        ),
      );
      clearAcceptedDraft("coding", threadId, attempt.text);
      files.clear(attempt.attachmentIds || []);
      if (selected.current === threadId) {
        submission.current = null;
        chatScroll.follow();
        setText((value) => (value === attempt.text ? "" : value));
        setActive(r.turn?.id || "");
        void history(threadId).catch(fail);
      }
      return {
        outcome: "submitted",
        run_id: r.turn.id,
        autoReply: r.operation !== "steer" || (pending.state !== "complete" && baselines.has(r.turn.id)),
        ...(r.operation === "steer" && pending.state !== "complete" && baselines.has(r.turn.id)
          ? { readoutId: attempt.id, readoutBaseline: baselines.get(r.turn.id)! }
          : {}),
      };
    } catch (e) {
      if (authGeneration === sessionGeneration() && attemptId)
        updateEchoes(threadId, (previous) =>
          previous.map((echo) =>
            echo.id === attemptId ? { ...echo, state: "uncertain" } : echo,
          ),
        );
      fail(e);
      throw e;
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  async function send(e: React.FormEvent) {
    e.preventDefault();
    stopConversation();
    stopRecognition();
    try {
      await submitText(text);
    } catch {
      /* Saved receipt/draft remains available. */
    }
  }
  const latestReply = turns.flatMap((turn) => (turn.items || [])
    .filter((item: Data) => item.type === "agentMessage" && typeof item.text === "string" && item.text.trim())
    .map((item: Data) => ({ turn, item }))).at(-1);
  return (
    <section className={"coding-page " + (thread ? "has-thread" : "")}>
      <div className="page-heading">
        <div>
          <span className="eyebrow">BUILD WITH CODEX</span>
          <h1>{thread ? "Your workspace" : "Pick up a thread."}</h1>
          <p>Your coding sessions, directly connected.</p>
        </div>
        <button
          className="icon-button"
          aria-label="Refresh sessions"
          onClick={() => void load()}
        >
          <RefreshCw size={18} />
        </button>
      </div>
      <div className="coding-layout">
        <div className="chat-toolbar" aria-label="Coding chat controls">
        <ConversationList
          kind="coding"
          catalogStatus={<div className="coding-catalog-status" aria-label="Session catalog status">
            {listNotice && <p role="status">{listNotice}</p>}
            {listError && <p role="alert">{listError}</p>}
            {listCheckedAt !== null && <small>Last {listError ? "attempt" : "check"}: <time dateTime={new Date(listCheckedAt).toISOString()}>{new Date(listCheckedAt).toLocaleTimeString()}</time></small>}
            <button type="button" className="secondary" disabled={listing} onClick={() => void load()} aria-label="Refresh session list">{listing ? "Checking sessions…" : "Refresh session list"}</button>
          </div>}
          items={threads}
          selected={thread?.id || ""}
          selectedItem={thread}
          loading={listing}
          hasMore={!!threadCursor}
          loadMore={() => void load(true)}
          onOpen={() => {
            if (Date.now() - listFetchedAt.current >= 15000) void load();
          }}
          onSelect={(item) => void choose(item)}
          onChanged={changedConversation}
        />
        {thread && <MainCodingControl key={thread.id} thread={thread} draft={text} onChanged={() => void load()} />}
        <ChatDialog label="Coding chat options">
          <p><strong>{thread?.name || "Coding sessions"}</strong></p>
          {thread && <>
            <p>{active ? "Working" : connected ? "Connected" : "Read only"}</p>
            <p className="chat-path">{thread.cwd}</p>
            <p>{thread.model || "Owner model"} · {thread.reasoningEffort || "Owner reasoning"}</p>
            {goal && <section><h3>Build goal · {goal.status}</h3><p>{goal.objective}</p></section>}
            {thread.transport === "ide-owner" && <p>Continue this same conversation here or in Codex. Messages sent while Codex is working become follow-ups.{thread.truncated && " Showing the latest 50 messages; long messages may be shortened."}</p>}
          </>}
          <button className="secondary" disabled={listing} onClick={() => { setOptionsError(""); void load(false, (e) => setOptionsError(e instanceof Error ? e.message : String(e))); }}>Refresh sessions</button>
          {optionsError && <p role="alert">{optionsError}</p>}
        </ChatDialog>
      <ChatDialog label="Start a new coding session" icon={<Plus size={20} />} closeSignal={createdSession}>
        <form
          className="quick-add"
          onSubmit={async (e) => {
            e.preventDefault();
            if (createInFlight.current) return;
            createInFlight.current = true;
            setCreatingSession(true);
            setCreateError("");
            try {
              const result = await api("/codex/threads", "POST", {
                cwd: newWorkspace,
              });
              await load();
              await choose(result.thread);
              setCreatedSession((value) => value + 1);
            } catch (e) {
              setCreateError(e instanceof Error ? e.message : String(e));
            } finally {
              createInFlight.current = false;
              setCreatingSession(false);
            }
          }}
        >
          <input
            aria-label="Workspace directory"
            disabled={creatingSession}
            placeholder="/home/you/Github/project"
            value={newWorkspace}
            onChange={(e) => setNewWorkspace(e.target.value)}
            required
          />
          <button className="secondary" disabled={creatingSession}>{creatingSession ? "Creating…" : "Create session"}</button>
          {createError && <p role="alert">{createError}</p>}
        </form>
      </ChatDialog>
        </div>
        {thread ? (
          <div className="conversation">
            {thread.connectionError && (
              <p role="alert" className="error">
                {thread.connectionError}
              </p>
            )}
            {displayNotice && <p role="status">{displayNotice}</p>}
            {!connected && (
              <div className="handoff">
                <p>
                  {thread.transport === "ide-owner"
                    ? "Keep the original Codex window open to reconnect this shared conversation."
                    : "To continue here, first finish the active turn in your original Codex window."}
                </p>
                <button
                  className="secondary"
                  disabled={
                    thread.transport === "ide-owner" &&
                    sharedBindingId !== undefined &&
                    sharedBindingId !== thread.id
                  }
                  onClick={async () => {
                    if (
                      thread.transport === "ide-owner" &&
                      !bindingAllows(thread.id)
                    )
                      return;
                    try {
                      await api(`/codex/threads/${thread.id}/connect`, "POST", {
                        handoffConfirmed: true,
                      });
                      if (selected.current === thread.id) {
                        setConnected(
                          thread.transport !== "ide-owner" ||
                            bindingAllows(thread.id),
                        );
                        await history(thread.id);
                      }
                    } catch (e) {
                      fail(e);
                    }
                  }}
                >
                  {thread.transport === "ide-owner"
                    ? "Reconnect shared session"
                    : "Continue this session here"}
                </button>
              </div>
            )}
            <div
              className="messages"
              ref={chatScroll.viewport}
              onScroll={chatScroll.onScroll}
            >
              <div ref={chatScroll.content}>
                {cursor && (
                  <button
                    className="secondary"
                    onClick={async () => {
                      try {
                        const r = await api(
                          `/codex/threads/${thread.id}/turns?cursor=${encodeURIComponent(cursor)}`,
                        );
                        if (selected.current === thread.id) {
                          chatScroll.preservePrepend();
                          setTurns((v) => [...(r.data || []).reverse(), ...v]);
                          setCursor(r.nextCursor || null);
                        }
                      } catch (e) {
                        fail(e);
                      }
                    }}
                  >
                    Earlier messages
                  </button>
                )}
                {turns.map((t) => (
                  <React.Fragment key={t.id}>
                    <TurnMessages turn={t} threadId={thread.id} />
                  </React.Fragment>
                ))}
                {echoes.map((echo) => (
                  <article
                    className="message user"
                    key={echo.id}
                    data-local-submission={echo.id}
                  >
                    <span className="message-label">YOU</span>
                    <div className="prose">
                      {echo.text || "Attachment message"}
                    </div>
                    <small role="status">
                      {echo.state === "sending"
                        ? "Sending — waiting for a delivery receipt."
                        : echo.state === "accepted"
                          ? "Accepted — waiting for the conversation to refresh."
                          : "Delivery uncertain — check the conversation before retrying."}
                    </small>
                  </article>
                ))}
                {requests
                  .filter((r) => r.params?.threadId === thread.id)
                  .map((r) => (
                    <RequestCard
                      key={r.id}
                      request={r}
                      onAnswer={async (body) => {
                        try {
                          await api(`/codex/requests/${r.id}`, "POST", body);
                          setRequests((v) =>
                            v.map((x) =>
                              x.id === r.id ? { ...x, submitted: true } : x,
                            ),
                          );
                        } catch (e) {
                          fail(e);
                        }
                      }}
                    />
                  ))}
              </div>
            </div>
            {submission.current && !busy && (
              <div className="notice">
                A saved submission needs checking. Send checks its delivery
                receipt before retrying.
                <button
                  className="secondary"
                  onClick={() => history(thread.id).catch(fail)}
                >
                  Refresh conversation
                </button>
                <button
                  className="secondary"
                  onClick={() => {
                    if (
                      !window.confirm(
                        "Have you checked the conversation? This removes the saved draft, but cannot cancel or undo a message that may already be running. It will not resend it.",
                      )
                    )
                      return;
                    sessionStorage.removeItem("leam-submission:" + thread.id);
                    if (submission.current)
                      updateEchoes(thread.id, (previous) =>
                        previous.filter(
                          (echo) => echo.id !== submission.current?.id,
                        ),
                      );
                    submission.current = null;
                    setText("");
                  }}
                >
                  Set draft aside
                </button>
              </div>
            )}
            <form className="composer" onSubmit={send}>
              <AttachmentComposer
                compact
                key={"attachments:" + thread.id}
                items={files.items}
                onChange={files.change}
                disabled={busy || !!submission.current}
                onBusyChange={setUploading}
              />
              <textarea
                aria-label="Message Codex"
                placeholder={
                  connected
                    ? "Continue with Codex…"
                    : "Connect this session to continue"
                }
                value={text}
                onChange={(e) => {
                  stopConversation(
                    "Conversation stopped to protect your draft.",
                  );
                  stopRecognition();
                  setText(e.target.value);
                }}
                rows={2}
              />
              <VoiceComposer
                key={thread.id}
                threadId={thread.id}
                playbackSource={{ module: "coding", threadId: thread.id }}
                draft={text}
                onDraft={setText}
                disabled={
                  !connected ||
                  busy ||
                  uploading ||
                  (!!active && thread.transport !== "ide-owner") ||
                  !!submission.current
                }
                replyPending={!!active}
                replayReply={latestReply ? {
                  text: latestReply.item.text,
                  final: latestReply.turn.status === "completed",
                  runId: latestReply.turn.id,
                  messageId: latestReply.item.id,
                } : undefined}
                dictationDisabled={busy || !!submission.current}
                submit={submitText}
                messages={turns.flatMap((turn) => {
                  // A completed turn's final agent message is the user-facing answer;
                  // earlier commentary and all non-agent items must never autoplay.
                  const answers = (turn.items || []).filter(
                    (item: Data) => item.type === "agentMessage",
                  );
                  const last =
                    answers
                      .filter((item: Data) => item.phase === "final_answer")
                      .at(-1) ||
                    (turn.status === "completed" ? answers.at(-1) : undefined);
                  return last
                    ? [
                        {
                          runId: turn.id,
                          messageId: last.id,
                          text: last.text || "",
                          final: turn.status === "completed",
                          proseElementId: `coding-message-${last.id}`,
                        },
                      ]
                    : [];
                })}
                runs={turns.map((turn) => ({
                  id: turn.id,
                  terminal: ["completed", "failed", "interrupted"].includes(
                    turn.status,
                  ),
                  failed: ["failed", "interrupted"].includes(turn.status),
                }))}
              />
              <div>
                <small>
                  Direct to Codex ·{" "}
                  {active ? "Working" : "Your workspace, your tools"}
                </small>
                {active && thread.transport === "ide-owner" && (
                  <SharedStop
                    key={`${thread.generation}:${active}`}
                    turnId={active}
                    generation={thread.generation || ""}
                    connected={connected}
                  />
                )}
                {active && thread.transport !== "ide-owner" ? (
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Stop turn"
                    onClick={() =>
                      api(
                        `/codex/threads/${thread.id}/interrupt/${active}`,
                        "POST",
                        {},
                      ).catch(fail)
                    }
                  >
                    <Square size={18} />
                  </button>
                ) : (
                  <button
                    aria-label="Send message"
                    className="send"
                    disabled={
                      !connected ||
                      busy ||
                      uploading ||
                      (!text.trim() && !files.ids.length)
                    }
                  >
                    <ArrowUp size={20} />
                  </button>
                )}
              </div>
            </form>
          </div>
        ) : (
          <div className="conversation-placeholder">
            <div className="orbit">
              <Code2 size={32} />
            </div>
            <h2>Keep the thread.</h2>
            <p>
              Open an existing session to see its history,
              <br />
              then carry on from wherever you are.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
function TurnMessages({ turn, threadId }: { turn: Data; threadId: string }) {
  const groups: Data[][] = [];
  for (const item of turn.items || []) {
    const message = ["userMessage", "agentMessage"].includes(item.type);
    const last = groups[groups.length - 1];
    if (
      !message &&
      last &&
      !["userMessage", "agentMessage"].includes(last[0].type)
    )
      last.push(item);
    else groups.push([item]);
  }
  return (
    <>
      <AttachmentList items={turn.leamAttachments || []} />
      {groups.map((items) =>
        ["userMessage", "agentMessage"].includes(items[0].type) ? (
          <Message
            key={`${threadId}:${items[0].id}`}
            item={items[0]}
            final={turn.status === "completed"}
            playbackTarget={{
              module: "coding",
              threadId,
              runId: turn.id,
              messageId: items[0].id,
            }}
          />
        ) : (
          <details className="turn-activity" key={`${threadId}:${items[0].id}`}>
            <summary>Activity · {items.length}</summary>
            {items.map((item) => (
              <Message key={item.id} item={item} />
            ))}
          </details>
        ),
      )}
    </>
  );
}
function Message({
  item,
  final = false,
  playbackTarget,
}: {
  item: Data;
  final?: boolean;
  playbackTarget?: PlaybackTarget;
}) {
  if (item.type === "userMessage")
    return (
      <article className="message user">
        <span className="message-label">YOU</span>
        <div className="prose">
          {(item.content || []).map((c: Data) => c.text || "").join("\n")}
        </div>
        {item.deliveryStatus && (
          <small>Codex follow-up status: {String(item.deliveryStatus)}</small>
        )}
      </article>
    );
  if (item.type === "agentMessage")
    return (
      <article id={`coding-message-${item.id}`} className="message assistant">
        <span className="message-label">CODEX</span>
        <div className="prose markdown">
          <ReactMarkdown components={{ a: ({ node: _node, ...props }) => <ArtifactLink {...props} /> }} remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
        </div>
        {playbackTarget && item.text?.trim() && (
          <ReadAloud text={item.text} target={playbackTarget} final={final} />
        )}
      </article>
    );
  if (item.type === "reasoning") return null;
  return (
    <details className="tool-item">
      <summary>
        {item.type.replace(/([A-Z])/g, " $1")} <span>{item.status || ""}</span>
      </summary>
      <pre>
        {item.command || item.aggregatedOutput || JSON.stringify(item, null, 2)}
      </pre>
    </details>
  );
}
function RequestCard({
  request,
  onAnswer,
}: {
  request: Data;
  onAnswer: (body: Data) => void;
}) {
  const p = request.params || {},
    [answers, setAnswers] = useState<Record<string, string>>({});
  if (request.transport === "ide-owner")
    return (
      <SharedRequest
        key={`${request.generation}:${request.id}`}
        request={request as any}
      />
    );
  if (request.unsupported)
    return (
      <div className="request-card">
        <h3>Input needed in Codex</h3>
        <p>{request.detail}</p>
        <small>{request.method}</small>
      </div>
    );
  if (request.submitted)
    return (
      <div className="request-card">
        <h3>Response sent</h3>
        <p>Waiting for Codex to confirm this request is resolved.</p>
      </div>
    );
  if (request.method === "item/tool/requestUserInput")
    return (
      <div className="request-card">
        <h3>Codex needs your input</h3>
        {(p.questions || []).map((q: Data) => (
          <label key={q.id}>
            {q.question}
            {q.options?.length > 0 && (
              <select
                value={answers[q.id] || ""}
                onChange={(e) =>
                  setAnswers({ ...answers, [q.id]: e.target.value })
                }
              >
                <option value="">Choose an answer</option>
                {q.options.map((o: Data) => (
                  <option key={o.label}>{o.label}</option>
                ))}
              </select>
            )}
            <input
              aria-label={q.header || q.question}
              placeholder="Or write your answer"
              value={answers[q.id] || ""}
              onChange={(e) =>
                setAnswers({ ...answers, [q.id]: e.target.value })
              }
            />
          </label>
        ))}
        <button
          className="primary"
          disabled={p.questions?.some((q: Data) => !answers[q.id]?.trim())}
          onClick={() =>
            onAnswer({
              answers: Object.fromEntries(
                Object.entries(answers).map(([k, v]) => [k, { answers: [v] }]),
              ),
            })
          }
        >
          Reply
        </button>
      </div>
    );
  if (
    [
      "item/commandExecution/requestApproval",
      "item/fileChange/requestApproval",
    ].includes(request.method)
  )
    return (
      <div className="request-card">
        <h3>Approval requested</h3>
        <p>{p.reason || "Review this action before continuing."}</p>
        <pre>{p.command || JSON.stringify(p, null, 2)}</pre>
        <div className="actions">
          <button
            className="primary"
            onClick={() => onAnswer({ decision: "accept" })}
          >
            Allow once
          </button>
          <button
            className="secondary"
            onClick={() => onAnswer({ decision: "decline" })}
          >
            Decline
          </button>
        </div>
      </div>
    );
  return (
    <div className="request-card">
      <h3>Input required in Codex</h3>
      <p>
        This request type is not supported here yet: {request.method}. This turn
        is waiting; stop it before continuing in another client. No permission
        has been granted automatically.
      </p>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
if ("serviceWorker" in navigator)
  window.addEventListener("load", () =>
    navigator.serviceWorker.register("/sw.js").catch(() => {}),
  );
