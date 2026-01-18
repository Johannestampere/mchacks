import pyautogui
import base64
from io import BytesIO
import time

# Safety settings
pyautogui.FAILSAFE = True  # Move mouse to corner to abort
pyautogui.PAUSE = 0.1  # Small delay between actions


def click(x: int, y: int):
    pyautogui.click(x, y)


def double_click(x: int, y: int):
    pyautogui.doubleClick(x, y)


def right_click(x: int, y: int):
    pyautogui.rightClick(x, y)


def type_text(text: str, interval: float = 0.02):
    pyautogui.typewrite(text, interval=interval)


def type_unicode(text: str):
    pyautogui.write(text)


def hotkey(*keys):
    pyautogui.hotkey(*keys)


def press(key: str):
    pyautogui.press(key)

# Scroll up (positive) or down (negative).
def scroll(clicks: int, x: int = None, y: int = None):
    pyautogui.scroll(clicks, x=x, y=y)

# Move mouse to coordinates
def move_to(x: int, y: int, duration: float = 0.2):
    pyautogui.moveTo(x, y, duration=duration)

# Drag from current pos to coordinates
def drag_to(x: int, y: int, duration: float = 0.5):
    pyautogui.dragTo(x, y, duration=duration)

def screenshot() -> str:
    img = pyautogui.screenshot()
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=70)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def get_screen_size() -> tuple[int, int]:
    return pyautogui.size()

def get_mouse_position() -> tuple[int, int]:
    return pyautogui.position()

def wait(seconds: float):
    time.sleep(seconds)

ACTION_PARAMS = {
    "click": ["x", "y"],
    "double_click": ["x", "y"],
    "right_click": ["x", "y"],
    "type_text": ["text"],
    "type_unicode": ["text"],
    "hotkey": ["keys"],
    "press": ["key"],
    "scroll": ["clicks"],
    "move_to": ["x", "y"],
    "drag_to": ["x", "y"],
    "wait": ["seconds"],
}

def execute_action(action: dict) -> str:
    action_name = action.get("action")

    if action_name not in ACTION_PARAMS:
        raise ValueError(f"Unknown action: {action_name}")

    if action_name == "click":
        click(action["x"], action["y"])
    elif action_name == "double_click":
        double_click(action["x"], action["y"])
    elif action_name == "right_click":
        right_click(action["x"], action["y"])
    elif action_name == "type_text":
        type_text(action["text"], action.get("interval", 0.02))
    elif action_name == "type_unicode":
        type_unicode(action["text"])
    elif action_name == "hotkey":
        hotkey(*action["keys"])
    elif action_name == "press":
        press(action["key"])
    elif action_name == "scroll":
        scroll(action["clicks"], action.get("x"), action.get("y"))
    elif action_name == "move_to":
        move_to(action["x"], action["y"], action.get("duration", 0.2))
    elif action_name == "drag_to":
        drag_to(action["x"], action["y"], action.get("duration", 0.5))
    elif action_name == "wait":
        wait(action["seconds"])

    return screenshot()