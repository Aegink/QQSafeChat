from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import threading
import tkinter as tk
import time
import tempfile
from types import SimpleNamespace
from tkinter import messagebox
from urllib.parse import unquote
import win32api
import win32clipboard
import win32con
import win32gui
import uiautomation as auto
import ttkbootstrap as ttk
from PIL import Image, ImageChops, ImageGrab, ImageOps, ImageStat
from ttkbootstrap.constants import *

from uia.uia_picker import HighlightRect, control_from_point_safe


MAX_NODES = 10000  # 最大节点数 如果显示不全可以改这个
MAX_DEPTH = 16  # 最大深度 如果选择整个窗口 建议把深度调整到16以上 如果选择消息列表 可以把这个改成 10（理论 8 足够）

COPY_IMAGE_ADDRESS_MENU_NAMES = (
    "复制图片地址",
    "複製圖片位址",
    "Copy image address",
)

OPEN_ORIGINAL_IMAGE_MENU_NAMES = (
    "在新标签页中打开原始图片",
    "在新標籤頁中開啟原始圖片",
    "Open original image in new tab",
)


class UIAComponentBrowserBootstrap:

    def __init__(self):
        self.win = ttk.Window(themename="darkly")
        self.win.title("QQSafeChat Debug - UIA 遍历工具")
        self.win.geometry("1320x860")

        self.tk_hwnd = self.win.winfo_id()

        self.picking = False
        self.hover_ctrl = None
        self.highlight = HighlightRect()

        self._item_meta: dict[str, dict] = {}

        self._payload_all: dict[str, str] = {}
        self._payload_type: dict[str, str] = {}
        self._current_nodes: list[dict] = []

        self._hits: list[str] = []
        self._hit_idx: int = 0
        self._selected_item: str | None = None

        self.status_var = tk.StringVar(value="就绪")
        self.detail_var = tk.StringVar(value="节点信息：")
        self.search_var = tk.StringVar(value="")
        self.search_mode_var = tk.StringVar(value="All")

        self.show_type_var = tk.BooleanVar(value=False)
        self.show_name_var = tk.BooleanVar(value=False)
        self.show_value_var = tk.BooleanVar(value=True)
        self.show_aid_var = tk.BooleanVar(value=False)
        self.show_class_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._apply_tree_style()
        self._bind()

        self.update_display_columns()

        self.win.after(30, self.pick_loop)
        self.win.mainloop()

    def _build_ui(self):
        top = ttk.Frame(self.win, padding=10)
        top.pack(fill=X)

        self.btn_pick = ttk.Button(
            top,
            text="按住拖拽选择组件（松开锁定）",
            bootstyle=PRIMARY,
        )
        self.btn_pick.pack(side=LEFT)

        ttk.Label(top, textvariable=self.status_var).pack(side=LEFT, padx=12)

        info = ttk.Frame(self.win, padding=(10, 0, 10, 6))
        info.pack(fill=X)
        ttk.Label(info, textvariable=self.detail_var, bootstyle=INFO).pack(anchor=W)

        bar = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        bar.pack(fill=X)

        ttk.Label(bar, text="搜索：").pack(side=LEFT)
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=46)
        self.search_entry.pack(side=LEFT, padx=6)

        self.mode_box = ttk.Combobox(
            bar,
            textvariable=self.search_mode_var,
            values=["All", "ControlType"],
            width=14,
            state="readonly",
        )
        self.mode_box.pack(side=LEFT, padx=(0, 10))

        ttk.Button(bar, text="查找", command=self.do_search, bootstyle=SUCCESS).pack(
            side=LEFT
        )
        ttk.Button(bar, text="上一个", command=self.prev_hit).pack(side=LEFT, padx=4)
        ttk.Button(bar, text="下一个", command=self.next_hit).pack(side=LEFT)
        ttk.Button(
            bar,
            text="导出当前 UIA 树",
            command=self.export_current_tree,
            bootstyle=INFO,
        ).pack(side=LEFT, padx=(10, 0))
        ttk.Button(
            bar,
            text="尝试 Ctrl+C 复制图片",
            command=self.copy_selected_image_via_ctrl_c,
            bootstyle=SUCCESS,
        ).pack(side=LEFT, padx=(6, 0))

        cols_box = ttk.Frame(bar)
        cols_box.pack(side=RIGHT)

        ttk.Label(cols_box, text="显示列：").pack(side=LEFT, padx=(0, 6))
        ttk.Checkbutton(
            cols_box,
            text="Type列",
            variable=self.show_type_var,
            command=self.update_display_columns,
            bootstyle=SECONDARY,
        ).pack(side=LEFT, padx=2)
        ttk.Checkbutton(
            cols_box,
            text="Name",
            variable=self.show_name_var,
            command=self.update_display_columns,
            bootstyle=SECONDARY,
        ).pack(side=LEFT, padx=2)
        ttk.Checkbutton(
            cols_box,
            text="Value",
            variable=self.show_value_var,
            command=self.update_display_columns,
            bootstyle=SECONDARY,
        ).pack(side=LEFT, padx=2)
        ttk.Checkbutton(
            cols_box,
            text="AutomationId",
            variable=self.show_aid_var,
            command=self.update_display_columns,
            bootstyle=SECONDARY,
        ).pack(side=LEFT, padx=2)
        ttk.Checkbutton(
            cols_box,
            text="ClassName",
            variable=self.show_class_var,
            command=self.update_display_columns,
            bootstyle=SECONDARY,
        ).pack(side=LEFT, padx=2)

        outer = ttk.Labelframe(self.win, text="UIA Tree", padding=(10, 8))
        outer.pack(fill=BOTH, expand=YES, padx=10, pady=(0, 10))

        main = ttk.Frame(outer)
        main.pack(fill=BOTH, expand=YES)

        cols = ("type", "name", "value", "aid", "class")
        self.tree = ttk.Treeview(main, columns=cols, show="tree headings")

        self.tree.heading("#0", text="层级 / ControlType")
        self.tree.heading("type", text="ControlType")
        self.tree.heading("name", text="Name")
        self.tree.heading("value", text="Value")
        self.tree.heading("aid", text="AutomationId")
        self.tree.heading("class", text="ClassName")

        self.tree.column("#0", width=340, stretch=False)
        self.tree.column("type", width=150, stretch=False)
        self.tree.column("name", width=240)
        self.tree.column("value", width=320)
        self.tree.column("aid", width=280)
        self.tree.column("class", width=220)

        vs = ttk.Scrollbar(main, orient=VERTICAL, command=self.tree.yview)
        hs = ttk.Scrollbar(main, orient=HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)

        self.tree.tag_configure("odd")
        self.tree.tag_configure("even")

    def _apply_tree_style(self):

        style = ttk.Style()

        try:
            style.configure("Treeview", rowheight=26, borderwidth=1, relief="solid")
            style.configure(
                "Treeview.Heading", font=("Segoe UI", 10, "bold"), relief="raised"
            )
        except Exception:
            pass

        odd_bg = "#1f232a"
        even_bg = "#252b33"
        self.tree.tag_configure("odd", background=odd_bg)
        self.tree.tag_configure("even", background=even_bg)

    def _bind(self):
        self.btn_pick.bind("<ButtonPress-1>", self.start_pick)
        self.btn_pick.bind("<ButtonRelease-1>", self.stop_pick_and_traverse)
        self.search_entry.bind("<Return>", lambda _e: self.do_search())
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

    def update_display_columns(self):

        display = []
        if self.show_type_var.get():
            display.append("type")
        if self.show_name_var.get():
            display.append("name")
        if self.show_value_var.get():
            display.append("value")
        if self.show_aid_var.get():
            display.append("aid")
        if self.show_class_var.get():
            display.append("class")

        if not display:
            self.show_value_var.set(True)
            display = ["value"]

        self.tree.configure(displaycolumns=display)

    def start_pick(self, _event=None):
        self.picking = True
        self.hover_ctrl = None
        win32api.SetCursor(win32gui.LoadCursor(0, win32con.IDC_CROSS))
        self.status_var.set("正在拾取：移到目标组件上，松开锁定…")

    def stop_pick_and_traverse(self, _event=None):
        self.picking = False
        win32api.SetCursor(win32gui.LoadCursor(0, win32con.IDC_ARROW))
        self.highlight.hide()

        ctrl = self.hover_ctrl
        self.hover_ctrl = None

        if not ctrl:
            self.status_var.set("未选中组件")
            return

        try:
            ctype = getattr(ctrl, "ControlTypeName", "") or ""
            name = getattr(ctrl, "Name", "") or ""
            self.status_var.set(f"已锁定组件：{ctype} | Name={name}")
        except Exception:
            self.status_var.set("已锁定组件（信息读取失败）")

        self._clear_tree()
        threading.Thread(
            target=self._build_and_populate, args=(ctrl,), daemon=True
        ).start()

    def pick_loop(self):
        if self.picking:
            x, y = win32api.GetCursorPos()
            ctrl = control_from_point_safe(x, y, self.tk_hwnd)
            if ctrl:
                self.hover_ctrl = ctrl
                try:
                    self.highlight.show_rect(ctrl.BoundingRectangle)
                except Exception:
                    pass
            else:
                self.highlight.hide()

        self.win.after(30, self.pick_loop)

    def _clear_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._item_meta.clear()
        self._payload_all.clear()
        self._payload_type.clear()
        self._current_nodes = []
        self._hits.clear()
        self._hit_idx = 0
        self._selected_item = None
        self.detail_var.set("节点信息：")
        self.highlight.hide()

    @staticmethod
    def _s(x) -> str:
        try:
            return "" if x is None else str(x)
        except Exception:
            return ""

    def _get_value(self, ctrl) -> str:
        try:
            vp = ctrl.GetValuePattern()
            return vp.Value or ""
        except Exception:
            return ""

    def _get_live_debug_hints(self, ctrl) -> dict[str, str]:
        hints = {
            "help_text": "",
            "legacy_name": "",
            "legacy_value": "",
            "legacy_description": "",
            "capture": "",
        }
        if not ctrl:
            return hints

        try:
            hints["help_text"] = self._s(getattr(ctrl, "HelpText", ""))
        except Exception:
            pass

        try:
            legacy = ctrl.GetLegacyIAccessiblePattern()
        except Exception:
            legacy = None

        if legacy:
            for key, attr in (
                ("legacy_name", "Name"),
                ("legacy_value", "Value"),
                ("legacy_description", "Description"),
            ):
                try:
                    hints[key] = self._s(getattr(legacy, attr, ""))
                except Exception:
                    pass

        try:
            hints["capture"] = "yes" if hasattr(ctrl, "CaptureToImage") else "no"
        except Exception:
            pass

        return hints

    def _control_from_meta(self, meta: dict):
        rect = meta.get("rect") or {}
        left = rect.get("left")
        top = rect.get("top")
        right = rect.get("right")
        bottom = rect.get("bottom")
        if None in (left, top, right, bottom):
            return None

        if right <= left or bottom <= top:
            return None

        cx = int((left + right) / 2)
        cy = int((top + bottom) / 2)
        return control_from_point_safe(cx, cy, self.tk_hwnd)

    def _detail_text(self, meta: dict, hints: dict[str, str]) -> str:
        hwnd = meta.get("hwnd", 0) or 0
        pid = meta.get("pid", 0) or 0
        tid = meta.get("tid", 0) or 0
        hwnd_str = f"0x{hwnd:X}" if hwnd else "N/A"

        lines = [
            f"HWND={hwnd_str} | PID={pid or 'N/A'} | TID={tid or 'N/A'}",
            f"Type={meta.get('type','')} | Name={meta.get('name','')}",
        ]

        rect = meta.get("rect")
        if rect:
            lines.append(
                f"Rect=({rect.get('left')}, {rect.get('top')}) - ({rect.get('right')}, {rect.get('bottom')})"
            )

        if meta.get("value"):
            lines.append(f"Value={meta.get('value')}")

        for label, key in (
            ("HelpText", "help_text"),
            ("Legacy.Name", "legacy_name"),
            ("Legacy.Value", "legacy_value"),
            ("Legacy.Description", "legacy_description"),
        ):
            value = (hints.get(key) or "").strip()
            if value:
                lines.append(f"{label}={value}")

        if hints.get("capture"):
            lines.append(f"CaptureToImage={hints['capture']}")

        return "\n".join(lines)

    def _export_dir(self) -> Path:
        path = Path.cwd() / "debug_exports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _export_selected_via_grab(self, rect: dict, out_path: Path) -> bool:
        if not rect:
            return False

        left = rect.get("left")
        top = rect.get("top")
        right = rect.get("right")
        bottom = rect.get("bottom")
        if None in (left, top, right, bottom):
            return False
        if right <= left or bottom <= top:
            return False

        try:
            self.highlight.hide()
            self.win.update_idletasks()
            time.sleep(0.05)
            img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
            img.save(out_path)
            return True
        except Exception:
            return False
        finally:
            try:
                self.highlight.show_rect(SimpleNamespace(**rect))
            except Exception:
                self.highlight.hide()

    def _capture_selected_to_path(self, meta: dict, out_path: Path) -> bool:
        ctrl = self._control_from_meta(meta)
        if ctrl:
            try:
                if bool(ctrl.CaptureToImage(str(out_path))):
                    return True
            except Exception:
                pass
        return self._export_selected_via_grab(meta.get("rect"), out_path)

    def _selected_center(self, meta: dict) -> tuple[int, int] | None:
        rect = meta.get("rect") or {}
        left = rect.get("left")
        top = rect.get("top")
        right = rect.get("right")
        bottom = rect.get("bottom")
        if None in (left, top, right, bottom):
            return None
        if right <= left or bottom <= top:
            return None
        return int((left + right) / 2), int((top + bottom) / 2)

    def _focus_target_window(self, meta: dict):
        hwnd = int(meta.get("hwnd", 0) or 0)
        if not hwnd:
            return
        try:
            hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
        except Exception:
            pass
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        except Exception:
            pass
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

    @staticmethod
    def _mouse_click_point(x: int, y: int, button: str = "left"):
        win32api.SetCursorPos((x, y))
        if button == "right":
            down = win32con.MOUSEEVENTF_RIGHTDOWN
            up = win32con.MOUSEEVENTF_RIGHTUP
        else:
            down = win32con.MOUSEEVENTF_LEFTDOWN
            up = win32con.MOUSEEVENTF_LEFTUP
        win32api.mouse_event(down, 0, 0)
        win32api.mouse_event(up, 0, 0)

    @staticmethod
    def _clipboard_get_text() -> str:
        for _ in range(8):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                time.sleep(0.03)
        else:
            return ""

        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
            return ""
        except Exception:
            return ""
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def _clipboard_format_name(fmt: int) -> str:
        known = {
            win32con.CF_TEXT: "CF_TEXT",
            win32con.CF_BITMAP: "CF_BITMAP",
            win32con.CF_UNICODETEXT: "CF_UNICODETEXT",
            win32con.CF_HDROP: "CF_HDROP",
            getattr(win32con, "CF_DIB", 8): "CF_DIB",
            getattr(win32con, "CF_DIBV5", 17): "CF_DIBV5",
        }
        if fmt in known:
            return known[fmt]
        try:
            return win32clipboard.GetClipboardFormatName(fmt)
        except Exception:
            return f"FORMAT_{fmt}"

    def _clipboard_list_formats(self) -> list[str]:
        for _ in range(8):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                time.sleep(0.03)
        else:
            return []

        try:
            formats: list[str] = []
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                formats.append(self._clipboard_format_name(fmt))
            return formats
        except Exception:
            return []
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _clipboard_get_named_payload(self, preferred_names: tuple[str, ...]):
        targets = {name.lower() for name in preferred_names}

        for _ in range(8):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                time.sleep(0.03)
        else:
            return None, None

        try:
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                fmt_name = self._clipboard_format_name(fmt)
                if fmt_name.lower() not in targets:
                    continue
                try:
                    data = win32clipboard.GetClipboardData(fmt)
                except Exception:
                    continue
                if isinstance(data, memoryview):
                    data = data.tobytes()
                elif isinstance(data, bytearray):
                    data = bytes(data)
                return fmt_name, data
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

        return None, None

    @staticmethod
    def _clipboard_clear():
        for _ in range(8):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                time.sleep(0.03)
        else:
            return

        try:
            win32clipboard.EmptyClipboard()
        except Exception:
            pass
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _find_context_menu_item(self, sub_names: tuple[str, ...], timeout_sec: float = 1.8):
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            for name in sub_names:
                try:
                    item = auto.MenuItemControl(searchDepth=20, SubName=name)
                    if item.Exists(maxSearchSeconds=0.15, searchIntervalSeconds=0.05):
                        return item
                except Exception:
                    continue
            time.sleep(0.05)
        return None

    def _dismiss_context_menu(self):
        try:
            auto.SendKeys("{ESC}", waitTime=0.05)
        except Exception:
            pass

    def _set_detail_action_note(self, meta: dict, note: str):
        ctrl = self._control_from_meta(meta)
        hints = self._get_live_debug_hints(ctrl)
        self.detail_var.set(f"{self._detail_text(meta, hints)}\n\n实验结果\n{note}")

    @staticmethod
    def _parse_cf_html_payload(html_text: str) -> dict:
        source_url = ""
        source_match = re.search(r"^SourceURL:(.*)$", html_text, re.MULTILINE)
        if source_match:
            source_url = source_match.group(1).strip()

        fragment = ""
        fragment_match = re.search(
            r"<!--StartFragment-->(.*?)<!--EndFragment-->",
            html_text,
            re.IGNORECASE | re.DOTALL,
        )
        if fragment_match:
            fragment = fragment_match.group(1).strip()

        search_scope = fragment or html_text
        img_src = ""
        img_match = re.search(
            r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']",
            search_scope,
            re.IGNORECASE,
        )
        if img_match:
            img_src = img_match.group(1).strip()

        local_path = ""
        if img_src.lower().startswith("file://"):
            raw_path = unquote(img_src[7:])
            if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
                raw_path = raw_path[1:]
            local_path = raw_path.replace("/", "\\")

        return {
            "source_url": source_url,
            "fragment": fragment,
            "img_src": img_src,
            "local_path": local_path,
        }

    def _read_clipboard_payload(self) -> dict:
        formats = self._clipboard_list_formats()
        out_dir = self._export_dir()
        stamp = int(time.time() * 1000)

        png_name, png_data = self._clipboard_get_named_payload(("PNG", "image/png"))
        if isinstance(png_data, bytes) and png_data.startswith(b"\x89PNG\r\n\x1a\n"):
            out_path = out_dir / f"clipboard_image_{stamp}.png"
            try:
                out_path.write_bytes(png_data)
            except Exception:
                out_path = None
            return {
                "kind": "png-bytes",
                "formats": formats,
                "path": out_path,
                "format_name": png_name,
            }

        html_name, html_data = self._clipboard_get_named_payload(("HTML Format", "text/html"))
        if html_data:
            if isinstance(html_data, bytes):
                html_text = html_data.decode("utf-8", errors="replace")
            else:
                html_text = str(html_data)
            out_path = out_dir / f"clipboard_html_{stamp}.html"
            try:
                out_path.write_text(html_text, encoding="utf-8")
            except Exception:
                out_path = None
            html_info = self._parse_cf_html_payload(html_text)

            local_path = html_info.get("local_path") or ""
            if local_path and Path(local_path).exists():
                copied_path = out_dir / f"clipboard_exact_{stamp}{Path(local_path).suffix or '.bin'}"
                try:
                    shutil.copy2(local_path, copied_path)
                except Exception:
                    copied_path = None
                return {
                    "kind": "html-image-file",
                    "formats": formats,
                    "path": out_path,
                    "text": html_text,
                    "format_name": html_name,
                    "img_src": html_info.get("img_src") or "",
                    "source_url": html_info.get("source_url") or "",
                    "local_path": local_path,
                    "copied_path": copied_path,
                }

            if html_info.get("img_src"):
                return {
                    "kind": "html-image-src",
                    "formats": formats,
                    "path": out_path,
                    "text": html_text,
                    "format_name": html_name,
                    "img_src": html_info.get("img_src") or "",
                    "source_url": html_info.get("source_url") or "",
                }

            return {
                "kind": "html",
                "formats": formats,
                "path": out_path,
                "text": html_text,
                "format_name": html_name,
            }

        payload = None
        try:
            payload = ImageGrab.grabclipboard()
        except Exception:
            payload = None

        if isinstance(payload, Image.Image):
            out_path = out_dir / f"clipboard_image_{stamp}.png"
            try:
                payload.save(out_path, format="PNG")
            except Exception:
                out_path = None
            return {"kind": "image", "formats": formats, "path": out_path}

        if isinstance(payload, list):
            lines = [str(p) for p in payload]
            out_path = out_dir / f"clipboard_files_{stamp}.txt"
            try:
                out_path.write_text("\n".join(lines), encoding="utf-8")
            except Exception:
                out_path = None
            return {"kind": "files", "formats": formats, "path": out_path, "files": lines}

        text = (self._clipboard_get_text() or "").strip()
        if text:
            out_path = out_dir / f"clipboard_text_{stamp}.txt"
            try:
                out_path.write_text(text, encoding="utf-8")
            except Exception:
                out_path = None
            return {"kind": "text", "formats": formats, "path": out_path, "text": text}

        return {"kind": "empty", "formats": formats}

    def _copy_selected_payload_by_ctrl_c(self, meta: dict) -> dict:
        center = self._selected_center(meta)
        if not center:
            return {"kind": "invalid-rect", "formats": []}

        ctrl = self._control_from_meta(meta)
        attempts: list[tuple[str, bool]] = []
        if ctrl is not None:
            attempts.append(("SetFocus", False))
        attempts.append(("LeftClick", True))

        self.highlight.hide()
        self.win.update_idletasks()

        try:
            for attempt_name, need_click in attempts:
                self._clipboard_clear()
                self._focus_target_window(meta)
                time.sleep(0.08)

                if need_click:
                    self._mouse_click_point(center[0], center[1], button="left")
                elif ctrl is not None:
                    try:
                        ctrl.SetFocus()
                    except Exception:
                        continue

                time.sleep(0.08)
                auto.SendKeys("{CTRL}c", waitTime=0.08)
                time.sleep(0.18)

                result = self._read_clipboard_payload()
                result["attempt"] = attempt_name
                if result.get("kind") != "empty" or result.get("formats"):
                    return result

            return {"kind": "empty", "formats": [], "attempt": attempts[-1][0] if attempts else "None"}
        finally:
            try:
                self.highlight.show_rect(SimpleNamespace(**meta.get("rect", {})))
            except Exception:
                self.highlight.hide()

    def _clipboard_result_note(self, result: dict) -> str:
        attempt = result.get("attempt", "Unknown")
        formats = result.get("formats") or []
        formats_text = ", ".join(formats) if formats else "无"
        kind = result.get("kind")

        if kind == "html-image-file":
            path = result.get("path")
            img_src = result.get("img_src") or ""
            local_path = result.get("local_path") or ""
            copied_path = result.get("copied_path")
            return (
                f"Ctrl+C 成功（方式：{attempt}）\n载荷：HTML 图片源\n剪贴板格式：{formats_text}\n"
                f"img src：{img_src}\n本地原图：{local_path}\nHTML 保存：{path}\n"
                f"导出副本：{copied_path}"
            )

        if kind == "html-image-src":
            path = result.get("path")
            img_src = result.get("img_src") or ""
            source_url = result.get("source_url") or ""
            return (
                f"Ctrl+C 成功（方式：{attempt}）\n载荷：HTML 图片源\n剪贴板格式：{formats_text}\n"
                f"img src：{img_src}\nSourceURL：{source_url or '(空)'}\nHTML 保存：{path}"
            )

        if kind in {"image", "png-bytes"}:
            path = result.get("path")
            src = result.get("format_name") or "系统图像格式"
            return f"Ctrl+C 成功（方式：{attempt}）\n载荷：图片\n来源格式：{src}\n剪贴板格式：{formats_text}\n保存位置：{path}"

        if kind == "files":
            path = result.get("path")
            files = result.get("files") or []
            return (
                f"Ctrl+C 成功（方式：{attempt}）\n载荷：文件列表\n剪贴板格式：{formats_text}\n"
                f"文件数：{len(files)}\n保存位置：{path}\n首个文件：{files[0] if files else ''}"
            )

        if kind in {"text", "html"}:
            path = result.get("path")
            text = (result.get("text") or "").strip()
            if len(text) > 240:
                text = text[:240] + "..."
            src = result.get("format_name") or "CF_UNICODETEXT"
            return (
                f"Ctrl+C 成功（方式：{attempt}）\n载荷：文本\n来源格式：{src}\n"
                f"剪贴板格式：{formats_text}\n保存位置：{path}\n内容预览：{text}"
            )

        if kind == "invalid-rect":
            return "选中项边框无效，无法将焦点落到图片上"

        if formats:
            return (
                f"Ctrl+C 后剪贴板出现了格式，但当前还没解析出可用载荷\n方式：{attempt}\n"
                f"剪贴板格式：{formats_text}"
            )

        return f"Ctrl+C 后剪贴板仍为空，当前控件大概率没有真正拿到图片焦点\n方式：{attempt}"

    def copy_selected_image_via_ctrl_c(self):
        item = self._selected_item
        if not item:
            self.status_var.set("请先在树里选中一个图片控件")
            return

        meta = self._item_meta.get(item)
        if not meta:
            self.status_var.set("未找到选中项元数据")
            return

        result = self._copy_selected_payload_by_ctrl_c(meta)
        note = self._clipboard_result_note(result)
        self._set_detail_action_note(meta, note)
        self.status_var.set(note.splitlines()[0])

    def _invoke_selected_context_menu_item(self, names: tuple[str, ...]):
        item = self._selected_item
        if not item:
            self.status_var.set("请先在树里选中一个图片控件")
            return None

        meta = self._item_meta.get(item)
        if not meta:
            self.status_var.set("未找到选中项元数据")
            return None

        center = self._selected_center(meta)
        if not center:
            self.status_var.set("选中项边框无效，无法弹出图片菜单")
            return None

        self._focus_target_window(meta)
        self.highlight.hide()
        self.win.update_idletasks()
        time.sleep(0.08)

        try:
            self._mouse_click_point(center[0], center[1], button="right")
            time.sleep(0.12)
            menu_item = self._find_context_menu_item(names)
            if not menu_item:
                self.status_var.set("未找到图片右键菜单项，当前控件可能不是 Chromium 图片元素")
                self._dismiss_context_menu()
                return None

            try:
                menu_item.Click(simulateMove=False, waitTime=0.15)
            except Exception:
                try:
                    menu_item.SetFocus()
                except Exception:
                    pass
                auto.SendKeys("{ENTER}", waitTime=0.05)
            return meta
        finally:
            try:
                self.highlight.show_rect(SimpleNamespace(**meta.get("rect", {})))
            except Exception:
                self.highlight.hide()

    def copy_selected_image_address(self):
        self._clipboard_clear()
        meta = self._invoke_selected_context_menu_item(COPY_IMAGE_ADDRESS_MENU_NAMES)
        if not meta:
            return

        time.sleep(0.12)
        text = (self._clipboard_get_text() or "").strip()
        if not text:
            self.status_var.set("菜单已触发，但剪贴板里没有拿到图片地址")
            return

        out_dir = self._export_dir()
        out_path = out_dir / "last_image_address.txt"
        try:
            out_path.write_text(text, encoding="utf-8")
        except Exception:
            out_path = None

        prefix = f"图片地址已复制：{text}"
        if out_path:
            prefix += f" | 已保存到 {out_path}"
        self.status_var.set(prefix)

    def open_selected_original_image(self):
        meta = self._invoke_selected_context_menu_item(OPEN_ORIGINAL_IMAGE_MENU_NAMES)
        if not meta:
            return
        self.status_var.set("已触发“打开原始图片”菜单项，请观察 QQ 是否打开了原图标签页")

    @staticmethod
    def _qq_cache_roots() -> list[Path]:
        base = Path.home() / "AppData" / "Roaming" / "QQ" / "Partitions"
        if not base.exists():
            return []

        roots: list[Path] = []
        for part in sorted(base.glob("qqnt_*"), reverse=True):
            cache_dir = part / "Cache" / "Cache_Data"
            if cache_dir.exists():
                roots.append(cache_dir)
        return roots

    @staticmethod
    def _guess_cache_ext(path: Path) -> str | None:
        try:
            with path.open("rb") as f:
                header = f.read(16)
        except Exception:
            return None

        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if header.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return ".webp"
        return None

    def _qq_cache_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for root in self._qq_cache_roots():
            try:
                files = [
                    p
                    for p in root.iterdir()
                    if p.is_file() and p.name.startswith("f_") and p.stat().st_size > 8 * 1024
                ]
            except Exception:
                continue

            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for path in files:
                if self._guess_cache_ext(path):
                    candidates.append(path)
        return candidates

    @staticmethod
    def _prepare_compare_image(img: Image.Image, size=(96, 96)) -> Image.Image:
        return ImageOps.fit(img.convert("RGB"), size, method=Image.Resampling.LANCZOS)

    def _image_diff_score(self, ref_img: Image.Image, cand_img: Image.Image) -> float:
        ref_small = self._prepare_compare_image(ref_img)
        cand_small = self._prepare_compare_image(cand_img)
        diff = ImageChops.difference(ref_small, cand_small)
        stat = ImageStat.Stat(diff)
        return float(sum(stat.mean) / max(len(stat.mean), 1))

    def _load_reference_image(self, meta: dict) -> Image.Image | None:
        tmp_path = Path(tempfile.gettempdir()) / f"qqsafechat_ref_{int(time.time() * 1000)}.png"
        try:
            if not self._capture_selected_to_path(meta, tmp_path):
                return None
            with Image.open(tmp_path) as img:
                return img.convert("RGB")
        except Exception:
            return None
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def match_selected_original_file(self):
        item = self._selected_item
        if not item:
            self.status_var.set("请先在树里选中一个图片控件")
            return

        meta = self._item_meta.get(item)
        if not meta:
            self.status_var.set("未找到选中项元数据")
            return

        ref_img = self._load_reference_image(meta)
        if ref_img is None:
            self.status_var.set("无法获取选中控件参考图，无法匹配原图")
            return

        candidates = self._qq_cache_candidates()
        if not candidates:
            self.status_var.set("未找到 QQNT Cache_Data 图片缓存，无法匹配原图")
            return

        best_score: float | None = None
        best_path: Path | None = None
        best_ext = ".png"

        for path in candidates:
            ext = self._guess_cache_ext(path)
            if not ext:
                continue
            try:
                with Image.open(path) as cand_img:
                    score = self._image_diff_score(ref_img, cand_img)
            except Exception:
                continue

            if best_score is None or score < best_score:
                best_score = score
                best_path = path
                best_ext = ext

        if best_path is None or best_score is None:
            self.status_var.set("扫描了 QQNT 缓存，但没有找到可读取的候选图片")
            return

        out_dir = self._export_dir()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        out_path = out_dir / f"qqnt_original_match_{stamp}{best_ext}"

        try:
            shutil.copy2(best_path, out_path)
        except Exception as exc:
            self.status_var.set(f"找到候选原图但导出失败：{exc}")
            return

        confidence = "高" if best_score <= 12 else ("中" if best_score <= 24 else "低")
        self.status_var.set(
            f"已导出候选原图：{out_path} | 匹配度={confidence} | score={best_score:.2f}"
        )

    def export_selected_image(self):
        item = self._selected_item
        if not item:
            self.status_var.set("请先在树里选中一个控件")
            return

        meta = self._item_meta.get(item)
        if not meta:
            self.status_var.set("未找到选中项元数据")
            return

        out_dir = self._export_dir()
        ctrl_type = (meta.get("type") or "Control").replace("/", "_")
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        out_path = out_dir / f"uia_{ctrl_type}_{stamp}.png"

        ctrl = self._control_from_meta(meta)
        export_ok = False
        if ctrl:
            try:
                export_ok = bool(ctrl.CaptureToImage(str(out_path)))
            except Exception:
                export_ok = False

        if not export_ok:
            export_ok = self._export_selected_via_grab(meta.get("rect"), out_path)

        if export_ok:
            self.status_var.set(f"已导出选中控件图片：{out_path}")
        else:
            self.status_var.set("导出失败：该控件无法截图或边框无效")

    def _snapshot(self, ctrl, depth: int) -> dict:
        try:
            ctype = self._s(getattr(ctrl, "ControlTypeName", ""))
            name = self._s(getattr(ctrl, "Name", ""))
            aid = self._s(getattr(ctrl, "AutomationId", ""))
            cls = self._s(getattr(ctrl, "ClassName", ""))
        except Exception:
            ctype, name, aid, cls = "", "", "", ""

        value = self._get_value(ctrl)

        try:
            rect_obj = ctrl.BoundingRectangle
            rect = {
                "left": int(rect_obj.left),
                "top": int(rect_obj.top),
                "right": int(rect_obj.right),
                "bottom": int(rect_obj.bottom),
            }
        except Exception:
            rect = None

        try:
            hwnd = int(getattr(ctrl, "NativeWindowHandle", 0) or 0)
        except Exception:
            hwnd = 0
        try:
            pid = int(getattr(ctrl, "ProcessId", 0) or 0)
        except Exception:
            pid = 0
        try:
            tid = int(getattr(ctrl, "ThreadId", 0) or 0)
        except Exception:
            tid = 0

        return {
            "depth": depth,
            "type": ctype,
            "name": name,
            "value": value,
            "aid": aid,
            "class": cls,
            "rect": rect,
            "hwnd": hwnd,
            "pid": pid,
            "tid": tid,
        }

    def _build_nodes(self, root_ctrl, max_depth=MAX_DEPTH, max_nodes=MAX_NODES):
        nodes = []

        def rec(ctrl, parent_idx: int, depth: int):
            if len(nodes) >= max_nodes or depth > max_depth:
                return
            snap = self._snapshot(ctrl, depth)
            my_idx = len(nodes)
            nodes.append({"parent": parent_idx, "snap": snap})
            try:
                children = ctrl.GetChildren()
            except Exception:
                return
            for ch in children:
                rec(ch, my_idx, depth + 1)

        rec(root_ctrl, -1, 0)
        return nodes

    def _build_and_populate(self, root_ctrl):
        try:
            with auto.UIAutomationInitializerInThread():
                nodes = self._build_nodes(root_ctrl)
        except Exception as e:
            self.win.after(0, lambda: messagebox.showerror("错误", str(e)))
            return

        self.win.after(0, lambda: self._populate_tree(nodes))

    @staticmethod
    def _tree_line_from_snap(snap: dict) -> str:
        indent = "  " * max(int(snap.get("depth", 0) or 0), 0)
        parts = [
            f"Type={snap.get('type', '')}",
            f"Name={snap.get('name', '')}",
            f"AutomationId={snap.get('aid', '')}",
            f"ClassName={snap.get('class', '')}",
        ]

        value = snap.get("value") or ""
        if value:
            parts.append(f"Value={value}")

        rect = snap.get("rect")
        if rect:
            parts.append(
                "Rect=(%s,%s)-(%s,%s)" % (
                    rect.get("left"),
                    rect.get("top"),
                    rect.get("right"),
                    rect.get("bottom"),
                )
            )

        hwnd = snap.get("hwnd") or 0
        pid = snap.get("pid") or 0
        tid = snap.get("tid") or 0
        if hwnd:
            parts.append(f"HWND=0x{int(hwnd):X}")
        if pid:
            parts.append(f"PID={pid}")
        if tid:
            parts.append(f"TID={tid}")
        return f"{indent}- " + " | ".join(parts)

    def export_current_tree(self):
        if not self._current_nodes:
            self.status_var.set("当前没有可导出的 UIA 树，请先锁定一个窗口或控件")
            return

        out_dir = self._export_dir()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        json_path = out_dir / f"uia_tree_{stamp}.json"
        txt_path = out_dir / f"uia_tree_{stamp}.txt"

        payload = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "node_count": len(self._current_nodes),
            "nodes": self._current_nodes,
        }
        text = "\n".join(
            self._tree_line_from_snap(node.get("snap") or {}) for node in self._current_nodes
        )

        try:
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            txt_path.write_text(text, encoding="utf-8")
        except Exception as exc:
            self.status_var.set(f"导出 UIA 树失败：{exc}")
            return

        self.status_var.set(f"已导出 UIA 树：{json_path.name} / {txt_path.name}")

    def _populate_tree(self, nodes):
        self._current_nodes = list(nodes)
        idx_to_item: dict[int, str] = {}

        row_no = 0
        for idx, node in enumerate(nodes):
            parent_idx = node["parent"]
            snap = node["snap"]
            parent_item = "" if parent_idx < 0 else idx_to_item.get(parent_idx, "")

            text = f"[{snap['depth']}] {snap['type']}"

            value_display = snap["value"]
            if len(value_display) > 220:
                value_display = value_display[:220] + "…"

            values = (
                snap["type"],
                snap["name"],
                value_display,
                snap["aid"],
                snap["class"],
            )

            tag = "even" if (row_no % 2 == 0) else "odd"
            item = self.tree.insert(
                parent_item, "end", text=text, values=values, tags=(tag,)
            )
            idx_to_item[idx] = item
            row_no += 1

            self._item_meta[item] = snap

            all_payload = " ".join(
                [
                    text,
                    snap["type"],
                    snap["name"],
                    snap["value"],
                    snap["aid"],
                    snap["class"],
                ]
            ).lower()
            self._payload_all[item] = all_payload

            self._payload_type[item] = (snap["type"] or "").lower()

        self.status_var.set(
            f"遍历完成：{len(nodes)} 个节点（可搜索 ControlType / 全字段）"
        )

    def on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            self._selected_item = None
            self.highlight.hide()
            return
        item = sel[0]
        self._selected_item = item
        meta = self._item_meta.get(item)
        if not meta:
            self._selected_item = None
            self.highlight.hide()
            return

        ctrl = self._control_from_meta(meta)
        hints = self._get_live_debug_hints(ctrl)
        self.detail_var.set(self._detail_text(meta, hints))

        rect = meta.get("rect")
        if not rect:
            self.highlight.hide()
            return

        try:
            if rect["right"] <= rect["left"] or rect["bottom"] <= rect["top"]:
                self.highlight.hide()
                return
            self.highlight.show_rect(SimpleNamespace(**rect))
        except Exception:
            self.highlight.hide()

    @staticmethod
    def _token_match(hay: str, key: str) -> bool:
        tokens = [t for t in key.strip().split() if t]
        return all(t in hay for t in tokens)

    def do_search(self):
        key = self.search_var.get().strip().lower()
        if not key:
            self.status_var.set("请输入搜索关键字")
            return

        mode = (self.search_mode_var.get() or "ControlType").strip()
        payload_map = self._payload_type if mode == "ControlType" else self._payload_all

        self._hits.clear()
        self._hit_idx = 0

        for item, payload in payload_map.items():
            if self._token_match(payload, key):
                self._hits.append(item)

        if not self._hits:
            self.status_var.set(f"未找到匹配项（模式：{mode}）")
            return

        self.status_var.set(f"找到 {len(self._hits)} 个匹配项（模式：{mode}）")
        self._focus_hit(0)

    def _focus_hit(self, idx: int):
        item = self._hits[idx]

        # 展开
        p = self.tree.parent(item)
        while p:
            self.tree.item(p, open=True)
            p = self.tree.parent(p)

        self.tree.selection_set(item)
        self.tree.see(item)

    def next_hit(self):
        if not self._hits:
            return
        self._hit_idx = (self._hit_idx + 1) % len(self._hits)
        self._focus_hit(self._hit_idx)

    def prev_hit(self):
        if not self._hits:
            return
        self._hit_idx = (self._hit_idx - 1) % len(self._hits)
        self._focus_hit(self._hit_idx)


if __name__ == "__main__":
    auto.uiautomation.SetGlobalSearchTimeout(1)
    UIAComponentBrowserBootstrap()
