import io
import zipfile
import unittest
from unittest.mock import patch
from services.zip_extractor import ZipExtractor
from services.invoice_parser import InvoiceParser
from services.data_correlator import DataCorrelator


class UploadIntegrityTest(unittest.TestCase):
    def test_exact_duplicate_files_are_reported_and_not_counted_twice(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("a/photo.png", b"same image bytes")
            archive.writestr("b/renamed.png", b"same image bytes")
        extractor = ZipExtractor()
        try:
            files = extractor.extract_zip(payload.getvalue())
            self.assertEqual(len(files), 1)
            self.assertEqual(len(extractor.duplicate_files), 1)
        finally:
            extractor.cleanup()

    def test_single_taxi_invoice_is_preserved_without_itinerary(self):
        text = "电子发票（普通发票）\n滴滴\n旅客运输服务\n开票日期：2026年09月05日\n（小写）￥60.00"
        result = InvoiceParser("").parse_all_invoices([{"filename": "invoice.pdf", "images": ["p1"], "page_texts": [text]}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["amount"], 60)

    def test_aggregate_taxi_invoice_is_not_added_on_top_of_itinerary(self):
        invoice = {"type": "打车", "date": "2026-09-10", "amount": 60, "source_file": "invoice.pdf", "raw_text": "滴滴电子发票"}
        trips = [{"type": "打车行程单", "date": "2026-09-05", "amount": 30, "source_file": "trips.pdf",
                  "time": clock, "start_location": "温州南", "end_location": "酒店", "car_type": "快车"}
                 for clock in ["12:00", "13:00"]]
        result = DataCorrelator("").correlate_all([invoice, *trips], "")
        self.assertEqual(len(result), 2)
        self.assertEqual(sum(r["amount"] for r in result), 60)
        self.assertTrue(all(r["has_invoice"] for r in result))

    def test_exception_finishes_progress_and_reports_failed_file(self):
        parser = InvoiceParser("")
        progress = []
        with patch.object(parser, "parse_single_invoice", side_effect=RuntimeError("simulated API exception")):
            result = parser.parse_all_invoices([{"filename": "scan.png", "images": ["p1"]}],
                                              progress_callback=lambda n, total, name: progress.append((n, total, name)))
        self.assertEqual(result, [])
        self.assertEqual(parser.failed_files, ["scan.png"])
        self.assertEqual(progress, [(1, 1, "scan.png")])
