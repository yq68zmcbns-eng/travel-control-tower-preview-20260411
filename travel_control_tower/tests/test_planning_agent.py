from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from travel_control_tower.planner_core.models import TripRequest
from travel_control_tower.planner_core.planning_agent import CodexExecPlanningAgent, PlanningContext


class PlanningAgentTests(unittest.TestCase):
    def _build_context(self) -> PlanningContext:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            days=2,
            nights=1,
            must_go=["夫子庙", "中山陵"],
        )
        return PlanningContext(
            request=request,
            selected_hotel=None,
            selected_transport=None,
            hotel_candidates=[],
            transport_candidates=[],
            poi_candidates=[],
        )

    def test_codex_exec_planning_agent_is_available_requires_cli_and_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "auth.json"
            auth_file.write_text("{}", encoding="utf-8")
            agent = CodexExecPlanningAgent(auth_file=auth_file)
            with patch.object(agent, "_resolve_command", return_value="codex.cmd"):
                self.assertTrue(agent.is_available())
            with patch.object(agent, "_resolve_command", return_value=""):
                self.assertFalse(agent.is_available())

    def test_codex_exec_planning_agent_can_parse_output_file(self) -> None:
        captured: dict[str, object] = {}

        def fake_runner(*args, **kwargs):
            command = list(args[0])
            captured["command"] = command
            captured["input"] = kwargs.get("input")
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text(
                json.dumps(
                    {
                        "daily_plan": [
                            {
                                "day_index": 1,
                                "theme": "秦淮河主线",
                                "why_this_day": "抵达后先走核心片区",
                                "transport_strategy": "地铁+步行",
                                "meal_strategy": "景点附近解决简餐",
                                "fallback_if_fast": "补一个小景点",
                                "fallback_if_tired": "提前回酒店",
                                "items": [
                                    {"label": "前往 夫子庙", "category": "交通", "duration_minutes": 30, "notes": ""},
                                    {"label": "夫子庙慢逛", "category": "游玩", "duration_minutes": 120, "notes": ""},
                                ],
                            },
                            {
                                "day_index": 2,
                                "theme": "中山陵主线",
                                "why_this_day": "返程日前往大景点",
                                "transport_strategy": "单线移动",
                                "meal_strategy": "返程前简餐",
                                "fallback_if_fast": "补一个轻点位",
                                "fallback_if_tired": "直接返程",
                                "items": [
                                    {"label": "前往 中山陵", "category": "交通", "duration_minutes": 35, "notes": ""},
                                    {"label": "中山陵游览", "category": "游玩", "duration_minutes": 150, "notes": ""},
                                ],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "auth.json"
            auth_file.write_text("{}", encoding="utf-8")
            agent = CodexExecPlanningAgent(auth_file=auth_file, runner=fake_runner)
            daily_plan, trace = agent.plan(self._build_context())

        self.assertEqual(trace.engine, "智能规划")
        self.assertEqual(trace.mode, "llm")
        self.assertEqual(len(daily_plan), 2)
        self.assertEqual(daily_plan[0].theme, "秦淮河主线")
        self.assertIn("--ephemeral", captured["command"])
        self.assertIn("只输出一个 JSON 对象", str(captured["input"]))


if __name__ == "__main__":
    unittest.main()
