import { outputAvailable, unlockSpeech } from "./engines";
import { playback } from "./playback";
import { targetKey, type PlaybackTarget } from "./playback-source";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Mic, Volume2, Square, X } from "lucide-react";
import { stopConversation } from "./conversation";
import { readInputPreferences } from "./input-preferences";
import {
  createSpeechInput,
  recognitionAvailable,
  stopSpeech,
  type SpeechInput,
} from "./engines";

type DictationProps = {
  threadId: string;
  draft: string;
  onDraft: (text: string) => void;
  disabled: boolean;
};
export function DictationControls({
  threadId,
  draft,
  onDraft,
  disabled,
}: DictationProps) {
  const [state, setState] = useState("idle"),
    [notice, setNotice] = useState("");
  const [preview, setPreview] = useState("");
  const capture = useRef<SpeechInput | null>(null);
  const finishing = useRef(false);
  const generation = useRef(0),
    base = useRef(""),
    final = useRef("");
  const currentDraft = useRef(draft);
  currentDraft.current = draft;
  const currentDisabled = useRef(disabled);
  currentDisabled.current = disabled;
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cancel = (message = "", preserve = false) => {
    const recovered =
      preserve &&
      currentDraft.current === base.current &&
      !currentDisabled.current
        ? final.current.trim()
        : "";
    generation.current++;
    clearTimeout(timer.current);
    capture.current?.cancel();
    capture.current = null;
    setState("idle");
    setPreview("");
    setNotice(
      message + (recovered ? " Recognized text was kept for review." : ""),
    );
    if (recovered)
      onDraft([base.current.trimEnd(), recovered].filter(Boolean).join(" "));
  };
  useEffect(() => {
    const hide = () => {
      if (document.hidden) cancel("Dictation paused. Tap Dictate when ready.");
    };
    const pagehide = () => cancel();
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", pagehide);
    return () => {
      cancel();
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("pagehide", pagehide);
    };
  }, [threadId]);
  useEffect(() => {
    if (capture.current && (draft !== base.current || disabled))
      cancel("Dictation stopped; your typed draft is preserved.");
  }, [draft, disabled]);
  useEffect(() => {
    if (!draft && notice === "Review the text, then Send.") setNotice("");
  }, [draft, notice]);
  function start() {
    if (capture.current || disabled) return;
    stopConversation("");
    stopSpeech();
    finishing.current = false;
    base.current = draft;
    final.current = "";
    setNotice("");
    setPreview("");
    setState("starting");
    const token = ++generation.current;
    const input = createSpeechInput();
    capture.current = input;
    timer.current = setTimeout(
      () =>
        cancel(
          "Speech did not start. Check microphone permissions or type instead.",
        ),
      15000,
    );
    try {
      input.start(readInputPreferences().language, (event) => {
        if (generation.current !== token) return;
        if (event.type === "started") {
          if (finishing.current) return;
          setState("listening");
          clearTimeout(timer.current);
          timer.current = setTimeout(
            () =>
              cancel(
                "Dictation reached its time limit. Please try a shorter turn.",
                true,
              ),
            90000,
          );
        } else if (event.type === "text") {
          final.current = event.finalText;
          setPreview(
            [event.finalText, event.interimText].filter(Boolean).join(" "),
          );
        } else if (event.type === "error")
          cancel(event.code === "cancelled" ? "" : event.message);
        else {
          const result = final.current.trim();
          const safe =
            currentDraft.current === base.current && !currentDisabled.current;
          cancel(
            result && safe
              ? "Review the text, then Send."
              : "No dictation added. Your draft is unchanged.",
          );
          if (result && safe)
            onDraft([base.current.trimEnd(), result].filter(Boolean).join(" "));
        }
      });
    } catch (error) {
      cancel(
        error instanceof Error ? error.message : "Speech could not start.",
      );
    }
  }
  return (
    <div className="dictation-controls">
      {state === "idle" ? (
        <button
          type="button"
          className="secondary voice-icon"
          disabled={disabled || !recognitionAvailable()}
          onClick={start}
          aria-label="Dictate"
          title={
            recognitionAvailable()
              ? "Dictate"
              : "Dictation is unavailable in this browser"
          }
        >
          <Mic size={20} aria-hidden="true" />
        </button>
      ) : (
        <>
          <button
            type="button"
            className="secondary"
            aria-label="Finish dictation"
            title="Finish dictation"
            disabled={state === "finishing"}
            onClick={() => {
              finishing.current = true;
              setState("finishing");
              clearTimeout(timer.current);
              timer.current = setTimeout(
                () => cancel("Speech did not finish.", true),
                5000,
              );
              try {
                capture.current?.finish();
              } catch {
                cancel("Speech stopped. Your typed draft is unchanged.");
              }
            }}
          >
            <Square size={18} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="secondary"
            aria-label="Cancel dictation"
            title="Cancel dictation"
            onClick={() => cancel()}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </>
      )}
      {(state !== "idle" || notice) && (
        <small role="status">
          {state === "idle"
            ? notice
            : state === "starting"
              ? "Starting microphone…"
              : state === "finishing"
                ? "Finishing dictation…"
                : "Listening…"}
        </small>
      )}
      {preview && (
        <small className="voice-preview" aria-label="Provisional dictation">
          {preview}
        </small>
      )}
    </div>
  );
}

export function ReadAloud({
  text,
  target,
  final = true,
  label = "Read aloud",
}: {
  text: string;
  target: PlaybackTarget;
  final?: boolean;
  label?: string;
}) {
  const state = useSyncExternalStore(playback.subscribe, playback.snapshot);
  const active =
    state.target &&
    targetKey(state.target) === targetKey(target) &&
    !["error", "completed"].includes(state.phase);
  useEffect(() => {
    playback.updateTarget(target, { text, final });
  }, [text, final, targetKey(target)]);
  return (
    <button
      type="button"
      className="secondary voice-icon"
      aria-label={active ? "Stop speaking" : label}
      title={active ? "Stop speaking" : label}
      disabled={!outputAvailable()}
      onClick={() => {
        if (active) {
          playback.stop();
          return;
        }
        stopConversation("Conversation stopped for manual playback.");
        unlockSpeech();
        playback.start(target, text, final);
      }}
    >
      {active ? (
        <Square size={18} aria-hidden="true" />
      ) : (
        <Volume2 size={18} aria-hidden="true" />
      )}
    </button>
  );
}
