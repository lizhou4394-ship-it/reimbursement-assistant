"""数据关联智能体 - 跨文档交叉分析匹配"""
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta


class DataCorrelator:
    """
    数据关联智能体
    职责：
    1. 打车发票 + 行程单配对（按金额匹配）
    2. 酒店日期整理（只使用发票明示入住/离店日期；否则保留开票日期）
    3. 工作内容与各行程匹配
    """

    def __init__(self, api_key: str, model: str = "qwen-max"):
        self.api_key = api_key
        self.model = model

    def correlate_all(
        self,
        raw_invoices: List[Dict],
        work_description: str,
        hotel_infer_prompt: str = "",
        work_match_prompt: str = "",
    ) -> List[Dict]:
        """
        主流程：对所有原始票据进行关联分析

        Args:
            raw_invoices: 原始识别结果列表（每张票据独立）
            work_description: 当月工作内容描述

        Returns:
            关联整合后的费用记录列表
        """
        # 第一步：分类票据
        train_tickets = [inv for inv in raw_invoices if inv.get("type") == "火车票"]
        taxi_invoices = [inv for inv in raw_invoices if inv.get("type") == "打车"]
        taxi_itineraries = [inv for inv in raw_invoices if inv.get("type") == "打车行程单"]
        flight_tickets = [inv for inv in raw_invoices if inv.get("type") == "飞机票"]
        hotel_invoices = [inv for inv in raw_invoices if inv.get("type") == "酒店"]
        other_invoices = [inv for inv in raw_invoices if inv.get("type") not in (
            "火车票", "打车", "打车行程单", "飞机票", "酒店"
        )]
        other_invoices = self._dedupe_courier_summaries(other_invoices)

        # 第二步：打车发票 + 行程单配对
        taxi_records = self._match_taxi(taxi_invoices, taxi_itineraries)

        # 第三步：整理酒店日期；不从交通记录反推真实入住日期
        hotel_records = self._infer_hotel_dates(
            hotel_invoices, train_tickets, taxi_records, flight_tickets,
            hotel_infer_prompt=hotel_infer_prompt,
        )

        # 第四步：合并所有记录
        all_records = (
            train_tickets + taxi_records + flight_tickets +
            hotel_records + other_invoices
        )

        # 第五步：按日期排序
        all_records.sort(key=lambda x: x.get("date", ""))

        # 第六步：匹配工作内容
        if work_description:
            all_records = self._match_work_content(
                all_records, work_description,
                work_match_prompt=work_match_prompt,
            )

        return all_records

    def _match_taxi(
        self,
        taxi_invoices: List[Dict],
        taxi_itineraries: List[Dict],
    ) -> List[Dict]:
        """
        打车发票与行程单配对
        策略：按金额匹配，无法配对的行程单单独保留
        """
        matched = []
        used_itineraries = set()

        for invoice in taxi_invoices:
            inv_amount = float(invoice.get("amount", 0))
            candidates = []

            for i, itin in enumerate(taxi_itineraries):
                if i in used_itineraries:
                    continue
                itin_amount = float(itin.get("amount", 0))
                # 金额匹配（允许0.01误差）
                if abs(inv_amount - itin_amount) < 0.02:
                    date_gap = self._date_gap_days(invoice.get("date", ""), itin.get("date", ""))
                    candidates.append((date_gap, i))

            best_match = min(candidates)[1] if candidates else None

            if best_match is not None:
                itin = taxi_itineraries[best_match]
                used_itineraries.add(best_match)
                # 合并：日期/起点/终点取行程单，金额取发票
                merged = {
                    "type": "打车",
                    "date": itin.get("date", invoice.get("date", "")),
                    "start_location": itin.get("start_location", ""),
                    "end_location": itin.get("end_location", ""),
                    "amount": inv_amount,
                    "has_invoice": True,
                    "need_substitute": itin.get("need_substitute", False),
                    "car_type": itin.get("car_type", ""),
                    "source_file": invoice.get("source_file", ""),
                    "itinerary_file": itin.get("source_file", ""),
                    "source_file_rank": itin.get("source_file_rank", 0),
                    "itinerary_rank": itin.get("source_file_rank", 0),
                    "trip_index": itin.get("trip_index", 0),
                    "time": itin.get("time", ""),
                    "work_content": "",
                    "raw_text": (
                        f"发票: {invoice.get('raw_text', '')}\n"
                        f"行程单: {itin.get('raw_text', '')}"
                    ),
                }
                matched.append(merged)
            else:
                # 无法配对的发票，保留（只有金额）
                matched.append({
                    "type": "打车",
                    "date": invoice.get("date", ""),
                    "start_location": "",
                    "end_location": "",
                    "amount": inv_amount,
                    "has_invoice": True,
                    "need_substitute": False,
                    "car_type": "",
                    "source_file": invoice.get("source_file", ""),
                    "work_content": "",
                    "raw_text": invoice.get("raw_text", ""),
                })

        # 没有对应发票的行程单（无发票情况）
        for i, itin in enumerate(taxi_itineraries):
            if i not in used_itineraries:
                matched.append({
                    "type": "打车",
                    "date": itin.get("date", ""),
                    "start_location": itin.get("start_location", ""),
                    "end_location": itin.get("end_location", ""),
                    "amount": float(itin.get("amount", 0)),
                    "has_invoice": False,
                    "need_substitute": itin.get("need_substitute", False),
                    "car_type": itin.get("car_type", ""),
                    "source_file": itin.get("source_file", ""),
                    "itinerary_file": itin.get("source_file", ""),
                    "source_file_rank": itin.get("source_file_rank", 0),
                    "itinerary_rank": itin.get("source_file_rank", 0),
                    "trip_index": itin.get("trip_index", 0),
                    "time": itin.get("time", ""),
                    "work_content": "",
                    "raw_text": itin.get("raw_text", ""),
                })

        return matched

    @staticmethod
    def _date_gap_days(left: str, right: str) -> int:
        """返回两条记录日期差；无法解析时放到候选末尾。"""
        try:
            return abs((datetime.strptime(left, "%Y-%m-%d").date() - datetime.strptime(right, "%Y-%m-%d").date()).days)
        except (ValueError, TypeError):
            return 10 ** 6

    def _infer_hotel_dates(
        self,
        hotel_invoices: List[Dict],
        train_tickets: List[Dict],
        taxi_records: List[Dict],
        flight_tickets: List[Dict],
        hotel_infer_prompt: str = "",
    ) -> List[Dict]:
        """
        整理酒店日期。

        发票未明确写入住/离店日期时，报销单日期使用开票日期。
        只有发票文本中明确识别出入住/离店日期时，才填写入住时间段、天数和单价。
        """
        result = []

        for hotel in hotel_invoices:
            hotel_name = hotel.get("hotel_name", hotel.get("start_location", ""))
            invoice_date_str = hotel.get("date", "")
            amount = hotel.get("amount", 0)
            daily_rate = hotel.get("daily_rate", 0)
            nights = hotel.get("nights", 0)

            # 酒店真实入住/离店日期不能从交通票据可靠反推。
            # 仅使用发票文本中明确识别出的入住/离店日期；否则报销单日期使用开票日期。
            check_in_date = hotel.get("check_in_date", "")
            check_out_date = hotel.get("check_out_date", "")

            # 计算天数和单价
            if check_in_date and check_out_date:
                try:
                    d_in = datetime.strptime(check_in_date, "%Y-%m-%d").date()
                    d_out = datetime.strptime(check_out_date, "%Y-%m-%d").date()
                    nights = (d_out - d_in).days
                except (ValueError, TypeError):
                    pass

            if nights and amount:
                try:
                    daily_rate = round(float(amount) / int(nights), 2)
                except (ValueError, ZeroDivisionError):
                    pass

            result.append({
                "type": "酒店",
                "date": check_in_date or invoice_date_str,
                "hotel_name": hotel_name,
                "check_in_date": check_in_date,
                "check_out_date": check_out_date,
                "nights": nights,
                "daily_rate": daily_rate,
                "amount": float(amount),
                "start_location": hotel_name,
                "end_location": "",
                "has_invoice": hotel.get("has_invoice", True),
                "source_file": hotel.get("source_file", ""),
                "work_content": "",
                "raw_text": hotel.get("raw_text", ""),
            })

        return result

    @staticmethod
    def _dedupe_courier_summaries(other_invoices: List[Dict]) -> List[Dict]:
        """如果同时上传了顺丰发票和运单明细，只保留逐条运单明细。"""
        detail_totals: Dict[str, float] = {}
        for inv in other_invoices:
            if inv.get("type") == "快递" and inv.get("is_waybill_detail"):
                key = inv.get("invoice_number", "") or "_unknown"
                detail_totals[key] = detail_totals.get(key, 0.0) + float(inv.get("amount", 0) or 0)

        if not detail_totals:
            return other_invoices

        filtered = []
        for inv in other_invoices:
            if inv.get("type") == "快递" and not inv.get("is_waybill_detail"):
                key = inv.get("invoice_number", "") or "_unknown"
                detail_total = detail_totals.get(key)
                if detail_total is None:
                    detail_total = next(
                        (
                            total for total in detail_totals.values()
                            if abs(total - float(inv.get("amount", 0) or 0)) < 0.02
                        ),
                        None,
                    )
                if detail_total is not None and abs(detail_total - float(inv.get("amount", 0) or 0)) < 0.02:
                    continue
            filtered.append(inv)
        return filtered

    def _match_work_content(
        self,
        records: List[Dict],
        work_description: str,
        work_match_prompt: str = "",
        batch_size: int = 20,
    ) -> List[Dict]:
        """为每条记录匹配工作内容（规则优先，AI作为增强）"""
        # 先初始化所有记录的work_content为空
        for r in records:
            r["work_content"] = ""

        if not work_description:
            return records

        # 第1优先：规则匹配（零AI调用，始终可用）
        self._match_work_content_rules(records, work_description)

        # 第2优先：AI增强（如果有有效API Key）
        try:
            self._match_work_content_ai(records, work_description, work_match_prompt, batch_size)
        except Exception:
            pass  # AI失败不影响，规则匹配已完成

        return records

    def _match_work_content_rules(
        self, records: List[Dict], work_description: str
    ):
        """基于规则的工作内容匹配（无需AI）"""
        import re

        # 解析工作内容描述，提取 城市→工作内容 映射
        # 支持分隔符：、，, \n
        items = re.split(r'[、，,\n]+', work_description)

        # 从票据记录中动态提取所有出现的城市名（不用硬编码）
        all_cities = set()
        for r in records:
            for loc_key in ('start_location', 'end_location', 'hotel_name'):
                loc = r.get(loc_key, '')
                if loc:
                    # 从站点名中提取城市（去掉站名后缀）
                    city = self._extract_city_from_text(loc)
                    if city and len(city) >= 2:
                        all_cities.add(city)

        # 解析工作描述，将每条内容与匹配的城市关联
        city_contents: Dict[str, List[str]] = {}
        for item in items:
            item = item.strip()
            if not item:
                continue
            # 在工作描述中查找票据中出现的城市名
            matched = False
            for city in sorted(all_cities, key=len, reverse=True):  # 优先匹配长城市名
                if city in item:
                    city_contents.setdefault(city, []).append(item)
                    matched = True
                    break
            if not matched:
                # 尝试从工作描述中提取“城市:”或“城市-”格式
                prefix_match = re.match(r'^([\u4e00-\u9fff]{2,4})[:：\-—]', item)
                if prefix_match:
                    city = prefix_match.group(1)
                    city_contents.setdefault(city, []).append(item)
                else:
                    city_contents.setdefault('_other', []).append(item)

        if not city_contents:
            return

        # 按日期排序记录（便于分配同一城市的多个工作内容）
        dated_records = [(r, r.get('date', '')) for r in records if r.get('date')]
        dated_records.sort(key=lambda x: x[1])

        # 为每条记录匹配工作内容
        city_used: Dict[str, int] = {}  # 记录每个城市已使用到第几个工作内容
        day_content_cache: Dict[str, str] = {}  # 同一天同一城市用同一个工作内容

        for r, date in dated_records:
            # 确定记录属于哪个城市
            start = r.get('start_location', '')
            end = r.get('end_location', '')
            hotel = r.get('hotel_name', '')
            all_text = f"{start} {end} {hotel}"

            matched_city = None
            for city in city_contents:
                if city == '_other':
                    continue
                if city in all_text:
                    matched_city = city
                    break

            if matched_city and matched_city in city_contents:
                contents = city_contents[matched_city]
                # 同一天的多条记录用同一个工作内容
                day_key = f"{matched_city}_{date}"
                if day_key in day_content_cache:
                    r['work_content'] = day_content_cache[day_key]
                else:
                    # 分配下一个未使用的工作内容
                    used_idx = city_used.get(matched_city, 0)
                    if used_idx < len(contents):
                        r['work_content'] = contents[used_idx]
                        city_used[matched_city] = used_idx + 1
                    elif contents:
                        # 所有内容已分配完，用最后一个
                        r['work_content'] = contents[-1]
                    day_content_cache[day_key] = r.get('work_content', '')
            elif '_other' in city_contents:
                r['work_content'] = city_contents['_other'][0]

    @staticmethod
    def _extract_city_from_text(text: str) -> str:
        """
        从地点文本中提取城市名（通用，无硬编码列表）
        支持多种格式：
        - 火车站: "杭州西站" -> "杭州", "桐庐东" -> "桐庐"
        - 打车地点: "温州北站-网约车上车点" -> "温州"
        - 酒店名: "全季酒店(温州车站大道店)" -> "温州"
        """
        import re as _re
        if not text:
            return ''
        hotel_prefix = _re.match(r'([\u4e00-\u9fff]{2})(?=[\u4e00-\u9fff]{0,8}(?:酒店|宾馆|旅馆|民宿|大厦))', text)
        if hotel_prefix:
            return hotel_prefix.group(1)
        # 去掉站名后缀提取城市
        for suffix in ('西站', '东站', '南站', '北站', '站', '东', '西', '南', '北'):
            if text.endswith(suffix) and len(text) > len(suffix):
                return text[:-len(suffix)]
        # 匹配“XX市”或“XX区”格式
        m = _re.search(r'([\u4e00-\u9fff]{2,4})(?:市|区|县)', text)
        if m:
            return m.group(1)
        # 从括号中提取城市（如“全季酒店(温州车站大道店)”）
        m = _re.search(r'[\(\uff08]([^\)\uff09]*?)[\)\uff09]', text)
        if m:
            inner = m.group(1)
            # 提取括号内的前2-4个中文字符作为城市名
            cm = _re.match(r'([\u4e00-\u9fff]{2,4})', inner)
            if cm:
                return cm.group(1)
        # 直接返回前2-4个中文字符
        m = _re.match(r'([\u4e00-\u9fff]{2,4})', text)
        if m:
            return m.group(1)
        return text

    def _match_work_content_ai(
        self,
        records: List[Dict],
        work_description: str,
        work_match_prompt: str = "",
        batch_size: int = 20,
    ):
        """AI增强工作内容匹配（可选，需要有效API Key）"""
        # 分批处理
        for batch_start in range(0, len(records), batch_size):
            batch = records[batch_start:batch_start + batch_size]
            try:
                self._match_work_content_batch(
                    batch, batch_start, work_description, work_match_prompt
                )
            except Exception as e:
                print(f"AI工作内容匹配失败（批次 {batch_start}-{batch_start + len(batch)}）: {e}")

    def _match_work_content_batch(
        self,
        batch: List[Dict],
        batch_start: int,
        work_description: str,
        work_match_prompt: str = "",
    ):
        """处理一批记录的工作内容匹配"""
        import dashscope
        from dashscope import Generation

        dashscope.api_key = self.api_key

        record_summary = []
        for i, r in enumerate(batch):
            summary = (
                f"{i + 1}. 日期:{r.get('date', '')} "
                f"类型:{r.get('type', '')} "
                f"地点:{r.get('start_location', '')}-{r.get('end_location', '')} "
                f"酒店:{r.get('hotel_name', '')}"
            )
            record_summary.append(summary)

        if work_match_prompt:
            prompt = work_match_prompt.format(
                work_description=work_description,
                record_summary=chr(10).join(record_summary),
            )
        else:
            from config import load_prompt
            template = load_prompt("work_match")
            prompt = template.format(
                work_description=work_description,
                record_summary=chr(10).join(record_summary),
            )

        response = Generation.call(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            result_format="message",
        )

        if response.status_code == 200:
            content = response.output.choices[0].message.content
            matches = self._extract_json(content)
            if isinstance(matches, list):
                for m in matches:
                    # index是批次内的序号（从1开始），转为全局索引
                    local_idx = m.get("index", 0) - 1
                    if 0 <= local_idx < len(batch):
                        batch[local_idx]["work_content"] = m.get("work_content", "")
        else:
            print(f"AI工作内容匹配API失败: {response.code} - {response.message}")

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从文本中提取JSON"""
        import re

        # 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 代码块
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 花括号/方括号
        match = re.search(r"[\[{][\s\S]*[\]}]", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def check_train_roundtrip(
        invoices: List[Dict], home_city: str = "杭州"
    ) -> Dict:
        """
        检查火车票/飞机票的往返配对情况

        Returns:
            {
                'issues': [{'city': str, 'problem': str, 'detail': str}],
                'has_bus': bool,
                'summary': str
            }
        """
        from services.excel_generator import ExcelGenerator

        issues = []
        departures = {}  # {城市: [日期]}
        returns = {}     # {城市: [日期]}
        has_bus = False

        for inv in invoices:
            inv_type = inv.get('type', '')
            date_str = inv.get('date', '')
            start = inv.get('start_location', '')
            end = inv.get('end_location', '')

            # 检测大巴票
            if inv_type in ('大巴', '客车', '长途汽车') or '大巴' in inv.get('raw_text', ''):
                has_bus = True

            if inv_type not in ('火车票', '飞机票'):
                continue

            start_city = ExcelGenerator._extract_city(start)
            end_city = ExcelGenerator._extract_city(end)

            if home_city in start and home_city not in end:
                departures.setdefault(end_city, []).append(date_str)
            elif home_city in end and home_city not in start:
                returns.setdefault(start_city, []).append(date_str)
            elif home_city not in start and home_city not in end:
                if start_city and end_city and start_city != end_city:
                    departures.setdefault(start_city, []).append(date_str)

        # 检查有出发无返回的城市
        all_cities = set(list(departures.keys()) + list(returns.keys()))
        for city in sorted(all_cities):
            deps = sorted(departures.get(city, []))
            rets = sorted(returns.get(city, []))

            if deps and not rets:
                dep_str = '、'.join(deps)
                issues.append({
                    'city': city,
                    'problem': 'missing_return',
                    'detail': f'{city}: 有去程({dep_str})但无返程票',
                })
            elif rets and not deps:
                ret_str = '、'.join(rets)
                issues.append({
                    'city': city,
                    'problem': 'missing_departure',
                    'detail': f'{city}: 有返程({ret_str})但无去程票',
                })
            elif len(deps) != len(rets):
                dep_str = '、'.join(deps)
                ret_str = '、'.join(rets)
                issues.append({
                    'city': city,
                    'problem': 'count_mismatch',
                    'detail': f'{city}: 去程{len(deps)}张({dep_str})，返程{len(rets)}张({ret_str})',
                })

        # 构建汇总文本
        if not issues:
            summary = ''
        else:
            problem_lines = [f'  - {i["detail"]}' for i in issues]
            summary = f'发现 {len(issues)} 个城市的往返票可能不完整：\n' + '\n'.join(problem_lines)
            if has_bus:
                summary += '\n（已检测到其他交通方式票据，可能已补充）'
            else:
                missing_returns = [i['city'] for i in issues if i['problem'] == 'missing_return']
                missing_departs = [i['city'] for i in issues if i['problem'] == 'missing_departure']
                hints = []
                if missing_returns:
                    hints.append(f'缺少返程票: {"、".join(missing_returns)}')
                if missing_departs:
                    hints.append(f'缺少去程票: {"、".join(missing_departs)}')
                if hints:
                    summary += '\n可能缺少：' + '; '.join(hints)
                    summary += '\n如确认缺少的是大巴票等其他票据，请补充上传或忽略此提醒。'

        return {
            'issues': issues,
            'has_bus': has_bus,
            'summary': summary,
        }
