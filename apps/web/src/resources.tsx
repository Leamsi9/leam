import {
  useResourceUnread,
  markResourcesRead,
  refreshResourceUnread,
} from "./resource-unread";
import { ResourceDelete } from "./resource-delete";
import { ResourceAssociations } from "./resource-links";
import { useEffect, useRef, useState } from "react";
import {
  FileText,
  FileImage,
  Globe,
  Search,
  Download,
  RefreshCw,
  FolderOpen,
} from "lucide-react";
import { api } from "./api";
import {
  imageKinds,
  resourceLabels,
  resourceSize,
  type Artifact,
} from "./artifacts";
import { sessionValue, rememberSession } from "./session-cache";
import "./resources.css";

type PageResult = {
  items: Artifact[];
  nextCursor: string | null;
  total: number;
  storage?: { bytes: number; maxBytes: number; maxFileBytes: number };
};
const kinds = [
  ["", "All resources"],
  ["document", "Documents"],
  ["html", "Website"],
  ["image", "Images"],
  ["pdf", "PDF"], ["docx", "Word · DOCX"], ["xlsx", "Spreadsheet · XLSX"],
  ["pptx", "Presentation · PPTX"], ["markdown", "Markdown"], ["text", "Plain text"],
  ["png", "PNG"], ["jpeg", "JPEG"], ["webp", "WebP"],
];

function ResourceCard({
  item,
  unread,
  marking,
  mark,
  parentSort,
  onDeleted,
}: {
  item: Artifact;
  unread: boolean;
  marking: boolean;
  mark: () => void;
  parentSort: boolean;
  onDeleted: () => void;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const picture = imageKinds.has(item.kind);
  const Icon = picture ? FileImage : item.kind === "html" ? Globe : FileText;
  const id = encodeURIComponent(item.id);
  return (
    <article
      className="resource-card"
      data-unread={unread ? "true" : "false"}
      data-resource-kind={
        picture ? "image" : item.kind === "html" ? "webpage" : "document"
      }
    >
      <a
        className="resource-open"
        href={`/?artifact=${id}`}
        aria-label={`Open ${item.title}`}
      >
        <span className="resource-thumbnail" aria-hidden="true">
          {picture && !imageFailed ? (
            <img
              src={`/api/artifacts/${id}/preview`}
              alt=""
              loading="lazy"
              onError={() => setImageFailed(true)}
            />
          ) : (
            <Icon size={25} />
          )}
        </span>
        <span className="resource-copy">
          <strong>
            {unread && (
              <span
                className="resources-unread-dot"
                aria-label="Unread resource"
              />
            )}{" "}
            {item.title}
          </strong>
          <span className="resource-caption">
            {item.category === "attachment" && (
              <span className="resource-attachment-tag">Upload · </span>
            )}
            {resourceLabels[item.kind] || "File"}
            {item.bytes !== undefined && ` · ${resourceSize(item.bytes)}`}
          </span>
          {parentSort && (
            <small>
              {item.sortParent?.title || "No available parent ticket"}
            </small>
          )}
          <time
            title="Last modified"
            dateTime={new Date(
              (item.modifiedAt ?? item.publishedAt) * 1000,
            ).toISOString()}
          >
            {new Date(
              (item.modifiedAt ?? item.publishedAt) * 1000,
            ).toLocaleDateString(undefined, {
              dateStyle: "medium",
            })}
          </time>
        </span>
      </a>
      <a
        className="icon-button resource-download-icon"
        href={`/api/artifacts/${id}/download`}
        aria-label={`Download ${item.title}`}
        title="Download"
      >
        <Download size={19} />
      </a>
      {unread && (
        <button
          type="button"
          className="secondary resource-read-button"
          disabled={marking}
          aria-label={`Mark ${item.title} as read`}
          onClick={mark}
        >
          Mark read
        </button>
      )}
      <ResourceDelete item={item} onDeleted={onDeleted} />
      <ResourceAssociations resourceId={item.id} />
    </article>
  );
}

export function ResourcesPage() {
  const unread = useResourceUnread();
  const [marking, setMarking] = useState(false);
  const [readError, setReadError] = useState("");
  const reading = useRef(false);
  async function mark(ids: string[]) {
    if (reading.current || !ids.length) return;
    reading.current = true;
    setMarking(true);
    setReadError("");
    try {
      await markResourcesRead(ids);
    } catch (reason) {
      setReadError(
        reason instanceof Error
          ? reason.message
          : "Read state could not be saved.",
      );
    } finally {
      reading.current = false;
      setMarking(false);
    }
  }
  const restored = useRef(
    sessionValue<{ q: string; kind: string; sort?: string; order?: string; group?: string }>(
      "resources:filter",
      {
        q: "",
        kind: "",
      },
    ),
  );
  const [group, setGroup] = useState(restored.current.group === "uploads" || restored.current.kind === "attachment" ? "uploads" : "generated");
  const [input, setInput] = useState(restored.current.q);
  const [query, setQuery] = useState(restored.current.q);
  const [kind, setKind] = useState(
    kinds.some(([key]) => key === restored.current.kind)
      ? restored.current.kind
      : "",
  );
  const [sort, setSort] = useState(
    ["modified", "parent", "title", "type"].includes(restored.current.sort || "")
      ? restored.current.sort!
      : "modified",
  );
  const [order, setOrder] = useState(
    restored.current.order === "asc" ? "asc" : "desc",
  );
  const [result, setResult] = useState<PageResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [moreError, setMoreError] = useState("");
  const [moreLoading, setMoreLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  const pagePending = useRef(false);
  const moreController = useRef<AbortController | null>(null);
  function url(cursor?: string) {
    const params = new URLSearchParams({
      q: query,
      kind,
      group,
      sort,
      order,
      limit: "30",
    });
    if (cursor) params.set("cursor", cursor);
    return `/artifacts?${params}`;
  }
  useEffect(() => {
    rememberSession("resources:filter", { q: query, kind, group, sort, order });
    const current = ++generation.current;
    const controller = new AbortController();
    moreController.current?.abort();
    pagePending.current = false;
    setMoreLoading(false);
    setLoading(true);
    setError("");
    setMoreError("");
    setResult(null);
    void api(url(), "GET", undefined, controller.signal)
      .then((value) => {
        if (current === generation.current) setResult(value as PageResult);
      })
      .catch((reason) => {
        if (!controller.signal.aborted && current === generation.current)
          setError(reason.message || "Resources could not be loaded.");
      })
      .finally(() => {
        if (current === generation.current) setLoading(false);
      });
    return () => {
      ++generation.current;
      controller.abort();
      moreController.current?.abort();
    };
  }, [query, kind, group, sort, order, refresh]);
  async function more() {
    if (!result?.nextCursor || pagePending.current) return;
    const current = generation.current;
    pagePending.current = true;
    setMoreLoading(true);
    setMoreError("");
    const controller = new AbortController();
    moreController.current = controller;
    try {
      const value = (await api(
        url(result.nextCursor),
        "GET",
        undefined,
        controller.signal,
      )) as PageResult;
      if (current !== generation.current) return;
      setResult((previous) =>
        previous
          ? {
              ...value,
              items: [
                ...new Map(
                  [...previous.items, ...value.items].map((item) => [
                    item.id,
                    item,
                  ]),
                ).values(),
              ],
            }
          : value,
      );
    } catch (reason) {
      if (!controller.signal.aborted && current === generation.current)
        setMoreError(
          reason instanceof Error
            ? reason.message
            : "Could not load more resources.",
        );
    } finally {
      if (current === generation.current) {
        pagePending.current = false;
        setMoreLoading(false);
      }
    }
  }
  return (
    <section className="page resources-page" aria-label="Resources">
      <div className="resources-heading">
        <div>
          <span className="eyebrow">YOUR LIBRARY</span>
          <h1>Resources</h1>
          <p>Reports, pages, documents and images, ready to revisit.</p>
        </div>
        <button
          className="icon-button secondary"
          aria-label="Refresh resources"
          disabled={loading}
          onClick={() => {
            setRefresh((value) => value + 1);
            void refreshResourceUnread();
          }}
        >
          <RefreshCw size={20} />
        </button>
      </div>
      <nav aria-label="Resource sections" className="resources-sort">
        {[["generated", "Generated"], ["uploads", "Uploads"]].map(([value, label]) => (
          <button key={value} type="button" className={group === value ? "" : "secondary"}
            aria-pressed={group === value} onClick={() => setGroup(value)}>{label}</button>
        ))}
      </nav>
      <div className="resources-read-actions">
        {unread && <span>{unread.unreadCount} unread</span>}
        <button
          type="button"
          className="secondary"
          disabled={marking || !unread?.unreadIds.length}
          onClick={() => void mark([...(unread?.unreadIds || [])])}
        >
          Mark all resources read
        </button>
      </div>
      {readError && <p role="alert">{readError}</p>}
      <form
        className="resources-filters"
        onSubmit={(event) => {
          event.preventDefault();
          if (input.trim() === query) setRefresh((value) => value + 1);
          else setQuery(input.trim());
        }}
      >
        <label className="resource-search">
          Search resources
          <span>
            <Search size={18} aria-hidden="true" />
            <input
              aria-label="Search resources"
              type="search"
              maxLength={200}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="Find a title or filename"
            />
            <button className="secondary" type="submit">
              Search
            </button>
          </span>
        </label>
        <label>
          File type
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            {kinds.map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </form>
      <div className="resources-sort">
        <label>
          Sort resources
          <select
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="modified">Modification date</option>
            <option value="parent">Parent ticket · unlinked last</option>
            <option value="title">Alphabetical</option>
            <option value="type">File type</option>
          </select>
        </label>
        <label>
          Order
          <select
            value={order}
            onChange={(event) => setOrder(event.target.value)}
          >
            <option value="desc">
              {sort === "modified" ? "Newest first" : "Descending"}
            </option>
            <option value="asc">
              {sort === "modified" ? "Oldest first" : "Ascending"}
            </option>
          </select>
        </label>
      </div>
      {loading && <p role="status">Opening your library…</p>}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button
            className="secondary"
            onClick={() => {
              setRefresh((value) => value + 1);
              void refreshResourceUnread();
            }}
          >
            Retry resources
          </button>
        </div>
      )}
      {result && (
        <>
          <p className="resources-count" role="status">
            {result.total} {result.total === 1 ? "resource" : "resources"}
            {query || kind ? " matching your filters" : " saved"}
          </p>
          {!result.items.length ? (
            <div className="resources-empty">
              <FolderOpen size={34} aria-hidden="true" />
              <h2>
                {query || kind
                  ? "No matching resources"
                  : "Your library is ready"}
              </h2>
              <p>
                {query || kind
                  ? "Try another search or type."
                  : "Published reports, webpages, images and documents appear here. Shared resource links continue to work."}
              </p>
              {(query || kind) && (
                <button
                  className="secondary"
                  onClick={() => {
                    setInput("");
                    setQuery("");
                    setKind("");
                  }}
                >
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <div className="resource-grid">
              {result.items.map((item) => (
                <ResourceCard
                  key={item.id}
                  item={item}
                  unread={
                    unread
                      ? unread.unreadIds.includes(item.id)
                      : item.unread === true
                  }
                  marking={marking}
                  mark={() => void mark([item.id])}
                  parentSort={sort === "parent"}
                  onDeleted={() => setRefresh((value) => value + 1)}
                />
              ))}
            </div>
          )}
          {moreError && <p role="alert">{moreError}</p>}
          {result.nextCursor && (
            <button
              className="secondary resources-more"
              disabled={moreLoading}
              onClick={() => void more()}
            >
              {moreLoading
                ? "Loading more…"
                : moreError
                  ? "Retry loading more"
                  : "Load more resources"}
            </button>
          )}
          <details className="resources-about">
            <summary>About your library</summary>
            <p>
              These are private, saved copies. Open a resource to preview it or
              download the original file. PDF and Office documents open in your
              device’s document app after download.
            </p>
            <p>
              HTML runs in an isolated preview. Leam access, forms and external
              resource requests are blocked. Nothing here is sent to an external
              document viewer.
            </p>
            {result.storage && (
              <small>
                {resourceSize(result.storage.bytes)} of{" "}
                {resourceSize(result.storage.maxBytes)} used
              </small>
            )}
          </details>
        </>
      )}
    </section>
  );
}
