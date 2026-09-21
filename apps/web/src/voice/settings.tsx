import { useEffect, useRef, useState } from "react";
import { Volume2, Square } from "lucide-react";
import { browserOutput, type SpeechOutput } from "./speech";
import { stopConversation } from "./conversation";
import {
  defaultInputPreferences,
  readInputPreferences,
  saveInputPreferences,
  type InputPreferences,
} from "./input-preferences";
import {
  availableVoices,
  readVoicePreferences,
  saveVoicePreferences,
  voiceIdentity,
  voicePreferencesKey,
  voicePreferencesChanged,
  type VoicePreferences,
} from "./preferences";

export function VoiceSettings() {
  const [input, setInput] = useState(readInputPreferences);
  const [preferences, setPreferences] = useState(readVoicePreferences),
    [voices, setVoices] = useState(availableVoices);
  const [notice, setNotice] = useState(""),
    [speaking, setSpeaking] = useState(false);
  const container = useRef<HTMLElement>(null);
  const output = useRef<SpeechOutput | null>(null),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const refresh = () => setVoices(availableVoices());
    const preferencesChanged = () => {
      output.current?.cancel();
      setPreferences(readVoicePreferences());
    };
    const storage = (event: StorageEvent) => {
      if (event.key === voicePreferencesKey || event.key === null)
        preferencesChanged();
    };
    const hide = () => {
      if (document.hidden) output.current?.cancel();
    };
    const disclosure = container.current?.closest("details");
    const toggle = () => {
      if (!disclosure?.open) output.current?.cancel();
    };
    disclosure?.addEventListener("toggle", toggle);
    refresh();
    window.speechSynthesis?.addEventListener?.("voiceschanged", refresh);
    window.addEventListener("storage", storage);
    window.addEventListener(voicePreferencesChanged, preferencesChanged);
    document.addEventListener("visibilitychange", hide);
    return () => {
      mounted.current = false;
      disclosure?.removeEventListener("toggle", toggle);
      output.current?.cancel();
      window.speechSynthesis?.removeEventListener?.("voiceschanged", refresh);
      window.removeEventListener("storage", storage);
      window.removeEventListener(voicePreferencesChanged, preferencesChanged);
      document.removeEventListener("visibilitychange", hide);
    };
  }, []);
  const selected = preferences.voice ? voiceIdentity(preferences.voice) : "";
  const missing =
    !!selected && !voices.some((voice) => voiceIdentity(voice) === selected);
  const unique = [
    ...new Map(voices.map((voice) => [voiceIdentity(voice), voice])).values(),
  ].sort(
    (a, b) => a.lang.localeCompare(b.lang) || a.name.localeCompare(b.name),
  );
  function save(value: VoicePreferences) {
    setPreferences(value);
    const persisted = saveVoicePreferences(value);
    setNotice(
      persisted
        ? "Saved on this browser. Applies to the next playback everywhere in Leam."
        : "Browser storage is unavailable. Using these settings in this tab for now.",
    );
  }
  function saveInput(value: InputPreferences) {
    setInput(value);
    const persisted = saveInputPreferences(value);
    setNotice(
      persisted
        ? "Saved on this browser. Applies to the next dictation or conversation."
        : "Browser storage is unavailable. Using these settings in this tab for now.",
    );
  }
  return (
    <section
      ref={container}
      className="card settings-form"
      aria-label="Voice and playback settings"
    >
      <h3>Voice and playback</h3>
      <p>
        Choose this device's voice and speaking speed for Companion, Coding,
        ticket chats and read-aloud.
      </p>
      <label>
        Speech language
        <input
          value={input.language}
          maxLength={40}
          onChange={(event) =>
            saveInput({ ...input, language: event.target.value })
          }
        />
      </label>
      <label>
        Pause before sending (seconds)
        <input
          type="number"
          min={1}
          max={10}
          step={0.5}
          value={input.pauseSeconds}
          onChange={(event) =>
            saveInput({
              ...input,
              pauseSeconds: Math.min(
                10,
                Math.max(1, Number(event.target.value) || 4),
              ),
            })
          }
        />
      </label>
      <small>
        Conversation waits this long after speech before sending. The separate
        10-second no-input limit ends listening when you have not spoken.
      </small>
      <details>
        <summary>Speech behavior and privacy</summary>
        <p>
          Dictation adds editable text; review it before sending. Conversation
          sends after a pause, reads the matching reply, then listens again. It
          ends after 10 seconds without speech while listening. Ending voice
          does not cancel an accepted message.
        </p>
        <p>
          Your browser may send audio to its speech service. Leam does not save
          audio. Playback may use an online OS voice. Pause detection uses
          transcript activity, not precise acoustic silence detection. Browser
          support varies.
        </p>
      </details>
      {!window.speechSynthesis && (
        <p role="status">Speech playback is unavailable in this browser.</p>
      )}
      <label>
        Speaking voice
        <select
          aria-label="Speaking voice"
          value={selected}
          disabled={!window.speechSynthesis}
          onChange={(event) => {
            const voice = voices.find(
              (item) => voiceIdentity(item) === event.target.value,
            );
            save({
              ...preferences,
              voice: voice
                ? { uri: voice.voiceURI, name: voice.name, lang: voice.lang }
                : null,
            });
          }}
        >
          <option value="">Automatic device voice</option>
          {missing && (
            <option value={selected}>
              {preferences.voice?.name} · unavailable here
            </option>
          )}
          {unique.map((voice) => (
            <option key={voiceIdentity(voice)} value={voiceIdentity(voice)}>
              {voice.name} · {voice.lang} ·{" "}
              {voice.localService ? "device" : "online"}
              {voice.default ? " · default" : ""}
            </option>
          ))}
        </select>
      </label>
      {missing && (
        <p role="status">
          Your saved voice is unavailable. Playback uses an available device
          voice until it returns or you choose another.
        </p>
      )}
      {!voices.length && window.speechSynthesis && (
        <small>
          No named voices reported yet. The list updates when the browser loads
          them; automatic playback can still be available.
        </small>
      )}
      <label>
        Speaking speed · {preferences.rate.toFixed(2)}×
        <input
          aria-label="Speaking speed"
          type="range"
          min={0.5}
          max={2}
          step={0.05}
          value={preferences.rate}
          onChange={(event) =>
            save({ ...preferences, rate: Number(event.target.value) })
          }
        />
      </label>
      <div className="actions">
        <button
          type="button"
          className="secondary"
          disabled={!window.speechSynthesis}
          onClick={() => {
            if (speaking) {
              output.current?.cancel();
              return;
            }
            stopConversation();
            output.current = browserOutput();
            setSpeaking(true);
            output.current.speak(
              "Hello. This is how Leam will sound on this device.",
              navigator.language || "en-GB",
              (state) => {
                if (!mounted.current) return;
                setSpeaking(state === "speaking");
                if (state === "error")
                  setNotice(
                    "Playback could not start. Try another voice or check this browser's audio settings.",
                  );
              },
            );
          }}
        >
          {speaking ? (
            <Square size={18} aria-hidden="true" />
          ) : (
            <Volume2 size={18} aria-hidden="true" />
          )}
          {speaking ? "Stop voice preview" : "Preview voice"}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => {
            saveInput(defaultInputPreferences());
            save({ version: 1, voice: null, rate: 1 });
          }}
        >
          Reset voice settings
        </button>
      </div>
      {notice && <p role="status">{notice}</p>}
      <small>
        Saved only in this browser, not synced across devices or included in
        server backups. Available voices come from your browser/OS; online
        voices may send text to its speech service. The microphone stays off
        during playback.
      </small>
    </section>
  );
}
