import { useSyncExternalStore } from "react";
import { Download } from "lucide-react";
type InstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: string }>;
};
let prompt: InstallPrompt | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  prompt = event as InstallPrompt;
  notify();
});
window.addEventListener("appinstalled", () => {
  prompt = null;
  notify();
});
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
export function PwaSettings() {
  const available = useSyncExternalStore(subscribe, () => prompt);
  const standalone = window.matchMedia("(display-mode: standalone)").matches;
  return (
    <section className="card" aria-label="App installation and offline">
      <h3>Keep Leam close</h3>
      {standalone ? (
        <p>You're using Leam as an installed app.</p>
      ) : available ? (
        <button
          type="button"
          className="secondary"
          onClick={async () => {
            const current = prompt;
            if (!current) return;
            prompt = null;
            notify();
            try {
              await current.prompt();
              await current.userChoice;
            } catch {
              /* Browser keeps control of installation. */
            }
          }}
        >
          <Download size={18} aria-hidden="true" />
          Install Leam
        </button>
      ) : (
        <p>
          To add Leam to your home screen, use your browser's Install app or Add
          to Home Screen option, if available.
        </p>
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
