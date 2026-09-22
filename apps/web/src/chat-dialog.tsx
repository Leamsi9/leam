import { useEffect, useRef, type ReactNode } from "react";
import { Menu, X } from "lucide-react";

/** Mounted content preserves edits. Opening and closing never submits a form. */
export function ChatDialog({
  label,
  children,
  icon,
  badge,
  closeSignal = 0,
}: {
  label: string;
  children: ReactNode;
  icon?: ReactNode;
  badge?: string;
  closeSignal?: number;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (closeSignal) dialog.current?.close();
  }, [closeSignal]);
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="icon-button chat-dialog-trigger"
        aria-label={label}
        title={label}
        aria-haspopup="dialog"
        onClick={() => dialog.current?.showModal()}
      >
        {icon || <Menu size={20} aria-hidden="true" />}
        {badge && <span className="chat-control-badge">{badge}</span>}
      </button>
      <dialog
        ref={dialog}
        className="chat-options-dialog"
        aria-label={label}
        onClose={() => trigger.current?.focus()}
      >
        <header>
          <h2>{label}</h2>
          <button
            type="button"
            className="icon-button"
            aria-label={`Close ${label}`}
            onClick={() => dialog.current?.close()}
          >
            <X size={20} />
          </button>
        </header>
        <div className="chat-options-body">{children}</div>
      </dialog>
    </>
  );
}
