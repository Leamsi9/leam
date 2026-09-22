import type { Page } from "@playwright/test";

/** Browser caller fixture: records real WAV payloads without claiming OS playback. */
export async function pocketMediaProbe(page: Page) {
  await page.addInitScript(() => {
    const w = window as any;
    const blobs = new Map<string, Blob>();
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    w.mediaProbe = { plays: 0, pauses: 0, loads: 0, rejected: 0, rejectNext: false,
      urls: [], revoked: [], elements: [], handlers: {}, metadata: null, playbackState: "none" };
    URL.createObjectURL = (blob: Blob | MediaSource) => {
      const url = create(blob);
      if (blob instanceof Blob) blobs.set(url, blob);
      w.mediaProbe.urls.push(url);
      return url;
    };
    URL.revokeObjectURL = (url: string) => {
      blobs.delete(url); w.mediaProbe.revoked.push(url); revoke(url);
    };
    w.Audio = class {
      src = ""; preload = ""; playbackRate = 1; onended: any = null; onerror: any = null;
      timer: any; paused = true;
      constructor() { w.mediaProbe.elements.push(this); }
      load() { w.mediaProbe.loads++; }
      removeAttribute(name: string) { if (name === "src") this.src = ""; }
      async play() {
        w.mediaProbe.plays++;
        if (w.mediaProbe.rejectNext) {
          w.mediaProbe.rejectNext = false; w.mediaProbe.rejected++;
          throw new DOMException("Gesture needed", "NotAllowedError");
        }
        const blob = blobs.get(this.src);
        if (!blob) throw new Error("Only real Blob audio may play");
        const view = new DataView(await blob.arrayBuffer());
        w.mediaProbe.wav = { bytes: blob.size, type: blob.type, sampleRate: view.getUint32(24, true),
          channels: view.getUint16(22, true), bits: view.getUint16(34, true), dataBytes: view.getUint32(40, true) };
        this.paused = false;
        if (w.localProbe) {
          w.localProbe.played.push((blob.size - 44) / 2);
          w.localProbe.playedRates.push(this.playbackRate);
        }
        if (!w.localProbe?.holdAudio) this.timer = setTimeout(() => this.onended?.(), 80);
      }
      pause() {
        this.paused = true; clearTimeout(this.timer); w.mediaProbe.pauses++;
        if (w.localProbe) w.localProbe.stopped++;
      }
    };
    Object.defineProperty(navigator, "mediaSession", { configurable: true, value: {
      setActionHandler(name: string, handler: any) { w.mediaProbe.handlers[name] = handler; },
      set metadata(value: any) { w.mediaProbe.metadata = value; },
      get metadata() { return w.mediaProbe.metadata; },
      set playbackState(value: string) { w.mediaProbe.playbackState = value; },
      get playbackState() { return w.mediaProbe.playbackState; },
      setPositionState(value: any) { w.mediaProbe.position = value ?? null; },
    } });
  });
}
