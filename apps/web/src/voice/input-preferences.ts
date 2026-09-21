/** Recognition preferences are local to this browser; no audio is stored. */
export type InputPreferences = { language: string; pauseSeconds: number };
export const inputPreferencesKey = "leam.voice.input.v1";
export const defaultInputPreferences = (): InputPreferences => ({
  language: navigator.language || "en-GB",
  pauseSeconds: 4,
});
let fallback = defaultInputPreferences();
let memoryOnly = false;
function normalize(value: Partial<InputPreferences>): InputPreferences {
  return {
    language:
      typeof value.language === "string" &&
      value.language.trim() &&
      value.language.length <= 40
        ? value.language.trim()
        : defaultInputPreferences().language,
    pauseSeconds:
      typeof value.pauseSeconds === "number" &&
      Number.isFinite(value.pauseSeconds)
        ? Math.min(10, Math.max(1, value.pauseSeconds))
        : 4,
  };
}
export function readInputPreferences(): InputPreferences {
  if (memoryOnly) return fallback;
  try {
    const value = JSON.parse(
      localStorage.getItem(inputPreferencesKey) || "null",
    );
    return value && typeof value === "object"
      ? normalize(value)
      : defaultInputPreferences();
  } catch {
    return fallback;
  }
}
export function saveInputPreferences(value: InputPreferences): boolean {
  fallback = normalize(value);
  try {
    localStorage.setItem(inputPreferencesKey, JSON.stringify(fallback));
    memoryOnly = false;
    return true;
  } catch {
    memoryOnly = true;
    return false;
  }
}
