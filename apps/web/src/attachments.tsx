import { useEffect, useRef, useState } from "react";
import { Paperclip, X } from "lucide-react";
import { api } from "./api";
import {
  rememberSession,
  sessionGeneration,
  sessionValue,
  clearPrivateSession,
} from "./session-cache";

export type Attachment = {
  id: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
  state: string;
};
const accept =
  ".png,.jpg,.jpeg,.webp,.pdf,.txt,.md,.csv,.json,image/png,image/jpeg,image/webp,application/pdf,text/plain,text/markdown,text/csv,application/json";
const guess: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  pdf: "application/pdf",
  txt: "text/plain",
  md: "text/markdown",
  csv: "text/csv",
  json: "application/json",
};
export function useAttachmentDraft(key: string) {
  const storage = "attachments:" + key;
  const [items, setItems] = useState<Attachment[]>(() =>
    sessionValue(storage, []),
  );
  const current = useRef(storage);
  useEffect(() => {
    current.current = storage;
    setItems(sessionValue(storage, []));
  }, [storage]);
  const change = (rows: Attachment[]) => {
    rememberSession(storage, rows);
    if (current.current === storage) setItems(rows);
  };
  const clear = (ids: string[]) =>
    change(
      sessionValue<Attachment[]>(storage, []).filter(
        (a) => !ids.includes(a.id),
      ),
    );
  return { items, change, clear, ids: items.map((a) => a.id) };
}
export function AttachmentComposer({
  items,
  onChange,
  disabled = false,
  onBusyChange,
}: {
  items: Attachment[];
  onChange: (rows: Attachment[]) => void;
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const alive = useRef(true),
    latest = useRef(items);
  const busyRef = useRef(false),
    controller = useRef<AbortController | null>(null);
  latest.current = items;
  useEffect(() => {
    alive.current = true;
    onBusyChange?.(false);
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);
  async function add(files: File[]) {
    if (busyRef.current || disabled) return;
    busyRef.current = true;
    const request = new AbortController();
    controller.current = request;
    const timer = setTimeout(() => request.abort(), 30000);
    setBusy(true);
    onBusyChange?.(true);
    setError("");
    const epoch = sessionGeneration();
    let next = [...latest.current];
    try {
      for (const file of files) {
        if (
          next.length >= 10 ||
          next.reduce((sum, a) => sum + a.sizeBytes, 0) + file.size >
            10 * 1024 * 1024
        )
          throw new Error(
            "Choose up to 10 files with a combined size of 10 MiB.",
          );
        const mime =
          guess[file.name.split(".").at(-1)?.toLowerCase() || ""] || file.type;
        const response = await fetch(
          "/api/attachments?filename=" + encodeURIComponent(file.name),
          {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": mime || "application/octet-stream" },
            body: file,
            signal: request.signal,
          },
        );
        const row = await response.json();
        if (
          !alive.current ||
          request.signal.aborted ||
          epoch !== sessionGeneration()
        )
          throw new Error("Session changed. Sign in again.");
        if (response.status === 401) {
          clearPrivateSession();
          window.dispatchEvent(new Event("leam:auth-lost"));
          throw new Error("Sign in to Leam");
        }
        if (!response.ok)
          throw new Error(
            typeof row.detail === "string"
              ? row.detail
              : "Attachment upload failed",
          );
        next = [...next, row];
        onChange(next);
      }
    } catch (e) {
      if (alive.current)
        setError(e instanceof Error ? e.message : "Attachment upload failed");
    } finally {
      clearTimeout(timer);
      busyRef.current = false;
      if (alive.current) {
        setBusy(false);
        onBusyChange?.(false);
      }
    }
  }
  return (
    <div
      className="attachment-composer"
      style={{ display: "block", gridColumn: "1 / -1", minWidth: 0 }}
    >
      <label
        className="secondary"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          minHeight: 44,
          padding: "8px 12px",
          cursor: disabled || busy ? "default" : "pointer",
        }}
      >
        <Paperclip size={18} aria-hidden="true" />
        {busy ? "Uploading…" : "Attach files"}
        <input
          aria-label="Attach files"
          type="file"
          accept={accept}
          multiple
          disabled={disabled || busy}
          style={{ position: "absolute", width: 1, height: 1, opacity: 0 }}
          onChange={(e) => {
            void add(Array.from(e.target.files || []));
            e.target.value = "";
          }}
        />
      </label>
      {items.length > 0 && (
        <ul
          aria-label="Attached files"
          style={{ padding: 0, listStyle: "none" }}
        >
          {items.map((a) => (
            <li
              key={a.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                margin: "6px 0",
                overflowWrap: "anywhere",
              }}
            >
              {a.mimeType.startsWith("image/") && (
                <img
                  src={"/api/attachments/" + a.id}
                  alt={"Preview of " + a.filename}
                  style={{ width: 48, height: 48, objectFit: "contain" }}
                />
              )}
              <a href={"/api/attachments/" + a.id} download={a.filename}>
                {a.filename}
              </a>
              <small>{Math.ceil(a.sizeBytes / 1024)} KiB · Ready to send</small>
              <button
                type="button"
                className="secondary"
                aria-label={"Remove " + a.filename}
                disabled={disabled || busy}
                onClick={async () => {
                  if (busyRef.current || disabled) return;
                  busyRef.current = true;
                  setBusy(true);
                  onBusyChange?.(true);
                  const epoch = sessionGeneration(),
                    request = new AbortController();
                  controller.current = request;
                  const timer = setTimeout(() => request.abort(), 15000);
                  try {
                    await api(
                      "/attachments/" + a.id,
                      "DELETE",
                      undefined,
                      request.signal,
                    );
                    if (
                      alive.current &&
                      !request.signal.aborted &&
                      epoch === sessionGeneration()
                    )
                      onChange(latest.current.filter((v) => v.id !== a.id));
                  } catch (e) {
                    if (alive.current)
                      setError(
                        e instanceof Error
                          ? e.message
                          : "Could not remove attachment",
                      );
                  } finally {
                    clearTimeout(timer);
                    busyRef.current = false;
                    if (alive.current) {
                      setBusy(false);
                      onBusyChange?.(false);
                    }
                  }
                }}
              >
                <X size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert">{error}</p>}
      {items.length > 0 && (
        <small>
          Files are uploaded privately. Send your message to give this
          conversation access. PDFs use text extraction; scanned pages need
          images.
        </small>
      )}
    </div>
  );
}

export function AttachmentList({ items }: { items: Attachment[] }) {
  return items?.length ? (
    <div
      aria-label="Message attachments"
      style={{ display: "flex", gap: 8, flexWrap: "wrap" }}
    >
      {items.map((a) => (
        <a key={a.id} href={"/api/attachments/" + a.id} download={a.filename}>
          {a.mimeType.startsWith("image/") && (
            <img
              src={"/api/attachments/" + a.id}
              alt={a.filename}
              style={{ maxWidth: 120, maxHeight: 120, display: "block" }}
            />
          )}
          {a.filename}
        </a>
      ))}
    </div>
  ) : null;
}
