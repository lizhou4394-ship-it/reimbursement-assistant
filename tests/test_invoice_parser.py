import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.invoice_parser import InvoiceParser


class InvoiceParserRegressionTest(unittest.TestCase):
    def setUp(self):
        self.parser = InvoiceParser("", "")

    def test_hotel_amount_prefers_tax_inclusive_total(self):
        text = """电子发票（普通发票）
项目名称
金额
税额
开票日期：2026年07月06日
合计
价税合计（大写）
（小写）
备注
开票人：
¥478.04
¥28.68
伍佰零陆圆柒角贰分
¥ 506.72
名称：温州瑶住酒店管理有限公司
*生产生活服务*住宿费
478.04
6%
28.68
"""

        result = self.parser._regex_hotel(text)

        self.assertIsNotNone(result)
        self.assertEqual(result["amount"], 506.72)

    def test_hotel_amount_handles_currency_symbol_after_numbers(self):
        text = """电子发票（普通发票）
发票号码：
开票日期：
26332000005818210036
2026年07月06日
温州市汇际酒店管理有限公司
项目名称
金额
税额
*生产生活服务*住宿费
260.99
6%
15.66
价税合计（大写）
合        计
（小写）
贰佰柒拾陆圆陆角伍分
¥276.65
260.99
¥
15.66
¥
"""

        result = self.parser._regex_hotel(text)

        self.assertIsNotNone(result)
        self.assertEqual(result["amount"], 276.65)

    def test_hotel_extracts_explicit_stay_dates_only(self):
        text = """电子发票（普通发票）
开票日期：2026年07月06日
温州某某酒店
入住日期：2026年06月08日
离店日期：2026年06月10日
价税合计（小写）
¥506.72
*生产生活服务*住宿费
"""

        result = self.parser._regex_hotel(text)

        self.assertEqual(result["date"], "2026-07-06")
        self.assertEqual(result["check_in_date"], "2026-06-08")
        self.assertEqual(result["check_out_date"], "2026-06-10")

    def test_date_extraction_ignores_tax_ids(self):
        text = """电子发票（普通发票）
发票号码：
开票日期：
购买方信息
统一社会信用代码/纳税人识别号：
91330109MA2B24MM2N
91330102MAE7NHEL1E
2026年07月06日
99.00
¥
申丹丹
达美乐比萨（杭州）有限公司
93.40
¥
*生产生活服务*餐饮
93.40
6%
5.60
"""

        result = self.parser._regex_restaurant(text)

        self.assertIsNotNone(result)
        self.assertEqual(result["date"], "2026-07-06")
        self.assertEqual(result["amount"], 99.0)
        self.assertTrue(self.parser._validate_parsed_result(result))

    def test_didi_itinerary_handles_wrapped_locations(self):
        text = """滴滴出行-行程单
· 申请日期：2026-07-06
· 行程起止日期：2026-06-05 至 2026-07-05
· 共1笔行程， 合计42.50元
序号
车型
上车时间
城市
起点
终点
里程[公里] 金额[元] 备注
1
特惠快
车
06-10 17:57 周
三
杭州
市
新塘路|杭州东站-网约车西-6号室内网约车上
客区
闲林|云睦宝庆苑
23.0
42.50
"""

        result = self.parser._regex_didi_itinerary(text)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start_location"], "杭州东站-网约车西-6号室内网约车上客区")
        self.assertEqual(result[0]["end_location"], "云睦宝庆苑")
        self.assertEqual(result[0]["time"], "17:57")

    def test_courier_waybill_detail_returns_line_items(self):
        text = """顺丰电子发票 — 运单明细
#
运单号码
寄件时间
寄方
寄件地
收方
收件地
运单金额(元)
1
SF0210643716276
2026/06/16
石彩霞
杭州市
林逸清
杭州市
32.34
2
SF0213933567111
2026/06/12
石彩霞
杭州市
李沈飞
嘉兴市
23.92
开票时间
发票号码
2026/07/06 26337000000649766771
公司名称
杭州星灿生物技术有限公司
发票总金额(元)
56.26
"""

        result = self.parser._regex_courier_waybill_detail(text)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], "2026-06-16")
        self.assertEqual(result[0]["amount"], 32.34)
        self.assertEqual(result[0]["invoice_number"], "26337000000649766771")
        self.assertEqual(result[1]["date"], "2026-06-12")
        self.assertEqual(result[1]["amount"], 23.92)
        self.assertEqual(result[1]["invoice_number"], "26337000000649766771")

    def test_train_station_order_prefers_pinyin_when_chinese_is_reversed(self):
        text = """国家税务总局
Tongludong
C2376
2026年05月07日
电子发票（铁路电子客票）
15:21开
票价:￥26.00
Hangzhouxi
杭州西站
桐庐东站
中国铁路祝您旅途愉快
"""

        result = self.parser._regex_train_ticket(text)

        self.assertEqual(result["start_location"], "桐庐东")
        self.assertEqual(result["end_location"], "杭州西")

    def test_train_station_order_uses_pinyin_for_outbound_ticket(self):
        text = """国家税务总局
Hangzhouxi
G1415
2026年05月07日
电子发票（铁路电子客票）
08:44开
票价:￥30.00
Tongludong
桐庐东站
杭州西站
中国铁路祝您旅途愉快
"""

        result = self.parser._regex_train_ticket(text)

        self.assertEqual(result["start_location"], "杭州西")
        self.assertEqual(result["end_location"], "桐庐东")

    def test_train_station_order_falls_back_to_chinese_station_order(self):
        text = """国家税务总局
杭州西站
衢州西站
G1439
2026年05月12日
12:28开
票价:￥93.00
电子发票（铁路电子客票）
"""

        result = self.parser._regex_train_ticket(text)

        self.assertEqual(result["start_location"], "杭州西")
        self.assertEqual(result["end_location"], "衢州西")


if __name__ == "__main__":
    unittest.main()
