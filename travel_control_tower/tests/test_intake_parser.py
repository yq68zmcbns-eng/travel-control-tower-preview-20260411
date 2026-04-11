from __future__ import annotations

from datetime import date
import unittest

from travel_control_tower.planner_core.intake_parser import parse_freeform_request


class IntakeParserTests(unittest.TestCase):
    def test_parse_weekend_chinese_prompt(self) -> None:
        parsed = parse_freeform_request(
            "下周末上海去南京两天，预算2000，想轻松一点，想去夫子庙和中山陵",
            today=date(2026, 4, 7),
        )

        self.assertEqual(parsed.departure_city, "上海")
        self.assertEqual(parsed.destination, "南京")
        self.assertEqual(parsed.start_date, "2026-04-11")
        self.assertEqual(parsed.end_date, "2026-04-12")
        self.assertEqual(parsed.budget_per_person, 2000.0)
        self.assertEqual(parsed.travel_style, "relaxed")
        self.assertEqual(parsed.must_go, ["夫子庙", "中山陵"])

    def test_parse_weekend_prompt_with_from_prefix(self) -> None:
        parsed = parse_freeform_request(
            "下周末从上海去南京两天，预算2000，想轻松一点，想去夫子庙和老门东",
            today=date(2026, 4, 7),
        )

        self.assertEqual(parsed.departure_city, "上海")
        self.assertEqual(parsed.destination, "南京")
        self.assertEqual(parsed.start_date, "2026-04-11")
        self.assertEqual(parsed.end_date, "2026-04-12")
        self.assertEqual(parsed.must_go, ["夫子庙", "老门东"])

    def test_parse_explicit_dates_and_travelers(self) -> None:
        parsed = parse_freeform_request(
            "从上海出发去北京，2026年5月1日到2026年5月3日，2人，预算4500，想去故宫、什刹海",
            today=date(2026, 4, 7),
        )

        self.assertEqual(parsed.departure_city, "上海")
        self.assertEqual(parsed.destination, "北京")
        self.assertEqual(parsed.start_date, "2026-05-01")
        self.assertEqual(parsed.end_date, "2026-05-03")
        self.assertEqual(parsed.traveler_count, 2)
        self.assertEqual(parsed.must_go, ["故宫", "什刹海"])

    def test_parse_price_scan_window_request(self) -> None:
        parsed = parse_freeform_request(
            "未来3个月上海去东京待3天，想看低价机票，预算4000",
            today=date(2026, 4, 8),
        )

        self.assertEqual(parsed.departure_city, "上海")
        self.assertEqual(parsed.destination, "东京")
        self.assertEqual(parsed.request_mode, "price_scan")
        self.assertEqual(parsed.flexible_window_start, "2026-04-08")
        self.assertEqual(parsed.flexible_window_end, "2026-07-07")
        self.assertEqual(parsed.target_trip_days, 3)
        self.assertEqual(parsed.target_trip_nights, 2)
        self.assertEqual(parsed.price_priority, "low")
        self.assertEqual(parsed.budget_per_person, 4000.0)


if __name__ == "__main__":
    unittest.main()
