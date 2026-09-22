import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

// Outside Settings, existing panels keep their normal activity semantics.
export const SettingsActivity = createContext(true);
export const useSettingsActive = () => useContext(SettingsActivity);

export function SettingsActivityBoundary({
  active,
  children,
}: {
  active: boolean;
  children: ReactNode;
}) {
  const [visible, setVisible] = useState(!document.hidden);
  useEffect(() => {
    const changed = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, []);
  return (
    <SettingsActivity.Provider value={active && visible}>
      {children}
    </SettingsActivity.Provider>
  );
}

/** First-open loading; later collapse preserves drafts and accepted operations. */
export function LazySettingsSection({
  title,
  children,
  initiallyOpen = false,
}: {
  title: string;
  children: ReactNode;
  initiallyOpen?: boolean;
}) {
  const active = useSettingsActive();
  const [open, setOpen] = useState(initiallyOpen);
  const [visited, setVisited] = useState(initiallyOpen);
  return (
    <details
      className="settings-section"
      open={open}
      onToggle={(event) => {
        // Nested native toggle events must never close their enclosing section.
        if (event.target !== event.currentTarget) return;
        const next = event.currentTarget.open;
        setOpen(next);
        if (next) setVisited(true);
      }}
    >
      <summary>{title}</summary>
      {visited && (
        <SettingsActivity.Provider value={active && open}>
          {children}
        </SettingsActivity.Provider>
      )}
    </details>
  );
}
