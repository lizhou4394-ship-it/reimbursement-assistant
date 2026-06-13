"""压缩包解压服务"""
import os
import zipfile
import fitz  # PyMuPDF
from typing import List, Dict, Tuple
from utils.file_utils import is_supported_file, is_pdf, is_image, create_temp_dir


class ZipExtractor:
    """压缩包解压与文件处理服务"""

    def __init__(self):
        self.temp_dir = create_temp_dir()
        self.extracted_files: List[Dict] = []

    def extract_zip(self, zip_bytes: bytes) -> List[Dict]:
        """
        解压zip文件并返回文件信息列表

        Args:
            zip_bytes: zip文件的二进制内容

        Returns:
            文件信息列表，每项包含:
            - filename: 文件名
            - filepath: 解压后的完整路径
            - type: 文件类型 (image/pdf)
            - images: 转换后的图片路径列表（PDF会有多页）
        """
        # 保存zip到临时目录
        zip_path = os.path.join(self.temp_dir, "upload.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_bytes)

        # 解压
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(self.temp_dir)

        # 遍历解压后的文件
        self.extracted_files = []
        for root, dirs, files in os.walk(self.temp_dir):
            for filename in files:
                if filename == "upload.zip":
                    continue
                if not is_supported_file(filename):
                    continue

                filepath = os.path.join(root, filename)
                file_info = {
                    "filename": filename,
                    "filepath": filepath,
                    "type": "pdf" if is_pdf(filename) else "image",
                    "images": [],
                }

                if is_pdf(filename):
                    # PDF转图片 + 提取文字
                    file_info["images"] = self._pdf_to_images(filepath, filename)
                    file_info["page_texts"] = self._extract_pdf_texts(filepath)
                else:
                    # 图片直接使用
                    file_info["images"] = [filepath]
                    file_info["page_texts"] = []

                self.extracted_files.append(file_info)

        # 按文件名排序
        self.extracted_files.sort(key=lambda x: x["filename"])
        return self.extracted_files

    def _extract_pdf_texts(self, pdf_path: str) -> List[str]:
        """
        提取PDF每一页的文字内容（用于文本型PDF的精确解析）

        Returns:
            每页文字的列表，如 ["第1页文字", "第2页文字", ...]
        """
        texts = []
        try:
            doc = fitz.open(pdf_path)
            for page in doc:
                text = page.get_text("text").strip()
                texts.append(text)
            doc.close()
        except Exception as e:
            print(f"PDF文字提取失败: {pdf_path}, 错误: {e}")
        return texts

    def _pdf_to_images(self, pdf_path: str, pdf_name: str) -> List[str]:
        """
        将PDF每一页转为图片

        Args:
            pdf_path: PDF文件路径
            pdf_name: PDF文件名（用于生成图片名）

        Returns:
            图片路径列表
        """
        image_paths = []
        try:
            doc = fitz.open(pdf_path)
            base_name = os.path.splitext(pdf_name)[0]

            for page_num in range(len(doc)):
                page = doc[page_num]
                # 高分辨率渲染
                mat = fitz.Matrix(3, 3)  # 3x缩放，提高清晰度
                pix = page.get_pixmap(matrix=mat)

                # 保存图片
                img_name = f"{base_name}_page{page_num + 1}.png"
                img_path = os.path.join(self.temp_dir, img_name)
                pix.save(img_path)
                image_paths.append(img_path)

            doc.close()
        except Exception as e:
            print(f"PDF转图片失败: {pdf_path}, 错误: {e}")
            # 如果PDF转图片失败，尝试直接作为PDF处理
            image_paths = []

        return image_paths

    def get_file_count(self) -> Tuple[int, int]:
        """返回 (图片文件数, PDF文件数)"""
        image_count = sum(1 for f in self.extracted_files if f["type"] == "image")
        pdf_count = sum(1 for f in self.extracted_files if f["type"] == "pdf")
        return image_count, pdf_count

    def cleanup(self):
        """清理临时文件"""
        import shutil
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except Exception:
            pass
