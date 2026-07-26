
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class OpenAISettings:
    provider: str = "openai"  # 可选值: mock, openai, siliconflow
    api_key: str = "$OPENAI_API_KEY"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    temperature: float = 0.6

    system_prompt: str = "你是一个中文私聊代聊助手。你的回复要自然、像真人、克制，不要解释规则，不要写旁白。"
    user_template: str = (
        "请根据聊天历史、对方最新消息和可选识图结果，输出一个 JSON actions 对象。\n\n"
        "【聊天上下文】\n"
        "{history}\n\n"
        "【对方最新消息】\n"
        "{incoming}\n\n"
        "【识图结果】\n"
        "{image_context}"
    )
    vision_model: str = ""
    vision_prompt: str = "请用中文简洁描述这张聊天图片的关键信息，包含主体、动作、情绪和明显文字，控制在 80 字以内。"
    tts_provider: str = "openai"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    tts_format: str = "mp3"
    tts_language_type: str = "auto"
    tts_api_key: str = ""
    tts_base_url: str = ""


class SettingsStore:
    def __init__(self, path: str):
        self.path = path
        self.settings = OpenAISettings()
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            for k, v in data.items():
                if hasattr(self.settings, k):
                    setattr(self.settings, k, v)
        except Exception:
            pass

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        except Exception:
            pass
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(asdict(self.settings), f, ensure_ascii=False, indent=2)
        except Exception:
            pass
