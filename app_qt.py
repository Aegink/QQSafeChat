import sys
import os
import shutil
import time
import subprocess
from pathlib import Path
from collections import deque
import tkinter as tk  # Still needed for the background tk_root loop

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QCheckBox, QSpinBox,
    QListWidget, QTextEdit, QSplitter, QAbstractItemView,
    QStackedWidget, QScrollArea, QFrame, QInputDialog, QMessageBox,
    QSizePolicy
)
from PyQt6.QtCore import QTimer, Qt, QSize
from PyQt6.QtGui import QFont, QTextCursor

import win32api
import win32con
import win32gui
import uiautomation as auto

from core.bot_engine import BotEngine
from storage.config import AppConfig
from storage.history_store import HistoryStore
from core.llm_client import MockLLMClient, OpenAIClient, SiliconFlowClient
from storage.settings_store import OpenAISettings, SettingsStore

from uia.uia_picker import (
    HighlightRect,
    auto_detect_chat_bindings,
    build_bound_control,
    control_from_point_safe,
    pick_chat_bind_root,
    reacquire,
    find_child_control_by_type
)
from core.models import BoundControl

from windows.help_launcher import _start_docs_server
from windows_qt.settings_window import SettingsWindowQt
from windows_qt.log_window import LogWindowQt
from windows_qt.info_window import InfoWindowQt
from windows_qt.debug_window import DebugWindowQt
from windows_qt.ui_theme import (
    STYLESHEET, make_accent_btn, make_danger_btn,
    SIDEBAR_BG, SIDEBAR_FG, SIDEBAR_FG_HOVER, SIDEBAR_FG_ACTIVE, SIDEBAR_ACTIVE_BG,
    BG_SIDEBAR, BG_CARD, BG_WINDOW, BG_INPUT, BG_HOVER, BG_SELECTED,
    BORDER, ACCENT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    SUCCESS, DANGER,
    NavButton,
)

EDIT_COMPAT_CONTROL_TYPES = ("EditControl", "GroupControl")


def _bind_target_types(expected_type: str) -> tuple[str, ...]:
    if expected_type in EDIT_COMPAT_CONTROL_TYPES:
        return EDIT_COMPAT_CONTROL_TYPES
    return (expected_type,)


def _bind_target_label(expected_type: str) -> str:
    if expected_type in EDIT_COMPAT_CONTROL_TYPES:
        return "EditControl/GroupControl"
    return expected_type


# ── Bind Status Row ─────────────────────────────────────────────────────────
class BindCard(QWidget):
    """A single row showing one bind-target and its current status."""

    def __init__(self, icon: str, name: str, desc: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        self.name_lbl = QLabel(name)
        self.name_lbl.setStyleSheet(
            f"color:{TEXT_PRIMARY}; font-size:13px; background:transparent;"
        )
        h.addWidget(self.name_lbl)

        self.desc_lbl = QLabel(desc)
        self.desc_lbl.setStyleSheet(
            f"color:{TEXT_MUTED}; font-size:11px; background:transparent;"
        )
        h.addWidget(self.desc_lbl)
        h.addStretch()

        self._status = QLabel("未绑定")
        self._status.setStyleSheet(
            f"font-size:12px; color:{TEXT_MUTED}; background:transparent;"
        )
        h.addWidget(self._status)

    def set_bound(self, ok: bool):
        if ok:
            self._status.setText("已绑定")
            self._status.setStyleSheet(
                f"font-size:12px; color:{SUCCESS}; font-weight:600; background:transparent;"
            )
        else:
            self._status.setText("未绑定")
            self._status.setStyleSheet(
                f"font-size:12px; color:{TEXT_MUTED}; background:transparent;"
            )


# ── Bot Status Widget ───────────────────────────────────────────────────────
class BotStatusWidget(QWidget):
    """Compact running-state indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(14)
        self._dot.setStyleSheet(f"font-size:10px; color:{TEXT_MUTED}; background:transparent;")
        h.addWidget(self._dot)

        v = QVBoxLayout()
        v.setSpacing(2)
        self._title = QLabel("已停止")
        self._title.setStyleSheet(
            f"font-size:14px; font-weight:600; color:{TEXT_PRIMARY}; background:transparent;"
        )
        self._sub = QLabel("绑定控件后点击启动")
        self._sub.setStyleSheet(
            f"font-size:12px; color:{TEXT_SECONDARY}; background:transparent;"
        )
        v.addWidget(self._title)
        v.addWidget(self._sub)
        h.addLayout(v)
        h.addStretch()

    def set_running(self, running: bool):
        if running:
            self._dot.setStyleSheet(f"font-size:10px; color:{SUCCESS}; background:transparent;")
            self._title.setText("运行中")
            self._title.setStyleSheet(
                f"font-size:14px; font-weight:600; color:{SUCCESS}; background:transparent;"
            )
            self._sub.setText("正在监听消息，自动生成回复")
        else:
            self._dot.setStyleSheet(f"font-size:10px; color:{TEXT_MUTED}; background:transparent;")
            self._title.setText("已停止")
            self._title.setStyleSheet(
                f"font-size:14px; font-weight:600; color:{TEXT_PRIMARY}; background:transparent;"
            )
            self._sub.setText("绑定控件后点击启动")
class AppQt(QMainWindow):
    _PAGE_TITLES = ["绑定控件", "机器人控制", "历史管理"]

    def __init__(self):
        super().__init__()

        # Shared Tkinter Root
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()
        self.tk_root.report_callback_exception = lambda exc, val, tb: print(f"Tk Error: {exc} {val}")

        self.tk_timer = QTimer(self)
        self.tk_timer.timeout.connect(self._update_tk)
        self.tk_timer.start(20)

        self.setWindowTitle("QQSafeChat")
        self.resize(980, 680)
        self.setMinimumSize(800, 520)

        # Core logic
        self.cfg = AppConfig.load("config.json")
        self._normalize_history_path()
        self.settings_store = SettingsStore(self.cfg.settings_path)
        self.history = HistoryStore(
            self.cfg.history_path,
            self.cfg.history_max_messages,
            selected_name=self.cfg.history_selected,
        )

        self._last_auto_history_name = None
        self._history_refreshing = False

        self.debug_win = None
        self._last_debug_payload = None
        self.info_window = None
        self.log_win = None
        self._status_log = deque(maxlen=2500)

        self.tk_hwnd = self.tk_root.winfo_id()
        self.highlight = HighlightRect()
        self.picking = False
        self.pick_expected_type = None
        self.hover_ctrl = None
        self._pick_slot = None
        self._pick_name = ""
        self._auto_bind_timer_id = None

        self.bound_edit = None
        self.bound_button = None
        self.bound_voice_button = None
        self.bound_window = None

        self._init_ui()

        self.refresh_history_list(select_name=self.history.current_name)
        if self.history.items:
            self.set_status("检测到上次本地历史：可在历史页清空重置对齐。")

        self.llm = self.make_llm_client(self.settings_store.settings)
        if hasattr(self.llm, "set_debug_hook"):
            self.llm.set_debug_hook(self.on_llm_debug)

        self.engine = BotEngine(
            cfg=self.cfg,
            history=self.history,
            llm=self.llm,
            tk_hwnd=self.tk_hwnd,
            ui_log=self.set_log,
            ui_status=self.set_status,
        )
        self.engine.set_auto_reply(self.check_auto_reply.isChecked())

        self.pick_timer = QTimer(self)
        self.pick_timer.timeout.connect(self.pick_loop)
        self.pick_timer.start(30)

        self.bot_timer = QTimer(self)
        self.bot_timer.timeout.connect(self.bot_loop)
        self.bot_timer.start(self.cfg.poll_ms)

    def _update_tk(self):
        try:
            self.tk_root.update()
        except Exception:
            pass

    # ── UI Construction ──────────────────────────────────────────────────────
    def _init_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        root_h = QHBoxLayout(root)
        root_h.setContentsMargins(0, 0, 0, 0)
        root_h.setSpacing(0)

        root_h.addWidget(self._build_sidebar())
        root_h.addWidget(self._build_main_area(), 1)

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(182)
        sidebar.setStyleSheet(f"background:{SIDEBAR_BG};")

        v = QVBoxLayout(sidebar)
        v.setContentsMargins(8, 0, 8, 0)
        v.setSpacing(1)

        # App title
        title_w = QWidget()
        title_w.setFixedHeight(56)
        title_w.setStyleSheet("background:transparent;")
        th = QHBoxLayout(title_w)
        th.setContentsMargins(6, 0, 4, 0)
        title_lbl = QLabel("QQSafeChat")
        title_lbl.setStyleSheet(
            "color:#ffffff; font-size:15px; font-weight:700; background:transparent;"
        )
        th.addWidget(title_lbl)
        th.addStretch()
        v.addWidget(title_w)

        # Nav section
        v.addWidget(self._sidebar_section("导航"))
        v.addSpacing(2)

        self.nav_bind = NavButton("  绑定控件")
        self.nav_ctrl = NavButton("  机器人控制")
        self.nav_hist = NavButton("  历史管理")
        self._nav_btns = [self.nav_bind, self.nav_ctrl, self.nav_hist]

        for i, btn in enumerate(self._nav_btns):
            btn.clicked.connect(lambda _, idx=i: self._switch_page(idx))
            v.addWidget(btn)

        v.addStretch()

        # Divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet("background:rgba(255,255,255,0.08); border:none; margin:0;")
        v.addWidget(div)

        v.addSpacing(4)
        v.addWidget(self._sidebar_section("工具"))
        v.addSpacing(2)

        for label, cb in [
            ("  设置", self.open_settings),
            ("  帮助", self.open_help),
            ("  关于", self.open_info),
            ("  调试", self.open_debug),
            ("  日志", self.open_log),
        ]:
            v.addWidget(self._sidebar_tool_btn(label, cb))

        v.addSpacing(10)
        return sidebar

    def _sidebar_section(self, text: str) -> QLabel:
        lbl = QLabel(f"  {text.upper()}")
        lbl.setFixedHeight(18)
        lbl.setStyleSheet(
            "color:#52525b; font-size:10px; font-weight:600; letter-spacing:0.5px; background:transparent;"
        )
        return lbl

    def _sidebar_tool_btn(self, label: str, cb) -> QPushButton:
        btn = QPushButton(label)
        btn.setFixedHeight(34)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {SIDEBAR_FG};
                border: none;
                border-radius: 6px;
                text-align: left;
                padding: 0 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.08);
                color: {SIDEBAR_FG_HOVER};
            }}
        """)
        btn.clicked.connect(cb)
        return btn

    # ── Main Area ─────────────────────────────────────────────────────────────
    def _build_main_area(self) -> QWidget:
        area = QWidget()
        area.setStyleSheet(f"background:{BG_WINDOW};")

        v = QVBoxLayout(area)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(self._build_topbar())

        hdiv = QFrame()
        hdiv.setFixedHeight(1)
        hdiv.setStyleSheet(f"background:{BORDER}; border:none;")
        v.addWidget(hdiv)

        self._main_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.setChildrenCollapsible(False)

        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(self._build_page_bind())
        self.page_stack.addWidget(self._build_page_ctrl())
        self.page_stack.addWidget(self._build_page_hist())
        self._main_splitter.addWidget(self.page_stack)

        self._main_splitter.addWidget(self._build_log_panel())
        self._main_splitter.setSizes([480, 160])
        self._main_splitter.setCollapsible(1, True)

        v.addWidget(self._main_splitter, 1)
        self._switch_page(0)
        return area

    # ── Top Bar ───────────────────────────────────────────────────────────────
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet(f"background:{BG_WINDOW};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 16, 0)
        h.setSpacing(12)

        self._page_title = QLabel("绑定控件")
        self._page_title.setStyleSheet(
            f"font-size:15px; font-weight:700; color:{TEXT_PRIMARY}; background:transparent;"
        )
        h.addWidget(self._page_title)
        h.addStretch()

        self._status_lbl = QLabel("就绪")
        self._status_lbl.setStyleSheet(
            f"font-size:12px; color:{TEXT_SECONDARY}; background:transparent;"
        )
        self._status_lbl.setMaximumWidth(340)
        h.addWidget(self._status_lbl)

        self._btn_toggle_log = QPushButton("日志")
        self._btn_toggle_log.setFixedSize(48, 28)
        self._btn_toggle_log.setCheckable(True)
        self._btn_toggle_log.setChecked(True)
        self._btn_toggle_log.setStyleSheet(f"""
            QPushButton {{
                background: {BG_CARD};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background:{BG_HOVER}; color:{TEXT_PRIMARY}; }}
            QPushButton:checked {{
                background: {BG_SELECTED};
                color: {ACCENT};
                border-color: #99ccff;
            }}
        """)
        self._btn_toggle_log.toggled.connect(self._toggle_log_panel)
        h.addWidget(self._btn_toggle_log)
        return bar

    def _toggle_log_panel(self, visible: bool):
        sizes = self._main_splitter.sizes()
        total = sum(sizes)
        if visible:
            self._main_splitter.setSizes([total - 160, 160])
        else:
            self._main_splitter.setSizes([total, 0])

    # ── Log Panel ─────────────────────────────────────────────────────────────
    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background:{BG_WINDOW};")
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 8, 16, 10)
        v.setSpacing(6)

        hdr = QHBoxLayout()
        lbl = QLabel("日志")
        lbl.setStyleSheet(
            f"font-size:11px; font-weight:600; color:{TEXT_SECONDARY}; background:transparent;"
        )
        hdr.addWidget(lbl)
        hdr.addStretch()

        btn_clr = QPushButton("清空")
        btn_clr.setFixedHeight(22)
        btn_clr.setStyleSheet(
            f"background:transparent; color:{TEXT_MUTED}; border:1px solid {BORDER};"
            f"border-radius:4px; padding:0 8px; font-size:11px;"
        )
        btn_clr.clicked.connect(lambda: self.text_log.clear())
        hdr.addWidget(btn_clr)
        v.addLayout(hdr)

        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setFont(QFont("Consolas", 10))
        self.text_log.setStyleSheet(
            f"background:{BG_CARD}; border:1px solid {BORDER}; border-radius:6px; padding:4px;"
        )
        v.addWidget(self.text_log)
        return panel

    # ── Page: Bind ───────────────────────────────────────────────────────────
    def _build_page_bind(self) -> QScrollArea:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)

        hint = QLabel("长按绑定按钮，移到目标控件上后松开鼠标即可完成绑定。")
        hint.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ── Status card
        status_card = self._make_card()
        sv = QVBoxLayout(status_card)
        sv.setContentsMargins(16, 14, 16, 14)
        sv.setSpacing(0)
        sv.addWidget(self._card_label("绑定状态"))
        sv.addSpacing(8)

        self.card_edit  = BindCard("", "输入框",       "EditControl")
        self.card_btn   = BindCard("", "发送按钮",     "ButtonControl")
        self.card_voice = BindCard("", "语音消息按钮", "ButtonControl")
        self.card_win   = BindCard("", "消息列表",     "WindowControl")

        for i, card in enumerate([self.card_edit, self.card_btn, self.card_voice, self.card_win]):
            if i > 0:
                sv.addWidget(self._divider())
            sv.addWidget(card)
        v.addWidget(status_card)

        # ── Actions card
        action_card = self._make_card()
        av = QVBoxLayout(action_card)
        av.setContentsMargins(16, 14, 16, 14)
        av.setSpacing(8)
        av.addWidget(self._card_label("手动绑定"))
        av.addSpacing(4)

        g1 = QHBoxLayout()
        g1.setSpacing(8)
        g2 = QHBoxLayout()
        g2.setSpacing(8)

        self.btn_bind_edit  = QPushButton("绑定输入框")
        self.btn_bind_btn   = QPushButton("绑定发送按钮")
        self.btn_bind_voice = QPushButton("绑定语音消息按钮")
        self.btn_bind_win   = QPushButton("绑定消息列表")
        for btn in [self.btn_bind_edit, self.btn_bind_btn, self.btn_bind_voice, self.btn_bind_win]:
            btn.setFixedHeight(34)

        self._setup_bind_button(self.btn_bind_edit,  "edit",         "EditControl",   "输入框")
        self._setup_bind_button(self.btn_bind_btn,   "send_button",  "ButtonControl", "发送按钮")
        self._setup_bind_button(self.btn_bind_voice, "voice_button", "ButtonControl", "语音消息按钮")
        self._setup_bind_button(self.btn_bind_win,   "window",       "WindowControl", "消息列表")

        g1.addWidget(self.btn_bind_edit)
        g1.addWidget(self.btn_bind_btn)
        g2.addWidget(self.btn_bind_voice)
        g2.addWidget(self.btn_bind_win)
        av.addLayout(g1)
        av.addLayout(g2)
        av.addWidget(self._divider())

        self.btn_auto_bind = QPushButton("自动绑定（3 秒内切换到目标聊天窗口）")
        self.btn_auto_bind.setFixedHeight(36)
        make_accent_btn(self.btn_auto_bind)
        self.btn_auto_bind.clicked.connect(self.schedule_auto_bind)
        av.addWidget(self.btn_auto_bind)
        v.addWidget(action_card)
        v.addStretch()
        return self._scrollable(page)

    # ── Page: Control ─────────────────────────────────────────────────────────
    def _build_page_ctrl(self) -> QScrollArea:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)

        ctrl_card = self._make_card()
        cv = QVBoxLayout(ctrl_card)
        cv.setContentsMargins(16, 14, 16, 14)
        cv.setSpacing(12)

        self.bot_status_widget = BotStatusWidget()
        cv.addWidget(self.bot_status_widget)
        cv.addWidget(self._divider())

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_start = QPushButton("启动")
        self.btn_start.setFixedHeight(36)
        self.btn_start.setEnabled(False)
        make_accent_btn(self.btn_start)
        self.btn_start.clicked.connect(self.start_bot)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setFixedHeight(36)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_bot)

        btn_row.addWidget(self.btn_start, 1)
        btn_row.addWidget(self.btn_stop, 1)
        cv.addLayout(btn_row)
        v.addWidget(ctrl_card)

        opts_card = self._make_card()
        ov = QVBoxLayout(opts_card)
        ov.setContentsMargins(16, 14, 16, 14)
        ov.setSpacing(10)
        ov.addWidget(self._card_label("选项"))
        ov.addSpacing(4)

        self.check_auto_reply = QCheckBox("自动回复")
        self.check_auto_reply.setChecked(bool(self.cfg.auto_reply_enabled))
        self.check_auto_reply.toggled.connect(self.on_toggle_auto_reply)
        ov.addWidget(self.check_auto_reply)
        ov.addWidget(self._divider())

        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("本地历史保存条数"))
        self.spin_keep = QSpinBox()
        self.spin_keep.setRange(20, 500)
        self.spin_keep.setValue(int(self.cfg.history_max_messages))
        self.spin_keep.setFixedWidth(75)
        self.spin_keep.valueChanged.connect(self.on_change_keep)
        keep_row.addWidget(self.spin_keep)
        hint = QLabel("条")
        hint.setStyleSheet(f"color:{TEXT_SECONDARY}; background:transparent;")
        keep_row.addWidget(hint)
        keep_row.addStretch()
        ov.addLayout(keep_row)
        v.addWidget(opts_card)
        v.addStretch()
        return self._scrollable(page)

    # ── Page: History ─────────────────────────────────────────────────────────
    def _build_page_hist(self) -> QScrollArea:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)

        self.check_auto_hist = QCheckBox("按联系人名称自动切换历史")
        self.check_auto_hist.setChecked(bool(self.cfg.auto_history_by_bind))
        self.check_auto_hist.toggled.connect(self.on_toggle_auto_history)
        v.addWidget(self.check_auto_hist)

        list_card = self._make_card()
        cv = QVBoxLayout(list_card)
        cv.setContentsMargins(14, 12, 14, 12)
        cv.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addWidget(self._card_label("历史列表"))
        hdr.addStretch()
        self.lbl_hist_status = QLabel()
        self.lbl_hist_status.setStyleSheet(
            f"color:{TEXT_SECONDARY}; font-size:11px; background:transparent;"
        )
        hdr.addWidget(self.lbl_hist_status)
        cv.addLayout(hdr)

        self.list_hist = QListWidget()
        self.list_hist.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_hist.setMinimumHeight(160)
        self.list_hist.itemSelectionChanged.connect(self.on_history_select)
        cv.addWidget(self.list_hist)

        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        for label, cb in [
            ("新增", self.on_history_add),
            ("重命名", self.on_history_rename),
            ("编辑", self.on_history_edit),
            ("刷新", lambda: self.refresh_history_list()),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(30)
            b.clicked.connect(cb)
            act_row.addWidget(b)
        act_row.addStretch()

        btn_del = QPushButton("删除")
        btn_del.setFixedHeight(30)
        make_danger_btn(btn_del)
        btn_del.clicked.connect(self.on_history_delete)
        act_row.addWidget(btn_del)

        btn_clr = QPushButton("清空")
        btn_clr.setFixedHeight(30)
        make_danger_btn(btn_clr)
        btn_clr.clicked.connect(self.clear_history)
        act_row.addWidget(btn_clr)

        cv.addLayout(act_row)
        v.addWidget(list_card)
        v.addStretch()
        return self._scrollable(page)

    # ── Shared Helpers ────────────────────────────────────────────────────────
    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background:{BG_CARD}; border:1px solid {BORDER}; border-radius:8px; }}"
        )
        return card

    def _card_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            f"font-size:10px; font-weight:700; color:{TEXT_MUTED}; letter-spacing:0.5px; background:transparent;"
        )
        return lbl

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{BORDER}; border:none;")
        return line

    def _scrollable(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{BG_WINDOW}; }}")
        return scroll

    def _switch_page(self, idx: int):
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == idx)
        self.page_stack.setCurrentIndex(idx)
        self._page_title.setText(self._PAGE_TITLES[idx])

    def _setup_bind_button(self, btn: QPushButton, bind_slot: str, expected_type: str, name: str):
        btn.pressed.connect(lambda: self.start_pick(bind_slot, expected_type, name))
        # released is a fallback; pick_loop handles it via win32 polling
        btn.released.connect(lambda: self.stop_pick_and_bind(
            self._pick_slot or bind_slot,
            self.pick_expected_type or expected_type,
            self._pick_name or name,
        ))

    def _normalize_history_path(self):
        raw_path = self.cfg.history_path or "history/history.jsonl"
        filename = os.path.basename(raw_path) or "history.jsonl"
        target_dir = "history"
        normalized = os.path.join(target_dir, filename)
        if normalized != raw_path:
            os.makedirs(target_dir, exist_ok=True)
            if os.path.exists(raw_path):
                shutil.move(raw_path, normalized)
            self.cfg.history_path = normalized
            self.cfg.save(self.cfg.config_path)
        else:
            os.makedirs(target_dir, exist_ok=True)



    # ── Status ────────────────────────────────────────────────────────────────
    def set_status(self, msg: str):
        ts = time.strftime("%H:%M:%S", time.localtime())
        line = f"[{ts}] {msg}"
        self._status_lbl.setText(msg[:80])
        self._status_log.append(line)
        if self.log_win:
            try:
                self.log_win.append_line(line)
            except Exception:
                pass

    def set_log(self, text: str):
        self.text_log.setPlainText(text)
        self.text_log.moveCursor(QTextCursor.MoveOperation.End)

    def update_bind_state(self):
        self.card_edit.set_bound(bool(self.bound_edit))
        self.card_btn.set_bound(bool(self.bound_button))
        self.card_voice.set_bound(bool(self.bound_voice_button))
        self.card_win.set_bound(bool(self.bound_window))

        self.engine.bound_edit = self.bound_edit
        self.engine.bound_button = self.bound_button
        self.engine.bound_voice_button = self.bound_voice_button
        self.engine.bound_window = self.bound_window

        ready = self.engine.is_ready()
        self.btn_start.setEnabled(ready and not getattr(self.engine, "running", False))

    # ── Picking ───────────────────────────────────────────────────────────────
    def start_pick(self, bind_slot: str, expected_type: str, target_name: str):
        if self.picking:
            return
        self.picking = True
        self.pick_expected_type = expected_type
        self._pick_slot = bind_slot
        self._pick_name = target_name
        self.hover_ctrl = None
        win32api.SetCursor(win32gui.LoadCursor(0, win32con.IDC_CROSS))
        self.set_status(f"拾取中：移到目标「{target_name}」后松开鼠标即可绑定…")

    def stop_pick_and_bind(self, bind_slot: str, expected_type: str, target_name: str):
        if not self.picking:
            return  # already handled by pick_loop polling
        self.picking = False
        win32api.SetCursor(win32gui.LoadCursor(0, win32con.IDC_ARROW))
        self.highlight.hide()

        ctrl = self.hover_ctrl
        self.hover_ctrl = None

        if not ctrl:
            self.set_status("未选中控件")
            return

        matched_ctrl = ctrl
        target_types = _bind_target_types(expected_type)
        target_label = _bind_target_label(expected_type)
        try:
            ctrl_type = getattr(ctrl, "ControlTypeName", "") or ""
            if ctrl_type not in target_types:
                preferred_name = "语音消息" if bind_slot == "voice_button" else ""
                matched_ctrl = find_child_control_by_type(ctrl, expected_type, preferred_name=preferred_name)
                if not matched_ctrl:
                    self.set_status(f"选中的不是 {target_label}，而是 {ctrl_type or '未知控件'}")
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

        actual = bound.actual_type or bound.expected_type
        if bind_slot == "edit":
            self.set_status(f"✅ 已绑定 输入框控件（{actual}）")
            if self.check_auto_hist.isChecked():
                self.auto_load_history_for_bound_edit()
        elif bind_slot in ["send_button", "voice_button"]:
            self.set_status(f"✅ 已绑定 按钮（名称={bound.name or '无名'} | 实际={actual}）")
        else:
            self.set_status(f"✅ 已绑定 {target_name}（实际={actual}）")

        self.update_bind_state()

    def pick_loop(self):
        if not (self.picking and self.pick_expected_type):
            return

        # Poll Win32 mouse state – fires even when released outside the Qt window
        if not (win32api.GetKeyState(win32con.VK_LBUTTON) & 0x8000):
            slot = self._pick_slot or ""
            etype = self.pick_expected_type or ""
            name = self._pick_name or ""
            self.stop_pick_and_bind(slot, etype, name)
            return

        px, py = win32api.GetCursorPos()

        # Exclude our own Qt window to prevent self-recognition
        try:
            qt_hwnd = int(self.winId())
            hwnd_at = win32gui.WindowFromPoint((px, py))
            if hwnd_at == qt_hwnd or win32gui.IsChild(qt_hwnd, hwnd_at):
                return
        except Exception:
            pass

        ctrl = control_from_point_safe(px, py, self.tk_hwnd)
        if ctrl:
            try:
                self.hover_ctrl = ctrl
                self.highlight.show_rect(ctrl.BoundingRectangle)
            except Exception:
                pass

    def schedule_auto_bind(self):
        if self._auto_bind_timer_id is not None:
            self.killTimer(self._auto_bind_timer_id)
        self.set_status("3 秒内切到目标 QQ 聊天窗口，程序将尝试自动绑定…")
        self._auto_bind_timer_id = self.startTimer(3000)

    def timerEvent(self, event):
        if event.timerId() == self._auto_bind_timer_id:
            self.killTimer(self._auto_bind_timer_id)
            self._auto_bind_timer_id = None
            self.auto_bind_foreground_window()

    def auto_bind_foreground_window(self):
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(fg_hwnd) or "未知窗口"
        except Exception as exc:
            self.set_status(f"自动绑定失败：({exc})")
            return

        try:
            with auto.UIAutomationInitializerInThread():
                root_ctrl = pick_chat_bind_root(fg_hwnd)
                detected = auto_detect_chat_bindings(root_ctrl) if root_ctrl else {}
        except Exception as exc:
            self.set_status(f"自动绑定失败：({exc})")
            return

        eb = build_bound_control(detected.get("edit"), "EditControl") if detected.get("edit") else None
        bb = build_bound_control(detected.get("button"), "ButtonControl") if detected.get("button") else None
        vb = build_bound_control(detected.get("voice_button"), "ButtonControl") if detected.get("voice_button") else None
        wb = build_bound_control(detected.get("window"), "WindowControl") if detected.get("window") else None

        if bb and any(
            k in " ".join([bb.name or "", bb.automation_id or ""]).lower()
            for k in ("关闭", "close", "minimize")
        ):
            self.set_status("自动绑定失败：识别到的发送按钮疑似标题栏按钮，拒绝绑定。")
            return

        if not (eb and bb and wb):
            self.set_status(f"自动绑定失败：前台窗口 {title} 组件识别不全。")
            return

        self.bound_edit = eb
        self.bound_button = bb
        self.bound_voice_button = vb
        self.bound_window = wb
        self.update_bind_state()

        if self.check_auto_hist.isChecked():
            self.auto_load_history_for_bound_edit()
        self.set_status(f"✅ 已自动绑定：{title}")

    # ── Bot Engine Controls ───────────────────────────────────────────────────
    def on_toggle_auto_reply(self, checked: bool):
        self.engine.set_auto_reply(checked)
        self.cfg.auto_reply_enabled = checked
        self.cfg.save(self.cfg.config_path)

    def on_change_keep(self, val: int):
        self.cfg.history_max_messages = val
        self.history.max_messages = val
        self.history.save()
        self.cfg.save(self.cfg.config_path)

    def start_bot(self):
        self.update_bind_state()
        self.engine.start()
        if self.engine.running:
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.bot_status_widget.set_running(True)
            if (
                bool(self.cfg.tts_enabled)
                and str(self.cfg.voice_send_mode).lower() == "real"
                and not self.bound_voice_button
            ):
                self.set_status('⚠️ 真语音模式下未绑定“语音消息”按钮，发送将失败')

    def stop_bot(self):
        self.engine.stop()
        self.btn_start.setEnabled(self.engine.is_ready())
        self.btn_stop.setEnabled(False)
        self.bot_status_widget.set_running(False)

    def bot_loop(self):
        try:
            self.poll_auto_history_from_edit()
            self.engine.step()
        except BaseException as e:
            self.set_status(f"运行异常：{e}")

    # ── History Management ────────────────────────────────────────────────────
    def refresh_history_list(self, select_name=None):
        self._history_refreshing = True
        self.list_hist.clear()
        names = self.history.list_histories()
        self.list_hist.addItems(names)

        target = select_name or self.history.current_name
        if target:
            try:
                idx = names.index(target)
                self.list_hist.setCurrentRow(idx)
            except ValueError:
                pass
        self._history_refreshing = False

        count = len(self.history.items)
        name = self.history.current_name
        self.lbl_hist_status.setText(f"{name}（{count} 条）")

    def select_history(self, name: str):
        actual = self.history.switch(name)
        self.cfg.history_selected = actual
        self.cfg.save(self.cfg.config_path)
        self.refresh_history_list(actual)
        self.set_status(f"📜 已切换到历史：{actual}")

    def on_history_select(self):
        if self._history_refreshing:
            return
        items = self.list_hist.selectedItems()
        if items:
            self.select_history(items[0].text())

    def on_history_add(self):
        name, ok = QInputDialog.getText(self, "新增 History", "输入名称：")
        if ok and name:
            if self.history.create(name):
                self.select_history(self.history.current_name)
                self.set_status(f"✅ 新增历史：{name}")

    def on_history_rename(self):
        cur = self.history.current_name
        if not cur:
            return
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=cur)
        if ok and new_name and new_name != cur:
            if self.history.rename(cur, new_name):
                self.select_history(self.history.current_name)
                self.set_status(f"✅ 已重命名为：{new_name}")

    def on_history_delete(self):
        cur = self.history.current_name
        if not cur:
            return
        ans = QMessageBox.question(
            self, "删除",
            f"确定删除 {cur} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            if self.history.delete(cur):
                self.cfg.history_selected = self.history.current_name
                self.cfg.save(self.cfg.config_path)
                self.refresh_history_list()
                self.set_status(f"🗑️ 已删除：{cur}")

    def on_history_edit(self):
        name = self.history.current_name
        if not name:
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit

        editor = QDialog(self)
        editor.setWindowTitle(f"编辑历史：{name}")
        editor.resize(800, 520)

        layout = QVBoxLayout(editor)
        layout.addWidget(QLabel("每行一个 JSON 对象。"))

        text_edit = QTextEdit()
        text_edit.setPlainText(self.history.export_raw_text(name))
        layout.addWidget(text_edit)

        def _save():
            cnt = text_edit.toPlainText()
            if self.history.import_raw_text(cnt, name):
                self.select_history(name)
                editor.accept()
                self.set_status("✅ 历史内容已保存")
            else:
                QMessageBox.critical(editor, "错误", "保存失败，格式错误")

        btn = QPushButton("保存")
        make_accent_btn(btn)
        btn.clicked.connect(_save)
        layout.addWidget(btn)
        editor.show()

    def on_toggle_auto_history(self, checked: bool):
        self.cfg.auto_history_by_bind = checked
        self.cfg.save(self.cfg.config_path)

    def auto_load_history_for_bound_edit(self):
        if not self.bound_edit:
            return
        name = (self.bound_edit.name or "").strip()
        if not name:
            return
        actual = self.history.ensure_history(name)
        self._last_auto_history_name = actual
        self.select_history(actual)

    def poll_auto_history_from_edit(self):
        if not self.check_auto_hist.isChecked() or not self.bound_edit:
            return
        try:
            with auto.UIAutomationInitializerInThread():
                ctrl = reacquire(self.bound_edit, self.tk_hwnd)
                if not ctrl:
                    return
                name = (getattr(ctrl, "Name", "") or "").strip()
                if name and name != self._last_auto_history_name:
                    self.auto_load_history_for_bound_edit()
        except Exception:
            pass

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
        except BaseException:
            pass
        self.refresh_history_list()
        self.set_status("🧹 已清空本地历史（建议重新对齐后再启动）")

    # ── Sub-windows ───────────────────────────────────────────────────────────
    def open_help(self):
        docs_dir = Path.cwd() / "docs"
        index = docs_dir / "index.html"
        if not index.exists():
            return QMessageBox.critical(self, "错误", f"无文档：{index}")
        try:
            port = _start_docs_server(docs_dir)
            subprocess.Popen([sys.executable, "help_viewer.py", f"http://127.0.0.1:{port}/index.html"])
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def open_info(self):
        if self.info_window:
            self.info_window.raise_()
            self.info_window.activateWindow()
            return
        self.info_window = InfoWindowQt(
            cfg=self.cfg,
            on_close=lambda: setattr(self, "info_window", None),
        )
        self.info_window.show()

    def open_settings(self):
        win = SettingsWindowQt(
            self.settings_store,
            self.cfg,
            self.apply_settings,
            self.apply_cfg_settings,
        )
        win.exec()

    def apply_settings(self, s: OpenAISettings):
        self.llm = self.make_llm_client(s)
        if hasattr(self.llm, "set_debug_hook"):
            self.llm.set_debug_hook(self.on_llm_debug)
        self.engine.set_llm_client(self.llm)
        self.set_status(f"✅ 设置已应用：provider={s.provider}")

    def apply_cfg_settings(self, cfg: AppConfig):
        self.cfg.save(self.cfg.config_path)
        self.set_status(f"✅ 行为设置已应用：模式={cfg.reply_delay_mode}")

    def make_llm_client(self, s: OpenAISettings):
        provider = (s.provider or "").strip().lower()
        if provider == "openai":
            return OpenAIClient(
                api_key=s.api_key, base_url=s.base_url, model=s.model,
                temperature=s.temperature, system_prompt=s.system_prompt,
                user_template=s.user_template, vision_model=s.vision_model,
                vision_prompt=s.vision_prompt, tts_provider=s.tts_provider,
                tts_model=s.tts_model, tts_voice=s.tts_voice, tts_format=s.tts_format,
                tts_language_type=s.tts_language_type, tts_api_key=s.tts_api_key,
                tts_base_url=s.tts_base_url,
            )
        elif provider == "siliconflow":
            return SiliconFlowClient(
                api_key=s.api_key, base_url=s.base_url, model=s.model,
                temperature=s.temperature, system_prompt=s.system_prompt,
                user_template=s.user_template, vision_model=s.vision_model,
                vision_prompt=s.vision_prompt, tts_provider=s.tts_provider,
                tts_model=s.tts_model, tts_voice=s.tts_voice, tts_format=s.tts_format,
                tts_language_type=s.tts_language_type, tts_api_key=s.tts_api_key,
                tts_base_url=s.tts_base_url,
            )
        return MockLLMClient()

    def open_debug(self):
        if self.debug_win:
            self.debug_win.raise_()
            self.debug_win.activateWindow()
            return
        self.debug_win = DebugWindowQt(
            on_close=lambda: setattr(self, "debug_win", None)
        )
        if getattr(self, "_last_debug_payload", None):
            self.debug_win.update_debug(self._last_debug_payload)
        self.debug_win.show()

    def on_llm_debug(self, data: dict):
        self._last_debug_payload = data
        if self.debug_win:
            self.debug_win.update_debug(data)

    def open_log(self):
        if self.log_win:
            self.log_win.raise_()
            self.log_win.activateWindow()
            return
        self.log_win = LogWindowQt(
            initial_lines=list(self._status_log),
            on_clear=lambda: self._status_log.clear(),
            on_close=lambda: setattr(self, "log_win", None),
        )
        self.log_win.show()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    win = AppQt()
    win.show()
    sys.exit(app.exec())
