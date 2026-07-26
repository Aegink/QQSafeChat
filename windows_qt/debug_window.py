from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit,
    QLabel, QSplitter, QApplication, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from windows_qt.ui_theme import make_accent_btn, TEXT_SECONDARY
import json


class DebugWindowQt(QDialog):
    def __init__(self, on_close=None):
        super().__init__()
        self.setWindowTitle("LLM Debug")
        self.resize(1000, 720)
        self.on_close_cb = on_close

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title_lbl = QLabel("LLM Debug")
        title_lbl.setStyleSheet("font-size:16px; font-weight:700;")
        hdr.addWidget(title_lbl)
        self.title_label = QLabel("等待第一条 debug 数据…")
        self.title_label.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px;")
        hdr.addWidget(self.title_label)
        hdr.addStretch()

        for label, cb in [
            ("System", self.copy_system),
            ("User", self.copy_user),
            ("Payload", self.copy_payload),
            ("Raw Output", self.copy_raw_output),
        ]:
            b = QPushButton(f"复制 {label}")
            b.clicked.connect(cb)
            hdr.addWidget(b)

        layout.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)
        
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        self.text_req = QTextEdit()
        self.text_req.setReadOnly(True)
        self.text_req.setFont(QFont("Consolas", 10))
        splitter.addWidget(self.text_req)
        
        self.text_resp = QTextEdit()
        self.text_resp.setReadOnly(True)
        self.text_resp.setFont(QFont("Consolas", 10))
        splitter.addWidget(self.text_resp)
        
        layout.addWidget(splitter)
        
        self._last_data = {}

    def update_debug(self, data: dict):
        self._last_data = data
        model = data.get("model", "unknown")
        req_start = data.get("request_start", "?")
        cost = data.get("cost_seconds", 0.0)
        reason = data.get("reason_str", "")
        if reason:
            reason = f" [{reason}]"
            
        self.title_label.setText(f"模型: {model} | 开始: {req_start} | 耗时: {cost:.2f}s{reason}")
        
        payload_str = json.dumps(data.get("payload", {}), ensure_ascii=False, indent=2)
        sys_info = data.get("system_prompt", "")
        if not sys_info:
            sys_info = "(无 system_prompt / 采用 o1 message 原则等)"
            
        history_msgs = data.get("history_msgs", [])
        hist_str = ""
        for i, m in enumerate(history_msgs):
            r = m.get("role", "")
            c = m.get("content", "")
            hist_str += f"\n--- history[{i}] ({r}) ---\n{c}\n"
            
        user_prompt = data.get("user_prompt", "")
        req_full = (
            f"=== SYSTEM PROMPT ===\n{sys_info}\n"
            f"=== HISTORY MESSAGES ===\n{hist_str}\n"
            f"=== USER PROMPT ===\n{user_prompt}\n\n"
            f"=== RAW PAYLOAD ===\n{payload_str}"
        )
        self.text_req.setPlainText(req_full)
        
        raw_out = data.get("raw_output", "")
        err = data.get("error", None)
        if err:
            resp_full = f"ERROR:\n{err}\n\nRaw Output:\n{raw_out}"
        else:
            resp_full = f"RAW OUTPUT:\n{raw_out}"
        self.text_resp.setPlainText(resp_full)

    def closeEvent(self, event):
        if self.on_close_cb:
            self.on_close_cb()
        super().closeEvent(event)

    def copy_system(self):
        cb = QApplication.clipboard()
        cb.setText(self._last_data.get("system_prompt", ""))
        
    def copy_user(self):
        cb = QApplication.clipboard()
        cb.setText(self._last_data.get("user_prompt", ""))
        
    def copy_payload(self):
        cb = QApplication.clipboard()
        cb.setText(json.dumps(self._last_data.get("payload", {}), ensure_ascii=False, indent=2))
        
    def copy_raw_output(self):
        cb = QApplication.clipboard()
        cb.setText(self._last_data.get("raw_output", ""))
