export type SpeechEngines = {
  input: "browser" | "moonshine";
  output: "browser" | "pocket";
};
const key = "leam.voice.engines.v1";
let fallback: SpeechEngines = { input: "browser", output: "browser" };
let memoryOnly = false;
export function readSpeechEngines(): SpeechEngines {
  if (memoryOnly) return fallback;
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return {
      input: value.input === "moonshine" ? "moonshine" : "browser",
      output: value.output === "pocket" ? "pocket" : "browser",
    };
  } catch {
    return fallback;
  }
}
export function saveSpeechEngines(value: SpeechEngines) {
  fallback = value;
  try {
    localStorage.setItem(key, JSON.stringify(value));
    memoryOnly = false;
  } catch {
    memoryOnly = true;
  }
  window.dispatchEvent(new Event("leam:voice-engines"));
  return !memoryOnly;
}
