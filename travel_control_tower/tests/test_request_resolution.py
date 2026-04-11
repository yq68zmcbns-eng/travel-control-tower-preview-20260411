from __future__ import annotations

from datetime import date
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from travel_control_tower.planner_core.intake_parser import ParsedRequest
from travel_control_tower.planner_core.request_resolution import (
    CodexRequestSlotExtractor,
    ExplicitFormOverrides,
    _build_llm_request_extractor,
    resolve_trip_request,
)
from travel_control_tower.runtime_config import RuntimeConfig


class _FakeExtractor:
    def __init__(self, parsed: ParsedRequest) -> None:
        self.parsed = parsed
        self.called = False

    def is_available(self) -> bool:
        return True

    def extract(self, raw: str, *, today=None) -> ParsedRequest:
        self.called = True
        return self.parsed


class RequestResolutionTests(unittest.TestCase):
    def test_build_llm_request_extractor_auto_prefers_codex_without_openai_key(self) -> None:
        extractor = _build_llm_request_extractor(
            RuntimeConfig(
                openai_api_key="",
                request_parser_mode="auto",
                codex_cmd="codex",
            )
        )
        self.assertIsInstance(extractor, CodexRequestSlotExtractor)

    def test_codex_request_slot_extractor_parses_cli_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "auth.json"
            auth_file.write_text("{}", encoding="utf-8")

            def fake_runner(command, **kwargs):
                output_path = Path(command[command.index("-o") + 1])
                output_path.write_text(
                    '{"departure_city":"上海","destination":"苏州","start_date":"2026-04-11","end_date":"2026-04-12","target_trip_days":2,"target_trip_nights":1,"budget_per_person":2000,"travel_style":"relaxed","request_mode":"itinerary","price_priority":"balanced","must_go":[],"hotel_preferences":[],"transport_preferences":[]}',
                    encoding="utf-8",
                )
                return CompletedProcess(command, 0, stdout="", stderr="")

            extractor = CodexRequestSlotExtractor(
                codex_cmd=str(Path(temp_dir) / "codex.cmd"),
                auth_file=auth_file,
                runner=fake_runner,
            )
            Path(extractor.codex_cmd).write_text("@echo off", encoding="utf-8")

            parsed = extractor.extract("下周末从上海出发，去附近城市玩两天。", today=date(2026, 4, 8))

            self.assertEqual(parsed.departure_city, "上海")
            self.assertEqual(parsed.destination, "苏州")
            self.assertEqual(parsed.start_date, "2026-04-11")
            self.assertEqual(parsed.end_date, "2026-04-12")

    def test_auto_mode_uses_llm_backfill_when_rule_slots_still_missing(self) -> None:
        extractor = _FakeExtractor(
            ParsedRequest(
                departure_city="上海",
                destination="苏州",
                start_date="2026-04-11",
                end_date="2026-04-12",
                target_trip_days=2,
                target_trip_nights=1,
                budget_per_person=2000.0,
                travel_style="relaxed",
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir) / "request-parser-cache"
            with patch("travel_control_tower.planner_core.request_resolution.REQUEST_PARSE_CACHE_DIR", cache_root):
                request = resolve_trip_request(
                    freeform_text="下周末想去一个适合放松的城市玩两天，预算2000，节奏轻松一点。",
                    overrides=ExplicitFormOverrides(enable_live_search=True),
                    today=date(2026, 4, 8),
                    llm_extractor=extractor,
                    runtime_config=RuntimeConfig(request_parser_mode="auto"),
                )

        self.assertTrue(extractor.called)
        self.assertEqual(request.departure_city, "上海")
        self.assertEqual(request.destination, "苏州")
        self.assertEqual(request.start_date, "2026-04-11")
        self.assertEqual(request.end_date, "2026-04-12")

    def test_auto_mode_skips_llm_when_rule_parse_plus_autofill_is_enough(self) -> None:
        extractor = _FakeExtractor(
            ParsedRequest(
                departure_city="上海",
                destination="南京",
            )
        )

        request = resolve_trip_request(
            freeform_text="下周末从上海出发，去附近城市玩两天，预算2000，节奏轻松一点。",
            overrides=ExplicitFormOverrides(),
            today=date(2026, 4, 8),
            llm_extractor=extractor,
            runtime_config=RuntimeConfig(request_parser_mode="auto"),
        )

        self.assertFalse(extractor.called)
        self.assertEqual(request.departure_city, "上海")
        self.assertEqual(request.destination, "苏州")
        self.assertEqual(request.start_date, "2026-04-11")
        self.assertEqual(request.end_date, "2026-04-12")

    def test_auto_mode_skips_llm_when_rule_parse_is_enough(self) -> None:
        extractor = _FakeExtractor(
            ParsedRequest(
                departure_city="上海",
                destination="苏州",
            )
        )

        request = resolve_trip_request(
            freeform_text="下周末从上海去南京两天，预算2000，想轻松一点。",
            overrides=ExplicitFormOverrides(),
            today=date(2026, 4, 8),
            llm_extractor=extractor,
            runtime_config=RuntimeConfig(request_parser_mode="auto"),
        )

        self.assertFalse(extractor.called)
        self.assertEqual(request.destination, "南京")

    def test_auto_mode_skips_llm_when_structured_overrides_already_cover_missing_slots(self) -> None:
        extractor = _FakeExtractor(
            ParsedRequest(
                departure_city="上海",
                destination="苏州",
            )
        )

        request = resolve_trip_request(
            freeform_text="去附近城市玩两天，预算2000，想轻松一点。",
            overrides=ExplicitFormOverrides(
                departure_city="上海",
                destination="苏州",
                budget_per_person="2000",
            ),
            today=date(2026, 4, 8),
            llm_extractor=extractor,
            runtime_config=RuntimeConfig(request_parser_mode="auto"),
        )

        self.assertFalse(extractor.called)
        self.assertEqual(request.departure_city, "上海")
        self.assertEqual(request.destination, "苏州")
        self.assertEqual(request.budget_per_person, 2000.0)

    def test_auto_mode_skips_llm_when_departure_override_is_enough_for_autofill(self) -> None:
        extractor = _FakeExtractor(
            ParsedRequest(
                departure_city="上海",
                destination="南京",
            )
        )

        request = resolve_trip_request(
            freeform_text="去附近城市玩两天，预算2000，想轻松一点。",
            overrides=ExplicitFormOverrides(
                departure_city="上海",
                budget_per_person="2000",
            ),
            today=date(2026, 4, 8),
            llm_extractor=extractor,
            runtime_config=RuntimeConfig(request_parser_mode="auto"),
        )

        self.assertFalse(extractor.called)
        self.assertEqual(request.departure_city, "上海")
        self.assertEqual(request.destination, "苏州")
        self.assertEqual(request.budget_per_person, 2000.0)

    def test_resolve_trip_request_requires_explicit_departure_for_nearby_city_templates(self) -> None:
        extractor = _FakeExtractor(
            ParsedRequest(
                departure_city="上海",
                destination="苏州",
            )
        )

        with self.assertRaises(ValueError):
            resolve_trip_request(
                freeform_text="下周末去附近城市玩两天，预算2000，想轻松一点。",
                overrides=ExplicitFormOverrides(),
                today=date(2026, 4, 8),
                llm_extractor=extractor,
                runtime_config=RuntimeConfig(request_parser_mode="auto"),
            )

        self.assertFalse(extractor.called)

    def test_structured_overrides_still_win_after_llm_extract(self) -> None:
        extractor = _FakeExtractor(
            ParsedRequest(
                departure_city="上海",
                destination="南京",
                start_date="2026-04-11",
                end_date="2026-04-12",
                budget_per_person=2000.0,
                travel_style="relaxed",
            )
        )

        request = resolve_trip_request(
            freeform_text="下周末从上海去一个适合周末的城市玩两天，预算2000，轻松一点。",
            overrides=ExplicitFormOverrides(
                destination="苏州",
                budget_per_person="2600",
                travel_style="packed",
            ),
            today=date(2026, 4, 8),
            llm_extractor=extractor,
            runtime_config=RuntimeConfig(request_parser_mode="always"),
        )

        self.assertEqual(request.destination, "苏州")
        self.assertEqual(request.budget_per_person, 2600.0)
        self.assertEqual(request.travel_style, "packed")

    def test_auto_mode_reuses_cached_llm_parse_result(self) -> None:
        extractor = _FakeExtractor(
            ParsedRequest(
                departure_city="上海",
                destination="苏州",
                start_date="2026-04-18",
                end_date="2026-04-19",
                target_trip_days=2,
                target_trip_nights=1,
                budget_per_person=2000.0,
                travel_style="relaxed",
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir) / "request-parser-cache"
            with patch("travel_control_tower.planner_core.request_resolution.REQUEST_PARSE_CACHE_DIR", cache_root):
                request1 = resolve_trip_request(
                    freeform_text="下周末想去一个适合放松的城市玩两天，预算2000，节奏轻松一点。",
                    overrides=ExplicitFormOverrides(),
                    today=date(2026, 4, 11),
                    llm_extractor=extractor,
                    runtime_config=RuntimeConfig(request_parser_mode="auto"),
                )
                self.assertTrue(extractor.called)
                extractor.called = False

                request2 = resolve_trip_request(
                    freeform_text="下周末想去一个适合放松的城市玩两天，预算2000，节奏轻松一点。",
                    overrides=ExplicitFormOverrides(),
                    today=date(2026, 4, 11),
                    llm_extractor=extractor,
                    runtime_config=RuntimeConfig(request_parser_mode="auto"),
                )

        self.assertEqual(request1.destination, "苏州")
        self.assertEqual(request2.destination, "苏州")
        self.assertFalse(extractor.called)


if __name__ == "__main__":
    unittest.main()
