"""滴滴文字行程单：按行读取，不能确认完整性时交给后备识别。"""
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation


def normalize(text):
    return unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")


def is_didi(text):
    compact = re.sub(r"\s+", "", normalize(text))
    return "滴滴出行" in compact and "行程单" in compact


def itinerary_period(text):
    compact = re.sub(r"\s+", "", normalize(text))
    period = re.search(
        r"行程起止日期:(\d{4}-\d{1,2}-\d{1,2})(?:至|到|~|-)(\d{4}-\d{1,2}-\d{1,2})",
        compact,
    )
    if not period:
        return None
    try:
        start, end = [date(*map(int, value.split("-"))) for value in period.groups()]
    except ValueError:
        return None
    return (start, end) if start <= end and end.year - start.year <= 1 else None


def summary_matches(text, records):
    """同时约束规则和 AI 返回的笔数、金额；拒绝空结果及非数字金额。"""
    if not isinstance(records, list) or not records:
        return False
    compact = re.sub(r"\s+", "", normalize(text))
    count = re.search(r"共(\d+)笔行程", compact)
    total = re.search(r"合计([\d,]+(?:\.\d{1,2})?)元", compact)
    if count and len(records) != int(count[1]):
        return False
    try:
        amounts = [Decimal(str(item["amount"])) for item in records]
        if any(not value.is_finite() or value <= 0 for value in amounts):
            return False
        if total and sum(amounts) != Decimal(total[1].replace(",", "")):
            return False
        period = itinerary_period(text)
        if period and any(not period[0] <= date.fromisoformat(item["date"]) <= period[1] for item in records):
            return False
    except (KeyError, TypeError, InvalidOperation, ValueError):
        return False
    return True


def parse_itinerary(text, parser):
    text = normalize(text)
    # 行程日期优先于申请日期。没有行程范围时不猜年份。
    period = itinerary_period(text)
    if not period:
        return None
    start, end = period

    # 序号和车型固定在本行日期之前；未知车型也不会导致后续错位。
    row_pattern = re.compile(
        r"(?m)^\s*(\d+)\s*\n([\u4e00-\u9fff \t\n]+?)\s*"
        r"(\d{1,2})\s*-\s*(\d{1,2})\s+"
        r"(\d{1,2})\s*:\s*(\d{2})(?!\d)"
        r"(?:\s*(?:周|星期)\s*[一二三四五六日天])?"
    )
    rows = list(row_pattern.finditer(text))
    if not rows:
        return None
    records = []
    for i, row in enumerate(rows):
        index, car, month, day, hour, minute = row.groups()
        if int(index) != i + 1 or int(hour) > 23 or int(minute) > 59:
            return None
        candidates = []
        for year in range(start.year, end.year + 1):
            try:
                candidate = date(year, int(month), int(day))
            except ValueError:
                continue
            if start <= candidate <= end:
                candidates.append(candidate)
        if len(candidates) != 1:
            return None
        segment = text[row.end():rows[i + 1].start() if i + 1 < len(rows) else len(text)]
        # 里程、金额是相邻的两个数值单元格，不扫描地址中的小数。
        money = list(re.finditer(
            r"(?m)^\s*\d+(?:\.\d+)?[ \t]*\n[ \t]*[¥￥]?"
            r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)[ \t]*(?=\n|$)", segment,
        ))
        if len(money) != 1:
            return None
        amount = Decimal(money[0][1].replace(",", ""))
        location_text = segment[:money[0].start()]
        # 城市可能独占一行，也可能在“市”前换行；只剥离一次。
        location_text = re.sub(r"^\s*[\u4e00-\u9fff]{1,8}\s*市[ \t]*\n", "", location_text, count=1)
        parts = parser._prepare_didi_location_lines(location_text)
        origin, destination = parser._split_didi_locations(parts)
        origin, destination = parser._clean_location(origin), parser._clean_location(destination)
        if not origin or not destination:
            return None
        car = re.sub(r"\s+", "", car)
        date_str = candidates[0].isoformat()
        time_str = f"{int(hour):02d}:{int(minute):02d}"
        records.append({
            "type": "打车行程单", "date": date_str, "time": time_str,
            "trip_index": int(index), "start_location": origin, "end_location": destination,
            "amount": float(amount), "car_type": car, "need_substitute": "顺风车" in car,
            "hotel_name": "", "nights": "", "daily_rate": "", "has_invoice": False,
            "raw_text": f"行程{index}: {date_str} {time_str} {origin}-{destination} ¥{amount} [{car}]",
        })
    return records if summary_matches(text, records) else None
