import os
import json
import base64
import requests
from dotenv import load_dotenv
from controller import screenshot, execute_action, get_screen_size
from data_shapes import GoalResult, HistoryEntry, DoneAction

load_dotenv(".env.local")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

SYSTEM_PROMPT = """You are a macOS automation agent. You see a screenshot and must output the NEXT SINGLE ACTION.

IMPORTANT: Look at the screenshot carefully to understand the CURRENT state before deciding your action.

Available actions:
- {"action": "click", "x": int, "y": int}
- {"action": "double_click", "x": int, "y": int}
- {"action": "right_click", "x": int, "y": int}
- {"action": "type_text", "text": "string"}
- {"action": "hotkey", "keys": ["cmd", "space"]}
- {"action": "press", "key": "return"}
- {"action": "scroll", "clicks": int}
- {"action": "move_to", "x": int, "y": int}
- {"action": "wait", "seconds": float}
- {"action": "done", "result": "description of what was accomplished"}

CRITICAL RULES:
1. Output ONLY valid JSON, no other text
2. Look at the screenshot - what do you SEE right now?
3. If Spotlight is open with text typed, press return to launch the app
4. If an app is open, interact with it directly
5. Coordinates are in screenshot pixels (the image you see)

Step-by-step patterns:
- To open an app:
  1. {"action": "hotkey", "keys": ["cmd", "space"]} - opens Spotlight
  2. {"action": "type_text", "text": "AppName"} - type the app name
  3. {"action": "press", "key": "return"} - MUST press return to launch!
- To use Calculator: type numbers and operators directly, e.g. "2+2", then press return for result
"""

def get_next_action(goal: str, screenshot_b64: str, history: list[dict] = None) -> dict:

    width, height = get_screen_size()

    # Build prompt with history context
    prompt_parts = [SYSTEM_PROMPT + f"\n\nScreen size: {width}x{height}\n\n"]

    if history:
        prompt_parts.append("Previous actions taken:\n")
        for i, entry in enumerate(history):
            prompt_parts.append(f"{i + 1}. {json.dumps(entry['action'])}\n")
        prompt_parts.append("\n")

    prompt_parts.append(f"Goal: {goal}\n\nCurrent screen state:")

    # Build content for OpenRouter
    prompt = "".join(prompt_parts) + "\n\nWhat is the next action? Output ONLY JSON."

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        },
        json={
            "model": "google/gemini-2.0-flash-001",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}}
                    ]
                }
            ]
        }
    )

    result = response.json()

    if "error" in result:
        return {"action": "done", "result": f"Failed: API error - {result['error']}"}

    if "choices" not in result:
        return {"action": "done", "result": f"Failed: Unexpected API response - {result}"}

    content_text = result["choices"][0]["message"]["content"].strip()

    # Parse JSON from response
    try:
        # Try to extract JSON from the response
        # First try direct parse
        try:
            return json.loads(content_text.strip())
        except json.JSONDecodeError:
            pass

        # Handle markdown code blocks anywhere in response
        if "```" in content_text:
            # Find JSON block
            parts = content_text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    try:
                        return json.loads(part)
                    except json.JSONDecodeError:
                        continue

        # Try to find raw JSON object in text
        start = content_text.find("{")
        end = content_text.rfind("}") + 1
        if start != -1 and end > start:
            json_str = content_text[start:end]
            return json.loads(json_str)

        raise json.JSONDecodeError("No JSON found", content_text, 0)
    except json.JSONDecodeError:
        return {"action": "done", "result": f"Failed: Could not parse model response: {content_text}"}

# Execute a goal by repeatedly passing actions
def execute_goal(goal: str, max_steps: int = 20, on_step=None) -> GoalResult:

    history: list[HistoryEntry] = []

    for step in range(max_steps):
        # Get current screen state
        current_screenshot = screenshot()

        # Ask model for next action
        action = get_next_action(goal, current_screenshot, [{"action": h.action, "screenshot": h.screenshot} for h in history])

        print(f"[STEP {step + 1}] {action}")

        # Notify callback if provided
        if on_step:
            on_step(step + 1, action, current_screenshot)

        # Check if done
        if action.get("action") == "done":
            return GoalResult(
                success="Failed" not in action.get("result", ""),
                result=action.get("result", "Completed"),
                steps=step + 1
            )

        # Execute the action
        try:
            result_screenshot = execute_action(action)
            history.append(HistoryEntry(
                action=action,
                screenshot=result_screenshot
            ))
        except Exception as e:
            return GoalResult(
                success=False,
                result=f"Action failed: {str(e)}",
                steps=step + 1
            )

    return GoalResult(
        success=False,
        result=f"Reached max steps ({max_steps}) without completing goal",
        steps=max_steps
    )