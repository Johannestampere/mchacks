const startButton = document.getElementById("startButton");
const stopButton = document.getElementById("stopButton");
const statusPill = document.getElementById("statusPill");
const preview = document.getElementById("preview");
const transcriptBox = document.getElementById("transcriptBox");
const assistantBox = document.getElementById("assistantBox");
const taskBox = document.getElementById("taskBox");
const fpsInput = document.getElementById("fpsInput");
const audioChunkMsInput = document.getElementById("audioChunkMsInput");
const logBox = document.getElementById("logBox");
const autoScrollLog = document.getElementById("autoScrollLog");
const clearLogButton = document.getElementById("clearLogButton");


let mediaStream = null;
let websocket = null;
let frameTimer = null;
let sessionId = null;

let audioContext = null;
let workletNode = null;
let audioSourceNode = null;
let pcmSendTimer = null;

let pcmInputSampleRate = null;
let pcmFloatBufferQueue = [];

// TTS playback state
let ttsAudioChunks = [];
let ttsReceiving = false;
let currentTtsAudio = null;

function isNearBottom(el, thresholdPx = 40) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < thresholdPx;
}

function appendLog(text) {
  if (!logBox) return;
  const shouldStick = autoScrollLog?.checked && isNearBottom(logBox);
  appendBox(logBox, text.endsWith("\n") ? text : text + "\n");
  if (shouldStick) logBox.scrollTop = logBox.scrollHeight;
}

function fmtTs() {
  const t = new Date();
  return t.toISOString().split("T")[1].replace("Z", "");
}

function setStatus(text) {
  statusPill.textContent = text;
}

function appendBox(box, text) {
  box.textContent = (box.textContent + text).slice(-20000);
}

function replaceBox(box, text) {
  box.textContent = text.slice(-20000);
}

function wsUrlFromLocation() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

function floatToInt16Pcm(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    let sample = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return int16;
}

function downsampleTo24k(float32Array, inputRate) {
  const targetRate = 24000;
  if (inputRate === targetRate) return float32Array;

  const ratio = inputRate / targetRate;
  const newLength = Math.floor(float32Array.length / ratio);
  const result = new Float32Array(newLength);

  let pos = 0;
  for (let i = 0; i < newLength; i++) {
    const idx = Math.floor(pos);
    result[i] = float32Array[idx] ?? 0;
    pos += ratio;
  }
  return result;
}

function concatFloat32(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Float32Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

async function startAudioStreamingPCM() {
  if (!websocket || websocket.readyState !== WebSocket.OPEN) return;

  audioContext = new (window.AudioContext || window.webkitAudioContext)({
    latencyHint: "interactive"
  });
  pcmInputSampleRate = audioContext.sampleRate;

  await audioContext.audioWorklet.addModule("/static/pcm-worklet.js");

  audioSourceNode = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-worklet");

  workletNode.port.onmessage = (event) => {
    pcmFloatBufferQueue.push(event.data);
  };

  audioSourceNode.connect(workletNode);

  pcmSendTimer = setInterval(() => {
    if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
    if (pcmFloatBufferQueue.length === 0) return;

    const merged = concatFloat32(pcmFloatBufferQueue);
    pcmFloatBufferQueue = [];

    const downsampled = downsampleTo24k(merged, pcmInputSampleRate);
    const pcm16 = floatToInt16Pcm(downsampled);
    const bytes = new Uint8Array(pcm16.buffer);

    websocket.send(JSON.stringify({
      type: "pcm_audio",
      session_id: sessionId,
      format: "audio/pcm",
      rate: 24000,
      byte_length: bytes.byteLength
    }));
    websocket.send(bytes);
  }, 100);
}

function startFrameStreaming() {
  const fps = Math.max(0, Math.min(10, Number(fpsInput.value) || 2));
  if (fps === 0) return;

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: false });

  frameTimer = setInterval(() => {
    if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
    if (preview.videoWidth === 0 || preview.videoHeight === 0) return;

    const targetWidth = 640;
    const scale = targetWidth / preview.videoWidth;
    const w = targetWidth;
    const h = Math.round(preview.videoHeight * scale);

    canvas.width = w;
    canvas.height = h;
    ctx.drawImage(preview, 0, 0, w, h);

    canvas.toBlob(async (blob) => {
      if (!blob) return;
      const arrayBuffer = await blob.arrayBuffer();

      websocket.send(JSON.stringify({
        type: "video_frame",
        session_id: sessionId,
        format: "image/jpeg",
        byte_length: arrayBuffer.byteLength
      }));
      websocket.send(arrayBuffer);
    }, "image/jpeg", 0.7);
  }, Math.round(1000 / fps));
}

async function start() {
  transcriptBox.textContent = "";
  assistantBox.textContent = "";
  taskBox.textContent = "";
  setStatus("requesting permissions...");

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("Camera/mic not available. On iPhone, this usually means you must use an https:// URL.");
  }

  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true },
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }
  });

  preview.srcObject = mediaStream;

  setStatus("connecting...");
  websocket = new WebSocket(wsUrlFromLocation());
  websocket.binaryType = "arraybuffer";

  websocket.onopen = async () => {
    sessionId = crypto.randomUUID();
    websocket.send(JSON.stringify({ type: "hello", session_id: sessionId, client: "iphone_web" }));
    appendLog(`[${fmtTs()}] ws.open`);

    setStatus("streaming");
    startButton.disabled = true;
    stopButton.disabled = false;

    await startAudioStreamingPCM();
    startFrameStreaming();
  };

websocket.onmessage = (event) => {
  // Handle text messages (JSON)
  if (typeof event.data === "string") {
    try {
      const message = JSON.parse(event.data);
      appendLog(`[${fmtTs()}] ${message.type || "unknown"} ${JSON.stringify(message)}`);

      // TTS control messages
      if (message.type === "tts_start") {
        ttsReceiving = true;
        ttsAudioChunks = [];
        return;
      }
      if (message.type === "tts_end") {
        ttsReceiving = false;
        playTtsAudio();
        return;
      }

      // Existing UI behavior:
      if (message.type === "partial_transcript" || message.type === "final_transcript") {
        replaceBox(transcriptBox, message.text || "");
        return;
      }
      if (message.type === "assistant_text_delta") {
        appendBox(assistantBox, message.delta || "");
        return;
      }
      if (message.type === "assistant_text") {
        replaceBox(assistantBox, message.text || "");
        return;
      }
      if (message.type === "laptop_status") {
        replaceBox(taskBox, `${message.state || ""}\n${message.message || ""}`.trim());
        return;
      }
    } catch {
      appendLog(`[${fmtTs()}] ws.text ${event.data}`);
    }
    return;
  }

  // Handle binary messages (TTS audio chunks)
  if (event.data instanceof ArrayBuffer) {
    if (ttsReceiving) {
      ttsAudioChunks.push(new Uint8Array(event.data));
      appendLog(`[${fmtTs()}] tts_chunk (${event.data.byteLength} bytes)`);
    } else {
      appendLog(`[${fmtTs()}] ws.binary (${event.data?.byteLength ?? "?"} bytes)`);
    }
  }
};

  websocket.onclose = () => {
  appendLog(`[${fmtTs()}] ws.close`);
  setStatus("disconnected");
  cleanup();
};

websocket.onerror = () => {
  appendLog(`[${fmtTs()}] ws.error`);
  setStatus("error");
  cleanup();
};
}

function playTtsAudio() {
  if (ttsAudioChunks.length === 0) return;

  // Combine all chunks into a single ArrayBuffer
  const totalLength = ttsAudioChunks.reduce((acc, chunk) => acc + chunk.length, 0);
  const combined = new Uint8Array(totalLength);
  let offset = 0;
  for (const chunk of ttsAudioChunks) {
    combined.set(chunk, offset);
    offset += chunk.length;
  }
  ttsAudioChunks = [];

  // Create a blob and play it
  const blob = new Blob([combined], { type: "audio/mpeg" });
  const url = URL.createObjectURL(blob);

  // Stop any currently playing TTS
  if (currentTtsAudio) {
    currentTtsAudio.pause();
    currentTtsAudio = null;
  }

  const audio = new Audio(url);
  currentTtsAudio = audio;

  audio.onended = () => {
    URL.revokeObjectURL(url);
    currentTtsAudio = null;
  };

  audio.onerror = () => {
    URL.revokeObjectURL(url);
    currentTtsAudio = null;
    appendLog(`[${fmtTs()}] tts_playback_error`);
  };

  audio.play().catch((e) => {
    appendLog(`[${fmtTs()}] tts_play_failed: ${e.message}`);
  });
}

function stop() {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.send(JSON.stringify({ type: "stop", session_id: sessionId }));
  }
  if (websocket) websocket.close();
  cleanup();
  setStatus("disconnected");
}

function cleanup() {
  startButton.disabled = false;
  stopButton.disabled = true;

  if (frameTimer) {
    clearInterval(frameTimer);
    frameTimer = null;
  }

  if (pcmSendTimer) {
    clearInterval(pcmSendTimer);
    pcmSendTimer = null;
  }
  pcmFloatBufferQueue = [];

  if (workletNode) {
    try { workletNode.disconnect(); } catch {}
    workletNode = null;
  }
  if (audioSourceNode) {
    try { audioSourceNode.disconnect(); } catch {}
    audioSourceNode = null;
  }
  if (audioContext) {
    try { audioContext.close(); } catch {}
    audioContext = null;
  }

  if (mediaStream) {
    for (const track of mediaStream.getTracks()) track.stop();
  }
  mediaStream = null;

  // Stop TTS playback
  if (currentTtsAudio) {
    currentTtsAudio.pause();
    currentTtsAudio = null;
  }
  ttsAudioChunks = [];
  ttsReceiving = false;

  websocket = null;
  sessionId = null;
}

if (clearLogButton) {
  clearLogButton.addEventListener("click", () => {
    if (logBox) logBox.textContent = "";
  });
}

startButton.addEventListener("click", () => start().catch((e) => {
  setStatus("permission/stream error");
  replaceBox(taskBox, String(e));
  cleanup();
}));

stopButton.addEventListener("click", stop);
