from __future__ import annotations

import ctypes
from ctypes import wintypes
import win32api
import win32gui
import win32con
import uiautomation as auto
from typing import Optional
from core.models import BoundControl


EDIT_COMPAT_CONTROL_TYPES = ("EditControl", "GroupControl")


def _control_type_candidates(expected_type: str) -> tuple[str, ...]:
    if expected_type in EDIT_COMPAT_CONTROL_TYPES:
        return EDIT_COMPAT_CONTROL_TYPES
    return (expected_type,)


def _matches_control_type(ctrl: Optional[auto.Control], expected_type: str) -> bool:
    if not ctrl:
        return False
    try:
        return (getattr(ctrl, "ControlTypeName", "") or "") in _control_type_candidates(
            expected_type
        )
    except Exception:
        return False


def _safe_text(ctrl, attr: str) -> str:
    try:
        return getattr(ctrl, attr, "") or ""
    except Exception:
        return ""


def _safe_rect(ctrl) -> Optional[tuple[int, int, int, int, int, int]]:
    try:
        rect = ctrl.BoundingRectangle
        left = int(rect.left)
        top = int(rect.top)
        right = int(rect.right)
        bottom = int(rect.bottom)
    except Exception:
        return None

    width = max(0, right - left)
    height = max(0, bottom - top)
    if width <= 1 or height <= 1:
        return None
    return left, top, right, bottom, width, height


def _enum_descendant_hwnds(root_hwnd: int) -> list[int]:
    handles: list[int] = []
    if not root_hwnd:
        return handles

    def _callback(hwnd, _lparam):
        try:
            handles.append(int(hwnd))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(root_hwnd, _callback, 0)
    except Exception:
        return handles
    return handles


def pick_chat_bind_root(window_hwnd: int):
    best: tuple[float, auto.Control] | None = None
    for hwnd in _enum_descendant_hwnds(window_hwnd):
        try:
            cls = win32gui.GetClassName(hwnd) or ""
        except Exception:
            cls = ""
        if cls != "Chrome_RenderWidgetHostHWND":
            continue

        try:
            ctrl = auto.ControlFromHandle(hwnd)
        except Exception:
            continue

        if _safe_text(ctrl, "ControlTypeName") != "DocumentControl":
            continue

        rect = _safe_rect(ctrl)
        if not rect:
            continue
        _, _, _, _, width, height = rect
        score = float(width * height)
        value = _safe_text(ctrl, "Value").lower()
        if "#/chat" in value or "chatpoolwin" in value:
            score += 1_000_000.0

        if best is None or score > best[0]:
            best = (score, ctrl)

    if best:
        return best[1]

    try:
        return auto.ControlFromHandle(window_hwnd)
    except Exception:
        return None


def _walk_controls(root_ctrl, max_depth: int = 8, max_nodes: int = 2500) -> list[dict]:
    nodes: list[dict] = []

    def rec(ctrl, depth: int):
        if not ctrl or depth > max_depth or len(nodes) >= max_nodes:
            return

        nodes.append(
            {
                "ctrl": ctrl,
                "depth": depth,
                "type": _safe_text(ctrl, "ControlTypeName"),
                "name": _safe_text(ctrl, "Name"),
                "aid": _safe_text(ctrl, "AutomationId"),
                "class": _safe_text(ctrl, "ClassName"),
                "rect": _safe_rect(ctrl),
            }
        )
        if len(nodes) >= max_nodes:
            return

        try:
            children = ctrl.GetChildren()
        except Exception:
            return

        for child in children:
            rec(child, depth + 1)
            if len(nodes) >= max_nodes:
                break

    rec(root_ctrl, 0)
    return nodes


def _node_blob(node: dict) -> str:
    return " ".join([node.get("name", ""), node.get("aid", ""), node.get("class", "")]).lower()


def _normalize_text(text: str) -> str:
    return (text or "").strip().lower()


def _is_exact_send_name(name: str) -> bool:
    return _normalize_text(name) in ("发送", "send")


def _is_exact_voice_button_name(name: str) -> bool:
    return _normalize_text(name) in ("语音消息", "voice message")


def _is_dangerous_button_blob(blob: str) -> bool:
    return any(
        k in blob
        for k in ("关闭", "close", "最小化", "minimize", "最大化", "maximize", "还原", "restore")
    )


def _pick_input_candidate(nodes: list[dict], root_rect) -> Optional[dict]:
    root_left, root_top, _, _, root_w, root_h = root_rect
    root_area = max(1, root_w * root_h)
    root_cx = root_left + (root_w / 2.0)

    best: tuple[float, dict] | None = None
    for node in nodes:
        rect = node.get("rect")
        if not rect or node.get("type") not in EDIT_COMPAT_CONTROL_TYPES:
            continue

        left, top, right, bottom, width, height = rect
        if width < 120 or height < 20:
            continue

        center_x = (left + right) / 2.0
        blob = _node_blob(node)
        score = 0.0
        score += ((bottom - root_top) / max(root_h, 1)) * 900.0
        score += min(width / max(root_w, 1), 1.4) * 700.0
        score += (width * height / root_area) * 350.0
        score += max(0.0, 1.0 - abs(center_x - root_cx) / max(root_w, 1)) * 240.0

        if top < root_top + root_h * 0.45:
            score -= 420.0
        if width < root_w * 0.28:
            score -= 320.0
        if "search" in blob or "搜索" in blob or "查找" in blob:
            score -= 480.0
        if "输入" in blob or "message" in blob or "chat" in blob or "msg" in blob:
            score += 120.0

        if best is None or score > best[0]:
            best = (score, node)

    return best[1] if best else None


def _pick_button_candidate(nodes: list[dict], root_rect, input_node: Optional[dict]) -> Optional[dict]:
    root_left, root_top, _, _, root_w, root_h = root_rect
    input_rect = input_node.get("rect") if input_node else None
    title_bar_bottom = root_top + max(48, int(root_h * 0.16))

    exact_best: tuple[float, dict] | None = None
    best: tuple[float, dict] | None = None
    for node in nodes:
        rect = node.get("rect")
        if not rect or node.get("type") != "ButtonControl":
            continue

        left, top, right, bottom, width, height = rect
        if width < 18 or height < 18:
            continue

        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0
        node_name = _normalize_text(node.get("name", "") or "")
        blob = _node_blob(node)
        is_exact_send = _is_exact_send_name(node_name)
        is_send_like = ("发送" in blob) or ("send" in blob)

        score = 0.0
        score += ((left - root_left) / max(root_w, 1)) * 260.0
        score += ((bottom - root_top) / max(root_h, 1)) * 220.0

        if is_send_like:
            score += 720.0

        if _is_dangerous_button_blob(blob):
            score -= 2400.0
        if any(k in blob for k in ("搜索", "search", "设置", "setting", "菜单", "menu", "更多", "more")):
            score -= 640.0

        if top <= title_bar_bottom and not is_send_like:
            score -= 1800.0
        if width <= 42 and height <= 42 and not is_send_like:
            score -= 420.0

        if input_rect:
            in_left, in_top, in_right, in_bottom, _, _ = input_rect
            input_cx = (in_left + in_right) / 2.0
            input_cy = (in_top + in_bottom) / 2.0

            if left >= in_right - 40:
                score += 320.0
            elif center_x > input_cx:
                score += 180.0

            score += max(0.0, 220.0 - abs(center_y - input_cy) * 4.0)
            if top >= in_top - 40:
                score += 80.0
            if center_y < in_top - 60 and not is_send_like:
                score -= 900.0
            if center_y > in_bottom + 120 and not is_send_like:
                score -= 260.0
            if left < in_left - 120 and not is_send_like:
                score -= 320.0
            if right > in_right + 260 and not is_send_like:
                score -= 180.0

        if is_exact_send:
            score += 2400.0
            if exact_best is None or score > exact_best[0]:
                exact_best = (score, node)

        if best is None or score > best[0]:
            best = (score, node)

    if exact_best:
        return exact_best[1]

    return best[1] if best else None


def _pick_named_button_candidate(
    nodes: list[dict], root_rect, name_matcher, prefer_left: bool = False
) -> Optional[dict]:
    root_left, root_top, _, _, root_w, root_h = root_rect
    best: tuple[float, dict] | None = None
    for node in nodes:
        rect = node.get("rect")
        if not rect or node.get("type") != "ButtonControl":
            continue
        if not name_matcher(node.get("name", "") or ""):
            continue

        left, top, right, bottom, width, height = rect
        if width < 18 or height < 18:
            continue

        score = 0.0
        score += ((bottom - root_top) / max(root_h, 1)) * 420.0
        if prefer_left:
            score += max(0.0, 1.0 - ((left - root_left) / max(root_w, 1))) * 260.0
        else:
            score += ((left - root_left) / max(root_w, 1)) * 120.0
        score += min(width * height, 4000) * 0.08

        if best is None or score > best[0]:
            best = (score, node)

    return best[1] if best else None


def _horizontal_overlap(rect1, rect2) -> int:
    return max(0, min(rect1[2], rect2[2]) - max(rect1[0], rect2[0]))


def _pick_message_container(
    nodes: list[dict], root_ctrl, root_rect, input_node: Optional[dict]
) -> Optional[dict]:
    _, root_top, _, _, root_w, root_h = root_rect
    root_area = max(1, root_w * root_h)
    input_rect = input_node.get("rect") if input_node else None
    candidate_types = {"WindowControl", "PaneControl", "ListControl", "CustomControl"}

    best: tuple[float, dict] | None = None
    for node in nodes:
        rect = node.get("rect")
        if not rect or node.get("type") not in candidate_types:
            continue

        ctrl = node.get("ctrl")
        if ctrl is root_ctrl:
            continue

        left, top, right, bottom, width, height = rect
        if width < root_w * 0.28 or height < root_h * 0.18:
            continue

        score = 0.0
        score += (width * height / root_area) * 1100.0
        score += min(width / max(root_w, 1), 1.2) * 280.0
        score += min(height / max(root_h, 1), 1.2) * 240.0

        if input_rect:
            in_left, in_top, in_right, _, in_w, _ = input_rect
            if top >= in_top - 20:
                continue
            if bottom > in_top + 40:
                score -= 360.0
            overlap_ratio = _horizontal_overlap(rect, input_rect) / max(in_w, 1)
            score += overlap_ratio * 420.0
            if left <= in_left + 40:
                score += 60.0
            if right >= in_right - 80:
                score += 60.0
        else:
            score += ((bottom - root_top) / max(root_h, 1)) * 120.0

        if best is None or score > best[0]:
            best = (score, node)

    if best:
        return best[1]

    return {"ctrl": root_ctrl, "rect": root_rect, "type": _safe_text(root_ctrl, "ControlTypeName")}


def auto_detect_chat_bindings(root_ctrl) -> dict[str, Optional[auto.Control]]:
    root_rect = _safe_rect(root_ctrl)
    if not root_rect:
        return {"edit": None, "button": None, "voice_button": None, "window": None}

    nodes = _walk_controls(root_ctrl, max_depth=16, max_nodes=6000)
    input_node = _pick_input_candidate(nodes, root_rect)
    button_node = _pick_button_candidate(nodes, root_rect, input_node)
    voice_button_node = _pick_named_button_candidate(
        nodes, root_rect, _is_exact_voice_button_name, prefer_left=True
    )
    window_node = _pick_message_container(nodes, root_ctrl, root_rect, input_node)

    return {
        "edit": input_node.get("ctrl") if input_node else None,
        "button": button_node.get("ctrl") if button_node else None,
        "voice_button": voice_button_node.get("ctrl") if voice_button_node else None,
        "window": window_node.get("ctrl") if window_node else None,
    }

gdiplus = ctypes.WinDLL("gdiplus")
gdi32 = ctypes.WinDLL("gdi32")
user32 = ctypes.WinDLL("user32")
Ok = 0
SmoothingModeAntiAlias = 4
TextRenderingHintAntiAliasGridFit = 3
UnitPixel = 2
FontStyleRegular = 0

StringAlignmentNear = 0
StringAlignmentCenter = 1

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

DIB_RGB_COLORS = 0


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", wintypes.UINT),
        ("DebugEventCallback", wintypes.LPVOID),
        ("SuppressBackgroundThread", wintypes.BOOL),
        ("SuppressExternalCodecs", wintypes.BOOL),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class RECTF(ctypes.Structure):
    _fields_ = [
        ("X", ctypes.c_float),
        ("Y", ctypes.c_float),
        ("Width", ctypes.c_float),
        ("Height", ctypes.c_float),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


BI_RGB = 0


def _check(status: int, where: str):
    if status != Ok:
        raise RuntimeError(f"GDI+ call failed ({where}): status={status}")


# Function prototypes (only what we use)
gdiplus.GdiplusStartup.argtypes = [
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(GdiplusStartupInput),
    wintypes.LPVOID,
]
gdiplus.GdiplusStartup.restype = ctypes.c_int
gdiplus.GdiplusShutdown.argtypes = [ctypes.c_void_p]
gdiplus.GdiplusShutdown.restype = None

gdiplus.GdipCreateFromHDC.argtypes = [wintypes.HDC, ctypes.POINTER(ctypes.c_void_p)]
gdiplus.GdipCreateFromHDC.restype = ctypes.c_int
gdiplus.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
gdiplus.GdipDeleteGraphics.restype = ctypes.c_int

gdiplus.GdipSetSmoothingMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdiplus.GdipSetSmoothingMode.restype = ctypes.c_int
gdiplus.GdipSetTextRenderingHint.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdiplus.GdipSetTextRenderingHint.restype = ctypes.c_int
gdiplus.GdipGraphicsClear.argtypes = [ctypes.c_void_p, wintypes.DWORD]
gdiplus.GdipGraphicsClear.restype = ctypes.c_int

gdiplus.GdipCreatePen1.argtypes = [
    wintypes.DWORD,
    ctypes.c_float,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p),
]
gdiplus.GdipCreatePen1.restype = ctypes.c_int
gdiplus.GdipDeletePen.argtypes = [ctypes.c_void_p]
gdiplus.GdipDeletePen.restype = ctypes.c_int
gdiplus.GdipDrawRectangle.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_float,
    ctypes.c_float,
    ctypes.c_float,
    ctypes.c_float,
]
gdiplus.GdipDrawRectangle.restype = ctypes.c_int

gdiplus.GdipCreateSolidFill.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
gdiplus.GdipCreateSolidFill.restype = ctypes.c_int
gdiplus.GdipDeleteBrush.argtypes = [ctypes.c_void_p]
gdiplus.GdipDeleteBrush.restype = ctypes.c_int

gdiplus.GdipCreatePath.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
gdiplus.GdipCreatePath.restype = ctypes.c_int
gdiplus.GdipDeletePath.argtypes = [ctypes.c_void_p]
gdiplus.GdipDeletePath.restype = ctypes.c_int

gdiplus.GdipCreateFontFamilyFromName.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPVOID,
    ctypes.POINTER(ctypes.c_void_p),
]
gdiplus.GdipCreateFontFamilyFromName.restype = ctypes.c_int
gdiplus.GdipDeleteFontFamily.argtypes = [ctypes.c_void_p]
gdiplus.GdipDeleteFontFamily.restype = ctypes.c_int

gdiplus.GdipCreateStringFormat.argtypes = [
    ctypes.c_int,
    wintypes.LANGID,
    ctypes.POINTER(ctypes.c_void_p),
]
gdiplus.GdipCreateStringFormat.restype = ctypes.c_int
gdiplus.GdipDeleteStringFormat.argtypes = [ctypes.c_void_p]
gdiplus.GdipDeleteStringFormat.restype = ctypes.c_int
gdiplus.GdipSetStringFormatAlign.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdiplus.GdipSetStringFormatAlign.restype = ctypes.c_int
gdiplus.GdipSetStringFormatLineAlign.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdiplus.GdipSetStringFormatLineAlign.restype = ctypes.c_int

gdiplus.GdipAddPathString.argtypes = [
    ctypes.c_void_p,  # path
    wintypes.LPCWSTR,  # string
    ctypes.c_int,  # length
    ctypes.c_void_p,  # fontFamily
    ctypes.c_int,  # style
    ctypes.c_float,  # emSize
    ctypes.POINTER(RECTF),  # layoutRect
    ctypes.c_void_p,  # stringFormat
]
gdiplus.GdipAddPathString.restype = ctypes.c_int

gdiplus.GdipFillPath.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
gdiplus.GdipFillPath.restype = ctypes.c_int
gdiplus.GdipDrawPath.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
gdiplus.GdipDrawPath.restype = ctypes.c_int


def _argb(a: int, r: int, g: int, b: int) -> int:
    return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


class _GDIPlusRuntime:
    _token: ctypes.c_void_p | None = None

    @classmethod
    def ensure(cls):
        if cls._token is not None:
            return
        token = ctypes.c_void_p()
        startup = GdiplusStartupInput(1, None, False, False)
        status = gdiplus.GdiplusStartup(
            ctypes.byref(token), ctypes.byref(startup), None
        )
        _check(status, "GdiplusStartup")
        cls._token = token

    @classmethod
    def shutdown(cls):
        if cls._token is not None:
            gdiplus.GdiplusShutdown(cls._token)
            cls._token = None


class HighlightRect:

    _BORDER_RGB = (255, 255, 0)  # 这是最好的颜色 最好不要乱改
    _BORDER_WIDTH = 3.0
    _TEXT_STROKE = 0

    def __init__(self):
        _GDIPlusRuntime.ensure()

        self._label: str = ""
        self._last_rect_key: tuple[int, int, int, int] | None = None

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self.wnd_proc
        wc.lpszClassName = "HighlightRect_UiaChatbot"
        wc.hInstance = win32api.GetModuleHandle(None)

        try:
            self.class_atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            self.class_atom = win32gui.GetClassInfo(wc.hInstance, wc.lpszClassName)[0]

        self.hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOPMOST
            | win32con.WS_EX_TOOLWINDOW,
            self.class_atom,
            None,
            win32con.WS_POPUP,
            0,
            0,
            0,
            0,
            0,
            0,
            wc.hInstance,
            None,
        )
        win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)

    def wnd_proc(self, hwnd, msg, w, l):
        if msg == win32con.WM_NCHITTEST:
            return win32con.HTTRANSPARENT
        return win32gui.DefWindowProc(hwnd, msg, w, l)

    def _label_from_ctrl(self, ctrl: Optional[auto.Control]) -> str:
        if not ctrl:
            return ""
        try:
            return getattr(ctrl, "ControlTypeName", "") or ""
        except Exception:
            return ""

    def _auto_pick_label_by_rect_center(self, rect) -> str:
        try:
            cx = int((rect.left + rect.right) / 2)
            cy = int((rect.top + rect.bottom) / 2)
            ctrl = auto.ControlFromPoint(cx, cy)
            return self._label_from_ctrl(ctrl)
        except Exception:
            return ""

    def _update_layered(self, x: int, y: int, w: int, h: int, label: str):
        # screen dc
        hdc_screen = user32.GetDC(None)
        if not hdc_screen:
            return

        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            user32.ReleaseDC(None, hdc_screen)
            return

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bmi.bmiHeader.biSizeImage = w * h * 4

        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(
            hdc_screen, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        if not hbmp:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
            return

        old_bmp = gdi32.SelectObject(hdc_mem, hbmp)

        # ---- GDI+ draw into the DIB via HDC ----
        graphics = ctypes.c_void_p()
        status = gdiplus.GdipCreateFromHDC(hdc_mem, ctypes.byref(graphics))
        if status != Ok:
            gdi32.SelectObject(hdc_mem, old_bmp)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
            return

        # smooth
        _check(
            gdiplus.GdipSetSmoothingMode(graphics, SmoothingModeAntiAlias),
            "SetSmoothingMode",
        )
        _check(
            gdiplus.GdipSetTextRenderingHint(
                graphics, TextRenderingHintAntiAliasGridFit
            ),
            "SetTextRenderingHint",
        )
        _check(
            gdiplus.GdipGraphicsClear(graphics, _argb(0, 0, 0, 0)), "GraphicsClear"
        )  # fully transparent

        # border (orange)
        pen_border = ctypes.c_void_p()
        br, bg, bb = self._BORDER_RGB
        _check(
            gdiplus.GdipCreatePen1(
                _argb(255, br, bg, bb),
                ctypes.c_float(self._BORDER_WIDTH),
                UnitPixel,
                ctypes.byref(pen_border),
            ),
            "CreatePen(border)",
        )

        inset = self._BORDER_WIDTH / 2.0
        _check(
            gdiplus.GdipDrawRectangle(
                graphics,
                pen_border,
                ctypes.c_float(inset),
                ctypes.c_float(inset),
                ctypes.c_float(max(1.0, w - self._BORDER_WIDTH)),
                ctypes.c_float(max(1.0, h - self._BORDER_WIDTH)),
            ),
            "DrawRectangle",
        )
        gdiplus.GdipDeletePen(pen_border)

        # text (white fill + black outline)
        label = (label or "").strip()
        if label and w >= 60 and h >= 30:
            base = min(w, h)
            font_px = int(base * 0.18)
            font_px = max(10, min(font_px, 40))

            family = ctypes.c_void_p()
            fmt = ctypes.c_void_p()
            path = ctypes.c_void_p()
            brush_white = ctypes.c_void_p()
            pen_black = ctypes.c_void_p()

            # font family
            _check(
                gdiplus.GdipCreateFontFamilyFromName(
                    "Segoe UI", None, ctypes.byref(family)
                ),
                "CreateFontFamily",
            )

            # string format (centered)
            _check(
                gdiplus.GdipCreateStringFormat(0, 0, ctypes.byref(fmt)),
                "CreateStringFormat",
            )
            _check(
                gdiplus.GdipSetStringFormatAlign(fmt, StringAlignmentCenter), "SetAlign"
            )
            _check(
                gdiplus.GdipSetStringFormatLineAlign(fmt, StringAlignmentCenter),
                "SetLineAlign",
            )

            # path
            _check(gdiplus.GdipCreatePath(0, ctypes.byref(path)), "CreatePath")

            # layout rect (padding a bit)
            pad = 6.0
            rectf = RECTF(
                pad, pad, float(max(1, w)) - pad * 2.0, float(max(1, h)) - pad * 2.0
            )

            _check(
                gdiplus.GdipAddPathString(
                    path,
                    label,
                    -1,
                    family,
                    FontStyleRegular,
                    ctypes.c_float(font_px),
                    ctypes.byref(rectf),
                    fmt,
                ),
                "AddPathString",
            )

            _check(
                gdiplus.GdipCreateSolidFill(
                    _argb(255, 255, 255, 0), ctypes.byref(brush_white)
                ),
                "CreateSolidFill(white)",
            )
            _check(gdiplus.GdipFillPath(graphics, brush_white, path), "FillPath(white)")

            # black outline on top
            _check(
                gdiplus.GdipCreatePen1(
                    _argb(0, 0, 0, 0),
                    ctypes.c_float(self._TEXT_STROKE),
                    UnitPixel,
                    ctypes.byref(pen_black),
                ),
                "CreatePen(black)",
            )
            _check(gdiplus.GdipDrawPath(graphics, pen_black, path), "DrawPath(black)")

            # cleanup text objects
            gdiplus.GdipDeletePen(pen_black)
            gdiplus.GdipDeleteBrush(brush_white)
            gdiplus.GdipDeletePath(path)
            gdiplus.GdipDeleteStringFormat(fmt)
            gdiplus.GdipDeleteFontFamily(family)

        gdiplus.GdipDeleteGraphics(graphics)

        # ---- push to layered window ----
        pt_dst = POINT(x, y)
        size = SIZE(w, h)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

        user32.UpdateLayeredWindow(
            wintypes.HWND(self.hwnd),
            wintypes.HDC(hdc_screen),
            ctypes.byref(pt_dst),
            ctypes.byref(size),
            wintypes.HDC(hdc_mem),
            ctypes.byref(pt_src),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )

        # cleanup GDI objects
        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

    def show_control(self, ctrl: auto.Control):
        try:
            rect = ctrl.BoundingRectangle
        except Exception:
            return
        self._label = self._label_from_ctrl(ctrl)
        self.show_rect(rect)

    def show_rect(self, rect):
        left = int(rect.left)
        top = int(rect.top)
        right = int(rect.right)
        bottom = int(rect.bottom)

        width = max(0, right - left)
        height = max(0, bottom - top)

        if width <= 1 or height <= 1:
            self.hide()
            return

        rect_key = (left, top, right, bottom)
        if rect_key != self._last_rect_key:
            self._last_rect_key = rect_key
            self._label = self._auto_pick_label_by_rect_center(rect)

        self._update_layered(left, top, width, height, self._label)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)

    def hide(self):
        win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)


# =========================
# rest of your helpers (unchanged)
# =========================


def find_child_control_by_type(
    ctrl, expected_type: str, max_depth: int = 3, preferred_name: str = ""
) -> Optional[auto.Control]:
    """递归查找指定类型的子控件"""
    if not ctrl:
        return None

    if _matches_control_type(ctrl, expected_type):
        return ctrl

    if max_depth <= 0:
        return None

    root_rect = _safe_rect(ctrl)
    if root_rect:
        nodes = _walk_controls(ctrl, max_depth=max_depth, max_nodes=1200)
        if expected_type in EDIT_COMPAT_CONTROL_TYPES:
            picked = _pick_input_candidate(nodes, root_rect)
            if picked and picked.get("ctrl"):
                return picked.get("ctrl")
        elif expected_type == "ButtonControl":
            preferred_name_norm = _normalize_text(preferred_name)
            if preferred_name_norm:
                if _is_exact_voice_button_name(preferred_name):
                    picked = _pick_named_button_candidate(
                        nodes, root_rect, _is_exact_voice_button_name, prefer_left=True
                    )
                else:
                    picked = _pick_named_button_candidate(
                        nodes,
                        root_rect,
                        lambda name: _normalize_text(name) == preferred_name_norm,
                        prefer_left=False,
                    )
            else:
                input_node = _pick_input_candidate(nodes, root_rect)
                picked = _pick_button_candidate(nodes, root_rect, input_node)
            if picked and picked.get("ctrl"):
                return picked.get("ctrl")
        elif expected_type == "WindowControl":
            input_node = _pick_input_candidate(nodes, root_rect)
            picked = _pick_message_container(nodes, ctrl, root_rect, input_node)
            if picked and picked.get("ctrl"):
                return picked.get("ctrl")

    try:
        children = ctrl.GetChildren()
        for child in children:
            found = find_child_control_by_type(child, expected_type, max_depth - 1, preferred_name=preferred_name)
            if found:
                return found
    except Exception:
        pass

    return None


def control_from_point_safe(x: int, y: int, tk_hwnd: int) -> Optional[auto.Control]:
    hwnd = win32gui.WindowFromPoint((x, y))

    if hwnd == tk_hwnd or win32gui.IsChild(tk_hwnd, hwnd):
        return None
    try:
        ctrl = auto.ControlFromPoint(x, y)
        return ctrl
    except Exception:
        return None


def _safe_hwnd(ctrl) -> int:
    try:
        return int(getattr(ctrl, "NativeWindowHandle", 0) or 0)
    except Exception:
        return 0


def _safe_pid(ctrl) -> int:
    try:
        return int(getattr(ctrl, "ProcessId", 0) or 0)
    except Exception:
        return 0


def _root_hwnd_from_control(ctrl, fallback_x: int, fallback_y: int) -> int:
    hwnd = _safe_hwnd(ctrl)
    if not hwnd:
        try:
            hwnd = win32gui.WindowFromPoint((fallback_x, fallback_y))
        except Exception:
            hwnd = 0
    if not hwnd:
        return 0

    try:
        class_name = win32gui.GetClassName(hwnd) or ""
    except Exception:
        class_name = ""
    if class_name == "Chrome_RenderWidgetHostHWND":
        return int(hwnd)

    try:
        return int(win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd)
    except Exception:
        return int(hwnd)


def _root_rect_from_hwnd(hwnd: int) -> Optional[tuple[int, int, int, int, int, int]]:
    if not hwnd:
        return None
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None
    width = max(0, int(right - left))
    height = max(0, int(bottom - top))
    if width <= 1 or height <= 1:
        return None
    return int(left), int(top), int(right), int(bottom), width, height


def _candidate_score(bound: BoundControl, ctrl, target_x: int, target_y: int) -> float:
    rect = _safe_rect(ctrl)
    if not rect:
        return float("-inf")
    left, top, right, bottom, _, _ = rect
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    dist = abs(center_x - target_x) + abs(center_y - target_y)
    score = -dist

    ctrl_hwnd = _safe_hwnd(ctrl)
    if bound.native_hwnd and ctrl_hwnd == bound.native_hwnd:
        score += 4000.0
    if bound.automation_id and _safe_text(ctrl, "AutomationId") == bound.automation_id:
        score += 900.0
    if bound.class_name and _safe_text(ctrl, "ClassName") == bound.class_name:
        score += 420.0
    if bound.name and _safe_text(ctrl, "Name") == bound.name:
        score += 260.0
    if bound.framework and _safe_text(ctrl, "FrameworkId") == bound.framework:
        score += 140.0
    if bound.process_id and _safe_pid(ctrl) == bound.process_id:
        score += 120.0
    if bound.expected_type == "ButtonControl":
        ctrl_name = _safe_text(ctrl, "Name")
        ctrl_blob = " ".join(
            [
                ctrl_name or "",
                _safe_text(ctrl, "AutomationId"),
                _safe_text(ctrl, "ClassName"),
            ]
        ).lower()
        if _is_dangerous_button_blob(ctrl_blob):
            score -= 3200.0
        if _is_exact_send_name(bound.name):
            if _is_exact_send_name(ctrl_name):
                score += 3200.0
            else:
                score -= 1800.0
        if _is_exact_voice_button_name(bound.name):
            if _is_exact_voice_button_name(ctrl_name):
                score += 2600.0
            else:
                score -= 1400.0
    return score


def _find_bound_candidate(root_ctrl, bound: BoundControl) -> Optional[auto.Control]:
    root_rect = _root_rect_from_hwnd(bound.root_hwnd)
    if root_rect:
        root_left, root_top, _, _, root_w, root_h = root_rect
        target_x = int(root_left + root_w * min(max(bound.rel_x, 0.0), 1.0))
        target_y = int(root_top + root_h * min(max(bound.rel_y, 0.0), 1.0))
    else:
        target_x = int(bound.center_x)
        target_y = int(bound.center_y)

    direct_hwnd = bound.native_hwnd
    if direct_hwnd:
        try:
            ctrl = auto.ControlFromHandle(direct_hwnd)
            if _matches_control_type(ctrl, bound.expected_type):
                return ctrl
        except Exception:
            pass

    nodes = _walk_controls(root_ctrl, max_depth=16, max_nodes=6000)
    if bound.expected_type == "ButtonControl" and _is_exact_send_name(bound.name):
        exact_send_nodes = [
            node
            for node in nodes
            if _matches_control_type(node.get("ctrl"), bound.expected_type)
            and _is_exact_send_name(node.get("name", "") or "")
        ]
        if exact_send_nodes:
            nodes = exact_send_nodes
    elif bound.expected_type == "ButtonControl" and _is_exact_voice_button_name(bound.name):
        exact_voice_nodes = [
            node
            for node in nodes
            if _matches_control_type(node.get("ctrl"), bound.expected_type)
            and _is_exact_voice_button_name(node.get("name", "") or "")
        ]
        if exact_voice_nodes:
            nodes = exact_voice_nodes

    best: tuple[float, auto.Control] | None = None
    for node in nodes:
        ctrl = node.get("ctrl")
        if not ctrl or not _matches_control_type(ctrl, bound.expected_type):
            continue
        score = _candidate_score(bound, ctrl, target_x, target_y)
        if best is None or score > best[0]:
            best = (score, ctrl)
    return best[1] if best else None


def build_bound_control(ctrl, expected_type: str) -> Optional[BoundControl]:
    try:
        r = ctrl.BoundingRectangle
        cx = int((r.left + r.right) / 2)
        cy = int((r.top + r.bottom) / 2)
        actual_type = getattr(ctrl, "ControlTypeName", "") or expected_type
        native_hwnd = _safe_hwnd(ctrl)
        process_id = _safe_pid(ctrl)
        root_hwnd = _root_hwnd_from_control(ctrl, cx, cy)
        root_rect = _root_rect_from_hwnd(root_hwnd)
        rel_x = 0.5
        rel_y = 0.5
        if root_rect:
            root_left, root_top, _, _, root_w, root_h = root_rect
            rel_x = min(max((cx - root_left) / max(root_w, 1), 0.0), 1.0)
            rel_y = min(max((cy - root_top) / max(root_h, 1), 0.0), 1.0)
        return BoundControl(
            expected_type=expected_type,
            center_x=cx,
            center_y=cy,
            actual_type=actual_type,
            name=getattr(ctrl, "Name", "") or "",
            framework=getattr(ctrl, "FrameworkId", "") or "",
            automation_id=getattr(ctrl, "AutomationId", "") or "",
            class_name=getattr(ctrl, "ClassName", "") or "",
            root_hwnd=root_hwnd,
            native_hwnd=native_hwnd,
            process_id=process_id,
            rel_x=rel_x,
            rel_y=rel_y,
        )
    except Exception:
        return None


def reacquire(bound: BoundControl, tk_hwnd: int):
    if not bound:
        return None

    if bound.root_hwnd:
        try:
            root_ctrl = pick_chat_bind_root(bound.root_hwnd)
        except Exception:
            root_ctrl = None
        if root_ctrl:
            found = _find_bound_candidate(root_ctrl, bound)
            if found:
                return found

    offsets = [
        (0, 0),
        (8, 0),
        (-8, 0),
        (0, 8),
        (0, -8),
        (15, 0),
        (-15, 0),
        (0, 15),
        (0, -15),
    ]
    for dx, dy in offsets:
        ctrl = control_from_point_safe(
            bound.center_x + dx, bound.center_y + dy, tk_hwnd
        )
        if not ctrl:
            continue
        if _matches_control_type(ctrl, bound.expected_type):
            return ctrl
    return None
