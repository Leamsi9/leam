import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Trash2, X, Check } from "lucide-react";
import { api, type Data } from "./api";

export function ConversationList({
  kind,
  items,
  selected,
  selectedItem,
  loading,
  hasMore,
  loadMore,
  onSelect,
  onChanged,
  onOpen,
  catalogStatus,
}: {
  kind: "companion" | "coding";
  items: Data[];
  selected: string;
  selectedItem?: Data | null;
  loading: boolean;
  hasMore: boolean;
  loadMore: () => void;
  onSelect: (item: Data) => void;
  onChanged: (id: string, value: Data | null) => void;
  onOpen?: () => void;
  catalogStatus?: ReactNode;
}) {
  const [scope, setScope] = useState("all");
  const [open, setOpen] = useState(!selected),
    [editing, setEditing] = useState("");
  const [title, setTitle] = useState(""),
    [error, setError] = useState("");
  const [busy, setBusy] = useState(false),
    [removing, setRemoving] = useState<Data | null>(null);
  const inflight = useRef(false),
    mounted = useRef(true),
    dialog = useRef<HTMLDialogElement>(null);
  const idOf = (item: Data) =>
    String(kind === "companion" ? item.thread_id : item.id);
  const titleOf = (item: Data) =>
    String(
      item.title ||
        item.name ||
        item.preview?.slice(0, 70) ||
        "Untitled conversation",
    );
  useEffect(() => {
    setOpen(!selected);
    setEditing("");
    setError("");
  }, [selected]);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );
  useEffect(() => {
    if (removing) dialog.current?.showModal();
  }, [removing]);
  async function rename(item: Data) {
    if (inflight.current || !title.trim()) return;
    inflight.current = true;
    setBusy(true);
    setError("");
    try {
      const value = await api(
        `/${kind === "companion" ? "companion" : "codex"}/threads/${encodeURIComponent(idOf(item))}`,
        "PATCH",
        { title, revision: item.title_revision || 0 },
      );
      if (mounted.current) {
        onChanged(idOf(item), value);
        setEditing("");
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      inflight.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function remove() {
    if (!removing || inflight.current) return;
    const id = idOf(removing);
    inflight.current = true;
    setBusy(true);
    setError("");
    try {
      await api(
        `/${kind === "companion" ? "companion" : "codex"}/threads/${encodeURIComponent(id)}`,
        "DELETE",
        kind === "companion"
          ? { confirmed: true }
          : { confirmed: true, deleteChildren: true, stopRunning: true },
      );
      if (mounted.current) {
        onChanged(id, null);
        setRemoving(null);
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      inflight.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  // A resumed session retains its native ID. Equal titles are not identity.
  const unique = [...new Map(items.map((item) => [idOf(item), item])).values()];
  const visible =
    kind === "coding" && scope === "leam"
      ? unique.filter(
          (item) =>
            item.leamPurpose ||
            item.transport === "ide-owner" ||
            item.originator === "leam" ||
            item.leamOrigin === "reviewed-companion-handoff",
        )
      : unique;
  const chosen =
    items.find((item) => idOf(item) === selected) ||
    (selectedItem && idOf(selectedItem) === selected ? selectedItem : null);
  return (
    <section
      className="conversation-list"
      aria-label={
        kind === "companion" ? "Companion conversations" : "Coding sessions"
      }
    >
      <button
        type="button"
        className="secondary conversation-list-toggle"
        aria-expanded={open}
        onClick={() => {
          if (!open) onOpen?.();
          setOpen(!open);
        }}
      >
        {open ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
        <span>
          {chosen
            ? titleOf(chosen)
            : kind === "companion"
              ? "Conversations"
              : "Sessions"}
        </span>
      </button>
      {open && (
        <div
          className="conversation-list-scroll"
          tabIndex={0}
          aria-label="Scrollable conversation list"
        >
          {catalogStatus}
          {kind === "coding" && (
            <label>
              Session catalog
              <select
                aria-label="Session catalog"
                value={scope}
                onChange={(event) => setScope(event.target.value)}
              >
                <option value="all">All Codex sessions</option>
                <option value="leam">Leam sessions</option>
              </select>
              {scope === "leam" && (
                <small>
                  Shows sessions explicitly linked to or created by Leam among
                  the loaded pages. Other Codex sessions remain in All Codex
                  sessions.
                </small>
              )}
            </label>
          )}
          {!visible.length && (
            <p role="status">
              {loading
                ? "Loading conversations…"
                : scope === "leam" && kind === "coding"
                  ? "No Leam sessions in the loaded pages."
                  : "No conversations yet."}
            </p>
          )}
          {visible.map((item) => {
            const id = idOf(item),
              name = titleOf(item),
              sharedThread = item.transport === "ide-owner",
              protectedThread =
                sharedThread || item.leamDeleteProtected === true,
              protectionReason = String(
                item.leamDeleteReason ||
                  "The shared coding owner is protected from deletion.",
              );
            return (
              <div
                className={`conversation-row ${id === selected ? "current" : ""}`}
                key={id}
                data-thread-id={id}
              >
                {editing === id ? (
                  <form
                    className="conversation-rename"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void rename(item);
                    }}
                  >
                    <input
                      aria-label="Conversation title"
                      autoFocus
                      maxLength={120}
                      value={title}
                      disabled={busy}
                      onChange={(e) => setTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape" && !busy) {
                          e.preventDefault();
                          setEditing("");
                          setError("");
                        }
                      }}
                    />
                    <button
                      type="submit"
                      className="icon-button"
                      aria-label="Save title"
                      disabled={busy || !title.trim()}
                    >
                      <Check size={18} />
                    </button>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Cancel rename"
                      disabled={busy}
                      onClick={() => {
                        setEditing("");
                        setError("");
                      }}
                    >
                      <X size={18} />
                    </button>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      className="conversation-title"
                      aria-label={`Rename ${name}`}
                      disabled={sharedThread || busy}
                      title={
                        sharedThread
                          ? "Manage the shared build title in Codex"
                          : "Rename conversation"
                      }
                      onClick={() => {
                        setEditing(id);
                        setTitle(name);
                        setError("");
                      }}
                    >
                      <span>{name}</span>
                      {kind === "coding" &&
                        item.leamOrigin === "reviewed-companion-handoff" && (
                          <small className="conversation-origin">
                            From Companion
                            {item.leamHandoffTitle &&
                            item.leamHandoffTitle !== name
                              ? ` · ${item.leamHandoffTitle}`
                              : ""}
                          </small>
                        )}
                      <small>{formatDate(item)}</small>
                      {kind === "coding" && (
                        <small>
                          {purposeLabel(item)} · ID {id.slice(-8)}
                        </small>
                      )}
                      {kind === "coding" && protectedThread && (
                        <small>{protectionReason}</small>
                      )}
                    </button>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={`Open ${name}`}
                      disabled={busy}
                      onClick={() => {
                        setOpen(false);
                        onSelect(item);
                      }}
                    >
                      <ChevronRight size={20} />
                    </button>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={`Delete ${name}`}
                      disabled={busy || protectedThread}
                      title={
                        protectedThread
                          ? protectionReason
                          : "Delete conversation"
                      }
                      onClick={() => {
                        setRemoving(item);
                        setError("");
                      }}
                    >
                      <Trash2 size={18} />
                    </button>
                  </>
                )}
              </div>
            );
          })}
          {hasMore && (
            <button
              type="button"
              className="secondary"
              disabled={loading}
              onClick={loadMore}
            >
              {loading ? "Loading…" : "More conversations"}
            </button>
          )}
        </div>
      )}
      {error && !removing && <p role="alert">{error}</p>}
      {removing && (
        <dialog
          ref={dialog}
          className="editor-dialog card"
          aria-label="Delete conversation"
          onCancel={(e) => {
            e.preventDefault();
            if (!busy) {
              setRemoving(null);
              setError("");
            }
          }}
        >
          <h2>Delete {titleOf(removing)}?</h2>
          <p>
            {kind === "coding"
              ? "This permanently deletes this coding conversation and all its spawned child conversations, and stops their running work. Workspace files are not removed."
              : "This removes the conversation and its runtime history."}{" "}
            Any unsent draft in this browser will be removed. Memories,
            proposals, delivery records and backups remain.
          </p>
          <p>
            This cannot be undone.{" "}
            {kind === "companion"
              ? "A conversation that is still working cannot be deleted."
              : "Shared build and update-linked conversations are protected."}
          </p>
          {error && <p role="alert">{error}</p>}
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => {
                setRemoving(null);
                setError("");
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="danger"
              disabled={busy}
              onClick={() => void remove()}
            >
              {busy ? "Deleting…" : "Delete conversation"}
            </button>
          </div>
        </dialog>
      )}
    </section>
  );
}

function formatDate(item: Data) {
  const raw =
    item.created_at ??
    (typeof (item.recencyAt ?? item.updatedAt ?? item.createdAt) === "number"
      ? (item.recencyAt ?? item.updatedAt ?? item.createdAt) * 1000
      : null);
  if (raw == null) return "Date unavailable";
  const date = new Date(raw);
  return Number.isNaN(date.getTime())
    ? "Date unavailable"
    : date.toLocaleString();
}

function purposeLabel(item: Data) {
  if (item.leamMain) return "Main · coordinator";
  if (item.transport === "ide-owner") return "Shared owner";
  if (item.leamPurpose === "update") return "Updates ticket";
  if (
    item.leamPurpose === "handoff" ||
    item.leamOrigin === "reviewed-companion-handoff"
  )
    return "Companion handoff";
  if (item.leamPurpose === "leam-origin" || item.originator === "leam")
    return "Created by Leam";
  return item.source === "cli"
    ? "Codex CLI"
    : item.source === "vscode"
      ? "Codex editor"
      : "Codex session";
}
