import pyautogui
import base64
from io import BytesIO
import time
import subprocess

# Safety settings
pyautogui.FAILSAFE = True  # Move mouse to corner to abort
pyautogui.PAUSE = 0.1  # Small delay between actions

# Calculate Retina scale factor
def get_scale_factor():
    screen_size = pyautogui.size()
    img = pyautogui.screenshot()
    scale_x = img.size[0] / screen_size[0]
    scale_y = img.size[1] / screen_size[1]
    return scale_x, scale_y

# Cache scale factor
_SCALE_FACTOR = None

def scale_coords(x: int, y: int) -> tuple[int, int]:
    global _SCALE_FACTOR
    if _SCALE_FACTOR is None:
        _SCALE_FACTOR = get_scale_factor()
    scale_x, scale_y = _SCALE_FACTOR
    return int(x / scale_x), int(y / scale_y)


def click(x: int, y: int):
    scaled_x, scaled_y = scale_coords(x, y)
    print(f"[DEBUG] click: image coords ({x}, {y}) -> screen coords ({scaled_x}, {scaled_y})")
    # Use AppleScript for reliable clicking on macOS
    cmd = f'tell application "System Events" to click at {{{scaled_x}, {scaled_y}}}'
    result = subprocess.run(['osascript', '-e', cmd], capture_output=True, text=True)
    if result.returncode != 0:
        # Fallback to cliclick if available, or use Quartz
        try:
            from Quartz.CoreGraphics import CGEventCreateMouseEvent, CGEventPost, kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGHIDEventTap, kCGMouseButtonLeft
            event = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (scaled_x, scaled_y), kCGMouseButtonLeft)
            CGEventPost(kCGHIDEventTap, event)
            event = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (scaled_x, scaled_y), kCGMouseButtonLeft)
            CGEventPost(kCGHIDEventTap, event)
            print(f"[DEBUG] click: used Quartz")
        except ImportError:
            pyautogui.click(scaled_x, scaled_y)
            print(f"[DEBUG] click: used pyautogui fallback")
    else:
        print(f"[DEBUG] click: used AppleScript")
    print(f"[DEBUG] click: done")


def double_click(x: int, y: int):
    scaled_x, scaled_y = scale_coords(x, y)
    print(f"[DEBUG] double_click: image coords ({x}, {y}) -> screen coords ({scaled_x}, {scaled_y})")
    # Use Quartz for reliable double-clicking on macOS
    try:
        from Quartz.CoreGraphics import CGEventCreateMouseEvent, CGEventPost, CGEventSetIntegerValueField, kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGHIDEventTap, kCGMouseButtonLeft, kCGMouseEventClickState
        import time as t
        # First click
        event = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (scaled_x, scaled_y), kCGMouseButtonLeft)
        CGEventSetIntegerValueField(event, kCGMouseEventClickState, 1)
        CGEventPost(kCGHIDEventTap, event)
        event = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (scaled_x, scaled_y), kCGMouseButtonLeft)
        CGEventSetIntegerValueField(event, kCGMouseEventClickState, 1)
        CGEventPost(kCGHIDEventTap, event)
        t.sleep(0.05)
        # Second click
        event = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (scaled_x, scaled_y), kCGMouseButtonLeft)
        CGEventSetIntegerValueField(event, kCGMouseEventClickState, 2)
        CGEventPost(kCGHIDEventTap, event)
        event = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (scaled_x, scaled_y), kCGMouseButtonLeft)
        CGEventSetIntegerValueField(event, kCGMouseEventClickState, 2)
        CGEventPost(kCGHIDEventTap, event)
        print(f"[DEBUG] double_click: used Quartz")
    except ImportError:
        pyautogui.doubleClick(scaled_x, scaled_y)
        print(f"[DEBUG] double_click: used pyautogui fallback")
    print(f"[DEBUG] double_click: done")


def right_click(x: int, y: int):
    scaled_x, scaled_y = scale_coords(x, y)
    print(f"[DEBUG] right_click: image coords ({x}, {y}) -> screen coords ({scaled_x}, {scaled_y})")
    try:
        from Quartz.CoreGraphics import CGEventCreateMouseEvent, CGEventPost, kCGEventRightMouseDown, kCGEventRightMouseUp, kCGHIDEventTap, kCGMouseButtonRight
        event = CGEventCreateMouseEvent(None, kCGEventRightMouseDown, (scaled_x, scaled_y), kCGMouseButtonRight)
        CGEventPost(kCGHIDEventTap, event)
        event = CGEventCreateMouseEvent(None, kCGEventRightMouseUp, (scaled_x, scaled_y), kCGMouseButtonRight)
        CGEventPost(kCGHIDEventTap, event)
        print(f"[DEBUG] right_click: used Quartz")
    except ImportError:
        pyautogui.rightClick(scaled_x, scaled_y)
        print(f"[DEBUG] right_click: used pyautogui fallback")
    print(f"[DEBUG] right_click: done")


def type_text(text: str, interval: float = 0.02):
    # Use AppleScript for reliable typing on macOS
    escaped_text = text.replace('\\', '\\\\').replace('"', '\\"')
    cmd = f'tell application "System Events" to keystroke "{escaped_text}"'
    print(f"[DEBUG] type_text: executing osascript with: {cmd}")
    result = subprocess.run(['osascript', '-e', cmd], capture_output=True, text=True)
    print(f"[DEBUG] type_text: stdout={result.stdout}, stderr={result.stderr}, returncode={result.returncode}")


def type_unicode(text: str):
    escaped_text = text.replace('\\', '\\\\').replace('"', '\\"')
    subprocess.run([
        'osascript', '-e',
        f'tell application "System Events" to keystroke "{escaped_text}"'
    ], check=True)


KEY_MAP = {
    'cmd': 'command',
    'ctrl': 'control',
    'alt': 'option',
    'return': 'return',
    'enter': 'return',
    'esc': 'escape',
    'escape': 'escape',
    'tab': 'tab',
    'space': 'space',
    'delete': 'delete',
    'backspace': 'delete',
    'up': 'up arrow',
    'down': 'down arrow',
    'left': 'left arrow',
    'right': 'right arrow',
}

def hotkey(*keys):
    print(f"[DEBUG] hotkey: pressing {keys}")
    # Build AppleScript for key combo
    modifiers = []
    main_key = None

    for key in keys:
        key_lower = key.lower()
        if key_lower in ['cmd', 'command']:
            modifiers.append('command down')
        elif key_lower in ['ctrl', 'control']:
            modifiers.append('control down')
        elif key_lower in ['alt', 'option']:
            modifiers.append('option down')
        elif key_lower in ['shift']:
            modifiers.append('shift down')
        else:
            main_key = KEY_MAP.get(key_lower, key_lower)

    if main_key:
        modifier_str = ', '.join(modifiers)
        if modifier_str:
            cmd = f'tell application "System Events" to key code (key code of "{main_key}") using {{{modifier_str}}}'
            # Use keystroke for single characters
            if len(main_key) == 1:
                cmd = f'tell application "System Events" to keystroke "{main_key}" using {{{modifier_str}}}'
            else:
                # Use key code for special keys
                key_code_map = {'space': 49, 'return': 36, 'escape': 53, 'tab': 48, 'delete': 51}
                if main_key in key_code_map:
                    cmd = f'tell application "System Events" to key code {key_code_map[main_key]} using {{{modifier_str}}}'
                else:
                    cmd = f'tell application "System Events" to keystroke "{main_key}" using {{{modifier_str}}}'
        else:
            cmd = f'tell application "System Events" to keystroke "{main_key}"'

        print(f"[DEBUG] hotkey: executing: {cmd}")
        result = subprocess.run(['osascript', '-e', cmd], capture_output=True, text=True)
        print(f"[DEBUG] hotkey: stdout={result.stdout}, stderr={result.stderr}, returncode={result.returncode}")
    print(f"[DEBUG] hotkey: done")


def press(key: str):
    print(f"[DEBUG] press: pressing {key}")
    key_lower = key.lower()
    key_code_map = {
        'return': 36, 'enter': 36, 'escape': 53, 'esc': 53,
        'tab': 48, 'space': 49, 'delete': 51, 'backspace': 51,
        'up': 126, 'down': 125, 'left': 123, 'right': 124,
        'f1': 122, 'f2': 120, 'f3': 99, 'f4': 118, 'f5': 96,
        'f6': 97, 'f7': 98, 'f8': 100, 'f9': 101, 'f10': 109,
        'f11': 103, 'f12': 111,
    }

    if key_lower in key_code_map:
        cmd = f'tell application "System Events" to key code {key_code_map[key_lower]}'
    else:
        cmd = f'tell application "System Events" to keystroke "{key}"'

    print(f"[DEBUG] press: executing: {cmd}")
    result = subprocess.run(['osascript', '-e', cmd], capture_output=True, text=True)
    print(f"[DEBUG] press: stdout={result.stdout}, stderr={result.stderr}, returncode={result.returncode}")
    print(f"[DEBUG] press: done")

# Scroll up (positive) or down (negative).
def scroll(clicks: int, x: int = None, y: int = None):
    if x is not None and y is not None:
        scaled_x, scaled_y = scale_coords(x, y)
        pyautogui.scroll(clicks, x=scaled_x, y=scaled_y)
    else:
        pyautogui.scroll(clicks)

# Move mouse to coordinates
def move_to(x: int, y: int, duration: float = 0.2):
    scaled_x, scaled_y = scale_coords(x, y)
    pyautogui.moveTo(scaled_x, scaled_y, duration=duration)

# Drag from current pos to coordinates
def drag_to(x: int, y: int, duration: float = 0.5):
    scaled_x, scaled_y = scale_coords(x, y)
    pyautogui.dragTo(scaled_x, scaled_y, duration=duration)

def screenshot() -> str:
    print(f"[DEBUG] screenshot: capturing...")
    img = pyautogui.screenshot()
    print(f"[DEBUG] screenshot: captured image mode={img.mode}, size={img.size}")
    if img.mode == "RGBA":
        img = img.convert("RGB")
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=70)
    b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    print(f"[DEBUG] screenshot: encoded, length={len(b64)}")
    return b64

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