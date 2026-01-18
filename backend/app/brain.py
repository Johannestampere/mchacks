"""
Brain module: Routes user intent to either a conversational response
or a device control action via the Large Action Model service.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# Max conversation history to keep (user + assistant pairs)
MAX_HISTORY_TURNS = 10

# Wake phrase patterns (case-insensitive)
WAKE_PHRASES = [
    r"\bhey[,\s]+wink\b",
    r"\bhi[,\s]+wink\b",
    r"\bokay[,\s]+wink\b",
    r"\bok[,\s]+wink\b",
]

# Seconds of inactivity before requiring wake phrase again
CONVERSATION_TIMEOUT = 30.0

# Confirmation patterns (user confirming a proposed action)
CONFIRMATION_PATTERNS = [
    r"^yes\b", r"^yeah\b", r"^yep\b", r"^yup\b", r"^sure\b",
    r"^ok\b", r"^okay\b", r"^do it\b", r"^go ahead\b", r"^please\b",
    r"^go for it\b", r"^sounds good\b", r"^let's do it\b", r"^absolutely\b",
]

# Denial patterns (user rejecting a proposed action)
DENIAL_PATTERNS = [
    r"^no\b", r"^nope\b", r"^nah\b", r"^nevermind\b", r"^never mind\b",
    r"^cancel\b", r"^don't\b", r"^stop\b", r"^wait\b", r"^hold on\b",
]


def _is_confirmation(text: str) -> bool:
    """Check if the text is a confirmation of a pending action."""
    text_lower = text.lower().strip()
    for pattern in CONFIRMATION_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def _is_denial(text: str) -> bool:
    """Check if the text is a denial/cancellation of a pending action."""
    text_lower = text.lower().strip()
    for pattern in DENIAL_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def _contains_wake_phrase(text: str) -> bool:
    """Check if the text contains a wake phrase."""
    text_lower = text.lower()
    for pattern in WAKE_PHRASES:
        if re.search(pattern, text_lower):
            return True
    return False


def _strip_wake_phrase(text: str) -> str:
    """Remove the wake phrase from the text."""
    text_stripped = text
    for pattern in WAKE_PHRASES:
        text_stripped = re.sub(pattern, "", text_stripped, flags=re.IGNORECASE)
    return text_stripped.strip(" ,.")


@dataclass
class PendingAction:
    """An action waiting for user confirmation."""
    device_id: str
    goal: str
    task_type: str = "laptop"


@dataclass
class Conversation:
    """Maintains conversation history and activation state."""
    messages: list[dict] = field(default_factory=list)
    last_interaction: float = 0.0
    is_active: bool = False
    pending_action: Optional[PendingAction] = None

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})
        self.last_interaction = time.time()
        self._trim()

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self.last_interaction = time.time()
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
        self.is_active = False
        self.pending_action = None

    def check_active(self) -> bool:
        """Check if conversation is still active (within timeout)."""
        if not self.is_active:
            return False
        if time.time() - self.last_interaction > CONVERSATION_TIMEOUT:
            self.is_active = False
            self.pending_action = None
            return False
        return True

    def activate(self) -> None:
        """Activate the conversation (wake phrase detected)."""
        self.is_active = True
        self.last_interaction = time.time()

    def set_pending_action(self, action: PendingAction) -> None:
        """Store an action awaiting user confirmation."""
        self.pending_action = action

    def get_pending_action(self) -> Optional[PendingAction]:
        """Get the current pending action, if any."""
        return self.pending_action

    def clear_pending_action(self) -> None:
        """Clear the pending action."""
        self.pending_action = None


@dataclass
class Device:
    """Represents a controllable device."""
    device_id: str
    name: str
    device_type: str  # e.g., "laptop", "phone", "tablet"


@dataclass
class IgnoredResponse:
    """Response when wake phrase not detected and conversation inactive."""
    pass


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


@dataclass
class TaskStatus:
    """Current status of a device task."""
    goal: str
    device_id: str
    status: str  # queued, started, in_progress, completed, failed
    message: str = ""

    def is_active(self) -> bool:
        return self.status in ("queued", "started", "in_progress")

    def summary_for_prompt(self) -> str:
        """Format task status for inclusion in system prompt."""
        if self.status == "queued":
            return f"PENDING TASK: '{self.goal}' on {self.device_id} - waiting to start"
        elif self.status == "started":
            return f"ACTIVE TASK: '{self.goal}' on {self.device_id} - just started"
        elif self.status == "in_progress":
            progress = f" ({self.message})" if self.message else ""
            return f"ACTIVE TASK: '{self.goal}' on {self.device_id} - in progress{progress}"
        elif self.status == "completed":
            result = f" - {self.message}" if self.message else ""
            return f"COMPLETED TASK: '{self.goal}'{result}"
        elif self.status == "failed":
            error = f" - {self.message}" if self.message else ""
            return f"FAILED TASK: '{self.goal}'{error}"
        return ""


BrainResponse = IgnoredResponse | SimpleResponse | DeviceActionResponse


def _build_system_prompt(devices: list[Device], task_status: Optional[TaskStatus] = None) -> str:
    """Build the system prompt with available devices and current task status."""
    device_descriptions = []
    for d in devices:
        device_descriptions.append(
            f'  - device_id: "{d.device_id}", name: "{d.name}", type: "{d.device_type}"'
        )

    devices_block = "\n".join(device_descriptions) if device_descriptions else "  (no devices available)"

    # Build task status section
    task_status_block = ""
    if task_status:
        task_status_block = f"""

## Current Task Status:
{task_status.summary_for_prompt()}

If user asks about the task status, refer to this information.
If a task is actively running, don't start new tasks on the same device."""

    return f"""You are an AI assistant built into smart glasses. You can see what the user sees and hear what they say. You help them with questions and can control their devices.

## Your Persona:
- You are embedded in smart glasses - you naturally see the user's view
- NEVER mention "image", "photo", "picture", "OCR", "text recognition", or similar
- NEVER thank the user for sharing visuals - you simply see through the glasses
- Respond naturally as if you're right there with them
- Keep responses brief and conversational (this is voice output)

## Conversation Style:
- Give DIRECT, helpful answers - don't ask unnecessary follow-up questions
- Be assertive and take action when the intent is clear
- Only ask for clarification when you genuinely cannot proceed without more info
- If someone asks "what's this?" - just tell them what you see
- If someone asks about something you can see, answer directly
- Avoid responses like "What would you like to know about it?" or "How can I help with that?"
- One natural response per turn, then wait for them to speak again

## Available Devices:
{devices_block}

## Response Format:
Respond with valid JSON only. No other text.

For conversational responses (questions, greetings, information):
{{"answer": "<your brief, natural response>"}}

For proposing a device action - ALWAYS include proposed_action when user asks you to do something on a device:
{{"answer": "Want me to write that for loop?", "proposed_action": {{"device_id": "macbook-pro-1", "goal": "Type a Python for loop in the PyCharm editor that is currently open", "task_type": "laptop"}}}}

## CRITICAL - When user asks for device actions:
You MUST include the "proposed_action" field in your JSON response. This is REQUIRED.
Example: User says "open youtube" -> You respond with BOTH an "answer" asking for confirmation AND a "proposed_action" object.
DO NOT just ask "Want me to do that?" without the proposed_action field - the action details MUST be included.

## CRITICAL - Goals MUST include screen context:
When writing the "goal" field, ALWAYS describe:
1. WHAT to do (click, type, open, etc.)
2. WHERE to do it (which app is visible/open)
3. The specific content if typing

BAD goals (too vague):
- "Write a for loop" (doesn't say WHERE)
- "Type some code" (doesn't say WHAT or WHERE)
- "Click the video" (doesn't describe which one)

GOOD goals (specific with context):
- "Type a Python for loop 'for i in range(10): print(i)' in the PyCharm editor that is currently open"
- "Click the first video thumbnail on the YouTube homepage that is currently visible"
- "Type 'hello world' in the Terminal app that is currently in focus"
- "Open Chrome browser using Spotlight (no browser currently open)"

Look at what's visible on screen and include that context in the goal!

## Device Control - What You Can Do:
You control devices through a screen agent that sees the screen and can:
- Type directly into apps that are ALREADY OPEN (IDE, browser, terminal, etc.)
- Click on visible elements (buttons, videos, links)
- Open NEW apps via Spotlight (only when needed)
- Navigate UI and fill forms

The agent CAN see what app is currently open - include this in your goals!
It CANNOT: control hardware (volume, brightness), run commands, or access system settings.

## Guidelines:
- Default to conversation - only trigger device actions when EXPLICITLY asked
- Greetings = conversational response, not an action
- If you see something relevant to the user's question, use that context naturally
- Never mention receiving or analyzing images - you just "see"
- Output ONLY valid JSON{task_status_block}"""


async def process_input(
    transcript: str,
    frames: list[bytes],
    devices: list[Device],
    conversation: Optional[Conversation] = None,
    task_status: Optional[TaskStatus] = None,
) -> BrainResponse:
    """
    Process user transcript and visual frames to determine intent.

    Args:
        transcript: The transcribed user speech
        frames: List of JPEG frames from the video stream (most recent last)
        devices: List of available devices the user can control
        conversation: Optional conversation history for context
        task_status: Optional current task status for context

    Returns:
        IgnoredResponse if not activated,
        SimpleResponse for conversational replies,
        or DeviceActionResponse for device control actions
    """
    # Check for wake phrase activation
    has_wake_phrase = _contains_wake_phrase(transcript)
    is_active = conversation.check_active() if conversation else False

    if not has_wake_phrase and not is_active:
        # Not activated - ignore this input
        return IgnoredResponse()

    # Activate conversation if wake phrase detected
    if has_wake_phrase and conversation:
        conversation.activate()

    # Strip wake phrase from transcript for processing
    clean_transcript = _strip_wake_phrase(transcript) if has_wake_phrase else transcript

    # If only wake phrase with no actual query, respond with acknowledgment
    if not clean_transcript.strip():
        answer = "Yes?"
        if conversation:
            conversation.add_user_message(transcript)
            conversation.add_assistant_message(answer)
        return SimpleResponse(answer=answer)

    # Check if there's a pending action awaiting confirmation
    if conversation and conversation.get_pending_action():
        pending = conversation.get_pending_action()
        print(f"[BRAIN] Pending action exists: {pending}")

        if _is_confirmation(clean_transcript):
            # User confirmed - execute the action
            print(f"[BRAIN] User confirmed action with: {clean_transcript}")
            answer = "On it."
            conversation.add_user_message(transcript)
            conversation.add_assistant_message(answer)
            conversation.clear_pending_action()
            return DeviceActionResponse(
                answer=answer,
                device_id=pending.device_id,
                goal=pending.goal,
                task_type=pending.task_type,
            )

        if _is_denial(clean_transcript):
            # User denied - clear the pending action
            answer = "Okay, nevermind."
            conversation.add_user_message(transcript)
            conversation.add_assistant_message(answer)
            conversation.clear_pending_action()
            return SimpleResponse(answer=answer)

        # User said something else - clear pending action and process normally
        conversation.clear_pending_action()

    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY environment variable is not set")

    system_prompt = _build_system_prompt(devices, task_status)

    # Build message content for current turn
    user_content: list = [{"type": "text", "text": clean_transcript}]

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

    # Try to extract JSON from the response (model sometimes outputs text before JSON)
    result = None
    json_start = response_text.find("{")
    if json_start != -1:
        # Find matching closing brace
        brace_count = 0
        json_end = -1
        for i, char in enumerate(response_text[json_start:], start=json_start):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    json_end = i + 1
                    break

        if json_end != -1:
            json_str = response_text[json_start:json_end]
            try:
                result = json.loads(json_str)
                print(f"[BRAIN] Parsed JSON: {result}")
            except json.JSONDecodeError:
                print(f"[BRAIN] JSON decode error for: {json_str}")

    if result is None:
        answer = response_text or "I didn't catch that. Could you try again?"
        # Update conversation history
        if conversation:
            conversation.add_user_message(transcript)
            conversation.add_assistant_message(answer)
        return SimpleResponse(answer=answer)

    # Check if this is a proposed action (needs user confirmation)
    if "proposed_action" in result:
        proposed = result["proposed_action"]
        answer = result.get("answer", "Want me to do that?")
        print(f"[BRAIN] Storing pending action: {proposed}")

        # Store the pending action for confirmation
        if conversation:
            conversation.set_pending_action(PendingAction(
                device_id=proposed.get("device_id", ""),
                goal=proposed.get("goal", ""),
                task_type=proposed.get("task_type", "laptop"),
            ))
            conversation.add_user_message(transcript)
            conversation.add_assistant_message(answer)

        return SimpleResponse(answer=answer)

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
    task_status: Optional[TaskStatus] = None,
) -> BrainResponse:
    """
    Simplified interface that takes a single frame instead of a list.

    Args:
        transcript: The transcribed user speech
        latest_frame: Most recent JPEG frame (or None if no video)
        devices: List of available devices
        conversation: Optional conversation history for context
        task_status: Optional current task status for context

    Returns:
        BrainResponse (SimpleResponse or DeviceActionResponse)
    """
    frames = [latest_frame] if latest_frame else []
    return await process_input(transcript, frames, devices, conversation, task_status)
