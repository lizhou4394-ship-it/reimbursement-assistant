"""发票解析服务 - 调用通义千问视觉API"""
import json
import re
import base64
from typing import List, Dict, Optional
from utils.file_utils import image_to_base64, get_image_mime_type


class InvoiceParser:
    """发票解析服务，调用通义千问VL模型识别发票信息"""

    def __init__(self, api_key: str, model: str = "qwen-vl-max"):
        self.api_key = api_key
        self.model = model

    def parse_single_invoice(
        self, image_path: str, system_prompt: str = "", invoice_parse_prompt: str = "",
        page_text: str = "",
    ):
        """
        解析单张发票/行程单

        解析优先级：正则解析（零成本）→ 文本模型（低成本）→ 视觉模型

        Returns:
            单个字典或字典列表（打车行程单可能返回多条记录）
        """
        # 第1优先：正则解析（零AI调用，结构化PDF专用）
        if page_text:
            regex_result = self._try_parse_regex(page_text)
            if regex_result is not None:
                return regex_result

        # 第2优先：文本模型（低成本，文本型PDF）
        if page_text and len(page_text) > 80:
            result = self._parse_with_text_model(page_text, invoice_parse_prompt)
            if result is not None:
                return result
            print(f"文本模型解析失败，回退视觉模型: {image_path}")

        # 第3优先：视觉模型（照片/扫描件）
        return self._parse_with_vision_model(image_path, system_prompt, invoice_parse_prompt)

    def _try_parse_regex(self, text: str):
        """
        尝试用正则解析结构化PDF（零AI调用）
        支持：滴滴行程单、火车票
        跳过：滴滴发票（行程单已包含全部信息）
        返回 None 表示无法正则解析，需回退AI
        """
        if '滴滴出行' in text and '行程单' in text:
            return self._regex_didi_itinerary(text)
        elif '电子发票' in text and '旅客运输服务' in text:
            # 滴滴电子发票无需解析，行程单已有全部信息
            return {'type': '_skip', 'reason': '滴滴发票无需解析'}
        elif '中国铁路' in text or ('车次' in text and '座位号' in text):
            return self._regex_train_ticket(text)
        return None

    def _regex_didi_itinerary(self, text: str):
        """正则解析滴滴出行行程单（全部行程）"""
        # 提取年份
        ym = re.search(r'(?:行程起止日期|申请日期)[：:]\s*(\d{4})', text)
        year = ym.group(1) if ym else '2026'

        # 提取总行程数用于校验
        total_match = re.search(r'共(\d+)笔行程', text)
        expected_count = int(total_match.group(1)) if total_match else 0

        # 去掉header部分（避免“合计334.85元”等干扰）
        table_start = text.find('备注')
        if table_start == -1:
            table_start = text.find('里程')
        trip_text = text[table_start:] if table_start >= 0 else text

        # 提取所有日期
        dates = re.findall(r'(\d{2}-\d{2})\s+\d{2}:\d{2}\s*[\n]?\s*[\u4e00-\u9fff]{1,2}', trip_text)
        # 提取所有金额（2位小数）
        amounts = re.findall(r'(?<!\d)(\d{1,3}\.\d{2})(?!\d)', trip_text)

        if not dates or not amounts or len(dates) != len(amounts):
            return None  # 解析不完整，回退AI

        # ★ 准确率校验
        if expected_count and len(dates) != expected_count:
            print(f"⚠️ 行程数不匹配: 期望{expected_count}条，正则提取{len(dates)}条，回退AI")
            return None
        for amt in amounts:
            if float(amt) > 400:
                print(f"⚠️ 异常金额: ¥{amt}，回退AI")
                return None

        # 提取车型（用于顺风车/拼车标记）
        car_types = self._extract_car_types(trip_text)

        # 提取地点
        segments = re.split(r'\d{2}-\d{2}\s+\d{2}:\d{2}\s*[\n]?\s*[\u4e00-\u9fff]{1,2}', trip_text)
        locations = []
        for i in range(1, len(segments)):
            seg = segments[i]
            seg = re.sub(r'^[\s\n]*[\u4e00-\u9fff]{1,4}[\s\n]*市[\s\n]*', '', seg)
            seg = re.sub(r'\n\d+\.?\d*\n\d+\.\d{2}.*$', '', seg, flags=re.DOTALL)
            parts = seg.strip().split('\n')
            parts = [p.strip() for p in parts if p.strip()]
            parts = [p for p in parts if not re.match(r'^[\s]*(?:特惠快车|惊喜特价|快车|专车|豪华车|拼车|优享|宽敞好车|极速拼车|顺风车)[\s]*$', p)]
            if len(parts) >= 2:
                start = self._clean_location(parts[0])
                end = self._clean_location(parts[1])
                locations.append((start, end))
            else:
                locations.append((parts[0] if parts else '', ''))

        trips = []
        for i in range(len(dates)):
            month_day = dates[i]
            loc = locations[i] if i < len(locations) else ('', '')
            car_type = car_types[i] if i < len(car_types) else ''
            # 仅顺风车需要餐饮发票替票（拼车有发票）
            need_substitute = '顺风车' in car_type
            trips.append({
                'type': '打车行程单',
                'date': f'{year}-{month_day}',
                'start_location': loc[0],
                'end_location': loc[1],
                'amount': float(amounts[i]) if i < len(amounts) else 0,
                'car_type': car_type,
                'need_substitute': need_substitute,
                'hotel_name': '',
                'nights': '',
                'daily_rate': '',
                'has_invoice': False,
                'raw_text': f'行程{i+1}: {year}-{month_day} {loc[0]}-{loc[1]} ¥{amounts[i] if i < len(amounts) else "?"} [{car_type}]',
            })

        return trips if trips else None

    def _extract_car_types(self, trip_text: str) -> List[str]:
        """提取每条行程的车型"""
        # 车型可能在两行（如"特惠快\n车"），先合并
        merged = re.sub(r'([\u4e00-\u9fff])\n([\u4e00-\u9fff])', r'\1\2', trip_text)
        types = re.findall(
            r'(?:特惠快车|惊喜特价|极速拼车|顺风车|宽敞好车|豪华车|专车|快车|拼车|优享)',
            merged
        )
        return types

    def _clean_location(self, loc: str) -> str:
        """清理地点名称"""
        loc = loc.strip()
        # 如果包含"|"分隔符，取后半部分（去掉区域/街道前缀）
        if '|' in loc:
            loc = loc.split('|')[-1]
        # 去掉特定后缀（先处理复合后缀，再去单字后缀）
        loc = re.sub(r'-\d+层[\u4e00-\u9fff]*$', '', loc)
        loc = re.sub(r'-进站口$', '', loc)
        loc = re.sub(r'-地下停车场.*$', '', loc)
        loc = re.sub(r'-急诊$', '', loc)
        loc = re.sub(r'-西南\d+门$', '', loc)
        loc = re.sub(r'-东\d+门.*$', '', loc)
        loc = re.sub(r'-北门$', '', loc)
        loc = re.sub(r'[\(\uff08][^\)\uff09]*[\)\uff09]', '', loc)
        # 去掉 "-P2停车场..." 等
        loc = re.sub(r'-P\d+.*$', '', loc)
        # 去掉火车站后缀"站"（报销单中写"杭州西"而非"杭州西站"）
        if loc.endswith('站') and len(loc) >= 3:
            loc = loc[:-1]
        return loc.strip()

    def _regex_didi_invoice(self, text: str):
        """正则解析滴滴电子发票"""
        # 提取价税合计小写金额
        amt = re.search(r'[\(\uff08]\u5c0f\u5199[\)\uff09]\s*[¥￥]?\s*([\d,]+\.?\d*)', text)
        if not amt:
            amt = re.search(r'(?:价税合计|合\s*计)[\s\S]{0,30}[¥￥]\s*([\d,]+\.\d{2})', text)
        if not amt:
            return None

        amount = float(amt.group(1).replace(',', ''))

        # 提取开票日期
        dm = re.search(r'开票日期[：:]\s*(\d{4})\u5e74(\d{1,2})\u6708(\d{1,2})\u65e5', text)
        date_str = f'{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}' if dm else ''

        return {
            'type': '打车',
            'date': date_str,
            'start_location': '',
            'end_location': '',
            'amount': amount,
            'hotel_name': '',
            'nights': '',
            'daily_rate': '',
            'has_invoice': True,
            'raw_text': f'滴滴电子发票 ¥{amount}',
        }

    def _regex_train_ticket(self, text: str):
        """正则解析火车票"""
        # 优先提取乘车日期（带时间，如"2026年05月12日\n12:28开"）
        # 用 \D 替代可能乱码的中文字符（年/月/日）
        # [\s\S]*? 处理日期和时间之间可能有换行的情况
        dm = re.search(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D[\s\S]{0,30}?(\d{1,2}:\d{2})', text)
        travel_date = None
        if dm:
            travel_date = f'{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}'

        # 回退：找所有日期，排除“开票日期”后的那个
        all_dates = re.findall(r'(\d{4})\D(\d{1,2})\D(\d{1,2})\D', text)
        if not travel_date and all_dates:
            # 找第一个不是“开票日期”的日期
            for y, m, d in all_dates:
                date_str = f'{y}-{int(m):02d}-{int(d):02d}'
                # 检查这个日期前面是否有“开票”字样
                pattern_pos = text.find(f'{y}')
                if pattern_pos >= 0:
                    prefix = text[max(0, pattern_pos - 10):pattern_pos]
                    if '开票' not in prefix and '绁ㄦ' not in prefix:
                        travel_date = date_str
                        break
            if not travel_date:
                # 最后回退：取第一个日期
                y, m, d = all_dates[0]
                travel_date = f'{y}-{int(m):02d}-{int(d):02d}'

        date_str = travel_date or ''

        # 提取出发站和到达站
        # 用拼音站名确定方向（拼音总是按列车运行方向排列）
        # 匹配任何3+字母大写词（覆盖 Pujiang/Quzhou 等无后缀站名）
        PINYIN_MAP = {
            'Hangzhouxi': '杭州西', 'Hangzhoudong': '杭州东',
            'Hangzhounan': '杭州南', 'Hangzhoubei': '杭州北',
            'Hangzhouzhan': '杭州站', 'Hangzhou': '杭州',
            'Tongludong': '桐庐东', 'Tonglu': '桐庐',
            'Quzhouxi': '衢州西', 'Quzhoudong': '衢州东',
            'Quzhou': '衢州', 'Quzhouzhan': '衢州站',
            'Pujiang': '浦江', 'Pujiangzhan': '浦江站',
        }
        NOISE = {'Mai', 'Shi', 'Hangzhouxingchen', 'Xingchen', 'Shengwu',
                 'Jishu', 'Youxian', 'Gongsi', 'Zhejiang', 'Zhongguo',
                 'Tielu', 'Dianzi', 'Faipioa', 'Keji', 'Sheng', 'Shi'}
        pinyin = re.findall(r'([A-Z][a-z]{2,})', text)
        pinyin = [p for p in pinyin if p in PINYIN_MAP or p not in NOISE]

        start_loc = ''
        end_loc = ''
        if len(pinyin) >= 2:
            p1 = PINYIN_MAP.get(pinyin[0], '')
            p2 = PINYIN_MAP.get(pinyin[1], '')
            if p1 and p2:
                # 检查两个拼音之间是否有第三个站名（被省略的出发站）
                idx1 = text.find(pinyin[0])
                idx2 = text.find(pinyin[1])
                if idx1 >= 0 and idx2 > idx1:
                    between = text[idx1 + len(pinyin[0]):idx2]
                    # 找中文站名 + 站/东/西/南/北
                    hidden = re.findall(
                        r'([\u4e00-\u9fff]{2,6})(?:站|\u4e1c|\u897f|\u5357|\u5317)',
                        between,
                    )
                    if hidden:
                        # 隐藏站是实际出发站，第一个拼音是到达站
                        start_loc = hidden[0]
                        end_loc = p2
                    else:
                        start_loc = p1
                        end_loc = p2

        # 回退：只有1个拼音时，搜索已知中文站名
        if not start_loc or not end_loc:
            known_stations = [
                '杭州西站', '杭州东站', '杭州南站', '杭州北站', '杭州站',
                '桐庐东站', '桐庐站', '衢州西站', '衢州东站', '衢州站',
                '浦江站',
            ]
            found_stations = []
            for st in known_stations:
                if st in text:
                    found_stations.append(st)
            if pinyin and len(found_stations) >= 1:
                p_loc = PINYIN_MAP.get(pinyin[0], '')
                if p_loc:
                    # 判断拼音站和中文站的出发/到达关系
                    is_known = any(p_loc in st or st.startswith(p_loc) for st in found_stations)
                    if is_known:
                        # 拼音站就是中文站之一，找另一个
                        other = [st for st in found_stations if p_loc not in st and not st.startswith(p_loc)]
                        if other:
                            # 按文本位置排序确定方向
                            pos_p = text.find(pinyin[0])
                            pos_o = text.find(other[0])
                            if pos_p < pos_o:
                                start_loc = p_loc
                                end_loc = other[0].rstrip('站')
                            else:
                                start_loc = other[0].rstrip('站')
                                end_loc = p_loc
                    else:
                        start_loc = p_loc
                        end_loc = found_stations[0].rstrip('站')
            elif len(found_stations) >= 2:
                start_loc = found_stations[0].rstrip('站')
                end_loc = found_stations[1].rstrip('站')

        # 最后回退：纯中文站名
        if not start_loc or not end_loc:
            stations = re.findall(
                r'([\u4e00-\u9fff]{2,6})(?:站|\u4e1c|\u897f|\u5357|\u5317)', text
            )
            if len(stations) >= 2:
                start_loc = stations[0]
                end_loc = stations[1]

        # 提取金额
        amt = re.search(r'[¥￥]\s*([\d.]+)', text)
        if not amt:
            amt = re.search(r'(?:票价|金额)[：:]\s*([\d.]+)', text)
        amount = float(amt.group(1)) if amt else 0

        if not date_str and not start_loc:
            return None  # 解析失败

        return {
            'type': '火车票',
            'date': date_str,
            'start_location': start_loc,
            'end_location': end_loc,
            'amount': amount,
            'hotel_name': '',
            'nights': '',
            'daily_rate': '',
            'has_invoice': True,
            'raw_text': text[:200],
        }

    def _parse_with_text_model(self, page_text: str, invoice_parse_prompt: str = ""):
        """使用纯文本模型解析PDF提取的文字（更精确、不会遗漏）"""
        try:
            from dashscope import Generation
            import dashscope
            dashscope.api_key = self.api_key

            if invoice_parse_prompt:
                base_prompt = invoice_parse_prompt
            else:
                from config import load_prompt
                base_prompt = load_prompt("invoice_parse_text")

            user_prompt = (
                f"以下是从PDF中直接提取的原始文字内容，请根据这些文字解析票据信息：\n\n"
                f"---原始文字开始---\n{page_text}\n---原始文字结束---\n\n"
                f"{base_prompt}"
            )

            response = Generation.call(
                model="qwen-max",
                messages=[{"role": "user", "content": user_prompt}],
                result_format="message",
            )

            if response.status_code == 200:
                content = response.output.choices[0].message.content
                result = self._extract_json(content)
                return result
            else:
                print(f"文本模型API失败: {response.code} - {response.message}")
                return None
        except Exception as e:
            print(f"文本模型解析失败: {e}")
            return None

    def _parse_with_vision_model(self, image_path: str, system_prompt: str = "", invoice_parse_prompt: str = ""):
        """使用视觉模型解析图片（用于照片或无文字的PDF）"""
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
                # 提取JSON（可能是单个对象或数组）
                result = self._extract_json(content)
                if result is None:
                    return None
                # 统一转为列表
                if isinstance(result, list):
                    for r in result:
                        r["source_file"] = image_path
                    return result
                else:
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

        # 构建任务列表
        # 文本型PDF：合并所有页文字为一个任务（节省AI调用）
        # 图片/扫描件：每页一个任务
        tasks = []
        for file_info in file_list:
            images = file_info.get("images", [])
            page_texts = file_info.get("page_texts", [])
            all_text = "\n\n".join(pt for pt in page_texts if pt)

            if all_text and len(all_text) > 80:
                # 文本型PDF：合并所有页为一个任务
                tasks.append((images[0] if images else "", file_info["filename"], all_text))
            else:
                # 图片/扫描件：每页一个任务
                for img_path in images:
                    tasks.append((img_path, file_info["filename"], ""))

        results = []
        results_lock = threading.Lock()
        completed = [0]  # 用列表以便在闭包中修改
        total = len(tasks)

        def _parse_one(img_path, filename, page_text=""):
            """解析单张图片（线程安全），可能返回多条记录（打车行程单）"""
            result = self.parse_single_invoice(
                img_path, system_prompt, invoice_parse_prompt, page_text=page_text
            )
            # 跳过不需要解析的文件（如滴滴发票）
            if isinstance(result, dict) and result.get('type') == '_skip':
                result = None
            if result is not None:
                if isinstance(result, list):
                    for r in result:
                        r["source_file"] = filename
                else:
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
                executor.submit(_parse_one, img_path, fn, pt): (img_path, fn)
                for img_path, fn, pt in tasks
            }
            for future in as_completed(future_to_task):
                try:
                    result = future.result()
                    if result is not None:
                        if isinstance(result, list):
                            results.extend(result)
                        else:
                            results.append(result)
                except Exception as e:
                    _, fn = future_to_task[future]
                    print(f"解析失败: {fn}, 错误: {e}")

        # 按日期排序
        results.sort(key=lambda x: x.get("date", ""))
        return results

    def _extract_json(self, text: str):
        """从AI返回文本中提取JSON（支持单个对象或数组）"""
        # 尝试直接解析
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            pass

        # 尝试提取markdown代码块中的JSON
        import re

        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1).strip())
                if isinstance(parsed, (dict, list)):
                    return parsed
            except json.JSONDecodeError:
                pass

        # 尝试提取方括号内的JSON数组
        bracket_match = re.search(r"\[[\s\S]*\]", text)
        if bracket_match:
            try:
                parsed = json.loads(bracket_match.group(0))
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass

        # 尝试提取花括号内的单个对象
        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group(0))
                if isinstance(parsed, dict):
                    return parsed
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
