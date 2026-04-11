from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from travel_control_tower.planner_core.models import TripRequest
from travel_control_tower.web.app import render_job_page, render_result_page
from travel_control_tower.web.generation_jobs import GenerationJobStore


class GenerationJobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = GenerationJobStore(Path(self.temp_dir.name))
        self.request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
        )

    def test_create_job_keeps_request_snapshot(self) -> None:
        job = self.store.create({"freeform_request": "上海去南京两天"}, self.request)

        self.assertEqual(job.status, "pending")
        self.assertEqual(job.request_snapshot["departure_city"], "上海")
        self.assertEqual(job.fields["freeform_request"], "上海去南京两天")

    def test_mark_job_succeeded_exposes_result_urls(self) -> None:
        job = self.store.create({}, self.request)
        self.store.mark_stage(job.job_id, stage="planning", stage_label="生成日程", progress=60, status="running")
        plan_path = self.store.plan_path(job.job_id)
        html_path = self.store.html_path(job.job_id)
        excel_path = self.store.excel_path(job.job_id)
        plan_path.write_text("{}", encoding="utf-8")
        html_path.write_text("<html></html>", encoding="utf-8")
        excel_path.write_bytes(b"PK")

        updated = self.store.mark_succeeded(
            job.job_id,
            plan_path=plan_path,
            html_path=html_path,
            excel_path=excel_path,
        )

        self.assertIsNotNone(updated)
        payload = updated.to_api_payload()
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["progress"], 100)
        self.assertEqual(payload["result_url"], f"/results/{job.job_id}")
        self.assertEqual(payload["plan_url"], f"/jobs/{job.job_id}/plan")
        self.assertEqual(payload["excel_url"], f"/jobs/{job.job_id}/excel")

    def test_job_store_reloads_jobs_from_disk_after_restart(self) -> None:
        job = self.store.create({"freeform_request": "上海去南京两天"}, self.request)
        self.store.mark_stage(job.job_id, stage="planning", stage_label="生成日程", progress=60, status="running")

        reloaded_store = GenerationJobStore(Path(self.temp_dir.name))
        reloaded_job = reloaded_store.get(job.job_id)

        self.assertIsNotNone(reloaded_job)
        self.assertEqual(reloaded_job.job_id, job.job_id)
        self.assertEqual(reloaded_job.stage, "planning")
        self.assertEqual(reloaded_job.status, "running")
        self.assertEqual(reloaded_job.fields["freeform_request"], "上海去南京两天")

    def test_job_store_reloads_jobs_from_sqlite_when_job_json_missing(self) -> None:
        job = self.store.create({"freeform_request": "上海去南京两天"}, self.request)
        self.store.mark_stage(job.job_id, stage="planning", stage_label="生成日程", progress=60, status="running")
        (self.store.job_dir(job.job_id) / "job.json").unlink(missing_ok=True)

        reloaded_store = GenerationJobStore(Path(self.temp_dir.name))
        reloaded_job = reloaded_store.get(job.job_id)

        self.assertIsNotNone(reloaded_job)
        self.assertEqual(reloaded_job.job_id, job.job_id)
        self.assertEqual(reloaded_job.stage, "planning")
        self.assertEqual(reloaded_job.status, "running")

    def test_health_report_checks_sqlite_and_storage(self) -> None:
        report = self.store.health_report()

        self.assertTrue(report["writable"])
        self.assertTrue(report["db_ok"])
        self.assertTrue(str(report["db_path"]).endswith("jobs.sqlite3"))
        self.assertEqual(report["job_count"], 0)

    def test_prune_expired_removes_old_completed_jobs(self) -> None:
        job = self.store.create({}, self.request)
        plan_path = self.store.plan_path(job.job_id)
        html_path = self.store.html_path(job.job_id)
        excel_path = self.store.excel_path(job.job_id)
        plan_path.write_text("{}", encoding="utf-8")
        html_path.write_text("<html></html>", encoding="utf-8")
        excel_path.write_bytes(b"PK")
        self.store.mark_succeeded(job.job_id, plan_path=plan_path, html_path=html_path, excel_path=excel_path)

        removed = self.store.prune_expired(10, now=job.updated_at + 11)

        self.assertEqual(removed, [job.job_id])
        self.assertIsNone(self.store.get(job.job_id))
        self.assertFalse((Path(self.temp_dir.name) / job.job_id).exists())

    def test_latest_successful_returns_most_recent_finished_job(self) -> None:
        older = self.store.create({}, self.request)
        newer = self.store.create({}, self.request)
        for current in (older, newer):
            plan_path = self.store.plan_path(current.job_id)
            html_path = self.store.html_path(current.job_id)
            excel_path = self.store.excel_path(current.job_id)
            plan_path.write_text("{}", encoding="utf-8")
            html_path.write_text("<html></html>", encoding="utf-8")
            excel_path.write_bytes(b"PK")
            self.store.mark_succeeded(
                current.job_id,
                plan_path=plan_path,
                html_path=html_path,
                excel_path=excel_path,
            )

        latest = self.store.latest_successful()

        self.assertIsNotNone(latest)
        self.assertEqual(latest.job_id, newer.job_id)

    def test_render_job_page_contains_polling_endpoint(self) -> None:
        job = self.store.create({"freeform_request": "上海去南京两天"}, self.request)
        self.store.mark_stage(job.job_id, stage="planning", stage_label="生成逐日行程", progress=60, status="running")
        job = self.store.get(job.job_id)

        html = render_job_page(job)

        self.assertIn(f"/api/jobs/{job.job_id}", html)
        self.assertIn("正在生成旅行方案", html)
        self.assertIn("后台步骤", html)
        self.assertIn("上海去南京两天", html)
        self.assertIn("生成逐日行程", html)

    def test_render_result_page_uses_job_specific_links(self) -> None:
        html = render_result_page(
            {
                "overview": {"title": "测试方案", "summary": "摘要"},
                "input_snapshot": {},
                "assumptions": [],
                "daily_plan": [],
                "budget": {"fixed_cost_total": 0, "per_person_cost": 0, "optional_upgrade_total": 0, "breakdown": []},
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
            },
            job_id="job123",
        )

        self.assertIn("/jobs/job123/plan", html)
        self.assertIn("/jobs/job123/excel", html)
        self.assertIn("/results/job123", html)


if __name__ == "__main__":
    unittest.main()
