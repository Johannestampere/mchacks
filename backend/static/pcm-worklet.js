// Sends Float32 audio frames from the mic to the main thread.
// We keep it simple here; resampling happens in main thread.
class PCMWorkletProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel0 = input[0];
    if (!channel0) return true;

    // Copy to transfer (Float32Array gets transferred as an ArrayBuffer)
    const buffer = new Float32Array(channel0.length);
    buffer.set(channel0);
    this.port.postMessage(buffer, [buffer.buffer]);
    return true;
  }
}

registerProcessor("pcm-worklet", PCMWorkletProcessor);
