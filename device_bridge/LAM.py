import os
import json
import base64
import requests
from dotenv import load_dotenv
from controller import screenshot_for_model, execute_action, get_screen_size, model_to_screen_coords
from data_shapes import GoalResult, HistoryEntry, DoneAction

load_dotenv(".env.local")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

SYSTEM_PROMPT = """You are a macOS automation agent. You see a screenshot and must output the NEXT SINGLE ACTION.

IMPORTANT: Carefully read all text labels in the screenshot before acting.

Available actions:
- {"action": "click", "x": int, "y": int}
- {"action": "double_click", "x": int, "y": int}
- {"action": "right_click", "x": int, "y": int}
- {"action": "type_text", "text": "string"}
- {"action": "hotkey", "keys": ["cmd", "space"]}
- {"action": "hotkey", "keys": ["cmd", "tab"]}
- {"action": "hotkey", "keys": ["cmd", "`"]}
- {"action": "press", "key": "return"}
- {"action": "press", "key": "tab"}
- {"action": "press", "key": "escape"}
- {"action": "scroll", "clicks": int}
- {"action": "move_to", "x": int, "y": int}
- {"action": "wait", "seconds": float}
- {"action": "done", "result": "description of what was accomplished"}

CRITICAL RULES:
1. Output ONLY valid JSON, no other text
2. READ the screenshot carefully - identify app names, window titles, labels, and any visible text
3. Coordinates are in screenshot pixels (the image dimensions you see)
4. Do NOT give up early - keep trying until the goal is achieved

CHROME RULES:
- use the shortcut command + option + n to access the searchbar

FOCUS + SAFETY RULES (VERY IMPORTANT):
5. Before typing anything, confirm the correct target is focused.
   - If you intend to type shell commands, you MUST be in Terminal with a visible shell prompt.
6. TERMINAL PROMPT GATING:
   - You may ONLY type shell commands if you can SEE a terminal prompt like: '$', '%', '➜', or a path line.
   - If you do NOT see a terminal prompt, do NOT type commands. Instead, open or focus Terminal first.
7. If you see VS Code, do NOT assume it is a terminal. VS Code editors accept text and will break the demo.
8. If typing ever appears in the wrong place, immediately recover:
   - {"action":"hotkey","keys":["cmd","k"]} to clear Terminal (only if Terminal is focused)
   - or {"action":"hotkey","keys":["cmd","tab"]} to switch apps
   - or {"action":"hotkey","keys":["cmd","`"]} to cycle windows of the current app

TERMINAL MODE (use this whenever the goal involves Terminal commands):
0) If we're already in a terminal, open a new terminal with {"action":"hotkey","keys":["cmd","n"]}
A) Ensure Terminal is active:
   - If Terminal is not visible, open it via Spotlight:
     1. {"action":"hotkey","keys":["cmd","space"]}
     2. {"action":"wait","seconds":0.2}
     3. {"action":"type_text","text":"Terminal"}
     4. {"action":"press","key":"return"}
     5. {"action":"wait","seconds":1.0}
   - If Terminal is visible but not focused, click inside the Terminal input line area OR use Cmd+Tab to focus it.
B) Confirm prompt is visible (must see '$', '%', '➜', etc.). If not visible, do NOT type commands; keep focusing Terminal.
C) Clear screen to remove noise:
   - {"action":"hotkey","keys":["cmd","k"]}
   - {"action":"wait","seconds":0.2}
D) Run ONE command at a time:
   - type the command, press Return, then wait 0.4 seconds.
E) Use python3 only (never use python).

RELIABLE TERMINAL EDITING RULES:
- Use `nano` (NOT vim).
- Save nano: Ctrl+O, Return. Exit: Ctrl+X.
- Use absolute Desktop path: `cd ~/Desktop` (NOT `cd Desktop`).

COMMON TERMINAL CHECKS:
- After cd: run `pwd`
- To verify file: run `ls`
- To run: `python3 code.py`

If you need to open a file from Desktop using GUI:
- Only do so if the goal explicitly requires GUI. For terminal tasks, stay in Terminal Mode.

Now output the next single action as JSON.
"""



def get_next_action(goal: str, screenshot_b64: str, meta: dict, history: list[dict] = None) -> dict:

    model_w, model_h = meta["model_w"], meta["model_h"]

    # Build prompt with history context - tell model EXACT image dimensions
    prompt_parts = [SYSTEM_PROMPT + f"\n\nThe image you are viewing is EXACTLY {model_w}x{model_h} pixels. All coordinates MUST be within this range (0-{model_w-1} for x, 0-{model_h-1} for y).\n\n"]

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
        # Get current screen state with metadata
        current_screenshot, meta = screenshot_for_model()

        # Ask model for next action
        action = get_next_action(goal, current_screenshot, meta, [{"action": h.action, "screenshot": h.screenshot} for h in history])

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

        # Convert model coordinates to screen coordinates for click actions
        action_name = action.get("action")
        if action_name in ["click", "double_click", "right_click", "move_to", "drag_to"]:
            if "x" in action and "y" in action:
                screen_x, screen_y = model_to_screen_coords(action["x"], action["y"], meta)
                action["x"] = screen_x
                action["y"] = screen_y
                action["_coords_converted"] = True  # Mark as already converted

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