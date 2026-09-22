// Only in-memory mono PCM; never records a file or sends network requests.
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = [];
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.finished = false;
    this.port.onmessage = () => {
      this.finished = true;
      this.flush();
      this.port.postMessage({ ended: true });
    };
  }
  flush() {
    if (this.samples.length) {
      const pcm = new Float32Array(this.samples);
      this.samples = [];
      this.port.postMessage({ pcm }, [pcm.buffer]);
    }
  }
  process(inputs) {
    if (this.finished) return false;
    for (const sample of inputs[0]?.[0] || []) {
      this.sum += sample;
      this.count++;
      this.phase += 16000;
      if (this.phase >= sampleRate) {
        this.phase -= sampleRate;
        this.samples.push(Math.max(-1, Math.min(1, this.sum / this.count)));
        this.sum = this.count = 0;
        if (this.samples.length === 8000) this.flush();
      }
    }
    return true;
  }
}
registerProcessor("leam-capture", Capture);
