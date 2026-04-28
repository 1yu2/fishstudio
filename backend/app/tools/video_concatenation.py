"""
视频拼接工具 - 将多个视频片段拼接为一个完整视频
"""
import json
import logging
import os
import uuid
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 优先加载 backend/.env（避免直接运行工具脚本时环境未加载）
BASE_DIR = Path(__file__).parent.parent.parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# 视频存储目录
STORAGE_DIR = BASE_DIR / "storage"
VIDEOS_DIR = STORAGE_DIR / "videos"

# 确保视频存储目录存在
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# Mock 模式配置（需要在 VIDEOS_DIR 定义之后）
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"
# Mock 视频路径（启用 MOCK_MODE 时必须配置）
MOCK_VIDEO_PATH = os.getenv("MOCK_VIDEO_PATH", "").strip()
if MOCK_MODE and not MOCK_VIDEO_PATH:
    logger.warning(
        "MOCK_MODE=true 时，建议配置 MOCK_VIDEO_PATH。"
        "请在 backend/.env 中设置 MOCK_VIDEO_PATH=/storage/videos/your_video.mp4"
    )

# 尝试导入 moviepy（兼容 1.x / 2.x）
try:
    # moviepy 2.x 推荐直接从 moviepy 导入
    from moviepy import VideoFileClip, concatenate_videoclips
    MOVIEPY_AVAILABLE = True
except Exception:
    try:
        # 兼容 1.x 旧路径
        from moviepy.editor import VideoFileClip, concatenate_videoclips
        MOVIEPY_AVAILABLE = True
    except ImportError:
        MOVIEPY_AVAILABLE = False
        logger.warning("⚠️ moviepy 未安装或版本不兼容，视频拼接功能将不可用。请运行: pip install \"moviepy>=1.0.3\"")


def prepare_video_path(video_url: str) -> Path:
    """
    准备视频文件路径，支持本地路径和 URL
    
    Args:
        video_url: 本地路径（如 /storage/videos/xxx.mp4）或 URL（如 http://localhost:8000/storage/videos/xxx.mp4）
    
    Returns:
        本地文件路径（Path 对象）
    
    Raises:
        FileNotFoundError: 本地文件不存在
        ValueError: URL 下载失败
    """
    # 检查是否是本地路径
    if video_url.startswith("/storage/"):
        file_path = BASE_DIR / video_url.lstrip("/")
        if not file_path.exists():
            raise FileNotFoundError(f"本地文件不存在: {file_path}")
        logger.info(f"📁 使用本地文件: {file_path}")
        return file_path
    
    # 检查是否是 localhost URL
    if video_url.startswith("http://localhost") or video_url.startswith("http://127.0.0.1"):
        # 提取路径部分
        parsed_url = urlparse(video_url)
        local_path = parsed_url.path
        if local_path.startswith("/storage/"):
            file_path = BASE_DIR / local_path.lstrip("/")
            if file_path.exists():
                logger.info(f"📁 从 localhost URL 转换为本地路径: {file_path}")
                return file_path
    
    # 如果是公网 URL，需要下载
    logger.info(f"📥 下载视频: {video_url}")
    try:
        response = requests.get(video_url, timeout=300, stream=True)
        response.raise_for_status()
        
        # 生成临时文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        ext = os.path.splitext(urlparse(video_url).path)[1] or ".mp4"
        filename = f"temp_{timestamp}_{unique_id}{ext}"
        temp_path = VIDEOS_DIR / filename
        
        # 保存到临时文件
        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        logger.info(f"✅ 视频已下载到: {temp_path}")
        return temp_path
        
    except Exception as e:
        logger.error(f"❌ 下载视频失败: {str(e)}")
        raise ValueError(f"无法下载视频: {str(e)}")


def concatenate_videos(
    video_urls: List[str],
    output_filename: Optional[str] = None
) -> str:
    """
    将多个视频片段拼接为一个完整视频
    
    Args:
        video_urls: 视频路径列表（支持本地路径和URL）
        output_filename: 输出文件名（可选，如果不提供则自动生成）
    
    Returns:
        拼接后的视频路径（相对路径，如 /storage/videos/xxx.mp4）
    
    Raises:
        ValueError: 如果 moviepy 未安装或视频列表为空
        FileNotFoundError: 如果视频文件不存在
    """
    if not MOVIEPY_AVAILABLE:
        raise ValueError(
            "moviepy 未安装，无法拼接视频。"
            "请运行: pip install moviepy"
        )
    
    if not video_urls or len(video_urls) < 2:
        raise ValueError("至少需要2个视频片段才能拼接")
    
    try:
        logger.info(f"🎬 开始拼接 {len(video_urls)} 个视频片段")
        
        # 准备所有视频文件路径
        video_paths = []
        for i, video_url in enumerate(video_urls, 1):
            logger.info(f"  处理片段 {i}/{len(video_urls)}: {video_url}")
            video_path = prepare_video_path(video_url)
            video_paths.append(video_path)
        
        # 加载所有视频片段
        clips = []
        first_clip = None
        for i, video_path in enumerate(video_paths):
            logger.info(f"  加载视频片段 {i+1}: {video_path}")
            clip = VideoFileClip(str(video_path))
            
            # 记录第一个视频的分辨率和帧率（用于统一）
            if i == 0:
                first_clip = clip
                target_size = clip.size
                target_fps = clip.fps
                logger.info(f"  目标分辨率: {target_size}, 帧率: {target_fps}")
            
            # 统一分辨率和帧率（如果需要）
            if clip.size != target_size or clip.fps != target_fps:
                logger.info(f"  调整视频 {i+1} 的分辨率和帧率: {clip.size} -> {target_size}, {clip.fps} -> {target_fps}")
                # moviepy 2.x 使用 resized/with_fps；1.x 使用 resize/set_fps
                if hasattr(clip, "resized"):
                    clip = clip.resized(target_size)
                else:
                    clip = clip.resize(target_size)
                if hasattr(clip, "with_fps"):
                    clip = clip.with_fps(target_fps)
                else:
                    clip = clip.set_fps(target_fps)
            
            clips.append(clip)
        
        # 拼接视频
        logger.info("  正在拼接视频片段...")
        final_clip = concatenate_videoclips(clips, method="compose")
        
        # 生成输出文件名
        if not output_filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            output_filename = f"concatenated_{timestamp}_{unique_id}.mp4"
        
        # 确保输出文件名有扩展名
        if not output_filename.endswith(".mp4"):
            output_filename += ".mp4"
        
        output_path = VIDEOS_DIR / output_filename
        
        # 写入文件
        logger.info(f"  正在保存拼接后的视频: {output_path}")
        final_clip.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            fps=target_fps,
            preset="medium"
        )
        
        # 清理资源
        for clip in clips:
            clip.close()
        final_clip.close()
        
        # 返回HTTP访问路径
        http_path = f"/storage/videos/{output_filename}"
        logger.info(f"✅ 视频拼接完成: {http_path}")
        logger.info(f"   总时长: {final_clip.duration:.2f}秒")
        logger.info(f"   分辨率: {target_size}")
        
        return http_path
        
    except Exception as e:
        logger.error(f"❌ 视频拼接失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise


class ConcatenateVideosInput(BaseModel):
    """视频拼接输入参数"""
    video_urls: List[str] = Field(description="视频路径列表，按顺序拼接。支持本地路径（如 /storage/videos/xxx.mp4）或 URL")
    output_filename: Optional[str] = Field(default=None, description="输出文件名（可选，如果不提供则自动生成）")


@tool("concatenate_videos", args_schema=ConcatenateVideosInput)
def concatenate_videos_tool(
    video_urls: List[str],
    output_filename: Optional[str] = None
) -> str:
    """
    将多个视频片段拼接为一个完整视频。
    
    **使用场景**：
    - 长视频生成：当需要生成超过单个片段时长限制（4-12秒）的长视频时
    - 视频组合：将多个独立生成的视频片段组合成完整作品
    
    **典型工作流**（长视频生成）：
    当用户要求生成超过单个片段时长限制的长视频时：
    1. 拆分镜头：根据总时长拆分为多个镜头场景
    2. 生成图片：为每个镜头生成首帧图片（generate_volcano_image）
    3. 生成视频：基于图片生成视频片段（generate_volcano_video，mode="image"）
    4. 拼接视频：使用本工具将所有片段按顺序拼接
    
    **参数说明**：
    - video_urls: 视频路径列表，按故事顺序排列。支持：
      * 本地路径：/storage/videos/xxx.mp4
      * localhost URL：http://localhost:8000/storage/videos/xxx.mp4
      * 公网 URL：https://example.com/video.mp4（会自动下载）
    - output_filename: 输出文件名（可选），如不提供则自动生成
    
    **技术细节**：
    - 自动统一所有片段的分辨率和帧率（使用第一个视频的参数）
    - 支持不同格式的视频（会自动转换）
    - 输出格式：MP4 (H.264 + AAC)
    
    Args:
        video_urls: 视频路径列表，按顺序拼接
        output_filename: 输出文件名（可选）
    
    Returns:
        拼接后的视频路径的JSON字符串（格式：{"video_url": "/storage/videos/xxx.mp4", ...}）
    """
    # Mock 模式：直接返回固定的视频路径
    if MOCK_MODE:
        logger.info(f"🎭 [MOCK模式] 拼接视频: {len(video_urls)} 个片段")
        result = {
            'video_url': MOCK_VIDEO_PATH or "/storage/videos/mock_concatenated.mp4",
            'local_path': MOCK_VIDEO_PATH or "/storage/videos/mock_concatenated.mp4",
            'video_count': len(video_urls),
            'output_filename': output_filename or "mock_concatenated.mp4",
            'mock': True,
            'message': '[MOCK] 视频已拼接并保存到本地'
        }
        return json.dumps(result, ensure_ascii=False)
    
    try:
        if not MOVIEPY_AVAILABLE:
            error_msg = "moviepy 未安装，无法拼接视频。请运行: pip install moviepy"
            logger.error(error_msg)
            return json.dumps({
                'error': error_msg
            }, ensure_ascii=False)
        
        logger.info(f"🎬 开始拼接 {len(video_urls)} 个视频片段")
        
        # 拼接视频
        output_path = concatenate_videos(video_urls, output_filename)
        
        # 构建返回结果
        result = {
            'video_url': output_path,
            'local_path': output_path,
            'video_count': len(video_urls),
            'output_filename': os.path.basename(output_path),
            'message': f'成功拼接 {len(video_urls)} 个视频片段'
        }
        
        return json.dumps(result, ensure_ascii=False)
        
    except Exception as e:
        error_msg = f"视频拼接失败: {str(e)}"
        logger.error(error_msg)
        import traceback
        logger.error(traceback.format_exc())
        return json.dumps({
            'error': error_msg
        }, ensure_ascii=False)


if __name__ == "__main__":
    # 简单测试示例（使用写死的本地视频列表）
    import sys

    # 加载环境变量
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
        print(f"✅ 已加载环境变量: {ENV_PATH}")
    else:
        print(f"⚠️  未找到 .env 文件: {ENV_PATH}")
    
    logging.basicConfig(level=logging.INFO)
    
    # 如果 moviepy 未安装，提示使用当前解释器安装并退出
    if not MOVIEPY_AVAILABLE:
        print("❌ moviepy 未安装，无法拼接视频。")
        print("请使用当前解释器安装：python -m pip install \"moviepy>=1.0.3\"")
        sys.exit(1)
    
    # 写死的测试视频列表（确保文件存在）
    video_urls = [
        "/storage/videos/volcano_20260115_182750_2c304555_喜庆红色背景金色祥云环绕一匹金色骏马从画面左侧奔腾至右侧.mp4",
        "/storage/videos/volcano_20260117_225006_b7514562_在阳光下熠熠生辉穿梭其中.mp4",
    ]

    print(f"\n测试拼接 {len(video_urls)} 个视频:")
    for i, url in enumerate(video_urls, 1):
        print(f"  {i}. {url}")
    
    result = concatenate_videos_tool.invoke({
        "video_urls": video_urls
    })
    print("\n拼接结果:")
    print(result)
