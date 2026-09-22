import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";
import { MailReviewControls } from "./mail-review-controls";

async function triageRequest(start: boolean, signal: AbortSignal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 15000);
  try {
    return await api(
      "/email/triage",
      start ? "POST" : "GET",
      start ? {} : undefined,
      controller.signal,
    );
  } catch (error) {
    if (timedOut)
      throw new Error(
        "Mail triage request timed out. Refresh status before trying again; classification may still be running.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}

/** Classification is advisory source metadata, never permission to mutate Gmail. */
export function EmailTriage({
  classification,
  pendingFallback,
  changed,
}: {
  classification?: Data;
  pendingFallback: number;
  changed: () => void | Promise<void>;
}) {
  const [snapshot, setSnapshot] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [reviewNotice, setReviewNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const active = useRef(true),
    request = useRef<AbortController | null>(null);
  const version = useRef(0);
  const changedRef = useRef(changed);
  changedRef.current = changed;
  const initialKey = JSON.stringify(classification || null);
  useEffect(() => {
    version.current++;
    request.current?.abort();
    request.current = null;
    setBusy(false);
    setSnapshot(null);
    if (Object.values(openGroups).some(Boolean)) void read();
  }, [initialKey]);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      request.current?.abort();
    };
  }, []);
  const status = snapshot?.classification || classification;
  const counts = status?.counts || {
    action: 0,
    ignore: 0,
    review: 0,
    pending: pendingFallback,
  };
  const running = status?.state === "running";
  async function read(start = false) {
    if (request.current) return;
    const controller = new AbortController();
    const n = ++version.current;
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await triageRequest(start, controller.signal);
      if (!active.current || n !== version.current) return;
      setSnapshot(result);
      if (result.classification?.state !== "running") void changedRef.current();
    } catch (e) {
      if (active.current && n === version.current && !controller.signal.aborted)
        setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (request.current === controller) {
        request.current = null;
        if (active.current) setBusy(false);
      }
    }
  }
  useEffect(() => {
    if (!running) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const wait = () =>
      new Promise<void>((resolve) => {
        const finish = () => {
          clearTimeout(timer);
          controller.signal.removeEventListener("abort", finish);
          resolve();
        };
        timer = setTimeout(finish, 2000);
        controller.signal.addEventListener("abort", finish, { once: true });
      });
    void (async () => {
      try {
        for (let i = 0; i < 60 && !controller.signal.aborted; i++) {
          await wait();
          if (controller.signal.aborted) return;
          const n = ++version.current;
          const result = await triageRequest(false, controller.signal);
          if (controller.signal.aborted || !active.current) return;
          if (n !== version.current) continue;
          setSnapshot(result);
          if (result.classification?.state !== "running") {
            void changedRef.current();
            return;
          }
        }
        if (!controller.signal.aborted && active.current)
          setError(
            "Triage is still running. Refresh its status in a moment; no second request was started.",
          );
      } catch (e) {
        if (!controller.signal.aborted && active.current)
          setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [running]);
  const others = (snapshot?.items || []).filter(
    (item: Data) => item.actionability?.state !== "action",
  );
  return (
    <div className="agenda-mail-triage">
      <p className="agenda-hint" role="status">
        {!status || status.state === "unclassified"
          ? "Not triaged yet. "
          : running
            ? "Finding actionable mail… "
            : status.state === "error"
              ? "Mail triage needs attention. "
              : "Saved mail triaged. "}
        {counts.action} actionable · {counts.ignore} excluded · {counts.review}{" "}
        need review · {counts.pending} not yet classified.
        {status?.error && <span> {status.error}</span>}
      </p>
      <div className="agenda-triage">
        <button
          className="secondary"
          disabled={busy || running}
          onClick={() => void read(true)}
        >
          Triage saved mail
        </button>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void read()}
        >
          Refresh triage status
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {reviewNotice && <p role="status">{reviewNotice}</p>}
      {[
        {
          id: "review",
          title: "Needs review",
          count: counts.review,
          hint: "These messages need your judgment before they can enter Today.",
          matches: (item: Data) =>
            item.actionability?.state === "review" &&
            !item.actionability?.pending,
        },
        {
          id: "ignore",
          title: "Excluded",
          count: counts.ignore,
          hint: "Subscriptions, outreach and informational mail are kept out of Today.",
          matches: (item: Data) => item.actionability?.state === "ignore",
        },
        {
          id: "pending",
          title: "Not yet classified",
          count: counts.pending,
          hint: "These messages are waiting for triage. They are not treated as obligations yet.",
          matches: (item: Data) =>
            !item.actionability || item.actionability.pending,
        },
      ].map((group) => {
        const items = others.filter(group.matches);
        return (
          <details
            key={group.id}
            open={Boolean(openGroups[group.id])}
            onToggle={(e) => {
              const open = e.currentTarget.open;
              setOpenGroups((current) => ({ ...current, [group.id]: open }));
              if (open && !snapshot) void read();
            }}
          >
            <summary>
              {group.title} · {group.count}
            </summary>
            <p className="agenda-hint">
              {group.hint} Open Gmail to inspect the original; nothing is sent
              or changed there.
            </p>
            {busy && <p>Loading saved mail…</p>}
            {snapshot && !items.length && <p>No messages in this group.</p>}
            {items.map((item: Data) => (
              <article
                className="card agenda-email"
                key={`${item.accountId}:${item.id}`}
              >
                <small>{item.from}</small>
                <h3>{item.subject || "(No subject)"}</h3>
                <p>
                  {item.actionability?.pending
                    ? "Not triaged yet"
                    : item.actionability?.reason || "Needs review"}
                </p>
                {item.snippet && (
                  <details>
                    <summary>Message excerpt</summary>
                    <p>{item.snippet}</p>
                  </details>
                )}
                <MailReviewControls
                  item={item}
                  saved={(message) => {
                    setReviewNotice(message);
                    void read();
                    void changedRef.current();
                  }}
                />
                {item.url && (
                  <a href={item.url} target="_blank" rel="noreferrer">
                    Review in Gmail
                  </a>
                )}
              </article>
            ))}
            {snapshot?.truncated && (
              <p className="agenda-hint">
                More messages remain outside this saved view.
              </p>
            )}
          </details>
        );
      })}
    </div>
  );
}
