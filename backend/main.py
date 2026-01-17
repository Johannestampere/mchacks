from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
from dataclasses import dataclass
from typing import Optional

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

print("websockets version:", websockets.__version__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
REALTIME_TRANSCRIBE_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

app = FastAPI()
app.mount("/static", StaticFiles(directory="backend/static"), name="static")

openai_client = OpenAI(api_key=OPENAI_API_KEY)


@app.get("/")
def index() -> HTMLResponse:
    with open("backend/static/index.html", "r", encoding="utf-8") as file_handle:
        return HTMLResponse(file_handle.read())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@dataclass
class PendingBinaryPayload:
    payload_type: str
    expected_byte_length: int
    format: Optional[str] = None
    rate: Optional[int] = None


async def call_brain_model(user_text: str, latest_jpeg_frame: Optional[bytes]) -> dict:
    content = [{"type": "text", "text": user_text}]
    if latest_jpeg_frame is not None:
        b64 = base64.b64encode(latest_jpeg_frame).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    system_instructions = (
        "You are a live conversational assistant. "
        "If the user is asking you to perform an action on the laptop, respond with JSON: "
        '{"type":"laptop_task","goal":"<single clear instruction>","confirmation_text":"<what to say>"} '
        "Otherwise respond with JSON: "
        '{"type":"answer","text":"<your response>"} '
        "Return ONLY valid JSON."
    )

    response = openai_client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": content},
        ],
    )

    output_text = ""
    for item in response.output:
        if item.type == "message":
            for part in item.content:
                if part.type == "output_text":
                    output_text += part.text

    try:
        return json.loads(output_text)
    except json.JSONDecodeError:
        return {"type": "answer", "text": output_text.strip() or "Sorry—could you repeat that?"}


async def openai_transcription_worker(
    outgoing_audio_queue: "asyncio.Queue[bytes]",
    send_status,
    on_delta,
    on_final,
) -> None:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    try:
        await send_status("info", "Connecting to OpenAI realtime transcription WS...")
        async with websockets.connect(REALTIME_TRANSCRIBE_URL, additional_headers=headers) as ws:
            await send_status("info", "Connected to OpenAI realtime transcription WS")

            await ws.send(json.dumps({
                "type": "transcription_session.update",
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gpt-4o-mini-transcribe",
                    "language": "en"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500
                }
            }))
            await send_status("info", "Sent transcription_session.update")

            async def sender() -> None:
                while True:
                    pcm_bytes = await outgoing_audio_queue.get()
                    audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
                    await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64}))

            async def receiver() -> None:
                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type") or "unknown"
                    # TEMP DEBUG: show every event type arriving from OpenAI
                    await send_status("debug", f"OpenAI event: {event_type}")

                    if event_type == "conversation.item.input_audio_transcription.delta":
                        await on_delta(event.get("delta") or "")
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        await on_final(event.get("transcript") or "")
                    elif event_type == "error":
                        await send_status("error", f"OpenAI error event: {json.dumps(event)}")

            await asyncio.gather(sender(), receiver())

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

    async def send_json(message: dict) -> None:
        await websocket.send_text(json.dumps(message))

    async def send_status(state: str, message: str) -> None:
        # state: info|debug|error|queued|streaming|idle
        await send_json({"type": "laptop_status", "state": state, "message": message})

    async def on_delta(delta: str) -> None:
        nonlocal partial_text
        partial_text += delta
        await send_json({"type": "partial_transcript", "text": partial_text})

    async def on_final(transcript: str) -> None:
        nonlocal partial_text
        partial_text = ""
        await send_json({"type": "final_transcript", "text": transcript})

        brain = await call_brain_model(transcript, latest_jpeg_frame)
        if brain.get("type") == "laptop_task":
            await send_json({"type": "assistant_text", "text": brain.get("confirmation_text") or "Okay."})
            await send_status("queued", brain.get("goal") or "")
        else:
            await send_json({"type": "assistant_text", "text": brain.get("text") or ""})

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
                    if audio_queue.full():
                        with contextlib.suppress(Exception):
                            audio_queue.get_nowait()
                    await audio_queue.put(payload)
                    print("pcm bytes:", len(payload))

                pending = None

    except WebSocketDisconnect:
        return
    finally:
        if transcribe_task:
            transcribe_task.cancel()
            with contextlib.suppress(Exception):
                await transcribe_task
