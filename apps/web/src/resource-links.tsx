import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";
import { rememberSession, sessionValue } from "./session-cache";
import "./resource-links.css";

type TargetType = "commitment" | "feature" | "proposal";
type Target = {
  targetType: TargetType;
  targetId: string;
  title: string | null;
  state: string | null;
  available: boolean;
  location?: string;
  updateId?: string;
};
type Links = { resourceId: string; revision: number; items: Target[] };
type Mutation = {
  requestId: string;
  revision: number;
  operation: "link" | "unlink";
  targetType: TargetType;
  targetId: string;
};
const labels = {
  commitment: "Commitments",
  feature: "Feature tickets",
  proposal: "Approvals",
};
const failure = (reason: unknown) =>
  reason instanceof Error ? reason.message : "Unable to load linked resources.";

/** One authoritative domain record can appear in several product views. */
export function selectedResourceTarget(type: TargetType, id: string) {
  return new URLSearchParams(location.search).get(type) === id;
}
export function ResourceTarget({
  type,
  id,
  children,
}: {
  type: TargetType;
  id: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const selected = selectedResourceTarget(type, id);
  useEffect(() => {
    if (selected) {
      ref.current?.scrollIntoView({ block: "center" });
      ref.current?.focus({ preventScroll: true });
    }
  }, [selected]);
  return (
    <div
      ref={ref}
      tabIndex={selected ? -1 : undefined}
      className={
        selected
          ? "resource-target resource-target-selected"
          : "resource-target"
      }
    >
      {children}
    </div>
  );
}
function targetUrl(target: Target) {
  if (!target.available) return null;
  const view =
    target.targetType === "commitment"
      ? "goals"
      : target.targetType === "proposal"
        ? "approvals"
        : target.location === "backlog"
          ? "backlog"
          : "updates";
  const query = new URLSearchParams({
    view,
    [target.targetType]: target.targetId,
  });
  if (
    target.targetType === "feature" &&
    target.location === "updates" &&
    target.updateId
  )
    query.set("update", target.updateId);
  return `/?${query}`;
}

/** Closed panels issue no requests; closing keeps accepted operations and drafts alive. */
function LinkDisclosure({
  title,
  children,
}: {
  title: string;
  children: (open: boolean) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [visited, setVisited] = useState(false);
  return (
    <details
      className="resource-links"
      open={open}
      onToggle={(event) => {
        if (event.target !== event.currentTarget) return;
        setOpen(event.currentTarget.open);
        if (event.currentTarget.open) setVisited(true);
      }}
    >
      <summary>{title}</summary>
      {visited && children(open)}
    </details>
  );
}
export function ResourceAssociations({ resourceId }: { resourceId: string }) {
  return (
    <LinkDisclosure title="Linked work">
      {(open) => (
        <AssociationEditor
          key={resourceId}
          resourceId={resourceId}
          active={open}
        />
      )}
    </LinkDisclosure>
  );
}
function AssociationEditor({
  resourceId,
  active,
}: {
  resourceId: string;
  active: boolean;
}) {
  const endpoint = `/artifacts/${encodeURIComponent(resourceId)}/links`;
  const pendingKey = `resources:link-request:${resourceId}`;
  const [links, setLinks] = useState<Links | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Mutation | null>(() =>
    sessionValue(pendingKey, null),
  );
  const [type, setType] = useState<TargetType>("commitment");
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<Target[]>([]);
  const [searched, setSearched] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [searching, setSearching] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [appliedQuery, setAppliedQuery] = useState("");
  const alive = useRef(true),
    locked = useRef(false),
    readSequence = useRef(0),
    searchSequence = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      ++readSequence.current;
      ++searchSequence.current;
    };
  }, []);
  async function load() {
    const sequence = ++readSequence.current;
    setLoading(true);
    setError("");
    try {
      const value = await api(endpoint);
      if (alive.current && sequence === readSequence.current)
        setLinks(value as Links);
    } catch (reason) {
      if (alive.current && sequence === readSequence.current)
        setError(failure(reason));
    } finally {
      if (alive.current && sequence === readSequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (active) void load();
  }, [active]);
  async function search(more = false) {
    const sequence = ++searchSequence.current;
    const q = more ? appliedQuery : query.trim();
    setSearching(true);
    setSearchError("");
    if (!more) {
      setOptions([]);
      setCursor(null);
      setSearched(false);
    }
    const params = new URLSearchParams({ type, q, limit: "30" });
    if (more && cursor) params.set("cursor", cursor);
    try {
      const value = await api(`/resource-links/targets?${params}`);
      if (!alive.current || sequence !== searchSequence.current) return;
      setOptions((previous) =>
        more
          ? [
              ...new Map(
                [...previous, ...value.items].map((item) => [
                  item.targetId,
                  item,
                ]),
              ).values(),
            ]
          : value.items,
      );
      setCursor(value.nextCursor || null);
      setAppliedQuery(q);
      setSearched(true);
    } catch (reason) {
      if (alive.current && sequence === searchSequence.current)
        setSearchError(failure(reason));
    } finally {
      if (alive.current && sequence === searchSequence.current)
        setSearching(false);
    }
  }
  async function mutate(body: Mutation) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    setPending(body);
    rememberSession(pendingKey, body);
    ++readSequence.current;
    try {
      await api(endpoint, "POST", body);
      rememberSession(pendingKey, null);
      if (!alive.current) return;
      setPending(null);
      setNotice(
        body.operation === "link"
          ? "Resource linked. Original records are unchanged."
          : "Association removed. Resource and original record are unchanged.",
      );
      await load();
    } catch (reason) {
      if (!alive.current) return;
      // Only explicit rejection is conclusive. A lost response must retry the exact receipt key.
      if (
        reason instanceof ApiError &&
        reason.status &&
        [400, 401, 403, 404, 409, 422].includes(reason.status)
      ) {
        rememberSession(pendingKey, null);
        setPending(null);
        if (reason.status === 409) {
          await load();
          setError(
            "Linked work changed. Review the refreshed links, then try again.",
          );
        } else setError(failure(reason));
      } else
        setError(
          `Confirmation unavailable. Retry the saved change to check its outcome. ${failure(reason)}`,
        );
    } finally {
      locked.current = false;
      if (alive.current) setBusy(false);
    }
  }
  function change(operation: Mutation["operation"], target: Target) {
    if (!links || pending || busy) return;
    void mutate({
      requestId: crypto.randomUUID(),
      revision: links.revision,
      operation,
      targetType: target.targetType,
      targetId: target.targetId,
    });
  }
  return (
    <section aria-label="Resource associations">
      {loading && <p role="status">Loading linked work…</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {pending && (
        <div className="resource-link-pending">
          <p>
            A saved {pending.operation === "link" ? "link" : "removal"} is
            awaiting confirmation.
          </p>
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => void mutate(pending)}
          >
            Retry saved change
          </button>
        </div>
      )}
      <button
        type="button"
        className="secondary"
        disabled={loading || busy}
        onClick={() => void load()}
      >
        Refresh linked work
      </button>
      {links && (
        <ul className="resource-link-list">
          {links.items.map((target) => (
            <li key={`${target.targetType}:${target.targetId}`}>
              <div>
                {targetUrl(target) ? (
                  <a href={targetUrl(target)!}>
                    {target.title || "Untitled record"}
                  </a>
                ) : (
                  <strong>{target.title || "Record unavailable"}</strong>
                )}
                <small>
                  {labels[target.targetType]} ·{" "}
                  {target.available
                    ? target.state || "Status unavailable"
                    : "Deleted or unavailable"}
                </small>
              </div>
              <button
                type="button"
                className="secondary"
                disabled={busy || !!pending || loading}
                aria-label={`Unlink ${target.title || "unavailable record"}`}
                onClick={() => change("unlink", target)}
              >
                Unlink
              </button>
            </li>
          ))}
        </ul>
      )}
      {links && !links.items.length && <p>No linked work yet.</p>}
      <form
        className="resource-link-search"
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <label>
          Link to
          <select
            value={type}
            onChange={(event) => {
              ++searchSequence.current;
              setType(event.target.value as TargetType);
              setOptions([]);
              setCursor(null);
              setSearched(false);
              setSearching(false);
              setSearchError("");
            }}
          >
            {Object.entries(labels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Find work by name
          <input
            type="search"
            value={query}
            maxLength={200}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search titles"
          />
        </label>
        <button className="secondary" disabled={searching}>
          Find work
        </button>
      </form>
      {searchError && <p role="alert">{searchError}</p>}
      {searching && <p role="status">Finding matching work…</p>}
      {searched && !options.length && (
        <p>No matching work. Try another name or type.</p>
      )}
      <ul className="resource-link-list">
        {options.map((target) => {
          const linked = links?.items.some(
            (item) =>
              item.targetType === target.targetType &&
              item.targetId === target.targetId,
          );
          return (
            <li key={`${target.targetType}:${target.targetId}`}>
              <div>
                <strong>{target.title || "Untitled record"}</strong>
                <small>{target.state || "Status unavailable"}</small>
              </div>
              <button
                type="button"
                className="secondary"
                disabled={
                  busy ||
                  !!pending ||
                  loading ||
                  !links ||
                  linked ||
                  !target.available
                }
                aria-label={`Link ${target.title || "record"}`}
                onClick={() => change("link", target)}
              >
                {linked ? "Linked" : "Link"}
              </button>
            </li>
          );
        })}
      </ul>
      {cursor && (
        <button
          type="button"
          className="secondary"
          disabled={searching}
          onClick={() => void search(true)}
        >
          More matching work
        </button>
      )}
    </section>
  );
}
export function LinkedResources({
  targetType,
  targetId,
}: {
  targetType: TargetType;
  targetId: string;
}) {
  return (
    <LinkDisclosure title="Resources">
      {(open) => (
        <ResourceList
          key={`${targetType}:${targetId}`}
          targetType={targetType}
          targetId={targetId}
          active={open}
        />
      )}
    </LinkDisclosure>
  );
}
function ResourceList({
  targetType,
  targetId,
  active,
}: {
  targetType: TargetType;
  targetId: string;
  active: boolean;
}) {
  const [items, setItems] = useState<
    { id: string; title: string; kind: string }[] | null
  >(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setError("");
    setItems(null);
    const query = new URLSearchParams({ targetType, targetId });
    void api(`/resource-links?${query}`, "GET", undefined, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setItems(value.items);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(failure(reason));
      });
    return () => controller.abort();
  }, [targetType, targetId, active, refresh]);
  return (
    <div>
      {error && <p role="alert">{error}</p>}
      {!items && !error && <p role="status">Loading resources…</p>}
      {items && !items.length && (
        <p>
          No resources linked yet. Open a resource in the library and use Linked
          work to add this item.
        </p>
      )}
      <ul className="resource-link-list">
        {items?.map((item) => (
          <li key={item.id}>
            <a href={`/?artifact=${encodeURIComponent(item.id)}`}>
              {item.title}
              <small>{item.kind}</small>
            </a>
          </li>
        ))}
      </ul>
      <div className="resource-link-actions">
        <a href="/?view=resources">Open Resources library</a>
        <button
          type="button"
          className="secondary"
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh resources
        </button>
      </div>
    </div>
  );
}
