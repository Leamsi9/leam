import { useEffect, useSyncExternalStore } from "react";
import { Mic, Pause, Play, Square, Volume2, X } from "lucide-react";
import { playback } from "./playback";
import { voiceChatTargets } from "./chat-targets";
import type { PlaybackTarget } from "./playback-source";
import { recognitionAvailable, outputAvailable, unlockSpeech, stopRecognition } from "./engines";
import { stopConversation } from "./conversation";
import { bindMediaSession } from "./media-session";
export function PlaybackPanel({ onReturnToChat }: { onReturnToChat: (target: PlaybackTarget) => void }) {
  const state = useSyncExternalStore(playback.subscribe, playback.snapshot);
  useSyncExternalStore(voiceChatTargets.subscribe, voiceChatTargets.snapshot);
  useEffect(() => {
    const releaseMedia = bindMediaSession();
    const stop = () => playback.clear();
    window.addEventListener("leam:auth-lost", stop);
    window.addEventListener("pagehide", stop);
    return () => {
      stop();
      releaseMedia();
      window.removeEventListener("leam:auth-lost", stop);
      window.removeEventListener("pagehide", stop);
    };
  }, []);
  const target = state.target || state.replayTarget;
  if (!target) return null;
  const attached = voiceChatTargets.has(target);
  const seconds = Math.floor(state.elapsedMs / 1000);
  return (
    <aside className="playback-panel" aria-label="Speech playback">
      <Volume2 size={18} aria-hidden="true" />
      <div>
        <strong>
          {target.module === "companion" ? "Leam" : "Codex"} read-aloud
        </strong>
        <small aria-label="Playback elapsed">
          {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}{" "}
          elapsed · {!state.target ? "Stopped" : state.final ? "Duration unavailable" : "Live"}
        </small>
        <small role="status">{state.notice || "Last readout available to replay."}</small>
      </div>
      <div className="playback-actions">
      <button type="button" className="secondary voice-icon" aria-label="Dismiss playback" title="Stop audio and dismiss playback" onClick={() => playback.clear()}><X size={18} aria-hidden="true" /></button>
      <button
        type="button"
        className="secondary voice-icon"
        aria-label={attached ? "Speak" : "Return to chat to speak"}
        title={attached ? "Stop readout and speak in this chat" : "Return to the original chat; microphone stays off"}
        disabled={attached && (!recognitionAvailable() || !outputAvailable())}
        onClick={() => {
          if (voiceChatTargets.speak(target)) return;
          playback.pause();
          onReturnToChat(target);
        }}
      >
        <Mic size={18} aria-hidden="true" />
        <span>{attached ? "Speak" : "Return to chat"}</span>
      </button>
      {state.replayTarget && (
        <button
          type="button"
          className="secondary voice-icon"
          aria-label="Replay last output"
          title="Replay the last reply from the beginning"
          disabled={!outputAvailable()}
          onClick={() => {
            stopConversation("Conversation paused for replay.");
            stopRecognition();
            unlockSpeech();
            playback.replay();
          }}
        >
          <Volume2 size={18} aria-hidden="true" />
        </button>
      )}
      {state.target && !["error", "completed"].includes(state.phase) && (
        <button
          className="secondary voice-icon"
          aria-label={
            state.phase === "paused" ? "Resume playback" : "Pause playback"
          }
          onClick={() =>
            state.phase === "paused" ? playback.resume() : playback.pause()
          }
        >
          {state.phase === "paused" ? <Play size={18} /> : <Pause size={18} />}
        </button>
      )}
      {state.target && <button
        className="secondary voice-icon"
        aria-label="Stop playback"
        onClick={() => playback.stop()}
      >
        <Square size={18} />
      </button>}
      </div>
    </aside>
  );
}
