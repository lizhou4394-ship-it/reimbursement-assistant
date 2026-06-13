"""数据关联智能体 - 跨文档交叉分析匹配"""
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta


class DataCorrelator:
    """
    数据关联智能体
    职责：
    1. 打车发票 + 行程单配对（按金额匹配）
    2. 酒店入住/离店日期推算（根据动车票+打车目的地）
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

        # 第二步：打车发票 + 行程单配对
        taxi_records = self._match_taxi(taxi_invoices, taxi_itineraries)

        # 第三步：推算酒店入住/离店日期
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
            best_match = None

            for i, itin in enumerate(taxi_itineraries):
                if i in used_itineraries:
                    continue
                itin_amount = float(itin.get("amount", 0))
                # 金额匹配（允许0.01误差）
                if abs(inv_amount - itin_amount) < 0.02:
                    best_match = i
                    break

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
                    "work_content": "",
                    "raw_text": itin.get("raw_text", ""),
                })

        return matched

    def _infer_hotel_dates(
        self,
        hotel_invoices: List[Dict],
        train_tickets: List[Dict],
        taxi_records: List[Dict],
        flight_tickets: List[Dict],
        hotel_infer_prompt: str = "",
    ) -> List[Dict]:
        """
        根据动车票/飞机票+打车记录，推算酒店入住/离店日期

        逻辑：
        - 找到离开杭州的交通票据（出发日期 = 入住日期附近）
        - 找到返回杭州的交通票据（返回日期 = 离店日期附近）
        - 打车目的地中出现酒店名称的，进一步确认入住日期
        """
        result = []

        # 收集所有出行日期段
        trips = self._build_trip_segments(train_tickets, flight_tickets)

        for hotel in hotel_invoices:
            hotel_name = hotel.get("hotel_name", hotel.get("start_location", ""))
            invoice_date_str = hotel.get("date", "")
            amount = hotel.get("amount", 0)
            daily_rate = hotel.get("daily_rate", 0)
            nights = hotel.get("nights", 0)

            check_in_date = ""
            check_out_date = ""

            # 方法1：如果有入住天数，从发票日期反推
            if nights and invoice_date_str:
                try:
                    # 酒店发票日期通常是离店日期或开票日期
                    # 尝试以发票日期为离店日期
                    inv_date = datetime.strptime(invoice_date_str, "%Y-%m-%d").date()
                    check_out = inv_date
                    check_in = check_out - timedelta(days=int(nights))
                    check_in_date = check_in.strftime("%Y-%m-%d")
                    check_out_date = check_out.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    pass

            # 方法2：用交通票据交叉验证/修正
            if not check_in_date or not check_out_date:
                best_trip = self._find_matching_trip(hotel_name, hotel, trips, taxi_records)
                if best_trip:
                    check_in_date = best_trip.get("depart_date", check_in_date)
                    check_out_date = best_trip.get("return_date", check_out_date)

            # 方法3：调用AI进行更精确的推断
            if not check_in_date and trips:
                ai_result = self._ai_infer_hotel_dates(
                    hotel, trips, taxi_records, hotel_infer_prompt
                )
                if ai_result:
                    check_in_date = ai_result.get("check_in_date", check_in_date)
                    check_out_date = ai_result.get("check_out_date", check_out_date)
                    if ai_result.get("nights"):
                        nights = ai_result["nights"]

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

    def _build_trip_segments(
        self,
        train_tickets: List[Dict],
        flight_tickets: List[Dict],
    ) -> List[Dict]:
        """构建出行段列表（离开杭州 → 返回杭州）"""
        trips = []
        home = "杭州"

        all_transport = train_tickets + flight_tickets
        all_transport.sort(key=lambda x: x.get("date", ""))

        departs = []
        returns = []

        for t in all_transport:
            start = t.get("start_location", "")
            end = t.get("end_location", "")
            date = t.get("date", "")

            if home in start:
                departs.append({
                    "destination": end,
                    "depart_date": date,
                    "transport_type": t.get("type"),
                })
            elif home in end:
                returns.append({
                    "origin": start,
                    "return_date": date,
                    "transport_type": t.get("type"),
                })

        # 配对出发和返回
        for dep in departs:
            dest = dep["destination"]
            dep_date = dep["depart_date"]
            matching_ret = None

            for ret in returns:
                if dest in ret.get("origin", "") or ret["origin"] in dest:
                    if ret["return_date"] >= dep_date:
                        matching_ret = ret
                        break

            trips.append({
                "destination": dest,
                "depart_date": dep_date,
                "return_date": matching_ret["return_date"] if matching_ret else "",
            })

        return trips

    def _find_matching_trip(
        self,
        hotel_name: str,
        hotel: Dict,
        trips: List[Dict],
        taxi_records: List[Dict],
    ) -> Optional[Dict]:
        """根据酒店名称在打车记录中找目的地，再匹配出行段"""
        # 在打车记录中找包含酒店名的记录
        hotel_taxi_dates = []
        for taxi in taxi_records:
            end_loc = taxi.get("end_location", "")
            start_loc = taxi.get("start_location", "")
            if hotel_name and (hotel_name in end_loc or hotel_name in start_loc):
                hotel_taxi_dates.append(taxi.get("date", ""))

        if not hotel_taxi_dates:
            return None

        # 取最早的打车日期作为入住日期参考
        hotel_taxi_dates.sort()
        earliest_taxi_date = hotel_taxi_dates[0]

        # 在出行段中找最匹配的
        for trip in trips:
            dep_date = trip.get("depart_date", "")
            ret_date = trip.get("return_date", "")
            # 打车日期在出行日期范围内
            if dep_date <= earliest_taxi_date:
                if not ret_date or earliest_taxi_date <= ret_date:
                    return trip

        return None

    def _ai_infer_hotel_dates(
        self,
        hotel: Dict,
        trips: List[Dict],
        taxi_records: List[Dict],
        hotel_infer_prompt: str = "",
    ) -> Optional[Dict]:
        """调用AI推断酒店入住/离店日期"""
        try:
            import dashscope
            from dashscope import Generation

            dashscope.api_key = self.api_key

            hotel_info = (
                f"酒店名称: {hotel.get('hotel_name', hotel.get('start_location', ''))}\n"
                f"发票金额: {hotel.get('amount', '')}\n"
                f"发票日期: {hotel.get('date', '')}\n"
                f"识别文本: {hotel.get('raw_text', '')}"
            )

            trips_info = "\n".join([
                f"出行: {t.get('destination', '')} "
                f"出发:{t.get('depart_date', '')} 返回:{t.get('return_date', '')}"
                for t in trips
            ])

            taxi_info = "\n".join([
                f"打车: {t.get('date', '')} "
                f"{t.get('start_location', '')} → {t.get('end_location', '')}"
                for t in taxi_records
            ])

            if hotel_infer_prompt:
                prompt = hotel_infer_prompt.format(
                    hotel_info=hotel_info,
                    trips_info=trips_info,
                    taxi_info=taxi_info,
                )
            else:
                from config import load_prompt
                template = load_prompt("hotel_infer")
                prompt = template.format(
                    hotel_info=hotel_info,
                    trips_info=trips_info,
                    taxi_info=taxi_info,
                )

            response = Generation.call(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                result_format="message",
            )

            if response.status_code == 200:
                content = response.output.choices[0].message.content
                result = self._extract_json(content)
                return result

        except Exception as e:
            print(f"AI推断酒店日期失败: {e}")

        return None

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
        city_contents: Dict[str, List[str]] = {}  # {城市简称: [工作内容列表]}

        for item in items:
            item = item.strip()
            if not item:
                continue
            # 查找已知城市关键词
            known_cities = ['衢州', '桐庐', '浦江', '临安', '建德', '淳安']
            for city in known_cities:
                if city in item:
                    city_contents.setdefault(city, []).append(item)
                    break
            else:
                # 未知城市，保存为通用
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
