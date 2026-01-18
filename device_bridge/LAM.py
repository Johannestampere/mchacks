import os
import json
import base64
import google.generativeai as genai
from controller import screenshot, execute_action, get_screen_size
from PIL import Image
from io import BytesIO

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.0-flash")

SYSTEM_PROMPT = """You are a macOS automation agent. You receive a goal and a screenshot of the current screen state.

Your job is to output the NEXT SINGLE ACTION to take toward completing the goal.

Available actions:
- {"action": "click", "x": int, "y": int}
- {"action": "double_click", "x": int, "y": int}
- {"action": "right_click", "x": int, "y": int}
- {"action": "type_text", "text": "string"}
- {"action": "hotkey", "keys": ["cmd", "space"]}
- {"action": "press", "key": "return"}
- {"action": "scroll", "clicks": int}  # positive=up, negative=down
- {"action": "move_to", "x": int, "y": int}
- {"action": "wait", "seconds": float}
- {"action": "done", "result": "description of what was accomplished"}

Rules:
1. Output ONLY valid JSON, nothing else
2. Output ONE action at a time
3. Use exact pixel coordinates from the screenshot
4. When the goal is complete, output {"action": "done", "result": "..."}
5. If you cannot complete the goal, output {"action": "done", "result": "Failed: reason"}

Common patterns:
- Open Spotlight: {"action": "hotkey", "keys": ["cmd", "space"]}
- Open app: hotkey spotlight, type app name, press return
- Click button: identify coordinates from screenshot, click
- Type in field: click field first, then type_text
"""

def b64_to_pil(b64_string: str) -> Image.Image:
    img_data = base64.b64decode(b64_string)
    return Image.open(BytesIO(img_data))

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

    # Convert screenshot to PIL Image
    current_image = b64_to_pil(screenshot_b64)

    # Build content for Gemini
    content = ["".join(prompt_parts), current_image, "\n\nWhat is the next action? Output ONLY JSON."]

    response = model.generate_content(content)
    content_text = response.text.strip()

    # Parse JSON from response
    try:
        # Handle potential markdown code blocks
        if content_text.startswith("```"):
            content_text = content_text.split("```")[1]
            if content_text.startswith("json"):
                content_text = content_text[4:]
        return json.loads(content_text.strip())
    except json.JSONDecodeError:
        return {"action": "done", "result": f"Failed: Could not parse model response: {content_text}"}

# Execute a goal by repeatedly passing actions
def execute_goal(goal: str, max_steps: int = 20, on_step=None):

    history = []

    for step in range(max_steps):
        # Get current screen state
        current_screenshot = screenshot()

        # Ask model for next action
        action = get_next_action(goal, current_screenshot, history)

        print(f"[STEP {step + 1}] {action}")

        # Notify callback if provided
        if on_step:
            on_step(step + 1, action, current_screenshot)

        # Check if done
        if action.get("action") == "done":
            return {
                "success": "Failed" not in action.get("result", ""),
                "result": action.get("result", "Completed"),
                "steps": step + 1
            }

        # Execute the action
        try:
            result_screenshot = execute_action(action)
            history.append({
                "action": action,
                "screenshot": result_screenshot
            })
        except Exception as e:
            return {
                "success": False,
                "result": f"Action failed: {str(e)}",
                "steps": step + 1
            }

    return {
        "success": False,
        "result": f"Reached max steps ({max_steps}) without completing goal",
        "steps": max_steps
    }