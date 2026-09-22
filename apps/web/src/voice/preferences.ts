/** Output preferences are per browser/device; OS voice inventories are not portable. */
export type VoiceChoice = { uri: string; name: string; lang: string };
export type VoicePreferences = {
  version: 1;
  voice: VoiceChoice | null;
  pocketVoice: string;
  rate: number;
  pocketRate: number;
};
export const voicePreferencesKey = "leam.voice.output.v1";
export const voicePreferencesChanged = "leam:voice-preferences";
const defaults = (): VoicePreferences => ({
  version: 1,
  voice: null,
  pocketVoice: "alba",
  rate: 1,
  pocketRate: 1,
});
let fallback = defaults();
let memoryOnly = false;
function normalized(value: unknown): VoicePreferences {
  if (
    !value ||
    typeof value !== "object" ||
    (value as VoicePreferences).version !== 1
  )
    return defaults();
  const input = value as VoicePreferences;
  const voice = input.voice;
  return {
    version: 1,
    pocketVoice:
      typeof input.pocketVoice === "string" &&
      /^[a-z0-9][a-z0-9_-]{0,79}$/.test(input.pocketVoice)
        ? input.pocketVoice
        : "alba",
    rate:
      typeof input.rate === "number" && Number.isFinite(input.rate)
        ? Math.max(0.5, Math.min(2, input.rate))
        : 1,
    // Preserve the former shared speed until the owner edits either profile.
    pocketRate:
      typeof input.pocketRate === "number" && Number.isFinite(input.pocketRate)
        ? Math.max(0.5, Math.min(2, input.pocketRate))
        : typeof input.rate === "number" && Number.isFinite(input.rate)
          ? Math.max(0.5, Math.min(2, input.rate))
          : 1,
    voice:
      voice &&
      typeof voice.uri === "string" &&
      voice.uri.length <= 1024 &&
      typeof voice.name === "string" &&
      voice.name.length <= 256 &&
      typeof voice.lang === "string" &&
      voice.lang.length <= 80
        ? { uri: voice.uri, name: voice.name, lang: voice.lang }
        : null,
  };
}
export function readVoicePreferences(): VoicePreferences {
  if (memoryOnly) return fallback;
  try {
    const raw = localStorage.getItem(voicePreferencesKey);
    return raw ? normalized(JSON.parse(raw)) : defaults();
  } catch {
    return fallback;
  }
}
export function saveVoicePreferences(value: VoicePreferences): boolean {
  fallback = normalized(value);
  let persisted = true;
  try {
    localStorage.setItem(voicePreferencesKey, JSON.stringify(fallback));
  } catch {
    persisted = false;
  }
  memoryOnly = !persisted;
  window.dispatchEvent(new Event(voicePreferencesChanged));
  return persisted;
}
export function voiceIdentity(
  voice: SpeechSynthesisVoice | VoiceChoice,
): string {
  return JSON.stringify([
    "voiceURI" in voice ? voice.voiceURI : voice.uri,
    voice.name,
    voice.lang,
  ]);
}
export function availableVoices(): SpeechSynthesisVoice[] {
  try {
    return window.speechSynthesis?.getVoices() || [];
  } catch {
    return [];
  }
}
export function chooseOutputVoice(
  preferences: VoicePreferences,
  language: string,
): SpeechSynthesisVoice | null {
  const voices = availableVoices();
  return (
    (preferences.voice &&
      voices.find(
        (voice) => voiceIdentity(voice) === voiceIdentity(preferences.voice!),
      )) ||
    voices.find(
      (voice) =>
        voice.localService &&
        voice.lang.split("-")[0] === language.split("-")[0],
    ) ||
    null
  );
}
