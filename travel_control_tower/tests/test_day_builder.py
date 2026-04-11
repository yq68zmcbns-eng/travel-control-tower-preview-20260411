from __future__ import annotations

import unittest

from travel_control_tower.adapters.base import POICandidate
from travel_control_tower.planner_core.day_builder import _build_poi_play_note


class DayBuilderTests(unittest.TestCase):
    def test_build_poi_play_note_hides_raw_enum_values(self) -> None:
        note = _build_poi_play_note(
            "山塘老街",
            {
                "山塘老街": POICandidate(
                    name="山塘老街",
                    city_name="苏州",
                    category="古镇古村",
                    poi_level="None",
                    free_status="UNKNOWN",
                    address="江苏省苏州市姑苏区山塘街",
                )
            },
            "第一天先把节奏放稳。",
        )

        self.assertIn("第一天先把节奏放稳。", note)
        self.assertIn("类型：古镇古村", note)
        self.assertIn("位置：江苏省苏州市姑苏区山塘街", note)
        self.assertNotIn("等级：None", note)
        self.assertNotIn("门票：UNKNOWN", note)

    def test_build_poi_play_note_maps_not_free_to_user_facing_copy(self) -> None:
        note = _build_poi_play_note(
            "平江路",
            {
                "平江路": POICandidate(
                    name="平江路",
                    city_name="苏州",
                    category="None",
                    free_status="NOT_FREE",
                )
            },
            "最后半天只保留一个主点。",
        )

        self.assertIn("门票：需购票", note)
        self.assertNotIn("门票：NOT_FREE", note)
        self.assertNotIn("类型：None", note)


if __name__ == "__main__":
    unittest.main()
