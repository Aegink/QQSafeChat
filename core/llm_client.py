from __future__ import annotations

from typing import Optional, Callable, Dict, Any
import base64
import json
import mimetypes
import os
from pathlib import Path
import re


time_out = 60


def resolve_env(value: str) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if s.startswith("$") and len(s) > 1:
        return os.environ.get(s[1:].strip(), "").strip()
    return s


def _strip_code_fence(text: str) -> str:
    s = str(text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _extract_json_text(raw: str) -> str:
    cleaned = _strip_code_fence(raw)
    if not cleaned:
        return "{}"
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _normalize_action_payload(obj: Any, fallback_text: str = "") -> Dict[str, Any]:
    actions_raw = obj.get("actions") if isinstance(obj, dict) else None
    normalized: list[dict[str, Any]] = []

    if isinstance(actions_raw, list):
        for item in actions_raw:
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("type") or item.get("action_type") or "").strip().lower()
            if action_type == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    normalized.append({"type": "text", "text": text})
            elif action_type == "sticker":
                tags_val = item.get("tags")
                if isinstance(tags_val, str):
                    tags = [t for t in re.split(r"[\s,，]+", tags_val) if t]
                elif isinstance(tags_val, list):
                    tags = [str(t).strip() for t in tags_val if str(t).strip()]
                else:
                    tags = []
                if tags:
                    normalized.append({"type": "sticker", "tags": tags[:6]})
            elif action_type == "voice":
                text = str(item.get("text") or "").strip()
                if text:
                    normalized.append({"type": "voice", "text": text})

    if not normalized:
        fallback = str(fallback_text or "").strip()
        if not fallback and isinstance(obj, dict):
            fallback = str(obj.get("text") or obj.get("reply") or "").strip()
        if fallback:
            normalized = [{"type": "text", "text": fallback}]

    return {"version": 1, "actions": normalized}


def _parse_action_response(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    try:
        obj = json.loads(_extract_json_text(text))
        return _normalize_action_payload(obj, fallback_text=text)
    except Exception as exc:
        payload = _normalize_action_payload({}, fallback_text=text)
        payload["parse_error"] = str(exc)
        return payload


def _file_to_data_url(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "application/octet-stream"
    data = base64.b64encode(open(path, "rb").read()).decode("ascii")
    return f"data:{mime};base64,{data}"


class BaseLLMClient:
    def __init__(self):
        self._debug_hook: Optional[Callable[[Dict[str, Any]], None]] = None
        self._last_tts_error: str = ""
        self._last_tts_format: str = ""

    def set_debug_hook(self, hook: Optional[Callable[[Dict[str, Any]], None]]):
        self._debug_hook = hook

    def _emit_debug(self, data: Dict[str, Any]):
        try:
            if self._debug_hook:
                self._debug_hook(data)
        except Exception:
            pass

    def _set_last_tts_error(self, message: str):
        self._last_tts_error = str(message or "").strip()

    def get_last_tts_error(self) -> str:
        return self._last_tts_error

    def _set_last_tts_format(self, audio_format: str):
        self._last_tts_format = str(audio_format or "").strip().lower()

    def get_last_tts_format(self) -> str:
        return self._last_tts_format

    def build_request(
        self,
        history_text: str,
        new_incoming: str,
        persona_text: str = "",
        image_context: str = "",
        allow_sticker: bool = False,
        allow_voice: bool = False,
    ) -> Dict[str, Any]:
        raise NotImplementedError()

    def generate_actions(
        self,
        history_text: str,
        new_incoming: str,
        persona_text: str = "",
        image_context: str = "",
        allow_sticker: bool = False,
        allow_voice: bool = False,
    ) -> Dict[str, Any]:
        raise NotImplementedError()

    def describe_image(self, image_path: str) -> str:
        return ""

    def synthesize_speech(self, text: str, preferred_format: str | None = None) -> bytes | None:
        return None


class MockLLMClient(BaseLLMClient):
    def build_request(
        self,
        history_text: str,
        new_incoming: str,
        persona_text: str = "",
        image_context: str = "",
        allow_sticker: bool = False,
        allow_voice: bool = False,
    ) -> Dict[str, Any]:
        feature_text = []
        if allow_sticker:
            feature_text.append("sticker")
        if allow_voice:
            feature_text.append("voice")
        payload = {
            "mock": True,
            "history": history_text,
            "incoming": new_incoming,
            "image_context": image_context,
            "features": feature_text,
        }
        return {
            "provider": "mock",
            "system": "MOCK SYSTEM",
            "user": f"incoming={new_incoming}",
            "payload": payload,
            "url": "",
            "headers": {},
            "meta": {"provider": "mock", "features": feature_text},
        }

    def generate_actions(
        self,
        history_text: str,
        new_incoming: str,
        persona_text: str = "",
        image_context: str = "",
        allow_sticker: bool = False,
        allow_voice: bool = False,
    ) -> Dict[str, Any]:
        req = self.build_request(
            history_text,
            new_incoming,
            persona_text,
            image_context,
            allow_sticker,
            allow_voice,
        )
        self._emit_debug({"phase": "pre_request", **req})
        text = f"收到：{new_incoming[:40]}"
        if image_context.strip():
            text += f"（我看到了图片：{image_context[:40]}）"
        result = {"version": 1, "actions": [{"type": "text", "text": text}]}
        self._emit_debug({"phase": "post_response", "raw_output": json.dumps(result, ensure_ascii=False), "parsed_actions": result})
        return result

    def describe_image(self, image_path: str) -> str:
        return f"图片文件：{os.path.basename(image_path)}"


class OpenAICompatibleClient(BaseLLMClient):
    provider_name = "openai-compatible"
    default_base_url = ""
    default_model = ""
    missing_key_error = "（未设置 API Key）"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float,
        system_prompt: str,
        user_template: str,
        vision_model: str = "",
        vision_prompt: str = "",
        tts_provider: str = "openai",
        tts_model: str = "",
        tts_voice: str = "alloy",
        tts_format: str = "mp3",
        tts_language_type: str = "auto",
        tts_api_key: str = "",
        tts_base_url: str = "",
    ):
        super().__init__()
        self.api_key_raw = api_key
        self.base_url_raw = base_url
        self.model_raw = model
        self.temperature = float(temperature)
        self.system_prompt_raw = system_prompt
        self.user_template_raw = user_template
        self.vision_model_raw = vision_model
        self.vision_prompt_raw = vision_prompt
        self.tts_provider_raw = tts_provider
        self.tts_model_raw = tts_model
        self.tts_voice_raw = tts_voice
        self.tts_format_raw = tts_format
        self.tts_language_type_raw = tts_language_type
        self.tts_api_key_raw = tts_api_key
        self.tts_base_url_raw = tts_base_url

    def _resolved(self):
        api_key = resolve_env(self.api_key_raw)
        base_url = resolve_env(self.base_url_raw).rstrip("/") or self.default_base_url
        model = resolve_env(self.model_raw) or self.default_model
        system_prompt = resolve_env(self.system_prompt_raw)
        user_template = resolve_env(self.user_template_raw)
        vision_model = resolve_env(self.vision_model_raw)
        vision_prompt = resolve_env(self.vision_prompt_raw)
        tts_provider = resolve_env(self.tts_provider_raw).strip().lower() or "openai"
        tts_model = resolve_env(self.tts_model_raw)
        tts_voice = resolve_env(self.tts_voice_raw)
        tts_format = resolve_env(self.tts_format_raw)
        tts_language_type = resolve_env(self.tts_language_type_raw)
        return (
            api_key,
            base_url,
            model,
            system_prompt,
            user_template,
            vision_model,
            vision_prompt,
            tts_provider,
            tts_model,
            tts_voice,
            tts_format,
            tts_language_type,
        )

    def _resolved_tts_transport(self, fallback_api_key: str, fallback_base_url: str) -> tuple[str, str]:
        tts_api_key = resolve_env(self.tts_api_key_raw) or fallback_api_key
        tts_base_url = resolve_env(self.tts_base_url_raw).rstrip("/") or fallback_base_url
        return tts_api_key, tts_base_url

    def _build_action_rules(self, allow_sticker: bool, allow_voice: bool) -> str:
        lines = [
            "【输出协议】",
            "- 你只能输出一个 JSON 对象，不要输出 markdown、解释、代码块或前后缀。",
            "- 固定 schema：{\"version\":1,\"actions\":[...]}。",
            "- actions 按实际发送顺序排列。",
            "- text action 格式：{\"type\":\"text\",\"text\":\"...\"}。",
            "- text 要像真人聊天，不要写舞台说明、括号心理活动、解释性旁白。",
            "- 一次回复尽量 1~3 个 action，避免刷屏。",
        ]
        if allow_sticker:
            lines.append(
                "- 允许 sticker action：{\"type\":\"sticker\",\"tags\":[\"标签1\",\"标签2\"]}，标签 2~5 个，越具体越好。"
            )
        else:
            lines.append("- 不允许 sticker action。")
        if allow_voice:
            lines.append(
                "- 允许 voice action：{\"type\":\"voice\",\"text\":\"要读出来的话\"}，适合短句、情绪更强、像语音更自然的场景。"
            )
        else:
            lines.append("- 不允许 voice action。")
        lines.append("- 如果不需要特殊动作，只输出单个 text action。")
        return "\n".join(lines)

    def build_request(
        self,
        history_text: str,
        new_incoming: str,
        persona_text: str = "",
        image_context: str = "",
        allow_sticker: bool = False,
        allow_voice: bool = False,
    ) -> Dict[str, Any]:
        (
            api_key,
            base_url,
            model,
            system_prompt,
            user_template,
            _vision_model,
            _vision_prompt,
            _tts_provider,
            _tts_model,
            _tts_voice,
            _tts_format,
            _tts_language_type,
        ) = self._resolved()

        if not api_key:
            return {"error": self.missing_key_error}
        if not base_url:
            return {"error": "（Base URL 为空）"}
        if not model:
            return {"error": "（Model 为空）"}
        if not user_template.strip():
            user_template = (
                "你在扮演聊天对象。请结合聊天历史、对方最新消息和可选的识图结果，输出符合 schema 的 JSON。\n\n"
                "【聊天历史】\n{history}\n\n【对方最新消息】\n{incoming}\n\n【识图结果】\n{image_context}"
            )

        system_parts = [system_prompt.strip() or "你是一个自然、克制、会聊天的中文回复助手。"]
        if persona_text.strip():
            system_parts.append("【人格设定】\n" + persona_text.strip())
        system_parts.append(self._build_action_rules(allow_sticker, allow_voice))
        system_final = "\n\n".join(system_parts)

        image_context_final = image_context.strip() or "（无）"
        try:
            user_prompt = user_template.format(
                history=history_text,
                incoming=new_incoming,
                image_context=image_context_final,
            )
        except Exception as exc:
            return {"error": f"（User Template 格式化失败：{exc}）"}

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_final},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        url = f"{base_url}/chat/completions"
        return {
            "provider": self.provider_name,
            "system": system_final,
            "user": user_prompt,
            "payload": payload,
            "url": url,
            "headers": {
                "Authorization": f"Bearer {'****' + api_key[-4:] if api_key else ''}",
                "Content-Type": "application/json",
            },
            "meta": {
                "provider": self.provider_name,
                "base_url": base_url,
                "model": model,
                "temperature": self.temperature,
                "allow_sticker": allow_sticker,
                "allow_voice": allow_voice,
                "persona_attached": bool(persona_text.strip()),
            },
        }

    def _chat_request(self, payload: Dict[str, Any], url: str, api_key: str) -> Dict[str, Any]:
        try:
            import requests

            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                data=json.dumps(payload, ensure_ascii=False),
                timeout=time_out,
            )
            r.raise_for_status()
            return r.json()
        except Exception as req_error:
            if payload.get("response_format"):
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)
                try:
                    return self._chat_request(fallback_payload, url, api_key)
                except Exception:
                    pass
            try:
                import urllib.request

                req_u = urllib.request.Request(
                    url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_u, timeout=time_out) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as url_error:
                raise RuntimeError(f"{req_error} | fallback={url_error}") from url_error

    def _extract_chat_text(self, data: Dict[str, Any]) -> str:
        raw = (data.get("choices", [{}])[0].get("message", {}).get("content") or "")
        return str(raw or "").strip()

    def _guess_audio_ext(self, content_type: str = "", url: str = "") -> str:
        ct = (content_type or "").lower()
        lower_url = (url or "").lower()
        if "audio/mpeg" in ct or "audio/mp3" in ct:
            return "mp3"
        if "audio/wav" in ct or "audio/x-wav" in ct or "audio/wave" in ct:
            return "wav"
        if "audio/ogg" in ct:
            return "ogg"
        if "audio/mp4" in ct or "audio/x-m4a" in ct or lower_url.endswith(".m4a"):
            return "m4a"
        if lower_url.endswith(".mp3"):
            return "mp3"
        if lower_url.endswith(".wav"):
            return "wav"
        if lower_url.endswith(".ogg"):
            return "ogg"
        return ""

    def _extract_error_message(self, payload: Any, fallback: str = "") -> str:
        if isinstance(payload, dict):
            code = str(payload.get("code") or "").strip()
            err = payload.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "").strip()
                if message:
                    return message
            message = str(payload.get("message") or "").strip()
            if message:
                return f"{code}: {message}" if code else message
            if code:
                return code
        return str(fallback or "").strip()

    def _extract_audio_candidate(self, payload: Any) -> tuple[str, str]:
        if isinstance(payload, dict):
            for key in ("audio", "audio_data", "audio_url", "url", "data", "content"):
                value = payload.get(key)
                if isinstance(value, dict):
                    audio_data, audio_url = self._extract_audio_candidate(value)
                    if audio_data or audio_url:
                        return audio_data, audio_url
                elif isinstance(value, str):
                    text = value.strip()
                    if not text:
                        continue
                    if key in {"audio_url", "url"} or text.startswith("http://") or text.startswith("https://"):
                        return "", text
                    if key in {"audio", "audio_data", "data", "content"}:
                        return text, ""
            for value in payload.values():
                audio_data, audio_url = self._extract_audio_candidate(value)
                if audio_data or audio_url:
                    return audio_data, audio_url
        elif isinstance(payload, list):
            for item in payload:
                audio_data, audio_url = self._extract_audio_candidate(item)
                if audio_data or audio_url:
                    return audio_data, audio_url
        return "", ""

    def _download_audio_bytes(self, url: str) -> tuple[bytes, str]:
        if not url:
            raise RuntimeError("TTS 响应缺少音频下载地址")
        try:
            import requests

            r = requests.get(url, timeout=time_out)
            r.raise_for_status()
            audio_format = self._guess_audio_ext(r.headers.get("Content-Type", ""), url)
            return bytes(r.content), audio_format
        except Exception as req_error:
            if isinstance(req_error, RuntimeError):
                error_message = str(req_error or "").strip()
                self._set_last_tts_error(error_message)
                self._emit_debug({"phase": "tts_error", "error": error_message, "meta": {"model": payload["model"], "voice": payload["voice"], "format": requested_format}})
                return None
            try:
                import urllib.request

                with urllib.request.urlopen(url, timeout=time_out) as resp:
                    data = resp.read()
                    audio_format = self._guess_audio_ext(resp.headers.get("Content-Type", ""), url)
                return data, audio_format
            except Exception as url_error:
                raise RuntimeError(f"下载 TTS 音频失败：{req_error} | fallback={url_error}") from url_error

    def _parse_tts_success(self, content: bytes, content_type: str, requested_format: str) -> tuple[bytes, str]:
        if "application/json" not in (content_type or "").lower():
            audio_format = self._guess_audio_ext(content_type, "") or requested_format
            return bytes(content), audio_format

        try:
            data = json.loads(content.decode("utf-8", errors="replace"))
        except Exception as exc:
            raise RuntimeError(f"TTS 返回了 JSON，但解析失败：{exc}") from exc

        error_message = self._extract_error_message(data)
        if error_message:
            raise RuntimeError(error_message)

        output = data.get("output") if isinstance(data, dict) else None
        audio = output.get("audio") if isinstance(output, dict) else None
        audio_data = ""
        audio_url = ""
        if isinstance(audio, dict):
            audio_data = str(audio.get("data") or "").strip()
            audio_url = str(audio.get("url") or "").strip()
        if not audio_data and not audio_url:
            audio_data, audio_url = self._extract_audio_candidate(data)
        if not audio_data and not audio_url:
            raise RuntimeError("TTS 响应里没有可用音频数据")

        if audio_data:
            if audio_data.startswith("data:"):
                comma = audio_data.find(",")
                header = audio_data[:comma] if comma >= 0 else ""
                body = audio_data[comma + 1 :] if comma >= 0 else audio_data
                mime = header[5:].split(";", 1)[0] if header.startswith("data:") else ""
                audio_format = self._guess_audio_ext(mime, audio_url) or requested_format
            else:
                body = audio_data
                audio_format = self._guess_audio_ext("", audio_url) or requested_format
            try:
                return base64.b64decode(body), audio_format
            except Exception as exc:
                raise RuntimeError(f"TTS 音频 base64 解码失败：{exc}") from exc

        downloaded, detected_format = self._download_audio_bytes(audio_url)
        return downloaded, detected_format or requested_format

    def _parse_tts_http_response(self, status_code: int, content_type: str, content: bytes, requested_format: str) -> tuple[bytes, str]:
        if status_code >= 400:
            message = ""
            if "application/json" in (content_type or "").lower():
                try:
                    message = self._extract_error_message(json.loads(content.decode("utf-8", errors="replace")))
                except Exception:
                    message = ""
            if not message:
                snippet = content.decode("utf-8", errors="replace").strip()[:300]
                message = snippet or f"HTTP {status_code}"
            raise RuntimeError(message)
        return self._parse_tts_success(content, content_type, requested_format)

    def generate_actions(
        self,
        history_text: str,
        new_incoming: str,
        persona_text: str = "",
        image_context: str = "",
        allow_sticker: bool = False,
        allow_voice: bool = False,
    ) -> Dict[str, Any]:
        req = self.build_request(
            history_text,
            new_incoming,
            persona_text,
            image_context,
            allow_sticker,
            allow_voice,
        )
        if "error" in req:
            return {"version": 1, "actions": [{"type": "text", "text": str(req["error"])}]}

        self._emit_debug(
            {
                "phase": "pre_request",
                "system": req["system"],
                "user": req["user"],
                "payload": req["payload"],
                "meta": req.get("meta", {}),
                "url": req.get("url", ""),
                "headers": req.get("headers", {}),
            }
        )

        api_key = resolve_env(self.api_key_raw)
        try:
            data = self._chat_request(req["payload"], req["url"], api_key)
            raw = self._extract_chat_text(data)
            parsed = _parse_action_response(raw)
            self._emit_debug(
                {
                    "phase": "post_response",
                    "raw_output": raw,
                    "parsed_actions": parsed,
                    "usage": data.get("usage", {}),
                    "raw_response": data,
                }
            )
            return parsed
        except Exception as exc:
            self._emit_debug({"phase": "error", "error": str(exc), "meta": {"provider": self.provider_name}})
            return {"version": 1, "actions": [{"type": "text", "text": f"（AI 请求失败：{exc}）"}]}

    def describe_image(self, image_path: str) -> str:
        if not image_path or not os.path.exists(image_path):
            return ""
        (
            api_key,
            base_url,
            model,
            _system_prompt,
            _user_template,
            vision_model,
            vision_prompt,
            _tts_provider,
            _tts_model,
            _tts_voice,
            _tts_format,
            _tts_language_type,
        ) = self._resolved()
        if not api_key or not base_url:
            return ""

        prompt = (vision_prompt or "").strip() or (
            "请用中文简洁描述这张聊天图片的关键信息，包含：主体、动作、情绪、明显文字。如果看不清就直说。控制在 80 字以内。"
        )
        payload = {
            "model": vision_model or model,
            "messages": [
                {"role": "system", "content": "你是一个精炼的识图助手。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _file_to_data_url(image_path)}},
                    ],
                },
            ],
            "temperature": 0.2,
        }
        url = f"{base_url}/chat/completions"
        self._emit_debug(
            {
                "phase": "vision_request",
                "system": "你是一个精炼的识图助手。",
                "user": prompt,
                "payload": payload,
                "meta": {
                    "provider": self.provider_name,
                    "base_url": base_url,
                    "model": vision_model or model,
                    "image_path": str(Path(image_path).name),
                },
                "url": url,
            }
        )
        try:
            data = self._chat_request(payload, url, api_key)
            raw = self._extract_chat_text(data)
            self._emit_debug(
                {
                    "phase": "vision_response",
                    "image_path": image_path,
                    "raw_output": raw,
                    "usage": data.get("usage", {}),
                    "raw_response": data,
                }
            )
            return raw.strip()
        except Exception as exc:
            self._emit_debug(
                {
                    "phase": "vision_error",
                    "error": str(exc),
                    "meta": {
                        "provider": self.provider_name,
                        "base_url": base_url,
                        "model": vision_model or model,
                        "image_path": str(Path(image_path).name),
                    },
                }
            )
            return ""

    def synthesize_speech(self, text: str, preferred_format: str | None = None) -> bytes | None:
        content = str(text or "").strip()
        if not content:
            self._set_last_tts_error("TTS 输入为空")
            self._set_last_tts_format("")
            return None
        (
            api_key,
            base_url,
            _model,
            _system_prompt,
            _user_template,
            _vision_model,
            _vision_prompt,
            tts_provider,
            tts_model,
            tts_voice,
            tts_format,
            tts_language_type,
        ) = self._resolved()
        tts_api_key, tts_base_url = self._resolved_tts_transport(api_key, base_url)
        if not tts_api_key or not tts_base_url:
            self._set_last_tts_error("TTS 缺少 API Key 或 Base URL")
            self._set_last_tts_format("")
            return None

        requested_format = (preferred_format or tts_format or "mp3").lower()
        provider = (tts_provider or "openai").strip().lower()
        if provider == "qwen":
            payload = {
                "model": tts_model or "qwen3-tts-vc-2026-01-22",
                "input": {
                    "text": content,
                    "voice": tts_voice,
                    "language_type": (tts_language_type or "auto").strip() or "auto",
                },
            }
            if requested_format:
                payload["parameters"] = {"format": requested_format}
            url = (
                tts_base_url
                if tts_base_url.lower().endswith("/multimodal-generation/generation")
                else f"{tts_base_url}/multimodal-generation/generation"
            )
        else:
            payload = {
                "model": tts_model or "gpt-4o-mini-tts",
                "voice": tts_voice or "alloy",
                "input": content,
                "response_format": requested_format,
            }
            url = tts_base_url if tts_base_url.lower().endswith("/audio/speech") else f"{tts_base_url}/audio/speech"
        self._set_last_tts_error("")
        self._set_last_tts_format("")

        try:
            import requests

            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {tts_api_key}", "Content-Type": "application/json"},
                data=json.dumps(payload, ensure_ascii=False),
                timeout=time_out,
            )
            audio_bytes, audio_format = self._parse_tts_http_response(
                r.status_code,
                r.headers.get("Content-Type", ""),
                bytes(r.content),
                requested_format,
            )
            self._set_last_tts_format(audio_format)
            self._emit_debug(
                {
                    "phase": "tts_response",
                    "bytes": len(audio_bytes),
                    "voice": tts_voice,
                    "meta": {"provider": provider, "format": audio_format or requested_format, "model": payload["model"]},
                }
            )
            return audio_bytes
        except Exception as req_error:
            if isinstance(req_error, RuntimeError):
                error_message = str(req_error or "").strip()
                self._set_last_tts_error(error_message)
                self._emit_debug({"phase": "tts_error", "error": error_message, "meta": {"provider": provider, "model": payload["model"], "voice": tts_voice, "format": requested_format}})
                return None
            try:
                import urllib.request

                req_u = urllib.request.Request(
                    url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Authorization": f"Bearer {tts_api_key}", "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_u, timeout=time_out) as resp:
                    data = resp.read()
                    audio_bytes, audio_format = self._parse_tts_http_response(
                        getattr(resp, "status", 200),
                        resp.headers.get("Content-Type", ""),
                        data,
                        requested_format,
                    )
                self._set_last_tts_format(audio_format)
                self._emit_debug(
                    {
                        "phase": "tts_response",
                        "bytes": len(audio_bytes),
                        "voice": tts_voice,
                        "meta": {"provider": provider, "fallback": "urllib", "format": audio_format or requested_format, "model": payload["model"]},
                    }
                )
                return audio_bytes
            except Exception as url_error:
                error_message = f"{req_error} | fallback={url_error}"
                self._set_last_tts_error(error_message)
                self._emit_debug({"phase": "tts_error", "error": error_message, "meta": {"provider": provider, "model": payload["model"], "voice": tts_voice, "format": requested_format}})
                return None


class OpenAIClient(OpenAICompatibleClient):
    provider_name = "openai"
    default_base_url = "https://api.openai.com/v1"
    default_model = "gpt-4.1-mini"
    missing_key_error = "（未设置 OpenAI API Key：请在设置里填入 Key 或 $ENV_VAR）"


class SiliconFlowClient(OpenAICompatibleClient):
    provider_name = "siliconflow"
    default_base_url = "https://api.siliconflow.cn/v1"
    default_model = "Qwen/Qwen2.5-72B-Instruct"
    missing_key_error = "（未设置 SiliconFlow API Key：请在设置里填入 Key 或 $ENV_VAR）"