import type { PlaybackTarget } from "./playback-source";

type ChatTarget = Pick<PlaybackTarget, "module" | "threadId">;
const key = (target: ChatTarget) => JSON.stringify([target.module, target.threadId]);
const mounted = new Map<string, Map<symbol, () => void>>();
const listeners = new Set<() => void>();
let revision = 0;
function changed() {
  revision++;
  listeners.forEach((listener) => listener());
}

/** Only mounted composers can receive an explicit playback-panel mic action. */
export const voiceChatTargets = {
  subscribe: (listener: () => void) => {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  snapshot: () => revision,
  register(target: ChatTarget, speak: () => void) {
    if (!target.threadId) return () => {};
    const id = key(target), owner = Symbol();
    const entries = mounted.get(id) || new Map<symbol, () => void>();
    entries.set(owner, speak);
    mounted.set(id, entries);
    changed();
    return () => {
      entries.delete(owner);
      if (!entries.size && mounted.get(id) === entries) mounted.delete(id);
      changed();
    };
  },
  has: (target: ChatTarget) => !!mounted.get(key(target))?.size,
  speak(target: ChatTarget) {
    const entries = mounted.get(key(target));
    // Most recently mounted instance of this same chat; cleanup is owner-fenced.
    const speak = entries && [...entries.values()].at(-1);
    if (!speak) return false;
    speak();
    return true;
  },
};
