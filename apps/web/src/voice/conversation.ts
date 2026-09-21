import { playback } from "./playback";
import type { PlaybackTarget } from "./playback-source";
import { browserInput, stopSpeech, type SpeechInput } from "./speech";

export type Receipt = {
  outcome: "submitted" | "already_submitted" | "deferred_busy";
  // With autoReply=false this is the associated existing run, never a new reply promise.
  run_id: string;
  autoReply?: boolean;
};
export type PhoneState = {
  phase:
    | "off"
    | "starting"
    | "listening"
    | "finalizing"
    | "sending"
    | "waitingReply"
    | "speaking"
    | "paused";
  notice: string;
  preview: string;
  runId?: string;
};
export class PhoneConversation {
  state: PhoneState = { phase: "off", notice: "", preview: "" };
  private generation = 0;
  private captureId = 0;
  private input: SpeechInput | null = null;
  private playbackId: number | null = null;
  private quiet?: ReturnType<typeof setTimeout>;
  private idle?: ReturnType<typeof setTimeout>;
  private watchdog?: ReturnType<typeof setTimeout>;
  private restart?: ReturnType<typeof setTimeout>;
  private language = "en-GB";
  private pauseMs = 4000;
  private sealed: string[] = [];
  private final = "";
  private interim = "";
  private evidence = "";
  private idleDeadline = 0;
  private quietDeadline = 0;
  private starts: number[] = [];
  private readoutDone = false;
  private playedRuns = new Set<string>();
  constructor(
    private update: (value: PhoneState) => void,
    private submit: (text: string) => Promise<Receipt>,
    private draft: () => string,
    private recover: (text: string) => void,
    private blocked: () => boolean,
    private source: () => Omit<PlaybackTarget, "runId">,
  ) {}
  private change(patch: Partial<PhoneState>) {
    this.state = { ...this.state, ...patch };
    this.update(this.state);
  }
  private timers() {
    clearTimeout(this.quiet);
    clearTimeout(this.idle);
    clearTimeout(this.watchdog);
    clearTimeout(this.restart);
  }
  stop(notice = "") {
    this.generation++;
    this.readoutDone = false;
    this.captureId++;
    this.timers();
    this.input?.cancel();
    this.input = null;
    if (this.playbackId !== null) playback.stop(this.playbackId);
    this.playbackId = null;
    this.change({ phase: "off", notice, preview: "", runId: undefined });
  }
  detach() {
    this.playbackId = null;
    this.stop(""); // Navigation stops capture and auto-rearm; app-owned output continues.
  }
  pause(notice: string, recover = false) {
    const preview = this.preview();
    this.stop("");
    if (recover && preview && !this.draft()) this.recover(preview);
    this.change({ phase: "paused", notice, preview });
  }
  start(language: string, pauseMs = 4000) {
    if (this.blocked() || this.draft().trim()) {
      this.change({
        phase: "paused",
        notice:
          "Review or send your existing draft before starting conversation.",
      });
      return;
    }
    this.stop("");
    stopSpeech();
    this.language = language;
    this.pauseMs = pauseMs;
    this.playedRuns.clear();
    this.newTurn();
  }
  interruptReadout() {
    if (this.state.phase !== "speaking") return;
    // Fence the old output callbacks BEFORE cancelling the browser utterance.
    // The old playback identity cannot update this new listening generation.
    this.generation++;
    this.captureId++;
    this.timers();
    if (this.playbackId !== null) playback.stop(this.playbackId);
    this.playbackId = null;
    this.newTurn();
  }
  private preview() {
    return [...this.sealed, this.final, this.interim]
      .filter(Boolean)
      .join(" ")
      .trim();
  }
  private newTurn() {
    if (this.draft().trim() || this.blocked()) {
      this.pause(
        "Your draft is preserved. Review it before resuming conversation.",
      );
      return;
    }
    this.sealed = [];
    this.final = "";
    this.interim = "";
    this.evidence = "";
    this.idleDeadline = 0;
    this.quietDeadline = 0;
    this.starts = [];
    this.change({
      phase: "starting",
      notice: "Starting microphone…",
      preview: "",
      runId: undefined,
    });
    this.capture();
  }
  private arm() {
    clearTimeout(this.idle);
    clearTimeout(this.quiet);
    const token = this.generation;
    // At the maximum pause, final speech and inactivity share a deadline.
    // Finalization wins so recognized text is never discarded as no-input.
    if (
      this.idleDeadline &&
      (!this.quietDeadline || this.idleDeadline < this.quietDeadline)
    )
      this.idle = setTimeout(
        () => {
          if (token === this.generation)
            this.stop("Conversation ended after 10 seconds without speech.");
        },
        Math.max(0, this.idleDeadline - Date.now()),
      );
    if (this.quietDeadline)
      this.quiet = setTimeout(
        () => {
          if (token === this.generation) this.finish();
        },
        Math.max(0, this.quietDeadline - Date.now()),
      );
  }
  private capture() {
    if (!["starting", "listening"].includes(this.state.phase)) return;
    if (document.hidden) {
      this.stop("Conversation stopped while the app was hidden.");
      return;
    }
    const now = Date.now();
    this.starts = this.starts.filter((time) => now - time < 5000);
    if (this.starts.length >= 4) {
      this.pause(
        "Microphone stopped repeatedly. Tap Resume conversation to try again.",
        true,
      );
      return;
    }
    this.starts.push(now);
    const generation = this.generation,
      id = ++this.captureId;
    this.final = "";
    this.interim = "";
    this.evidence = "";
    const input = browserInput();
    this.input = input;
    clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => {
      if (generation === this.generation && id === this.captureId)
        this.pause(
          "Microphone did not start. Check permissions, then tap Resume conversation.",
          true,
        );
    }, 5000);
    try {
      input.start(this.language, (event) => {
        if (generation !== this.generation || id !== this.captureId) return;
        if (event.type === "started") {
          if (this.state.phase === "finalizing") return;
          clearTimeout(this.watchdog);
          this.change({ phase: "listening", notice: "Listening…" });
          if (!this.idleDeadline) this.idleDeadline = Date.now() + 10000;
          this.arm();
        } else if (event.type === "text") {
          this.final = event.finalText.trim();
          this.interim = event.interimText.trim();
          const evidence = [this.final, this.interim].filter(Boolean).join(" ");
          if (
            evidence &&
            evidence !== this.evidence &&
            this.state.phase !== "finalizing"
          ) {
            this.evidence = evidence;
            this.idleDeadline = Date.now() + 10000;
            this.quietDeadline = Date.now() + this.pauseMs;
            this.arm();
          }
          this.change({ preview: this.preview() });
        } else if (event.type === "error") {
          if (event.code === "no-speech" && this.state.phase !== "finalizing") {
            input.cancel();
            this.ended(generation, id);
          } else this.pause(event.message, true);
        } else this.ended(generation, id);
      });
    } catch (error) {
      this.pause(
        error instanceof Error
          ? error.message
          : "Microphone unavailable. Tap Resume conversation.",
        true,
      );
    }
  }
  private ended(generation: number, id: number) {
    if (generation !== this.generation || id !== this.captureId) return;
    this.input = null;
    clearTimeout(this.watchdog);
    const nextCapture = ++this.captureId;
    if (this.interim) {
      this.pause(
        "Some speech was not finalized. Review the recovered draft before sending.",
        true,
      );
      return;
    }
    if (this.final) this.sealed.push(this.final);
    this.final = "";
    this.interim = "";
    if (this.state.phase === "finalizing") {
      void this.send();
      return;
    }
    this.change({ phase: "listening", preview: this.preview() });
    this.restart = setTimeout(() => {
      if (generation === this.generation && nextCapture === this.captureId)
        this.capture();
    }, 150);
  }
  private finish() {
    if (!["listening", "starting"].includes(this.state.phase)) return;
    this.timers();
    this.change({ phase: "finalizing", notice: "Finishing speech…" });
    if (!this.input) {
      void this.send();
      return;
    }
    const generation = this.generation;
    this.watchdog = setTimeout(() => {
      if (generation === this.generation)
        this.pause(
          "Speech did not finalize. Your words are kept for review.",
          true,
        );
    }, 5000);
    try {
      this.input.finish();
    } catch {
      this.pause("Speech could not finish. Review your draft.", true);
    }
  }
  private async send() {
    if (this.state.phase !== "finalizing") return;
    this.timers();
    this.input?.cancel();
    this.input = null;
    this.captureId++;
    const text = this.sealed.join(" ").trim();
    if (!text) {
      this.pause("No final speech was recognized. Tap Resume conversation.");
      return;
    }
    if (this.draft().trim() || this.blocked()) {
      this.pause(
        "Your draft changed. Conversation paused to protect it.",
        true,
      );
      return;
    }
    const generation = this.generation;
    this.change({ phase: "sending", notice: "Sending…", preview: text });
    try {
      const receipt = await this.submit(text);
      if (generation !== this.generation) return;
      if (receipt.autoReply === false) {
        this.pause(
          "Your message was delivered as a follow-up. Read the shared conversation, then resume when ready.",
        );
        return;
      }
      if (this.playedRuns.has(receipt.run_id)) {
        this.pause(
          "This reply was already read. Review the conversation before continuing.",
        );
        return;
      }
      this.playedRuns.add(receipt.run_id);
      this.change({
        phase: "waitingReply",
        notice: "Waiting for reply…",
        runId: receipt.run_id,
        preview: "",
      });
      this.playbackId = playback.start(
        { ...this.source(), runId: receipt.run_id, automatic: true },
        "",
        false,
        (state) => {
          if (generation !== this.generation) return;
          if (state.phase === "completed") {
            this.playbackId = null;
            this.readoutDone = true;
            this.readoutReady();
          } else if (state.phase === "idle")
            this.pause("Playback stopped. Resume conversation when ready.");
          else if (state.phase === "error") this.pause(state.notice);
          else
            this.change({
              phase: state.phase === "buffering" ? "waitingReply" : "speaking",
              notice:
                state.phase === "buffering"
                  ? "Waiting for reply…"
                  : state.notice,
            });
        },
      );
    } catch (error) {
      if (generation === this.generation)
        this.pause(
          error instanceof Error
            ? error.message
            : "Message delivery is uncertain. Review the saved draft before retrying.",
        );
    }
  }
  readoutReady() {
    if (!this.readoutDone) return;
    if (this.blocked()) {
      this.change({
        phase: "waitingReply",
        notice: "Checking response completion…",
      });
      return;
    }
    this.readoutDone = false;
    this.newTurn();
  }
  reply(
    messages: {
      message_id: string;
      content: string;
      final: boolean;
      proseElementId?: string;
    }[],
    terminal: boolean,
    failed: boolean,
  ) {
    if (
      !["waitingReply", "speaking"].includes(this.state.phase) ||
      this.playbackId === null
    )
      return;
    const message = messages[messages.length - 1];
    if (failed)
      playback.update(this.playbackId, { text: "", final: true, failed: true });
    else if (message)
      playback.update(this.playbackId, {
        text: message.content,
        final: terminal && message.final,
      });
  }
}

let activeConversation: PhoneConversation | null = null;
export function setActiveConversation(
  value: PhoneConversation | null,
  expected?: PhoneConversation,
) {
  if (!expected || activeConversation === expected) {
    if (value && activeConversation && value !== activeConversation)
      activeConversation.stop("Conversation moved to another chat.");
    activeConversation = value;
  }
}
export function stopConversation(notice = "Conversation stopped.") {
  if (
    activeConversation &&
    !["off", "paused"].includes(activeConversation.state.phase)
  )
    activeConversation.stop(notice);
}
