from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..planner_core.models import TripRequest


@dataclass
class GenerationJob:
    job_id: str
    status: str
    created_at: float
    updated_at: float
    stage: str = "queued"
    stage_label: str = "等待开始"
    progress: int = 0
    fields: dict[str, str] = field(default_factory=dict)
    request_snapshot: dict[str, Any] = field(default_factory=dict)
    plan_path: str = ""
    html_path: str = ""
    excel_path: str = ""
    error: str = ""

    def to_api_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stage": self.stage,
            "stage_label": self.stage_label,
            "progress": self.progress,
            "request_snapshot": self.request_snapshot,
            "error": self.error,
            "result_url": f"/results/{self.job_id}" if self.status == "succeeded" else "",
            "plan_url": f"/jobs/{self.job_id}/plan" if self.status == "succeeded" else "",
            "excel_url": f"/jobs/{self.job_id}/excel" if self.status == "succeeded" else "",
        }

    def to_record(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stage": self.stage,
            "stage_label": self.stage_label,
            "progress": self.progress,
            "fields": self.fields,
            "request_snapshot": self.request_snapshot,
            "plan_path": self.plan_path,
            "html_path": self.html_path,
            "excel_path": self.excel_path,
            "error": self.error,
        }

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> "GenerationJob":
        return cls(
            job_id=str(payload.get("job_id", "")).strip(),
            status=str(payload.get("status", "pending")).strip() or "pending",
            created_at=float(payload.get("created_at", time.time()) or time.time()),
            updated_at=float(payload.get("updated_at", time.time()) or time.time()),
            stage=str(payload.get("stage", "queued")).strip() or "queued",
            stage_label=str(payload.get("stage_label", "等待开始")).strip() or "等待开始",
            progress=max(0, min(int(payload.get("progress", 0) or 0), 100)),
            fields=dict(payload.get("fields") or {}),
            request_snapshot=dict(payload.get("request_snapshot") or {}),
            plan_path=str(payload.get("plan_path", "") or "").strip(),
            html_path=str(payload.get("html_path", "") or "").strip(),
            excel_path=str(payload.get("excel_path", "") or "").strip(),
            error=str(payload.get("error", "") or "").strip(),
        )


class GenerationJobStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self.base_dir / "jobs.sqlite3"
        self._lock = threading.Lock()
        self._jobs: dict[str, GenerationJob] = {}
        self._init_db()
        self._load_existing_jobs()

    def create(self, fields: dict[str, str], request: TripRequest | None = None) -> GenerationJob:
        timestamp = time.time()
        job_id = uuid.uuid4().hex[:12]
        job = GenerationJob(
            job_id=job_id,
            status="pending",
            created_at=timestamp,
            updated_at=timestamp,
            stage="queued",
            stage_label="等待开始",
            progress=0,
            fields=dict(fields),
            request_snapshot=request.to_dict() if request else {},
        )
        with self._lock:
            self._jobs[job_id] = job
            self._persist_job(job)
        return job

    def get(self, job_id: str) -> GenerationJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                return job
            job = self._load_job_from_db_unlocked(job_id)
            if job:
                self._jobs[job.job_id] = job
                return job
        return self._load_job_from_disk(job_id)

    def mark_running(self, job_id: str) -> GenerationJob | None:
        return self._update(job_id, status="running", error="")

    def set_request_snapshot(self, job_id: str, request: TripRequest) -> GenerationJob | None:
        return self._update(job_id, request_snapshot=request.to_dict())

    def mark_stage(
        self,
        job_id: str,
        *,
        stage: str,
        stage_label: str,
        progress: int,
        status: str | None = None,
    ) -> GenerationJob | None:
        changes: dict[str, Any] = {
            "stage": stage,
            "stage_label": stage_label,
            "progress": max(0, min(int(progress), 100)),
        }
        if status:
            changes["status"] = status
        return self._update(job_id, **changes)

    def mark_failed(self, job_id: str, error: str) -> GenerationJob | None:
        return self._update(
            job_id,
            status="failed",
            stage="failed",
            stage_label="生成失败",
            progress=100,
            error=str(error or "").strip(),
        )

    def mark_succeeded(
        self,
        job_id: str,
        *,
        plan_path: Path,
        html_path: Path,
        excel_path: Path,
    ) -> GenerationJob | None:
        return self._update(
            job_id,
            status="succeeded",
            stage="completed",
            stage_label="已完成",
            progress=100,
            plan_path=str(plan_path),
            html_path=str(html_path),
            excel_path=str(excel_path),
            error="",
        )

    def job_dir(self, job_id: str) -> Path:
        target = self.base_dir / job_id
        target.mkdir(parents=True, exist_ok=True)
        return target

    def plan_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "plan.json"

    def html_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "result.html"

    def excel_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "plan.xlsx"

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()
            with self._connection() as conn:
                conn.execute("DELETE FROM jobs")
                conn.commit()

    def list_jobs(self) -> list[GenerationJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda job: float(job.updated_at or job.created_at or 0.0), reverse=True)

    def latest_successful(self) -> GenerationJob | None:
        with self._lock:
            successful = [job for job in self._jobs.values() if job.status == "succeeded"]
            if successful:
                return max(successful, key=lambda job: float(job.updated_at or job.created_at or 0.0))
            job = self._load_latest_successful_from_db_unlocked()
            if job:
                self._jobs[job.job_id] = job
            return job

    def set_artifact_paths(
        self,
        job_id: str,
        *,
        plan_path: Path | None = None,
        html_path: Path | None = None,
        excel_path: Path | None = None,
    ) -> GenerationJob | None:
        changes: dict[str, Any] = {}
        if plan_path is not None:
            changes["plan_path"] = str(plan_path)
        if html_path is not None:
            changes["html_path"] = str(html_path)
        if excel_path is not None:
            changes["excel_path"] = str(excel_path)
        if not changes:
            return self.get(job_id)
        return self._update(job_id, **changes)

    def prune_expired(self, max_age_seconds: int, *, now: float | None = None) -> list[str]:
        now = float(now or time.time())
        if max_age_seconds <= 0:
            return []
        removed: list[str] = []
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.status not in {"succeeded", "failed"}:
                    continue
                if now - float(job.updated_at or job.created_at or now) < max_age_seconds:
                    continue
                self._jobs.pop(job_id, None)
                removed.append(job_id)
            if removed:
                with self._connection() as conn:
                    conn.executemany("DELETE FROM jobs WHERE job_id = ?", [(job_id,) for job_id in removed])
                    conn.commit()
        for job_id in removed:
            shutil.rmtree(self.base_dir / job_id, ignore_errors=True)
        return removed

    def health_report(self) -> dict[str, Any]:
        writable = False
        error = ""
        try:
            probe_dir = self.base_dir
            probe_dir.mkdir(parents=True, exist_ok=True)
            probe_file = probe_dir / ".write-probe"
            probe_file.write_text("ok", encoding="utf-8")
            probe_file.unlink(missing_ok=True)
            writable = True
        except OSError as exc:
            error = str(exc)

        db_ok = False
        if not error:
            try:
                with self._connection() as conn:
                    conn.execute("SELECT 1")
                db_ok = True
            except sqlite3.DatabaseError as exc:
                error = str(exc)

        return {
            "base_dir": str(self.base_dir),
            "db_path": str(self._db_path),
            "writable": writable,
            "db_ok": db_ok,
            "job_count": len(self.list_jobs()),
            "error": error,
        }

    def _update(self, job_id: str, **changes: Any) -> GenerationJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                job = self._load_job_from_db_unlocked(job_id)
                if not job:
                    return None
                self._jobs[job.job_id] = job
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = time.time()
            self._persist_job(job)
            return job

    def _record_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    record_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_status_updated
                ON jobs(status, updated_at DESC)
                """
            )
            conn.commit()

    def _persist_job(self, job: GenerationJob) -> None:
        payload = json.dumps(job.to_record(), ensure_ascii=False, indent=2)
        record_path = self._record_path(job.job_id)
        record_path.write_text(payload, encoding="utf-8")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO jobs(job_id, status, created_at, updated_at, record_json)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (job.job_id, job.status, job.created_at, job.updated_at, payload),
            )
            conn.commit()

    def _load_existing_jobs(self) -> None:
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute("SELECT record_json FROM jobs ORDER BY updated_at ASC").fetchall()
            for (record_json,) in rows:
                try:
                    payload = json.loads(str(record_json or ""))
                    job = GenerationJob.from_record(payload)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if job.job_id:
                    self._jobs[job.job_id] = job

            for child in self.base_dir.iterdir():
                if not child.is_dir():
                    continue
                record_path = child / "job.json"
                if not record_path.exists():
                    continue
                try:
                    payload = json.loads(record_path.read_text(encoding="utf-8"))
                    job = GenerationJob.from_record(payload)
                except (json.JSONDecodeError, OSError, ValueError, TypeError):
                    continue
                if not job.job_id:
                    continue
                self._jobs[job.job_id] = job
                self._persist_job(job)

    def _load_job_from_db_unlocked(self, job_id: str) -> GenerationJob | None:
        with self._connection() as conn:
            row = conn.execute("SELECT record_json FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(str(row[0] or ""))
            return GenerationJob.from_record(payload)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    def _load_latest_successful_from_db_unlocked(self) -> GenerationJob | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT record_json
                FROM jobs
                WHERE status = 'succeeded'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(str(row[0] or ""))
            return GenerationJob.from_record(payload)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    def _load_job_from_disk(self, job_id: str) -> GenerationJob | None:
        record_path = self.base_dir / job_id / "job.json"
        if not record_path.exists():
            return None
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
            job = GenerationJob.from_record(payload)
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return None
        with self._lock:
            self._jobs[job.job_id] = job
            self._persist_job(job)
        return job


def save_plan_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
