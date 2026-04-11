from __future__ import annotations

from datetime import date
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from travel_control_tower.planner_core.models import TripRequest
from travel_control_tower.runtime_config import RuntimeConfig
from travel_control_tower.web import form_ui
from travel_control_tower.web.app import (
    _build_ready_payload,
    _build_request_context,
    _ensure_job_excel_path,
    _ensure_job_html_path,
    _ensure_latest_excel_path,
    _ensure_latest_html_path,
    _ensure_latest_plan_payload,
    _build_planning_agent,
    _preview_rate_limit_decision,
    _start_generation_job,
    RATE_LIMIT_STATE,
    _preview_access_cookie_matches,
    _strip_query_param,
    default_form_values,
    parse_trip_request,
    render_form_page,
    render_job_page,
    render_preview_access_page,
    render_result_page,
)
from travel_control_tower.web.generation_jobs import GenerationJobStore


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        RATE_LIMIT_STATE.clear()
        self.request_resolution_config_patch = patch(
            "travel_control_tower.planner_core.request_resolution.load_runtime_config",
            return_value=RuntimeConfig(
                request_parser_mode="rule",
                openai_api_key="",
                codex_cmd="codex",
            ),
        )
        self.request_resolution_config_patch.start()

    def tearDown(self) -> None:
        self.request_resolution_config_patch.stop()

    @staticmethod
    def _sample_plan() -> dict:
        return {
            "overview": {"title": "测试方案", "summary": "摘要"},
            "input_snapshot": {},
            "assumptions": [],
            "daily_plan": [],
            "budget": {
                "fixed_cost_total": 0,
                "per_person_cost": 0,
                "optional_upgrade_total": 0,
                "breakdown": [],
            },
            "booking_items": [],
            "planning_trace": None,
            "provider_statuses": [],
            "selected_hotel": None,
            "selected_transport": None,
            "hotel_candidates": [],
            "transport_candidates": [],
            "poi_candidates": [],
            "price_scan_summary": None,
            "price_scan_candidates": [],
            "open_questions": [],
        }

    @patch("travel_control_tower.web.app.CodexExecPlanningAgent")
    @patch("travel_control_tower.web.app.load_runtime_config")
    def test_build_planning_agent_auto_mode_stays_on_candidate_without_api_key(self, mock_load_runtime_config, mock_codex_agent) -> None:
        mock_load_runtime_config.return_value = RuntimeConfig(
            openai_api_key="",
            planner_mode="auto",
            codex_cmd="codex",
            codex_planner_model="",
        )
        fake_agent = Mock()
        fake_agent.is_available.return_value = True
        mock_codex_agent.return_value = fake_agent

        agent = _build_planning_agent()

        self.assertIsNone(agent)
        mock_codex_agent.assert_not_called()

    @patch("travel_control_tower.web.app.CodexExecPlanningAgent")
    @patch("travel_control_tower.web.app.load_runtime_config")
    def test_build_planning_agent_uses_codex_when_explicitly_enabled(self, mock_load_runtime_config, mock_codex_agent) -> None:
        mock_load_runtime_config.return_value = RuntimeConfig(
            openai_api_key="",
            planner_mode="codex",
            codex_cmd="codex",
            codex_planner_model="",
        )
        fake_agent = Mock()
        fake_agent.is_available.return_value = True
        mock_codex_agent.return_value = fake_agent

        agent = _build_planning_agent()

        self.assertIs(agent, fake_agent)
        mock_codex_agent.assert_called_once()

    def test_default_form_uses_natural_language_as_main_input(self) -> None:
        values = default_form_values()
        self.assertEqual(values["freeform_request"], "")
        self.assertEqual(values["scenario_id"], "")
        self.assertEqual(values["destination"], "")
        self.assertEqual(values["traveler_count"], "1")

    def test_parse_trip_request_requires_freeform(self) -> None:
        with self.assertRaises(ValueError):
            parse_trip_request({"freeform_request": ""})

    def test_parse_trip_request_handles_multiline_fields(self) -> None:
        request = parse_trip_request(
            {
                "freeform_request": "下周末从上海去大阪三天，预算4200，想逛道顿堀和大阪城。",
                "departure_city": "上海",
                "destination": "大阪",
                "start_date": "2026-06-09",
                "end_date": "2026-06-11",
                "traveler_count": "2",
                "budget_per_person": "4200",
                "must_go": "道顿堀\n大阪城",
                "hotel_preferences": "干净\n近地铁",
                "transport_preferences": "铁路\n步行",
                "notes": "测试",
            }
        )
        self.assertEqual(request.traveler_count, 2)
        self.assertEqual(request.budget_per_person, 4200.0)
        self.assertEqual(request.must_go, ["道顿堀", "大阪城"])
        self.assertEqual(request.hotel_preferences, ["干净", "近地铁"])

    def test_parse_trip_request_accepts_user_supplied_hotel_and_transport(self) -> None:
        request = parse_trip_request(
            {
                "freeform_request": "上海去大阪3天，机酒我已经订好了，帮我排详细行程。",
                "departure_city": "上海",
                "destination": "大阪",
                "start_date": "2026-06-09",
                "end_date": "2026-06-11",
                "traveler_count": "1",
                "budget_per_person": "3800",
                "user_hotel_name": "KOKO HOTEL Osaka Shinsaibashi",
                "user_hotel_area": "心斋桥",
                "user_hotel_nightly_price": "386",
                "user_hotel_url": "https://example.com/hotel",
                "user_transport_label": "MM080 + MM079",
                "user_transport_category": "往返机票",
                "user_transport_total_price": "1558",
                "user_transport_depart_at": "2026-06-09 06:15",
                "user_transport_arrive_at": "2026-06-11 00:05",
                "user_arrival_at_destination": "2026-06-09 09:35",
                "user_return_depart_at": "2026-06-11 22:20",
                "user_transport_url": "https://example.com/flight",
                "enable_live_search": "on",
            }
        )
        self.assertTrue(request.enable_live_search)
        self.assertEqual(request.user_hotel_name, "KOKO HOTEL Osaka Shinsaibashi")
        self.assertEqual(request.user_hotel_area, "心斋桥")
        self.assertEqual(request.user_hotel_nightly_price, 386.0)
        self.assertEqual(request.user_transport_label, "MM080 + MM079")
        self.assertEqual(request.user_transport_total_price, 1558.0)

    def test_render_form_contains_required_freeform_and_optional_constraints(self) -> None:
        text = render_form_page()
        self.assertIn('action="/generate"', text)
        self.assertIn("自然语言需求", text)
        self.assertIn('name="departure_city"', text)
        self.assertIn("required", text)
        self.assertIn("展开补充约束", text)
        self.assertIn("/api/reverse-geocode", text)
        self.assertIn("启用实时机酒搜索", text)
        self.assertIn("必须去的点", text)
        self.assertIn("template.replaceAll('{departure}', departureValue)", text)

    def test_parse_trip_request_requires_departure_for_nearby_city_prompt(self) -> None:
        with self.assertRaises(ValueError):
            parse_trip_request(
                {
                    "freeform_request": "下周末去附近城市玩两天，预算2000，想轻松一点。",
                    "budget_per_person": "2000",
                },
                today=date(2026, 4, 8),
            )

    def test_parse_trip_request_prefers_freeform_over_blank_defaults(self) -> None:
        fields = default_form_values()
        fields["freeform_request"] = "下周末上海去南京两天，预算2000，想轻松一点，想去夫子庙和中山陵。"

        request = parse_trip_request(fields, today=date(2026, 4, 7))

        self.assertEqual(request.departure_city, "上海")
        self.assertEqual(request.destination, "南京")
        self.assertEqual(request.start_date, "2026-04-11")
        self.assertEqual(request.end_date, "2026-04-12")
        self.assertEqual(request.must_go, ["夫子庙", "中山陵"])
        self.assertEqual(request.budget_per_person, 2000.0)
        self.assertEqual(request.travel_style, "relaxed")

    def test_parse_trip_request_supports_price_scan_prompt(self) -> None:
        fields = default_form_values()
        fields["freeform_request"] = "未来3个月上海去东京待3天，想看低价机票，预算5000"

        request = parse_trip_request(fields, today=date(2026, 4, 8))

        self.assertEqual(request.request_mode, "price_scan")
        self.assertEqual(request.destination, "东京")
        self.assertEqual(request.flexible_window_start, "2026-04-08")
        self.assertEqual(request.flexible_window_end, "2026-07-07")
        self.assertEqual(request.target_trip_days, 3)
        self.assertEqual(request.price_priority, "low")

    def test_parse_trip_request_can_autofill_destination_for_relaxed_weekend_template(self) -> None:
        fields = default_form_values()
        fields["freeform_request"] = "下周末从上海出发，去附近城市玩两天，预算2000，节奏轻松一点，想吃当地特色，不要太赶。"
        fields["departure_city"] = "上海"
        fields["enable_live_search"] = "on"

        request = parse_trip_request(fields, today=date(2026, 4, 8))

        self.assertEqual(request.departure_city, "上海")
        self.assertEqual(request.destination, "苏州")
        self.assertTrue(request.enable_live_search)

    def test_parse_trip_request_structured_fields_override_freeform(self) -> None:
        fields = default_form_values()
        fields["freeform_request"] = "下周末从上海去南京两天，预算2000，想轻松一点。"
        fields["destination"] = "苏州"
        fields["budget_per_person"] = "2600"
        fields["travel_style"] = "packed"

        request = parse_trip_request(fields, today=date(2026, 4, 8))

        self.assertEqual(request.departure_city, "上海")
        self.assertEqual(request.destination, "苏州")
        self.assertEqual(request.budget_per_person, 2600.0)
        self.assertEqual(request.travel_style, "packed")

    def test_build_request_context_formats_internal_values_for_display(self) -> None:
        fields = default_form_values()
        fields["freeform_request"] = "下周末从上海出发，去附近城市玩两天。"
        fields["travel_style"] = "relaxed"
        fields["enable_live_search"] = "on"

        context = _build_request_context(fields)

        self.assertEqual(context["natural_language_request"], "下周末从上海出发，去附近城市玩两天。")
        self.assertIn("启用实时机酒搜索", context["manual_constraints"])
        self.assertIn("节奏偏好：松弛", context["manual_constraints"])
        self.assertNotIn("节奏偏好：relaxed", context["manual_constraints"])

    def test_render_result_page_formats_trace_and_snapshot_values(self) -> None:
        plan = self._sample_plan()
        plan["planning_trace"] = {
            "engine": "候选池规划器",
            "mode": "candidate",
            "model": "",
            "used_fallback": False,
            "details": "测试",
        }
        plan["request_context"] = {
            "natural_language_request": "下周末从上海出发，去附近城市玩两天。",
            "manual_constraints": ["节奏偏好：松弛"],
        }
        plan["input_snapshot"] = {
            "节奏": "relaxed",
            "需求模式": "itinerary",
            "已知酒店": "",
        }
        plan["poi_candidates"] = [
            {
                "name": "平江路",
                "category": "None",
                "notes": "门票：需购票；地址：江苏省苏州市姑苏区平江路",
            }
        ]

        html = render_result_page(plan, job_id="job123")

        self.assertIn("自动规划", html)
        self.assertIn("实时候选优先", html)
        self.assertNotIn(">candidate<", html)
        self.assertIn("松弛", html)
        self.assertNotIn(">relaxed<", html)
        self.assertNotIn("已知酒店", html)
        self.assertNotIn(">None<", html)

    def test_render_job_page_uses_human_status_and_sync_targets(self) -> None:
        job = Mock(
            job_id="job123",
            status="running",
            stage_label="解析输入与约束",
            progress=8,
            request_snapshot={"departure_city": "上海", "destination": "苏州", "traveler_count": 2, "budget_per_person": 2000},
            fields={"freeform_request": "测试需求"},
        )

        html = render_job_page(job)

        self.assertIn("处理中", html)
        self.assertIn("hero-stage-label", html)
        self.assertIn("hero-progress-text", html)
        self.assertIn("statusLabels", html)
        self.assertIn("job-route-summary", html)
        self.assertIn("job-party-summary", html)
        self.assertIn("formatRouteSummary", html)
        self.assertIn("formatPartySummary", html)
        self.assertIn("上海 → 苏州", html)
        self.assertIn("2 人 / ¥2000", html)

    def test_build_preview_request_uses_rule_mode_seed(self) -> None:
        fields = default_form_values()
        fields["freeform_request"] = "下周末从上海出发，去附近城市玩两天，预算2000，节奏轻松一点。"
        fields["departure_city"] = "上海"
        fields["enable_live_search"] = "on"

        request = form_ui.build_preview_request(fields, today=date(2026, 4, 8))

        self.assertIsNotNone(request)
        self.assertEqual(request.departure_city, "上海")
        self.assertEqual(request.destination, "苏州")
        self.assertEqual(request.start_date, "2026-04-11")
        self.assertEqual(request.end_date, "2026-04-12")
        self.assertTrue(request.enable_live_search)

    def test_start_generation_job_bubbles_preview_validation_error(self) -> None:
        fields = default_form_values()
        fields["freeform_request"] = "下周末去附近城市玩两天，预算2000，想轻松一点。"
        fields["budget_per_person"] = "2000"

        with self.assertRaises(ValueError):
            _start_generation_job(fields)

    @patch("travel_control_tower.web.app.threading.Thread")
    @patch("travel_control_tower.web.app.form_ui.build_preview_request")
    @patch("travel_control_tower.web.app.JOB_STORE.create")
    @patch("travel_control_tower.web.app.JOB_STORE.prune_expired")
    @patch("travel_control_tower.web.app.load_runtime_config")
    def test_start_generation_job_seeds_preview_request_for_initial_page(
        self,
        mock_load_runtime_config,
        mock_prune_expired,
        mock_create,
        mock_build_preview_request,
        mock_thread,
    ) -> None:
        preview_request = TripRequest(
            departure_city="上海",
            destination="苏州",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=2,
            budget_per_person=2000.0,
        )
        fake_job = Mock(job_id="job123")
        fake_thread = Mock()
        mock_load_runtime_config.return_value = RuntimeConfig(preview_job_retention_hours=72)
        mock_build_preview_request.return_value = preview_request
        mock_create.return_value = fake_job
        mock_thread.return_value = fake_thread

        returned = _start_generation_job({"freeform_request": "测试需求"}, request=None)

        self.assertIs(returned, fake_job)
        mock_prune_expired.assert_called_once()
        mock_build_preview_request.assert_called_once_with({"freeform_request": "测试需求"})
        mock_create.assert_called_once_with({"freeform_request": "测试需求"}, preview_request)
        fake_thread.start.assert_called_once()

    def test_preview_access_cookie_matches_expected_token(self) -> None:
        cookie_header = "travel_preview_access=secret-token; Path=/"
        self.assertTrue(_preview_access_cookie_matches(cookie_header, "secret-token"))
        self.assertFalse(_preview_access_cookie_matches(cookie_header, "wrong-token"))

    def test_strip_query_param_removes_preview_token_only(self) -> None:
        cleaned = _strip_query_param("/results/abc?preview_token=secret&foo=bar", "preview_token")
        self.assertEqual(cleaned, "/results/abc?foo=bar")

    def test_render_preview_access_page_contains_login_form(self) -> None:
        html = render_preview_access_page(next_path="/results/demo", error="口令不正确")
        self.assertIn('action="/preview-login"', html)
        self.assertIn('name="preview_token"', html)
        self.assertIn("口令不正确", html)
        self.assertIn("/results/demo", html)

    @patch("travel_control_tower.web.app.JOB_STORE.health_report")
    @patch("travel_control_tower.web.app.load_runtime_config")
    def test_build_ready_payload_reports_public_preview_warnings(self, mock_load_runtime_config, mock_health_report) -> None:
        mock_load_runtime_config.return_value = RuntimeConfig(
            preview_access_token="",
            preview_rate_limit_count=0,
            preview_rate_limit_window_seconds=0,
            amap_web_key="",
            google_maps_api_key="",
            flyai_cmd="",
            planner_mode="auto",
            request_parser_mode="auto",
        )
        mock_health_report.return_value = {
            "base_dir": "jobs",
            "db_path": "jobs.sqlite3",
            "writable": True,
            "db_ok": True,
            "job_count": 2,
            "error": "",
        }

        payload, ready = _build_ready_payload()

        self.assertTrue(ready)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["job_store"]["job_count"], 2)
        self.assertFalse(payload["config"]["preview_access_token_configured"])
        self.assertFalse(payload["config"]["preview_rate_limit_enabled"])
        warnings = "\n".join(payload["warnings"])
        self.assertIn("未设置预览访问口令", warnings)
        self.assertIn("未开启提交限流", warnings)

    def test_preview_rate_limit_blocks_after_limit(self) -> None:
        allowed_first, retry_first = _preview_rate_limit_decision("127.0.0.1", now=1000.0, limit_count=2, window_seconds=60)
        allowed_second, retry_second = _preview_rate_limit_decision("127.0.0.1", now=1010.0, limit_count=2, window_seconds=60)
        allowed_third, retry_third = _preview_rate_limit_decision("127.0.0.1", now=1020.0, limit_count=2, window_seconds=60)

        self.assertTrue(allowed_first)
        self.assertEqual(retry_first, 0)
        self.assertTrue(allowed_second)
        self.assertEqual(retry_second, 0)
        self.assertFalse(allowed_third)
        self.assertGreater(retry_third, 0)

    def test_missing_job_artifacts_can_be_rebuilt_from_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = GenerationJobStore(Path(temp_dir) / "jobs")
            request = TripRequest(
                departure_city="上海",
                destination="南京",
                start_date="2026-04-11",
                end_date="2026-04-12",
                traveler_count=1,
                budget_per_person=2000,
            )
            job = store.create({}, request)
            plan_path = store.plan_path(job.job_id)
            plan_path.write_text(json.dumps(self._sample_plan(), ensure_ascii=False), encoding="utf-8")
            html_path = store.html_path(job.job_id)
            excel_path = store.excel_path(job.job_id)
            store.mark_succeeded(job.job_id, plan_path=plan_path, html_path=html_path, excel_path=excel_path)

            with patch("travel_control_tower.web.app.JOB_STORE", store):
                rebuilt_html = _ensure_job_html_path(store.get(job.job_id))
                rebuilt_excel = _ensure_job_excel_path(store.get(job.job_id))

            self.assertIsNotNone(rebuilt_html)
            self.assertTrue(rebuilt_html.exists())
            self.assertIsNotNone(rebuilt_excel)
            self.assertTrue(rebuilt_excel.exists())

    def test_latest_exports_fall_back_to_latest_successful_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = GenerationJobStore(root / "jobs")
            request = TripRequest(
                departure_city="上海",
                destination="南京",
                start_date="2026-04-11",
                end_date="2026-04-12",
                traveler_count=1,
                budget_per_person=2000,
            )
            job = store.create({}, request)
            plan_path = store.plan_path(job.job_id)
            plan_path.write_text(json.dumps(self._sample_plan(), ensure_ascii=False), encoding="utf-8")
            html_path = store.html_path(job.job_id)
            excel_path = store.excel_path(job.job_id)
            store.mark_succeeded(job.job_id, plan_path=plan_path, html_path=html_path, excel_path=excel_path)

            latest_plan = root / "latest.plan.json"
            latest_html = root / "latest.preview.html"
            latest_excel = root / "latest.xlsx"
            with patch("travel_control_tower.web.app.JOB_STORE", store), patch(
                "travel_control_tower.web.app.LATEST_PLAN_PATH",
                latest_plan,
            ), patch(
                "travel_control_tower.web.app.LATEST_HTML_PATH",
                latest_html,
            ), patch(
                "travel_control_tower.web.app.LATEST_XLSX_PATH",
                latest_excel,
            ):
                payload = _ensure_latest_plan_payload()
                rebuilt_html = _ensure_latest_html_path()
                rebuilt_excel = _ensure_latest_excel_path()

            self.assertEqual(payload["overview"]["title"], "测试方案")
            self.assertIsNotNone(rebuilt_html)
            self.assertTrue(rebuilt_html.exists())
            self.assertIsNotNone(rebuilt_excel)
            self.assertTrue(rebuilt_excel.exists())


if __name__ == "__main__":
    unittest.main()
