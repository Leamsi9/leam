import { unified } from "unified";
import remarkParse from "remark-parse";
import {
  browserPlaybackAvailable,
  createSpeechOutput,
  ownSpeechSession,
  selectedSpeechOutputEngine,
  stopSpeech,
  type SpeechOutput,
  type SpeechOutputEngine,
} from "./engines";
import {
  followPlayback,
  targetKey,
  type PlaybackTarget,
  type PlaybackUpdate,
} from "./playback-source";
export type PlaybackState = {
  outputEngine: SpeechOutputEngine | null;
  id: number;
  target: PlaybackTarget | null;
  replayTarget: PlaybackTarget | null;
  phase: "idle" | "buffering" | "speaking" | "paused" | "completed" | "error";
  elapsedMs: number;
  notice: string;
  final: boolean;
};
const parser = unified().use(remarkParse);
export function speechText(markdown: string): string {
  const walk = (node: any): string => {
    if (node.type === "html" || node.type === "image")
      return node.type === "image" ? node.alt || "" : "";
    if (typeof node.value === "string") return node.value;
    const text = (node.children || [])
      .map(walk)
      .join(node.type === "root" || node.type === "list" ? "\n" : "");
    return (
      text +
      (["paragraph", "heading", "code", "tableRow", "listItem"].includes(
        node.type,
      )
        ? "\n"
        : "")
    );
  };
  return walk(parser.parse(markdown.slice(0, 100000)))
    .replace(/\s+/g, " ")
    .trim();
}
class Playback {
  state: PlaybackState = {
    outputEngine: null,
    id: 0,
    target: null,
    replayTarget: null,
    phase: "idle",
    elapsedMs: 0,
    notice: "",
    final: false,
  };
  private listeners = new Set<() => void>();
  private output: SpeechOutput | null = null;
  private prepared: { output: SpeechOutput; phrase: string; prefix: string } | null = null;
  private discardPrepared() {
    this.prepared?.output.cancel();
    this.prepared = null;
  }
  private owner: ReturnType<typeof ownSpeechSession> | null = null;
  private closeSource: (() => void) | null = null;
  private text = "";
  private issued = "";
  private active = false;
  private browserFallback = false;
  private canBrowserFallback = false;
  private primaryOutput: SpeechOutputEngine = "browser";
  private utteranceStarted = false;
  private speechStarted = 0;
  private tick?: ReturnType<typeof setInterval>;
  private callback?: (state: PlaybackState) => void;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  snapshot = () => this.state;
  private publish(patch: Partial<PlaybackState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
    this.callback?.(this.state);
  }
  private account() {
    if (this.speechStarted) {
      this.state = {
        ...this.state,
        elapsedMs:
          this.state.elapsedMs + performance.now() - this.speechStarted,
      };
      this.speechStarted = 0;
    }
  }
  stop(expected?: number) {
    if (expected !== undefined && expected !== this.state.id) return;
    this.account();
    const stopped = this.callback;
    this.callback = undefined;
    this.closeSource?.();
    this.closeSource = null;
    this.owner?.release();
    this.owner = null;
    this.active = false;
    this.discardPrepared();
    const previous = this.output;
    this.output = null;
    this.state = { ...this.state, id: this.state.id + 1 };
    previous?.cancel();
    clearInterval(this.tick);
    this.text = "";
    this.issued = "";
    this.browserFallback = false;
    this.canBrowserFallback = false;
    this.primaryOutput = "browser";
    this.publish({ target: null, phase: "idle", notice: "", final: false, outputEngine: null });
    stopped?.(this.state);
  }
  clear() {
    this.stop();
    this.publish({ replayTarget: null });
  }
  replay() {
    const target = this.state.replayTarget;
    if (!target) return;
    // Fetch the current exact reply through the existing read-only follower.
    // No audio is retained. A shared-turn baseline stays in this authenticated
    // in-memory target until dismissal/logout; replay never rearms capture.
    this.start({ ...target, automatic: false }, "", false);
  }
  start(
    target: PlaybackTarget,
    initial: string,
    final: boolean,
    callback?: (state: PlaybackState) => void,
  ) {
    this.stop();
    stopSpeech();
    const id = this.state.id;
    this.primaryOutput = selectedSpeechOutputEngine();
    this.canBrowserFallback =
      this.primaryOutput === "pocket" && browserPlaybackAvailable();
    this.callback = callback;
    this.owner = ownSpeechSession(() => this.stop(id));
    this.publish({
      target,
      outputEngine: this.primaryOutput,
      phase: "buffering",
      elapsedMs: 0,
      notice: "Waiting for speech…",
      final: false,
    });
    this.tick = setInterval(() => {
      if (this.speechStarted) {
        this.account();
        this.speechStarted = performance.now();
        this.publish({});
      }
    }, 250);
    this.update(id, { text: initial, final });
    if (!final && target.threadId && target.runId)
      this.closeSource = followPlayback(
        target,
        (value) => this.update(id, value),
        () => {
          if (this.state.id === id)
            this.publish({ notice: "Reconnecting to this reply…" });
        },
      );
    return id;
  }
  updateTarget(target: PlaybackTarget, value: PlaybackUpdate) {
    if (this.state.target && targetKey(target) === targetKey(this.state.target))
      this.update(this.state.id, value);
  }
  update(id: number, value: PlaybackUpdate) {
    if (
      id !== this.state.id ||
      !this.state.target ||
      this.state.phase === "error" ||
      this.state.phase === "completed"
    )
      return;
    if (value.failed) {
      this.error(
        "The response stopped. Read the saved text before restarting playback.",
      );
      return;
    }
    const next = speechText(value.text);
    if (
      !value.final &&
      this.text.startsWith(next) &&
      next.length < this.text.length
    )
      return; // older replay
    if (!next.startsWith(this.issued)) {
      this.error(
        "The reply changed after speech began. Read the updated text, then start playback again.",
      );
      return;
    }
    if (next === this.text && (this.state.final || !value.final)) return;
    this.text = next;
    if (this.prepared && !next.startsWith(this.prepared.prefix + this.prepared.phrase))
      this.discardPrepared();
    this.publish({
      final: this.state.final || value.final,
      ...(next.trim() ? { replayTarget: { ...this.state.target } } : {}),
    });
    if (value.final) {
      this.closeSource?.();
      this.closeSource = null;
    }
    this.pump();
    this.lookAhead();
  }
  private nextPhrase(): string {
    const rest = this.text.slice(this.issued.length);
    let size = this.state.final ? Math.min(rest.length, 240) : 0;
    if (!size) {
      const sentence = /[.!?][”"')\]]*\s/.exec(rest);
      if (sentence) size = sentence.index + sentence[0].length;
    }
    if (!size && rest.length > 180) size = rest.lastIndexOf(" ", 180);
    if (!size) return "";
    size = Math.min(size, 240);
    if (size < rest.length && !/\s/.test(rest[size - 1])) {
      const space = rest.lastIndexOf(" ", size);
      if (space > 0) size = space + 1;
    }
    return rest.slice(0, size);
  }
  private lookAhead() {
    if (!this.active || this.prepared || this.state.phase !== "speaking" ||
        this.primaryOutput !== "pocket" || this.browserFallback || !this.state.target) return;
    const phrase = this.nextPhrase();
    if (!phrase.trim()) return;
    const output = createSpeechOutput("pocket");
    this.prepared = { output, phrase, prefix: this.issued };
    output.prepare?.(phrase, navigator.language || "en-GB");
  }
  private pump() {
    if (
      this.active ||
      !this.state.target ||
      ["paused", "error", "completed"].includes(this.state.phase)
    )
      return;
    const rest = this.text.slice(this.issued.length);
    if (!rest.trim()) {
      if (this.state.final) {
        this.closeSource?.();
        this.closeSource = null;
        this.owner?.release();
        this.owner = null;
        clearInterval(this.tick);
        const completed = this.callback;
        this.callback = undefined;
        this.publish({ phase: "completed", notice: "Playback finished." });
        completed?.(this.state);
      } else
        this.publish({ phase: "buffering", notice: "Waiting for more text…" });
      return;
    }
    const phrase = this.nextPhrase();
    if (!phrase) {
      this.publish({ phase: "buffering", notice: "Waiting for the next phrase…" });
      return;
    }
    let output: SpeechOutput | undefined;
    if (this.prepared?.phrase === phrase && this.prepared.prefix === this.issued) {
      output = this.prepared.output;
      this.prepared = null;
    } else this.discardPrepared();
    this.issued += phrase;
    if (!phrase.trim()) {
      this.pump();
      return;
    }
    this.active = true;
    this.utteranceStarted = false;
    const id = this.state.id;
    this.speakPhrase(id, phrase, output);
  }
  private speakPhrase(id: number, phrase: string, prepared?: SpeechOutput) {
    this.output = prepared ?? createSpeechOutput(
      this.browserFallback ? "browser" : this.primaryOutput,
    );
    this.output.speak(
      phrase,
      navigator.language || "en-GB",
      (event) => {
        if (id !== this.state.id) return;
        if (event === "speaking") {
          this.utteranceStarted = true;
          this.speechStarted ||= performance.now();
          this.publish({
            phase: "speaking",
            notice: this.browserFallback
              ? "Pocket stopped; continuing with the device voice."
              : "Reading the reply…",
          });
          this.lookAhead();
        } else if (event === "blocked") {
          this.account();
          this.publish({ phase: "paused", notice: "Audio needs your permission. Tap Resume playback to listen." });
        } else {
          this.account();
          this.active = false;
          this.output = null;
          if (event === "completed") this.pump();
          else if (event === "cancelled") this.stop(id);
          else if (!this.browserFallback && this.canBrowserFallback) {
            this.browserFallback = true;
            this.discardPrepared();
            this.active = true;
            this.utteranceStarted = false;
            this.publish({
              phase: "buffering",
              outputEngine: "browser",
              notice: "Pocket stopped; switching to the device voice…",
            });
            this.speakPhrase(id, phrase);
          } else
            this.error(
              "Playback is unavailable. Use Stop, then tap Read aloud to try again.",
            );
        }
      },
      this.owner?.token,
    );
  }
  pause() {
    if (!["speaking", "buffering"].includes(this.state.phase)) return;
    if (this.active && !this.output?.pause()) {
      this.error("This browser could not pause speech. Playback stopped.");
      return;
    }
    this.account();
    this.publish({ phase: "paused", notice: "Playback paused." });
  }
  resume() {
    if (this.state.phase !== "paused") return;
    if (this.active) {
      if (!this.output?.resume()) {
        this.error(
          "This browser could not resume speech. Start playback again.",
        );
        return;
      }
      if (this.utteranceStarted) this.speechStarted = performance.now();
      this.publish({
        phase: this.utteranceStarted ? "speaking" : "buffering",
        notice: this.utteranceStarted
          ? "Reading the reply…"
          : "Starting speech…",
      });
    } else {
      this.publish({ phase: "buffering", notice: "Waiting for more text…" });
      this.pump();
    }
  }
  private error(notice: string) {
    this.account();
    this.closeSource?.();
    this.closeSource = null;
    this.owner?.release();
    this.owner = null;
    const previous = this.output;
    this.output = null;
    this.active = false;
    this.discardPrepared();
    // Fence native cancellation before delivering the error to the owner.
    this.state = { ...this.state, id: this.state.id + 1 };
    previous?.cancel();
    clearInterval(this.tick);
    this.publish({ phase: "error", notice });
  }
}
export const playback = new Playback();
