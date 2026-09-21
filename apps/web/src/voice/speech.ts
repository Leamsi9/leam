import { readVoicePreferences, chooseOutputVoice } from "./preferences";

/** Browser speech is an adapter, not the companion or conversation authority. */
export type CaptureEvent =
  | { type: "started" }
  | { type: "text"; finalText: string; interimText: string }
  | { type: "ended" }
  | { type: "error"; code?: string; message: string };
export interface SpeechInput {
  start(language: string, emit: (event: CaptureEvent) => void): void;
  finish(): void;
  cancel(): void;
}
type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onresult:
    | ((event: {
        results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
      }) => void)
    | null;
  start(): void;
  stop(): void;
  abort(): void;
};
type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition;
  webkitSpeechRecognition?: new () => Recognition;
};
export function recognitionAvailable() {
  const browser = window as SpeechWindow;
  return (
    window.isSecureContext &&
    !!(browser.SpeechRecognition || browser.webkitSpeechRecognition)
  );
}
let stopActiveInput: (() => void) | undefined;
export function stopRecognition() {
  stopActiveInput?.();
}
export function browserInput(): SpeechInput {
  let current: Recognition | undefined;
  const cancel = () => {
    const previous = current;
    current = undefined;
    if (previous) stopActiveInput = undefined;
    if (previous) {
      previous.onresult =
        previous.onend =
        previous.onerror =
        previous.onstart =
          null;
      try {
        previous.abort();
      } catch {
        /* Already stopped. */
      }
    }
  };
  return {
    start(language, emit) {
      cancel();
      const browser = window as SpeechWindow;
      const Constructor =
        browser.SpeechRecognition || browser.webkitSpeechRecognition;
      if (!Constructor || !window.isSecureContext)
        throw new Error(
          "Speech recognition is unavailable. You can still type.",
        );
      stopRecognition();
      const session = new Constructor();
      current = session;
      stopActiveInput = () => {
        cancel();
        emit({
          type: "error",
          code: "cancelled",
          message: "Dictation stopped. Your typed draft is unchanged.",
        });
      };
      session.lang = language;
      session.continuous = false;
      session.interimResults = true;
      session.onstart = () => {
        if (current === session) emit({ type: "started" });
      };
      session.onresult = (event) => {
        if (current !== session) return;
        const final: string[] = [],
          interim: string[] = [];
        for (let i = 0; i < event.results.length; i++) {
          const result = event.results[i];
          (result.isFinal ? final : interim).push(result[0].transcript);
        }
        emit({
          type: "text",
          finalText: final.join(" "),
          interimText: interim.join(" "),
        });
      };
      session.onerror = (event) => {
        if (current !== session) return;
        const messages: Record<string, string> = {
          "not-allowed":
            "Microphone or speech permission was denied. Check this site's browser permissions, or type instead.",
          "service-not-allowed":
            "Your browser's speech service is unavailable. Try your main browser or type instead.",
          "audio-capture":
            "The microphone is unavailable. Check your device and browser permissions.",
          network:
            "The browser speech service could not connect. Your typed draft is unchanged.",
          "no-speech": "No speech was recognized. Tap Dictate to try again.",
          "language-not-supported":
            "This speech language is unavailable. Choose another language or type instead.",
        };
        emit({
          type: "error",
          code: event.error,
          message:
            messages[event.error] ||
            "Speech recognition stopped. You can retry or type instead.",
        });
      };
      session.onend = () => {
        if (current === session) {
          current = undefined;
          stopActiveInput = undefined;
          emit({ type: "ended" });
        }
      };
      session.start();
    },
    finish() {
      current?.stop();
    },
    cancel,
  };
}
export interface SpeechOutput {
  speak(
    text: string,
    language: string,
    emit: (state: "speaking" | "completed" | "cancelled" | "error") => void,
    session?: symbol,
  ): void;
  pause(): boolean;
  resume(): boolean;
  cancel(): void;
}
// All buttons share the browser's output queue; replacing playback releases its owner.
let stopActive: (() => void) | undefined;
let sessionOwner: { token: symbol; stop: () => void } | undefined;
export function ownSpeechSession(stop: () => void) {
  const token = Symbol("speech session");
  sessionOwner = { token, stop };
  return {
    token,
    release: () => {
      if (sessionOwner?.token === token) sessionOwner = undefined;
    },
  };
}
export function stopSpeech(preserve?: symbol) {
  if (sessionOwner && sessionOwner.token !== preserve) sessionOwner.stop();
  stopActive?.();
}
export function browserOutput(): SpeechOutput {
  let generation = 0;
  let utterance: SpeechSynthesisUtterance | undefined;
  let paused = false;
  let started = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let report:
    | ((state: "speaking" | "completed" | "cancelled" | "error") => void)
    | undefined;
  const terminal = (reason: "completed" | "cancelled" | "error") => {
    generation++;
    clearTimeout(timer);
    if (stopActive === cancel) {
      stopActive = undefined;
      window.speechSynthesis?.cancel();
    }
    utterance = undefined;
    paused = false;
    started = false;
    const previous = report;
    report = undefined;
    previous?.(reason);
  };
  const cancel = () => terminal("cancelled");
  return {
    speak(text, language, emit, session) {
      stopRecognition();
      stopSpeech(session);
      cancel();
      if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
        emit("error");
        return;
      }
      stopActive = cancel;
      report = emit;
      const token = generation;
      const preferences = readVoicePreferences();
      const voice = chooseOutputVoice(preferences, language);
      // Bounded chunks avoid one long browser utterance and keep cancellation responsive.
      const chunks = text.match(/[\s\S]{1,260}(?:\s|$)|[\s\S]{1,260}/g) || [];
      let index = 0;
      const next = () => {
        if (generation !== token) return;
        if (index >= chunks.length) {
          terminal("completed");
          return;
        }
        started = false;
        utterance = new SpeechSynthesisUtterance(chunks[index++]);
        utterance.voice = voice;
        utterance.lang = voice?.lang || language;
        utterance.rate = preferences.rate;
        const error = () => {
          if (generation === token) terminal("error");
        };
        utterance.onstart = () => {
          if (generation === token) {
            started = true;
            if (paused) return;
            clearTimeout(timer);
            timer = setTimeout(
              error,
              Math.max(45000, 45000 / preferences.rate),
            );
            emit("speaking");
          }
        };
        utterance.onend = () => {
          if (generation === token) {
            clearTimeout(timer);
            next();
          }
        };
        utterance.onerror = error;
        timer = setTimeout(error, 5000);
        try {
          window.speechSynthesis.resume?.();
          window.speechSynthesis.speak(utterance);
        } catch {
          error();
        }
      };
      next();
    },
    cancel,
    pause() {
      if (!utterance || typeof window.speechSynthesis?.pause !== "function")
        return false;
      clearTimeout(timer);
      paused = true;
      window.speechSynthesis.pause();
      return true;
    },
    resume() {
      if (!utterance || typeof window.speechSynthesis?.resume !== "function")
        return false;
      paused = false;
      window.speechSynthesis.resume();
      timer = setTimeout(() => terminal("error"), 90000);
      if (started) report?.("speaking");
      return true;
    },
  };
}
