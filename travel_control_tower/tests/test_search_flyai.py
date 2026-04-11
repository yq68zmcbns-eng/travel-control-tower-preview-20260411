from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from travel_control_tower.adapters.stable_search_flyai import StableFlyAISearchAdapter as FlyAISearchAdapter


class FlyAISearchAdapterTests(unittest.TestCase):
    def test_safe_float_extracts_price_from_currency_strings(self) -> None:
        self.assertEqual(FlyAISearchAdapter._safe_float("¥268"), 268.0)
        self.assertEqual(FlyAISearchAdapter._safe_float("￥1,299起"), 1299.0)
        self.assertEqual(FlyAISearchAdapter._safe_float("约 540 元"), 540.0)
        self.assertEqual(FlyAISearchAdapter._safe_float(None), 0.0)

    def test_search_hotels_parses_nightly_price_from_flyai_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FlyAISearchAdapter(command="flyai", cache_dir=Path(tmpdir))
            payload = {
                "status": 0,
                "message": "success",
                "data": {
                    "itemList": [
                        {
                            "name": "测试酒店A",
                            "price": "¥268起",
                            "cityName": "苏州",
                            "star": "舒适型",
                            "address": "山塘街附近",
                            "interestsPoi": "山塘街",
                            "detailUrl": "https://example.com/a",
                        },
                        {
                            "name": "测试酒店B",
                            "price": "￥399",
                            "cityName": "苏州",
                            "star": "高档型",
                            "address": "平江路附近",
                            "interestsPoi": "平江路",
                            "detailUrl": "https://example.com/b",
                        },
                    ]
                },
            }

            with patch.object(adapter, "_run_json", return_value=payload):
                hotels = adapter.search_hotels("苏州", "2026-04-11", "2026-04-12", keyword="山塘街", max_price=600)

            self.assertEqual(len(hotels), 2)
            self.assertEqual(hotels[0].name, "测试酒店A")
            self.assertEqual(hotels[0].nightly_price, 268.0)
            self.assertEqual(hotels[1].nightly_price, 399.0)

    def test_keyword_search_hotels_preserves_price_when_payload_has_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FlyAISearchAdapter(command="flyai", cache_dir=Path(tmpdir))
            payload = {
                "status": 0,
                "message": "success",
                "data": {
                    "itemList": [
                        {
                            "info": {
                                "title": "测试酒店A",
                                "cityName": "苏州",
                                "areaName": "山塘街",
                                "price": "¥388起",
                                "star": "3",
                                "jumpUrl": "https://example.com/hotel-a",
                            }
                        }
                    ]
                },
            }

            with patch.object(adapter, "_run_json", return_value=payload):
                hotels = adapter.keyword_search_hotels("苏州 山塘街 酒店")

            self.assertEqual(len(hotels), 1)
            self.assertEqual(hotels[0].nightly_price, 388.0)

    def test_keyword_search_hotels_does_not_fake_area_from_query_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FlyAISearchAdapter(command="flyai", cache_dir=Path(tmpdir))
            payload = {
                "status": 0,
                "message": "success",
                "data": {
                    "itemList": [
                        {
                            "info": {
                                "title": "松果酒店(郑州桐柏路市中心医院地铁站店)",
                                "price": None,
                                "star": "2",
                                "jumpUrl": "https://example.com/hotel-a",
                            }
                        }
                    ]
                },
            }

            with patch.object(adapter, "_run_json", return_value=payload):
                hotels = adapter.keyword_search_hotels("苏州 山塘街 酒店")

            self.assertEqual(len(hotels), 1)
            self.assertEqual(hotels[0].area, "")

    def test_poi_notes_hides_unknown_status_values(self) -> None:
        notes = FlyAISearchAdapter._poi_notes(
            {
                "category": "None",
                "poiLevel": "None",
                "freePoiStatus": "UNKNOWN",
                "address": "苏州山塘街",
            }
        )

        self.assertIn("地址：苏州山塘街", notes)
        self.assertNotIn("类型：None", notes)
        self.assertNotIn("等级：None", notes)
        self.assertNotIn("门票：UNKNOWN", notes)

    def test_run_json_accepts_success_payload_even_when_process_exit_is_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FlyAISearchAdapter(command="flyai", cache_dir=Path(tmpdir))

            completed = subprocess.CompletedProcess(
                args=["flyai", "keyword-search"],
                returncode=1,
                stdout='{"status":0,"message":"success","data":{"itemList":[]}}',
                stderr="Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)",
            )

            with patch("travel_control_tower.adapters.search_flyai.subprocess.run", return_value=completed):
                payload = adapter._run_json(["keyword-search", "--query", "南京 周末 酒店"])

            self.assertEqual(payload["status"], 0)
            self.assertEqual(adapter.last_error, "")
            self.assertEqual(adapter.last_source, "live")

    def test_run_json_uses_cache_for_same_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FlyAISearchAdapter(command="flyai", cache_dir=Path(tmpdir))
            completed = subprocess.CompletedProcess(
                args=["flyai", "keyword-search"],
                returncode=0,
                stdout='{"status":0,"message":"success","data":{"itemList":[{"info":{"title":"南京酒店","jumpUrl":"https://example.com"}}]}}',
                stderr="",
            )

            with patch("travel_control_tower.adapters.search_flyai.subprocess.run", return_value=completed) as mocked_run:
                payload_one = adapter._run_json(["keyword-search", "--query", "南京 周末 酒店"])
                payload_two = adapter._run_json(["keyword-search", "--query", "南京 周末 酒店"])

            self.assertEqual(mocked_run.call_count, 1)
            self.assertEqual(payload_one["status"], 0)
            self.assertEqual(payload_two["status"], 0)
            self.assertEqual(adapter.last_source, "cache")

    def test_run_json_accepts_empty_result_payload_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FlyAISearchAdapter(command="flyai", cache_dir=Path(tmpdir))
            completed = subprocess.CompletedProcess(
                args=["flyai", "search-flight"],
                returncode=1,
                stdout='{"status":1,"message":"智慧交通结果为空","data":null}',
                stderr="Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)",
            )

            with patch("travel_control_tower.adapters.stable_search_flyai.subprocess.run", return_value=completed):
                payload = adapter._run_json(["search-flight", "--origin", "Shanghai"])

            self.assertEqual(payload["status"], 1)
            self.assertEqual(adapter.last_error, "")
            self.assertIn("结果为空", adapter.last_warning)

    def test_run_json_parses_first_valid_json_object_from_mixed_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FlyAISearchAdapter(command="flyai", cache_dir=Path(tmpdir))
            completed = subprocess.CompletedProcess(
                args=["flyai", "keyword-search"],
                returncode=0,
                stdout='noise {"status":0,"message":"success","data":{"itemList":[]}} trailing {"ignored":true}',
                stderr="",
            )

            with patch("travel_control_tower.adapters.stable_search_flyai.subprocess.run", return_value=completed):
                payload = adapter._run_json(["keyword-search", "--query", "南京 周末 酒店"])

            self.assertEqual(payload["status"], 0)

    def test_search_transport_uses_normalized_english_city_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FlyAISearchAdapter(command="flyai", cache_dir=Path(tmpdir))
            payload = {"status": 0, "message": "success", "data": {"itemList": []}}

            with patch.object(adapter, "_run_json", return_value=payload) as mocked_run:
                adapter.search_transport("上海", "苏州", "2026-04-18", "2026-04-19")

            args = mocked_run.call_args.args[0]
            self.assertIn("Shanghai", args)
            self.assertIn("Suzhou", args)


if __name__ == "__main__":
    unittest.main()
