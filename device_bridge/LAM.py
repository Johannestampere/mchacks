import os
import json
import base64
import requests
from dotenv import load_dotenv
from controller import screenshot_for_model, execute_action, get_screen_size, model_to_screen_coords
from data_shapes import GoalResult, HistoryEntry, DoneAction

load_dotenv(".env.local")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

SYSTEM_PROMPT = """You are a macOS automation agent. You see screenshots and execute actions to complete goals.

## Actions
- {"action": "click", "x": int, "y": int}
- {"action": "double_click", "x": int, "y": int}
- {"action": "right_click", "x": int, "y": int}
- {"action": "type_text", "text": "string"}
- {"action": "hotkey", "keys": ["key1", "key2"]}
- {"action": "press", "key": "return|tab|escape|up|down|left|right"}
- {"action": "scroll", "clicks": int} (positive=up, negative=down)
- {"action": "wait", "seconds": float}
- {"action": "done", "result": "what was accomplished"}

## CRITICAL: TASK TYPE RECOGNITION
Determine the task type BEFORE choosing an action:

**TYPE IN CURRENT APP tasks** - Type directly when goal says:
- "type X in [app]", "write X", "enter X"
- Any typing task when an app is already open and focused
→ Just use {"action": "type_text", "text": "..."} - NO Spotlight!

**CLICK/INTERACT tasks** - Use CLICK when user says:
- "click X", "open X", "play X", "select X", "tap X"
- "click the first/second/Nth item"
- "click that video/button/link"
→ LOOK at the screen and CLICK on the visible element. Do NOT search!

**OPEN APP tasks** - Use Spotlight ONLY when:
- Goal explicitly says "open [app name]" AND the app is NOT already visible
- You need to launch a NEW application
→ Use Spotlight Pattern below

**SEARCH tasks** - Use browser search when:
- "search for X", "find X", "look up X", "google X"
→ Use the Search Pattern below

## CRITICAL: NEVER USE SPOTLIGHT FOR TYPING!
If the goal is to TYPE something (code, text, message) into an app that is ALREADY OPEN:
- DO NOT open Spotlight
- DO NOT type into Spotlight
- Just click the text area if needed, then type directly with {"action": "type_text"}

## CRITICAL: RECOGNIZE THE CURRENT APP
Look at the screen and identify what app is in focus:
- IDE/Code editor (VS Code, PyCharm, etc.) = has code, file tree, tabs
- Browser = has URL bar, web content
- Terminal = black/dark background with command prompt
- Text editor = has document content

If the target app is ALREADY OPEN AND VISIBLE, work within it directly!

## CRITICAL: CLICK VISIBLE ELEMENTS - DON'T SEARCH FOR THEM!
If the user asks you to interact with something VISIBLE on screen, CLICK IT directly.
Do NOT open Spotlight or type in search bars to find something already visible.

Examples:
- User: "click the first video" → LOOK at screen, CLICK the video thumbnail
- User: "open that link" → LOOK at screen, CLICK the link
- User: "play the video" → LOOK at screen, CLICK the play button or video thumbnail

## How to Identify Clickable Elements
- **Video thumbnails**: Rectangular images, often with duration timestamps (e.g., "12:34") in corner, or play button overlays
- **Buttons**: Rectangular elements with text labels, often colored or outlined
- **Links**: Text that is underlined, blue/colored, or changes cursor on hover
- **List items**: Repeated similar elements in a column or grid (first item = top-left)
- **Menu options**: Text items in dropdown or sidebar menus

## Clicking Pattern
When asked to click something visible:
1. LOOK at the screenshot carefully
2. IDENTIFY the element (video, button, link, etc.)
3. CLICK at the CENTER of that element: {"action": "click", "x": <center_x>, "y": <center_y>}
4. {"action": "done", "result": "Clicked on [element description]"}

For "click the first video" on YouTube:
- Find the first video thumbnail (usually largest or top-left in the grid)
- Click the CENTER of that thumbnail
- Do NOT type anything, do NOT open Spotlight

## Type in Current App Pattern
When asked to type/write code or text AND an app (IDE, editor, browser) is ALREADY VISIBLE:
1. Look at the screen - is the cursor already in a text area? If yes, just type.
2. If cursor is not in text area, click the text area/editor first
3. Type the text: {"action": "type_text", "text": "your code or text here"}
4. {"action": "done", "result": "Typed [description] in [app name]"}

For "write a for loop in PyCharm" when PyCharm is open:
- DO NOT open Spotlight
- Click in the editor area if needed
- Type: {"action": "type_text", "text": "for i in range(10):\n    print(i)"}
- Done

## Spotlight Pattern (ONLY for opening NEW apps!)
Use this ONLY when you need to launch an app that is NOT already visible:
1. Open Spotlight: {"action": "hotkey", "keys": ["cmd", "space"]}
2. Type app name: {"action": "type_text", "text": "Chrome"}
3. Press return: {"action": "press", "key": "return"}
4. Wait for app to open

## CRITICAL: NO LOOPS - NEVER repeat the same action twice in a row!
Look at your action history. If you already did an action, DO NOT do it again.
- Already opened Spotlight? Don't open it again - type your query or press Return
- Already typed text? Don't type it again - press Return to submit
- Already pressed a hotkey? Move to the NEXT step

## CRITICAL: Read the screen state!
Before each action, look at what's CURRENTLY on screen:
- Is Spotlight already open? (search bar visible in center) → Type or press Return, don't open it again
- Is text already in the search field? → Press Return to search, don't retype
- Is the browser already open? → Use Cmd+L for URL bar, don't reopen Spotlight
- Are search results showing? → You're DONE

## Web Search Pattern (ONLY when user asks to SEARCH the web!)
If browser is NOT open:
1. Open browser via Spotlight: {"action": "hotkey", "keys": ["cmd", "space"]}, type "Chrome", press return
2. Once browser is open: {"action": "hotkey", "keys": ["cmd", "l"]} to focus URL bar
3. Type your search: {"action": "type_text", "text": "garlic bread recipes"}
4. Press return: {"action": "press", "key": "return"}
5. Done when results are visible

If browser IS ALREADY OPEN:
1. {"action": "hotkey", "keys": ["cmd", "l"]} to focus URL bar
2. Type search: {"action": "type_text", "text": "garlic bread recipes"}
3. Press return and done

## State Recognition
- Spotlight = centered search bar with magnifying glass icon
- Browser URL bar = address bar at top of browser window
- If you see your typed text AND search results below = you're done!

## When to Use "done"
Use done when:
- You clicked the requested element
- Search results are visible on screen
- The goal has been achieved
- You've completed all necessary steps

Output ONLY valid JSON. No explanation text.
"""



def get_next_action(goal: str, screenshot_b64: str, meta: dict, history: list[dict] = None) -> dict:

    model_w, model_h = meta["model_w"], meta["model_h"]

    # Build system message with dimensions
    system_content = SYSTEM_PROMPT + f"\n\nThe image you are viewing is EXACTLY {model_w}x{model_h} pixels. All coordinates MUST be within this range (0-{model_w-1} for x, 0-{model_h-1} for y)."

    # Build multi-turn conversation with screenshots after each action
    messages = []

    # First message: goal + initial screenshot (or current if no history)
    if history and len(history) > 0:
        # Show the first screenshot with the goal
        first_entry = history[0]
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"{system_content}\n\nGoal: {goal}\n\nHere is the current screen. What is the next action?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{first_entry['screenshot']}"}}
            ]
        })

        # Add each action and its resulting screenshot
        for i, entry in enumerate(history):
            # Assistant's action
            messages.append({
                "role": "assistant",
                "content": json.dumps(entry['action'])
            })

            # Show the result (next screenshot) - skip last one, we'll show current instead
            if i < len(history) - 1:
                next_entry = history[i + 1]
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Action executed. Here is the result. What is the next action?"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{next_entry['screenshot']}"}}
                    ]
                })

        # Final message: current screenshot after last action
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": "Action executed. Here is the result. What is the next action?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}}
            ]
        })
    else:
        # No history - just show current screenshot with goal
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"{system_content}\n\nGoal: {goal}\n\nHere is the current screen. What is the next action?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}}
            ]
        })

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        },
        json={
            "model": "google/gemini-2.0-flash-001",
            "messages": messages
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

def _actions_equal(a1: dict, a2: dict) -> bool:
    """Check if two actions are effectively the same."""
    if a1.get("action") != a2.get("action"):
        return False
    action_type = a1.get("action")
    if action_type == "hotkey":
        return a1.get("keys") == a2.get("keys")
    if action_type == "type_text":
        return a1.get("text") == a2.get("text")
    if action_type == "press":
        return a1.get("key") == a2.get("key")
    if action_type in ("click", "double_click", "right_click"):
        # Consider clicks within 20px as the same
        return abs(a1.get("x", 0) - a2.get("x", 0)) < 20 and abs(a1.get("y", 0) - a2.get("y", 0)) < 20
    return True


# Execute a goal by repeatedly passing actions
def execute_goal(goal: str, max_steps: int = 20, on_step=None) -> GoalResult:

    history: list[HistoryEntry] = []
    repeat_count = 0

    for step in range(max_steps):
        # Get current screen state with metadata
        current_screenshot, meta = screenshot_for_model()

        # Ask model for next action
        action = get_next_action(
            goal, current_screenshot, meta,
            [{"action": h.action, "screenshot": h.screenshot} for h in history]
        )

        print(f"[STEP {step + 1}] {action}")

        # Loop detection: check if this action was just done
        if history and _actions_equal(action, history[-1].action):
            repeat_count += 1
            print(f"[WARNING] Repeated action detected ({repeat_count}x)")
            if repeat_count >= 2:
                # Force a "press return" if we're stuck on hotkeys/typing
                if action.get("action") in ("hotkey", "type_text"):
                    print("[LOOP BREAK] Forcing press return to unstick")
                    action = {"action": "press", "key": "return"}
                    repeat_count = 0
                elif repeat_count >= 3:
                    # Give up on this loop
                    return GoalResult(
                        success=False,
                        result=f"Got stuck in a loop repeating: {action}",
                        steps=step + 1
                    )
        else:
            repeat_count = 0

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

        # Convert model coordinates to screen coordinates for click/move actions
        action_name = action.get("action")
        if action_name in ["click", "double_click", "right_click", "move_to", "drag_to"]:
            if "x" in action and "y" in action:
                screen_x, screen_y = model_to_screen_coords(action["x"], action["y"], meta)
                action["x"] = screen_x
                action["y"] = screen_y
                action["_coords_converted"] = True

        # Execute the action
        try:
            execute_action(action)
            # Store the screenshot that was shown to the model when it chose this action
            history.append(HistoryEntry(
                action=action,
                screenshot=current_screenshot
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