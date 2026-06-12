"""发票解析服务 - 调用通义千问视觉API"""
import json
import base64
from typing import List, Dict, Optional
from utils.file_utils import image_to_base64, get_image_mime_type


class InvoiceParser:
    """发票解析服务，调用通义千问VL模型识别发票信息"""

    def __init__(self, api_key: str, model: str = "qwen-vl-max"):
        self.api_key = api_key
        self.model = model

    def parse_single_invoice(
        self, image_path: str, system_prompt: str = "", invoice_parse_prompt: str = ""
    ) -> Optional[Dict]:
        """
        解析单张发票/行程单

        Args:
            image_path: 图片路径
            system_prompt: 系统提示词

        Returns:
            解析结果字典，包含日期、起点、终点、金额、费用类型等
        """
        try:
            from dashscope import MultiModalConversation
            import dashscope

            dashscope.api_key = self.api_key

            # 读取图片并转为base64
            img_b64 = image_to_base64(image_path)
            mime_type = get_image_mime_type(image_path)
            data_url = f"data:{mime_type};base64,{img_b64}"

            # 构建提示词（支持外部传入）
            if invoice_parse_prompt:
                user_prompt = invoice_parse_prompt
            else:
                from config import load_prompt
                user_prompt = load_prompt("invoice_parse")

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"image": data_url},
                        {"text": user_prompt},
                    ],
                }
            ]

            response = MultiModalConversation.call(
                model=self.model,
                messages=messages,
            )

            if response.status_code == 200:
                content = response.output.choices[0].message.content[0]["text"]
                # 提取JSON
                result = self._extract_json(content)
                if result:
                    result["source_file"] = image_path
                    return result
            else:
                print(
                    f"API调用失败: {response.code} - {response.message}"
                )
                return None

        except Exception as e:
            print(f"解析发票失败: {e}")
            return None

    def parse_all_invoices(
        self,
        file_list: List[Dict],
        system_prompt: str = "",
        progress_callback=None,
        max_workers: int = 5,
        invoice_parse_prompt: str = "",
    ) -> List[Dict]:
        """
        批量并发解析所有发票

        Args:
            file_list: ZipExtractor返回的文件信息列表
            system_prompt: 系统提示词
            progress_callback: 进度回调函数 callback(current, total, filename)
            max_workers: 并发数，默认5个并发，31个文件约节省70%时间

        Returns:
            所有发票的解析结果列表
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        # 构建任务列表：每个图片一个任务
        tasks = []
        for file_info in file_list:
            images = file_info.get("images", [])
            for img_path in images:
                tasks.append((img_path, file_info["filename"]))

        results = []
        results_lock = threading.Lock()
        completed = [0]  # 用列表以便在闭包中修改
        total = len(tasks)

        def _parse_one(img_path, filename):
            """解析单张图片（线程安全）"""
            result = self.parse_single_invoice(img_path, system_prompt, invoice_parse_prompt)
            if result:
                result["source_file"] = filename
            with results_lock:
                completed[0] += 1
                if progress_callback:
                    try:
                        progress_callback(completed[0], total, filename)
                    except Exception:
                        pass
            return result

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(_parse_one, img_path, fn): (img_path, fn)
                for img_path, fn in tasks
            }
            for future in as_completed(future_to_task):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as e:
                    _, fn = future_to_task[future]
                    print(f"解析失败: {fn}, 错误: {e}")

        # 按日期排序
        results.sort(key=lambda x: x.get("date", ""))
        return results

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从AI返回文本中提取JSON"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取markdown代码块中的JSON
        import re

        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试提取花括号内的内容
        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def enrich_with_work_content(
        self, invoices: List[Dict], work_description: str
    ) -> List[Dict]:
        """
        根据工作内容描述，为每张发票匹配对应的工作内容

        Args:
            invoices: 发票解析结果列表
            work_description: 当月工作内容描述

        Returns:
            添加了work_content字段的发票列表
        """
        if not work_description.strip():
            for inv in invoices:
                inv["work_content"] = ""
            return invoices

        try:
            from dashscope import Generation
            import dashscope

            dashscope.api_key = self.api_key

            # 构建发票摘要
            invoice_summary = []
            for i, inv in enumerate(invoices):
                summary = (
                    f"{i + 1}. 日期:{inv.get('date', '')} "
                    f"类型:{inv.get('type', '')} "
                    f"起点:{inv.get('start_location', '')} "
                    f"终点:{inv.get('end_location', '')} "
                    f"金额:{inv.get('amount', '')}"
                )
                invoice_summary.append(summary)

            prompt = f"""你是一个报销助手。请根据以下工作内容描述和发票列表，为每张发票匹配对应的工作内容。

工作内容描述：
{work_description}

发票列表：
{chr(10).join(invoice_summary)}

业务优先级顺序：放射培训 → 放射调试 → 放射回访

请根据发票的时间先后顺序和地点，匹配对应的工作内容。严格按JSON数组格式返回，每项包含index(发票序号，从1开始)和work_content(匹配的工作内容)：
[
    {{"index": 1, "work_content": "具体工作内容"}},
    ...
]

只返回JSON数组，不要有其他内容。"""

            response = Generation.call(
                model="qwen-max",
                messages=[{"role": "user", "content": prompt}],
                result_format="message",
            )

            if response.status_code == 200:
                content = response.output.choices[0].message.content
                matches = self._extract_json(content)
                if isinstance(matches, list):
                    for match in matches:
                        idx = match.get("index", 0) - 1
                        if 0 <= idx < len(invoices):
                            invoices[idx]["work_content"] = match.get(
                                "work_content", ""
                            )
            else:
                # 匹配失败，使用空字符串
                for inv in invoices:
                    inv["work_content"] = ""

        except Exception as e:
            print(f"匹配工作内容失败: {e}")
            for inv in invoices:
                inv["work_content"] = ""

        # 确保所有发票都有work_content字段
        for inv in invoices:
            if "work_content" not in inv:
                inv["work_content"] = ""

        return invoices
