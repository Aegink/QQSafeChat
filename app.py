from __future__ import annotations
from pathlib import Path
import os
import shutil
import subprocess
import sys
import time
from collections import deque
import tkinter as tk
from tkinter import messagebox, simpledialog

import win32api
import win32con
import win32gui
import uiautomation as auto

from core.bot_engine import BotEngine
from storage.config import AppConfig
from windows.help_launcher import _start_docs_server
from storage.history_store import HistoryStore
from core.llm_client import MockLLMClient, OpenAIClient, SiliconFlowClient
from core.models import BoundControl
from storage.settings_store import OpenAISettings, SettingsStore
from uia.uia_picker import (
    HighlightRect,
    auto_detect_chat_bindings,
    build_bound_control,
    control_from_point_safe,
    pick_chat_bind_root,
    reacquire,
)
from windows.ui_theme import (
    DARK_TEXT_BG,
    DARK_TEXT_FG,
    DARK_TEXT_INSERT,
    FONT_MONO,
    FONT_TITLE,
    THEME_NAME,
    _BOOTSTRAP,
    apply_global_style,
    make_root_window,
    ttk,
)
from windows import DebugWindow, InfoWindow, SettingsWindow, LogWindow


EDIT_COMPAT_CONTROL_TYPES = ("EditControl", "GroupControl")


def _bind_target_types(expected_type: str) -> tuple[str, ...]:
    if expected_type in EDIT_COMPAT_CONTROL_TYPES:
        return EDIT_COMPAT_CONTROL_TYPES
    return (expected_type,)


def _bind_target_label(expected_type: str) -> str:
    if expected_type in EDIT_COMPAT_CONTROL_TYPES:
        return "EditControl/GroupControl"
    return expected_type


class App:
    def __init__(self):
        self.cfg = AppConfig.load("config.json")
        self._normalize_history_path()

        self.root = make_root_window()
        apply_global_style(self.root)

        self.debug_win: DebugWindow | None = None
        self._last_debug_payload: dict | None = None
        self.info_window: InfoWindow | None = None

        self.log_win: LogWindow | None = None
        self._status_log = deque(maxlen=2500)  # 存最近 2500 行，避免无限增长

        self.tk_hwnd = self.root.winfo_id()

        self.settings_store = SettingsStore(self.cfg.settings_path)

        self.history = HistoryStore(
            self.cfg.history_path,
            self.cfg.history_max_messages,
            selected_name=self.cfg.history_selected,
        )

        self._last_auto_history_name: str | None = None

        self._history_refreshing = False
        self.history_status_var = tk.StringVar(value="")

        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        row1 = ttk.Frame(top)
        row1.pack(fill="x")

        ttk.Label(row1, text="QQSafeChat", font=FONT_TITLE).pack(side="left")

        btns = ttk.Frame(row1)
        btns.pack(side="right")

        ttk.Button(btns, text="⚙ 设置", command=self.open_settings).pack(side="right")
        ttk.Button(btns, text="❓ 帮助", command=self.open_help).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(btns, text="ℹ 信息", command=self.open_info).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(btns, text="🐞 Debug", command=self.open_debug).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(btns, text="📝 日志", command=self.open_log).pack(
            side="right", padx=(0, 8)
        )

        row2 = ttk.Frame(top)
        row2.pack(fill="x", pady=(6, 0))

        self.status_var = tk.StringVar(
            value="启动提示：如有上次本地历史，右键聊天区 → 清空。"
        )
        self.status_label = ttk.Label(row2, textvariable=self.status_var)
        self.status_label.pack(side="left", fill="x", expand=True)

        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y")

        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True, padx=(10, 0))

        bind_box = ttk.Labelframe(
            left, text="绑定（按住按钮拖到目标上，松开锁定）", padding=10
        )
        bind_box.pack(fill="x")

        self.highlight = HighlightRect()
        self.picking = False
        self.pick_expected_type: str | None = None
        self.hover_ctrl = None
        self._auto_bind_after_id: str | None = None

        self.bound_edit: BoundControl | None = None
        self.bound_button: BoundControl | None = None
        self.bound_window: BoundControl | None = None
        self.bound_voice_button: BoundControl | None = None

        self.btn_bind_edit = ttk.Button(
            bind_box, text="绑定 输入框 (EditControl/GroupControl)"
        )
        self.btn_bind_btn = ttk.Button(bind_box, text="绑定 发送按钮 (ButtonControl)")
        self.btn_bind_voice = ttk.Button(bind_box, text="绑定 语音消息按钮 (ButtonControl)")
        self.btn_bind_win = ttk.Button(bind_box, text="绑定 消息列表 (WindowControl)")
        self.btn_auto_bind = ttk.Button(
            bind_box, text="尝试自动绑定（点击后切到聊天窗）", command=self.schedule_auto_bind
        )

        self.btn_bind_edit.pack(fill="x", pady=4)
        self.btn_bind_btn.pack(fill="x", pady=4)
        self.btn_bind_voice.pack(fill="x", pady=4)
        self.btn_bind_win.pack(fill="x", pady=4)
        self.btn_auto_bind.pack(fill="x", pady=(4, 0))

        self.bind_state_var = tk.StringVar(
            value="Edit: 未绑定 | Button: 未绑定 | Voice: 未绑定 | Window: 未绑定"
        )
        ttk.Label(bind_box, textvariable=self.bind_state_var).pack(
            fill="x", pady=(8, 0)
        )

        ctrl_box = ttk.Labelframe(left, text="控制", padding=10)
        ctrl_box.pack(fill="x", pady=(10, 0))

        self.auto_reply_var = tk.BooleanVar(value=bool(self.cfg.auto_reply_enabled))
        ttk.Checkbutton(
            ctrl_box,
            text="自动回复",
            variable=self.auto_reply_var,
            command=self.on_toggle_auto_reply,
        ).pack(anchor="w")

        row = ttk.Frame(ctrl_box)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="本地保存条数：").pack(side="left")
        self.keep_var = tk.IntVar(value=self.cfg.history_max_messages)
        ttk.Spinbox(
            row,
            from_=20,
            to=500,
            textvariable=self.keep_var,
            width=6,
            command=self.on_change_keep,
        ).pack(side="left")
        ttk.Label(row, text="（保存到本地的历史，不是 UIA 全量）").pack(
            side="left", padx=6
        )

        row2c = ttk.Frame(ctrl_box)
        row2c.pack(fill="x", pady=(8, 0))
        self.btn_start = ttk.Button(
            row2c, text="▶ 启动", command=self.start_bot, state="disabled"
        )
        self.btn_stop = ttk.Button(
            row2c, text="⏸ 停止", command=self.stop_bot, state="disabled"
        )
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_stop.pack(side="left", fill="x", expand=True)

        history_box = ttk.Labelframe(left, text="History 管理", padding=10)
        history_box.pack(fill="both", pady=(10, 0))

        self.auto_history_var = tk.BooleanVar(value=bool(self.cfg.auto_history_by_bind))
        ttk.Checkbutton(
            history_box,
            text="绑定 Edit 后，按 Name 自动载入/创建历史（如需手动选择历史必须取消勾选）",
            variable=self.auto_history_var,
            command=self.on_toggle_auto_history,
        ).pack(anchor="w", pady=(0, 6))

        list_wrap = ttk.Frame(history_box)
        list_wrap.pack(fill="both")

        self.history_list = tk.Listbox(list_wrap, height=7, exportselection=False)
        self.history_list.pack(side="left", fill="both", expand=True)
        self.history_list.bind("<<ListboxSelect>>", self.on_history_select)

        hist_scroll = ttk.Scrollbar(
            list_wrap, orient="vertical", command=self.history_list.yview
        )
        hist_scroll.pack(side="right", fill="y")
        self.history_list.configure(yscrollcommand=hist_scroll.set)

        btn_row = ttk.Frame(history_box)
        btn_row.pack(fill="x", pady=(6, 0))
        ttk.Button(btn_row, text="新增", command=self.on_history_add).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(btn_row, text="重命名", command=self.on_history_rename).pack(
            side="left", fill="x", expand=True, padx=4
        )
        ttk.Button(btn_row, text="删除", command=self.on_history_delete).pack(
            side="left", fill="x", expand=True
        )

        btn_row2 = ttk.Frame(history_box)
        btn_row2.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row2, text="编辑", command=self.on_history_edit).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            btn_row2, text="刷新", command=lambda: self.refresh_history_list()
        ).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(btn_row2, text="清空当前", command=self.clear_history).pack(
            side="left", fill="x", expand=True
        )

        ttk.Label(history_box, textvariable=self.history_status_var).pack(
            fill="x", pady=(6, 0)
        )

        if _BOOTSTRAP and THEME_NAME in ("darkly", "superhero", "cyborg"):
            self.text = tk.Text(
                right,
                wrap="word",
                font=FONT_MONO,
                bg=DARK_TEXT_BG,
                fg=DARK_TEXT_FG,
                insertbackground=DARK_TEXT_INSERT,
                relief="flat",
                highlightthickness=1,
                highlightbackground="#2a2a2a",
            )
        else:
            self.text = tk.Text(right, wrap="word", font=FONT_MONO)
        self.text.pack(fill="both", expand=True)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="清空", command=self.clear_history)
        self.text.bind("<Button-3>", self.on_right_click)

        self.refresh_history_list(select_name=self.history.current_name)
        if self.history.items:
            self.set_status("检测到上次本地历史：右键聊天区 → 清空，可重置对齐。")

        self.llm = self.make_llm_client(self.settings_store.settings)

        try:
            self.llm.set_debug_hook(self.on_llm_debug)
        except Exception:
            pass

        self.engine = BotEngine(
            cfg=self.cfg,
            history=self.history,
            llm=self.llm,
            tk_hwnd=self.tk_hwnd,
            ui_log=self.set_log,
            ui_status=self.set_status,
        )
        self.engine.set_auto_reply(bool(self.auto_reply_var.get()))

        self.btn_bind_edit.bind(
            "<ButtonPress-1>", lambda e: self.start_pick("EditControl", "输入框")
        )
        self.btn_bind_edit.bind(
            "<ButtonRelease-1>", lambda e: self.stop_pick_and_bind("edit", "EditControl", "输入框")
        )

        self.btn_bind_btn.bind(
            "<ButtonPress-1>", lambda e: self.start_pick("ButtonControl", "发送按钮")
        )
        self.btn_bind_btn.bind(
            "<ButtonRelease-1>", lambda e: self.stop_pick_and_bind("send_button", "ButtonControl", "发送按钮")
        )

        self.btn_bind_voice.bind(
            "<ButtonPress-1>", lambda e: self.start_pick("ButtonControl", "语音消息按钮")
        )
        self.btn_bind_voice.bind(
            "<ButtonRelease-1>", lambda e: self.stop_pick_and_bind("voice_button", "ButtonControl", "语音消息按钮")
        )

        self.btn_bind_win.bind(
            "<ButtonPress-1>", lambda e: self.start_pick("WindowControl", "消息列表")
        )
        self.btn_bind_win.bind(
            "<ButtonRelease-1>", lambda e: self.stop_pick_and_bind("window", "WindowControl", "消息列表")
        )

        self.root.after(30, self.pick_loop)
        self.root.after(self.cfg.poll_ms, self.bot_loop)

        self.root.mainloop()

    def open_log(self):
        if self.log_win and self.log_win.winfo_exists():
            try:
                self.log_win.lift()
            except Exception:
                pass
            return

        def _on_clear():
            self._status_log.clear()

        def _on_close():
            self.log_win = None

        self.log_win = LogWindow(
            self.root,
            initial_lines=list(self._status_log),
            on_clear=_on_clear,
            on_close=_on_close,
        )
        try:
            self.log_win.lift()
        except Exception:
            pass

    def _push_status_log_line(self, line: str):
        self._status_log.append(line)
        if self.log_win and self.log_win.winfo_exists():
            try:
                self.log_win.append_line(line)
            except Exception:
                pass

    def _normalize_history_path(self):
        raw_path = self.cfg.history_path or "history/history.jsonl"
        filename = os.path.basename(raw_path) or "history.jsonl"
        target_dir = "history"
        normalized = os.path.join(target_dir, filename)

        if normalized != raw_path:
            self._move_history_if_needed(raw_path, normalized)
            self.cfg.history_path = normalized
            self.cfg.save(self.cfg.config_path)
        else:
            os.makedirs(target_dir, exist_ok=True)

    def _move_history_if_needed(self, old_path: str, new_path: str):
        if not old_path or old_path == new_path:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            return

        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        if not os.path.exists(old_path):
            return

        try:
            shutil.move(old_path, new_path)
        except Exception:
            pass

    def open_help(self):
        docs_dir = Path.cwd() / "docs"
        index = docs_dir / "index.html"
        if not index.exists():
            messagebox.showerror("错误", f"未找到帮助文档：{index}")
            return

        try:
            port = _start_docs_server(docs_dir)
            url = f"http://127.0.0.1:{port}/index.html"

            subprocess.Popen(
                [sys.executable, "help_viewer.py", url],
                close_fds=True,
            )
        except Exception as exc:
            messagebox.showerror("错误", f"无法打开帮助窗口：{exc}")

    def open_info(self):
        win = getattr(self, "info_window", None)
        if win and win.winfo_exists():
            win.lift()
            return
        self.info_window = InfoWindow(self.root, self.cfg)

    def open_settings(self):
        SettingsWindow(
            self.root,
            self.settings_store,
            self.cfg,
            self.apply_settings,
            self.apply_cfg_settings,
        )

    def apply_settings(self, s: OpenAISettings):
        self.llm = self.make_llm_client(s)
        if hasattr(self.llm, "set_debug_hook"):
            self.llm.set_debug_hook(self.on_llm_debug)
        self.engine.set_llm_client(self.llm)
        self.set_status(
            f"✅ 设置已应用：provider={s.provider}, model={s.model}, temp={s.temperature}"
        )

    def apply_cfg_settings(self, cfg: AppConfig):
        self.cfg.save(self.cfg.config_path)
        self.set_status(
            f"✅ 行为/人格设置已应用：delay={cfg.reply_stop_seconds}s mode={cfg.reply_delay_mode}, "
            f"speedx={cfg.split_speed_multiplier}, persona={cfg.persona_file or '无'}, "
            f"sticker={'开' if cfg.sticker_selector_enabled else '关'}, "
            f"vision={'开' if cfg.vision_enabled else '关'}, tts={'开' if cfg.tts_enabled else '关'}, voice_mode={cfg.voice_send_mode}"
        )

    def make_llm_client(self, s: OpenAISettings):
        provider = (s.provider or "").strip().lower()
        if provider == "openai":
            return OpenAIClient(
                api_key=s.api_key,
                base_url=s.base_url,
                model=s.model,
                temperature=s.temperature,
                system_prompt=s.system_prompt,
                user_template=s.user_template,
                vision_model=s.vision_model,
                vision_prompt=s.vision_prompt,
                tts_provider=s.tts_provider,
                tts_model=s.tts_model,
                tts_voice=s.tts_voice,
                tts_format=s.tts_format,
                tts_language_type=s.tts_language_type,
                tts_api_key=s.tts_api_key,
                tts_base_url=s.tts_base_url,
            )
        elif provider == "siliconflow":
            return SiliconFlowClient(
                api_key=s.api_key,
                base_url=s.base_url,
                model=s.model,
                temperature=s.temperature,
                system_prompt=s.system_prompt,
                user_template=s.user_template,
                vision_model=s.vision_model,
                vision_prompt=s.vision_prompt,
                tts_provider=s.tts_provider,
                tts_model=s.tts_model,
                tts_voice=s.tts_voice,
                tts_format=s.tts_format,
                tts_language_type=s.tts_language_type,
                tts_api_key=s.tts_api_key,
                tts_base_url=s.tts_base_url,
            )
        return MockLLMClient()

    def open_debug(self):
        dbg = getattr(self, "debug_win", None)
        if dbg and dbg.winfo_exists():
            dbg.lift()
            return
        self.debug_win = DebugWindow(self.root)
        if getattr(self, "_last_debug_payload", None):
            self.debug_win.update_debug(self._last_debug_payload)

    def on_llm_debug(self, data: dict):
        self._last_debug_payload = data

        def _apply():
            if self.debug_win and self.debug_win.winfo_exists():
                self.debug_win.update_debug(data)

        try:
            self.root.after(0, _apply)
        except Exception:
            pass

    def set_status(self, msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
        line = f"[{ts}] {msg}"

        def _():
            self.status_var.set(msg)
            self._push_status_log_line(line)

        try:
            self.root.after(0, _)
        except Exception:
            self.status_var.set(msg)
            self._push_status_log_line(line)

    def set_log(self, text: str):
        def _():
            self.text.delete("1.0", tk.END)
            self.text.insert(tk.END, text)

        try:
            self.root.after(0, _)
        except Exception:
            self.text.delete("1.0", tk.END)
            self.text.insert(tk.END, text)

    def update_bind_state(self):
        def fmt(b: BoundControl | None):
            return "✅" if b else "未绑定"

        self.bind_state_var.set(
            f"Edit: {fmt(self.bound_edit)} | Button: {fmt(self.bound_button)} | Voice: {fmt(self.bound_voice_button)} | Window: {fmt(self.bound_window)}"
        )
        self.engine.bound_edit = self.bound_edit
        self.engine.bound_button = self.bound_button
        self.engine.bound_voice_button = self.bound_voice_button
        self.engine.bound_window = self.bound_window

        ready = self.engine.is_ready()
        self.btn_start.configure(state=("normal" if ready else "disabled"))

    def start_pick(self, expected_type: str, target_name: str):
        self.picking = True
        self.pick_expected_type = expected_type
        win32api.SetCursor(win32gui.LoadCursor(0, win32con.IDC_CROSS))
        self.set_status(
            f"正在拾取：移到目标 {target_name}（{_bind_target_label(expected_type)}）上，松开锁定…"
        )

    def stop_pick_and_bind(self, bind_slot: str, expected_type: str, target_name: str):
        self.picking = False
        win32api.SetCursor(win32gui.LoadCursor(0, win32con.IDC_ARROW))
        self.highlight.hide()

        ctrl = self.hover_ctrl
        self.hover_ctrl = None

        if not ctrl:
            self.set_status("未选中控件")
            return

        # 尝试查找指定类型的控件，如果当前控件类型不匹配，则查找其子控件
        matched_ctrl = ctrl
        target_types = _bind_target_types(expected_type)
        target_label = _bind_target_label(expected_type)
        try:
            ctrl_type = getattr(ctrl, "ControlTypeName", "") or ""
            if ctrl_type not in target_types:
                # 从uia_picker导入find_child_control_by_type函数
                from uia.uia_picker import find_child_control_by_type

                preferred_name = "语音消息" if bind_slot == "voice_button" else ""
                matched_ctrl = find_child_control_by_type(ctrl, expected_type, preferred_name=preferred_name)
                if not matched_ctrl:
                    self.set_status(
                        f"选中的不是 {target_label}，而是 {ctrl_type or '未知控件'}"
                    )
                    return
        except Exception as e:
            self.set_status(f"控件类型读取失败: {e}")
            return

        bound = build_bound_control(matched_ctrl, expected_type)
        if not bound:
            self.set_status("绑定失败（无法获取 rect）")
            return

        if bind_slot == "edit":
            self.bound_edit = bound
        elif bind_slot == "send_button":
            self.bound_button = bound
        elif bind_slot == "voice_button":
            self.bound_voice_button = bound
        elif bind_slot == "window":
            self.bound_window = bound

        actual_type = bound.actual_type or bound.expected_type
        if bind_slot == "edit":
            self.set_status(f"✅ 已绑定 输入框控件（{actual_type}）")
        elif bind_slot == "send_button":
            self.set_status(
                f"✅ 已绑定 发送按钮（名称={bound.name or '无名'} | 实际={actual_type}）"
            )
        elif bind_slot == "voice_button":
            self.set_status(
                f"✅ 已绑定 语音消息按钮（名称={bound.name or '无名'} | 实际={actual_type}）"
            )
        else:
            self.set_status(f"✅ 已绑定 {target_name}（实际={actual_type}）")
        if bind_slot == "edit" and self.auto_history_var.get():
            self.auto_load_history_for_bound_edit()
        self.update_bind_state()

    def schedule_auto_bind(self):
        if self._auto_bind_after_id is not None:
            try:
                self.root.after_cancel(self._auto_bind_after_id)
            except Exception:
                pass

        self.set_status("3 秒内切到目标 QQ 聊天窗口，程序将尝试自动绑定…")
        self._auto_bind_after_id = self.root.after(3000, self.auto_bind_foreground_window)

    def auto_bind_foreground_window(self):
        self._auto_bind_after_id = None

        try:
            app_root_hwnd = win32gui.GetAncestor(self.tk_hwnd, win32con.GA_ROOT)
        except Exception:
            app_root_hwnd = self.tk_hwnd

        try:
            fg_hwnd = win32gui.GetForegroundWindow()
        except Exception as exc:
            self.set_status(f"自动绑定失败：无法获取前台窗口 ({exc})")
            return

        if not fg_hwnd or fg_hwnd == app_root_hwnd:
            self.set_status("自动绑定失败：请点击按钮后立即切到目标 QQ 聊天窗口。")
            return

        try:
            title = win32gui.GetWindowText(fg_hwnd) or "未知窗口"
        except Exception:
            title = "未知窗口"

        try:
            with auto.UIAutomationInitializerInThread():
                root_ctrl = pick_chat_bind_root(fg_hwnd)
                detected = auto_detect_chat_bindings(root_ctrl) if root_ctrl else {}
        except Exception as exc:
            self.set_status(f"自动绑定失败：扫描窗口 {title} 时出错 ({exc})")
            return

        edit_bound = build_bound_control(detected.get("edit"), "EditControl") if detected.get("edit") else None
        button_bound = build_bound_control(detected.get("button"), "ButtonControl") if detected.get("button") else None
        voice_button_bound = build_bound_control(detected.get("voice_button"), "ButtonControl") if detected.get("voice_button") else None
        window_bound = build_bound_control(detected.get("window"), "WindowControl") if detected.get("window") else None

        if button_bound:
            button_blob = " ".join(
                [
                    button_bound.name or "",
                    button_bound.automation_id or "",
                    button_bound.class_name or "",
                ]
            ).lower()
            if any(k in button_blob for k in ("关闭", "close", "最小化", "minimize", "最大化", "maximize", "还原", "restore")):
                self.set_status(
                    f"自动绑定失败：识别到的按钮疑似标题栏按钮（{button_bound.name or button_bound.class_name or '未知按钮'}），已拒绝自动绑定，请手动绑定发送按钮。"
                )
                return

        if not (edit_bound and button_bound and window_bound):
            missing = []
            if not edit_bound:
                missing.append("输入框")
            if not button_bound:
                missing.append("发送按钮")
            if not window_bound:
                missing.append("消息列表")
            self.set_status(
                f"自动绑定失败：前台窗口 {title} 未识别出 {'/'.join(missing)}，请继续手动绑定。"
            )
            return

        self.bound_edit = edit_bound
        self.bound_button = button_bound
        self.bound_voice_button = voice_button_bound
        self.bound_window = window_bound
        self.update_bind_state()

        if self.auto_history_var.get():
            self.auto_load_history_for_bound_edit()

        self.set_status(
            f"✅ 已自动绑定前台窗口：{title} | 输入={edit_bound.actual_type or edit_bound.expected_type} | 发送={button_bound.name or '无名'} | 语音={voice_button_bound.name if voice_button_bound else '未识别'} | 列表={window_bound.actual_type or window_bound.expected_type}"
        )

    def pick_loop(self):
        if self.picking and self.pick_expected_type:
            px, py = win32api.GetCursorPos()
            ctrl = control_from_point_safe(px, py, self.tk_hwnd)
            if ctrl:
                try:
                    self.hover_ctrl = ctrl
                    self.highlight.show_rect(ctrl.BoundingRectangle)
                except Exception:
                    pass
        self.root.after(30, self.pick_loop)

    def on_toggle_auto_reply(self):
        self.engine.set_auto_reply(bool(self.auto_reply_var.get()))
        self.cfg.auto_reply_enabled = bool(self.auto_reply_var.get())
        self.cfg.save(self.cfg.config_path)

    def on_change_keep(self):
        v = int(self.keep_var.get())
        self.cfg.history_max_messages = v
        self.history.max_messages = v
        self.history.save()
        self.cfg.save(self.cfg.config_path)
        self.set_status(f"本地保存条数已设置为 {v}")

    def start_bot(self):
        self.update_bind_state()
        self.engine.start()
        if self.engine.running:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            if bool(self.cfg.tts_enabled) and str(self.cfg.voice_send_mode or "file").strip().lower() == "real" and not self.bound_voice_button:
                self.set_status("⚠️ 当前为真语音模式，但还没有绑定“语音消息”按钮，voice action 会失败")

    def stop_bot(self):
        self.engine.stop()
        self.btn_start.configure(
            state=("normal" if self.engine.is_ready() else "disabled")
        )
        self.btn_stop.configure(state="disabled")

    def bot_loop(self):
        try:
            self.poll_auto_history_from_edit()
            self.engine.step()
        except Exception as e:
            self.set_status(f"运行异常：{e}")
        self.root.after(self.cfg.poll_ms, self.bot_loop)

    def update_history_status(self, extra: str | None = None):
        name = self.history.current_name
        count = len(self.history.items)
        msg = f"当前历史：{name}（{count} 条）"
        if extra:
            msg += f" | {extra}"
        self.history_status_var.set(msg)

    def refresh_history_list(self, select_name: str | None = None):
        names = self.history.list_histories()
        target = select_name or self.history.current_name
        self._history_refreshing = True
        try:
            self.history_list.delete(0, tk.END)
            for n in names:
                self.history_list.insert(tk.END, n)
            if target:
                for idx, n in enumerate(names):
                    if n == target:
                        self.history_list.selection_clear(0, tk.END)
                        self.history_list.selection_set(idx)
                        self.history_list.see(idx)
                        break
        finally:
            self._history_refreshing = False
        self.update_history_status()

    def on_toggle_auto_history(self):
        self.cfg.auto_history_by_bind = bool(self.auto_history_var.get())
        self.cfg.save(self.cfg.config_path)

    def _get_selected_history(self) -> str | None:
        sel = self.history_list.curselection()
        if sel:
            return self.history_list.get(sel[0])
        return self.history.current_name

    def on_history_select(self, _event=None):
        if self._history_refreshing:
            return
        name = self._get_selected_history()
        if name:
            self.select_history(name)

    def select_history(self, name: str):
        actual = self.history.switch(name)
        self.cfg.history_selected = actual
        self.cfg.save(self.cfg.config_path)
        self.refresh_history_list(select_name=actual)
        self.set_status(f"📜 已切换到历史：{actual}")

    def on_history_add(self):
        name = simpledialog.askstring("新增 History", "输入名称：", parent=self.root)
        if name is None:
            return
        created = self.history.create(name)
        if not created:
            messagebox.showerror("错误", "创建失败：名称为空或已存在。")
            return
        self.select_history(created)
        self.set_status(f"✅ 已新增历史：{created}")

    def on_history_rename(self):
        current = self._get_selected_history()
        if not current:
            return
        new_name = simpledialog.askstring(
            "重命名 History", "输入新名称：", initialvalue=current, parent=self.root
        )
        if not new_name or new_name == current:
            return
        if self.history.rename(current, new_name):
            self.select_history(self.history.current_name)
            self.set_status(f"✅ 已重命名为：{new_name}")
        else:
            messagebox.showerror("错误", "重命名失败：名称冲突或文件不存在。")

    def on_history_delete(self):
        current = self._get_selected_history()
        if not current:
            return
        if not messagebox.askyesno(
            "删除确认", f"确定删除历史 {current} 吗？这是你与那个虚拟的TA的回忆。", parent=self.root
        ):
            return
        if self.history.delete(current):
            self.cfg.history_selected = self.history.current_name
            self.cfg.save(self.cfg.config_path)
            self.refresh_history_list(select_name=self.history.current_name)
            self.set_status(f"🗑️ 已删除历史：{current}")
        else:
            messagebox.showerror("错误", "删除失败：文件不存在或被占用。")

    def on_history_edit(self):
        name = self._get_selected_history()
        if not name:
            return
        editor = tk.Toplevel(self.root)
        editor.title(f"编辑历史：{name}")
        editor.geometry("800x520")

        info = ttk.Label(
            editor,
            text="每行一个 JSON 对象，包含 sender/text/ts。错误行会导致保存失败。",
        )
        info.pack(fill="x", padx=10, pady=(10, 0))

        text = tk.Text(editor, wrap="word", font=FONT_MONO)
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", self.history.export_raw_text(name))

        def _save():
            content = text.get("1.0", tk.END)
            if self.history.import_raw_text(content, name):
                self.select_history(name)
                editor.destroy()
                self.set_status("✅ 历史内容已保存")
            else:
                messagebox.showerror("错误", "保存失败：请检查 JSON 格式。")

        ttk.Button(editor, text="保存", command=_save).pack(pady=(0, 10))

    def auto_load_history_for_bound_edit(self):
        if not self.bound_edit:
            return
        name = (self.bound_edit.name or "").strip()
        if not name:
            self.set_status("⚠️ Edit 的 Name 为空，无法自动匹配历史")
            return
        actual = self.history.ensure_history(name)

        self._last_auto_history_name = actual

        self.cfg.history_selected = actual
        self.cfg.save(self.cfg.config_path)
        self.refresh_history_list(select_name=actual)
        self.set_status(f"✅ 已根据 Edit Name 载入/创建历史：{actual}")

    def poll_auto_history_from_edit(self):
        if not self.auto_history_var.get() or not self.bound_edit:
            return
        try:
            with auto.UIAutomationInitializerInThread():
                ctrl = reacquire(self.bound_edit, self.tk_hwnd)
        except Exception:
            return
        if not ctrl:
            return
        try:
            name = (getattr(ctrl, "Name", "") or "").strip()
        except Exception:
            return
        if not name:
            return
        if name == self._last_auto_history_name and name == self.history.current_name:
            return

        actual = self.history.ensure_history(name)
        self._last_auto_history_name = actual
        self.cfg.history_selected = actual
        self.cfg.save(self.cfg.config_path)
        self.refresh_history_list(select_name=actual)
        self.set_status(f"✅ 已根据 Edit Name 自动切换/创建历史：{actual}")

    def clear_history(self):
        self.history.clear()
        try:
            if hasattr(self.engine, "_last_snapshot_sigs"):
                self.engine._last_snapshot_sigs = []
            if hasattr(self.engine, "_last_other_sig"):
                self.engine._last_other_sig = ""
            if hasattr(self.engine, "_baseline_taken"):
                self.engine._baseline_taken = False
            if hasattr(self.engine, "_pending_incoming"):
                self.engine._pending_incoming = []
            if hasattr(self.engine, "_pending_reply_at"):
                self.engine._pending_reply_at = 0.0
        except Exception:
            pass

        self.refresh_history_list()
        self.set_status("🧹 已清空本地历史（建议重新对齐后再启动）")

    def on_right_click(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()


if __name__ == "__main__":
    App()
