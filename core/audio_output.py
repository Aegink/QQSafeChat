from __future__ import annotations

import audioop
import os
import time
import wave


DEFAULT_OUTPUT_DEVICE_LABEL = "（系统默认输出设备）"

_GENERIC_OUTPUT_NAMES = {
    "microsoft 声音映射器 - output",
    "主声音驱动程序",
}

_HOSTAPI_PRIORITY = {
    "Windows WASAPI": 0,
    "Windows DirectSound": 1,
    "MME": 2,
    "Windows WDM-KS": 9,
}

_VB_AUDIO_DEVICE_MARKERS = (
    "vb-audio",
    "virtual cable",
)


def _normalize_device_name(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _playback_hostapi_priority(normalized_name: str, hostapi_name: str) -> int:
    if any(marker in normalized_name for marker in _VB_AUDIO_DEVICE_MARKERS):
        if hostapi_name == "MME":
            return 0
        if hostapi_name == "Windows WASAPI":
            return 1
        if hostapi_name == "Windows DirectSound":
            return 9
    return _HOSTAPI_PRIORITY.get(hostapi_name, 50)


def _same_output_device_name(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right) or right.startswith(left)


def _collect_output_candidates(sounddevice):
    devices = sounddevice.query_devices()
    hostapis = sounddevice.query_hostapis()
    candidates: list[tuple[int, int, str, str, str]] = []

    for index, device in enumerate(devices):
        max_output = int(device.get("max_output_channels", 0) or 0)
        if max_output <= 0:
            continue
        name = str(device.get("name") or f"输出设备 {index}").strip()
        if not name:
            continue
        if name.lower() in _GENERIC_OUTPUT_NAMES:
            continue
        hostapi_name = ""
        try:
            hostapi_index = int(device.get("hostapi", -1))
            if hostapi_index >= 0:
                hostapi = hostapis[hostapi_index]
                hostapi_name = str(hostapi.get("name") or "").strip()
        except Exception:
            hostapi_name = ""

        normalized_name = _normalize_device_name(name)
        priority = _playback_hostapi_priority(normalized_name, hostapi_name)
        candidates.append((priority, index, normalized_name, name, hostapi_name))
    return candidates


def _load_sounddevice():
    try:
        import sounddevice as sounddevice  # type: ignore

        return sounddevice, ""
    except Exception as exc:
        return None, str(exc)


def _iter_output_devices(sounddevice):
    candidates = _collect_output_candidates(sounddevice)

    if not candidates:
        return

    wasapi_candidates = [item for item in candidates if item[4] == "Windows WASAPI"]
    if wasapi_candidates:
        candidates = wasapi_candidates

    best_by_name: dict[str, tuple[int, int, str, str]] = {}
    for priority, index, normalized_name, name, hostapi_name in candidates:
        current = best_by_name.get(normalized_name)
        if current is None or (priority, index) < (current[0], current[1]):
            best_by_name[normalized_name] = (priority, index, name, hostapi_name)

    chosen = sorted(best_by_name.values(), key=lambda item: (item[0], item[2].lower(), item[1]))
    for _priority, index, name, hostapi_name in chosen:
        label = name
        yield index, name, label


def list_output_devices() -> tuple[list[str], str]:
    sounddevice, error = _load_sounddevice()
    if not sounddevice:
        return [], f"sounddevice 不可用：{error}"

    try:
        labels = [label for _, _, label in _iter_output_devices(sounddevice)]
        return labels, ""
    except Exception as exc:
        return [], str(exc)


def resolve_output_device(device_label: str | None) -> tuple[int | None, str]:
    sounddevice, error = _load_sounddevice()
    if not sounddevice:
        raise RuntimeError(f"sounddevice 不可用：{error}")

    target = str(device_label or "").strip()
    if not target or target == DEFAULT_OUTPUT_DEVICE_LABEL:
        return None, DEFAULT_OUTPUT_DEVICE_LABEL

    fallback_index = None
    fallback_name = ""
    for index, name, label in _iter_output_devices(sounddevice):
        if target == label or target == name:
            return index, label
        if not fallback_name and (target in label or target in name):
            fallback_index = index
            fallback_name = label
    if fallback_name:
        return fallback_index, fallback_name
    raise RuntimeError(f"未找到输出设备：{target}")


def _resolve_playback_device(sounddevice, device_label: str | None, channels: int, dtype: str, requested_rate: int) -> tuple[int | None, str]:
    target = str(device_label or "").strip()
    if not target or target == DEFAULT_OUTPUT_DEVICE_LABEL:
        return None, DEFAULT_OUTPUT_DEVICE_LABEL

    normalized_target = _normalize_device_name(target)
    candidates = _collect_output_candidates(sounddevice)
    exact_matches = [item for item in candidates if _same_output_device_name(item[2], normalized_target)]
    if not exact_matches:
        exact_matches = [item for item in candidates if normalized_target in item[2] or item[2] in normalized_target]
    if not exact_matches:
        raise RuntimeError(f"未找到输出设备：{target}")

    ranked: list[tuple[int, int, int, str, str]] = []
    for priority, index, _normalized, name, hostapi_name in exact_matches:
        supports_requested = 1
        try:
            sounddevice.check_output_settings(
                device=index,
                channels=channels,
                dtype=dtype,
                samplerate=requested_rate,
            )
            supports_requested = 0
        except Exception:
            supports_requested = 1
        ranked.append((supports_requested, priority, index, name, hostapi_name))

    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    _supports_requested, index, _idx, name, hostapi_name = ranked[0]
    resolved_name = f"{name} [{hostapi_name}]" if hostapi_name else name
    return _idx, resolved_name


def _query_output_device_info(sounddevice, device_index: int | None):
    try:
        if device_index is None:
            return sounddevice.query_devices(kind="output")
        return sounddevice.query_devices(device_index, "output")
    except Exception:
        if device_index is None:
            default_device = getattr(sounddevice, "default", None)
            default_pair = getattr(default_device, "device", None)
            if isinstance(default_pair, (tuple, list)) and len(default_pair) >= 2:
                return sounddevice.query_devices(default_pair[1], "output")
            return sounddevice.query_devices()
        return sounddevice.query_devices(device_index)


def _pick_output_sample_rate(sounddevice, device_index: int | None, channels: int, dtype: str, requested_rate: int) -> tuple[int, str]:
    try:
        sounddevice.check_output_settings(
            device=device_index,
            channels=channels,
            dtype=dtype,
            samplerate=requested_rate,
        )
        return requested_rate, ""
    except Exception:
        pass

    candidate_rates: list[int] = []
    try:
        device_info = _query_output_device_info(sounddevice, device_index)
        default_rate = int(round(float(device_info.get("default_samplerate") or 0)))
        if default_rate > 0:
            candidate_rates.append(default_rate)
    except Exception:
        pass

    for rate in (48000, 44100, 32000, 24000):
        if rate > 0 and rate not in candidate_rates and rate != requested_rate:
            candidate_rates.append(rate)

    for rate in candidate_rates:
        try:
            sounddevice.check_output_settings(
                device=device_index,
                channels=channels,
                dtype=dtype,
                samplerate=rate,
            )
            return rate, f"重采样 {requested_rate}Hz -> {rate}Hz"
        except Exception:
            continue

    return requested_rate, ""


def _resample_pcm_frames(raw_frames: bytes, sample_width: int, channels: int, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate:
        return raw_frames
    try:
        converted, _state = audioop.ratecv(
            raw_frames,
            sample_width,
            channels,
            source_rate,
            target_rate,
            None,
        )
        return converted
    except Exception as exc:
        raise RuntimeError(f"重采样失败：{exc}") from exc


def _load_wav_playback_context(wav_path: str):
    if not wav_path or not os.path.exists(wav_path):
        raise RuntimeError("音频文件不存在")
    if os.path.splitext(wav_path)[1].lower() != ".wav":
        raise RuntimeError("真语音模式目前只支持 WAV 音频")

    with wave.open(wav_path, "rb") as wav_file:
        channels = int(wav_file.getnchannels() or 1)
        sample_width = int(wav_file.getsampwidth() or 0)
        sample_rate = int(wav_file.getframerate() or 0)
        frame_count = int(wav_file.getnframes() or 0)
        raw_frames = wav_file.readframes(frame_count)

    dtype_map = {
        1: "uint8",
        2: "int16",
        3: "int24",
        4: "int32",
    }
    dtype = dtype_map.get(sample_width)
    if not dtype:
        raise RuntimeError(f"不支持的 WAV 位深：{sample_width * 8} bit")

    return raw_frames, channels, sample_width, sample_rate, dtype


def _resolve_wav_output_context(sounddevice, device_label: str | None, channels: int, dtype: str, sample_rate: int):
    device_index, resolved_name = _resolve_playback_device(
        sounddevice,
        device_label,
        channels,
        dtype,
        sample_rate,
    )
    target_rate, rate_note = _pick_output_sample_rate(
        sounddevice,
        device_index,
        channels,
        dtype,
        sample_rate,
    )
    resolved_text = resolved_name if not rate_note else f"{resolved_name}（{rate_note}）"
    return device_index, resolved_text, target_rate


def probe_wav_output(wav_path: str, device_label: str | None) -> tuple[bool, str]:
    sounddevice, error = _load_sounddevice()
    if not sounddevice:
        return False, f"sounddevice 不可用：{error}"

    try:
        _raw_frames, channels, _sample_width, sample_rate, dtype = _load_wav_playback_context(wav_path)
        device_index, resolved_text, target_rate = _resolve_wav_output_context(
            sounddevice,
            device_label,
            channels,
            dtype,
            sample_rate,
        )
        with sounddevice.RawOutputStream(
            samplerate=target_rate,
            channels=channels,
            dtype=dtype,
            device=device_index,
            blocksize=4096,
        ):
            pass
        return True, resolved_text
    except Exception as exc:
        return False, str(exc)


def play_wav_to_output(wav_path: str, device_label: str | None) -> tuple[bool, str, float]:
    sounddevice, error = _load_sounddevice()
    if not sounddevice:
        return False, f"sounddevice 不可用：{error}", 0.0

    try:
        raw_frames, channels, sample_width, sample_rate, dtype = _load_wav_playback_context(wav_path)
        device_index, resolved_name, target_rate = _resolve_wav_output_context(
            sounddevice,
            device_label,
            channels,
            dtype,
            sample_rate,
        )
    except Exception as exc:
        return False, str(exc), 0.0

    try:
        playback_frames = _resample_pcm_frames(
            raw_frames,
            sample_width,
            channels,
            sample_rate,
            target_rate,
        )
        bytes_per_frame = max(1, sample_width * channels)
        duration = len(playback_frames) / float(max(1, bytes_per_frame * target_rate))

        with sounddevice.RawOutputStream(
            samplerate=target_rate,
            channels=channels,
            dtype=dtype,
            device=device_index,
            blocksize=4096,
        ) as stream:
            chunk_size = bytes_per_frame * 4096
            playback_started_at = time.perf_counter()
            for offset in range(0, len(playback_frames), chunk_size):
                chunk = playback_frames[offset : offset + chunk_size]
                stream.write(chunk)
            remaining = duration - (time.perf_counter() - playback_started_at)
            if remaining > 0:
                time.sleep(remaining + 0.05)
            try:
                stream.stop()
            except Exception:
                pass
        return True, resolved_name, duration
    except Exception as exc:
        return False, f"{exc} (wav={sample_rate}Hz, device={resolved_name})", 0.0