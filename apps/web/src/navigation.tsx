import { useRef } from "react";
import {
  MessageCircle,
  Sun,
  Target,
  Layers,
  CalendarDays,
  Repeat,
  Code2,
  Settings,
  Bell,
  ListTodo,
  Ellipsis,
  X,
} from "lucide-react";
import { UpdatesBadge } from "./updates";
const destinations = [
  { id: "companion", label: "Companion", Icon: MessageCircle },
  { id: "today", label: "Today", Icon: Sun },
  { id: "goals", label: "Goals", Icon: Target },
  { id: "calendar", label: "Calendar", Icon: CalendarDays },
  { id: "routines", label: "Routines", Icon: Repeat },
  { id: "coding", label: "Coding", Icon: Code2 },
  { id: "overview", label: "Across Leam", Icon: Layers },
  { id: "settings", label: "Settings", Icon: Settings },
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
          aria-current={tab === id ? "page" : undefined}
          className={tab === id ? "selected" : ""}
          onClick={() => choose(id)}
        >
          <Icon size={20} />
          <span>
            {label}
            {id === "updates" && <UpdatesBadge />}
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
          className={!primary.has(tab) ? "selected" : ""}
          aria-haspopup="dialog"
          onClick={() => sheet.current?.showModal()}
        >
          <Ellipsis size={20} />
          <span>
            More <UpdatesBadge />
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
                className={tab === id ? "selected" : ""}
                onClick={() => choose(id)}
              >
                <Icon size={22} />
                <span>
                  {label}
                  {id === "updates" && <UpdatesBadge />}
                </span>
              </button>
            ))}
        </div>
      </dialog>
    </>
  );
}
