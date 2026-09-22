import { useEffect, useRef, useState, type HTMLAttributes } from "react";
import { ArrowDown, ArrowUp, GripVertical } from "lucide-react";

export const todayPageIds = [
  "plan",
  "schedule",
  "inbox",
  "chat",
  "boards",
  "wellbeing",
] as const;
export type TodayPage = (typeof todayPageIds)[number];
export const todayPageLabels: Record<TodayPage, string> = {
  plan: "Overview",
  schedule: "Schedule",
  inbox: "Inbox",
  chat: "Chat",
  boards: "Boards",
  wellbeing: "Wellbeing",
};
const storageKey = "leam-today-page-order-v1";
function readOrder(): TodayPage[] {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw || raw.length > 256) return [...todayPageIds];
    const value = JSON.parse(raw);
    if (
      value.version === 1 &&
      Array.isArray(value.order) &&
      value.order.length <= todayPageIds.length &&
      new Set(value.order).size === value.order.length &&
      value.order.every((id: unknown) => todayPageIds.includes(id as TodayPage))
    )
      return [...value.order, ...todayPageIds.filter(id => !value.order.includes(id))];
  } catch {
    /* Optional enum-only browser preference. */
  }
  return [...todayPageIds];
}
export function useTodayTabOrder() {
  const [order, setOrder] = useState<TodayPage[]>(readOrder);
  const [notice, setNotice] = useState("");
  function change(next: TodayPage[]) {
    setOrder(next);
    try {
      localStorage.setItem(
        storageKey,
        JSON.stringify({ version: 1, order: next }),
      );
      setNotice("Today page order saved on this browser.");
    } catch {
      setNotice(
        "Order changed for this visit. Browser storage is unavailable.",
      );
    }
  }
  return { order, change, notice };
}
export function TodayTabOrder({
  order,
  change,
  notice,
}: ReturnType<typeof useTodayTabOrder>) {
  const drag = useRef<{ id: TodayPage; pointer: number } | null>(null);
  const [target, setTarget] = useState<TodayPage | null>(null);
  function move(id: TodayPage, index: number) {
    const next = order.filter((value) => value !== id);
    next.splice(Math.max(0, Math.min(next.length, index)), 0, id);
    change(next);
  }
  const hit = (x: number, y: number) => {
    const value = document
      .elementFromPoint(x, y)
      ?.closest<HTMLElement>("[data-today-order-id]")?.dataset.todayOrderId;
    return todayPageIds.includes(value as TodayPage)
      ? (value as TodayPage)
      : null;
  };
  function cancel() {
    drag.current = null;
    setTarget(null);
  }
  return (
    <details className="today-order-settings">
      <summary>Arrange Today pages</summary>
      <p>
        Drag a handle or use the move buttons. This order is saved on this
        browser; it does not change Goals or your selected day.
      </p>
      <ol aria-label="Today page order">
        {order.map((id, index) => (
          <li
            key={id}
            data-today-order-id={id}
            data-drop-target={target === id || undefined}
          >
            <button
              type="button"
              className="icon-button today-order-handle"
              aria-label={`Drag ${todayPageLabels[id]} to reorder`}
              onPointerDown={(event) => {
                if (!event.isPrimary || event.button !== 0) return;
                drag.current = { id, pointer: event.pointerId };
                event.currentTarget.setPointerCapture(event.pointerId);
              }}
              onPointerMove={(event) => {
                if (drag.current?.pointer === event.pointerId)
                  setTarget(hit(event.clientX, event.clientY));
              }}
              onPointerUp={(event) => {
                const current = drag.current;
                if (current?.pointer !== event.pointerId) return;
                const destination = hit(event.clientX, event.clientY);
                cancel();
                if (destination && destination !== current.id)
                  move(current.id, order.indexOf(destination));
              }}
              onPointerCancel={cancel}
              onLostPointerCapture={cancel}
              onKeyDown={(event) => {
                if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                  event.preventDefault();
                  move(id, index + (event.key === "ArrowUp" ? -1 : 1));
                }
              }}
            >
              <GripVertical size={18} aria-hidden="true" />
            </button>
            <span className="today-page-label" data-page={id}>
              {todayPageLabels[id]}
            </span>
            <button
              type="button"
              className="icon-button"
              disabled={index === 0}
              aria-label={`Move ${todayPageLabels[id]} earlier`}
              onClick={() => move(id, index - 1)}
            >
              <ArrowUp size={18} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="icon-button"
              disabled={index === order.length - 1}
              aria-label={`Move ${todayPageLabels[id]} later`}
              onClick={() => move(id, index + 1)}
            >
              <ArrowDown size={18} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ol>
      <button
        type="button"
        className="secondary"
        onClick={() => change([...todayPageIds])}
      >
        Reset page order
      </button>
      {notice && <p role="status">{notice}</p>}
    </details>
  );
}

// The visible tabs and the options panel share the same enum-only preference.
export function useTodayTabDrag({
  order,
  change,
}: ReturnType<typeof useTodayTabOrder>) {
  const drag = useRef<{
    id: TodayPage;
    pointer: number;
    x: number;
    y: number;
    startedAt: number;
    active: boolean;
  } | null>(null);
  const suppressClick = useRef(false);
  const pendingTouchMove = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (pendingTouchMove.current !== null)
        cancelAnimationFrame(pendingTouchMove.current);
    },
    [],
  );
  const [source, setSource] = useState<TodayPage | null>(null);
  const [target, setTarget] = useState<TodayPage | null>(null);
  function cancel() {
    drag.current = null;
    setSource(null);
    setTarget(null);
  }
  function hit(x: number, y: number): TodayPage | null {
    const id = document
      .elementFromPoint(x, y)
      ?.closest<HTMLElement>(".today-navigation a[data-page]")?.dataset.page;
    return todayPageIds.includes(id as TodayPage) ? (id as TodayPage) : null;
  }
  function move(id: TodayPage, destination: TodayPage) {
    if (id === destination) return;
    const next = order.filter((value) => value !== id);
    next.splice(order.indexOf(destination), 0, id);
    change(next);
  }
  function props(id: TodayPage): HTMLAttributes<HTMLAnchorElement> {
    return {
      title: "Drag to reorder; Alt + Left/Right also moves this tab",
      "aria-keyshortcuts": "Alt+ArrowLeft Alt+ArrowRight",
      onDragStart: (event) => event.preventDefault(),
      onPointerDown: (event) => {
        if (!event.isPrimary || event.button !== 0) return;
        suppressClick.current = false;
        drag.current = {
          id,
          pointer: event.pointerId,
          x: event.clientX,
          y: event.clientY,
          startedAt: event.timeStamp,
          active: false,
        };
        event.currentTarget.setPointerCapture(event.pointerId);
      },
      onPointerMove: (event) => {
        const current = drag.current;
        if (!current || current.pointer !== event.pointerId) return;
        if (
          !current.active &&
          Math.hypot(event.clientX - current.x, event.clientY - current.y) < 8
        )
          return;
        current.active = true;
        suppressClick.current = true;
        setSource(current.id);
        setTarget(hit(event.clientX, event.clientY));
      },
      onPointerUp: (event) => {
        const current = drag.current;
        if (!current || current.pointer !== event.pointerId) return;
        const destination = hit(event.clientX, event.clientY);
        if (event.currentTarget.hasPointerCapture(event.pointerId))
          event.currentTarget.releasePointerCapture(event.pointerId);
        cancel();
        // Some touch browsers omit the compatibility click after a drag.
        // Activate an ordinary short tap through the same link handler. The
        // route handler deduplicates its hash if a native click also arrives.
        if (
          event.pointerType === "touch" &&
          !current.active &&
          destination === current.id &&
          event.timeStamp - current.startedAt < 500
        )
          event.currentTarget.click();
        if (current.active && destination) {
          if (event.pointerType === "touch") {
            // pointerup precedes touchend. Keep its target in place until the
            // browser finishes the native gesture before moving the DOM node.
            pendingTouchMove.current = requestAnimationFrame(() => {
              pendingTouchMove.current = null;
              move(current.id, destination);
            });
          } else move(current.id, destination);
        }
      },
      onPointerCancel: cancel,
      onLostPointerCapture: cancel,
      onClickCapture: (event) => {
        if (suppressClick.current) {
          event.preventDefault();
          event.stopPropagation();
          suppressClick.current = false;
        }
      },
      onKeyDown: (event) => {
        if (event.key === "Escape") {
          cancel();
          return;
        }
        if (!event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key))
          return;
        event.preventDefault();
        const destination =
          order[order.indexOf(id) + (event.key === "ArrowLeft" ? -1 : 1)];
        if (destination) move(id, destination);
      },
    };
  }
  return { props, source, target };
}
