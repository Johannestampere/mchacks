"""
Brain module: Routes user intent to either a conversational response
or a device control action via the Large Action Model service.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# Max conversation history to keep (user + assistant pairs)
MAX_HISTORY_TURNS = 10


@dataclass
class Conversation:
    """Maintains conversation history."""
    messages: list[dict] = field(default_factory=list)

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self._trim()

    def _trim(self) -> None:
        # Keep only the last N turns (each turn = 2 messages)
        max_messages = MAX_HISTORY_TURNS * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

    def get_messages(self) -> list[dict]:
        return self.messages.copy()

    def clear(self) -> None:
        self.messages = []


@dataclass
class Device:
    """Represents a controllable device."""
    device_id: str
    name: str
    device_type: str  # e.g., "laptop", "phone", "tablet"


@dataclass
class SimpleResponse:
    """A conversational response with no device action."""
    answer: str


@dataclass
class DeviceActionResponse:
    """A response that triggers a device action via the LAM."""
    answer: str
    device_id: str
    goal: str
    task_type: str = "laptop"


BrainResponse = SimpleResponse | DeviceActionResponse


def _build_system_prompt(devices: list[Device]) -> str:
    """Build the system prompt with available devices."""
    device_descriptions = []
    for d in devices:
        device_descriptions.append(
            f'  - device_id: "{d.device_id}", name: "{d.name}", type: "{d.device_type}"'
        )

    devices_block = "\n".join(device_descriptions) if device_descriptions else "  (no devices available)"

    return f"""You are a helpful voice assistant that can either answer questions conversationally or help users control their devices.

## Available Devices:
{devices_block}

## Your Task:
Analyze the user's request and determine if they want:
1. A conversational response (questions, chitchat, information)
2. To control or perform an action on one of their devices

## Response Format:
Respond with valid JSON only. No other text.

For conversational responses:
{{"answer": "<your helpful response>"}}

For device control requests:
{{"answer": "<brief confirmation of what you'll do>", "device_id": "<device_id from list above>", "goal": "<clear instruction for what to do>", "task_type": "<device type, defaults to laptop>"}}

## Guidelines:
- Determine the correct device_id from context (e.g., "my laptop" -> match to a laptop device_id, "my phone" -> match to a phone device_id)
- The "goal" should be a clear, actionable instruction (e.g., "Open Chrome and navigate to youtube.com")
- The "task_type" should match the device type (laptop, phone, tablet, etc.) - defaults to "laptop" if unclear
- Keep the answer brief and natural for voice output
- Output ONLY valid JSON"""


async def process_input(
    transcript: str,
    frames: list[bytes],
    devices: list[Device],
    conversation: Optional[Conversation] = None,
) -> BrainResponse:
    """
    Process user transcript and visual frames to determine intent.

    Args:
        transcript: The transcribed user speech
        frames: List of JPEG frames from the video stream (most recent last)
        devices: List of available devices the user can control
        conversation: Optional conversation history for context

    Returns:
        Either a SimpleResponse for conversational replies,
        or a DeviceActionResponse for device control actions
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY environment variable is not set")

    system_prompt = _build_system_prompt(devices)

    # Build message content for current turn
    user_content: list = [{"type": "text", "text": transcript}]

    # Add the most recent frame if available
    if frames:
        latest_frame = frames[-1]
        b64_image = base64.b64encode(latest_frame).decode("ascii")
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
        })

    # Build messages list with history
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history if provided
    if conversation:
        messages.extend(conversation.get_messages())

    # Add current user message
    messages.append({"role": "user", "content": user_content})

    # Make the API call to OpenRouter
    async with httpx.AsyncClient() as client:
        response = await client.post(
            OPENROUTER_BASE_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-2.0-flash-001",
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 512,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

    # Extract the response text
    response_text = data["choices"][0]["message"]["content"].strip()

    # Handle markdown code blocks if present
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        lines = [line for line in lines if not line.startswith("```")]
        response_text = "\n".join(lines).strip()

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        answer = response_text or "I didn't catch that. Could you try again?"
        # Update conversation history
        if conversation:
            conversation.add_user_message(transcript)
            conversation.add_assistant_message(answer)
        return SimpleResponse(answer=answer)

    # Check if this is a device action (has goal and device_id)
    if "goal" in result and "device_id" in result:
        answer = result.get("answer", "Okay, I'll do that.")
        # Update conversation history
        if conversation:
            conversation.add_user_message(transcript)
            conversation.add_assistant_message(answer)
        return DeviceActionResponse(
            answer=answer,
            device_id=result["device_id"],
            goal=result["goal"],
            task_type=result.get("task_type", "laptop"),
        )

    # Simple response
    answer = result.get("answer", "I'm here to help!")
    # Update conversation history
    if conversation:
        conversation.add_user_message(transcript)
        conversation.add_assistant_message(answer)
    return SimpleResponse(answer=answer)


async def process_transcript(
    transcript: str,
    latest_frame: Optional[bytes],
    devices: list[Device],
    conversation: Optional[Conversation] = None,
) -> BrainResponse:
    """
    Simplified interface that takes a single frame instead of a list.

    Args:
        transcript: The transcribed user speech
        latest_frame: Most recent JPEG frame (or None if no video)
        devices: List of available devices
        conversation: Optional conversation history for context

    Returns:
        BrainResponse (SimpleResponse or DeviceActionResponse)
    """
    frames = [latest_frame] if latest_frame else []
    return await process_input(transcript, frames, devices, conversation)
