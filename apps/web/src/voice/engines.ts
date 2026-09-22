import {
  browserInput as browserCapture,
  browserOutput as browserPlayback,
  recognitionAvailable as browserRecognition,
} from "./speech";
import { moonshineInput, pocketOutput, unlockLocalAudio } from "./local";
import { readSpeechEngines } from "./engine-preferences";
export * from "./speech";
export type SpeechOutputEngine = "browser" | "pocket";
export function selectedSpeechOutputEngine(): SpeechOutputEngine {
  return readSpeechEngines().output;
}
export function createSpeechInput() {
  return readSpeechEngines().input === "moonshine"
    ? moonshineInput()
    : browserCapture();
}
export function createSpeechOutput(
  engine: SpeechOutputEngine = selectedSpeechOutputEngine(),
) {
  return engine === "pocket" ? pocketOutput() : browserPlayback();
}
export function browserPlaybackAvailable() {
  return !!window.speechSynthesis && !!window.SpeechSynthesisUtterance;
}
export function recognitionAvailable() {
  return readSpeechEngines().input === "moonshine"
    ? !!(
        window.isSecureContext &&
        typeof navigator.mediaDevices?.getUserMedia === "function" &&
        typeof window.AudioWorkletNode === "function"
      )
    : browserRecognition();
}
export function outputAvailable(engine = selectedSpeechOutputEngine()) {
  return engine === "pocket"
    ? typeof window.Audio === "function"
    : !!window.speechSynthesis;
}
export function unlockSpeech(engine = selectedSpeechOutputEngine()) {
  if (engine === "pocket") unlockLocalAudio();
}
