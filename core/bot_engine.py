from __future__ import annotations

import ctypes
from typing import List, Optional
import hashlib
import os
import random
import re
import struct
import tempfile
import threading
import time
import traceback
import uuid
from collections import deque
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageGrab
import uiautomation as auto
import win32api
import win32clipboard
import win32con
import win32gui

from core.audio_output import play_wav_to_output, probe_wav_output
from core.chat_extractor import extract_messages
from core.llm_client import BaseLLMClient, resolve_env
from core.models import BoundControl, ExtractedMessage, ReplyAction
from features.sticker_selector import StickerSelectorClient
from storage.config import AppConfig
from storage.history_store import HistoryStore
from storage.persona_store import PersonaStore
from uia.uia_actions import bring_to_foreground, try_click_button, try_input_text
from uia.uia_picker import reacquire


class BotEngine:
    def __init__(self, cfg: AppConfig, history: HistoryStore, llm: BaseLLMClient, tk_hwnd: int, ui_log, ui_status):
        self.cfg = cfg
        self.history = history
        self.llm = llm
        self.tk_hwnd = tk_hwnd
        self.ui_log = ui_log
        self.ui_status = ui_status

        self.running = False
        self.auto_reply = bool(cfg.auto_reply_enabled)

        self.bound_edit: Optional[BoundControl] = None
        self.bound_button: Optional[BoundControl] = None
        self.bound_window: Optional[BoundControl] = None
        self.bound_voice_button: Optional[BoundControl] = None

        self._busy_reply = False
        self._uia_lock = threading.RLock()
        self._history_lock = threading.RLock()

        self._baseline_taken = False
        self._last_other_sig: str = ""
        self._pending_incoming: List[ExtractedMessage] = []
        self._pending_reply_at: float = 0.0

        self.personas = PersonaStore(self.cfg.persona_dir)
        self._visible_max = 6

        self._echo_ttl_sec = 90.0
        self._recent_self_hash_ts: dict[str, float] = {}
        self._recent_self_text: deque[tuple[float, str]] = deque(maxlen=80)

    def is_ready(self) -> bool:
        return bool(self.bound_edit and self.bound_button and self.bound_window)

    def set_auto_reply(self, enabled: bool):
        self.auto_reply = enabled
        self.cfg.auto_reply_enabled = enabled

    def set_llm_client(self, llm: BaseLLMClient):
        self.llm = llm

    def start(self):
        if not self.is_ready():
            self.ui_status("❌ 需要先绑定：输入框(Edit/Group) + 发送按钮(Button) + 消息列表(Window)")
            return

        self.running = True
        self._baseline_taken = False
        self._last_other_sig = ""
        self._pending_incoming = []
        self._pending_reply_at = 0.0
        self._recent_self_hash_ts.clear()
        self._recent_self_text.clear()
        self.ui_status("✅ QQSafeChat 已启动（JSON action / 识图 / 语音链路已启用）")

    def stop(self):
        self.running = False
        self.ui_status("⏸️ 已停止")

    def _canon_text(self, s: str) -> str:
        s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
        s = re.sub(r"[ \t\u3000]+", " ", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    def _sha1(self, s: str) -> str:
        return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

    def _cleanup_echo(self, now: float):
        dead = [k for k, ts in self._recent_self_hash_ts.items() if now - ts > self._echo_ttl_sec]
        for key in dead:
            self._recent_self_hash_ts.pop(key, None)
        while self._recent_self_text and (now - self._recent_self_text[0][0] > self._echo_ttl_sec):
            self._recent_self_text.popleft()

    def _register_self_outgoing(self, text: str):
        now = time.time()
        self._cleanup_echo(now)
        c = self._canon_text(text)
        if not c:
            return
        c_nospace = re.sub(r"\s+", "", c)
        for fp in {
            self._sha1(c),
            self._sha1(c[:240]),
            self._sha1(c_nospace),
            self._sha1(c_nospace[:400]),
        }:
            self._recent_self_hash_ts[fp] = now
        self._recent_self_text.append((now, c))

    def _is_self_echo(self, maybe_other_text: str) -> bool:
        now = time.time()
        self._cleanup_echo(now)

        c = self._canon_text(maybe_other_text)
        if not c or c == "[图片]":
            return False

        c_nospace = re.sub(r"\s+", "", c)
        for fp in [
            self._sha1(c),
            self._sha1(c[:240]),
            self._sha1(c_nospace),
            self._sha1(c_nospace[:400]),
        ]:
            ts = self._recent_self_hash_ts.get(fp)
            if ts and (now - ts <= self._echo_ttl_sec):
                return True

        if len(c) >= 40:
            for ts, sent in reversed(self._recent_self_text):
                if now - ts > self._echo_ttl_sec:
                    break
                if sent.startswith(c) or c.startswith(sent):
                    return True
                if len(c) >= 120 and c in sent:
                    return True
        return False

    def step(self):
        if not self.running or not self.is_ready():
            return

        with auto.UIAutomationInitializerInThread():
            with self._uia_lock:
                win = reacquire(self.bound_window, self.tk_hwnd)
                if not win:
                    self.ui_status("⚠️ 消息列表控件找不到了，请重新绑定")
                    return
                try:
                    list_rect = win.BoundingRectangle
                except Exception:
                    return
                msgs = extract_messages(win, list_rect)

        self.ui_log(self._format_visible(msgs))

        if not self._baseline_taken:
            last_other, last_idx = self._get_last_other(msgs)
            self._last_other_sig = self._make_last_other_sig(msgs, last_other, last_idx) if last_other else ""
            self._baseline_taken = True
            return

        last_other, last_idx = self._get_last_other(msgs)
        if last_other and (last_other.text or "").strip():
            sig = self._make_last_other_sig(msgs, last_other, last_idx)
            if sig and sig != self._last_other_sig:
                self._last_other_sig = sig
                if self._is_self_echo(last_other.text):
                    self.ui_status("🧯 忽略疑似自己回显（UIA 误判成对方）")
                else:
                    with self._history_lock:
                        self.history.append_messages([last_other])
                    self.ui_status(f"🧠 已保存 1 条对方{'图片' if last_other.msg_type == 'image' else '消息'}到本地历史")
                    self._pending_incoming.append(last_other)
                    self._schedule_reply_from_now()

        if self.auto_reply and (not self._busy_reply) and self._pending_incoming and self._pending_reply_at > 0:
            if time.time() >= self._pending_reply_at:
                pending = list(self._pending_incoming)
                self._pending_incoming = []
                self._pending_reply_at = 0.0
                self._start_reply_thread(pending)

    def _get_last_other(self, msgs: List[ExtractedMessage]) -> tuple[Optional[ExtractedMessage], int]:
        for i in range(len(msgs) - 1, -1, -1):
            m = msgs[i]
            if m.sender == "other" and (m.text or "").strip():
                return m, i
        return None, -1

    def _norm(self, s: str) -> str:
        return (s or "").strip()

    def _message_sig_part(self, msg: Optional[ExtractedMessage]) -> str:
        if not msg:
            return ""
        sender_name = self._norm(getattr(msg, "debug_sender_name", "") or "")
        return f"{msg.sender}:{msg.msg_type}:{sender_name}:{self._norm(msg.text)}"

    def _same_message_identity(self, left: Optional[ExtractedMessage], right: Optional[ExtractedMessage]) -> bool:
        return self._message_sig_part(left) == self._message_sig_part(right)

    def _make_last_other_sig(self, msgs: List[ExtractedMessage], last_other: ExtractedMessage, idx: int) -> str:
        run_length = 1
        prev_idx = idx - 1
        while prev_idx >= 0 and self._same_message_identity(msgs[prev_idx], last_other):
            run_length += 1
            prev_idx -= 1

        prev_part = self._message_sig_part(msgs[prev_idx]) if prev_idx >= 0 else ""
        return f"LO={self._message_sig_part(last_other)}|RUN={run_length}|P={prev_part}"

    def _schedule_reply_from_now(self):
        base = max(0.0, float(self.cfg.reply_stop_seconds))
        extra = 0.0
        mode = (self.cfg.reply_delay_mode or "fixed").strip().lower()
        if mode in ("fixed+random", "random", "rand"):
            a = float(self.cfg.reply_random_min)
            b = float(self.cfg.reply_random_max)
            if b < a:
                a, b = b, a
            extra = random.uniform(a, b)
        self._pending_reply_at = time.time() + base + extra
        self.ui_status(f"⏳ 检测到对方新消息：将在 {base + extra:.2f}s 后生成 JSON 回复（mode={mode}）")

    def _format_ts_full(self, ts_val: float | int | None) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts_val)))
        except Exception:
            return ""

    def _format_visible(self, msgs: List[ExtractedMessage]) -> str:
        lines: List[str] = []
        last_invalid_idx = -1
        for i, m in enumerate(msgs):
            if not self._format_ts_full(getattr(m, "ts", None)):
                last_invalid_idx = i
        effective_msgs = msgs[last_invalid_idx + 1 :]
        if self._visible_max and self._visible_max > 0:
            effective_msgs = effective_msgs[-self._visible_max :]

        for m in effective_msgs:
            who = "自己" if m.sender == "self" else ("对方" if m.sender == "other" else "未知")
            if m.msg_type == "image":
                body = "[图片]"
                if m.image_summary:
                    body += f" {m.image_summary}"
                lines.append(f"[{who}] {body}")
                continue
            text_lines = (m.text or "").splitlines() or [""]
            prefixed = [f"[{who}] {ln}" for ln in text_lines]
            lines.append("\n".join(prefixed))
        return "💬 可见聊天（实时）\n\n" + ("\n\n".join(lines) if lines else "")

    def _load_persona_text(self) -> str:
        fname = (self.cfg.persona_file or "").strip()
        if not fname:
            return ""
        return self.personas.read(fname)

    def _start_reply_thread(self, incoming_msgs: List[ExtractedMessage]):
        self._busy_reply = True
        self.ui_status("🤖 正在生成 JSON 回复...")

        def worker():
            with auto.UIAutomationInitializerInThread():
                try:
                    with self._history_lock:
                        history_text = self.history.format_for_prompt(last_n=30)

                    persona_text = self._load_persona_text()
                    image_count = sum(1 for msg in incoming_msgs if msg.msg_type == "image")
                    if image_count > 0:
                        self.ui_status(f"🖼️ 检测到 {image_count} 张图片，正在补充识图上下文...")
                    else:
                        self.ui_status("📝 正在整理聊天上下文...")
                    incoming_text, image_context = self._build_incoming_context(incoming_msgs)
                    self.ui_status("🧠 上下文已整理，正在请求 LLM...")
                    result = self.llm.generate_actions(
                        history_text,
                        incoming_text,
                        persona_text=persona_text,
                        image_context=image_context,
                        allow_sticker=bool(getattr(self.cfg, "sticker_selector_enabled", False)),
                        allow_voice=bool(getattr(self.cfg, "tts_enabled", False)),
                    )
                    self.ui_status("🧩 已收到模型响应，正在解析 action...")
                    actions = self._parse_actions(result)
                    if not actions:
                        self.ui_status("⚠️ AI 没生成可发送 action")
                        return

                    ok, history_parts = self._send_actions(actions)
                    if ok:
                        with self._history_lock:
                            for part in history_parts:
                                self.history.append_messages([
                                    ExtractedMessage(sender="self", text=part, top=0, left=0, right=0, ts=time.time())
                                ])
                        self.ui_status(f"✅ 已发送 {len(actions)} 个 action 并保存到本地历史")
                    else:
                        self.ui_status("⚠️ 回复生成了，但发送失败（多为焦点/前台/控件刷新）")
                except Exception as exc:
                    detail = traceback.format_exc()
                    emit_debug = getattr(self.llm, "_emit_debug", None)
                    if callable(emit_debug):
                        emit_debug({"phase": "reply_thread_error", "error": detail})
                    self.ui_status(f"❌ 自动回复线程异常：{exc}")
                finally:
                    self._busy_reply = False

        threading.Thread(target=worker, daemon=True).start()

    def _build_incoming_context(self, incoming_msgs: List[ExtractedMessage]) -> tuple[str, str]:
        incoming_parts: list[str] = []
        image_parts: list[str] = []
        image_total = sum(1 for msg in incoming_msgs if msg.msg_type == "image")
        image_index = 0
        for idx, msg in enumerate(incoming_msgs, start=1):
            if msg.msg_type == "image":
                image_index += 1
                incoming_parts.append("[图片]")
                self.ui_status(f"🖼️ 正在识图 {image_index}/{image_total}...")
                summary = self._describe_incoming_image(msg)
                if summary:
                    image_parts.append(f"图片{idx}：{summary}")
            else:
                text = (msg.text or "").strip()
                if text:
                    incoming_parts.append(text)
        incoming_text = "\n".join(incoming_parts).strip() or "[空消息]"
        image_context = "\n".join(image_parts).strip()
        return incoming_text, image_context

    def _parse_actions(self, payload: dict) -> List[ReplyAction]:
        actions: List[ReplyAction] = []
        if not isinstance(payload, dict):
            return actions
        for item in payload.get("actions") or []:
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("type") or "").strip().lower()
            if action_type == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    actions.append(ReplyAction(action_type="text", text=text))
            elif action_type == "sticker":
                tags_val = item.get("tags") or []
                if isinstance(tags_val, str):
                    tags = [t for t in re.split(r"[\s,，]+", tags_val) if t]
                else:
                    tags = [str(t).strip() for t in tags_val if str(t).strip()]
                if tags:
                    actions.append(ReplyAction(action_type="sticker", tags=tags[:6]))
            elif action_type == "voice":
                text = str(item.get("text") or "").strip()
                if text:
                    actions.append(ReplyAction(action_type="voice", text=text))
        return actions

    def _send_actions(self, actions: List[ReplyAction]) -> tuple[bool, list[str]]:
        history_parts: list[str] = []
        for idx, action in enumerate(actions):
            history_text = ""
            if action.action_type == "text":
                ok = self._send_one(action.text)
                history_text = action.text
            elif action.action_type == "sticker":
                ok = self._send_sticker_by_tags(action.tags)
                history_text = f"[表情包] {' '.join(action.tags)}"
            elif action.action_type == "voice":
                ok = self._send_voice(action.text)
                history_text = f"[语音] {action.text}"
            else:
                ok = True

            if not ok:
                return False, history_parts

            if history_text:
                history_parts.append(history_text)
            if idx < len(actions) - 1:
                time.sleep(self._calc_pause_for_part(history_text, idx))
        return True, history_parts

    def _calc_pause_for_part(self, text: str, idx: int) -> float:
        mult = float(self.cfg.split_speed_multiplier) if self.cfg.split_speed_multiplier else 1.0
        if mult <= 0:
            mult = 1.0
        char_time = float(self.cfg.split_char_time)
        base_pause = float(self.cfg.split_base_pause)
        est = base_pause + (len(text) * char_time) / mult
        est += idx * 0.12 / max(mult, 0.5)
        est += random.uniform(0.0, 0.35) / max(mult, 0.6)
        est = max(float(self.cfg.split_min_pause), min(float(self.cfg.split_max_pause), est))
        return est

    def _report_button_drift(self, btn_ctrl) -> None:
        bound = self.bound_button
        if not bound or not btn_ctrl:
            return
        try:
            current_name = (getattr(btn_ctrl, "Name", "") or "").strip()
        except Exception:
            current_name = ""
        bound_name = (bound.name or "").strip()
        if bound_name and current_name and current_name != bound_name:
            self.ui_status(f"⚠️ 发送按钮重获漂移：绑定的是“{bound_name}”，当前拿到的是“{current_name}”")

    def _send_one(self, text: str) -> bool:
        with self._uia_lock:
            edit_ctrl = reacquire(self.bound_edit, self.tk_hwnd)
            btn_ctrl = reacquire(self.bound_button, self.tk_hwnd)
            if not edit_ctrl or not btn_ctrl:
                return False
            self._report_button_drift(btn_ctrl)
            try:
                chat_hwnd = int(getattr(edit_ctrl, "NativeWindowHandle", 0) or 0)
            except Exception:
                chat_hwnd = 0
            r1 = try_input_text(edit_ctrl, text, hwnd=chat_hwnd)
            if r1 in ("failed", "blocked"):
                return False
            self._register_self_outgoing(text)
            r2 = try_click_button(btn_ctrl, hwnd=chat_hwnd)
            if r2 == "failed":
                try:
                    auto.SendKeys("{ENTER}", waitTime=0.01)
                    return True
                except Exception:
                    return False
            return True

    def _send_sticker_by_tags(self, tags: List[str]) -> bool:
        prompt = " ".join([t for t in tags if t]).strip()
        if not prompt:
            return False
        api = (getattr(self.cfg, "sticker_selector_api", "") or "").strip()
        if not api or not bool(getattr(self.cfg, "sticker_selector_enabled", False)):
            self.ui_status("⚠️ 表情包功能未开启，已回退为文本")
            return self._send_one(prompt)

        selector = StickerSelectorClient(api)
        k_cfg = getattr(self.cfg, "sticker_selector_k", 3) or 3
        series = getattr(self.cfg, "sticker_selector_series", "") or ""
        order = (getattr(self.cfg, "sticker_selector_order", "") or "desc").strip() or "desc"
        random_mode = bool(getattr(self.cfg, "sticker_selector_random", False))
        embed_raw_min = getattr(self.cfg, "sticker_selector_embed_raw_min", 0.0)
        choice = selector.choose(prompt, selector.normalize_k(k_cfg), series, order, random_mode, embed_raw_min)
        if choice.error:
            self.ui_status(f"⚠️ 表情包选择失败：{choice.error}")
            return False
        if not choice.picked:
            self.ui_status("⚠️ 表情包接口没有返回结果")
            return False
        self.ui_status(f"🎨 已选择表情包：{' '.join(tags)}")
        return self._send_sticker(choice.picked)

    def _guess_ext(self, url: str, content_type: str, data: bytes) -> str:
        u = (url or "").lower()
        ct = (content_type or "").lower()
        m = re.search(r"\.(gif|png|webp|jpg|jpeg|bmp|mp3|wav|ogg|m4a)(?:\?|#|$)", u)
        if m:
            return "." + m.group(1)
        if "image/gif" in ct:
            return ".gif"
        if "image/png" in ct:
            return ".png"
        if "image/webp" in ct:
            return ".webp"
        if "image/jpeg" in ct or "image/jpg" in ct:
            return ".jpg"
        if "audio/mpeg" in ct:
            return ".mp3"
        if "audio/wav" in ct or "audio/x-wav" in ct:
            return ".wav"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return ".gif"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if data[:2] == b"\xff\xd8":
            return ".jpg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        return ".bin"

    def _copy_files_to_clipboard(self, paths: List[str]) -> bool:
        abs_paths = [os.path.abspath(p) for p in paths if p and os.path.exists(p)]
        if not abs_paths:
            return False
        file_list = ("\0".join(abs_paths) + "\0\0").encode("utf-16le")
        dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1) + file_list
        for _ in range(8):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                time.sleep(0.05)
        else:
            return False
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, dropfiles)
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
        return True

    def _paste_files_and_send(self, paths: List[str], register_text: str = "", confirm_with_enter: bool = False) -> bool:
        with self._uia_lock:
            edit_ctrl = reacquire(self.bound_edit, self.tk_hwnd)
            btn_ctrl = reacquire(self.bound_button, self.tk_hwnd)
            if not edit_ctrl or not btn_ctrl:
                return False
            self._report_button_drift(btn_ctrl)
            try:
                chat_hwnd = int(getattr(edit_ctrl, "NativeWindowHandle", 0) or 0)
            except Exception:
                chat_hwnd = 0
            if not self._copy_files_to_clipboard(paths):
                return False
            try:
                try:
                    edit_ctrl.SetFocus()
                except Exception:
                    pass
                time.sleep(0.08)
                auto.SendKeys("{CTRL}v", waitTime=0.02)
                time.sleep(0.20)
                if register_text:
                    self._register_self_outgoing(register_text)
                if confirm_with_enter:
                    auto.SendKeys("{ENTER}", waitTime=0.01)
                    return True
                r2 = try_click_button(btn_ctrl, hwnd=chat_hwnd)
                if r2 == "failed":
                    auto.SendKeys("{ENTER}", waitTime=0.01)
                return True
            except Exception as exc:
                self.ui_status(f"⚠️ 粘贴文件发送失败：{exc}")
                return False

    def _send_sticker(self, item: dict) -> bool:
        if not isinstance(item, dict):
            return False
        url = item.get("url")
        if not url:
            return False
        if not (url.startswith("http://") or url.startswith("https://")):
            base = getattr(self.cfg, "sticker_selector_api", "") or ""
            if base:
                url = base.rstrip("/") + "/" + str(url).lstrip("/")

        try:
            import requests
            r = requests.get(url, stream=True, timeout=10)
            r.raise_for_status()
            content_type = (r.headers.get("Content-Type", "") or "")
            data = r.content
        except Exception as exc:
            self.ui_status(f"⚠️ 下载表情失败：{exc}")
            return False

        try:
            tmp_dir = os.path.join(tempfile.gettempdir(), "StickerSelectorCache")
            os.makedirs(tmp_dir, exist_ok=True)
            ext = self._guess_ext(url, content_type, data)
            tmp_path = os.path.join(tmp_dir, f"sticker_{uuid.uuid4().hex}{ext}")
            with open(tmp_path, "wb") as f:
                f.write(data)
        except Exception as exc:
            self.ui_status(f"⚠️ 写入表情临时文件失败：{exc}")
            return False

        if self._paste_files_and_send([tmp_path], register_text=str(item.get("url") or url)):
            return True

        try:
            bio = BytesIO()
            img = Image.open(BytesIO(data)).convert("RGB")
            img.save(bio, format="BMP")
            bmp_data = bio.getvalue()[14:]
            for _ in range(6):
                try:
                    win32clipboard.OpenClipboard()
                    break
                except Exception:
                    time.sleep(0.05)
            else:
                raise RuntimeError("OpenClipboard failed")
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_DIB, bmp_data)
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass

            with self._uia_lock:
                edit_ctrl = reacquire(self.bound_edit, self.tk_hwnd)
                btn_ctrl = reacquire(self.bound_button, self.tk_hwnd)
                if not edit_ctrl or not btn_ctrl:
                    return False
                self._report_button_drift(btn_ctrl)
                try:
                    chat_hwnd = int(getattr(edit_ctrl, "NativeWindowHandle", 0) or 0)
                except Exception:
                    chat_hwnd = 0
                try:
                    edit_ctrl.SetFocus()
                except Exception:
                    pass
                time.sleep(0.06)
                auto.SendKeys("{CTRL}v", waitTime=0.02)
                time.sleep(0.10)
                self._register_self_outgoing(str(item.get("url") or url))
                r2 = try_click_button(btn_ctrl, hwnd=chat_hwnd)
                if r2 == "failed":
                    auto.SendKeys("{ENTER}", waitTime=0.01)
                return True
        except Exception as exc:
            self.ui_status(f"⚠️ 位图回退也失败：{exc}")
        return False

    def _resolve_chat_hwnd(self, *controls) -> int:
        for ctrl in controls:
            try:
                hwnd = int(getattr(ctrl, "NativeWindowHandle", 0) or 0)
            except Exception:
                hwnd = 0
            if hwnd:
                return hwnd

        for bound in (self.bound_voice_button, self.bound_edit, self.bound_button, self.bound_window):
            if not bound:
                continue
            for attr in ("root_hwnd", "native_hwnd"):
                try:
                    hwnd = int(getattr(bound, attr, 0) or 0)
                except Exception:
                    hwnd = 0
                if hwnd:
                    return hwnd
        return 0

    def _key_down(self, vk_code: int):
        if self._send_input_key(vk_code, key_up=False):
            return
        try:
            scan_code = win32api.MapVirtualKey(vk_code, 0)
            win32api.keybd_event(vk_code, scan_code, 0, 0)
        except Exception:
            pass

    def _key_up(self, vk_code: int):
        if self._send_input_key(vk_code, key_up=True):
            return
        try:
            scan_code = win32api.MapVirtualKey(vk_code, 0)
            win32api.keybd_event(vk_code, scan_code, win32con.KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

    def _send_input_key(self, vk_code: int, key_up: bool) -> bool:
        try:
            user32 = ctypes.windll.user32
            scan_code = user32.MapVirtualKeyW(int(vk_code), 0)

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", ctypes.c_ushort),
                    ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class _INPUTUNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]

            flags = getattr(win32con, "KEYEVENTF_SCANCODE", 0x0008)
            if key_up:
                flags |= win32con.KEYEVENTF_KEYUP
            inp = INPUT(
                type=1,
                union=_INPUTUNION(
                    ki=KEYBDINPUT(
                        wVk=0,
                        wScan=scan_code,
                        dwFlags=flags,
                        time=0,
                        dwExtraInfo=None,
                    )
                ),
            )
            sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            return bool(sent == 1)
        except Exception:
            return False

    def _tap_key(self, vk_code: int, hold_sec: float = 0.03, repeat: int = 1, gap_sec: float = 0.04):
        repeat_count = max(1, int(repeat or 1))
        for idx in range(repeat_count):
            self._key_down(vk_code)
            time.sleep(max(0.0, hold_sec))
            self._key_up(vk_code)
            if idx < repeat_count - 1 and gap_sec > 0:
                time.sleep(gap_sec)

    def _post_key_message(self, hwnd: int, vk_code: int, key_up: bool, repeat: bool = False):
        if not hwnd:
            return
        try:
            scan_code = int(win32api.MapVirtualKey(vk_code, 0) or 0)
            lparam = 1 | (scan_code << 16)
            msg = win32con.WM_KEYUP if key_up else win32con.WM_KEYDOWN
            if key_up:
                lparam |= (1 << 30) | (1 << 31)
            elif repeat:
                lparam |= (1 << 30)
            win32gui.PostMessage(hwnd, msg, vk_code, lparam)
        except Exception:
            pass

    def _start_key_hold(self, vk_code: int, hwnd: int, interval_sec: float = 0.12):
        stop_event = threading.Event()
        interval = max(0.05, float(interval_sec or 0.12))

        self._key_down(vk_code)
        self._post_key_message(hwnd, vk_code, key_up=False)

        def worker():
            next_physical_down = time.perf_counter() + 0.45
            while not stop_event.wait(interval):
                self._post_key_message(hwnd, vk_code, key_up=False, repeat=True)
                now = time.perf_counter()
                if now >= next_physical_down:
                    self._key_down(vk_code)
                    next_physical_down = now + 0.45

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return stop_event, thread

    def _stop_key_hold(self, vk_code: int, hwnd: int, hold_state):
        stop_event, thread = hold_state
        try:
            stop_event.set()
            thread.join(timeout=0.35)
        except Exception:
            pass
        finally:
            self._post_key_message(hwnd, vk_code, key_up=True)
            self._key_up(vk_code)

    def _focus_chat_for_voice(self, chat_hwnd: int, window_ctrl=None):
        bring_to_foreground(chat_hwnd)
        try:
            if chat_hwnd:
                win32gui.SetFocus(chat_hwnd)
        except Exception:
            pass
        try:
            if chat_hwnd:
                auto.ControlFromHandle(chat_hwnd).SetFocus()
        except Exception:
            pass
        try:
            if window_ctrl:
                window_ctrl.SetFocus()
        except Exception:
            pass

    def _create_tts_audio_file(self, text: str, preferred_format: str | None = None) -> tuple[str | None, str]:
        if not bool(getattr(self.cfg, "tts_enabled", False)):
            self.ui_status("⚠️ 语音功能未开启，已回退为文本")
            return None, ""

        synth = getattr(self.llm, "synthesize_speech", None)
        if not callable(synth):
            self.ui_status("⚠️ 当前 LLM 客户端不支持 TTS")
            return None, ""

        try:
            audio_bytes = synth(text, preferred_format=preferred_format)
        except TypeError:
            audio_bytes = synth(text)

        if not audio_bytes:
            detail = ""
            getter = getattr(self.llm, "get_last_tts_error", None)
            if callable(getter):
                detail = str(getter() or "").strip()
            self.ui_status(f"⚠️ TTS 生成失败：{detail}" if detail else "⚠️ TTS 生成失败")
            return None, ""

        detected_ext = ""
        fmt_getter = getattr(self.llm, "get_last_tts_format", None)
        if callable(fmt_getter):
            detected_ext = str(fmt_getter() or "").strip().lower()
        ext = detected_ext or (preferred_format or resolve_env(getattr(self.llm, "tts_format_raw", "") or "") or "mp3").strip().lower()
        if ext not in {"mp3", "wav", "ogg", "m4a"}:
            ext = (preferred_format or "mp3").strip().lower() or "mp3"
        tmp_dir = os.path.join(tempfile.gettempdir(), "QQSafeChatVoice")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"voice_{uuid.uuid4().hex}.{ext}")
        try:
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
        except Exception as exc:
            self.ui_status(f"⚠️ 写入语音临时文件失败：{exc}")
            return None, ext

        return tmp_path, ext

    def _send_voice_file(self, text: str) -> bool:
        tmp_path, _ext = self._create_tts_audio_file(text)
        if not tmp_path:
            return False

        ok = self._paste_files_and_send([tmp_path], register_text=f"[语音]{text}", confirm_with_enter=True)
        if ok:
            self.ui_status("🎙️ 已发送语音文件 action")
        return ok

    def _send_voice_real(self, text: str) -> bool:
        if not self.bound_voice_button:
            self.ui_status("⚠️ 真语音模式需要先绑定“语音消息”按钮")
            return False

        tmp_path, ext = self._create_tts_audio_file(text, preferred_format="wav")
        if not tmp_path:
            return False
        if ext != "wav":
            self.ui_status(f"⚠️ 真语音模式需要 WAV 音频，当前返回的是 {ext}")
            return False

        playback_ready, playback_probe_detail = probe_wav_output(
            tmp_path,
            getattr(self.cfg, "real_voice_output_device", "") or "",
        )
        if not playback_ready:
            self.ui_status(f"⚠️ 真语音播放预检失败：{playback_probe_detail}")
            return False

        playback_ok = False
        playback_detail = ""
        with self._uia_lock:
            edit_ctrl = reacquire(self.bound_edit, self.tk_hwnd)
            voice_btn = reacquire(self.bound_voice_button, self.tk_hwnd)
            if not voice_btn:
                self.ui_status("⚠️ 语音消息按钮找不到了，请重新绑定")
                return False

            chat_hwnd = self._resolve_chat_hwnd(edit_ctrl, voice_btn)
            trigger_result = try_click_button(voice_btn, hwnd=chat_hwnd)
            if trigger_result == "failed":
                self.ui_status("⚠️ 无法触发语音消息按钮")
                return False

            # 语音页切换完成前过早按下空格会被吃掉，这里固定等 1 秒。
            time.sleep(1.0)
            window_ctrl = reacquire(self.bound_window, self.tk_hwnd)
            self._focus_chat_for_voice(chat_hwnd, window_ctrl=window_ctrl)
            time.sleep(0.12)
            space_hold = self._start_key_hold(win32con.VK_SPACE, chat_hwnd)
            try:
                start_delay = max(0.0, float(getattr(self.cfg, "real_voice_start_delay_sec", 0.5) or 0.5))
                if start_delay > 0:
                    time.sleep(start_delay)
                playback_ok, playback_detail, _duration = play_wav_to_output(
                    tmp_path,
                    getattr(self.cfg, "real_voice_output_device", "") or "",
                )
            finally:
                self._stop_key_hold(win32con.VK_SPACE, chat_hwnd, space_hold)

            time.sleep(0.10)
            window_ctrl = reacquire(self.bound_window, self.tk_hwnd)
            self._focus_chat_for_voice(chat_hwnd, window_ctrl=window_ctrl)
            time.sleep(0.06)
            self._tap_key(win32con.VK_ESCAPE, hold_sec=0.01, repeat=1, gap_sec=0.0)

        if not playback_ok:
            self.ui_status(f"⚠️ 真语音播放失败：{playback_detail}")
            return False

        self._register_self_outgoing(f"[语音]{text}")
        self.ui_status(f"🎙️ 已发送真语音 action（扬声器={playback_detail or '系统默认输出设备'}）")
        return True

    def _send_voice(self, text: str) -> bool:
        if not bool(getattr(self.cfg, "tts_enabled", False)):
            self.ui_status("⚠️ 语音功能未开启，已回退为文本")
            return self._send_one(text)

        mode = str(getattr(self.cfg, "voice_send_mode", "file") or "file").strip().lower()
        if mode == "real":
            return self._send_voice_real(text)

        return self._send_voice_file(text)

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
    def _parse_cf_html_payload(html_text: str) -> dict:
        source_url = ""
        source_match = re.search(r"^SourceURL:(.*)$", html_text, re.MULTILINE)
        if source_match:
            source_url = source_match.group(1).strip()

        fragment = ""
        fragment_match = re.search(r"<!--StartFragment-->(.*?)<!--EndFragment-->", html_text, re.IGNORECASE | re.DOTALL)
        if fragment_match:
            fragment = fragment_match.group(1).strip()

        search_scope = fragment or html_text
        img_src = ""
        img_match = re.search(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", search_scope, re.IGNORECASE)
        if img_match:
            img_src = img_match.group(1).strip()

        local_path = ""
        if img_src.lower().startswith("file://"):
            raw_path = img_src[7:]
            if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
                raw_path = raw_path[1:]
            local_path = raw_path.replace("/", "\\")
        return {"source_url": source_url, "fragment": fragment, "img_src": img_src, "local_path": local_path}

    def _read_image_path_from_clipboard(self) -> str:
        _name, html_data = self._clipboard_get_named_payload(("HTML Format", "text/html"))
        if html_data:
            html_text = html_data.decode("utf-8", errors="replace") if isinstance(html_data, bytes) else str(html_data)
            info = self._parse_cf_html_payload(html_text)
            local_path = info.get("local_path") or ""
            if local_path and Path(local_path).exists():
                return str(Path(local_path))

        try:
            payload = ImageGrab.grabclipboard()
        except Exception:
            payload = None

        if isinstance(payload, list):
            for path in payload:
                p = str(path)
                if os.path.exists(p) and os.path.splitext(p)[1].lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                    return p
        if isinstance(payload, Image.Image):
            tmp_dir = os.path.join(tempfile.gettempdir(), "QQSafeChatImageCache")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_path = os.path.join(tmp_dir, f"image_{uuid.uuid4().hex}.png")
            payload.save(tmp_path, format="PNG")
            return tmp_path
        return ""

    @staticmethod
    def _rect_overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        inter_left = max(a[0], b[0])
        inter_top = max(a[1], b[1])
        inter_right = min(a[2], b[2])
        inter_bottom = min(a[3], b[3])
        inter_w = max(0, inter_right - inter_left)
        inter_h = max(0, inter_bottom - inter_top)
        inter_area = inter_w * inter_h
        if inter_area <= 0:
            return 0.0
        area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
        area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
        return inter_area / float(min(area_a, area_b))

    @staticmethod
    def _control_rect_tuple(ctrl) -> tuple[int, int, int, int] | None:
        try:
            rect = ctrl.BoundingRectangle
            left = int(rect.left)
            top = int(rect.top)
            right = int(rect.right)
            bottom = int(rect.bottom)
        except Exception:
            return None
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom

    @staticmethod
    def _image_cache_path(prefix: str = "image") -> str:
        tmp_dir = os.path.join(tempfile.gettempdir(), "QQSafeChatImageCache")
        os.makedirs(tmp_dir, exist_ok=True)
        return os.path.join(tmp_dir, f"{prefix}_{uuid.uuid4().hex}.png")

    def _screenshot_rect_to_file(self, rect: tuple[int, int, int, int] | None, prefix: str = "region") -> str:
        if not rect:
            return ""
        left, top, right, bottom = [int(v) for v in rect]
        if right <= left or bottom <= top:
            return ""
        out_path = self._image_cache_path(prefix)
        try:
            img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
            if img.width <= 1 or img.height <= 1:
                return ""
            img.save(out_path, format="PNG")
            return out_path if os.path.exists(out_path) else ""
        except Exception:
            return ""

    def _capture_control_or_rect_to_file(self, ctrl, fallback_rect: tuple[int, int, int, int] | None) -> str:
        if ctrl:
            out_path = self._image_cache_path("control")
            try:
                if bool(ctrl.CaptureToImage(out_path)) and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return out_path
            except Exception:
                pass
            path = self._screenshot_rect_to_file(self._control_rect_tuple(ctrl), prefix="control_region")
            if path:
                return path
        return self._screenshot_rect_to_file(fallback_rect, prefix="message_region")

    def _find_image_control(self, root_ctrl, msg: ExtractedMessage):
        target_rect = (int(msg.left), int(msg.top), int(msg.right), int(msg.bottom))
        target_cx = (target_rect[0] + target_rect[2]) / 2.0
        target_cy = (target_rect[1] + target_rect[3]) / 2.0

        best: tuple[float, object] | None = None

        def rec(ctrl, depth: int):
            nonlocal best
            if not ctrl or depth > 10:
                return

            try:
                ctype = getattr(ctrl, "ControlTypeName", "") or ""
            except Exception:
                ctype = ""

            if ctype == "ImageControl":
                rect = self._control_rect_tuple(ctrl)
                if rect:
                    overlap = self._rect_overlap_ratio(rect, target_rect)
                    center_x = (rect[0] + rect[2]) / 2.0
                    center_y = (rect[1] + rect[3]) / 2.0
                    dist = abs(center_x - target_cx) + abs(center_y - target_cy)
                    score = overlap * 2400.0 - dist * 2.2
                    if overlap >= 0.35:
                        score += 320.0
                    if best is None or score > best[0]:
                        best = (score, ctrl)

            try:
                children = ctrl.GetChildren()
            except Exception:
                children = []
            for child in children:
                rec(child, depth + 1)

        rec(root_ctrl, 0)
        return best[1] if best else None

    def _copy_from_focused_control(self, ctrl, root_ctrl) -> str:
        focus_chain = []
        seen = set()
        current = ctrl
        for _ in range(4):
            if not current:
                break
            key = id(current)
            if key in seen:
                break
            seen.add(key)
            focus_chain.append(current)
            try:
                current = current.GetParentControl()
            except Exception:
                break
        if root_ctrl and id(root_ctrl) not in seen:
            focus_chain.append(root_ctrl)

        for candidate in focus_chain:
            self._clipboard_clear()
            try:
                candidate.SetFocus()
            except Exception:
                continue
            time.sleep(0.06)
            try:
                auto.SendKeys("{CTRL}c", waitTime=0.05)
            except Exception:
                continue
            time.sleep(0.18)
            path = self._read_image_path_from_clipboard()
            if path:
                return path
        return ""

    def _extract_image_file_from_message(self, msg: ExtractedMessage) -> str:
        if msg.image_path and os.path.exists(msg.image_path):
            return msg.image_path
        with self._uia_lock:
            list_ctrl = reacquire(self.bound_window, self.tk_hwnd)
            if not list_ctrl:
                return ""
            try:
                hwnd = int(getattr(self.bound_window, "root_hwnd", 0) or 0)
            except Exception:
                hwnd = 0
            if not hwnd:
                try:
                    hwnd = int(getattr(list_ctrl, "NativeWindowHandle", 0) or 0)
                except Exception:
                    hwnd = 0

        if msg.right <= msg.left or msg.bottom <= msg.top:
            return ""

        bring_to_foreground(hwnd)
        time.sleep(0.08)

        target_rect = (int(msg.left), int(msg.top), int(msg.right), int(msg.bottom))
        image_ctrl = self._find_image_control(list_ctrl, msg)
        path = self._copy_from_focused_control(image_ctrl, list_ctrl) if image_ctrl else ""
        if path:
            msg.image_path = path
            return path
        path = self._capture_control_or_rect_to_file(image_ctrl, target_rect)
        if path:
            msg.image_path = path
            self.ui_status("🖼️ 自动获取原图失败，已回退为当前图片区域截图")
        elif image_ctrl is None:
            self.ui_status("⚠️ 找到了图片消息，但没有在当前绑定窗口里定位到对应图片控件")
        return path

    def _describe_incoming_image(self, msg: ExtractedMessage) -> str:
        if not bool(getattr(self.cfg, "vision_enabled", False)):
            return ""
        if msg.image_summary:
            return msg.image_summary
        image_path = self._extract_image_file_from_message(msg)
        if not image_path:
            self.ui_status("⚠️ 图片已检测到，但自动获取原图失败")
            return ""
        summary = (self.llm.describe_image(image_path) or "").strip()
        msg.image_path = image_path
        msg.image_summary = summary
        if summary:
            try:
                with self._history_lock:
                    if self.history.items:
                        last = self.history.items[-1]
                        if str(last.get("msg_type") or "") == "image" and str(last.get("sender") or "") == "other":
                            last["image_path"] = image_path
                            last["image_summary"] = summary
                            self.history.save()
            except Exception:
                pass
            self.ui_status(f"🖼️ 识图完成：{summary}")
        return summary
