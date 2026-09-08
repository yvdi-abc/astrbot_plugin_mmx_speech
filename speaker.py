"""语音发送器 — 合成、保存、构造 QQ 语音消息、失败回退。"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .mmx_api import MiniMaxAPI, MiniMaxAPIError


def save_audio_from_response(response: dict[str, Any], out_path: str) -> str:
    """将 T2A 响应的音频(hex 或 url)保存到本地文件,返回文件路径。"""
    data = response.get("data") or {}
    audio_hex = data.get("audio")
    audio_url = data.get("audio_url")

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if audio_hex and isinstance(audio_hex, str) and audio_hex.strip():
        try:
            path.write_bytes(bytes.fromhex(audio_hex))
            return str(path)
        except ValueError:
            pass  # 非 hex,继续尝试 url

    if audio_url and isinstance(audio_url, str) and audio_url.startswith("http"):
        import httpx

        r = httpx.get(audio_url, timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
        return str(path)

    raise MiniMaxAPIError("TTS 响应中缺少 audio/audio_url")


def convert_audio_for_upload(src: str, dst_dir: str, ext: str = "mp3") -> str | None:
    """用 ffmpeg 把任意音频(amr/ogg/opus/silk 尝试)转为目标格式。

    返回输出文件路径;失败返回 None(上层会给出友好提示)。
    """
    import subprocess

    os.makedirs(dst_dir, exist_ok=True)
    out = os.path.join(dst_dir, f"voice_clone_{int(time.time() * 1000)}.{ext}")
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                src,
                "-acodec",
                "libmp3lame" if ext == "mp3" else "pcm_s16le",
                "-ar",
                "44100",
                "-ac",
                "1",
                out if ext == "mp3" else out.replace(f".{ext}", ".wav"),
            ],
            capture_output=True,
            timeout=120,
        )
        if proc.returncode != 0:
            logger.warning(
                f"[mmx_speech] ffmpeg 转码失败: {proc.stderr.decode('utf-8', 'ignore')[-300:]}"
            )
            return None
        return out if ext == "mp3" else out.replace(f".{ext}", ".wav")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[mmx_speech] ffmpeg 调用异常: {e}")
        return None


_CLEAN_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_tts_text(text: str) -> str:
    """清洗待合成文本:去除控制字符、首尾空白;长空白折叠。"""
    text = _CLEAN_RE.sub("", text or "")
    text = re.sub(r"[ \t\u3000]{2,}", " ", text)
    return text.strip()


class Speaker:
    """负责把一段文本变成 QQ 语音 Record,或按需回退文字。"""

    def __init__(
        self,
        api: MiniMaxAPI,
        cache_dir: str,
        audio_format: str = "mp3",
        fallback_to_text: bool = True,
    ) -> None:
        self._api = api
        self._cache_dir = cache_dir
        self._audio_format = audio_format
        self._fallback = fallback_to_text

    async def speak_text(
        self,
        text: str,
        voice_id: str,
        *,
        model: str = "speech-2.8-hd",
        language: str = "Chinese",
    ) -> tuple[str | None, str | None]:
        """合成文本为语音,返回 (audio_path, error)。失败时 error 说明原因。"""
        text = clean_tts_text(text)
        if not text:
            return None, "空文本"
        import asyncio

        try:
            response = await asyncio.to_thread(
                self._api.synthesize,
                text,
                voice_id,
                model=model,
                language=language,
                audio_format=self._audio_format,
            )
        except MiniMaxAPIError as e:
            return None, str(e)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[mmx_speech] TTS 异常: {e}")
            return None, f"TTS 异常: {e}"

        out_path = os.path.join(
            self._cache_dir, f"mmx_speech_{int(time.time() * 1000)}.{self._audio_format}"
        )
        try:
            saved = save_audio_from_response(response, out_path)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[mmx_speech] 音频保存失败: {e}")
            return None, f"音频保存失败: {e}"
        return saved, None
