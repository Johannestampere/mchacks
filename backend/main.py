from __future__ import annotations

import ssl
import certifi

import array
import asyncio
import base64
import contextlib
import json
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.app.brain import Conversation, DeviceActionResponse, IgnoredResponse, process_transcript, TaskStatus as BrainTaskStatus
from backend.app.device_registry import DEVICES
from backend.app.tts import stream_tts

print("websockets version:", websockets.__version__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
REALTIME_TRANSCRIBE_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# Connected devices registry: device_id -> WebSocket
connected_devices: dict[str, WebSocket] = {}
# Callbacks for status updates from devices: device_id -> callback function
device_status_callbacks: dict[str, callable] = {}


@app.get("/")
def index() -> HTMLResponse:
    with open("static/index.html", "r", encoding="utf-8") as file_handle:
        return HTMLResponse(file_handle.read())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/test-brain")
async def test_brain(text: str) -> dict:
    """Test endpoint to try the brain without audio/video streaming."""
    response = await process_transcript(text, None, DEVICES)
    if isinstance(response, DeviceActionResponse):
        return {
            "type": "device_action",
            "answer": response.answer,
            "device_id": response.device_id,
            "goal": response.goal,
            "task_type": response.task_type,
        }
    return {"type": "simple", "answer": response.answer}


@dataclass
class PendingBinaryPayload:
    payload_type: str
    expected_byte_length: int
    format: Optional[str] = None
    rate: Optional[int] = None


@dataclass
class TaskStatus:
    """Tracks the current device task status."""
    goal: str
    device_id: str
    status: str = "queued"  # queued, started, in_progress, completed, failed
    message: str = ""

    def is_active(self) -> bool:
        return self.status in ("queued", "started", "in_progress")

    def summary(self) -> str:
        if self.status == "queued":
            return f"Task queued: {self.goal}"
        elif self.status == "started":
            return f"Task started: {self.goal}"
        elif self.status == "in_progress":
            return f"Task in progress: {self.goal} - {self.message}"
        elif self.status == "completed":
            return f"Task completed: {self.goal} - {self.message}"
        elif self.status == "failed":
            return f"Task failed: {self.goal} - {self.message}"
        return ""


def pcm16_peak(pcm_bytes: bytes) -> int:
    if len(pcm_bytes) < 2:
        return 0
    if len(pcm_bytes) % 2 == 1:
        pcm_bytes = pcm_bytes[:-1]
    samples = array.array("h")
    samples.frombytes(pcm_bytes)
    if not samples:
        return 0
    return max(abs(sample) for sample in samples)


async def openai_transcription_worker(
    outgoing_audio_queue: "asyncio.Queue[bytes]",
    send_status,
    on_delta,
    on_final,
) -> None:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    # IMPORTANT: remove the beta header so the server accepts the current session schema.
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }

    ssl_context = ssl.create_default_context(cafile=certifi.where())

    async def safe_send(ws, payload: dict) -> None:
        await ws.send(json.dumps(payload))

    async def recv_json(ws) -> Optional[dict]:
        raw = await ws.recv()
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="ignore")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    try:
        await send_status("info", "Connecting to OpenAI realtime transcription WS...")

        async with websockets.connect(
            REALTIME_TRANSCRIBE_URL,
            additional_headers=headers,
            ssl=ssl_context,
        ) as ws:
            await send_status("info", "Connected to OpenAI realtime transcription WS")

            # Wait for session.created before sending session.update (avoids early-boot errors).
            try:
                for _ in range(50):
                    event = await asyncio.wait_for(recv_json(ws), timeout=5)
                    if not event:
                        continue
                    event_type = event.get("type") or "unknown"
                    await send_status("debug", f"OpenAI event: {event_type}")
                    if event_type == "session.created":
                        break
                    if event_type == "error":
                        await send_status("error", f"OpenAI error event: {json.dumps(event)}")
            except asyncio.TimeoutError:
                await send_status("debug", "Timed out waiting for session.created; continuing anyway")

            # Minimal config fields that are actually needed for transcription.
            await safe_send(
                ws,
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {"model": "gpt-4o-mini-transcribe", "language": "en"},
                                "turn_detection": {"type": "server_vad"},
                            }
                        },
                    },
                },
            )
            await send_status("info", "Sent session.update (transcription config)")

            async def sender() -> None:
                while True:
                    pcm_bytes = await outgoing_audio_queue.get()
                    audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
                    await safe_send(ws, {"type": "input_audio_buffer.append", "audio": audio_b64})

            async def receiver() -> None:
                async for raw in ws:
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8", errors="ignore")
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type") or "unknown"
                    await send_status("debug", f"OpenAI event: {event_type}")

                    if event_type == "session.updated":
                        await send_status("info", "OpenAI session.updated (config accepted)")
                        continue

                    if event_type == "conversation.item.input_audio_transcription.delta":
                        await on_delta(event.get("delta") or "")
                        continue

                    if event_type == "conversation.item.input_audio_transcription.completed":
                        await on_final(event.get("transcript") or "")
                        continue

                    if event_type == "error":
                        await send_status("error", f"OpenAI error event: {json.dumps(event)}")
                        continue

            sender_task = asyncio.create_task(sender())
            receiver_task = asyncio.create_task(receiver())
            done, pending = await asyncio.wait(
                {sender_task, receiver_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc

    except Exception as e:
        await send_status("error", f"Realtime worker crashed: {type(e).__name__}: {e}")
        raise


@app.websocket("/ws")
async def ws_phone(websocket: WebSocket) -> None:
    await websocket.accept()

    pending: Optional[PendingBinaryPayload] = None
    latest_jpeg_frame: Optional[bytes] = None

    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
    partial_text: str = ""
    audio_chunk_count = 0

    # Conversation history for this session
    conversation = Conversation()

    # TTS lock to prevent overlapping speech
    tts_lock = asyncio.Lock()

    # Current task status (if any)
    current_task: Optional[TaskStatus] = None

    async def send_json(message: dict) -> None:
        await websocket.send_text(json.dumps(message))

    async def send_status(state: str, message: str) -> None:
        await send_json({"type": "laptop_status", "state": state, "message": message})

    async def speak(text: str) -> None:
        """Stream TTS audio to the client, ensuring no overlapping speech."""
        async with tts_lock:
            try:
                # Signal TTS start
                await send_json({"type": "tts_start"})

                async for chunk in stream_tts(text):
                    await websocket.send_bytes(chunk)

                # Signal TTS end
                await send_json({"type": "tts_end"})
            except Exception as e:
                await send_status("error", f"TTS error: {e}")

    async def on_delta(delta: str) -> None:
        nonlocal partial_text
        partial_text += delta
        await send_json({"type": "partial_transcript", "text": partial_text})

    async def on_final(transcript: str) -> None:
        nonlocal partial_text, current_task
        partial_text = ""
        await send_json({"type": "final_transcript", "text": transcript})

        if not transcript.strip():
            return

        # Convert current task to brain task status for context
        brain_task_status = None
        if current_task:
            brain_task_status = BrainTaskStatus(
                goal=current_task.goal,
                device_id=current_task.device_id,
                status=current_task.status,
                message=current_task.message,
            )

        response = await process_transcript(transcript, latest_jpeg_frame, DEVICES, conversation, brain_task_status)

        # If not activated (no wake phrase and conversation inactive), ignore
        if isinstance(response, IgnoredResponse):
            return

        await send_json({"type": "assistant_text", "text": response.answer})

        # Speak the response
        asyncio.create_task(speak(response.answer))

        if isinstance(response, DeviceActionResponse):
            current_task = TaskStatus(
                goal=response.goal,
                device_id=response.device_id,
                status="queued"
            )
            await send_status("queued", f"[{response.device_id}] {response.goal}")

            # Status callback that updates task status and announces completion
            async def on_task_status(status: str, message: str) -> None:
                nonlocal current_task
                if current_task:
                    current_task.status = status
                    current_task.message = message

                    # Announce completion or failure via TTS
                    if status == "completed":
                        asyncio.create_task(speak(f"Done. {message[:100]}"))
                    elif status == "failed":
                        asyncio.create_task(speak(f"Task failed. {message[:50]}"))

                await send_status(status, message)

            # Send task to device via WebSocket
            sent = await send_task_to_device(
                response.device_id,
                response.goal,
                on_status=on_task_status
            )
            if not sent:
                current_task.status = "failed"
                current_task.message = "Device not connected"
                await send_status("warning", f"Device {response.device_id} not connected")

    transcribe_task: Optional[asyncio.Task] = None

    def _task_done(task: asyncio.Task) -> None:
        exc = task.exception()
        if exc:
            asyncio.create_task(send_status("error", f"Transcription task crashed: {type(exc).__name__}: {exc}"))

    try:
        await send_json({"type": "assistant_text", "text": "Connected. Tap Start and speak."})

        await send_status("info", "Starting transcription worker...")
        transcribe_task = asyncio.create_task(openai_transcription_worker(audio_queue, send_status, on_delta, on_final))
        transcribe_task.add_done_callback(_task_done)

        while True:
            message = await websocket.receive()

            if "text" in message and message["text"] is not None:
                try:
                    envelope = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                message_type = envelope.get("type")
                if message_type in ("pcm_audio", "video_frame"):
                    pending = PendingBinaryPayload(
                        payload_type=message_type,
                        expected_byte_length=int(envelope.get("byte_length") or 0),
                        format=envelope.get("format"),
                        rate=envelope.get("rate"),
                    )
                elif message_type == "stop":
                    await send_status("idle", "Stopped.")
                continue

            if "bytes" in message and message["bytes"] is not None:
                if pending is None:
                    continue

                payload = message["bytes"]
                if len(payload) != pending.expected_byte_length:
                    await send_status("error", "Binary length mismatch")
                    pending = None
                    continue

                if pending.payload_type == "video_frame":
                    latest_jpeg_frame = payload

                elif pending.payload_type == "pcm_audio":
                    audio_chunk_count += 1
                    if audio_chunk_count % 50 == 0:
                        peak = pcm16_peak(payload)
                        await send_status("debug", f"pcm chunk bytes={len(payload)} peak={peak}")

                    if audio_queue.full():
                        with contextlib.suppress(Exception):
                            audio_queue.get_nowait()
                    await audio_queue.put(payload)

                pending = None

    except WebSocketDisconnect:
        return

    finally:
        if transcribe_task:
            transcribe_task.cancel()
            with contextlib.suppress(Exception):
                await transcribe_task


@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket) -> None:
    """WebSocket endpoint for device bridges (LAM) to connect."""
    await websocket.accept()

    device_id: Optional[str] = None

    try:
        while True:
            message = await websocket.receive_text()
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            # Device registration
            if msg_type == "device_register":
                device_id = data.get("device_id")
                if device_id:
                    connected_devices[device_id] = websocket
                    print(f"[DEVICE] Registered: {device_id} ({data.get('platform', 'unknown')})")
                    await websocket.send_text(json.dumps({
                        "type": "registered",
                        "device_id": device_id,
                        "message": "Successfully registered"
                    }))

            # Status update from device
            elif msg_type == "status_update":
                dev_id = data.get("device_id", device_id)
                callback = device_status_callbacks.get(dev_id)
                if callback:
                    await callback(data.get("status", ""), data.get("message", ""))
                print(f"[DEVICE] {dev_id} status: {data.get('status')} - {data.get('message', '')[:100]}")

            # Pong response
            elif msg_type == "pong":
                pass  # Keep-alive response

    except WebSocketDisconnect:
        pass
    finally:
        if device_id and device_id in connected_devices:
            del connected_devices[device_id]
            print(f"[DEVICE] Disconnected: {device_id}")
        if device_id and device_id in device_status_callbacks:
            del device_status_callbacks[device_id]


async def send_task_to_device(device_id: str, goal: str, on_status: callable) -> bool:
    """Send a task to a connected device. Returns True if sent successfully."""
    if device_id not in connected_devices:
        return False

    ws = connected_devices[device_id]
    device_status_callbacks[device_id] = on_status

    try:
        await ws.send_text(json.dumps({
            "type": "laptop_task",
            "goal": goal,
        }))
        return True
    except Exception as e:
        print(f"[DEVICE] Failed to send task to {device_id}: {e}")
        return False
