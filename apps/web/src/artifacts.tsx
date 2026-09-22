import { useResourceUnread, markResourcesRead } from "./resource-unread";
import { ResourceDelete } from "./resource-delete";
import { ResourceAssociations } from "./resource-links";
import { useEffect, useState, type ComponentProps } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, Download, ExternalLink } from "lucide-react";
import { api } from "./api";
import "./artifacts.css";

export type Artifact = {
  id: string;
  title: string;
  kind:
    | "markdown"
    | "html"
    | "text"
    | "png"
    | "jpeg"
    | "webp"
    | "pdf"
    | "docx"
    | "xlsx"
    | "pptx";
  filename?: string;
  category?: "attachment" | "generated";
  tags?: string[];
  sources?: { surface: string; threadId?: string; turnId?: string }[];
  sourcesTruncated?: boolean;
  unread?: boolean;
  readAt?: number | null;
  modifiedAt?: number;
  sortParent?: { targetType: string; targetId: string; title: string } | null;
  mimeType?: string;
  bytes?: number;
  source?: {
    surface?: string;
    threadId?: string;
    turnId?: string;
    toolCallId?: string;
  } | null;
  content?: string;
  publishedAt: number;
  sha256: string;
};

export const imageKinds = new Set(["png", "jpeg", "webp"]);
export const resourceLabels: Record<string, string> = {
  markdown: "Markdown report",
  html: "Website",
  text: "Text document",
  png: "PNG image",
  jpeg: "JPEG image",
  webp: "WebP image",
  pdf: "PDF",
  docx: "Word document",
  xlsx: "Spreadsheet",
  pptx: "Presentation",
};
export function resourceSize(bytes?: number) {
  if (bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "";
  return bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 * 1024
      ? `${(bytes / 1024).toFixed(1)} KiB`
      : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

/** Plain laptop paths must not silently navigate the PWA back to its home. */
export function ArtifactLink({
  href,
  children,
  ...props
}: ComponentProps<"a">) {
  if (href && /^(?:\/(?:home|tmp|Users|private|var|mnt)\/|file:)/i.test(href)) {
    return (
      <span className="unpublished-link">
        {children} <small>(local file; needs a published link)</small>
      </span>
    );
  }
  const artifact = href && /(?:^|[?&])artifact=[a-z0-9-]+(?:&|$)/.test(href);
  return (
    <a
      {...props}
      href={href}
      {...(artifact ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {children}
    </a>
  );
}

export function ArtifactViewer({ id }: { id: string }) {
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [deleted, setDeleted] = useState(false);
  const unreadStatus = useResourceUnread();
  const [markingRead, setMarkingRead] = useState(false);
  const [readError, setReadError] = useState("");
  const unread = unreadStatus
    ? unreadStatus.unreadIds.includes(id)
    : artifact?.unread === true;
  useEffect(() => {
    let current = true;
    setArtifact(null);
    setDeleted(false);
    setError("");
    api(`/artifacts/${encodeURIComponent(id)}`)
      .then((value) => {
        if (current) setArtifact(value as Artifact);
      })
      .catch((reason) => {
        if (current) setError(reason.message);
      });
    return () => {
      current = false;
    };
  }, [id, attempt]);
  const endpoint = `/api/artifacts/${encodeURIComponent(id)}`;
  return (
    <main
      className={`artifact-viewer ${artifact?.kind === "html" ? "artifact-interactive" : ""}`}
    >
      <header className="artifact-toolbar">
        <a href="/?view=resources" aria-label="Back to Resources">
          <ArrowLeft size={20} /> Resources
        </a>
        <span>Private resource{unread ? " · unread" : ""}</span>
        {artifact && unread && (
          <button
            className="secondary"
            disabled={markingRead}
            onClick={() => {
              setMarkingRead(true);
              setReadError("");
              void markResourcesRead([id])
                .catch((reason) => setReadError(reason.message))
                .finally(() => setMarkingRead(false));
            }}
          >
            {markingRead ? "Marking read…" : "Mark as read"}
          </button>
        )}
        {artifact && (
          <a href={`${endpoint}/download`}>
            <Download size={18} /> Download
          </a>
        )}
        {artifact && (
          <ResourceDelete
            item={artifact}
            onDeleted={() => {
              setDeleted(true);
              setArtifact(null);
            }}
          />
        )}
      </header>
      {readError && <p role="alert">{readError}</p>}
      {deleted ? (
        <section role="status">
          <h1>Resource deleted</h1>
          <p>
            The file and its associations were removed.{" "}
            <a href="/?view=resources">Back to Resources</a>
          </p>
        </section>
      ) : error ? (
        <section role="alert">
          <h1>Unable to open this artifact</h1>
          <p>{error}</p>
          <button onClick={() => setAttempt((n) => n + 1)}>Try again</button>
        </section>
      ) : !artifact ? (
        <p role="status">Opening document…</p>
      ) : (
        <>
          <div className="artifact-heading">
            <h1>{artifact.title}</h1>
            <p>
              {artifact.category === "attachment" ? "Attachment · " : ""}
              {resourceLabels[artifact.kind] || "Published resource"}
              {artifact.kind === "html" ? " · isolated preview" : ""} ·{" "}
              {new Date(artifact.publishedAt * 1000).toLocaleDateString()}
            </p>
          </div>
          <ResourceAssociations key={artifact.id} resourceId={artifact.id} />
          {!!artifact.sources?.length && (
            <details>
              <summary>Conversation references</summary>
              <ul>
                {artifact.sources.map((source, index) => (
                  <li key={index}>
                    {source.surface === "coding"
                      ? "Coding"
                      : "Companion / Today"}
                    {source.threadId && (
                      <>
                        {" "}
                        · Conversation <code>{source.threadId}</code>
                      </>
                    )}
                    {source.turnId && (
                      <>
                        {" "}
                        · Turn <code>{source.turnId}</code>
                      </>
                    )}
                  </li>
                ))}
              </ul>
              {artifact.sourcesTruncated && (
                <p>More references exist; the displayed list is bounded.</p>
              )}
            </details>
          )}
          {artifact.kind === "html" ? (
            <iframe
              title={artifact.title}
              src={`${endpoint}/preview`}
              sandbox="allow-scripts"
              referrerPolicy="no-referrer"
            />
          ) : imageKinds.has(artifact.kind) ? (
            <figure className="artifact-image">
              <img src={`${endpoint}/preview`} alt={artifact.title} />
              <figcaption>{artifact.filename || artifact.title}</figcaption>
            </figure>
          ) : artifact.kind === "text" ? (
            <pre className="artifact-text">{artifact.content || ""}</pre>
          ) : artifact.kind !== "markdown" ? (
            <section className="artifact-download-note">
              <h2>Open on your device</h2>
              <p>
                This format is available to download. Open it in your preferred
                document app.
              </p>
              <a className="resource-download" href={`${endpoint}/download`}>
                <Download size={18} /> Download{" "}
                {resourceLabels[artifact.kind] || "file"}
              </a>
            </section>
          ) : (
            <article className="markdown artifact-report">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                urlTransform={(url) => defaultUrlTransform(url)}
                components={{
                  a: ({ node: _node, ...props }) => (
                    <ArtifactLink
                      {...props}
                      target="_blank"
                      rel="noopener noreferrer"
                    />
                  ),
                  // Remote images would send a private report reader's address to third parties.
                  img: ({ alt }) => (
                    <span className="unpublished-link">
                      {alt || "Image"} (not embedded in this report)
                    </span>
                  ),
                }}
              >
                {artifact.content || ""}
              </ReactMarkdown>
            </article>
          )}
          <details className="artifact-metadata">
            <summary>Resource details</summary>
            <p>
              This is a saved snapshot. Updated versions receive a new link.
            </p>
            <p>
              {artifact.filename && <span>{artifact.filename} · </span>}
              {resourceSize(artifact.bytes)}
            </p>
            {artifact.source?.surface && (
              <p>Published from {artifact.source.surface}</p>
            )}
            {artifact.source?.threadId && (
              <p>Source conversation: {artifact.source.threadId}</p>
            )}
            <small>SHA-256: {artifact.sha256}</small>
            <p>
              <ExternalLink size={14} /> The link requires your Tailscale
              connection and Leam sign-in.
            </p>
          </details>
        </>
      )}
    </main>
  );
}
