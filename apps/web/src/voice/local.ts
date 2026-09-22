import { api } from "../api";
import { clearPrivateSession, sessionGeneration } from "../session-cache";
import {
  claimInput,
  claimOutput,
  stopRecognition,
  stopSpeech,
  type SpeechInput,
  type SpeechOutput,
  type CaptureEvent,
} from "./speech";
import { readVoicePreferences } from "./preferences";
const workletUrl = new URL("./capture-worklet.js?no-inline", import.meta.url)
  .href;

let outputAudio: HTMLAudioElement | undefined;
let audioOwner: symbol | undefined;
function pocketAudio() {
  outputAudio ??= new Audio();
  outputAudio.preload = "auto";
  return outputAudio;
}
// Microphone tracks stop immediately. A new local operation waits for the old
// worker capture to release ownership, without retrying any speech request.
let pendingCaptureClose: Promise<unknown> = Promise.resolve();
export function unlockLocalAudio() {
  // Register real media in the gesture; never play fake/silent unlock audio.
  // Browsers may still require explicit Resume after asynchronous synthesis.
  if (!audioOwner) pocketAudio().load();
}
function wave(chunks: Float32Array[], samples: number): Blob {
  const bytes = new ArrayBuffer(44 + samples * 2), view = new DataView(bytes);
  const label = (at: number, text: string) => {
    for (let n = 0; n < text.length; n++) view.setUint8(at + n, text.charCodeAt(n));
  };
  label(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true);
  label(8, "WAVE"); label(12, "fmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 24000, true); view.setUint32(28, 48000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  label(36, "data"); view.setUint32(40, samples * 2, true);
  let at = 44;
  for (const chunk of chunks) for (const sample of chunk) {
    const value = Math.max(-1, Math.min(1, sample));
    view.setInt16(at, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    at += 2;
  }
  return new Blob([bytes], { type: "audio/wav" });
}
function encoded(samples: Float32Array) {
  const bytes = new Uint8Array(
    samples.buffer,
    samples.byteOffset,
    samples.byteLength,
  );
  let value = "";
  for (let at = 0; at < bytes.length; at += 8192)
    value += String.fromCharCode(...bytes.subarray(at, at + 8192));
  return btoa(value);
}
export function moonshineInput(): SpeechInput {
  let generation = 0,
    captureId = "",
    sequence = 0,
    queued = 0;
  let media: MediaStream | undefined,
    context: AudioContext | undefined,
    node: AudioWorkletNode | undefined;
  let controller: AbortController | undefined,
    release: (() => void) | undefined;
  let emit: ((event: CaptureEvent) => void) | undefined;
  let language = "en-GB",
    serial = Promise.resolve(),
    finishing = false;
  const stopMicrophone = () =>
    media?.getTracks().forEach((track) => track.stop());
  const cleanup = () => {
    stopMicrophone();
    media = undefined;
    node?.disconnect();
    node = undefined;
    void context?.close().catch(() => undefined);
    context = undefined;
    release?.();
    release = undefined;
  };
  const cancel = () => {
    generation++;
    controller?.abort();
    controller = undefined;
    cleanup();
    if (captureId) {
      const closingId = captureId;
      pendingCaptureClose = pendingCaptureClose.then(() =>
        api("/voice/capture/cancel", "POST", { captureId: closingId }).catch(
          () => undefined,
        ),
      );
    }
    captureId = "";
    emit = undefined;
  };
  const fail = (message: string) => {
    const report = emit;
    cancel();
    report?.({ type: "error", message });
  };
  function send(samples: Float32Array, finish: boolean, token: number) {
    if (token !== generation) return;
    if (++queued > 3) {
      fail(
        "Local recognition could not keep up. Your typed draft is unchanged.",
      );
      return;
    }
    const body = {
      captureId,
      sequence: sequence++,
      pcm: encoded(samples),
      sampleRate: 16000,
      language,
      finish,
    };
    serial = serial
      .then(async () => {
        if (token !== generation) return;
        const value = await api(
          "/voice/capture",
          "POST",
          body,
          controller?.signal,
        );
        if (token !== generation) return;
        if (
          value.captureId !== body.captureId ||
          value.sequence !== body.sequence
        )
          throw new Error("Recognition response changed.");
        emit?.({
          type: "text",
          finalText: String(value.finalText || ""),
          interimText: String(value.interimText || ""),
        });
        if (finish) {
          const report = emit;
          captureId = "";
          cleanup();
          emit = undefined;
          report?.({ type: "ended" });
        }
      })
      .catch((error) => {
        if (token === generation)
          fail(
            error instanceof Error
              ? error.message
              : "Local recognition stopped.",
          );
      })
      .finally(() => {
        if (token === generation) queued--;
      });
  }
  return {
    maxCaptureMs: 60000,
    start(lang, report) {
      cancel();
      stopSpeech();
      language = lang;
      emit = report;
      finishing = false;
      sequence = queued = 0;
      serial = Promise.resolve();
      const token = generation;
      captureId = crypto.randomUUID();
      controller = new AbortController();
      release = claimInput(() =>
        fail("Dictation stopped. Your typed draft is unchanged."),
      );
      context = new AudioContext();
      void context.resume().catch(() => undefined);
      const selectedContext = context;
      void (async () => {
        await pendingCaptureClose;
        if (token !== generation) return;
        await api(
          "/voice/capture",
          "POST",
          { captureId, sequence: sequence++, pcm: "", language },
          controller?.signal,
        );
        if (token !== generation) return;
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
          },
          video: false,
        });
        if (token !== generation) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        media = stream;
        await selectedContext.audioWorklet.addModule(workletUrl);
        if (token !== generation) return;
        node = new AudioWorkletNode(selectedContext, "leam-capture");
        const source = selectedContext.createMediaStreamSource(stream);
        const muted = selectedContext.createGain();
        muted.gain.value = 0;
        source.connect(node);
        node.connect(muted);
        muted.connect(selectedContext.destination);
        node.port.onmessage = (event) => {
          if (token !== generation) return;
          if (event.data.pcm) send(event.data.pcm, false, token);
          if (event.data.ended) send(new Float32Array(), true, token);
        };
        report({ type: "started" });
        if (finishing) {
          stopMicrophone();
          node.port.postMessage("finish");
        }
      })().catch((error) => {
        if (token === generation)
          fail(
            error instanceof Error ? error.message : "Microphone unavailable.",
          );
      });
    },
    finish() {
      if (finishing) return;
      finishing = true;
      stopMicrophone();
      node?.port.postMessage("finish");
    },
    cancel,
  };
}

// Preparation owns no media or microphone. Keep only one phrase per adapter,
// bounded by the same stream limits and deadline as foreground synthesis.
async function synthesizePocket(text: string, language: string, voice: string,
  signal: AbortSignal, auth: number): Promise<Blob> {
  const check = () => !signal.aborted && auth === sessionGeneration();
  let sequence = 0, done = false, total = 0, sampleCount = 0;
  const chunks: Float32Array[] = [];
  await pendingCaptureClose;
  if (!check()) throw new Error("Speech preparation cancelled");
  const response = await fetch("/api/voice/speak", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, language, voice }),
    signal,
  });
  if (!check()) throw new Error("Speech preparation cancelled");
  if (response.status === 401) {
    clearPrivateSession();
    window.dispatchEvent(new Event("leam:auth-lost"));
  }
  if (!response.ok || !response.body) throw new Error("Local speech unavailable");
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let pending = "";
  while (check()) {
    const value = await reader.read();
    if (!check()) throw new Error("Speech preparation cancelled");
    if (value.done) break;
    pending += decoder.decode(value.value, { stream: true });
    total += value.value.length;
    if (total > 7000000) throw new Error("Speech response too large");
    let end: number;
    while ((end = pending.indexOf("\n")) >= 0) {
      if (end > 150000) throw new Error("Speech frame too large");
      const raw = pending.slice(0, end);
      pending = pending.slice(end + 1);
      const frame = JSON.parse(raw);
      if (done || frame.error) throw new Error("Speech stream stopped");
      if (frame.done === true) { done = true; continue; }
      if (frame.sequence !== sequence++ || frame.sampleRate !== 24000 ||
          typeof frame.pcm !== "string" || frame.pcm.length > 128000)
        throw new Error("Invalid speech frame");
      const bytes = Uint8Array.from(atob(frame.pcm), char => char.charCodeAt(0));
      if (!bytes.length || bytes.length % 4) throw new Error("Invalid audio samples");
      const samples = new Float32Array(bytes.buffer);
      if (!samples.every(Number.isFinite)) throw new Error("Invalid audio samples");
      chunks.push(samples);
      sampleCount += samples.length;
    }
    if (pending.length > 150000) throw new Error("Speech frame too large");
  }
  if (!check()) throw new Error("Speech preparation cancelled");
  if (!done || pending.trim() || !sampleCount) throw new Error("Incomplete speech stream");
  return wave(chunks, sampleCount);
}
type PreparedPocket = {
  text: string; language: string; voice: string; rate: number; auth: number;
  controller: AbortController;
  result: Promise<{ blob: Blob } | { error: true }>;
};
function preparePocket(text: string, language: string): PreparedPocket {
  const preferences = readVoicePreferences(), controller = new AbortController();
  const auth = sessionGeneration();
  const timeout = setTimeout(() => controller.abort(), 90000);
  const result = synthesizePocket(text, language, preferences.pocketVoice, controller.signal, auth)
    .then(blob => ({ blob }), () => ({ error: true as const }))
    .finally(() => { clearTimeout(timeout); controller.abort(); });
  return { text, language, voice: preferences.pocketVoice, rate: preferences.pocketRate, auth, controller, result };
}

export function pocketOutput(): SpeechOutput {
  let prepared: PreparedPocket | undefined;
  let generation = 0, controller: AbortController | undefined, paused = false;
  let release: (() => void) | undefined;
  let report: Parameters<SpeechOutput["speak"]>[2] | undefined;
  let audio: HTMLAudioElement | undefined, url: string | undefined;
  const owner = Symbol("Pocket audio");
  let timer: ReturnType<typeof setTimeout> | undefined;
  let remainingMs = 90000, timerStarted = 0, playAttempt = 0;
  const armTimer = () => {
    clearTimeout(timer);
    timerStarted = performance.now();
    const token = generation;
    timer = setTimeout(() => {
      if (token === generation) terminal("error");
    }, remainingMs);
  };
  const hold = () => {
    if (!paused) remainingMs = Math.max(1, remainingMs - (performance.now() - timerStarted));
    paused = true;
    clearTimeout(timer);
    playAttempt++;
    audio?.pause();
  };
  const terminal = (state: "completed" | "cancelled" | "error") => {
    generation++;
    playAttempt++;
    controller?.abort();
    controller = undefined;
    prepared?.controller.abort();
    prepared = undefined;
    clearTimeout(timer);
    if (audio && audioOwner === owner) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      audioOwner = undefined;
    }
    audio = undefined;
    if (url) URL.revokeObjectURL(url);
    url = undefined;
    release?.();
    release = undefined;
    paused = false;
    const previous = report;
    report = undefined;
    previous?.(state);
  };
  const play = () => {
    if (!audio || !url || paused) return;
    const token = generation, auth = sessionGeneration(), attempt = ++playAttempt;
    void audio.play().then(() => {
      if (token !== generation || attempt !== playAttempt || paused) return;
      if (auth !== sessionGeneration()) { terminal("cancelled"); return; }
      report?.("speaking");
    }).catch((error: unknown) => {
      if (token !== generation || attempt !== playAttempt || paused) return;
      if (auth !== sessionGeneration()) { terminal("cancelled"); return; }
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        hold();
        report?.("blocked");
      } else terminal("error");
    });
  };
  return {
    prepare(text, language) {
      prepared?.controller.abort();
      prepared = preparePocket(text, language);
    },
    speak(text, language, emit, session) {
      const preferences = readVoicePreferences();
      let selected = prepared;
      prepared = undefined;
      if (selected && (selected.text !== text || selected.language !== language ||
          selected.voice !== preferences.pocketVoice || selected.rate !== preferences.pocketRate ||
          selected.auth !== sessionGeneration())) {
        selected.controller.abort();
        selected = undefined;
      }
      stopRecognition();
      stopSpeech(session);
      terminal("cancelled");
      const ready = selected ?? preparePocket(text, language);
      report = emit;
      release = claimOutput(() => terminal("cancelled"));
      audio = pocketAudio();
      audioOwner = owner;
      const token = generation, auth = sessionGeneration();
      controller = ready.controller;
      const check = () => token === generation && auth === sessionGeneration();
      remainingMs = 90000;
      armTimer();
      void (async () => {
        await pendingCaptureClose;
        if (!check()) {
          if (token === generation) terminal("cancelled");
          return;
        }
        const prepared = await ready.result;
        if (!check()) { if (token === generation) terminal("cancelled"); return; }
        if (!("blob" in prepared)) throw new Error("Local speech unavailable");
        url = URL.createObjectURL(prepared.blob);
        audio!.src = url;
        audio!.playbackRate = ready.rate;
        audio!.onended = () => {
          if (check()) terminal("completed");
          else if (token === generation) terminal("cancelled");
        };
        audio!.onerror = () => { if (token === generation) terminal("error"); };
        play();
      })().catch(() => {
        if (token === generation)
          terminal(auth === sessionGeneration() ? "error" : "cancelled");
      });
    },
    cancel() { terminal("cancelled"); },
    pause() {
      if (!report || paused) return false;
      hold();
      return true;
    },
    resume() {
      if (!report || !paused) return false;
      paused = false;
      armTimer();
      play();
      return true;
    },
  };
}
