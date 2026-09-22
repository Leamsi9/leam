import { ComposerModelPicker } from "../composer-model-picker";
import { outputAvailable, unlockSpeech } from "./engines";
import type { PlaybackTarget } from "./playback-source";
import { useEffect, useRef, useState } from "react";
import { AudioLines, Ear, Mic, Square } from "lucide-react";
import { readInputPreferences } from "./input-preferences";
import { DictationControls, ReadAloud } from "./controls";
import {
  PhoneConversation,
  type PhoneState,
  type Receipt,
  setActiveConversation,
} from "./conversation";
import { recognitionAvailable } from "./engines";
import { voiceChatTargets } from "./chat-targets";
import { playback } from "./playback";

export type ChatVoiceReply = {
  runId: string;
  messageId: string;
  /** null follows the run while live text has no persisted message ID yet. */
  playbackMessageId?: string | null;
  /** Failed live partials remain available per message, but are not the last reply. */
  replayEligible?: boolean;
  text: string;
  final: boolean;
  proseElementId?: string;
};
export type ChatVoiceRun = { id: string; terminal: boolean; failed: boolean };

/** Shared by every chat surface; transport owns receipt and reply correlation. */
export function VoiceComposer(props: {
  threadId: string;
  playbackSource: Omit<PlaybackTarget, "runId">;
  draft: string;
  onDraft: (text: string) => void;
  disabled: boolean;
  replyPending?: boolean;
  replayReply?: ChatVoiceReply;
  submit: (text: string) => Promise<Receipt>;
  dictationDisabled?: boolean;
  messages: ChatVoiceReply[];
  runs: ChatVoiceRun[];
}) {
  const [state, setState] = useState<PhoneState>({
    phase: "off",
    notice: "",
    preview: "",
  });
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!state.activeUntil) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [state.activeUntil]);
  const current = useRef(props);
  current.current = props;
  const engine = useRef<PhoneConversation | null>(null);
  useEffect(() => {
    let mounted = true;
    const phone = new PhoneConversation(
      (value) => {
        if (mounted) setState(value);
      },
      (text) => current.current.submit(text),
      () => current.current.draft,
      (text) => current.current.onDraft(text),
      () => current.current.disabled,
      () => current.current.playbackSource,
      () => !!current.current.replyPending,
    );
    engine.current = phone;
    const hide = () => {
      if (document.hidden) phone.detach();
    };
    const pagehide = () => phone.stop();
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", pagehide);
    return () => {
      mounted = false;
      phone.detach();
      setActiveConversation(null, phone);
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("pagehide", pagehide);
    };
  }, [props.threadId]);
  useEffect(() => {
    const phone = engine.current;
    if (!phone) return;
    const source = props.playbackSource;
    return voiceChatTargets.register(source, () => {
      if (
        engine.current !== phone ||
        current.current.playbackSource.module !== source.module ||
        current.current.playbackSource.threadId !== source.threadId
      ) return;
      unlockSpeech();
      setActiveConversation(phone);
      if (phone.state.phase === "speaking") phone.interruptReadout();
      else {
        playback.stop();
        const settings = readInputPreferences();
        phone.start(settings.language, settings.pauseSeconds * 1000);
      }
    });
  }, [props.threadId, props.playbackSource.module, props.playbackSource.threadId]);
  useEffect(() => {
    const phone = engine.current;
    if (!phone || ["off", "paused"].includes(phone.state.phase)) return;
    if (
      props.draft.trim() &&
      !(phone.state.phase === "sending" && props.draft === phone.state.preview)
    )
      phone.stop("Conversation stopped to protect your draft.");
  }, [props.draft]);
  useEffect(() => {
    const phone = engine.current,
      id = state.runId;
    if (!phone || !id) return;
    const run = props.runs.find((run) => run.id === id);
    phone.reply(
      props.messages
        .filter((message) => message.runId === id && message.text.trim())
        .map((message) => ({
          message_id: message.messageId,
          content: message.text,
          final: message.final,
          proseElementId: message.proseElementId,
        })),
      !!run?.terminal,
      !!run?.failed,
    );
  }, [props.messages, props.runs, state.runId, state.phase]);
  useEffect(() => {
    engine.current?.readoutReady();
  }, [props.disabled, props.replyPending]);
  const latestReply = props.replayReply ?? props.messages.filter((message) => message.replayEligible !== false && message.text.trim()).at(-1);
  const active = !["off", "paused"].includes(state.phase);
  const extended = !!state.activeUntil;
  const remaining = Math.max(0, Math.ceil(((state.activeUntil || now) - now) / 1000));
  function start(mode: "conversation" | "active" = "conversation") {
    unlockSpeech();
    const settings = readInputPreferences();
    if (engine.current) setActiveConversation(engine.current);
    engine.current?.start(settings.language, settings.pauseSeconds * 1000, mode);
  }
  return (
    <div className="voice-composer">
      <div className="actions voice-status-row">
        <ComposerModelPicker
          key={`${props.playbackSource.module}:${props.playbackSource.threadId}`}
          module={props.playbackSource.module}
          threadId={props.playbackSource.threadId}
        />
        <DictationControls
          threadId={props.threadId}
          draft={props.draft}
          onDraft={props.onDraft}
          disabled={props.dictationDisabled ?? props.disabled}
        />
        <button
          type="button"
          className="secondary voice-icon"
          aria-label={active && !extended ? "End conversation" : "Conversation"}
          aria-pressed={active && !extended}
          title={active && !extended ? "End conversation" : "Conversation"}
          disabled={
            (!active || extended) &&
            (props.disabled ||
              !!props.draft.trim() ||
              !recognitionAvailable() ||
              !outputAvailable())
          }
          onClick={() => (active && !extended ? engine.current?.stop() : start())}
        >
          {active && !extended ? (
            <Square size={20} aria-hidden="true" />
          ) : (
            <AudioLines size={20} aria-hidden="true" />
          )}
        </button>
        <button
          type="button"
          className="secondary voice-icon active-listening-toggle"
          aria-label={extended ? "Stop active listening" : "Active listening (5 minutes)"}
          aria-pressed={extended}
          title={extended ? "Stop active listening" : "Keep listening through pauses for up to 5 minutes"}
          disabled={!extended && (props.disabled || !!props.draft.trim() || !recognitionAvailable() || !outputAvailable())}
          onClick={() => extended ? engine.current?.stop("Active listening stopped.") : start("active")}
        >
          <Ear size={20} aria-hidden="true" />
        </button>
        {latestReply && props.playbackSource.threadId && (
          <ReadAloud
            label="Replay last reply"
            text={latestReply.text}
            final={latestReply.final}
            target={{ ...props.playbackSource, runId: latestReply.runId, messageId: latestReply.playbackMessageId === null ? undefined : latestReply.playbackMessageId ?? latestReply.messageId }}
          />
        )}
        {state.phase === "speaking" && (
          <button
            type="button"
            className="secondary voice-icon"
            aria-label="Speak now"
            title="Stop readout and start listening"
            onClick={() => engine.current?.interruptReadout()}
          >
            <Mic size={18} aria-hidden="true" />
          </button>
        )}
        {state.phase === "paused" && (
          <button
            type="button"
            className="secondary"
            disabled={props.disabled || !!props.draft.trim()}
            onClick={() => start()}
          >
            Resume conversation
          </button>
        )}
        {state.notice && !extended && <small role="status">{state.notice}</small>}
      </div>
      {extended && <small className="active-listening-status">
        {state.phase === "starting" ? "Starting microphone" : state.phase === "listening" ? "Microphone on" : "Microphone off"}
        {" · "}<span role="timer" aria-label="Active listening time remaining" aria-live="off">
          {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")} left
        </span>
        {!["starting", "listening"].includes(state.phase) && state.notice && <> · {state.notice}</>}
      </small>}
      {state.preview && (
        <small className="voice-preview" aria-label="Conversation speech">
          {state.preview}
        </small>
      )}
    </div>
  );
}
