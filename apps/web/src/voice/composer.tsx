import type { PlaybackTarget } from "./playback-source";
import { useEffect, useRef, useState } from "react";
import { AudioLines, Mic, Square } from "lucide-react";
import { readInputPreferences } from "./input-preferences";
import { DictationControls } from "./controls";
import {
  PhoneConversation,
  type PhoneState,
  type Receipt,
  setActiveConversation,
} from "./conversation";
import { recognitionAvailable } from "./speech";

export type ChatVoiceReply = {
  runId: string;
  messageId: string;
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
  }, [props.disabled]);
  const active = !["off", "paused"].includes(state.phase);
  function start() {
    const settings = readInputPreferences();
    if (engine.current) setActiveConversation(engine.current);
    engine.current?.start(settings.language, settings.pauseSeconds * 1000);
  }
  return (
    <div className="voice-composer">
      <div className="actions voice-status-row">
        <DictationControls
          threadId={props.threadId}
          draft={props.draft}
          onDraft={props.onDraft}
          disabled={props.dictationDisabled ?? props.disabled}
        />
        <button
          type="button"
          className="secondary voice-icon"
          aria-label={active ? "End conversation" : "Conversation"}
          aria-pressed={active}
          title={active ? "End conversation" : "Conversation"}
          disabled={
            !active &&
            (props.disabled ||
              !!props.draft.trim() ||
              !recognitionAvailable() ||
              !window.speechSynthesis)
          }
          onClick={() => (active ? engine.current?.stop() : start())}
        >
          {active ? (
            <Square size={20} aria-hidden="true" />
          ) : (
            <AudioLines size={20} aria-hidden="true" />
          )}
        </button>
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
            onClick={start}
          >
            Resume conversation
          </button>
        )}
        {state.notice && <small role="status">{state.notice}</small>}
      </div>
      {state.preview && (
        <small className="voice-preview" aria-label="Conversation speech">
          {state.preview}
        </small>
      )}
    </div>
  );
}
