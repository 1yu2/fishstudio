"""
Qwen3-Omni 多模态理解工具 - 阿里云百炼 Qwen3-Omni-Flash
支持对图片、音频、视频进行理解分析，并以文字 + 语音双模态返回结果。
https://help.aliyun.com/zh/model-studio/qwen-omni
"""
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.tools import tool
from openai import OpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 优先加载 backend/.env
BASE_DIR = Path(__file__).parent.parent.parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# 从环境变量获取配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com"
).strip()

# Qwen3-Omni 模型（非思考模式）
QWEN_OMNI_MODEL = "qwen3-omni-flash"

# 音频存储目录
STORAGE_DIR = BASE_DIR / "storage"
AUDIOS_DIR = STORAGE_DIR / "audios"
AUDIOS_DIR.mkdir(parents=True, exist_ok=True)

# 支持的媒体格式
IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
AUDIO_MIME = {
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}
VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/avi",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _resolve_local_path(media_path: str) -> Path:
    """将 /storage/... 路径解析为绝对路径，并校验文件存在。"""
    if media_path.startswith("/storage/"):
        abs_path = BASE_DIR / media_path.lstrip("/")
    else:
        abs_path = Path(media_path)

    if not abs_path.exists():
        raise FileNotFoundError(f"文件不存在: {abs_path}")
    return abs_path


def _encode_file_to_base64(file_path: Path) -> str:
    """读取文件并返回 Base64 字符串。"""
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _detect_media_type(file_path: Path) -> str:
    """根据扩展名自动检测媒体类型（image / audio / video）。"""
    ext = file_path.suffix.lower()
    if ext in IMAGE_MIME:
        return "image"
    if ext in AUDIO_MIME:
        return "audio"
    if ext in VIDEO_MIME:
        return "video"
    raise ValueError(
        f"无法识别的媒体格式 '{ext}'，支持: "
        f"{list(IMAGE_MIME)} / {list(AUDIO_MIME)} / {list(VIDEO_MIME)}"
    )


def _get_mime(file_path: Path, media_type: str) -> str:
    """获取文件的 MIME 类型。"""
    ext = file_path.suffix.lower()
    table = {"image": IMAGE_MIME, "audio": AUDIO_MIME, "video": VIDEO_MIME}[media_type]
    return table.get(ext, f"{media_type}/octet-stream")


def _save_audio_chunks(chunks_b64: list[str], prefix: str = "omni") -> str:
    """
    拼接流式返回的 Base64 音频片段并保存为 WAV 文件。

    Returns:
        HTTP 访问路径，如 /storage/audios/omni_xxx.wav
    """
    raw = b"".join(base64.b64decode(c) for c in chunks_b64)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = str(uuid.uuid4())[:8]
    filename = f"{prefix}_{timestamp}_{uid}.wav"
    file_path = AUDIOS_DIR / filename
    with open(file_path, "wb") as f:
        f.write(raw)
    http_path = f"/storage/audios/{filename}"
    logger.info(f"✅ 音频已保存: {file_path}")
    return http_path


def _call_qwen_omni(
    messages: list,
    output_audio: bool,
    voice: str,
) -> tuple[str, Optional[str]]:
    """
    调用 Qwen3-Omni 流式接口，收集文本和音频。

    Returns:
        (text_response, audio_url_or_None)
    """
    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=f"{DASHSCOPE_BASE_URL.rstrip('/')}/compatible-mode/v1",
    )

    kwargs: dict = {
        "model": QWEN_OMNI_MODEL,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        # 关闭思考模式（Qwen3-Omni-Flash 要求）
        "extra_body": {"enable_thinking": False},
    }

    if output_audio:
        kwargs["modalities"] = ["text", "audio"]
        kwargs["audio"] = {"voice": voice, "format": "wav"}
    else:
        kwargs["modalities"] = ["text"]

    completion = client.chat.completions.create(**kwargs)

    text_parts: list[str] = []
    audio_chunks: list[str] = []

    for chunk in completion:
        if not chunk.choices:
            # usage chunk
            continue
        delta = chunk.choices[0].delta

        # 收集文本
        if delta.content:
            text_parts.append(delta.content)

        # 收集音频（delta.audio 是 dict，包含 "data" 键）
        if output_audio and hasattr(delta, "audio") and delta.audio:
            audio_data = delta.audio
            if isinstance(audio_data, dict) and "data" in audio_data:
                audio_chunks.append(audio_data["data"])

    text_response = "".join(text_parts)

    audio_url: Optional[str] = None
    if output_audio and audio_chunks:
        audio_url = _save_audio_chunks(audio_chunks, prefix="omni")

    return text_response, audio_url


# ── Tool Schema & 实现 ────────────────────────────────────────────────────────

class QwenOmniUnderstandInput(BaseModel):
    """Qwen3-Omni 多模态理解输入参数"""

    media_path: str = Field(
        description=(
            "媒体文件路径，支持本地存储路径（如 /storage/images/xxx.jpg、"
            "/storage/audios/xxx.mp3、/storage/videos/xxx.mp4）或绝对路径。"
            "支持图片（jpg/png/webp/gif/bmp）、音频（mp3/wav/m4a/aac）、"
            "视频（mp4/mov/avi/mkv/webm）。"
        )
    )
    question: str = Field(
        description="对该媒体内容提出的问题或指令，例如：'图中描绘的是什么景象？'、'这段音频在说什么？'、'视频里的人在做什么？'"
    )
    media_type: Optional[str] = Field(
        default=None,
        description=(
            "媒体类型：'image'、'audio'、'video'。"
            "留空则根据文件扩展名自动判断。"
        ),
    )
    output_audio: bool = Field(
        default=True,
        description="是否同时生成语音回答（WAV 格式）。默认 True，同时返回文字和音频。",
    )
    voice: str = Field(
        default="Cherry",
        description="语音音色，默认 Cherry。可选：Ethan、Serena 等 Qwen-TTS 支持的音色。",
    )


@tool("qwen_omni_understand", args_schema=QwenOmniUnderstandInput)
def qwen_omni_understand_tool(
    media_path: str,
    question: str,
    media_type: Optional[str] = None,
    output_audio: bool = True,
    voice: str = "Cherry",
) -> str:
    """
    Qwen3-Omni 多模态理解工具 —— 对图片、音频或视频进行智能分析，并以文字（+ 可选语音）返回结果。

    **支持的媒体类型**
    - 图片（jpg / png / webp / gif / bmp）：描述画面、识别内容、回答视觉问题
    - 音频（mp3 / wav / m4a / aac）：转录内容、分析语义、识别声音
    - 视频（mp4 / mov / avi / mkv / webm）：理解情节、描述画面、回答视频问题

    **输出**
    - 文字回答（text_response）
    - 可选：WAV 语音回答（audio_url，可直接播放）

    **典型用途**
    - 理解用户上传的图片后生成配套文案
    - 转录并总结用户上传的音频/视频内容
    - 作为多媒体创作流水线的内容分析节点

    Args:
        media_path: 媒体文件路径（/storage/... 本地路径或绝对路径）
        question: 对媒体内容提出的问题
        media_type: 媒体类型（可选，留空自动判断）
        output_audio: 是否同时返回语音回答，默认 True
        voice: 语音音色，默认 Cherry

    Returns:
        JSON 字符串，包含 text_response、audio_url（可选）、media_type 等信息
    """
    try:
        if not DASHSCOPE_API_KEY:
            return "Error: 未配置 DASHSCOPE_API_KEY（请在 backend/.env 设置）"

        # 1. 解析文件路径
        file_path = _resolve_local_path(media_path)

        # 2. 检测媒体类型
        detected_type = media_type or _detect_media_type(file_path)
        logger.info(
            f"🔍 Qwen3-Omni 分析: type={detected_type}, file={file_path.name}, "
            f"question={question[:60]}..."
        )

        # 3. 编码文件
        b64_data = _encode_file_to_base64(file_path)
        mime = _get_mime(file_path, detected_type)
        logger.info(f"📦 文件已编码: mime={mime}, size={file_path.stat().st_size} bytes")

        # 4. 构建消息
        if detected_type == "image":
            media_content = {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64_data}"},
            }
        elif detected_type == "audio":
            # input_audio 格式
            fmt = file_path.suffix.lstrip(".").lower()
            if fmt == "m4a":
                fmt = "mp4"
            media_content = {
                "type": "input_audio",
                "input_audio": {
                    "data": f"data:{mime};base64,{b64_data}",
                    "format": fmt,
                },
            }
        else:  # video
            media_content = {
                "type": "video_url",
                "video_url": {"url": f"data:{mime};base64,{b64_data}"},
            }

        messages = [
            {
                "role": "user",
                "content": [
                    media_content,
                    {"type": "text", "text": question},
                ],
            }
        ]

        # 5. 调用模型
        text_response, audio_url = _call_qwen_omni(
            messages=messages,
            output_audio=output_audio,
            voice=voice,
        )

        # 6. 组装结果
        result: dict = {
            "text_response": text_response,
            "media_path": media_path,
            "media_type": detected_type,
            "question": question,
            "model": QWEN_OMNI_MODEL,
            "provider": "qwen3-omni",
            "message": "多模态理解完成",
        }
        if audio_url:
            result["audio_url"] = audio_url

        logger.info(
            f"✅ Qwen3-Omni 理解成功: text_len={len(text_response)}"
            + (f", audio={audio_url}" if audio_url else "")
        )
        return json.dumps(result, ensure_ascii=False)

    except FileNotFoundError as e:
        logger.error(f"❌ 文件未找到: {e}")
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"❌ Qwen3-Omni 理解失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error: {e}"


# ── 本地测试入口 ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✅ 已加载环境变量: {env_path}")
    else:
        print(f"⚠️  未找到 .env 文件: {env_path}")

    logging.basicConfig(level=logging.INFO)

    # --- 图片理解 ---
    print("\n=== 测试图片理解 ===")
    result = qwen_omni_understand_tool.invoke({
        "media_path": "/storage/images/20251203_161650_d8a0d590_Oriental_Pearl_Tower_in_Shangh.png",
        "question": "图中描绘的是什么景象？",
        "output_audio": True,
    })
    print(result)

    # --- 音频理解 ---
    print("\n=== 测试音频理解 ===")
    result = qwen_omni_understand_tool.invoke({
        "media_path": "/storage/audios/your_audio.mp3",
        "question": "这段音频在说什么？",
        "output_audio": True,
    })
    print(result)

    # --- 视频理解 ---
    # print("\n=== 测试视频理解 ===")
    # result = qwen_omni_understand_tool.invoke({
    #     "media_path": "/storage/videos/your_video.mp4",
    #     "question": "视频中发生了什么？",
    #     "output_audio": True,
    # })
    # print(result)

    print("请取消注释上方测试用例后运行。")
