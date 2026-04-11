import json
from pathlib import Path
import unittest

from travel_control_tower.preview.render_html import render_plan_file


class PreviewTests(unittest.TestCase):
    def test_render_plan_file_contains_expected_sections(self) -> None:
        base_dir = Path(__file__).resolve().parents[1]
        input_path = base_dir / "examples" / "_preview_test.plan.json"
        output_path = base_dir / "examples" / "_preview_test.html"

        sample = {
            "status": "draft",
            "overview": {"title": "旅行方案", "summary": "测试摘要"},
            "request_context": {
                "natural_language_request": "下周末从上海去南京两天，想轻松一点，想去夫子庙和老门东。",
                "manual_constraints": ["补充必去点：夫子庙 / 老门东", "补充交通偏好：高铁 / 地铁"],
            },
            "input_snapshot": {"出发地": "上海"},
            "assumptions": ["测试假设"],
            "daily_plan": [
                {
                    "day_index": 1,
                    "date": "2026-06-09",
                    "theme": "测试主题",
                    "items": [
                        {
                            "label": "到达交通",
                            "category": "交通",
                            "start_time": "11:00",
                            "end_time": "12:15",
                            "duration_minutes": 75,
                            "notes": "测试说明",
                            "route_origin": "南京南站",
                            "route_destination": "夫子庙片区",
                            "route_mode": "DRIVE",
                            "route_mode_label": "打车 / 驾车",
                            "route_provider": "amap",
                            "route_distance_km": 12.6,
                            "route_summary": "高德地图预计 34 分钟。",
                        }
                    ],
                    "why_this_day": "测试",
                    "transport_strategy": "测试",
                    "meal_strategy": "测试",
                    "fallback_if_fast": "测试",
                    "fallback_if_tired": "测试",
                }
            ],
            "budget": {
                "fixed_cost_total": 1000,
                "per_person_cost": 1000,
                "optional_upgrade_total": 300,
                "breakdown": [{"category": "交通", "total": 500, "per_person": 500, "notes": "测试"}],
            },
            "booking_items": [
                {
                    "name": "酒店",
                    "category": "住宿",
                    "priority": "high",
                    "timing": "尽快",
                    "why_now": "价格会变",
                    "risk_if_wait": "可能涨价",
                    "url": "https://example.com",
                }
            ],
            "provider_statuses": [{"name": "路线数据", "status": "已接入真实路线", "details": "测试"}],
            "selected_hotel": {"name": "测试酒店", "area": "市中心", "nightly_price": 300, "notes": "测试"},
            "selected_transport": {"label": "测试交通", "category": "机票", "total_price": 800, "depart_at": "2026-06-09 08:00", "arrive_at": "2026-06-11 20:00"},
            "hotel_candidates": [],
            "transport_candidates": [],
            "poi_candidates": [
                {
                    "name": "南京夫子庙",
                    "category": "人文景点",
                    "poi_level": "4A",
                    "free_status": "部分免费",
                    "address": "南京市秦淮区",
                    "notes": "测试景点候选",
                    "booking_url": "https://example.com/poi",
                }
            ],
            "price_scan_summary": {
                "window_start": "2026-04-08",
                "window_end": "2026-07-07",
                "chosen_start_date": "2026-05-13",
                "chosen_end_date": "2026-05-15",
                "trip_days": 3,
                "trip_nights": 2,
                "chosen_price": 2136,
                "sample_count": 5,
                "chosen_label": "测试窗口",
            },
            "price_scan_candidates": [
                {
                    "trip_start_date": "2026-05-13",
                    "trip_end_date": "2026-05-15",
                    "label": "测试窗口",
                    "total_price": 2136,
                    "booking_url": "https://example.com/flight",
                }
            ],
            "open_questions": [],
        }
        input_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        render_plan_file(input_path, output_path)
        content = output_path.read_text(encoding="utf-8")

        self.assertIn("旅行方案", content)
        self.assertIn("预算", content)
        self.assertIn("预定事项", content)
        self.assertIn("数据状态", content)
        self.assertIn("D1", content)
        self.assertIn("当前主选择", content)
        self.assertIn("分钟", content)
        self.assertIn("低价窗口结果", content)
        self.assertIn("景点候选", content)
        self.assertIn("本次输入来源", content)
        self.assertIn("自然语言原文", content)
        self.assertIn("手动补充约束", content)
        self.assertIn("南京南站", content)
        self.assertIn("夫子庙片区", content)
        self.assertIn("高德地图", content)

        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)

    def test_render_plan_file_marks_estimated_hotel_price(self) -> None:
        base_dir = Path(__file__).resolve().parents[1]
        input_path = base_dir / "examples" / "_preview_test_estimated.plan.json"
        output_path = base_dir / "examples" / "_preview_test_estimated.html"

        sample = {
            "status": "draft",
            "overview": {"title": "旅行方案", "summary": "测试摘要"},
            "daily_plan": [],
            "budget": {"fixed_cost_total": 560, "per_person_cost": 280, "optional_upgrade_total": 0, "breakdown": []},
            "selected_hotel": {
                "name": "测试酒店",
                "area": "市中心",
                "nightly_price": 560,
                "currency": "CNY",
                "notes": "价格未直接返回，当前按同片区同档位酒店估算每晚约 560 元。",
            },
            "selected_transport": {},
            "hotel_candidates": [],
            "transport_candidates": [],
            "poi_candidates": [],
            "booking_items": [],
            "provider_statuses": [],
            "assumptions": [],
            "open_questions": [],
        }
        input_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        render_plan_file(input_path, output_path)
        content = output_path.read_text(encoding="utf-8")

        self.assertIn("约 ¥560（估）", content)

        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
