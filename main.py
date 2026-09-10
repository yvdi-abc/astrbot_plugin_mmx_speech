"""MiniMax 语音播报 — AstrBot 插件入口。

功能:
- LLM 回复自动概率语音:dual 模式(默认)=文字照发+语音按概率叠加;voice_only=命中段纯语音。
- /speak:回复引用某条消息即朗读该消息文本(纯语音)。
- /voice clone|list|use|delete|preview:音色复刻、命名、切换、删除、试听。
- 会话白名单 / 黑名单;TTS 失败回退文字。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Record, Reply
from astrbot.core.message import components as Comp
from astrbot.core.message.components import File as FileComp
from astrbot.core.platform.astr_message_event import MessageChain
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .mmx_api import MiniMaxAPI, MiniMaxAPIError
from .speaker import Speaker, convert_audio_for_upload
from .voice_manager import VoiceManager


def _log_info(msg: str) -> None:
    logger.info(f"[mmx_speech] {msg}")


def _log_warn(msg: str) -> None:
    logger.warning(f"[mmx_speech] {msg}")


class Main(star.Star):
    """MiniMax 语音播报插件主类。"""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        # ---- 配置读取
        api_key = str(config.get("api_key", "") or "").strip()
        region = str(config.get("region", "cn") or "cn")
        base_url = str(config.get("base_url", "") or "").strip() or None
        timeout = float(config.get("timeout", 120) or 120)
        self._default_model = str(config.get("default_model", "speech-2.8-hd") or "speech-2.8-hd")
        self._default_voice_id = str(config.get("default_voice_id", "FurinaVoice01") or "").strip()
        try:
            self._probability = float(config.get("voice_probability", 0.3) or 0.0)
        except (TypeError, ValueError):
            self._probability = 0.0
        self._probability = max(0.0, min(1.0, self._probability))
        self._auto_mode = str(config.get("auto_speak_mode", "dual") or "dual").strip().lower()
        if self._auto_mode not in {"dual", "voice_only", "off"}:
            self._auto_mode = "dual"
        self._dual_delay = max(0.0, float(config.get("dual_delay_seconds", 2.0) or 2.0))
        if self._auto_mode == "off":
            self._probability = 0.0
        self._audio_format = str(config.get("audio_format", "mp3") or "mp3")
        self._fallback = bool(config.get("fallback_to_text", True))
        self._use_voice_short = bool(config.get("use_voice_for_short", True))
        self._min_voice_len = int(config.get("min_voice_len", 15) or 15)
        self._max_chars = int(config.get("max_chars", 1000) or 1000)
        self._split_chars = list(config.get("split_chars", ["。", "！", "？", "!", "?", "\n"]))
        self._min_seg = max(1, int(config.get("min_segment_len", 10) or 10))
        self._max_seg = max(1, int(config.get("max_segment_len", 200) or 200))
        self._enabled_sessions = [
            str(x).strip() for x in (config.get("enabled_sessions", []) or []) if str(x).strip()
        ]
        self._whitelist_mode = str(config.get("whitelist_mode", "whitelist") or "whitelist")

        # ---- key 继承 mmx 插件
        if not api_key:
            api_key = self._inherit_mmx_key(region)

        # ---- 数据/缓存目录
        _plugin_name = getattr(self, "name", None) or "astrbot_plugin_mmx_speech"
        self._data_dir = Path(get_astrbot_data_path()) / "plugin_data" / _plugin_name
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = self._data_dir / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # ---- API / Speaker / VoiceManager
        self._api = MiniMaxAPI(
            api_key=api_key,
            region=region,
            base_url=base_url,
            timeout=timeout,
        )
        self._speaker = Speaker(
            self._api, str(self._cache_dir), self._audio_format, self._fallback
        )
        self._voices = VoiceManager(str(self._data_dir), self._api)

        self._bg_tasks: set[asyncio.Task] = set()

        _log_info(
            f"加载完成: voice={self._voices.get_current_voice_id(self._default_voice_id)} "
            f"mode={self._auto_mode} prob={self._probability} model={self._default_model} "
            f"key={'OK' if api_key else 'EMPTY'}"
        )
        self._warn_enhancer_conflict()

    # ------------------------------------------------------------ 工具方法

    def _inherit_mmx_key(self, region: str) -> str:
        """从 astrbot_plugin_mmx_cli_tool 的配置中继承第一个 api_key。"""
        try:
            cfg_path = (
                Path(get_astrbot_data_path())
                / "config"
                / "astrbot_plugin_mmx_cli_tool_config.json"
            )
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text("utf-8-sig"))
                keys = data.get("api_key") or []
                if keys:
                    key = str(keys[0]).strip()
                    if key:
                        _log_info("已自动继承 mmx_cli_tool 插件的 api_key")
                        return key
        except Exception as e:  # noqa: BLE001
            _log_warn(f"读取 mmx_cli_tool 配置失败: {e}")
        return ""

    def _warn_enhancer_conflict(self) -> None:
        """voice_only 模式会接管发送,与 chat_enhancer 分段发送互斥,给出提示。"""
        if self._auto_mode != "voice_only":
            return
        try:
            cfg_path = (
                Path(get_astrbot_data_path())
                / "config"
                / "astrbot_plugin_chat_enhancer_config.json"
            )
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text("utf-8-sig"))
                split = data.get("enable_split", True)
                if split:
                    _log_warn(
                        "voice_only 模式会接管发送,检测到 chat_enhancer 分段开启,"
                        "二者会冲突。建议改用 auto_speak_mode=dual(文字照发+语音叠加),"
                        "或关闭 chat_enhancer 的分段/转发。"
                    )
        except Exception:  # noqa: BLE001
            pass

    def _session_allowed(self, event: AstrMessageEvent) -> bool:
        """会话白名单/黑名单判定。"""
        if not self._enabled_sessions:
            return True
        session_id = str(event.get_session_id() or "")
        # session_id 形如 group_xxx / private_xxx;取数字部分判断
        numbers = re.findall(r"\d+", session_id)
        hit = any(num in self._enabled_sessions for num in numbers)
        if self._whitelist_mode == "blacklist":
            return not hit
        return hit

    def _is_qq(self, event: AstrMessageEvent) -> bool:
        try:
            return event.get_platform_name() == "aiocqhttp"
        except Exception:  # noqa: BLE001
            return False

    def _resolve_voice_id(self) -> str:
        return self._voices.get_current_voice_id(self._default_voice_id)

    # ------------------------------------------------------------ 分段逻辑

    def _split_text(self, text: str) -> list[str]:
        """按标点/长度智能分段(与 chat_enhancer 同思路的简化实现)。"""
        if not text:
            return []
        segments: list[str] = []
        current = ""
        for ch in text:
            current += ch
            if ch in self._split_chars:
                if len(current.strip()) >= self._min_seg:
                    segments.append(current.strip())
                    current = ""
                elif not current.strip():
                    current = ""
        if current.strip():
            segments.append(current.strip())

        final: list[str] = []
        for seg in segments:
            if len(seg) <= self._max_seg:
                final.append(seg)
            else:
                while len(seg) > self._max_seg:
                    final.append(seg[: self._max_seg].strip())
                    seg = seg[self._max_seg :]
                if seg.strip():
                    final.append(seg.strip())
        return [seg for seg in final if seg.strip()] or ([text.strip()] if text.strip() else [])

    def _chunk_text(self, text: str) -> list[str]:
        """按 max_chars 上限切块(尽量在句号/换行处),用于 dual 单次语音叠加。"""
        cap = max(1, self._max_chars)
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= cap:
            return [text]
        breaks = set("。！？!?\n")
        chunks: list[str] = []
        current = ""
        for ch in text:
            current += ch
            if len(current) >= cap:
                chunks.append(current.strip())
                current = ""
            elif ch in breaks and len(current.strip()) >= self._min_seg:
                chunks.append(current.strip())
                current = ""
        if current.strip():
            chunks.append(current.strip())
        # 超长残块硬切兜底
        final: list[str] = []
        for c in chunks:
            if len(c) <= cap:
                final.append(c)
            else:
                while len(c) > cap:
                    final.append(c[:cap].strip())
                    c = c[cap:]
                if c.strip():
                    final.append(c.strip())
        return [c for c in final if c.strip()]

    # ------------------------------------------------------------ 语音发送

    async def _send_voice_segment(
        self, event: AstrMessageEvent, segment: str
    ) -> None:
        """合成并发送一段语音;失败时按配置回退文字。"""
        voice_id = self._resolve_voice_id()
        audio_path, err = await self._speaker.speak_text(
            segment, voice_id, model=self._default_model
        )
        if audio_path:
            try:
                chain = MessageChain()
                chain.chain = [Record(file=audio_path)]
                await event.send(chain)
                return
            except Exception as e:  # noqa: BLE001
                _log_warn(f"语音消息发送失败: {e}")
        if self._fallback:
            try:
                await event.send(event.plain_result(segment))
            except Exception as e:  # noqa: BLE001
                _log_warn(f"回退文字发送失败: {e}")

    async def _schedule_segments(
        self, event: AstrMessageEvent, segments: list[str]
    ) -> None:
        """后台逐段发送:每段独立抛硬币,命中段纯语音、未命中段发文字。"""

        async def runner() -> None:
            for i, seg in enumerate(segments):
                if not seg.strip():
                    continue
                if not self._use_voice_short and len(seg.strip()) < self._min_voice_len:
                    await event.send(event.plain_result(seg.strip()))
                elif random.random() < self._probability:
                    await self._send_voice_segment(event, seg.strip())
                else:
                    await event.send(event.plain_result(seg.strip()))
                if i < len(segments) - 1:
                    await asyncio.sleep(0.8)

        task = asyncio.create_task(runner())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ------------------------------------------------------------ LLM 钩子

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp) -> None:
        """dual 模式:文字照常发,按概率延迟叠一条语音。"""
        original = getattr(resp, "completion_text", None)

        if self._auto_mode != "dual":
            return
        if self._probability <= 0 or not self._is_qq(event) or not self._session_allowed(event):
            return
        if not original or not str(original).strip():
            return
        if random.random() >= self._probability:
            return
        text = self._clean_llm_text(str(original))
        if not text:
            return
        if not self._use_voice_short and len(text) < self._min_voice_len:
            return
        if getattr(event, "_mmx_dual_scheduled", False):
            return
        event._mmx_dual_scheduled = True
        chunks = self._chunk_text(text)
        if not chunks:
            return

        async def delayed_voice() -> None:
            try:
                await asyncio.sleep(self._dual_delay)
                for i, chunk in enumerate(chunks):
                    await self._send_voice_segment(event, chunk)
                    if i < len(chunks) - 1:
                        await asyncio.sleep(0.6)
            except Exception as e:  # noqa: BLE001
                _log_warn(f"dual 语音发送失败: {e}")

        task = asyncio.create_task(delayed_voice())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        """voice_only 模式:自动语音接管 LLM 回复发送(命中段纯语音,未命中段文字)。

        该模式与 chat_enhancer 分段冲突,请用 dual 模式或关闭 chat_enhancer。
        """
        if self._auto_mode != "voice_only":
            return
        if self._probability <= 0:
            return
        if not self._is_qq(event):
            return
        if not self._session_allowed(event):
            return

        result = event.get_result()
        if result is None or not result.chain:
            return
        is_llm = False
        try:
            is_llm = result.is_llm_result() or result.is_model_result()
        except Exception:  # noqa: BLE001
            is_llm = False
        if not is_llm:
            return
        # 只接管纯文本 LLM 回复:含任何富媒体/文件/Node/工具产物(图片/视频/语音/文件/合并转发)都跳过,
        # 保持原文发送,避免语音层破坏 Agent 工具调用与多媒体结果。
        if any(
            not isinstance(c, Comp.Plain)
            for c in result.chain
        ):
            return

        text = self._clean_llm_text(
            "".join(c.text for c in result.chain if isinstance(c, Comp.Plain))
        )
        if not text:
            return
        segments = self._split_text(text)
        if not segments:
            return
        result.chain = []
        await self._schedule_segments(event, segments)

    def _clean_llm_text(self, text: str) -> str:
        """清理 LLM 文本:去代码块/MD 标记/控制标签,供语音合成。"""
        text = re.sub(r"```[\s\S]*?```", "", text or "")
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"#{1,6}\s+", "", text)
        text = re.sub(r"\*+|_+|`+", "", text)
        return text.strip()

    # ------------------------------------------------------------ /speak

    @filter.command("speak")
    async def cmd_speak(self, event: AstrMessageEvent):
        """朗读引用消息。用法: 回复一条消息并输入 /speak"""
        if not self._is_qq(event):
            yield event.plain_result("语音播报目前仅支持 QQ(aiocqhttp) 平台。")
            return
        if not self._session_allowed(event):
            yield event.plain_result("当前会话未在语音生效范围内。")
            return

        text = ""
        messages = event.get_messages()
        for seg in messages:
            if isinstance(seg, Reply):
                quote = getattr(seg, "chain", None) or getattr(seg, "message", None)
                if isinstance(quote, list):
                    text = "".join(
                        c.text for c in quote if isinstance(c, Comp.Plain)
                    ).strip()
                break
        if not text:
            # 允许 /speak <文本> 直接朗读(非引用)
            msg = re.sub(r"\[MSG_ID:\d+\]", "", event.message_str or "").strip()
            parts = msg.split(maxsplit=1)
            if len(parts) > 1:
                text = parts[1].strip()
        if not text:
            yield event.plain_result("请回复一条消息再发送 /speak,或输入 /speak <要朗读的文字>")
            return
        if len(text) > self._max_chars:
            yield event.plain_result(
                f"文本超长({len(text)} 字符 > 上限 {self._max_chars}),请分段或截短后重试。"
            )
            return

        voice_id = self._resolve_voice_id()
        audio_path, err = await self._speaker.speak_text(
            text, voice_id, model=self._default_model
        )
        if not audio_path:
            if self._fallback:
                yield event.plain_result(f"语音合成失败,已用文字回复:\n{text}\n\n({err})")
                return
            yield event.plain_result(f"语音合成失败: {err}")
            return
        chain = MessageChain()
        chain.chain = [Record(file=audio_path)]
        yield event.chain_result(chain)

    # ------------------------------------------------------------ /voice

    @filter.command_group("voice")
    def voice_group(self) -> None:
        """音色管理指令组"""

    @voice_group.command("clone")
    async def voice_clone(self, event: AstrMessageEvent, name: GreedyStr):
        """复刻音色。用法: /voice clone <名字>(需同时/引用一条语音消息)"""
        if not self._is_qq(event):
            yield event.plain_result("语音复刻目前仅支持 QQ(aiocqhttp) 平台。")
            return
        if not self._session_allowed(event):
            yield event.plain_result("当前会话未在语音生效范围内。")
            return

        display_name = str(name or "").strip()
        if not display_name:
            yield event.plain_result(
                "用法: /voice clone <名字>\n"
                "然后在本消息附带/引用一条语音(或文件)作为复刻素材。\n"
                "要求: 单人干净人声,10 秒~5 分钟,mp3/m4a/wav/amr。"
            )
            return

        audio_src = await self._extract_audio_input(event)
        if not audio_src:
            yield event.plain_result(
                "未检测到可用的语音/音频。请直接发送语音或引用一条语音消息后重试 /voice clone。"
            )
            return

        yield event.plain_result("收到音频,正在复刻音色,请稍候(约 10~30 秒)...")

        ok, msg = await self._voices.clone_from_audio(
            audio_src,
            display_name,
            convert=convert_audio_for_upload,
        )
        if ok:
            yield event.plain_result("✅ " + msg)
        else:
            yield event.plain_result("❌ " + msg)

    @voice_group.command("list")
    async def voice_list(self, event: AstrMessageEvent):
        """列出已复刻音色"""
        names = self._voices.list_names()
        current = self._resolve_voice_id()
        if not names:
            yield event.plain_result(
                f"暂无自定义复刻音色。当前默认 voice_id: {current}\n"
                "用 /voice clone <名字> 附语音可复刻新音色。"
            )
            return
        lines = ["🎙 已复刻音色:"]
        for n in names:
            rec = self._voices._voices.get(n) or {}
            vid = rec.get("voice_id", "")
            marker = " ⭐当前" if (n == self._voices._current or vid == current) else ""
            lines.append(f"- {n} {marker}({vid})")
        lines.append(f"\n当前默认 voice_id: {current}\n切换: /voice use <名字>")
        yield event.plain_result("\n".join(lines))

    @voice_group.command("use")
    async def voice_use(self, event: AstrMessageEvent, name: GreedyStr):
        """切换全局默认音色。用法: /voice use <名字或voice_id>"""
        ok, msg = self._voices.set_current(str(name or "").strip())
        if ok:
            yield event.plain_result("✅ " + msg)
        else:
            yield event.plain_result("❌ " + msg)

    @voice_group.command("delete")
    async def voice_delete(self, event: AstrMessageEvent, name: GreedyStr):
        """删除音色。用法: /voice delete <名字或voice_id>"""
        value = str(name or "").strip()
        if not value:
            yield event.plain_result("用法: /voice delete <名字或voice_id>")
            return
        ok, msg = await self._voices.delete(value)
        if ok:
            yield event.plain_result("✅ " + msg)
        else:
            yield event.plain_result("❌ " + msg)

    @voice_group.command("preview")
    async def voice_preview(self, event: AstrMessageEvent, text: GreedyStr):
        """试听当前音色。用法: /voice preview <文本>"""
        t = str(text or "").strip()
        if not t:
            yield event.plain_result("用法: /voice preview <文本>,用当前默认音色朗读")
            return
        voice_id = self._resolve_voice_id()
        audio_path, err = await self._speaker.speak_text(
            t, voice_id, model=self._default_model
        )
        if not audio_path:
            yield event.plain_result(f"语音合成失败: {err}")
            return
        chain = MessageChain()
        chain.chain = [Record(file=audio_path)]
        yield event.chain_result(chain)

    # ------------------------------------------------------------ 音频提取

    async def _extract_audio_input(self, event: AstrMessageEvent) -> str | None:
        """从当前消息/引用消息中提取音频文件路径或 URL(下载到本地缓存)。"""
        messages = event.get_messages()
        reply_texts: list[str] = []
        candidates: list[str] = []

        def _scan(segments: list) -> None:
            for seg in segments:
                if isinstance(seg, (Record, FileComp)):
                    src = self._component_media(seg)
                    if src:
                        candidates.append(src)
                elif isinstance(seg, Reply):
                    rid = getattr(seg, "id", None)
                    if rid is not None:
                        reply_texts.append(str(rid))
                    for attr in ("chain", "message", "origin", "content"):
                        nested = getattr(seg, attr, None)
                        if isinstance(nested, list) and nested:
                            _scan(nested)

        _scan(list(messages or []))

        # 若直接扫描没拿到,尝试用 onebot API 按 reply id 拉取
        if not candidates and reply_texts:
            for rid in reply_texts:
                src = await self._resolve_quoted_audio(event, rid)
                if src:
                    candidates.append(src)

        for src in candidates:
            local = await self._download_or_keep(src)
            if local:
                return local
        return None

    def _component_media(self, comp) -> str | None:
        for attr in ("path", "file", "file_", "url"):
            val = getattr(comp, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    async def _download_or_keep(self, src: str) -> str | None:
        """URL 下载到缓存;本地路径直接使用(须存在)。"""
        if src.startswith(("http://", "https://")):
            try:
                import httpx

                async with httpx.AsyncClient(timeout=120) as client:
                    r = await client.get(src)
                    r.raise_for_status()
                ext = Path(src.split("?")[0]).suffix or ".amr"
                if ext not in {".mp3", ".m4a", ".wav", ".amr", ".ogg", ".opus", ".silk"}:
                    ext = ".amr"
                dst = self._cache_dir / f"audio_in_{int(time.time() * 1000)}{ext}"
                dst.write_bytes(r.content)
                return str(dst)
            except Exception as e:  # noqa: BLE001
                _log_warn(f"音频下载失败: {e}")
                return None
        if src.startswith("file://"):
            src = src[len("file://") :]
        if os.path.exists(src):
            return src
        return None

    async def _resolve_quoted_audio(self, event: AstrMessageEvent, reply_id: str) -> str | None:
        """按回复消息 id 通过 onebot get_msg 拉取音频。"""
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return None
        try:
            payload = await call_action("get_msg", **{"message_id": reply_id})
        except TypeError:
            try:
                payload = await call_action("get_msg", **{"id": reply_id})
            except Exception:  # noqa: BLE001
                return None
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(payload, dict):
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        message = data.get("message") or data.get("messages")
        if not isinstance(message, list):
            return None
        return await self._scan_onebot_segments(event, message)

    async def _scan_onebot_segments(self, event, segments: list) -> str | None:
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if str(seg.get("type") or "").lower() not in {"record", "voice", "audio"}:
                continue
            seg_data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            url = seg_data.get("url") or seg_data.get("path")
            if isinstance(url, str) and url.strip():
                return url.strip()
            file_ref = seg_data.get("file") or seg_data.get("file_id")
            if isinstance(file_ref, str) and file_ref.strip():
                return file_ref.strip()
        return None

    async def terminate(self) -> None:
        for task in list(self._bg_tasks):
            if not task.done():
                task.cancel()
