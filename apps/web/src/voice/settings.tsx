import { useSettingsActive } from "../settings-lifecycle";
import { api } from "../api";
import {
  readSpeechEngines,
  saveSpeechEngines,
  type SpeechEngines,
} from "./engine-preferences";
import { stopSpeech } from "./engines";
import { outputAvailable, unlockSpeech } from "./engines";
import { useEffect, useRef, useState } from "react";
import { Volume2, Square } from "lucide-react";
import { createSpeechOutput, type SpeechOutput } from "./engines";
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
  const panelActive = useSettingsActive();
  const [input, setInput] = useState(readInputPreferences);
  const [engines, setEngines] = useState(readSpeechEngines);
  const [profile, setProfile] = useState<SpeechEngines["output"]>(() => readSpeechEngines().output);
  const [localReady, setLocalReady] = useState(false);
  const [localVoices, setLocalVoices] = useState<string[]>([]);
  const [localNotice, setLocalNotice] = useState(
    "Checking local speech availability…",
  );
  function refreshLocal() {
    void api("/voice/status")
      .then((result) => {
        if (!mounted.current) return;
        setLocalReady(result.ready === true);
        setLocalVoices(
          result.ready === true && Array.isArray(result.voices)
            ? result.voices.filter(
                (voice: unknown): voice is string =>
                  typeof voice === "string" &&
                  /^[a-z0-9][a-z0-9_-]{0,79}$/.test(voice),
              )
            : [],
        );
        setLocalNotice(
          result.ready
            ? "Local English speech is ready on your Leam host."
            : String(result.notice || "Local speech is unavailable."),
        );
      })
      .catch(() => {
        if (mounted.current) {
          setLocalVoices([]);
          setLocalNotice(
            "Could not check local speech. Browser speech remains available.",
          );
        }
      });
  }
  function selectEngines(value: SpeechEngines) {
    stopConversation();
    stopSpeech();
    output.current?.cancel();
    if (value.output !== engines.output) setProfile(value.output);
    setEngines(value);
    const saved = saveSpeechEngines(value);
    setNotice(
      saved
        ? "Input and output saved independently on this browser."
        : "Browser storage is unavailable. Using these engines in this tab for now.",
    );
  }
  const [preferences, setPreferences] = useState(readVoicePreferences),
    [voices, setVoices] = useState(availableVoices);
  const [notice, setNotice] = useState(""),
    [speaking, setSpeaking] = useState(false);
  const container = useRef<HTMLElement>(null);
  const output = useRef<SpeechOutput | null>(null),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    refreshLocal();
    const refresh = () => setVoices(availableVoices());
    const visible = () => { if (!document.hidden) refresh(); };
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
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visible);
    window.speechSynthesis?.addEventListener?.("voiceschanged", refresh);
    window.addEventListener("storage", storage);
    window.addEventListener(voicePreferencesChanged, preferencesChanged);
    document.addEventListener("visibilitychange", hide);
    return () => {
      mounted.current = false;
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visible);
      disclosure?.removeEventListener("toggle", toggle);
      output.current?.cancel();
      window.speechSynthesis?.removeEventListener?.("voiceschanged", refresh);
      window.removeEventListener("storage", storage);
      window.removeEventListener(voicePreferencesChanged, preferencesChanged);
      document.removeEventListener("visibilitychange", hide);
    };
  }, []);
  useEffect(() => { if (!panelActive) output.current?.cancel(); }, [panelActive]);
  const selected = preferences.voice ? voiceIdentity(preferences.voice) : "";
  const missing =
    !!selected && !voices.some((voice) => voiceIdentity(voice) === selected);
  const localMissing = !localVoices.includes(preferences.pocketVoice);
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
        Speech recognition
        <select
          aria-label="Speech recognition engine"
          value={engines.input}
          onChange={(event) =>
            selectEngines({
              ...engines,
              input: event.target.value as SpeechEngines["input"],
            })
          }
        >
          <option value="browser">Browser speech recognition</option>
          <option value="moonshine" disabled={!localReady}>
            Moonshine · local English
          </option>
        </select>
      </label>
      <label>
        Speech playback
        <select
          aria-label="Speech playback engine"
          value={engines.output}
          onChange={(event) =>
            selectEngines({
              ...engines,
              output: event.target.value as SpeechEngines["output"],
            })
          }
        >
          <option value="browser">Device / browser voice</option>
          <option value="pocket" disabled={!localReady}>
            Pocket TTS · local English
          </option>
        </select>
      </label>
      <p role="status">{localNotice}</p>
      <small>Pocket buffers each short phrase before playing, and supports background audio and device media controls where available. Device/browser voices are best effort; background playback and media controls vary by browser.</small>
      <p>The ear button keeps foreground listening available for up to 5 minutes.
        Silence sends no chat request; recognition still uses microphone, processing and network resources.</p>
      <button type="button" className="secondary" onClick={refreshLocal}>
        Check local speech
      </button>
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
          audio. Moonshine sends short audio chunks only to your Leam host for
          recognition; Pocket reads text on that host using your selected installed stock
          voice. Neither local engine stores audio. Your main conversational
          model is unchanged. Device / browser playback may use an online OS
          voice. If Pocket stops, Leam visibly retries the current short phrase
          with that device / browser voice; microphone audio is never part of
          this fallback. Pause detection uses transcript activity, not precise
          acoustic silence detection. Browser support varies.
        </p>
      </details>
      {(engines.input === "moonshine" || engines.output === "pocket") && (
        <p>
          Local models currently support English. Input and playback never run
          together. Pocket stock voices are provided by Kyutai under CC BY 4.0 or
          CC0. See the{" "}
          <a
            href="https://huggingface.co/kyutai/pocket-tts-without-voice-cloning"
            target="_blank"
            rel="noreferrer"
          >
            individual voice credits
          </a>
          .
        </p>
      )}
      <h4>Provider preferences</h4>
      <label>
        Configure provider
        <select aria-label="Configure voice provider" value={profile} onChange={(event) => {
          output.current?.cancel();
          setProfile(event.target.value as SpeechEngines["output"]);
        }}>
          <option value="browser">Device / browser</option>
          <option value="pocket">Pocket TTS</option>
        </select>
      </label>
      <small>Each provider remembers its own voice and speed. Changing this profile does not change your playback engine. Browser preferences also apply when Pocket falls back.</small>
      <button type="button" className="secondary" onClick={() => {
        setVoices(availableVoices());
        refreshLocal();
      }}>Refresh voices</button>
      {!outputAvailable(profile) && (
        <p role="status">Speech playback is unavailable in this browser.</p>
      )}
      <label>
        Speaking voice
        <select
          aria-label="Speaking voice"
          value={
            profile === "pocket" ? preferences.pocketVoice : selected
          }
          disabled={
            profile === "pocket"
              ? !localReady || !localVoices.length
              : !window.speechSynthesis
          }
          onChange={(event) => {
            if (profile === "pocket") {
              if (localVoices.includes(event.target.value))
                save({ ...preferences, pocketVoice: event.target.value });
              return;
            }
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
          {profile === "pocket" ? (
            <>
              {localMissing && (
                <option value={preferences.pocketVoice}>
                  {preferences.pocketVoice} · unavailable on your Leam host
                </option>
              )}
              {localVoices.map((voice) => (
                <option key={voice} value={voice}>
                  {voice.charAt(0).toUpperCase() + voice.slice(1)} · local
                  English
                </option>
              ))}
            </>
          ) : (
            <>
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
            </>
          )}
        </select>
      </label>
      {profile === "browser" && missing && (
        <p role="status">
          Your saved voice is unavailable. Playback uses an available device
          voice until it returns or you choose another.
        </p>
      )}
      {profile === "pocket" && localMissing && (
        <p role="status">
          Your saved Pocket voice is unavailable on this Leam host. Choose an
          installed voice when local speech is ready.
        </p>
      )}
      {profile === "browser" &&
        !voices.length &&
        window.speechSynthesis && (
          <small>
            No named voices reported yet. The list updates when the browser
            loads them; automatic playback can still be available.
          </small>
        )}
      <label>
        Speaking speed · {(profile === "pocket" ? preferences.pocketRate : preferences.rate).toFixed(2)}×
        <input
          aria-label="Speaking speed"
          type="range"
          min={0.5}
          max={2}
          step={0.05}
          value={profile === "pocket" ? preferences.pocketRate : preferences.rate}
          onChange={(event) =>
            save({ ...preferences, [profile === "pocket" ? "pocketRate" : "rate"]: Number(event.target.value) })
          }
        />
      </label>
      <div className="actions">
        <button
          type="button"
          className="secondary"
          disabled={!outputAvailable(profile)}
          onClick={() => {
            if (speaking) {
              output.current?.cancel();
              return;
            }
            stopConversation();
            unlockSpeech(profile);
            output.current = createSpeechOutput(profile);
            setSpeaking(true);
            output.current.speak(
              "Hello. This is how Leam will sound on this device.",
              profile === "pocket" ? "en-GB" : navigator.language || "en-GB",
              (state) => {
                if (!mounted.current) return;
                setSpeaking(state === "speaking");
                if (state === "blocked") {
                  output.current?.cancel();
                  setNotice("Audio was blocked by this browser. Tap Preview voice to try again.");
                }
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
            save({
              version: 1,
              voice: null,
              pocketVoice: "alba",
              rate: 1,
              pocketRate: 1,
            });
          }}
        >
          Reset voice settings
        </button>
      </div>
      {notice && <p role="status">{notice}</p>}
      <small>
        Saved only in this browser, not synced across devices or included in
        server backups. Device voices come from your browser/OS; online voices
        may send text to its speech service. Pocket voices come from your Leam
        host. The microphone stays off during playback.
      </small>
    </section>
  );
}
