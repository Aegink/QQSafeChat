
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Any

Sender = Literal["self", "other", "unknown"]
MsgType = Literal["text", "image", "system_time", "unknown"]
ActionType = Literal["text", "sticker", "voice"]

@dataclass
class BoundControl:
    expected_type: str
    center_x: int
    center_y: int
    actual_type: str = ""
    name: str = ""
    framework: str = ""
    automation_id: str = ""
    class_name: str = ""
    root_hwnd: int = 0
    native_hwnd: int = 0
    process_id: int = 0
    rel_x: float = 0.5
    rel_y: float = 0.5

@dataclass
class UiNodeText:
    text: str
    left: int
    top: int
    right: int
    bottom: int

@dataclass
class ExtractedMessage:
    sender: Sender
    text: str
    top: int
    left: int
    right: int
    bottom: int = 0
    
    debug_sender_name: Optional[str] = None
    msg_type: MsgType = "text"
    ts: float | None = None
    image_path: str = ""
    image_summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplyAction:
    action_type: ActionType
    text: str = ""
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    