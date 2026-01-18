from dataclasses import dataclass, asdict
from typing import Optional, Literal
import json

###############################################
### WebSocket Messages (Device <-> Backend) ###
###############################################

@dataclass
class DeviceRegistration:
    device_id: str
    device_type: str
    platform: str
    capabilities: list[str]
    type: str = "device_register"

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class StatusUpdate:
    device_id: str
    status: Literal["started", "in_progress", "completed", "failed"]
    message: str = ""
    screenshot: Optional[str] = None
    type: str = "status_update"

    def to_json(self) -> str:
        d = asdict(self)
        if self.screenshot is None:
            del d["screenshot"]
        return json.dumps(d)


@dataclass
class PongMessage:
    device_id: str
    type: str = "pong"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

# Can just be
# { "type": "laptop_task", "goal": "Check code for errors and fix the errors."}
@dataclass
class LaptopTask:
    goal: str
    task_id: Optional[str] = None
    confirmation_text: Optional[str] = None
    type: str = "laptop_task"

    @classmethod
    def from_dict(cls, data: dict) -> "LaptopTask":
        return cls(
            goal=data.get("goal", ""),
            task_id=data.get("task_id"),
            confirmation_text=data.get("confirmation_text"),
        )


###############################################
### LAM actions ###
###############################################

@dataclass
class ClickAction:
    x: int
    y: int
    action: str = "click"


@dataclass
class DoubleClickAction:
    x: int
    y: int
    action: str = "double_click"


@dataclass
class RightClickAction:
    x: int
    y: int
    action: str = "right_click"


@dataclass
class TypeTextAction:
    text: str
    interval: float = 0.02
    action: str = "type_text"


@dataclass
class HotkeyAction:
    keys: list[str]
    action: str = "hotkey"


@dataclass
class PressAction:
    key: str
    action: str = "press"


@dataclass
class ScrollAction:
    clicks: int
    x: Optional[int] = None
    y: Optional[int] = None
    action: str = "scroll"


@dataclass
class MoveToAction:
    x: int
    y: int
    duration: float = 0.2
    action: str = "move_to"


@dataclass
class DragToAction:
    x: int
    y: int
    duration: float = 0.5
    action: str = "drag_to"


@dataclass
class WaitAction:
    seconds: float
    action: str = "wait"


@dataclass
class DoneAction:
    result: str
    action: str = "done"


# === LAM Results ===
###############################################
### LAM results ###
###############################################

@dataclass
class GoalResult:
    success: bool
    result: str
    steps: int


@dataclass
class HistoryEntry:
    action: dict
    screenshot: str