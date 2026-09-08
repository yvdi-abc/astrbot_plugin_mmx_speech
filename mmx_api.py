"""MiniMax API 层 — 独立实现,不依赖 mmx_cli_tool 插件。

提供:
- ``POST /v1/t2a_v2`` 文本转语音
- ``POST /v1/files/upload`` 上传音频(purpose=voice_clone / prompt_audio)
- ``POST /v1/voice_clone`` 快速复刻
- ``POST /v1/get_voice`` 查询音色
- ``POST /v1/delete_voice`` 删除复刻音色
"""

from __future__ import annotations

from typing import Any

import httpx

REGIONS: dict[str, str] = {
    "global": "https://api.minimax.io",
    "cn": "https://api.minimaxi.com",
}


class MiniMaxAPIError(Exception):
    """MiniMax API 调用异常,带 status_code 便于上层映射友好提示。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class MiniMaxAPI:
    """轻量 MiniMax 语音 API 客户端(httpx 同步内部实现,外层负责异步线程)。"""

    def __init__(
        self,
        api_key: str,
        region: str = "cn",
        base_url: str | None = None,
        timeout: float = 120,
    ) -> None:
        self.api_key = api_key
        self.base = (base_url or REGIONS.get(region, REGIONS["cn"])).rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ utils

    def _headers(self, json_body: bool = True) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}"}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _post(self, path: str, body: dict | None = None, **kw: Any) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(
                    f"{self.base}{path}",
                    headers=self._headers(json_body=body is not None),
                    json=body,
                    **kw,
                )
        except httpx.HTTPError as e:
            raise MiniMaxAPIError(f"网络请求失败: {e}") from e

        try:
            data = r.json()
        except ValueError:
            raise MiniMaxAPIError(f"非 JSON 响应 [{r.status_code}]") from None

        base_resp = data.get("base_resp") or {}
        code = base_resp.get("status_code")
        msg = base_resp.get("status_msg", "")
        if r.status_code >= 400 or (code is not None and code != 0):
            raise MiniMaxAPIError(
                f"MiniMax 错误 code={code}: {msg}",
                status_code=code if code is not None else None,
            )
        return data

    # ------------------------------------------------------------------- T2A

    def synthesize(
        self,
        text: str,
        voice_id: str,
        *,
        model: str = "speech-2.8-hd",
        language: str = "Chinese",
        audio_format: str = "mp3",
        speed: float | None = None,
        volume: float | None = None,
        pitch: float | None = None,
        sample_rate: int = 32000,
        bitrate: int = 128000,
    ) -> dict[str, Any]:
        """文本转语音。返回响应 dict(data.audio 为 hex 或 data.audio_url 为 URL)。"""
        body: dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
            },
            "audio_setting": {
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "format": audio_format,
                "channel": 1,
            },
        }
        if speed is not None:
            body["voice_setting"]["speed"] = speed
        if volume is not None:
            body["voice_setting"]["vol"] = volume
        if pitch is not None:
            body["voice_setting"]["pitch"] = pitch
        if language:
            body["language_boost"] = language

        return self._post("/v1/t2a_v2", body)

    # ------------------------------------------------------------- voice mgmt

    def list_voices(self, voice_type: str = "voice_cloning") -> list[dict]:
        """查询指定类型音色。voice_cloning 需要至少被使用过一次才会列出。"""
        data = self._post("/v1/get_voice", {"voice_type": voice_type})
        items = data.get(voice_type)
        return items if isinstance(items, list) else []

    def delete_voice(self, voice_id: str) -> dict:
        """删除复刻音色。"""
        return self._post(
            "/v1/delete_voice", {"voice_type": "voice_cloning", "voice_id": voice_id}
        )

    # -------------------------------------------------------------- cloning

    def upload_audio(self, file_path: str, purpose: str = "voice_clone") -> int:
        """上传本地音频,返回 file_id。purpose: voice_clone | prompt_audio"""
        if purpose not in {"voice_clone", "prompt_audio"}:
            raise MiniMaxAPIError(f"不支持的 purpose: {purpose}")
        import os
        from pathlib import Path

        p = Path(file_path)
        if not p.is_file():
            raise MiniMaxAPIError(f"文件不存在: {file_path}")
        size = os.path.getsize(p)
        if size > 20 * 1024 * 1024:
            raise MiniMaxAPIError("音频超过 20MB 上限")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with open(p, "rb") as f:
                    r = client.post(
                        f"{self.base}/v1/files/upload",
                        headers=self._headers(json_body=False),
                        data={"purpose": purpose},
                        files={"file": (p.name, f, "application/octet-stream")},
                    )
        except httpx.HTTPError as e:
            raise MiniMaxAPIError(f"上传网络失败: {e}") from e
        try:
            data = r.json()
        except ValueError:
            raise MiniMaxAPIError(f"上传非 JSON 响应 [{r.status_code}]") from None
        base_resp = data.get("base_resp") or {}
        code = base_resp.get("status_code")
        if r.status_code >= 400 or (code is not None and code != 0):
            raise MiniMaxAPIError(
                f"上传错误 code={code}: {base_resp.get('status_msg', '')}",
                status_code=code,
            )
        item = data.get("file") or {}
        file_id = item.get("file_id")
        if file_id is None:
            raise MiniMaxAPIError("上传成功但未返回 file_id")
        return int(file_id)

    def clone_voice(
        self,
        file_id: int,
        voice_id: str,
        *,
        prompt_audio_id: int | None = None,
        prompt_text: str | None = None,
        language: str = "Chinese",
    ) -> dict:
        """快速复刻音色。返回响应(demo_audio 可能为空)。"""
        body: dict[str, Any] = {
            "file_id": int(file_id),
            "voice_id": voice_id,
        }
        if prompt_audio_id is not None and prompt_text:
            body["clone_prompt"] = {
                "prompt_audio": int(prompt_audio_id),
                "prompt_text": prompt_text,
            }
        if language:
            body["language_boost"] = language
        return self._post("/v1/voice_clone", body)
