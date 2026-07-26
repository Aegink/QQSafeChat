import os
import json
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QStackedWidget, QScrollArea, QWidget,
    QLabel, QLineEdit, QComboBox, QTextEdit, QSpinBox, QDoubleSpinBox,
    QPushButton, QGroupBox, QRadioButton, QCheckBox,
    QListWidget, QSplitter, QAbstractItemView, QFormLayout,
    QMessageBox, QInputDialog, QPlainTextEdit, QFrame, QTabWidget
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont

from storage.config import AppConfig
from storage.persona_store import PersonaStore
from storage.settings_store import SettingsStore, OpenAISettings
from core.audio_output import list_output_devices, DEFAULT_OUTPUT_DEVICE_LABEL
from core.llm_client import MockLLMClient, OpenAIClient, SiliconFlowClient
from windows_qt.ui_theme import (
    make_accent_btn, make_danger_btn, NavButton,
    SIDEBAR_BG, BG_WINDOW, BG_CARD, BORDER, TEXT_PRIMARY,
    TEXT_SECONDARY, TEXT_MUTED, ACCENT,
)


class SettingsWindowQt(QDialog):
    _PAGE_LABELS = ["LLM", "行为", "功能", "人格", "Prompt 预览"]

    def __init__(self, store: SettingsStore, cfg: AppConfig, on_apply_llm, on_apply_cfg):
        super().__init__()
        self.setWindowTitle("设置")
        self.resize(960, 660)

        self.store = store
        self.cfg = cfg
        self.on_apply_llm = on_apply_llm
        self.on_apply_cfg = on_apply_cfg

        self.personas = PersonaStore(self.cfg.persona_dir)
        self.persona_dirty = {}
        self._persona_current_name = ""
        self._persona_original_text = ""
        self._real_voice_device_error = ""

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Sidebar
        sidebar = QWidget()
        sidebar.setFixedWidth(152)
        sidebar.setStyleSheet(f"background:{SIDEBAR_BG};")
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(8, 20, 8, 16)
        sv.setSpacing(2)

        title_lbl = QLabel("设置")
        title_lbl.setStyleSheet(
            "color:#ffffff; font-size:14px; font-weight:700; "
            "background:transparent; padding: 0 6px 14px 6px;"
        )
        sv.addWidget(title_lbl)

        self._nav_btns = []
        for label in self._PAGE_LABELS:
            btn = NavButton(f"  {label}")
            self._nav_btns.append(btn)
            sv.addWidget(btn)
        sv.addStretch()
        outer.addWidget(sidebar)

        # Right area
        right_area = QWidget()
        right_area.setStyleSheet(f"background:{BG_WINDOW};")
        rv = QVBoxLayout(right_area)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        self.page_stack = QStackedWidget()

        self.tab_llm      = QWidget()
        self.tab_behavior = QWidget()
        self.tab_features = QWidget()
        self.tab_persona  = QWidget()
        self.tab_prompt   = QWidget()

        for tab in [self.tab_llm, self.tab_behavior, self.tab_features,
                    self.tab_persona, self.tab_prompt]:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(tab)
            scroll.setStyleSheet(
                f"QScrollArea {{ border:none; background:{BG_WINDOW}; }}"
            )
            self.page_stack.addWidget(scroll)

        rv.addWidget(self.page_stack, 1)

        # Bottom button bar
        btn_bar = QWidget()
        btn_bar.setStyleSheet(
            f"background:{BG_CARD}; border-top: 1px solid {BORDER};"
        )
        bh = QHBoxLayout(btn_bar)
        bh.setContentsMargins(16, 10, 16, 10)
        bh.setSpacing(8)
        bh.addStretch()

        self.btn_save = QPushButton("保存并应用")
        make_accent_btn(self.btn_save)
        self.btn_save.clicked.connect(self.save_apply)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.close)

        bh.addWidget(self.btn_save)
        bh.addWidget(self.btn_cancel)
        rv.addWidget(btn_bar)
        outer.addWidget(right_area, 1)

        for i, btn in enumerate(self._nav_btns):
            btn.clicked.connect(lambda _, idx=i: self._switch_settings_page(idx))

        self._build_llm_tab()
        self._build_behavior_tab()
        self._build_features_tab()
        self._build_persona_tab()
        self._build_prompt_tab()
        self._switch_settings_page(0)

    def _switch_settings_page(self, idx: int):
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == idx)
        self.page_stack.setCurrentIndex(idx)
        if idx == 4:
            self.refresh_prompt_preview()

    # ─── Settings Page Helpers ────────────────────────────────────────────────
    def _scard(self, title: str = ""):
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background:{BG_CARD}; border:1px solid {BORDER}; border-radius:10px; }}"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 14, 20, 14)
        v.setSpacing(0)
        if title:
            lbl = QLabel(title.upper())
            lbl.setStyleSheet(
                f"font-size:10px; font-weight:700; color:{TEXT_MUTED}; "
                f"letter-spacing:0.6px; background:transparent;"
            )
            v.addWidget(lbl)
            v.addSpacing(8)
            div = QFrame()
            div.setFixedHeight(1)
            div.setStyleSheet(f"background:{BORDER}; border:none;")
            v.addWidget(div)
            v.addSpacing(8)
        return card, v

    def _sdiv(self, layout):
        layout.addSpacing(3)
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{BORDER}; border:none;")
        layout.addWidget(div)
        layout.addSpacing(3)

    def _frow(self, layout, label: str, widget, hint: str = "", stretch: bool = True):
        h = QHBoxLayout()
        h.setContentsMargins(0, 3, 0, 3)
        h.setSpacing(12)
        lbl = QLabel(label)
        lbl.setFixedWidth(120)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet(f"color:{TEXT_SECONDARY}; background:transparent; font-size:12px;")
        h.addWidget(lbl)
        if stretch:
            h.addWidget(widget, 1)
        else:
            h.addWidget(widget)
            h.addStretch()
        if hint:
            hl = QLabel(hint)
            hl.setStyleSheet(f"color:{TEXT_MUTED}; background:transparent; font-size:11px;")
            hl.setWordWrap(True)
            h.addWidget(hl)
        layout.addLayout(h)

    def _hbox_w(self, *items) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for item in items:
            if item is None:
                h.addStretch()
            elif isinstance(item, int):
                h.addSpacing(item)
            else:
                h.addWidget(item)
        return w

    def _slbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{TEXT_MUTED}; background:transparent; font-size:11px;")
        lbl.setWordWrap(True)
        return lbl

    # ─── Page: LLM ────────────────────────────────────────────────────────────
    def _build_llm_tab(self):
        layout = QVBoxLayout(self.tab_llm)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        card, cv = self._scard("接入配置")
        self.combo_provider = QComboBox()
        self.combo_provider.addItems(["mock", "openai", "siliconflow"])
        self.combo_provider.setCurrentText(self.store.settings.provider)
        self._frow(cv, "Provider", self.combo_provider, stretch=False)
        self._sdiv(cv)

        self.edit_api_key = QLineEdit(self.store.settings.api_key)
        self.edit_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._frow(cv, "API Key", self.edit_api_key)

        self.edit_base_url = QLineEdit(self.store.settings.base_url)
        self._frow(cv, "Base URL", self.edit_base_url)

        self.edit_model = QLineEdit(self.store.settings.model)
        self._frow(cv, "Model", self.edit_model)

        self.edit_temp = QLineEdit(str(self.store.settings.temperature))
        self.edit_temp.setFixedWidth(72)
        self._frow(cv, "Temperature",
                   self._hbox_w(self.edit_temp, self._slbl("范围 0~2，通常 0.4~0.9")),
                   stretch=False)
        layout.addWidget(card)

        pcard, pv = self._scard("提示词")
        pv.addWidget(self._slbl(
            "User Template 支持 {history}、{incoming}、{image_context} 三个占位符。"
            "  任意字段以 $ 开头则从环境变量读取（如 $OPENAI_API_KEY）。"
        ))
        pv.addSpacing(6)
        sys_lbl = QLabel("System Prompt")
        sys_lbl.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent; font-weight:600;")
        pv.addWidget(sys_lbl)
        self.text_system = QPlainTextEdit()
        self.text_system.setPlainText(self.store.settings.system_prompt)
        self.text_system.setMinimumHeight(88)
        pv.addWidget(self.text_system)
        pv.addSpacing(8)
        usr_lbl = QLabel("User Template")
        usr_lbl.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent; font-weight:600;")
        pv.addWidget(usr_lbl)
        self.text_user = QPlainTextEdit()
        self.text_user.setPlainText(self.store.settings.user_template)
        self.text_user.setMinimumHeight(88)
        pv.addWidget(self.text_user)
        row_btn = QHBoxLayout()
        row_btn.setContentsMargins(0, 8, 0, 0)
        row_btn.addStretch()
        btn_example = QPushButton("填充示例模板")
        btn_example.clicked.connect(self.fill_example)
        row_btn.addWidget(btn_example)
        pv.addLayout(row_btn)
        layout.addWidget(pcard)
        layout.addStretch()

    def fill_example(self):
        self.text_system.setPlainText("你是一个中文私聊代聊助手。回复要像真人，不要写旁白，不要解释规则。")
        example = ("请根据聊天历史、对方最新消息和可选识图结果，输出一个 JSON actions 对象。\n\n"
                   "【聊天上下文】\n{history}\n\n"
                   "【对方最新消息】\n{incoming}\n\n"
                   "【识图结果】\n{image_context}")
        self.text_user.setPlainText(example)
        self.refresh_prompt_preview()

    # ─── Page: 行为 ───────────────────────────────────────────────────────────
    def _build_behavior_tab(self):
        layout = QVBoxLayout(self.tab_behavior)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        card, cv = self._scard("回复延迟")
        self.edit_reply_stop = QLineEdit(str(self.cfg.reply_stop_seconds))
        self.edit_reply_stop.setFixedWidth(72)
        self._frow(cv, "基础等待（秒）",
                   self._hbox_w(self.edit_reply_stop, self._slbl("对方最后一条消息后至少等这么久")),
                   stretch=False)
        self._sdiv(cv)

        mode = self.cfg.reply_delay_mode or "fixed+random"
        self.radio_fixed = QRadioButton("固定")
        self.radio_random = QRadioButton("固定 + 随机")
        if mode == "fixed":
            self.radio_fixed.setChecked(True)
        else:
            self.radio_random.setChecked(True)
        self.radio_fixed.toggled.connect(self._refresh_delay_mode)
        self.radio_random.toggled.connect(self._refresh_delay_mode)
        self._frow(cv, "延迟模式",
                   self._hbox_w(self.radio_fixed, self.radio_random, None),
                   stretch=False)

        self.edit_rand_min = QLineEdit(str(self.cfg.reply_random_min))
        self.edit_rand_max = QLineEdit(str(self.cfg.reply_random_max))
        self.edit_rand_min.setFixedWidth(68)
        self.edit_rand_max.setFixedWidth(68)
        sep = QLabel("~")
        sep.setStyleSheet("background:transparent;")
        self._frow(cv, "随机范围",
                   self._hbox_w(self.edit_rand_min, sep, self.edit_rand_max,
                                self._slbl("秒（在基础等待后额外加）"), None),
                   stretch=False)
        layout.addWidget(card)

        scard, sv = self._scard("发送节奏")
        self.edit_speed_mult = QLineEdit(str(self.cfg.split_speed_multiplier))
        self.edit_speed_mult.setFixedWidth(72)
        self._frow(sv, "速度倍率",
                   self._hbox_w(self.edit_speed_mult,
                                self._slbl("1.0=正常；2.0更快；0.5更慢")),
                   stretch=False)
        layout.addWidget(scard)
        layout.addStretch()
        self._refresh_delay_mode()

    def _refresh_delay_mode(self):
        enabled = self.radio_random.isChecked()
        self.edit_rand_min.setEnabled(enabled)
        self.edit_rand_max.setEnabled(enabled)

    # ─── Page: 功能 ───────────────────────────────────────────────────────────
    def _build_features_tab(self):
        layout = QVBoxLayout(self.tab_features)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Sticker
        stcard, stv = self._scard("贴图回复")
        self.check_sticker = QCheckBox("启用贴图动作")
        self.check_sticker.setChecked(bool(getattr(self.cfg, "sticker_selector_enabled", False)))
        stv.addWidget(self.check_sticker)
        self._sdiv(stv)
        self.edit_sticker_api = QLineEdit(getattr(self.cfg, "sticker_selector_api", ""))
        self._frow(stv, "API", self.edit_sticker_api)
        self.spin_sticker_k = QSpinBox()
        self.spin_sticker_k.setRange(1, 6)
        self.spin_sticker_k.setValue(int(getattr(self.cfg, "sticker_selector_k", 3) or 3))
        self.spin_sticker_k.setFixedWidth(72)
        self.check_sticker_rand = QCheckBox("随机挑选")
        self.check_sticker_rand.setChecked(bool(getattr(self.cfg, "sticker_selector_random", False)))
        self._frow(stv, "候选数 k",
                   self._hbox_w(self.spin_sticker_k, self.check_sticker_rand, None),
                   stretch=False)
        sticker_p_lbl = QLabel("贴图说明（给模型看）")
        sticker_p_lbl.setStyleSheet(f"color:{TEXT_SECONDARY}; background:transparent; font-size:12px;")
        stv.addWidget(sticker_p_lbl)
        self.text_sticker_prompt = QPlainTextEdit()
        self.text_sticker_prompt.setMaximumHeight(68)
        self.text_sticker_prompt.setPlainText(
            getattr(self.cfg, "sticker_selector_prompt", "") or
            "当需要发贴图时，使用 sticker action，并给出 2~5 个中文标签，"
            "标签要具体，优先描述主体、表情和情绪，例如：可爱、小猫、委屈、撒娇。"
        )
        stv.addWidget(self.text_sticker_prompt)
        layout.addWidget(stcard)

        # Vision
        vcard, vv = self._scard("识图")
        self.check_vision = QCheckBox("启用自动识图（检测到对方图片时自动复制并识别）")
        self.check_vision.setChecked(bool(getattr(self.cfg, "vision_enabled", False)))
        vv.addWidget(self.check_vision)
        self._sdiv(vv)
        self.edit_vision_model = QLineEdit(self.store.settings.vision_model)
        self._frow(vv, "识图模型", self.edit_vision_model)
        vp_lbl = QLabel("识图提示词")
        vp_lbl.setStyleSheet(f"color:{TEXT_SECONDARY}; background:transparent; font-size:12px;")
        vv.addWidget(vp_lbl)
        self.text_vision_prompt = QPlainTextEdit()
        self.text_vision_prompt.setMaximumHeight(56)
        self.text_vision_prompt.setPlainText(self.store.settings.vision_prompt)
        vv.addWidget(self.text_vision_prompt)
        layout.addWidget(vcard)

        # TTS
        tcard, tv = self._scard("语音发送（TTS）")
        self.check_tts = QCheckBox("启用 voice action")
        self.check_tts.setChecked(bool(getattr(self.cfg, "tts_enabled", False)))
        self.check_tts.toggled.connect(self._refresh_voice_mode)
        tv.addWidget(self.check_tts)
        self._sdiv(tv)

        self.combo_tts_prov = QComboBox()
        self.combo_tts_prov.addItems(["openai", "qwen"])
        self.combo_tts_prov.setCurrentText(getattr(self.store.settings, "tts_provider", "openai"))
        self._frow(tv, "TTS 提供方", self.combo_tts_prov, stretch=False)

        self.edit_tts_url = QLineEdit(getattr(self.store.settings, "tts_base_url", ""))
        self._frow(tv, "TTS URL", self.edit_tts_url)

        self.edit_tts_key = QLineEdit(getattr(self.store.settings, "tts_api_key", ""))
        self.edit_tts_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._frow(tv, "TTS Key", self.edit_tts_key)

        tv.addWidget(self._slbl("留空时回退使用 LLM 页的 Base URL / API Key。"))

        self.edit_tts_model = QLineEdit(self.store.settings.tts_model)
        self._frow(tv, "TTS 模型", self.edit_tts_model)

        self.edit_tts_voice = QLineEdit(self.store.settings.tts_voice)
        self.combo_tts_fmt = QComboBox()
        self.combo_tts_fmt.addItems(["mp3", "wav", "ogg", "m4a"])
        self.combo_tts_fmt.setCurrentText(self.store.settings.tts_format or "mp3")
        fmt_lbl = QLabel("格式")
        fmt_lbl.setStyleSheet(f"color:{TEXT_SECONDARY}; background:transparent; font-size:12px;")
        self._frow(tv, "Voice", self._hbox_w(self.edit_tts_voice, fmt_lbl, self.combo_tts_fmt))

        self.combo_tts_lang = QComboBox()
        self.combo_tts_lang.addItems(["auto", "Chinese", "English"])
        self.combo_tts_lang.setCurrentText(getattr(self.store.settings, "tts_language_type", "auto"))
        self._frow(tv, "语言类型", self.combo_tts_lang, stretch=False)

        self.radio_voice_file = QRadioButton("文件")
        self.radio_voice_real = QRadioButton("真语音")
        if getattr(self.cfg, "voice_send_mode", "file") == "real":
            self.radio_voice_real.setChecked(True)
        else:
            self.radio_voice_file.setChecked(True)
        self.radio_voice_file.toggled.connect(self._refresh_voice_mode)
        self.radio_voice_real.toggled.connect(self._refresh_voice_mode)
        self._frow(tv, "发送模式",
                   self._hbox_w(self.radio_voice_file, self.radio_voice_real, None),
                   stretch=False)

        self.combo_voice_dev = QComboBox()
        self.btn_refresh_dev = QPushButton("刷新")
        self.btn_refresh_dev.setFixedWidth(52)
        self._frow(tv, "扬声器", self._hbox_w(self.combo_voice_dev, self.btn_refresh_dev))
        self.btn_refresh_dev.clicked.connect(self._refresh_output_devices)

        self.lbl_voice_dev_status = QLabel("")
        self.lbl_voice_dev_status.setStyleSheet(
            f"color:{TEXT_MUTED}; background:transparent; font-size:11px;"
        )
        tv.addWidget(self.lbl_voice_dev_status)

        self.edit_voice_delay = QLineEdit(str(getattr(self.cfg, "real_voice_start_delay_sec", 0.5)))
        self.edit_voice_delay.setFixedWidth(72)
        self._frow(tv, "录音校准",
                   self._hbox_w(self.edit_voice_delay,
                                self._slbl("秒（按住空格后等待这么久再播）"), None),
                   stretch=False)

        self.lbl_voice_hint = QLabel("")
        self.lbl_voice_hint.setWordWrap(True)
        self.lbl_voice_hint.setStyleSheet(
            f"color:{TEXT_MUTED}; background:transparent; font-size:11px;"
        )
        tv.addWidget(self.lbl_voice_hint)
        layout.addWidget(tcard)
        layout.addStretch()
        self._refresh_output_devices()

    def _refresh_output_devices(self):
        labels, error = list_output_devices()
        self._real_voice_device_error = error
        values = [DEFAULT_OUTPUT_DEVICE_LABEL] + labels
        self.combo_voice_dev.clear()
        self.combo_voice_dev.addItems(values)
        current = getattr(self.cfg, "real_voice_output_device", "")
        if not current or current not in values:
            self.combo_voice_dev.setCurrentText(DEFAULT_OUTPUT_DEVICE_LABEL)
        else:
            self.combo_voice_dev.setCurrentText(current)
        if error:
            self.lbl_voice_dev_status.setText(f"列表读取失败: {error}")
        else:
            self.lbl_voice_dev_status.setText(f"已检测到 {len(labels)} 个设备。")
        self._refresh_voice_mode()

    def _refresh_voice_mode(self):
        enabled = self.check_tts.isChecked()
        real_mode = enabled and self.radio_voice_real.isChecked()
        self.combo_voice_dev.setEnabled(enabled)
        self.btn_refresh_dev.setEnabled(enabled)
        self.edit_voice_delay.setEnabled(real_mode)
        lines = []
        if not enabled:
            lines.append("voice action 已关闭。")
        elif real_mode:
            lines.append("真语音模式：自动按住空格，播放声音。需绑定'语音消息'按钮。")
        else:
            lines.append("文件模式：生成音频并发出。")
        if self._real_voice_device_error:
            lines.append(f"错误: {self._real_voice_device_error}")
        self.lbl_voice_hint.setText(" ".join(lines))

    # ─── Page: 人格 ───────────────────────────────────────────────────────────
    def _build_persona_tab(self):
        layout = QVBoxLayout(self.tab_persona)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        dir_lbl = QLabel(f"目录：{self.cfg.persona_dir}")
        dir_lbl.setStyleSheet(f"color:{TEXT_MUTED}; font-size:11px; background:transparent;")
        top_row.addWidget(dir_lbl)
        top_row.addStretch()
        btn_ren = QPushButton("刷新")
        btn_ren.setFixedHeight(28)
        btn_ren.clicked.connect(lambda: self._persona_refresh(check_dirty=True))
        btn_new = QPushButton("新建")
        btn_new.setFixedHeight(28)
        btn_new.clicked.connect(self._persona_create)
        top_row.addWidget(btn_ren)
        top_row.addWidget(btn_new)
        layout.addLayout(top_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left
        left_widget = QWidget()
        left_widget.setStyleSheet("background:transparent;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(6)
        self.list_persona = QListWidget()
        self.list_persona.itemSelectionChanged.connect(self._persona_on_select)
        left_layout.addWidget(self.list_persona, 1)
        self.lbl_persona_info = QLabel(f"应用中：{self.cfg.persona_file or '（无）'}")
        self.lbl_persona_info.setStyleSheet(
            f"color:{TEXT_SECONDARY}; font-size:11px; background:transparent; padding:2px 0;"
        )
        left_layout.addWidget(self.lbl_persona_info)

        btn_apply = QPushButton("应用")
        btn_apply.setFixedHeight(30)
        make_accent_btn(btn_apply)
        btn_apply.clicked.connect(self._persona_apply_selected)
        btn_save = QPushButton("保存内容")
        btn_save.setFixedHeight(30)
        btn_save.clicked.connect(self._persona_save_current)
        btn_rename = QPushButton("重命名")
        btn_rename.setFixedHeight(30)
        btn_rename.clicked.connect(self._persona_rename)
        btn_delete = QPushButton("删除")
        btn_delete.setFixedHeight(30)
        make_danger_btn(btn_delete)
        btn_delete.clicked.connect(self._persona_delete)
        for b in [btn_apply, btn_save, btn_rename, btn_delete]:
            left_layout.addWidget(b)

        # Right
        right_widget = QWidget()
        right_widget.setStyleSheet("background:transparent;")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(6)
        edit_lbl = QLabel("内容编辑")
        edit_lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent; font-weight:600;"
        )
        right_layout.addWidget(edit_lbl)
        self.text_persona = QPlainTextEdit()
        self.text_persona.textChanged.connect(self._on_persona_modified)
        right_layout.addWidget(self.text_persona, 1)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([200, 580])
        layout.addWidget(splitter, 1)
        self._persona_refresh(select=self.cfg.persona_file)

    def _persona_refresh(self, select="", check_dirty=False):
        if check_dirty and self._persona_current_name and self.persona_dirty.get(self._persona_current_name):
            res = self._confirm_unsaved_change("refresh")
            if res == "cancel": return
            if res == "save" and not self._persona_save_current(): return
        cur = self._persona_current_name
        self.list_persona.clear()
        files = self.personas.list_files()
        self.list_persona.addItems(files)
        for i in range(self.list_persona.count()):
            name = self.list_persona.item(i).text()
            if self.persona_dirty.get(name):
                self.list_persona.item(i).setText(name + " *")
        target = select if select in files else ""
        if not target and cur in files: target = cur
        if not target and self.cfg.persona_file in files: target = self.cfg.persona_file
        if not target and files: target = files[0]
        if target:
            self._persona_select_name(target)
            self._persona_load_content(target)
        else:
            self._persona_current_name = ""
            self.text_persona.clear()

    def _persona_select_name(self, name):
        for i in range(self.list_persona.count()):
            if self.list_persona.item(i).text().replace(" *", "") == name:
                self.list_persona.setCurrentRow(i)
                break

    def _persona_selected_name(self):
        items = self.list_persona.selectedItems()
        if not items: return ""
        return items[0].text().replace(" *", "")

    def _persona_load_content(self, name):
        try:
            content = self.personas.read(name)
        except Exception:
            content = ""
        self._persona_current_name = name
        self._persona_original_text = content
        self.text_persona.blockSignals(True)
        self.text_persona.setPlainText(content)
        self.text_persona.blockSignals(False)
        self.persona_dirty[name] = False
        self._refresh_persona_list_item(name)

    def _on_persona_modified(self):
        name = self._persona_current_name
        if not name: return
        cur = self.text_persona.toPlainText()
        dirty = cur != self._persona_original_text
        if dirty != self.persona_dirty.get(name, False):
            self.persona_dirty[name] = dirty
            self._refresh_persona_list_item(name)

    def _refresh_persona_list_item(self, name):
        for i in range(self.list_persona.count()):
            text = self.list_persona.item(i).text().replace(" *", "")
            if text == name:
                self.list_persona.item(i).setText(name + (" *" if self.persona_dirty.get(name) else ""))
                break

    def _persona_on_select(self):
        target = self._persona_selected_name()
        if not target or target == self._persona_current_name: return
        if self._persona_current_name and self.persona_dirty.get(self._persona_current_name):
            res = self._confirm_unsaved_change("switch", target)
            if res == "cancel":
                self._persona_select_name(self._persona_current_name)
                return
            if res == "save" and not self._persona_save_current(False):
                self._persona_select_name(self._persona_current_name)
                return
        self._persona_load_content(target)

    def _confirm_unsaved_change(self, action, target=None):
        if not self._persona_current_name or not self.persona_dirty.get(self._persona_current_name):
            return "proceed"
        msg = f"'{self._persona_current_name}' 有未保存的修改。要保存吗？"
        reply = QMessageBox.question(
            self, "未保存", msg,
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
        )
        if reply == QMessageBox.StandardButton.Save: return "save"
        elif reply == QMessageBox.StandardButton.Discard:
            self.persona_dirty[self._persona_current_name] = False
            self._refresh_persona_list_item(self._persona_current_name)
            return "discard"
        return "cancel"

    def _persona_save_current(self, show_msg=True):
        name = self._persona_current_name
        if not name: return False
        content = self.text_persona.toPlainText()
        try:
            self.personas.write(name, content)
            self._persona_original_text = content
            self.persona_dirty[name] = False
            self._refresh_persona_list_item(name)
            if show_msg: QMessageBox.information(self, "保存成功", "已保存。")
            self.refresh_prompt_preview()
            return True
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败:\n{e}")
            return False

    def _persona_create(self):
        name, ok = QInputDialog.getText(self, "新建", "输入人设名(如 new.txt):")
        if not ok or not name: return
        if not name.endswith(".txt"): name += ".txt"
        try:
            self.personas.write(name, "")
            self._persona_refresh(select=name)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _persona_rename(self):
        old = self._persona_selected_name()
        if not old: return
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称:", QLineEdit.EchoMode.Normal, old)
        if not ok or not new_name or old == new_name: return
        if not new_name.endswith(".txt"): new_name += ".txt"
        if self.persona_dirty.get(old):
            self.personas.write(old, self.text_persona.toPlainText())
            self.persona_dirty[old] = False
        try:
            os.rename(
                os.path.join(self.cfg.persona_dir, old),
                os.path.join(self.cfg.persona_dir, new_name)
            )
            if self.cfg.persona_file == old:
                self.cfg.persona_file = new_name
                self.lbl_persona_info.setText(f"应用中：{new_name}")
            self._persona_refresh(select=new_name)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _persona_delete(self):
        name = self._persona_selected_name()
        if not name: return
        reply = QMessageBox.question(self, "删除确认", f"确定删除 {name} 吗？")
        if reply == QMessageBox.StandardButton.Yes:
            try:
                os.remove(os.path.join(self.cfg.persona_dir, name))
                if name in self.persona_dirty: del self.persona_dirty[name]
                if self.cfg.persona_file == name:
                    self.cfg.persona_file = ""
                    self.lbl_persona_info.setText("应用中：（无）")
                self._persona_refresh()
            except Exception as e:
                QMessageBox.critical(self, "错误", str(e))

    def _persona_apply_selected(self):
        name = self._persona_selected_name()
        if not name: return
        self.cfg.persona_file = name
        self.cfg.save(self.cfg.config_path)
        self.lbl_persona_info.setText(f"应用中：{name}")
        self.refresh_prompt_preview()
        QMessageBox.information(self, "完成", f"已应用 {name}")

    # ─── Page: Prompt 预览 ────────────────────────────────────────────────────
    def _build_prompt_tab(self):
        layout = QVBoxLayout(self.tab_prompt)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(self._slbl(
            "Prompt 预览 · Persona 使用'当前应用'的人格（非编辑框内容）。\n"
            "模拟数据会替换 {history} / {incoming} / {image_context} 占位符。"
        ))

        scard, sv = self._scard("模拟输入")
        sv.addWidget(QLabel("history"))
        self.txt_sim_history = QPlainTextEdit(
            "[对方] 测试消息1\n[对方] 测试消息2\n[自己] 测试消息3\n[自己] 测试消息4"
        )
        self.txt_sim_history.setMaximumHeight(72)
        sv.addWidget(self.txt_sim_history)
        sv.addSpacing(6)

        sv.addWidget(QLabel("incoming"))
        self.txt_sim_incoming = QPlainTextEdit("[对方] 测试消息5")
        self.txt_sim_incoming.setMaximumHeight(44)
        sv.addWidget(self.txt_sim_incoming)
        sv.addSpacing(6)

        sv.addWidget(QLabel("image_context"))
        self.txt_sim_image = QPlainTextEdit("图片里是一只举牌子的白猫，图片上有'思源神是正常的'这行字。")
        self.txt_sim_image.setMaximumHeight(52)
        sv.addWidget(self.txt_sim_image)

        row_btns = QHBoxLayout()
        row_btns.setContentsMargins(0, 8, 0, 0)
        row_btns.addStretch()
        btn_reset = QPushButton("重置示例")
        btn_reset.clicked.connect(self._reset_sim_samples)
        btn_refresh = QPushButton("刷新预览")
        btn_refresh.clicked.connect(self.refresh_prompt_preview)
        row_btns.addWidget(btn_reset)
        row_btns.addWidget(btn_refresh)
        sv.addLayout(row_btns)
        layout.addWidget(scard)

        self.tabs_preview = QTabWidget()
        self.tab_prev_system = QPlainTextEdit()
        self.tab_prev_user = QPlainTextEdit()
        self.tab_prev_payload = QPlainTextEdit()
        self.tab_prev_system.setReadOnly(True)
        self.tab_prev_user.setReadOnly(True)
        self.tab_prev_payload.setReadOnly(True)
        self.tabs_preview.addTab(self.tab_prev_system, "System（最终）")
        self.tabs_preview.addTab(self.tab_prev_user, "User（最终）")
        self.tabs_preview.addTab(self.tab_prev_payload, "Payload（OpenAI）")
        layout.addWidget(self.tabs_preview, 1)

    def _reset_sim_samples(self):
        self.txt_sim_history.setPlainText("[对方] 测试消息1\n[对方] 测试消息2\n[自己] 测试消息3\n[自己] 测试消息4")
        self.txt_sim_incoming.setPlainText("[对方] 测试消息5")
        self.txt_sim_image.setPlainText("图片里是一只举牌子的白猫，图片上有'思源神是正常的'这行字。")
        self.refresh_prompt_preview()

    def refresh_prompt_preview(self):
        provider = self.combo_provider.currentText()
        sys_txt = self.text_system.toPlainText()
        usr_txt = self.text_user.toPlainText()
        try:
            temp = float(self.edit_temp.text())
        except: temp = 0.7

        if provider == "openai":
            client = OpenAIClient(
                api_key=self.edit_api_key.text(), base_url=self.edit_base_url.text(),
                model=self.edit_model.text(), temperature=temp,
                system_prompt=sys_txt, user_template=usr_txt,
                vision_model=self.edit_vision_model.text(),
                vision_prompt=self.text_vision_prompt.toPlainText(),
                tts_provider=self.combo_tts_prov.currentText(),
                tts_model=self.edit_tts_model.text(),
                tts_voice=self.edit_tts_voice.text(),
                tts_format=self.combo_tts_fmt.currentText(),
                tts_language_type=self.combo_tts_lang.currentText(),
                tts_api_key=self.edit_tts_key.text(),
                tts_base_url=self.edit_tts_url.text()
            )
        elif provider == "siliconflow":
            client = SiliconFlowClient(
                api_key=self.edit_api_key.text(), base_url=self.edit_base_url.text(),
                model=self.edit_model.text(), temperature=temp,
                system_prompt=sys_txt, user_template=usr_txt,
                vision_model=self.edit_vision_model.text(),
                vision_prompt=self.text_vision_prompt.toPlainText(),
                tts_provider=self.combo_tts_prov.currentText(),
                tts_model=self.edit_tts_model.text(),
                tts_voice=self.edit_tts_voice.text(),
                tts_format=self.combo_tts_fmt.currentText(),
                tts_language_type=self.combo_tts_lang.currentText(),
                tts_api_key=self.edit_tts_key.text(),
                tts_base_url=self.edit_tts_url.text()
            )
        else:
            client = MockLLMClient()

        persona_name = self.cfg.persona_file
        persona_text = ""
        if persona_name:
            try: persona_text = self.personas.read(persona_name)
            except: pass

        req = client.build_request(
            self.txt_sim_history.toPlainText(),
            self.txt_sim_incoming.toPlainText(),
            persona_text=persona_text,
            image_context=self.txt_sim_image.toPlainText(),
            allow_sticker=self.check_sticker.isChecked(),
            allow_voice=self.check_tts.isChecked()
        )

        if "error" in req:
            err = str(req["error"])
            self.tab_prev_system.setPlainText(err)
            self.tab_prev_user.setPlainText(err)
            self.tab_prev_payload.setPlainText(err)
        else:
            self.tab_prev_system.setPlainText(req.get("system", ""))
            self.tab_prev_user.setPlainText(req.get("user", ""))
            self.tab_prev_payload.setPlainText(
                json.dumps(req.get("payload", {}), ensure_ascii=False, indent=2)
            )

    def save_apply(self):
        res = self._confirm_unsaved_change("exit")
        if res == "cancel": return
        if res == "save" and not self._persona_save_current(): return

        s = self.store.settings
        s.provider = self.combo_provider.currentText()
        s.api_key = self.edit_api_key.text().strip()
        s.base_url = self.edit_base_url.text().strip()
        s.model = self.edit_model.text().strip()
        s.system_prompt = self.text_system.toPlainText()
        s.user_template = self.text_user.toPlainText()
        try: s.temperature = float(self.edit_temp.text().strip())
        except: return QMessageBox.critical(self, "错误", "Temperature 不是数字")

        s.vision_model = self.edit_vision_model.text().strip()
        s.vision_prompt = self.text_vision_prompt.toPlainText()
        s.tts_provider = self.combo_tts_prov.currentText()
        s.tts_model = self.edit_tts_model.text().strip()
        s.tts_voice = self.edit_tts_voice.text().strip()
        s.tts_format = self.combo_tts_fmt.currentText()
        s.tts_language_type = self.combo_tts_lang.currentText()
        s.tts_api_key = self.edit_tts_key.text().strip()
        s.tts_base_url = self.edit_tts_url.text().strip()

        try: self.cfg.reply_stop_seconds = float(self.edit_reply_stop.text())
        except: return QMessageBox.critical(self, "错误", "基础等待（秒）不是数字")

        self.cfg.reply_delay_mode = "fixed+random" if self.radio_random.isChecked() else "fixed"

        try:
            self.cfg.reply_random_min = float(self.edit_rand_min.text())
            self.cfg.reply_random_max = float(self.edit_rand_max.text())
            self.cfg.split_speed_multiplier = float(self.edit_speed_mult.text())
        except: return QMessageBox.critical(self, "错误", "倍率或随机范围不是数字")

        self.cfg.sticker_selector_enabled = self.check_sticker.isChecked()
        self.cfg.sticker_selector_api = self.edit_sticker_api.text()
        self.cfg.sticker_selector_k = self.spin_sticker_k.value()
        self.cfg.sticker_selector_random = self.check_sticker_rand.isChecked()
        self.cfg.sticker_selector_prompt = self.text_sticker_prompt.toPlainText()

        self.cfg.vision_enabled = self.check_vision.isChecked()
        self.cfg.tts_enabled = self.check_tts.isChecked()
        self.cfg.voice_send_mode = "real" if self.radio_voice_real.isChecked() else "file"

        dev = self.combo_voice_dev.currentText()
        self.cfg.real_voice_output_device = "" if dev == DEFAULT_OUTPUT_DEVICE_LABEL else dev

        try: self.cfg.real_voice_start_delay_sec = float(self.edit_voice_delay.text())
        except: return QMessageBox.critical(self, "错误", "录音校准秒数不是数字")

        self.store.save()
        self.cfg.save(self.cfg.config_path)

        self.on_apply_llm(s)
        self.on_apply_cfg(self.cfg)
        self.accept()

    def closeEvent(self, event):
        res = self._confirm_unsaved_change("exit")
        if res == "cancel":
            event.ignore()
            return
        if res == "save" and not self._persona_save_current():
            event.ignore()
            return
        super().closeEvent(event)