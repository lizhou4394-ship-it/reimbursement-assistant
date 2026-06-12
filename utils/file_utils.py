"""文件工具函数"""
import os
import base64
import tempfile
from pathlib import Path

# 支持的图片格式
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}

# 支持的PDF格式
PDF_EXTENSIONS = {".pdf"}


def is_image(filename: str) -> bool:
    """判断是否为图片文件"""
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def is_pdf(filename: str) -> bool:
    """判断是否为PDF文件"""
    return Path(filename).suffix.lower() in PDF_EXTENSIONS


def is_supported_file(filename: str) -> bool:
    """判断是否为支持的文件类型"""
    return is_image(filename) or is_pdf(filename)


def image_to_base64(image_path: str) -> str:
    """将图片文件转为base64字符串"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def get_image_mime_type(filename: str) -> str:
    """获取图片MIME类型"""
    ext = Path(filename).suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }
    return mime_map.get(ext, "image/jpeg")


def create_temp_dir() -> str:
    """创建临时目录"""
    return tempfile.mkdtemp(prefix="reimbursement_")


def format_amount(amount) -> float:
    """格式化金额，保留2位小数"""
    try:
        return round(float(amount), 2)
    except (ValueError, TypeError):
        return 0.00
