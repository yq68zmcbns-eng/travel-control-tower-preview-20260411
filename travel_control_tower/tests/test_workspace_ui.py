from __future__ import annotations

import unittest

from travel_control_tower.preview.render_html import render_plan_html
from travel_control_tower.web.workspace_ui import render_edit_page, render_orders_page


class WorkspaceUiTests(unittest.TestCase):
    def test_edit_page_contains_day_and_item_fields(self) -> None:
        plan = {
            "overview": {"title": "杭州两日", "summary": "慢慢逛"},
            "daily_plan": [{"date": "2026-09-10", "theme": "西湖", "items": [{"start_time": "09:00", "end_time": "11:00", "label": "断桥", "notes": "步行"}]}],
        }
        page = render_edit_page(plan, "abc123")
        self.assertIn("编辑旅行攻略", page)
        self.assertIn("d0_i0_label", page)
        self.assertIn("断桥", page)

    def test_orders_page_contains_order_entry_form(self) -> None:
        page = render_orders_page("abc123", [])
        self.assertIn("订单与凭证", page)
        self.assertIn("name='confirmation'", page)
        self.assertIn("action='/jobs/abc123/orders'", page)

    def test_flyai_candidate_booking_url_is_clickable(self) -> None:
        plan = {
            "overview": {"title": "测试", "summary": ""}, "daily_plan": [], "budget": {},
            "hotel_candidates": [{"name": "飞猪酒店", "booking_url": "https://example.com/hotel"}],
            "transport_candidates": [], "poi_candidates": [], "booking_items": [],
        }
        page = render_plan_html(plan)
        self.assertIn("去飞猪查看酒店", page)
        self.assertIn("https://example.com/hotel", page)


if __name__ == "__main__":
    unittest.main()
