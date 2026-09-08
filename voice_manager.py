"""音色管理器 — 复刻音色的命名、映射、全局默认、持久化。

MiniMax 的 voice_id 有严格格式(8-256 字符、字母开头、仅字母数字-_),
用户取名可能是中文/短名,所以维护一张「显示名 → voice_id」映射表,
持久化在插件数据目录的 voices.json,并记录「当前默认音色」。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from astrbot.api import logger

from .mmx_api import MiniMaxAPI, MiniMaxAPIError

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]")


def make_voice_id(display_name: str) -> str:
    """由显示名生成合法的 MiniMax voice_id(字母开头,≥8 位,含短哈希保证唯一)。"""
    slug = _SAFE_SLUG_RE.sub("", display_name)[:24] or "voice"
    digest = hashlib.md5(display_name.encode("utf-8")).hexdigest()[:8]
    vid = f"mmx_{slug}_{digest}"
    # 确保 ≥8 且不以 -/_ 结尾
    while len(vid) < 8:
        vid += "_"
    return vid


class VoiceManager:
    """音色命名表 + 全局默认音色的管理。"""

    def __init__(self, data_dir: str, api: MiniMaxAPI) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._store_path = self._data_dir / "voices.json"
        self._api = api
        self._voices: dict[str, dict] = {}  # display_name -> {voice_id, created}
        self._current: str | None = None  # 当前显示名(不是 voice_id)
        self._load()

    # ------------------------------------------------------------ persistence

    def _load(self) -> None:
        if not self._store_path.exists():
            return
        try:
            data = json.loads(self._store_path.read_text("utf-8"))
            self._voices = data.get("voices") or {}
            self._current = data.get("current")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[mmx_speech] 音色表加载失败: {e}")

    def _save(self) -> None:
        payload = {"voices": self._voices, "current": self._current}
        tmp = self._store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, self._store_path)

    # ------------------------------------------------------------------ names

    def list_names(self) -> list[str]:
        return sorted(self._voices.keys())

    def voice_id_of(self, name_or_vid: str) -> str | None:
        """把「显示名或原生 voice_id」解析为 voice_id。"""
        value = (name_or_vid or "").strip()
        if not value:
            return None
        # 先查本地命名表
        if value in self._voices:
            return self._voices[value].get("voice_id")
        # 已是合法形态的原生 voice_id(8-256 字母开头)→ 原样返回
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]", value):
            return value
        return None

    def get_current_voice_id(self, config_default: str = "") -> str:
        """返回当前默认 voice_id(优先本地 current → 配置默认 → FurinaVoice01)。"""
        if self._current and self._current in self._voices:
            return self._voices[self._current].get("voice_id") or ""
        if config_default:
            resolved = self.voice_id_of(config_default)
            if resolved:
                return resolved
            return config_default
        return "FurinaVoice01"

    def set_current(self, name_or_vid: str) -> tuple[bool, str]:
        """把某显示名/原生 voice_id 设为当前默认。返回 (是否成功, 提示)。"""
        value = (name_or_vid or "").strip()
        if not value:
            return False, "请提供音色名或 voice_id"
        if value in self._voices:
            self._current = value
            self._save()
            return True, f"已切换到音色「{value}」"
        if self.voice_id_of(value) is not None:
            # 原生 voice_id 也可以直接作为 current 存下(存 value 本身)
            self._current = value
            self._save()
            return True, f"已切换到 voice_id: {value}"
        return False, f"找不到音色: {value},可用 /voice list 查看"

    # -------------------------------------------------------------- cloning

    async def clone_from_audio(
        self,
        audio_path: str,
        display_name: str,
        *,
        language: str = "Chinese",
        convert: object = None,
    ) -> tuple[bool, str]:
        """上传音频并复刻音色,登记显示名。convert 为可调用转换函数(src,dst_dir)->path。"""
        import asyncio

        name = (display_name or "").strip()
        if not name:
            return False, "请给音色起个名字,例如 /voice clone 我的声音"
        if name in self._voices:
            return False, f"音色「{name}」已存在,先 /voice delete {name} 再复刻"

        path = audio_path
        if convert is not None:
            try:
                converted = await asyncio.to_thread(
                    convert, path, str(self._data_dir / "converted")
                )
            except Exception as e:  # noqa: BLE001
                converted = None
                logger.warning(f"[mmx_speech] 音频转换失败: {e}")
            if converted:
                path = converted

        # 上传 → 复刻
        try:
            file_id = await asyncio.to_thread(self._api.upload_audio, path, "voice_clone")
        except MiniMaxAPIError as e:
            return False, f"上传失败: {e}"
        voice_id = make_voice_id(name)
        try:
            await asyncio.to_thread(
                self._api.clone_voice,
                file_id,
                voice_id,
                language=language,
            )
        except MiniMaxAPIError as e:
            if e.status_code == 2038:
                return False, "复刻被拒(2038):账号需在 MiniMax 完成实名认证"
            if e.status_code == 2037:
                return False, "音频时长不合规:需 10 秒~5 分钟"
            return False, f"复刻失败: {e}"

        self._voices[name] = {
            "voice_id": voice_id,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if not self._current:
            self._current = name
        self._save()
        return True, f"音色「{name}」复刻成功(voice_id={voice_id}),已设为默认。用 /voice use <名> 切换"

    async def delete(self, name_or_vid: str) -> tuple[bool, str]:
        """删除音色(本地映射 + MiniMax 远端)。"""
        import asyncio

        value = (name_or_vid or "").strip()
        voice_id = self.voice_id_of(value)
        if not voice_id:
            return False, f"找不到音色: {value}"
        try:
            await asyncio.to_thread(self._api.delete_voice, voice_id)
        except MiniMaxAPIError as e:
            # 远端删失败不阻塞本地清理(如已不存在)
            logger.warning(f"[mmx_speech] 远端删除失败: {e}")
        # 本地清理
        for name, rec in list(self._voices.items()):
            if rec.get("voice_id") == voice_id or name == value:
                del self._voices[name]
                if self._current == name:
                    self._current = next(iter(self._voices), None)
        self._save()
        return True, f"已删除音色: {value}"
