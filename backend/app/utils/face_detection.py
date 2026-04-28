"""
人脸检测工具函数 - 使用OpenCV进行轻量级人脸检测
"""
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 尝试导入PIL用于图像处理
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("⚠️ PIL未安装，部分功能可能受限")


def detect_face_opencv(image_path: Path) -> Dict[str, any]:
    """
    使用OpenCV Haar Cascade检测人脸（轻量级方案）
    
    Args:
        image_path: 图片路径
    
    Returns:
        {
            "has_face": bool,  # 是否检测到人脸
            "face_count": int,  # 检测到的人脸数量
            "face_boxes": List[Tuple[x1, y1, x2, y2]],  # 人脸边界框列表（像素坐标）
            "confidence": float,  # 平均置信度（Haar Cascade不提供置信度，返回1.0或0.0）
            "largest_face": Optional[Dict]  # 最大人脸的信息
        }
    """
    try:
        # 加载Haar Cascade模型（OpenCV自带，无需额外下载）
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        face_cascade = cv2.CascadeClassifier(cascade_path)
        
        if face_cascade.empty():
            raise RuntimeError("无法加载Haar Cascade模型文件")
        
        # 读取图片
        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"无法读取图片: {image_path}")
        
        # 转换为灰度图（Haar Cascade需要灰度图）
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        
        logger.info(f"🔍 开始检测人脸: 图片尺寸={width}x{height}")
        
        # 检测人脸
        # scaleFactor: 每次图像缩放的倍数（1.1表示每次缩小10%）
        # minNeighbors: 每个候选矩形应该保留的邻居数量（越高越严格）
        # minSize: 最小人脸尺寸（30x30像素）
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30),
            flags=cv2.CASCADE_SCALE_IMAGE
        )
        
        face_count = len(faces)
        face_boxes = []
        largest_face = None
        max_area = 0
        
        for (x, y, w, h) in faces:
            # 转换为 (x1, y1, x2, y2) 格式，并转换为 Python 原生类型（避免 JSON 序列化错误）
            x1, y1, x2, y2 = int(x), int(y), int(x + w), int(y + h)
            face_boxes.append((x1, y1, x2, y2))
            
            # 计算人脸面积，转换为 Python 原生类型
            area = int(w * h)
            if area > max_area:
                max_area = area
                largest_face = {
                    "box": (x1, y1, x2, y2),
                    "confidence": 1.0,  # Haar Cascade不提供置信度，使用1.0作为占位值
                    "area": area,
                    "width": int(w),
                    "height": int(h),
                    "center": (int(x + w // 2), int(y + h // 2))
                }
        
        result = {
            "has_face": bool(face_count > 0),
            "face_count": int(face_count),  # 确保是 Python int 类型
            "face_boxes": face_boxes,
            "confidence": float(1.0 if face_count > 0 else 0.0),  # 确保是 Python float 类型
            "largest_face": largest_face
        }
        
        logger.info(f"✅ 人脸检测完成: 检测到{face_count}张人脸")
        return result
        
    except Exception as e:
        logger.error(f"❌ OpenCV人脸检测失败: {e}")
        raise


def validate_face_quality(face_info: Dict, image_path: Path) -> Tuple[bool, str]:
    """
    验证人脸质量是否适合用于虚拟主播生成
    
    Args:
        face_info: detect_face_opencv 返回的人脸信息
        image_path: 图片路径
    
    Returns:
        (is_valid, error_message)
        - is_valid: 是否通过验证
        - error_message: 错误信息（如果验证失败）
    """
    # 1. 基础验证：是否有脸
    if not face_info["has_face"]:
        return False, "图片中未检测到人脸，请上传包含清晰人脸的肖像照片"
    
    # 2. 验证：人脸数量（建议只有一张脸）
    if face_info["face_count"] > 1:
        logger.warning(f"⚠️ 检测到多张人脸（{face_info['face_count']}张），将使用最大的人脸")
        # 不直接拒绝，但给出警告
    
    # 3. 验证：人脸大小（至少占图片的10%）
    # 注意：LLM方法可能不提供area字段，需要容错处理
    if face_info.get("largest_face"):
        largest_face = face_info["largest_face"]
        # 检查是否有area字段（OpenCV方法提供，LLM方法不提供）
        if "area" in largest_face:
            if PIL_AVAILABLE:
                image = Image.open(image_path)
                img_width, img_height = image.size
            else:
                # 使用OpenCV获取尺寸
                img = cv2.imread(str(image_path))
                if img is not None:
                    img_height, img_width = img.shape[:2]
                else:
                    return False, "无法读取图片尺寸"
            
            img_area = img_width * img_height
            face_area = largest_face["area"]
            face_ratio = face_area / img_area
            
            if face_ratio < 0.1:  # 人脸太小
                return False, f"人脸在图片中占比过小（{face_ratio*100:.1f}%），请上传人脸更清晰、更大的照片（建议占比>10%）"
            
            if face_ratio > 0.8:  # 人脸太大（可能是裁剪问题）
                logger.warning(f"⚠️ 人脸占比很大（{face_ratio*100:.1f}%），可能影响生成效果")
        else:
            # LLM方法不提供area，使用其他信息进行验证
            # 如果LLM返回is_clear=False，说明人脸不清晰
            if face_info.get("method") == "llm":
                llm_result = face_info.get("llm_result", {})
                is_clear = llm_result.get("is_clear", False)
                if not is_clear:
                    logger.warning("⚠️ 大模型检测到人脸可能不够清晰，建议上传更清晰的照片")
            else:
                logger.warning("⚠️ 无法获取人脸面积信息，跳过面积验证")
    
    # 4. 验证：图片尺寸（建议至少256x256）
    if PIL_AVAILABLE:
        image = Image.open(image_path)
        img_width, img_height = image.size
    else:
        img = cv2.imread(str(image_path))
        if img is not None:
            img_height, img_width = img.shape[:2]
        else:
            return False, "无法读取图片尺寸"
    
    if img_width < 256 or img_height < 256:
        return False, f"图片尺寸过小（{img_width}x{img_height}），建议至少512x512像素"
    
    return True, "验证通过"
