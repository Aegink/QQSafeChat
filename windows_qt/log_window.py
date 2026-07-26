from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtGui import QFont, QTextCursor
from windows_qt.ui_theme import make_accent_btn


class LogWindowQt(QDialog):
    def __init__(self, initial_lines, on_clear, on_close):
        super().__init__()
        self.setWindowTitle("运行日志")
        self.resize(860, 560)
        self.on_clear = on_clear
        self.on_close_cb = on_close

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # Header row
        hdr = QHBoxLayout()
        title_lbl = QLabel("运行日志")
        title_lbl.setStyleSheet("font-size:16px; font-weight:700;")
        hdr.addWidget(title_lbl)
        hdr.addStretch()
        btn_clear = QPushButton("清空")
        make_accent_btn(btn_clear)
        btn_clear.clicked.connect(self._clear_log)
        hdr.addWidget(btn_clear)
        layout.addLayout(hdr)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text_edit, 1)

        if initial_lines:
            self.text_edit.setPlainText("\n".join(initial_lines))
            self.text_edit.moveCursor(QTextCursor.MoveOperation.End)

    def _clear_log(self):
        self.text_edit.clear()
        if self.on_clear:
            self.on_clear()

    def append_line(self, line: str):
        self.text_edit.append(line)
        self.text_edit.moveCursor(QTextCursor.MoveOperation.End)

    def closeEvent(self, event):
        if self.on_close_cb:
            self.on_close_cb()
        super().closeEvent(event)
