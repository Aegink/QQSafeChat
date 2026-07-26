
from __future__ import annotations
import re
import time
from typing import List, Optional
import uiautomation as auto
from core.models import ExtractedMessage

TIME_HHMM = re.compile(r"^\d{1,2}:\d{2}$")

DATE_TIME = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}\s+\d{1,2}:\d{2}$")
DATE_ONLY = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$")

def _is_system_time_line(s: str) -> bool:
    s = s.strip()
    return bool(TIME_HHMM.match(s) or DATE_TIME.match(s) or DATE_ONLY.match(s))


def _classify_sender(rect, list_rect) -> str:
    list_width = max(1, int(list_rect.right - list_rect.left))
    list_center_x = (list_rect.left + list_rect.right) / 2.0

    left_gap = int(rect.left - list_rect.left)
    right_gap = int(list_rect.right - rect.right)
    margin_tolerance = max(24, int(list_width * 0.08))

    if left_gap + margin_tolerance < right_gap:
        return "other"
    if right_gap + margin_tolerance < left_gap:
        return "self"

    center_x = (rect.left + rect.right) / 2.0
    return "other" if center_x < list_center_x else "self"


def _is_near_duplicate(prev: ExtractedMessage, cur: ExtractedMessage) -> bool:
    if prev.sender != cur.sender or prev.msg_type != cur.msg_type:
        return False
    if (prev.text or "") != (cur.text or ""):
        return False

    return (
        abs(prev.top - cur.top) <= 8
        and abs(prev.left - cur.left) <= 8
        and abs(prev.right - cur.right) <= 8
        and abs(prev.bottom - cur.bottom) <= 8
    )

def extract_messages(window_ctrl, list_rect, bubble_merge_gap_px: int = 26) -> List[ExtractedMessage]:
    raw: List[ExtractedMessage] = []

    def append_message(text: str, rect, sender: str, msg_type: str, sender_name: Optional[str]):
        raw.append(
            ExtractedMessage(
                sender=sender,
                text=text,
                top=int(rect.top),
                left=int(rect.left),
                right=int(rect.right),
                bottom=int(rect.bottom),
                debug_sender_name=sender_name,
                msg_type=msg_type,
                ts=time.time(),
            )
        )

    def dfs(ctrl, current_sender_name: Optional[str] = None):
        try:
            ctype = ctrl.ControlTypeName
        except Exception:
            return

        
        next_sender_name = current_sender_name
        try:
            if ctype == "GroupControl":
                n = (ctrl.Name or "").strip()
                if n:
                    next_sender_name = n
        except Exception:
            pass

        if ctype == "TextControl":
            try:
                text = (ctrl.Name or "").strip()
                if text:
                    r = ctrl.BoundingRectangle
                    sender = _classify_sender(r, list_rect)
                    msg_type = "system_time" if _is_system_time_line(text) else "text"
                    append_message(text, r, sender, msg_type, next_sender_name)
            except Exception:
                pass

        if ctype == "ImageControl":
            try:
                r = ctrl.BoundingRectangle
                width = int(r.right - r.left)
                height = int(r.bottom - r.top)
                if width >= 40 and height >= 40:
                    sender = _classify_sender(r, list_rect)
                    append_message("[图片]", r, sender, "image", next_sender_name)
            except Exception:
                pass

        
        try:
            for child in ctrl.GetChildren():
                dfs(child, next_sender_name)
        except Exception:
            pass

    dfs(window_ctrl, None)

    
    raw.sort(key=lambda m: (m.top, m.left, m.right, m.bottom))

    deduped: List[ExtractedMessage] = []
    for m in raw:
        if deduped and _is_near_duplicate(deduped[-1], m):
            continue
        deduped.append(m)

    
    raw2 = [m for m in deduped if m.msg_type != "system_time"]

    
    merged: List[ExtractedMessage] = []
    for m in raw2:
        if not merged:
            merged.append(m)
            continue
        prev = merged[-1]
        if (
            m.sender == prev.sender
            and m.msg_type == "text"
            and prev.msg_type == "text"
            and abs(m.top - prev.top) <= bubble_merge_gap_px
        ):
            
            prev.text = prev.text + "\n" + m.text
            prev.top = min(prev.top, m.top)
            prev.left = min(prev.left, m.left)
            prev.right = max(prev.right, m.right)
            prev.bottom = max(prev.bottom, m.bottom)
            if prev.ts is None:
                prev.ts = m.ts
        else:
            merged.append(m)

    return merged
