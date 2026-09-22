import { useEffect, useState } from "react";
import { Palette } from "lucide-react";
type Preference = "light" | "dark" | "system";
declare global {
  interface Window {
    LeamAppearance?: { getPreference(): Preference; setPreference(value: Preference): boolean };
  }
}
export function AppearanceSettings() {
  const [preference, setPreference] = useState<Preference>(() => window.LeamAppearance?.getPreference() || "system");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const update = () => setPreference(window.LeamAppearance?.getPreference() || "system");
    window.addEventListener("leam:themechange", update);
    return () => window.removeEventListener("leam:themechange", update);
  }, []);
  return <section className="card appearance-settings" aria-labelledby="appearance-heading">
    <h3 id="appearance-heading"><Palette size={20} aria-hidden="true" /> Appearance</h3>
    <p>A quieter space for your day. Follow your device, or choose your own light.</p>
    <label htmlFor="colour-theme">Colour theme</label>
    <select id="colour-theme" value={preference} onChange={event => {
      const value = event.target.value as Preference;
      const controller = window.LeamAppearance;
      if (!controller) { setNotice("Appearance could not load. Reload to try again."); return; }
      const saved = controller.setPreference(value);
      setPreference(value);
      setNotice(saved ? "Saved on this device." : "Applied for this visit. Browser storage is unavailable.");
    }}>
      <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
    </select>
    <small>System follows your device’s appearance. Your choice stays on this browser.</small>
    {notice && <p role="status">{notice}</p>}
  </section>;
}
