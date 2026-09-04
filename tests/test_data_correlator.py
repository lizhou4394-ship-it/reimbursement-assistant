import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.data_correlator import DataCorrelator


class DataCorrelatorHotelDateTest(unittest.TestCase):
    def test_hotel_keeps_invoice_date_without_explicit_stay_dates(self):
        correlator = DataCorrelator("", "")
        raw = [
            {
                "type": "火车票",
                "date": "2026-06-08",
                "time": "06:35",
                "start_location": "杭州西",
                "end_location": "温州北",
                "amount": 117,
            },
            {
                "type": "火车票",
                "date": "2026-06-10",
                "time": "13:55",
                "start_location": "温州南",
                "end_location": "杭州东",
                "amount": 52,
            },
            {
                "type": "酒店",
                "date": "2026-07-06",
                "hotel_name": "温州瑶住酒店",
                "amount": 506.72,
                "nights": "",
                "daily_rate": "",
            },
        ]

        records = correlator.correlate_all(raw, work_description="")
        hotel = next(r for r in records if r["type"] == "酒店")

        self.assertEqual(hotel["date"], "2026-07-06")
        self.assertEqual(hotel["check_in_date"], "")
        self.assertEqual(hotel["check_out_date"], "")


class DataCorrelatorCourierTest(unittest.TestCase):
    def test_courier_summary_is_removed_when_waybill_details_exist(self):
        correlator = DataCorrelator("", "")
        raw = [
            {
                "type": "快递",
                "date": "2026-07-06",
                "amount": 56.26,
                "invoice_number": "26337000000649766771",
            },
            {
                "type": "快递",
                "date": "2026-06-16",
                "amount": 32.34,
                "invoice_number": "26337000000649766771",
                "is_waybill_detail": True,
            },
            {
                "type": "快递",
                "date": "2026-06-12",
                "amount": 23.92,
                "invoice_number": "26337000000649766771",
                "is_waybill_detail": True,
            },
        ]

        records = correlator.correlate_all(raw, work_description="")
        couriers = [r for r in records if r["type"] == "快递"]

        self.assertEqual(len(couriers), 2)
        self.assertAlmostEqual(sum(r["amount"] for r in couriers), 56.26, places=2)
        self.assertTrue(all(r.get("is_waybill_detail") for r in couriers))


if __name__ == "__main__":
    unittest.main()
