from __future__ import annotations

import unittest

from travel_control_tower.booking_compare import (
    build_provider_comparison_rows,
    flight_provider_links,
    hotel_provider_links,
)
from travel_control_tower.preview.render_html import render_booking_comparison


class BookingCompareTests(unittest.TestCase):
    @staticmethod
    def _sample_plan() -> dict:
        return {
            "input_snapshot": {"开始日期": "2027-05-10", "结束日期": "2027-05-20", "人数": 2},
            "daily_plan": [
                {"day_index": 1, "date": "2027-05-10"},
                {"day_index": 3, "date": "2027-05-12"},
            ],
            "route_segments": [
                {"day_index": 1, "origin": "杭州HGH", "destination": "新加坡SIN", "mode": "flight"},
                {"day_index": 3, "origin": "新加坡SIN", "destination": "槟城PEN", "mode": "flight"},
            ],
            "transport_candidates": [
                {"name": "杭州到新加坡直飞", "route": "HGH-SIN", "price": "约1200-1800元/人"},
                {"name": "新加坡到槟城直飞", "route": "SIN-PEN", "price": "约350-650元/人"},
            ],
            "hotel_stay_groups": [
                {
                    "dates": "2027-05-10",
                    "nights": 1,
                    "city": "新加坡",
                    "recommended_option": {
                        "name": "Hotel Mi Bencoolen",
                        "price_cny_per_night": "约750-1050元/晚",
                        "provider_links": {
                            "ctrip": "https://hotels.ctrip.com/hotels/10231080.html",
                            "fliggy": "https://router.feizhu.com/hotel-mi",
                        },
                        "provider_price_snapshots": {"fliggy": "FlyAI候选价：¥1xxx/晚"},
                    },
                }
            ],
        }

    def test_flight_links_use_same_route_date_and_adult_count(self) -> None:
        links = flight_provider_links("杭州HGH", "新加坡SIN", "2027-05-10", 2)

        self.assertIn("oneway-hgh-sin", links["ctrip_url"])
        self.assertIn("depdate=2027-05-10", links["ctrip_url"])
        self.assertIn("adult=2", links["ctrip_url"])
        self.assertIn("depCity=HGH", links["fliggy_url"])
        self.assertIn("arrCity=SIN", links["fliggy_url"])
        self.assertIn("adultNum=2", links["fliggy_url"])

    def test_hotel_links_keep_name_city_and_dates(self) -> None:
        links = hotel_provider_links("新加坡", "Hotel Mi Bencoolen", "2027-05-10", "2027-05-11")

        self.assertIn("hotels.ctrip.com", links["ctrip_url"])
        self.assertIn("Hotel+Mi+Bencoolen", links["ctrip_url"])
        self.assertIn("2027-05-10", links["fliggy_url"])
        self.assertIn("Hotel+Mi+Bencoolen", links["fliggy_url"])

    def test_plan_rows_and_html_show_both_providers(self) -> None:
        plan = self._sample_plan()
        rows = build_provider_comparison_rows(plan)
        html = render_booking_comparison(plan)

        self.assertEqual(len(rows), 3)
        self.assertIn("Hotel Mi Bencoolen", html)
        self.assertIn("携程预订这家", html)
        self.assertIn("飞猪预订这家", html)
        self.assertIn("可直接预订", html)
        self.assertIn("FlyAI候选价：¥1xxx/晚", html)
        self.assertEqual(rows[0]["ctrip_link_type"], "酒店详情页")


if __name__ == "__main__":
    unittest.main()
