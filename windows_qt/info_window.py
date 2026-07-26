from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QFrame
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from pathlib import Path
from __version__ import __version__
from windows_qt.ui_theme import make_accent_btn, ACCENT, TEXT_SECONDARY


class InfoWindowQt(QDialog):
    def __init__(self, cfg, on_close=None):
        super().__init__()
        self.setWindowTitle("关于")
        self.setFixedSize(480, 280)
        self.on_close_cb = on_close

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(16)

        # Top: logo + title
        top = QHBoxLayout()
        top.setSpacing(20)

        logo_label = QLabel()
        logo_path = Path(__file__).parent.parent / "docs" / "assets" / "Logo.png"
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path)).scaled(
                80, 80,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_label.setPixmap(pixmap)
        logo_label.setFixedSize(80, 80)
        top.addWidget(logo_label)

        info = QVBoxLayout()
        info.setSpacing(4)
        title_lbl = QLabel(f"QQSafeChat")
        title_lbl.setStyleSheet("font-size:20px; font-weight:700;")
        ver_lbl = QLabel(f"版本 {__version__}")
        ver_lbl.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px;")
        desc_lbl = QLabel("基于 UIAutomation + LLM 的 QQ / 微信自动回复机器人")
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color:{TEXT_SECONDARY};")
        info.addWidget(title_lbl)
        info.addWidget(ver_lbl)
        info.addSpacing(4)
        info.addWidget(desc_lbl)
        info.addStretch()
        top.addLayout(info)
        layout.addLayout(top)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        repo_label = QLabel(
            f'开源地址：<a href="https://github.com/a1195655988/QQSafeChat" '
            f'style="color:{ACCENT};">GitHub - QQSafeChat</a>'
        )
        repo_label.setOpenExternalLinks(True)
        layout.addWidget(repo_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        make_accent_btn(btn_close)
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def closeEvent(self, event):
        if hasattr(self, "on_close_cb") and self.on_close_cb:
            self.on_close_cb()
        super().closeEvent(event)
