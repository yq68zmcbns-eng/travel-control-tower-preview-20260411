from __future__ import annotations

import unittest
from types import SimpleNamespace

from travel_control_tower.web.app import _parse_manual_schedule
from travel_control_tower.web.form_ui import render_form_page
from travel_control_tower.web.route_maps import enrich_plan_route_maps, static_map_params
from travel_control_tower.web.workspace_ui import render_manual_plan_page


class FakeAmapAdapter:
    is_available = True

    COORDINATES = {
        "杭州 西湖": "120.143,30.250",
        "杭州 湖滨午餐": "120.161,30.252",
        "杭州 灵隐寺": "120.102,30.240",
    }

    def geocode(self, query: str) -> dict:
        return {"location": self.COORDINATES[query]}

    def estimate_transfer(self, origin: str, destination: str, mode: str):
        return SimpleNamespace(distance_km=2.4, duration_minutes=18)


class RouteMapAndManualPlannerTests(unittest.TestCase):
    def test_manual_schedule_builds_multiple_days(self) -> None:
        plan = _parse_manual_schedule({
            "title": "杭州两日",
            "destination": "杭州",
            "start_date": "2026-09-01",
            "schedule_text": "第1天\n09:00-11:00 西湖\n11:30-12:30 湖滨午餐\n第2天\n灵隐寺",
        })
        self.assertEqual(len(plan["daily_plan"]), 2)
        self.assertEqual(plan["daily_plan"][1]["date"], "2026-09-02")
        self.assertEqual(plan["daily_plan"][0]["items"][1]["category"], "餐食")

    def test_route_map_contains_distances_and_order_advice(self) -> None:
        plan = {
            "input_snapshot": {"目的地": "杭州"},
            "selected_hotel": {},
            "daily_plan": [{
                "day_index": 1,
                "items": [
                    {"category": "游玩", "label": "西湖"},
                    {"category": "餐食", "label": "湖滨午餐"},
                    {"category": "游玩", "label": "灵隐寺"},
                ],
            }],
        }
        enrich_plan_route_maps(plan, FakeAmapAdapter())
        route_map = plan["daily_plan"][0]["route_map"]
        self.assertTrue(route_map["available"])
        self.assertEqual(route_map["total_distance_km"], 4.8)
        self.assertEqual(len(route_map["segments"]), 2)
        self.assertIn(route_map["status"], {"路线较顺", "有少量折返", "建议调整顺序"})
        params = static_map_params(route_map)
        self.assertIn("markers", params)
        self.assertIn("paths", params)
        self.assertNotIn("key", params)

    def test_home_and_manual_pages_offer_both_creation_modes(self) -> None:
        home = render_form_page()
        manual = render_manual_plan_page()
        self.assertIn("AI 帮我生成", home)
        self.assertIn("我自己制定", home)
        self.assertIn("保存并生成路线图", manual)


if __name__ == "__main__":
    unittest.main()
