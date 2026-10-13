"""Regression: future layouts and malformed model results, QA 2026-10-13.

AI responses are mocked: these tests verify guards, not OCR accuracy.
"""
import unittest
from unittest.mock import patch
from services.invoice_parser import InvoiceParser


def ticket(day="2026-09-05", amount=93):
    return {"type": "火车票", "date": day, "time": "12:28",
            "start_location": "杭州西", "end_location": "衢州西", "amount": amount}


def train_text(day="2026-09-05", extra=""):
    return f"电子发票（铁路电子客票）\n中国铁路\n{extra}\n杭州西站\n衢州西站\n乘车日期：{day} 12:28开\n票价：￥93.00\n"


class ParserIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.parser = InvoiceParser("")

    def test_invalid_ai_records_never_appear_in_successful_results(self):
        for bad in [ticket(amount=float("nan")), ticket(amount=float("inf")),
                    ticket(amount=-1), ticket(amount=True), ticket(day="2026-02-30"),
                    {**ticket(), "start_location": ""}, {**ticket(), "type": "火车"},
                    {**ticket(), "end_location": []}, "not a record", {}, [], [ticket(), {}]]:
            with self.subTest(bad=bad), patch.object(self.parser, "_parse_with_vision_model", return_value=bad):
                result = self.parser.parse_all_invoices([{"filename": "scan.pdf", "images": ["p1"]}])
                self.assertEqual(result, [])
                self.assertEqual(self.parser.failed_files, ["scan.pdf"])

    def test_invalid_text_ai_falls_back_to_vision(self):
        with patch.object(self.parser, "_try_parse_regex", return_value=None), \
             patch.object(self.parser, "_parse_with_text_model", return_value=ticket(day="2026-02-30")), \
             patch.object(self.parser, "_parse_with_vision_model", return_value=ticket()) as vision:
            result = self.parser.parse_single_invoice("p1", page_text="unfamiliar layout " * 10)
        self.assertEqual(result["date"], "2026-09-05")
        vision.assert_called_once()

    def test_amount_strings_and_fullwidth_dates_are_normalized(self):
        record = {**ticket(), "date": "２０２６年９月５日", "amount": "￥1,200.50", "time": "１２：２８"}
        with patch.object(self.parser, "_parse_with_vision_model", return_value=record):
            result = self.parser.parse_single_invoice("p1")
        self.assertEqual(result["date"], "2026-09-05")
        self.assertEqual(result["amount"], 1200.5)
        self.assertEqual(result["time"], "12:28")

    def test_all_pages_of_combined_train_pdf_are_processed(self):
        result = self.parser.parse_all_invoices([{"filename": "combined.pdf", "images": ["p1", "p2"],
                                                 "page_texts": [train_text(), train_text("2026-09-06")]}])
        self.assertEqual(len(result), 2)
        self.assertEqual([r["date"] for r in result], ["2026-09-05", "2026-09-06"])

    def test_train_invoice_is_not_skipped_as_taxi_invoice(self):
        result = self.parser._try_parse_regex(train_text(extra="*旅客运输服务*铁路运输"))
        self.assertEqual(result["type"], "火车票")

    def test_train_date_prefers_travel_date_over_invoice_date(self):
        result = self.parser._try_parse_regex(train_text(extra="开票日期：2026-10-07"))
        self.assertEqual(result["date"], "2026-09-05")

    def test_refunds_are_not_parsed_as_positive_ticket_costs(self):
        for marker in ["退票", "红字发票", "已作废"]:
            with self.subTest(marker=marker), patch.object(self.parser, "_parse_with_vision_model", return_value=ticket()):
                self.assertIsNone(self.parser.parse_single_invoice("p1", page_text=train_text(extra=marker)))

    def test_valid_train_layout_variants(self):
        for text in [train_text(), train_text().replace("-", "/"),
                     train_text().replace("2026-09-05", "2026年09月05日"),
                     train_text().replace("票价：￥93.00", "票价\n￥93.00"),
                     train_text().replace("12:28", "12：\n28")]:
            with self.subTest(text=text):
                result = self.parser._try_parse_regex(text)
                self.assertEqual(result["date"], "2026-09-05")
                self.assertEqual(result["amount"], 93)

    def test_future_calendar_year_is_not_artificially_rejected(self):
        self.assertTrue(self.parser._validate_parsed_result(ticket(day="2031-09-05")))

    def test_large_valid_hotel_amount_is_not_rejected(self):
        self.assertTrue(self.parser._validate_parsed_result({"type": "酒店", "date": "2026-09-05", "amount": 8000}))


if __name__ == "__main__":
    unittest.main()
