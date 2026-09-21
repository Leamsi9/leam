import { useEffect, useSyncExternalStore } from "react";
import { Pause, Play, Square, Volume2 } from "lucide-react";
import { playback } from "./playback";
export function PlaybackPanel() {
  const state = useSyncExternalStore(playback.subscribe, playback.snapshot);
  useEffect(() => {
    const stop = () => playback.stop();
    const hide = () => {
      if (document.hidden) playback.pause();
    };
    window.addEventListener("leam:auth-lost", stop);
    window.addEventListener("pagehide", stop);
    document.addEventListener("visibilitychange", hide);
    return () => {
      stop();
      window.removeEventListener("leam:auth-lost", stop);
      window.removeEventListener("pagehide", stop);
      document.removeEventListener("visibilitychange", hide);
    };
  }, []);
  if (!state.target) return null;
  const seconds = Math.floor(state.elapsedMs / 1000);
  return (
    <aside className="playback-panel" aria-label="Speech playback">
      <Volume2 size={18} aria-hidden="true" />
      <div>
        <strong>
          {state.target.module === "companion" ? "Leam" : "Codex"} read-aloud
        </strong>
        <small aria-label="Playback elapsed">
          {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}{" "}
          elapsed · {state.final ? "Duration unavailable" : "Live"}
        </small>
        <small role="status">{state.notice}</small>
      </div>
      {!["error", "completed"].includes(state.phase) && (
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
      <button
        className="secondary voice-icon"
        aria-label="Stop playback"
        onClick={() => playback.stop()}
      >
        <Square size={18} />
      </button>
    </aside>
  );
}
