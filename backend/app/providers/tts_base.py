"""TTS provider 抽象与内置实现（P1-12：对白/旁白语音合成闭环）。

- TTSRequest / TTSProvider：统一请求与抽象基类（风格对齐 providers/base.py）。
- MockTTSProvider：ffmpeg 生成正弦波 wav，时长按文本字数估算；仅测试/演示，
  worker 侧受 ALLOW_MOCK_PROVIDERS 控制。
- QwenTTSProvider：薄封装 app.tools.qwen_tts 的阿里云百炼声音设计/复刻能力
  （同步 requests 调用，经 run_in_executor 包装）；无 DASHSCOPE_API_KEY 时
  is_available()=False。
"""

import asyncio
import json
import logging
import os
import subprocess
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from app.providers.base import ProviderResult

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
AUDIOS_DIR = BASE_DIR / "storage" / "audios"

# 语速估算：中文约 4 字/秒（mock 时长用）
_CHARS_PER_SECOND = 4.0


class TTSRequest(BaseModel):
    """TTS 合成统一请求。"""

    text: str
    voice_id: str = ""  # 音色标识（voice asset id / provider 音色名），空 = provider 默认音色
    voice_model: str = ""
    lang: str = "zh"
    # 参考音频（声音复刻用）：/storage/ 路径或本地路径，可选
    voice_reference: Optional[str] = None
    # 输出文件名前缀（mock/落盘用）
    filename_prefix: str = "tts"


class TTSProvider(ABC):
    """TTS provider 抽象基类：synthesize -> ProviderResult（artifacts 落 storage/audios/）。"""

    name: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """provider 当前是否可用（缺 API key 等返回 False）。"""

    @abstractmethod
    async def synthesize(self, req: TTSRequest) -> ProviderResult:
        """合成语音并落盘到 backend/storage/audios/，返回含 storage_key 的 artifacts。"""

    async def clone_voice(
        self, reference_audio_path: str, voice_name: str
    ) -> ProviderResult:
        """用参考音频复刻音色，返回 voice_id（raw_metadata.voice_id）+ 试听样本 artifact。

        可选实现：不支持的 provider 返回 success=False + error_code="unsupported"。
        """
        return ProviderResult(
            success=False, provider=self.name,
            error_code="unsupported", error_message=f"{self.name} 不支持 clone_voice",
        )


def estimate_duration_ms(text: str) -> int:
    """按文本字数估算语音时长（mock 与 artifact duration_ms 用）。"""
    return max(1000, int(len(text) / _CHARS_PER_SECOND * 1000))


class MockTTSProvider(TTSProvider):
    """ffmpeg 正弦波占位语音。仅测试/演示用。"""

    name = "mock_tts"

    def is_available(self) -> bool:
        return True

    async def synthesize(self, req: TTSRequest) -> ProviderResult:
        duration_ms = estimate_duration_ms(req.text)
        filename = f"{req.filename_prefix}_{uuid.uuid4().hex[:12]}.wav"
        AUDIOS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = AUDIOS_DIR / filename
        try:
            import imageio_ffmpeg

            subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(), "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000",
                    "-t", f"{duration_ms / 1000:.3f}",
                    str(file_path),
                ],
                check=True, capture_output=True, timeout=30,
            )
            generated = True
        except Exception as e:
            logger.warning(f"mock TTS 音频生成失败，退化为仅元数据: {e}")
            generated = False
        return ProviderResult(
            success=True,
            provider=self.name,
            model="mock-tts",
            artifacts=[
                {"storage_key": f"audios/{filename}", "type": "audio", "duration_ms": duration_ms}
            ],
            usage={"output_audio_seconds": duration_ms / 1000.0},
            raw_metadata={"mocked": True, "generated": generated},
        )

    async def clone_voice(
        self, reference_audio_path: str, voice_name: str
    ) -> ProviderResult:
        """mock 音色复刻：返回假 voice_id（标 mocked=True）+ 一条正弦波试听样本。"""
        voice_id = f"mock_voice_{uuid.uuid4().hex[:8]}"
        sample = await self.synthesize(
            TTSRequest(text="这是音色试听样本。", voice_id=voice_id, filename_prefix="voice_clone")
        )
        return ProviderResult(
            success=sample.success,
            provider=self.name,
            model="mock-tts",
            artifacts=sample.artifacts,
            raw_metadata={"mocked": True, "voice_id": voice_id, "voice_name": voice_name},
        )


class QwenTTSProvider(TTSProvider):
    """阿里云百炼 Qwen-TTS（薄封装 app.tools.qwen_tts）。

    - 有 voice_id 时直接使用持久化音色合成，不重复创建音色；
    - 只有 reference_audio 时执行一次声音复刻；
    - 都没有时才走声音设计临时音色。
    底层是同步 requests 调用，这里用 run_in_executor 避免阻塞事件循环。
    """

    name = "qwen_tts"
    # 默认音色描述（无 voice asset / 无参考音频时）
    DEFAULT_VOICE_DESCRIPTION = "自然清晰的中文旁白声，语速适中"

    def __init__(self, api_key: str | None = None):
        self.api_key = (
            api_key if api_key is not None else os.getenv("DASHSCOPE_API_KEY", "")
        ).strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _synthesize_sync(self, req: TTSRequest) -> dict:
        """同步调用 qwen_tts 工具函数，返回解析后的结果 dict（含 audio_url）。

        voice_id 优先级：持久化 voice_id → reference_audio → 临时声音设计。
        """
        from app.tools import qwen_tts

        if req.voice_id:
            tool_fn = getattr(qwen_tts.qwen_voice_synthesis_tool, "func", None)
            payload = {
                "voice_id": req.voice_id,
                "text": req.text,
                "language": req.lang,
                "target_model": req.voice_model or qwen_tts.QWEN_VOICE_CLONE_TARGET_MODEL,
            }
            if tool_fn is None:
                raw = qwen_tts.qwen_voice_synthesis_tool.invoke(payload)
            else:
                raw = tool_fn(**payload)
        elif req.voice_reference:
            tool_fn = getattr(qwen_tts.qwen_voice_cloning_tool, "func", None)
            if tool_fn is None:
                raw = qwen_tts.qwen_voice_cloning_tool.invoke(
                    {"reference_audio": req.voice_reference, "text": req.text, "language": req.lang}
                )
            else:
                raw = tool_fn(req.voice_reference, req.text, req.lang)
        else:
            tool_fn = getattr(qwen_tts.qwen_voice_design_tool, "func", None)
            if tool_fn is None:
                raw = qwen_tts.qwen_voice_design_tool.invoke(
                    {
                        "voice_description": self.DEFAULT_VOICE_DESCRIPTION,
                        "text": req.text,
                        "language": req.lang,
                    }
                )
            else:
                raw = tool_fn(self.DEFAULT_VOICE_DESCRIPTION, req.text, req.lang)
        if isinstance(raw, str) and raw.startswith("Error:"):
            raise RuntimeError(raw)
        return json.loads(raw)

    async def synthesize(self, req: TTSRequest) -> ProviderResult:
        if not self.is_available():
            return ProviderResult(
                success=False, provider=self.name,
                error_code="no_provider", error_message="未配置 DASHSCOPE_API_KEY",
            )
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, self._synthesize_sync, req)
        except Exception as e:
            return ProviderResult(
                success=False, provider=self.name,
                error_code="tts_failed", error_message=str(e)[:500], retryable=True,
            )
        # qwen_tts 落盘后返回 /storage/audios/xxx.wav 形式的 http 路径 → storage_key
        audio_url = result.get("audio_url") or result.get("local_path") or ""
        storage_key = audio_url.removeprefix("/storage/").lstrip("/")
        if not storage_key or not (BASE_DIR / "storage" / storage_key).is_file():
            return ProviderResult(
                success=False, provider=self.name,
                error_code="tts_failed",
                error_message=f"合成返回但产物缺失: {audio_url}",
                retryable=True, raw_metadata=result,
            )
        duration_ms = estimate_duration_ms(req.text)
        return ProviderResult(
            success=True,
            provider=self.name,
            model=result.get("provider", "qwen-tts"),
            external_request_id=result.get("request_id", ""),
            artifacts=[{"storage_key": storage_key, "type": "audio", "duration_ms": duration_ms}],
            usage={"output_audio_seconds": duration_ms / 1000.0},
            raw_metadata={
                "voice_name": result.get("voice_name", ""),
                "voice_id": result.get("voice_id", req.voice_id),
                "target_model": result.get("target_model", req.voice_model),
                "mocked": False,
            },
        )

    # 音色复刻试听文本（clone 时顺带产出的样本，供用户确认音色）
    CLONE_PREVIEW_TEXT = "你好，这是音色试听样本。"

    def _clone_voice_sync(self, reference_audio_path: str) -> dict:
        """同步调用 qwen_voice_cloning（创建音色 + 合成试听样本），返回结果 dict。

        qwen_voice_cloning 内部先走音色创建接口拿到 voice（音色标识），再用该音色
        合成文本；返回 JSON 含 voice_name（即 voice_id）与 audio_url（试听样本）。
        """
        from app.tools import qwen_tts

        tool_fn = getattr(qwen_tts.qwen_voice_cloning_tool, "func", None)
        if tool_fn is None:
            raw = qwen_tts.qwen_voice_cloning_tool.invoke(
                {
                    "reference_audio": reference_audio_path,
                    "text": self.CLONE_PREVIEW_TEXT,
                    "language": "zh",
                }
            )
        else:
            raw = tool_fn(reference_audio_path, self.CLONE_PREVIEW_TEXT, "zh")
        if isinstance(raw, str) and raw.startswith("Error"):
            raise RuntimeError(raw)
        return json.loads(raw)

    async def clone_voice(
        self, reference_audio_path: str, voice_name: str
    ) -> ProviderResult:
        """真实音色复刻：raw_metadata.voice_id 为百炼返回的音色标识，artifacts 为试听样本。"""
        if not self.is_available():
            return ProviderResult(
                success=False, provider=self.name,
                error_code="no_provider", error_message="未配置 DASHSCOPE_API_KEY",
            )
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, self._clone_voice_sync, reference_audio_path
            )
        except Exception as e:
            return ProviderResult(
                success=False, provider=self.name,
                error_code="clone_failed", error_message=str(e)[:500], retryable=True,
            )
        voice_id = result.get("voice_name", "")
        if not voice_id:
            return ProviderResult(
                success=False, provider=self.name,
                error_code="clone_failed", error_message="复刻返回但无音色标识",
                retryable=True, raw_metadata=result,
            )
        audio_url = result.get("audio_url") or result.get("local_path") or ""
        storage_key = audio_url.removeprefix("/storage/").lstrip("/")
        artifacts = []
        if storage_key and (BASE_DIR / "storage" / storage_key).is_file():
            artifacts.append({"storage_key": storage_key, "type": "audio"})
        return ProviderResult(
            success=True,
            provider=self.name,
            model=result.get("provider", "qwen-tts-voice-cloning"),
            external_request_id=result.get("request_id", ""),
            artifacts=artifacts,
            raw_metadata={
                "voice_id": voice_id,
                "voice_name": voice_name,
                "target_model": result.get("target_model", ""),
                "reference_audio": reference_audio_path,
                "mocked": False,
            },
        )
