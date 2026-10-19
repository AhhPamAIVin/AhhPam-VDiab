// AudioWorklet processor for the voice-input feature: downsamples the mic's
// native sample rate to 16kHz mono PCM16 and posts ~100ms chunks back to the
// main thread, ready to send straight over the STT WebSocket. Loaded via
// audioContext.audioWorklet.addModule("/pcm-worklet.js") from
// hooks/use-voice-transcription.ts — served as-is from public/, same as any
// other static asset in this app.
class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._targetRate = 16000;
    this._samplesPerChunk = 1600; // 100ms @ 16kHz
    this._pending = [];
  }

  process(inputs) {
    const channelData = inputs[0] && inputs[0][0];
    if (!channelData) return true;

    const resampled =
      sampleRate === this._targetRate
        ? channelData
        : this._resample(channelData, sampleRate, this._targetRate);

    for (let i = 0; i < resampled.length; i++) {
      this._pending.push(resampled[i]);
    }

    while (this._pending.length >= this._samplesPerChunk) {
      const chunk = this._pending.splice(0, this._samplesPerChunk);
      const pcm16 = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        const s = Math.max(-1, Math.min(1, chunk[i]));
        pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    }

    return true;
  }

  _resample(input, fromRate, toRate) {
    const ratio = fromRate / toRate;
    const outLength = Math.floor(input.length / ratio);
    const out = new Float32Array(outLength);
    for (let i = 0; i < outLength; i++) {
      const srcIndex = i * ratio;
      const i0 = Math.floor(srcIndex);
      const i1 = Math.min(i0 + 1, input.length - 1);
      const frac = srcIndex - i0;
      out[i] = input[i0] * (1 - frac) + input[i1] * frac;
    }
    return out;
  }
}

registerProcessor("pcm-worklet", PCMWorkletProcessor);
