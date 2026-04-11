from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG_PATH = Path.home() / ".codex" / "travel-control-tower.json"


@dataclass
class RuntimeConfig:
    google_maps_api_key: str = ""
    amap_web_key: str = ""
    flyai_cmd: str = ""
    codex_cmd: str = "codex"
    codex_planner_model: str = ""
    openai_api_key: str = ""
    request_parser_mode: str = "auto"
    request_parser_model: str = ""
    planner_model: str = "gpt-4.1-mini"
    planner_mode: str = "auto"
    preview_access_token: str = ""
    preview_rate_limit_count: int = 6
    preview_rate_limit_window_seconds: int = 600
    preview_job_retention_hours: int = 72
    web_data_dir: str = ""
    preview_port: int = 8766
    web_port: int = 8770


def load_runtime_config(config_path: Path | None = None) -> RuntimeConfig:
    config_path = config_path or DEFAULT_CONFIG_PATH
    file_data: dict[str, str] = {}
    platform_port = int(os.environ.get("PORT", 0) or 0)

    if config_path.exists():
        try:
            file_data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            file_data = {}

    return RuntimeConfig(
        google_maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        or str(file_data.get("google_maps_api_key", "")).strip(),
        amap_web_key=os.environ.get("AMAP_WEB_KEY", "").strip()
        or str(file_data.get("amap_web_key", "")).strip(),
        flyai_cmd=os.environ.get("FLYAI_CMD", "").strip()
        or str(file_data.get("flyai_cmd", "")).strip(),
        codex_cmd=os.environ.get("CODEX_CMD", "").strip()
        or str(file_data.get("codex_cmd", "codex")).strip()
        or "codex",
        codex_planner_model=os.environ.get("TRAVEL_CODEX_MODEL", "").strip()
        or str(file_data.get("codex_planner_model", "")).strip(),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip()
        or str(file_data.get("openai_api_key", "")).strip(),
        request_parser_mode=os.environ.get("TRAVEL_REQUEST_PARSER_MODE", "").strip()
        or str(file_data.get("request_parser_mode", "auto")).strip()
        or "auto",
        request_parser_model=os.environ.get("TRAVEL_REQUEST_PARSER_MODEL", "").strip()
        or str(file_data.get("request_parser_model", "")).strip(),
        planner_model=os.environ.get("TRAVEL_PLANNER_MODEL", "").strip()
        or str(file_data.get("planner_model", "gpt-4.1-mini")).strip()
        or "gpt-4.1-mini",
        planner_mode=os.environ.get("TRAVEL_PLANNER_MODE", "").strip()
        or str(file_data.get("planner_mode", "auto")).strip()
        or "auto",
        preview_access_token=os.environ.get("TRAVEL_PREVIEW_ACCESS_TOKEN", "").strip()
        or str(file_data.get("preview_access_token", "")).strip(),
        preview_rate_limit_count=int(
            os.environ.get("TRAVEL_PREVIEW_RATE_LIMIT_COUNT", 0)
            or file_data.get("preview_rate_limit_count", 6)
            or 6
        ),
        preview_rate_limit_window_seconds=int(
            os.environ.get("TRAVEL_PREVIEW_RATE_LIMIT_WINDOW_SECONDS", 0)
            or file_data.get("preview_rate_limit_window_seconds", 600)
            or 600
        ),
        preview_job_retention_hours=int(
            os.environ.get("TRAVEL_PREVIEW_JOB_RETENTION_HOURS", 0)
            or file_data.get("preview_job_retention_hours", 72)
            or 72
        ),
        web_data_dir=os.environ.get("TRAVEL_WEB_DATA_DIR", "").strip()
        or str(file_data.get("web_data_dir", "")).strip(),
        preview_port=int(os.environ.get("TRAVEL_PREVIEW_PORT", 0) or file_data.get("preview_port", 8766) or 8766),
        web_port=int(
            os.environ.get("TRAVEL_WEB_PORT", 0)
            or platform_port
            or file_data.get("web_port", 8770)
            or 8770
        ),
    )
