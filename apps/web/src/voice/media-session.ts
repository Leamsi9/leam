import { playback } from "./playback";

/** App-owned controls only. Never expose conversation content on a lock screen. */
export function bindMediaSession(): () => void {
  if (!("mediaSession" in navigator)) return () => {};
  const session = navigator.mediaSession;
  let attached = false, metadataKey = "";
  const actions: MediaSessionAction[] = ["play", "pause", "stop"];
  const attempt = (action: () => void) => {
    try { action(); } catch { /* Unsupported device action must not stop audio. */ }
  };
  const clear = () => {
    if (!attached) return;
    attached = false;
    metadataKey = "";
    for (const name of actions) attempt(() => session.setActionHandler(name, null));
    attempt(() => { session.metadata = null; });
    attempt(() => { session.playbackState = "none"; });
    attempt(() => session.setPositionState());
  };
  const update = () => {
    const state = playback.snapshot();
    if (!state.target || state.outputEngine !== "pocket" ||
        ["idle", "completed", "error"].includes(state.phase)) {
      clear();
      return;
    }
    if (!attached) {
      attached = true;
      // Actions consult the current app owner, not a stale phrase or thread.
      attempt(() => session.setActionHandler("play", () => playback.resume()));
      attempt(() => session.setActionHandler("pause", () => playback.pause()));
      attempt(() => session.setActionHandler("stop", () => playback.stop()));
      // Streaming phrases have no truthful total reply duration or seek range.
      attempt(() => session.setPositionState());
    }
    const name = state.target.module === "companion" ? "Leam" : "Codex";
    if (metadataKey !== name) {
      metadataKey = name;
      attempt(() => {
        session.metadata = new MediaMetadata({ title: `${name} read-aloud`, artist: "Leam" });
      });
    }
    attempt(() => { session.playbackState = state.phase === "paused" ? "paused" : "playing"; });
  };
  const unsubscribe = playback.subscribe(update);
  update();
  return () => { unsubscribe(); clear(); };
}
