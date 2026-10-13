"""规则识别、AI、人工编辑和导出共同使用的数据约束。"""
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

TYPES = {"火车票", "打车", "打车行程单", "飞机票", "酒店", "餐饮", "快递", "其他",
         "出差补贴", "通讯补贴", "餐补"}


def normalize_date(value):
    if not isinstance(value, str):
        raise ValueError("日期必须是文字")
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text)
    if not match:
        raise ValueError("日期应为 YYYY-MM-DD")
    return date(*map(int, match.groups())).isoformat()


def normalize_amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("金额应为有效数字")
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = re.sub(r"^[¥￥]\s*", "", text)
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", text):
        raise ValueError("金额必须为非负数字，最多两位小数")
    try:
        amount = Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise ValueError("金额应为有效数字") from None
    if not amount.is_finite():
        raise ValueError("金额应为有限数字")
    return float(amount)


def normalize_record(record, allow_zero=False):
    if not isinstance(record, dict):
        raise ValueError("识别结果不是票据记录")
    item = dict(record)
    if item.get("type") not in TYPES:
        raise ValueError("费用类型无法识别")
    item["date"] = normalize_date(item.get("date"))
    item["amount"] = normalize_amount(item.get("amount"))
    if not allow_zero and item["amount"] <= 0:
        raise ValueError("票据金额必须大于零")
    for key in ["start_location", "end_location", "hotel_name", "work_content", "raw_text", "car_type"]:
        value = item.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{key} 应为文字")
        item[key] = value.strip()
    if item["type"] in {"火车票", "飞机票", "打车行程单"}:
        if not item["start_location"] or not item["end_location"]:
            raise ValueError("交通记录缺少起点或终点")
    time = item.get("time", "")
    if not isinstance(time, str):
        raise ValueError("时间应为 HH:MM")
    time = re.sub(r"\s+", "", unicodedata.normalize("NFKC", time))
    if time:
        match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", time)
        if not match:
            raise ValueError("时间应为有效的 HH:MM")
        time = f"{int(match[1]):02d}:{match[2]}"
    item["time"] = time
    if item["type"] == "打车行程单" and (not time or not item["car_type"]):
        raise ValueError("行程单缺少时间或车型")
    if item["type"] == "酒店":
        for key in ["check_in_date", "check_out_date"]:
            if item.get(key):
                item[key] = normalize_date(item[key])
        start, end = item.get("check_in_date"), item.get("check_out_date")
        if bool(start) != bool(end):
            raise ValueError("入住与离店日期请同时填写或同时留空")
        if start and end < start:
            raise ValueError("离店日期不能早于入住日期")
        if item.get("nights") not in (None, ""):
            nights = normalize_amount(item["nights"])
            if nights != int(nights):
                raise ValueError("住宿天数应为非负整数")
            item["nights"] = int(nights)
        if item.get("daily_rate") not in (None, ""):
            item["daily_rate"] = normalize_amount(item["daily_rate"])
    return item


def normalize_records(result):
    records = result if isinstance(result, list) else [result]
    if not records:
        return None
    try:
        items = [normalize_record(record) for record in records]
    except (ValueError, TypeError, OverflowError):
        return None
    return items if isinstance(result, list) else items[0]
