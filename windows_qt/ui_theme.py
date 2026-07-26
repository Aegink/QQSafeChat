"""
Modern UI theme for QQSafeChat
Dark sidebar + light content area
"""

# ─── Sidebar (dark) ───────────────────────────────────────────────────────────
SIDEBAR_BG        = "#1c1c1e"
SIDEBAR_FG        = "#8e8e93"
SIDEBAR_FG_HOVER  = "#e5e5ea"
SIDEBAR_FG_ACTIVE = "#ffffff"
SIDEBAR_ACTIVE_BG = "rgba(255,255,255,0.10)"

# ─── Content area (light) ─────────────────────────────────────────────────────
BG_WINDOW   = "#f2f2f7"
BG_SIDEBAR  = SIDEBAR_BG   # compat alias
BG_CARD     = "#ffffff"
BG_INPUT    = "#f9f9f9"
BG_HOVER    = "#ebebf0"
BG_SELECTED = "#e8f0fe"

# ─── Accent ───────────────────────────────────────────────────────────────────
ACCENT        = "#007aff"
ACCENT_HOVER  = "#0062cc"
ACCENT_PRESS  = "#004fa3"
ACCENT_SUBTLE = "#d6eaff"

# ─── Status ───────────────────────────────────────────────────────────────────
SUCCESS        = "#34c759"
SUCCESS_SUBTLE = "#d4f5de"
WARNING        = "#ff9500"
WARNING_SUBTLE = "#fff3e0"
DANGER         = "#ff3b30"
DANGER_SUBTLE  = "#ffeeec"

# ─── Text ─────────────────────────────────────────────────────────────────────
TEXT_PRIMARY   = "#1c1c1e"
TEXT_SECONDARY = "#636366"
TEXT_MUTED     = "#aeaeb2"

# ─── Border ───────────────────────────────────────────────────────────────────
BORDER       = "#e5e5ea"
BORDER_FOCUS = "#007aff"

# ─── Global QSS ───────────────────────────────────────────────────────────────
STYLESHEET = f"""
QWidget {{
    background-color: {BG_WINDOW};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI Variable", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 16px;
    font-weight: 500;
    outline: none;
    min-height: 22px;
}}
QPushButton:hover {{ background-color: {BG_HOVER}; border-color: #d1d1d6; }}
QPushButton:pressed {{ background-color: #dcdce0; border-color: #c7c7cc; }}
QPushButton:disabled {{ background-color: {BG_HOVER}; color: {TEXT_MUTED}; border-color: {BORDER}; }}
QPushButton[accent="true"] {{
    background-color: {ACCENT}; color: white; border: none;
    border-radius: 7px;
}}
QPushButton[accent="true"]:hover {{ background-color: {ACCENT_HOVER}; }}
QPushButton[accent="true"]:pressed {{ background-color: {ACCENT_PRESS}; }}
QPushButton[accent="true"]:disabled {{ background-color: #99ccff; color: rgba(255,255,255,0.55); }}
QPushButton[danger="true"] {{
    background-color: {DANGER_SUBTLE}; color: {DANGER};
    border: 1px solid #ffcbc7; border-radius: 7px;
}}
QPushButton[danger="true"]:hover {{ background-color: #ffe0de; border-color: #ffb3ae; }}

/* ── Text inputs ── */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_SUBTLE};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {BORDER_FOCUS}; }}
QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only {{
    background-color: {BG_HOVER}; color: {TEXT_SECONDARY};
}}

/* ── ComboBox ── */
QComboBox {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
    min-width: 80px;
    min-height: 22px;
}}
QComboBox:focus {{ border-color: {BORDER_FOCUS}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none;
    width: 8px; height: 8px;
    border-left: 2px solid {TEXT_SECONDARY};
    border-bottom: 2px solid {TEXT_SECONDARY};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    outline: none;
    selection-background-color: {BG_SELECTED};
    selection-color: {ACCENT};
    padding: 2px;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: 4px;
    min-height: 24px;
}}

/* ── SpinBox ── */
QSpinBox, QDoubleSpinBox {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 8px;
    color: {TEXT_PRIMARY};
    min-height: 22px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {BORDER_FOCUS}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: right;
    width: 0px; border: none;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: right;
    width: 0px; border: none;
}}

/* ── Scrollbars ── */
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #d1d1d6; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #aeaeb2; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 6px; }}
QScrollBar::handle:horizontal {{ background: #d1d1d6; border-radius: 3px; min-width: 28px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── ListWidget ── */
QListWidget {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    outline: none;
    padding: 3px;
}}
QListWidget::item {{ padding: 8px 12px; border-radius: 6px; margin: 1px 2px; }}
QListWidget::item:hover {{ background: {BG_HOVER}; }}
QListWidget::item:selected {{ background: {BG_SELECTED}; color: {ACCENT}; font-weight: 600; }}

/* ── CheckBox ── */
QCheckBox {{
    spacing: 8px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 1.5px solid #c7c7cc;
    border-radius: 5px;
    background: {BG_CARD};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; background: {ACCENT_SUBTLE}; }}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    image: url("data:image/svg+xml,<svg/>");
}}
QCheckBox::indicator:checked:hover {{ background-color: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}

/* ── RadioButton ── */
QRadioButton {{ spacing: 8px; color: {TEXT_PRIMARY}; font-size: 13px; }}
QRadioButton::indicator {{
    width: 18px; height: 18px;
    border: 1.5px solid #c7c7cc;
    border-radius: 9px;
    background: {BG_CARD};
}}
QRadioButton::indicator:hover {{ border-color: {ACCENT}; background: {ACCENT_SUBTLE}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* ── TabBar (for prompt preview tabs only) ── */
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 18px;
    font-weight: 500;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT_PRIMARY}; background: {BG_HOVER}; border-radius: 5px 5px 0 0; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; background: {BG_CARD}; }}

/* ── GroupBox (legacy) ── */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
    font-size: 12px;
    font-weight: 600;
    color: {TEXT_SECONDARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background: {BG_WINDOW};
    color: {TEXT_SECONDARY};
}}

/* ── Splitter ── */
QSplitter::handle:vertical {{ background: {BORDER}; height: 1px; margin: 0 4px; }}
QSplitter::handle:horizontal {{ background: {BORDER}; width: 1px; margin: 4px 0; }}

/* ── Tooltip ── */
QToolTip {{
    background: #2c2c2e;
    color: #ffffff;
    border: none;
    border-radius: 5px;
    padding: 4px 8px;
    font-size: 12px;
}}
"""


def make_accent_btn(btn):
    btn.setProperty("accent", "true")
    btn.style().unpolish(btn)
    btn.style().polish(btn)


def make_danger_btn(btn):
    btn.setProperty("danger", "true")
    btn.style().unpolish(btn)
    btn.style().polish(btn)


# ─── Shared Widget Components ─────────────────────────────────────────────────
from PyQt6.QtWidgets import QPushButton  # noqa: E402


class NavButton(QPushButton):
    """Dark sidebar navigation button with checkable active state."""

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setFixedHeight(36)
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {SIDEBAR_FG};
                border: none;
                border-radius: 6px;
                text-align: left;
                padding: 0 10px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.07);
                color: {SIDEBAR_FG_HOVER};
            }}
            QPushButton:checked {{
                background: {SIDEBAR_ACTIVE_BG};
                color: {SIDEBAR_FG_ACTIVE};
                font-weight: 600;
            }}
        """)
