import "./navigation-colours.css";
import { InboxBadge, useInboxStatus } from "./inbox-status";
import { ResourcesBadge, useResourceUnread } from "./resource-unread";
import { useRef } from "react";
import {
  MessageCircle,
  Sun,
  Target,
  ChartNoAxesCombined,
  CalendarDays,
  Repeat,
  Code2,
  Settings,
  Bell,
  ListTodo,
  Ellipsis,
  ClipboardCheck,
  FolderOpen,
  X,
} from "lucide-react";
import { UpdatesBadge } from "./updates";
import { ApprovalsBadge } from "./approvals-status";
const destinations = [
  { id: "companion", label: "Companion", Icon: MessageCircle },
  { id: "today", label: "Today", Icon: Sun },
  { id: "goals", label: "Goals", Icon: Target },
  { id: "calendar", label: "Calendar", Icon: CalendarDays },
  { id: "routines", label: "Routines", Icon: Repeat },
  { id: "coding", label: "Coding", Icon: Code2 },
  { id: "resources", label: "Resources", Icon: FolderOpen },
  { id: "usage", label: "Usage", Icon: ChartNoAxesCombined },
  { id: "settings", label: "Settings", Icon: Settings },
  { id: "approvals", label: "Approvals", Icon: ClipboardCheck },
  { id: "updates", label: "Updates", Icon: Bell },
  { id: "backlog", label: "Backlog", Icon: ListTodo },
];
const primary = new Set(["companion", "today", "goals", "coding"]);
export function Navigation({
  tab,
  onSelect,
}: {
  tab: string;
  onSelect: (id: string) => void;
}) {
  const sheet = useRef<HTMLDialogElement>(null);
  const inboxUnread = useInboxStatus()?.unreadCount || 0;
  const resourceUnread = useResourceUnread()?.unreadCount || 0;
  function choose(id: string) {
    sheet.current?.close();
    onSelect(id);
  }
  function items(mobile: boolean) {
    return destinations
      .filter((d) => !mobile || primary.has(d.id))
      .map(({ id, label, Icon }) => (
        <button
          key={id}
          data-destination={id}
          aria-current={tab === id ? "page" : undefined}
          className={tab === id ? "selected" : ""}
          onClick={() => choose(id)}
        >
          <Icon size={20} />
          <span>
            {label}
            {id === "today" && <InboxBadge count={inboxUnread} />}
            {id === "resources" && <ResourcesBadge count={resourceUnread} />}
            {id === "updates" && <UpdatesBadge />}
            {id === "approvals" && <ApprovalsBadge />}
          </span>
        </button>
      ));
  }
  return (
    <>
      <nav className="desktop-navigation" aria-label="Main navigation">
        {items(false)}
      </nav>
      <nav className="mobile-navigation" aria-label="Main navigation">
        {items(true)}
        <button
          data-destination="more"
          className={!primary.has(tab) ? "selected" : ""}
          aria-haspopup="dialog"
          onClick={() => sheet.current?.showModal()}
        >
          <Ellipsis size={20} />
          <span>
            More <UpdatesBadge /> <ApprovalsBadge /> <ResourcesBadge count={resourceUnread} />
          </span>
        </button>
      </nav>
      <dialog
        ref={sheet}
        className="navigation-sheet"
        aria-labelledby="navigation-title"
        onClick={(e) => {
          if (e.target === e.currentTarget) {
            const r = e.currentTarget.getBoundingClientRect();
            if (
              e.clientX < r.left ||
              e.clientX > r.right ||
              e.clientY < r.top ||
              e.clientY > r.bottom
            )
              e.currentTarget.close();
          }
        }}
      >
        <header>
          <h2 id="navigation-title">More from Leam</h2>
          <button
            className="icon-button"
            aria-label="Close navigation"
            onClick={() => sheet.current?.close()}
          >
            <X size={20} />
          </button>
        </header>
        <div className="navigation-grid">
          {destinations
            .filter((d) => !primary.has(d.id))
            .map(({ id, label, Icon }) => (
              <button
                key={id}
                data-destination={id}
                className={tab === id ? "selected" : ""}
                onClick={() => choose(id)}
              >
                <Icon size={22} />
                <span>
                  {label}
                  {id === "today" && <InboxBadge count={inboxUnread} />}
            {id === "resources" && <ResourcesBadge count={resourceUnread} />}
            {id === "updates" && <UpdatesBadge />}
                  {id === "approvals" && <ApprovalsBadge />}
                </span>
              </button>
            ))}
        </div>
      </dialog>
    </>
  );
}
