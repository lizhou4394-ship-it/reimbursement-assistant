"""Excel报销单生成服务"""
import io
from copy import copy
from typing import List, Dict, Tuple
from datetime import datetime
from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill


class ExcelGenerator:
    """报销单Excel生成器"""

    def __init__(self, template_bytes: bytes):
        """
        初始化生成器

        Args:
            template_bytes: Excel模板文件的二进制内容
        """
        self.wb = load_workbook(io.BytesIO(template_bytes))
        self.ws = self.wb.active

    def generate(
        self,
        invoices: List[Dict],
        travel_days: Dict,
        work_description: str,
    ) -> bytes:
        """
        根据解析结果生成报销单

        Args:
            invoices: 发票解析结果列表（已匹配工作内容）
            travel_days: 出差天数统计 {地区: 天数, ...}
            work_description: 工作内容描述

        Returns:
            生成的Excel文件二进制内容
        """
        # 分类发票数据
        transport_data = []  # 城际交通（火车票、打车、飞机票）
        subsidy_data = []    # 补贴
        hotel_data = []      # 住宿费
        other_data = []      # 其他费用

        for inv in invoices:
            inv_type = inv.get("type", "")
            if inv_type in ("火车票", "打车", "飞机票"):
                transport_data.append(inv)
            elif inv_type == "酒店":
                hotel_data.append(inv)
            elif inv_type in ("快递", "其他"):
                other_data.append(inv)

        # 生成补贴数据
        subsidy_data = self._generate_subsidies(travel_days)

        # 查找各板块起始行
        section_rows = self._find_section_rows()

        # 按倒序写入数据（避免行号偏移问题）
        # 先处理后面的板块
        if "其他费用" in section_rows:
            self._write_other_section(
                section_rows["其他费用"], other_data
            )

        if "住宿费" in section_rows:
            self._write_hotel_section(section_rows["住宿费"], hotel_data)

        if "补贴" in section_rows:
            self._write_subsidy_section(section_rows["补贴"], subsidy_data)

        if "城际交通" in section_rows:
            self._write_transport_section(
                section_rows["城际交通"], transport_data
            )

        # 写入出差天数统计
        self._write_travel_days_summary(travel_days)

        # 输出到bytes
        output = io.BytesIO()
        self.wb.save(output)
        return output.getvalue()

    def _find_section_rows(self) -> Dict[str, int]:
        """查找各板块标题所在行号"""
        section_rows = {}
        for row in self.ws.iter_rows(min_row=1, max_row=self.ws.max_row):
            for cell in row:
                if cell.value:
                    cell_text = str(cell.value).strip()
                    if "城际交通" in cell_text or "交通" in cell_text:
                        section_rows["城际交通"] = cell.row
                    elif "补贴" in cell_text and "出差补贴" not in cell_text:
                        section_rows["补贴"] = cell.row
                    elif "住宿" in cell_text:
                        section_rows["住宿费"] = cell.row
                    elif "其他费用" in cell_text or "其他" in cell_text:
                        if "其他费用" not in section_rows:
                            section_rows["其他费用"] = cell.row
        return section_rows

    def _find_data_start_row(self, section_row: int) -> int:
        """查找板块数据起始行（标题行的下一行）"""
        # 跳过标题行，找到第一个空行或数据行
        for row in range(section_row + 1, section_row + 5):
            cell_val = self.ws.cell(row=row, column=1).value
            if cell_val is None or str(cell_val).strip() == "":
                return row
        return section_row + 1

    def _write_transport_section(
        self, section_row: int, data: List[Dict]
    ):
        """写入城际交通板块数据"""
        # 假设列结构：A=日期, B=起点, C=终点, D=工作内容和目的, E=费用名称, F=金额, G=备注
        start_row = self._find_data_start_row(section_row)

        for i, inv in enumerate(data):
            row = start_row + i
            date_str = inv.get("date", "")
            try:
                date_val = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                date_val = date_str

            self.ws.cell(row=row, column=1, value=date_val)
            self.ws.cell(row=row, column=2, value=inv.get("start_location", ""))
            self.ws.cell(row=row, column=3, value=inv.get("end_location", ""))
            self.ws.cell(row=row, column=4, value=inv.get("work_content", ""))
            self.ws.cell(row=row, column=5, value=inv.get("type", ""))
            amount = inv.get("amount", 0)
            self.ws.cell(row=row, column=6, value=float(amount))
            self.ws.cell(row=row, column=7, value="")  # 备注留空

    def _write_subsidy_section(
        self, section_row: int, data: List[Dict]
    ):
        """写入补贴板块数据"""
        start_row = self._find_data_start_row(section_row)

        for i, item in enumerate(data):
            row = start_row + i
            self.ws.cell(row=row, column=4, value=item.get("work_content", ""))
            self.ws.cell(row=row, column=5, value=item.get("type", ""))
            self.ws.cell(row=row, column=6, value=item.get("amount", 0))
            self.ws.cell(row=row, column=7, value="餐费代替")

    def _write_hotel_section(
        self, section_row: int, data: List[Dict]
    ):
        """写入住宿费板块数据"""
        start_row = self._find_data_start_row(section_row)

        for i, inv in enumerate(data):
            row = start_row + i
            check_in = inv.get("check_in_date", "")
            check_out = inv.get("check_out_date", "")
            time_range = f"{check_in}至{check_out}" if check_in and check_out else inv.get("date", "")

            self.ws.cell(row=row, column=1, value=time_range)
            self.ws.cell(row=row, column=2, value=inv.get("hotel_name", inv.get("start_location", "")))
            nights = inv.get("nights", "")
            self.ws.cell(row=row, column=3, value=nights if nights else "")
            daily_rate = inv.get("daily_rate", "")
            self.ws.cell(row=row, column=4, value=float(daily_rate) if daily_rate else "")
            self.ws.cell(row=row, column=5, value="酒店")
            amount = inv.get("amount", 0)
            self.ws.cell(row=row, column=6, value=float(amount))
            self.ws.cell(row=row, column=7, value="")  # 备注留空

    def _write_other_section(
        self, section_row: int, data: List[Dict]
    ):
        """写入其他费用板块数据"""
        start_row = self._find_data_start_row(section_row)

        for i, inv in enumerate(data):
            row = start_row + i
            date_str = inv.get("date", "")
            try:
                date_val = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                date_val = date_str

            self.ws.cell(row=row, column=1, value=date_val)
            self.ws.cell(row=row, column=4, value=inv.get("work_content", ""))
            self.ws.cell(row=row, column=5, value=inv.get("type", ""))
            amount = inv.get("amount", 0)
            self.ws.cell(row=row, column=6, value=float(amount))

            # 无发票的备注写"餐费代替"
            has_invoice = inv.get("has_invoice", True)
            if not has_invoice:
                self.ws.cell(row=row, column=7, value="餐费代替")
            else:
                self.ws.cell(row=row, column=7, value="")

    def _generate_subsidies(self, travel_days: Dict) -> List[Dict]:
        """生成补贴数据（出差补贴、通讯补贴、餐补）"""
        subsidies = []

        # 计算出差总天数
        total_days = sum(travel_days.values()) if travel_days else 0

        # 出差补贴：50元/天
        if total_days > 0:
            subsidies.append({
                "type": "出差补贴",
                "amount": total_days * 50,
                "work_content": "",
                "remark": "餐费代替",
            })

        # 通讯补贴：固定200元
        subsidies.append({
            "type": "通讯补贴",
            "amount": 200,
            "work_content": "",
            "remark": "餐费代替",
        })

        # 餐补：固定500元
        subsidies.append({
            "type": "餐补",
            "amount": 500,
            "work_content": "",
            "remark": "餐费代替",
        })

        return subsidies

    def _write_travel_days_summary(self, travel_days: Dict):
        """写入出差天数统计文字"""
        if not travel_days:
            return

        # 构建统计文字：XX（X天），XX（X天），共X天
        parts = []
        total = 0
        for city, days in travel_days.items():
            parts.append(f"{city}（{days}天）")
            total += days
        summary = "，".join(parts) + f"，共{total}天"

        # 在表格中寻找合适位置写入出差天数统计
        for row in self.ws.iter_rows(min_row=1, max_row=self.ws.max_row):
            for cell in row:
                if cell.value and "出差天数" in str(cell.value):
                    # 在下一个单元格写入统计
                    next_cell = self.ws.cell(
                        row=cell.row, column=cell.column + 1
                    )
                    next_cell.value = summary
                    return

    def calculate_travel_days(
        self, invoices: List[Dict], home_city: str = "杭州"
    ) -> Dict:
        """
        计算出差天数

        Args:
            invoices: 发票列表
            home_city: 常驻城市

        Returns:
            {地区: 天数} 字典
        """
        travel_days = {}
        trips = []

        # 提取所有出行相关发票
        for inv in invoices:
            inv_type = inv.get("type", "")
            date_str = inv.get("date", "")
            if not date_str:
                continue

            if inv_type in ("火车票", "飞机票"):
                start = inv.get("start_location", "")
                end = inv.get("end_location", "")

                # 判断是否离开常驻城市
                if home_city in start:
                    # 出发行程
                    destination = end
                    trips.append({
                        "destination": destination,
                        "depart_date": date_str,
                        "type": "depart",
                    })
                elif home_city in end:
                    # 返回行程
                    trips.append({
                        "destination": start,
                        "return_date": date_str,
                        "type": "return",
                    })

        # 配对出发和返回，计算天数
        depart_trips = [t for t in trips if t["type"] == "depart"]
        return_trips = [t for t in trips if t["type"] == "return"]

        for dep in depart_trips:
            dest = dep["destination"]
            dep_date = datetime.strptime(dep["depart_date"], "%Y-%m-%d").date()

            # 寻找对应的返回行程
            matching_return = None
            for ret in return_trips:
                if dest in ret.get("destination", ""):
                    ret_date = datetime.strptime(
                        ret["return_date"], "%Y-%m-%d"
                    ).date()
                    if ret_date >= dep_date:
                        matching_return = ret
                        break

            if matching_return:
                ret_date = datetime.strptime(
                    matching_return["return_date"], "%Y-%m-%d"
                ).date()
                days = (ret_date - dep_date).days + 1
            else:
                # 没有返回行程，计为1天（单日往返）
                days = 1

            if dest in travel_days:
                travel_days[dest] += days
            else:
                travel_days[dest] = days

        # 酒店住宿也参与天数计算（优先使用酒店日期来补充交通票据未覆盖的行程）
        for inv in invoices:
            if inv.get("type") == "酒店":
                check_in = inv.get("check_in_date", "")
                check_out = inv.get("check_out_date", "")
                if check_in and check_out:
                    try:
                        d_in = datetime.strptime(check_in, "%Y-%m-%d").date()
                        d_out = datetime.strptime(check_out, "%Y-%m-%d").date()
                        nights = (d_out - d_in).days
                        # 查找酒店对应的城市（从打车记录或火车票目的地推断）
                        hotel_city = self._infer_hotel_city(inv, invoices)
                        if hotel_city and hotel_city not in travel_days:
                            travel_days[hotel_city] = nights
                        elif hotel_city and hotel_city in travel_days:
                            # 如果火车票已经计算了天数，取较大值
                            travel_days[hotel_city] = max(
                                travel_days[hotel_city], nights
                            )
                    except ValueError:
                        pass

        return travel_days

    def _infer_hotel_city(self, hotel: Dict, all_invoices: List[Dict]) -> str:
        """根据酒店名称和打车记录推断所在城市"""
        hotel_name = hotel.get("hotel_name", hotel.get("start_location", ""))
        check_in = hotel.get("check_in_date", "")

        if not hotel_name or not check_in:
            return ""

        # 在打车记录中找目的地包含酒店名的记录
        for inv in all_invoices:
            if inv.get("type") == "打车":
                end_loc = inv.get("end_location", "")
                if hotel_name and hotel_name in end_loc:
                    # 从打车记录推断不出城市，尝试火车票
                    pass

        # 从火车票目的地中找时间最接近的
        best_city = ""
        for inv in all_invoices:
            if inv.get("type") in ("火车票", "飞机票"):
                start = inv.get("start_location", "")
                end = inv.get("end_location", "")
                if "杭州" in start:
                    best_city = end
                    break

        return best_city
