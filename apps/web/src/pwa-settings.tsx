import { useState, useSyncExternalStore } from "react";
import { Download } from "lucide-react";
type InstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: string }>;
};
const displayMode = window.matchMedia("(display-mode: standalone)");
let state = {
  prompt: null as InstallPrompt | null,
  installed:
    displayMode.matches ||
    Boolean((navigator as Navigator & { standalone?: boolean }).standalone),
};
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state = { ...state, prompt: event as InstallPrompt };
  notify();
});
window.addEventListener("appinstalled", () => {
  state = { prompt: null, installed: true };
  notify();
});
displayMode.addEventListener("change", (event) => {
  if (event.matches) {
    state = { ...state, installed: true };
    notify();
  }
});
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
export function PwaSettings() {
  const current = useSyncExternalStore(subscribe, () => state);
  const [instructions, setInstructions] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const appleMobile =
    /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  return (
    <section className="card" aria-label="App installation and offline">
      <img
        src="/leam-icon-192.png"
        width="48"
        height="48"
        alt="Leam robot icon"
      />
      <h3>Install Leam</h3>
      {current.installed ? (
        <p role="status">
          Leam is installed. Open it from your home screen or app launcher.
        </p>
      ) : (
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={async () => {
            const available = state.prompt;
            if (!available) {
              setInstructions(true);
              return;
            }
            setBusy(true);
            setNotice("");
            state = { ...state, prompt: null };
            notify();
            try {
              await available.prompt();
              const choice = await available.userChoice;
              setNotice(
                choice.outcome === "accepted"
                  ? "Installation requested. Your browser will confirm when it is ready."
                  : "Installation dismissed. You can try again from your browser menu.",
              );
            } catch {
              setInstructions(true);
              setNotice("The browser could not open its install prompt.");
            } finally {
              setBusy(false);
            }
          }}
        >
          <Download size={18} aria-hidden="true" />
          {busy ? "Opening installer…" : "Install Leam"}
        </button>
      )}
      {notice && !current.installed && <p role="status">{notice}</p>}
      {instructions && !current.installed && (
        <div role="region" aria-label="Installation instructions">
          {appleMobile ? (
            <p>
              Open Leam in Safari. Tap Share, then Add to Home Screen and Add.
              On newer versions, enable Open as Web App if shown.
            </p>
          ) : (
            <p>
              Open your browser menu and choose Install Leam, Install app, or
              Add to Home Screen. On desktop, the install icon may appear beside
              the address bar. If your browser does not offer installation, open
              Leam in a browser that supports installing web apps.
            </p>
          )}
          <button
            type="button"
            className="secondary"
            onClick={() => setInstructions(false)}
          >
            Hide instructions
          </button>
        </div>
      )}
      <p>
        The app interface can open offline after your first visit. Signing in,
        refreshing records and sending messages need a connection.
      </p>
      <p>
        When a new version is ready, Leam offers Reload Leam. It won't reload
        automatically while you're working.
      </p>
    </section>
  );
}
