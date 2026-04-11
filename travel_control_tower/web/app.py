from __future__ import annotations

import html
import json
import time
import threading
import webbrowser
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..adapters.route_amap import AmapRouteAdapter
from ..adapters.route_google import GoogleRouteAdapter
from ..adapters.stable_search_flyai import StableFlyAISearchAdapter
from ..exporters.excel_export import export_plan_to_excel
from ..planner_core.models import TripRequest
from ..planner_core.pipeline import build_plan_stub
from ..planner_core.planning_agent import CodexExecPlanningAgent, OpenAIPlanningAgent
from ..preview.render_html import render_plan_html
from ..runtime_config import load_runtime_config
from . import form_ui
from .generation_jobs import GenerationJob, GenerationJobStore, save_plan_json


BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_EXAMPLES_DIR = BASE_DIR / "examples"


def _resolve_data_dir() -> Path:
    configured = str(load_runtime_config().web_data_dir or "").strip()
    if configured:
        target = Path(configured).expanduser()
        return target if target.is_absolute() else (BASE_DIR / target)
    return STATIC_EXAMPLES_DIR


DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR = DATA_DIR / "jobs"
LATEST_PLAN_PATH = DATA_DIR / "web_latest.plan.json"
LATEST_HTML_PATH = DATA_DIR / "web_latest.preview.html"
LATEST_XLSX_PATH = DATA_DIR / "web_latest.xlsx"
JOB_STORE = GenerationJobStore(JOBS_DIR)
PREVIEW_ACCESS_COOKIE = "travel_preview_access"
RATE_LIMIT_LOCK = threading.Lock()
RATE_LIMIT_STATE: dict[str, list[float]] = {}


def _esc(value: str) -> str:
    return html.escape(str(value or ""))


def _preview_access_token() -> str:
    return load_runtime_config().preview_access_token.strip()


def _client_ip(handler: BaseHTTPRequestHandler) -> str:
    forwarded = str(handler.headers.get("X-Forwarded-For", "") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    return str(handler.client_address[0] if handler.client_address else "unknown")


def _preview_rate_limit_decision(ip: str, *, now: float, limit_count: int, window_seconds: int) -> tuple[bool, int]:
    if limit_count <= 0 or window_seconds <= 0:
        return True, 0
    cutoff = now - float(window_seconds)
    with RATE_LIMIT_LOCK:
        history = [stamp for stamp in RATE_LIMIT_STATE.get(ip, []) if stamp >= cutoff]
        allowed = len(history) < int(limit_count)
        if allowed:
            history.append(now)
        RATE_LIMIT_STATE[ip] = history
        retry_after = 0 if allowed or not history else max(1, int(history[0] + window_seconds - now))
    return allowed, retry_after


def _preview_access_cookie_matches(cookie_header: str, expected_token: str) -> bool:
    if not cookie_header or not expected_token:
        return False
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return False
    morsel = cookie.get(PREVIEW_ACCESS_COOKIE)
    return bool(morsel and morsel.value == expected_token)


def _probe_writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def _build_ready_payload() -> tuple[dict[str, object], bool]:
    runtime = load_runtime_config()
    data_dir_ok, data_dir_error = _probe_writable_dir(DATA_DIR)
    latest_dir_ok, latest_dir_error = _probe_writable_dir(LATEST_PLAN_PATH.parent)
    job_store_health = JOB_STORE.health_report()
    warnings: list[str] = []
    if not runtime.preview_access_token:
        warnings.append("未设置预览访问口令，公开链接会直接暴露。")
    if int(runtime.preview_rate_limit_count or 0) <= 0 or int(runtime.preview_rate_limit_window_seconds or 0) <= 0:
        warnings.append("未开启提交限流，公开预览容易被刷。")
    if not runtime.amap_web_key and not runtime.google_maps_api_key:
        warnings.append("未配置地图 Key，本地交通时间会退回保守估算。")
    if not runtime.flyai_cmd:
        warnings.append("未配置 FlyAI 命令，机酒实时搜索不可用。")
    ready = bool(data_dir_ok and latest_dir_ok and job_store_health.get("db_ok"))
    payload = {
        "status": "ready" if ready else "not_ready",
        "service": "travel-control-tower",
        "data_dir": str(DATA_DIR),
        "latest_dir": str(LATEST_PLAN_PATH.parent),
        "job_store": job_store_health,
        "config": {
            "preview_access_token_configured": bool(runtime.preview_access_token),
            "preview_rate_limit_enabled": bool(
                int(runtime.preview_rate_limit_count or 0) > 0 and int(runtime.preview_rate_limit_window_seconds or 0) > 0
            ),
            "amap_configured": bool(runtime.amap_web_key),
            "google_maps_configured": bool(runtime.google_maps_api_key),
            "flyai_configured": bool(runtime.flyai_cmd),
            "planner_mode": str(runtime.planner_mode or "").strip() or "auto",
            "request_parser_mode": str(runtime.request_parser_mode or "").strip() or "auto",
        },
        "checks": {
            "data_dir_writable": data_dir_ok,
            "latest_dir_writable": latest_dir_ok,
            "data_dir_error": data_dir_error,
            "latest_dir_error": latest_dir_error,
        },
        "warnings": warnings,
    }
    return payload, ready


def _job_plan_path(job: GenerationJob) -> Path:
    raw_path = str(getattr(job, "plan_path", "") or "").strip()
    return Path(raw_path) if raw_path else JOB_STORE.plan_path(job.job_id)


def _job_html_path(job: GenerationJob) -> Path:
    raw_path = str(getattr(job, "html_path", "") or "").strip()
    return Path(raw_path) if raw_path else JOB_STORE.html_path(job.job_id)


def _job_excel_path(job: GenerationJob) -> Path:
    raw_path = str(getattr(job, "excel_path", "") or "").strip()
    return Path(raw_path) if raw_path else JOB_STORE.excel_path(job.job_id)


def _load_plan_payload(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_job_plan(job: GenerationJob) -> dict | None:
    plan_path = _job_plan_path(job)
    payload = _load_plan_payload(plan_path)
    if payload is not None and str(getattr(job, "plan_path", "") or "").strip() != str(plan_path):
        JOB_STORE.set_artifact_paths(job.job_id, plan_path=plan_path)
    return payload


def _ensure_job_html_path(job: GenerationJob) -> Path | None:
    html_path = _job_html_path(job)
    if html_path.exists():
        if str(getattr(job, "html_path", "") or "").strip() != str(html_path):
            JOB_STORE.set_artifact_paths(job.job_id, html_path=html_path)
        return html_path
    plan = _load_job_plan(job)
    if plan is None:
        return None
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_result_page(plan, job_id=job.job_id), encoding="utf-8")
    JOB_STORE.set_artifact_paths(job.job_id, html_path=html_path)
    return html_path


def _ensure_job_excel_path(job: GenerationJob) -> Path | None:
    excel_path = _job_excel_path(job)
    if excel_path.exists():
        if str(getattr(job, "excel_path", "") or "").strip() != str(excel_path):
            JOB_STORE.set_artifact_paths(job.job_id, excel_path=excel_path)
        return excel_path
    plan = _load_job_plan(job)
    if plan is None:
        return None
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    export_plan_to_excel(plan, excel_path)
    JOB_STORE.set_artifact_paths(job.job_id, excel_path=excel_path)
    return excel_path


def _latest_successful_job() -> GenerationJob | None:
    return JOB_STORE.latest_successful()


def _ensure_latest_plan_payload() -> dict | None:
    payload = _load_plan_payload(LATEST_PLAN_PATH)
    if payload is not None:
        return payload
    latest_job = _latest_successful_job()
    if not latest_job:
        return None
    payload = _load_job_plan(latest_job)
    if payload is None:
        return None
    save_plan_json(LATEST_PLAN_PATH, payload)
    return payload


def _ensure_latest_html_path() -> Path | None:
    if LATEST_HTML_PATH.exists():
        return LATEST_HTML_PATH
    latest_job = _latest_successful_job()
    if not latest_job:
        return None
    job_html_path = _ensure_job_html_path(latest_job)
    if job_html_path is None or not job_html_path.exists():
        return None
    LATEST_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_HTML_PATH.write_bytes(job_html_path.read_bytes())
    return LATEST_HTML_PATH


def _ensure_latest_excel_path() -> Path | None:
    if LATEST_XLSX_PATH.exists():
        return LATEST_XLSX_PATH
    latest_job = _latest_successful_job()
    if not latest_job:
        return None
    job_excel_path = _ensure_job_excel_path(latest_job)
    if job_excel_path is None or not job_excel_path.exists():
        return None
    LATEST_XLSX_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_XLSX_PATH.write_bytes(job_excel_path.read_bytes())
    return LATEST_XLSX_PATH


def _strip_query_param(url: str, key: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop(key, None)
    query = urlencode(params, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment)) or parsed.path or "/"


def _normalize_next_path(value: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("/"):
        return "/"
    if text.startswith("//"):
        return "/"
    return text


def render_preview_access_page(next_path: str = "/", error: str = "") -> str:
    error_html = f"<div class='error'>{_esc(error)}</div>" if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Travel Control Tower 预览访问</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
    :root {{ --bg:#f8f2e8; --paper:#fffdfa; --ink:#172538; --muted:#675d53; --line:rgba(128,105,78,.18); --accent:#a6532f; --shadow:0 18px 40px rgba(43,32,22,.07); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"Manrope","PingFang SC","Microsoft YaHei",sans-serif; color:var(--ink); background:linear-gradient(rgba(116,96,70,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(116,96,70,.035) 1px, transparent 1px), linear-gradient(180deg, #fcf8f2 0%, var(--bg) 100%); background-size:24px 24px,24px 24px,auto; }}
    .page {{ width:min(920px, calc(100vw - 32px)); margin:42px auto; }}
    .panel {{ background:var(--paper); border:1px solid var(--line); border-radius:32px; padding:30px; box-shadow:var(--shadow); }}
    .eyebrow {{ display:inline-flex; padding:7px 12px; border-radius:999px; background:#f4ece0; color:#7c3d22; font-size:12px; letter-spacing:.16em; text-transform:uppercase; border:1px solid rgba(166,83,47,.12); }}
    h1 {{ margin:18px 0 10px; font-family:"Cormorant Garamond","Songti SC",serif; font-size:64px; line-height:.92; letter-spacing:-.05em; }}
    p {{ margin:0; color:var(--muted); line-height:1.9; max-width:680px; }}
    form {{ margin-top:24px; display:grid; gap:14px; }}
    label {{ display:grid; gap:8px; font-weight:700; }}
    input {{ width:100%; padding:14px 16px; border:1px solid rgba(170,145,114,.26); border-radius:18px; background:#fff; color:var(--ink); }}
    button {{ width:max-content; padding:12px 18px; border:0; border-radius:999px; background:linear-gradient(180deg,#cc8451 0%, var(--accent) 100%); color:#fff; font-weight:700; cursor:pointer; }}
    .error {{ padding:12px 14px; border:1px solid #fecdd3; border-radius:14px; background:#fff1f2; color:#9f1239; }}
  </style>
</head>
<body>
  <main class="page">
    <section class="panel">
      <div class="eyebrow">preview access</div>
      <h1>输入预览口令</h1>
      <p>当前链接已开启最低限度的访问保护。输入口令后会写入当前浏览器会话，用于访问这套旅行规划预览站。</p>
      {error_html}
      <form method="post" action="/preview-login">
        <input type="hidden" name="next" value="{_esc(_normalize_next_path(next_path))}" />
        <label>预览口令<input type="password" name="preview_token" autocomplete="current-password" required /></label>
        <button type="submit">进入预览</button>
      </form>
    </section>
  </main>
</body>
</html>"""


def default_form_values() -> dict[str, str]:
    return form_ui.default_form_values()


def _split_lines(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").splitlines() if part.strip()]


def _field_float(fields: dict[str, str], name: str) -> float | None:
    raw = str(fields.get(name, "") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _prefer_parsed_value(raw_value: str, default_value: str, parsed_value):
    raw_value = str(raw_value or "").strip()
    default_value = str(default_value or "").strip()
    if raw_value and parsed_value not in ("", None, [], ()) and _looks_broken_text(raw_value):
        return parsed_value
    if raw_value and raw_value != default_value:
        return raw_value
    if parsed_value not in ("", None, [], ()):
        return parsed_value
    return raw_value or default_value


def _looks_broken_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "\ufffd" in text:
        return True
    visible = [ch for ch in text if not ch.isspace()]
    if not visible:
        return False
    question_count = sum(1 for ch in visible if ch == "?")
    return question_count >= max(1, len(visible) // 2)


def _display_snapshot_value(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(value).strip()


def _format_job_party_summary(snapshot: dict) -> str:
    travelers = _display_snapshot_value((snapshot or {}).get("traveler_count")) or "?"
    budget = _display_snapshot_value((snapshot or {}).get("budget_per_person"))
    budget_text = f"¥{budget}" if budget else "?"
    return f"{travelers} 人 / {budget_text}"


def parse_trip_request(fields: dict[str, str], today=None):
    return form_ui.parse_trip_request(fields, today=today)


def render_form_page(values: dict[str, str] | None = None, error: str = "") -> str:
    return form_ui.render_form_page(values=values, error=error)


def render_result_page(plan: dict, job_id: str = "") -> str:
    body = render_plan_html(plan)
    plan_link = f"/jobs/{job_id}/plan" if job_id else "/latest/plan"
    html_link = f"/results/{job_id}" if job_id else "/latest/html"
    excel_link = f"/jobs/{job_id}/excel" if job_id else "/latest/excel"
    toolbar = f"""
    <section class="panel" style="margin-top:20px;padding:18px 20px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;">
        <div style="display:grid;gap:6px;">
          <div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#7b6b57;">workspace</div>
          <div style="font-size:20px;font-weight:800;color:#13263a;">结果导出与复查入口</div>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;">
          <a href="/" style="display:inline-flex;align-items:center;justify-content:center;padding:11px 15px;border-radius:999px;border:1px solid rgba(177,151,120,.35);background:#f8f2e8;color:#1f3248;text-decoration:none;font-weight:700;">重新填写</a>
          <a href="{plan_link}" style="display:inline-flex;align-items:center;justify-content:center;padding:11px 15px;border-radius:999px;border:1px solid rgba(177,151,120,.35);background:#ffffff;color:#1f3248;text-decoration:none;font-weight:700;">查看任务 JSON</a>
          <a href="{html_link}" style="display:inline-flex;align-items:center;justify-content:center;padding:11px 15px;border-radius:999px;border:1px solid rgba(177,151,120,.35);background:#ffffff;color:#1f3248;text-decoration:none;font-weight:700;">当前结果页</a>
          <a href="{excel_link}" style="display:inline-flex;align-items:center;justify-content:center;padding:11px 15px;border-radius:999px;border:1px solid rgba(177,151,120,.35);background:#13263a;color:#f7f3ec;text-decoration:none;font-weight:700;">下载 Excel</a>
          <a href="/latest/plan" style="display:inline-flex;align-items:center;justify-content:center;padding:11px 15px;border-radius:999px;border:1px solid rgba(177,151,120,.35);background:#ffffff;color:#1f3248;text-decoration:none;font-weight:700;">最新 JSON</a>
        </div>
      </div>
    </section>
    """
    return body.replace('<main class="page">', '<main class="page">' + toolbar, 1)


def _format_constraint_value(value: str) -> str:
    parts = [item.strip() for item in str(value or "").splitlines() if item.strip()]
    if len(parts) >= 2:
        return " / ".join(parts)
    return str(value or "").strip()


def _display_constraint_value(key: str, value: str) -> str:
    raw = _format_constraint_value(value)
    if not raw:
        return ""
    normalized_key = str(key or "").strip().lower()
    normalized_value = raw.strip().lower()
    if normalized_key == "travel_style":
        return {
            "relaxed": "松弛",
            "balanced": "均衡",
            "packed": "偏满",
        }.get(normalized_value, raw)
    if normalized_key == "request_mode":
        return {
            "itinerary": "固定日期行程",
            "price_scan": "时间窗口比价",
        }.get(normalized_value, raw)
    return raw


def _build_request_context(fields: dict[str, str]) -> dict:
    defaults = default_form_values()
    raw_prompt = str(fields.get("freeform_request", "") or "").strip()
    labels = {
        "scenario_id": "场景模板",
        "departure_city": "手动指定出发地",
        "destination": "手动指定目的地",
        "start_date": "手动指定开始日期",
        "end_date": "手动指定结束日期",
        "traveler_count": "手动指定人数",
        "budget_per_person": "手动指定人均预算",
        "travel_style": "节奏偏好",
        "must_go": "补充必去点",
        "hotel_preferences": "补充酒店偏好",
        "transport_preferences": "补充交通偏好",
        "user_hotel_name": "已知酒店",
        "user_hotel_area": "酒店区域",
        "user_hotel_nightly_price": "酒店每晚价格",
        "user_hotel_url": "酒店链接",
        "user_transport_label": "已知交通",
        "user_transport_category": "交通类型",
        "user_transport_total_price": "交通总价",
        "user_transport_depart_at": "交通出发时间",
        "user_transport_arrive_at": "交通到达时间",
        "user_arrival_at_destination": "到达目的地时间",
        "user_return_depart_at": "返程出发时间",
        "user_transport_url": "交通链接",
        "notes": "备注",
    }
    manual_constraints: list[str] = []
    if str(fields.get("enable_live_search", "") or "").strip():
        manual_constraints.append("启用实时机酒搜索")
    for key, label in labels.items():
        raw_value = str(fields.get(key, "") or "").strip()
        default_value = str(defaults.get(key, "") or "").strip()
        if not raw_value:
            continue
        if raw_value == default_value:
            continue
        display_value = _display_constraint_value(key, raw_value)
        if not display_value:
            continue
        manual_constraints.append(f"{label}：{display_value}")
    return {
        "natural_language_request": raw_prompt,
        "manual_constraints": manual_constraints,
    }


def render_job_page(job: GenerationJob) -> str:
    snapshot = job.request_snapshot or {}
    destination = _esc(snapshot.get("destination", ""))
    departure = _esc(snapshot.get("departure_city", ""))
    party_summary = _esc(_format_job_party_summary(snapshot))
    prompt_text = _esc(str((job.fields or {}).get("freeform_request", "") or ""))
    stage_label = _esc(getattr(job, "stage_label", "") or "等待开始")
    progress = max(0, min(int(getattr(job, "progress", 0) or 0), 100))
    status_label = {
        "pending": "等待中",
        "running": "处理中",
        "succeeded": "已完成",
        "failed": "失败",
    }.get(str(getattr(job, "status", "") or "").strip().lower(), _esc(job.status or "等待中"))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Travel Control Tower 生成中</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Manrope:wght@400;500;600;700;800&family=Noto+Serif+SC:wght@500;600;700&display=swap');
    :root {{ --bg:#f4ede3; --paper:rgba(252,248,241,.94); --ink:#162739; --muted:#6a6258; --line:rgba(177,151,120,.30); --accent:#b26b35; --brand:#19324c; --shadow:0 26px 62px rgba(49,38,24,.10); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"Manrope","PingFang SC","Microsoft YaHei",sans-serif; color:var(--ink); background:linear-gradient(rgba(94,78,56,.045) 1px, transparent 1px), linear-gradient(90deg, rgba(94,78,56,.045) 1px, transparent 1px), radial-gradient(circle at top left, rgba(198,150,110,.16), transparent 24%), radial-gradient(circle at top right, rgba(78,116,150,.10), transparent 24%), linear-gradient(180deg, #fbf7f1 0%, var(--bg) 100%); background-size:28px 28px, 28px 28px, auto, auto, auto; }}
    .masthead {{ width:min(1240px, calc(100vw - 40px)); margin:0 auto; padding:18px 0 0; font-size:12px; letter-spacing:.16em; text-transform:uppercase; color:#6f6458; }}
    .page {{ width:min(1240px, calc(100vw - 40px)); margin:0 auto 56px; }}
    .hero-band {{ width:100vw; margin-left:calc(50% - 50vw); background:radial-gradient(circle at 82% 18%, rgba(242,222,194,.52), transparent 18%), linear-gradient(135deg, #f4ebdf 0%, #efe2d1 42%, #dfe6ea 100%); color:var(--ink); overflow:hidden; margin:14px 0 24px; position:relative; border-top:1px solid rgba(255,255,255,.42); border-bottom:1px solid rgba(177,151,120,.18); }}
    .hero-band::before {{ content:""; position:absolute; inset:0; background:linear-gradient(rgba(255,255,255,.22) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.22) 1px, transparent 1px); background-size:92px 92px; opacity:.34; pointer-events:none; }}
    .hero-inner {{ width:min(1240px, calc(100vw - 40px)); margin:0 auto; padding:42px 0 38px; display:grid; grid-template-columns:minmax(0,1.35fr) 320px; gap:24px; align-items:end; }}
    .eyebrow {{ letter-spacing:.16em; text-transform:uppercase; font-size:12px; color:#7b6b57; margin-bottom:14px; }}
    h1 {{ margin:0; font-family:"Cormorant Garamond","Noto Serif SC","Songti SC",serif; font-size:clamp(42px, 5.2vw, 72px); line-height:.92; letter-spacing:-.05em; }}
    .hero-copy p {{ margin:18px 0 0; max-width:760px; color:#52483f; line-height:1.9; }}
    .hero-note {{ padding:18px 18px 18px 22px; border-radius:24px; background:linear-gradient(180deg, rgba(255,255,255,.94) 0%, rgba(247,240,231,.94) 100%); color:var(--ink); box-shadow:0 18px 42px rgba(10,17,24,.10); position:relative; border:1px solid rgba(177,151,120,.20); }}
    .hero-note::before {{ content:""; position:absolute; left:0; top:18px; bottom:18px; width:4px; border-radius:999px; background:linear-gradient(180deg, #c88d5e 0%, var(--accent) 100%); }}
    .hero-note span {{ display:block; color:var(--muted); font-size:12px; margin-bottom:8px; letter-spacing:.08em; text-transform:uppercase; }}
    .hero-note strong {{ display:block; line-height:1.7; }}
    .workspace {{ display:grid; grid-template-columns:minmax(0,1.35fr) 320px; gap:20px; }}
    .panel {{ background:var(--paper); border:1px solid var(--line); border-radius:28px; padding:24px; box-shadow:var(--shadow); position:relative; overflow:hidden; }}
    .panel::before {{ content:""; position:absolute; inset:0 0 auto 0; height:4px; background:linear-gradient(90deg, #d4a06f 0%, #54738f 100%); opacity:.88; }}
    .meta-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:14px; margin-bottom:18px; }}
    .meta-card {{ padding:16px 18px; border-radius:22px; background:rgba(255,255,255,.82); border:1px solid rgba(217,204,184,.72); }}
    .meta-card span {{ display:block; color:#7b6b57; font-size:12px; margin-bottom:8px; }}
    .meta-card strong {{ display:block; font-size:16px; line-height:1.6; }}
    .stage-card {{ padding:20px; border-radius:24px; background:rgba(255,255,255,.82); border:1px solid rgba(217,204,184,.72); }}
    .stage-row {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }}
    .stage-badge {{ display:inline-flex; padding:6px 10px; border-radius:999px; background:#dbe6ef; color:var(--brand); font-size:12px; font-weight:700; }}
    .progress-text {{ color:var(--muted); }}
    .bar {{ height:10px; border-radius:999px; background:rgba(217,204,184,.76); overflow:hidden; }}
    .bar span {{ display:block; width:{progress}%; height:100%; border-radius:999px; background:linear-gradient(90deg, #c88d5e 0%, var(--accent) 100%); transition:width .25s ease; }}
    .steps {{ display:grid; gap:12px; margin-top:16px; }}
    .step {{ display:flex; gap:14px; align-items:flex-start; padding:12px 14px; border-radius:18px; background:rgba(246,239,228,.86); border:1px solid rgba(217,204,184,.58); }}
    .step-index {{ width:28px; height:28px; border-radius:50%; background:#dbe6ef; color:var(--brand); display:inline-flex; align-items:center; justify-content:center; font-size:12px; font-weight:700; flex:0 0 auto; }}
    .step strong {{ display:block; margin-bottom:4px; }}
    .prompt-box {{ margin-top:18px; padding:20px; border-radius:24px; background:rgba(255,255,255,.82); border:1px solid rgba(217,204,184,.72); line-height:1.9; white-space:pre-wrap; }}
    .actions {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:18px; }}
    a {{ display:inline-flex; align-items:center; justify-content:center; border-radius:999px; padding:12px 16px; text-decoration:none; }}
    .ghost {{ border:1px solid rgba(217,204,184,.9); background:rgba(255,255,255,.84); color:var(--ink); font-weight:700; }}
    .side-stack {{ display:grid; gap:16px; }}
    .side-block {{ padding:18px; border-radius:22px; background:rgba(255,255,255,.82); border:1px solid rgba(217,204,184,.72); }}
    .side-block span {{ display:block; color:#7b6b57; font-size:12px; margin-bottom:8px; letter-spacing:.08em; text-transform:uppercase; }}
    .error {{ margin-top:16px; padding:14px 16px; background:#fff1f2; border:1px solid #fecdd3; color:#9f1239; border-radius:14px; display:none; }}
    .error.visible {{ display:block; }}
    @media (max-width: 980px) {{ .hero-inner, .workspace {{ grid-template-columns:1fr; }} }}
    @media (max-width: 760px) {{ .masthead, .page {{ width:min(100vw - 20px, 1240px); }} .hero-inner {{ width:min(100vw - 20px, 1240px); }} .meta-grid {{ grid-template-columns:1fr; }} h1 {{ font-size:40px; }} }}
  </style>
</head>
<body>
  <div class="masthead">Travel Control Tower / generation queue</div>
  <main class="page">
    <section class="hero-band">
      <div class="hero-inner">
        <div class="hero-copy">
          <div class="eyebrow">plan in progress / structured pipeline</div>
          <h1>正在生成旅行方案</h1>
          <p>当前任务已经进入后台流水线。系统会继续补齐交通、酒店、日内路线、预算、预定事项和导出文件，完成后自动跳转到结果页。</p>
        </div>
        <article class="hero-note"><span>当前阶段</span><strong id="hero-stage-label">{stage_label}</strong><div id="hero-progress-text" class="progress-text">当前进度 {progress}%</div></article>
      </div>
    </section>
    <section class="workspace">
      <section class="panel">
        <div class="meta-grid">
          <article class="meta-card"><span>任务编号</span><strong>{_esc(job.job_id)}</strong></article>
          <article class="meta-card"><span>当前状态</span><strong id="job-status">{status_label}</strong></article>
          <article class="meta-card"><span>出发地 / 目的地</span><strong id="job-route-summary">{departure or '处理中'} → {destination or '处理中'}</strong></article>
          <article class="meta-card"><span>人数 / 人均预算</span><strong id="job-party-summary">{party_summary}</strong></article>
        </div>
        <div class="stage-card">
          <div class="stage-row"><span id="job-stage" class="stage-badge">{stage_label}</span><span id="job-progress-text" class="progress-text">当前进度 {progress}%</span></div>
          <div class="bar"><span></span></div>
          <div style="margin-top:16px;font-weight:800;font-size:18px;">后台步骤</div>
          <div class="steps">
            <div class="step"><div class="step-index">01</div><div><strong>解析需求</strong>把自然语言需求和补充字段合并成统一请求。</div></div>
            <div class="step"><div class="step-index">02</div><div><strong>补齐候选池</strong>补景点、交通、酒店和基础路线数据。</div></div>
            <div class="step"><div class="step-index">03</div><div><strong>生成主方案</strong>按天编排节奏、预算、预定事项和备选项。</div></div>
            <div class="step"><div class="step-index">04</div><div><strong>写入导出物</strong>生成结果页、JSON 和 Excel 文件。</div></div>
          </div>
        </div>
        <div class="prompt-box"><strong>本次自然语言需求</strong><br />{prompt_text or '-'}</div>
        <div id="job-error" class="error"></div>
        <div class="actions"><a class="ghost" href="/">返回重新填写</a><a class="ghost" href="/api/jobs/{_esc(job.job_id)}" target="_blank" rel="noreferrer">查看任务 JSON</a></div>
      </section>
      <aside class="side-stack">
        <section class="panel side-block"><span>系统行为</span><strong>生成成功后会自动跳转结果页，不需要手动刷新或重复提交。</strong></section>
        <section class="panel side-block"><span>当前输入方式</span><strong>本轮任务仍然以自然语言为主，结构化字段只负责补充边界条件。</strong></section>
        <section class="panel side-block"><span>等待预期</span><strong>启用实时机酒搜索时，通常会比纯模板方案更慢一些；如果交通和酒店都要实时拉取，等待 20 到 40 秒是正常范围。</strong></section>
      </aside>
    </section>
  </main>
  <script>
    const statusNode = document.getElementById('job-status');
    const stageNode = document.getElementById('job-stage');
    const progressTextNode = document.getElementById('job-progress-text');
    const heroStageNode = document.getElementById('hero-stage-label');
    const heroProgressNode = document.getElementById('hero-progress-text');
    const routeSummaryNode = document.getElementById('job-route-summary');
    const partySummaryNode = document.getElementById('job-party-summary');
    const progressBarNode = document.querySelector('.bar span');
    const errorNode = document.getElementById('job-error');
    const statusLabels = {{ pending: '等待中', running: '处理中', succeeded: '已完成', failed: '失败' }};
    const formatRouteSummary = (snapshot) => {{
      const departure = String((snapshot && snapshot.departure_city) || '').trim() || '处理中';
      const destination = String((snapshot && snapshot.destination) || '').trim() || '处理中';
      return departure + ' → ' + destination;
    }};
    const formatPartySummary = (snapshot) => {{
      const travelersRaw = Number((snapshot && snapshot.traveler_count) ?? NaN);
      const travelers = Number.isFinite(travelersRaw) ? String(Math.trunc(travelersRaw)) : '?';
      const budgetRaw = Number((snapshot && snapshot.budget_per_person) ?? NaN);
      const budget = Number.isFinite(budgetRaw)
        ? '¥' + (Number.isInteger(budgetRaw) ? String(Math.trunc(budgetRaw)) : budgetRaw.toFixed(1).replace(/\\.0$/, ''))
        : '?';
      return travelers + ' 人 / ' + budget;
    }};
    const poll = async () => {{
      try {{
        const resp = await fetch('/api/jobs/{_esc(job.job_id)}', {{ cache: 'no-store' }});
        const data = await resp.json();
        const stageLabel = data.stage_label || data.stage || '处理中';
        const progressLabel = '当前进度 ' + String(data.progress ?? 0) + '%';
        const snapshot = data.request_snapshot || {{}};
        statusNode.textContent = statusLabels[String(data.status || '').toLowerCase()] || data.status || 'unknown';
        stageNode.textContent = stageLabel;
        progressTextNode.textContent = progressLabel;
        heroStageNode.textContent = stageLabel;
        heroProgressNode.textContent = progressLabel;
        routeSummaryNode.textContent = formatRouteSummary(snapshot);
        partySummaryNode.textContent = formatPartySummary(snapshot);
        progressBarNode.style.width = String(data.progress ?? 0) + '%';
        if (data.status === 'succeeded' && data.result_url) {{
          window.location.replace(data.result_url);
          return;
        }}
        if (data.status === 'failed') {{
          errorNode.textContent = data.error || '任务执行失败';
          errorNode.classList.add('visible');
          return;
        }}
      }} catch (err) {{
        errorNode.textContent = '状态轮询失败：' + (err && err.message ? err.message : String(err));
        errorNode.classList.add('visible');
      }}
      window.setTimeout(poll, 1500);
    }};
    window.setTimeout(poll, 600);
  </script>
</body>
</html>"""

def render_job_failure_page(job: GenerationJob) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Travel Control Tower 生成失败</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Manrope:wght@400;500;600;700;800&family=Noto+Serif+SC:wght@500;600;700&display=swap');
    body {{ margin:0; font-family:"Manrope","PingFang SC","Microsoft YaHei",sans-serif; background:linear-gradient(rgba(94,78,56,.045) 1px, transparent 1px), linear-gradient(90deg, rgba(94,78,56,.045) 1px, transparent 1px), linear-gradient(180deg, #fbf7f1 0%, #f4ede3 100%); background-size:28px 28px, 28px 28px, auto; color:#162739; }}
    .masthead {{ width:min(980px, calc(100vw - 32px)); margin:0 auto; padding:18px 0 0; font-size:12px; letter-spacing:.16em; text-transform:uppercase; color:#6f6458; }}
    .page {{ width:min(980px, calc(100vw - 32px)); margin:18px auto 56px; }}
    .panel {{ background:rgba(252,248,241,.94); border:1px solid rgba(177,151,120,.30); border-radius:28px; padding:30px; box-shadow:0 22px 48px rgba(49,38,24,.10); position:relative; overflow:hidden; }}
    .panel::before {{ content:""; position:absolute; inset:0 0 auto 0; height:4px; background:linear-gradient(90deg, #d4a06f 0%, #54738f 100%); }}
    h1 {{ margin:0 0 12px; font-family:"Cormorant Garamond","Noto Serif SC","Songti SC",serif; font-size:52px; line-height:1; letter-spacing:-.04em; }}
    p {{ margin:0; color:#605446; line-height:1.9; }}
    .error {{ margin-top:18px; padding:18px; border-radius:18px; background:#fff1f2; border:1px solid #fecdd3; color:#9f1239; white-space:pre-wrap; }}
    .actions {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:20px; }}
    a {{ display:inline-flex; align-items:center; justify-content:center; padding:12px 16px; border-radius:999px; border:1px solid rgba(177,151,120,.35); background:#ffffff; color:#19324c; text-decoration:none; font-weight:700; }}
  </style>
</head>
<body>
  <div class="masthead">Travel Control Tower / failure</div>
  <main class="page">
    <section class="panel">
      <h1>任务执行失败</h1>
      <p>任务编号：{_esc(job.job_id)}</p>
      <div class="error">{_esc(job.error or "未知错误")}</div>
      <div class="actions">
        <a href="/">返回重新填写</a>
        <a href="/api/jobs/{_esc(job.job_id)}" target="_blank" rel="noreferrer">查看任务 JSON</a>
      </div>
    </section>
  </main>
</body>
</html>"""


def _run_generation_job(job_id: str, request: TripRequest | None = None) -> None:
    job = JOB_STORE.get(job_id)
    fields = dict((job.fields if job else {}) or {})
    JOB_STORE.mark_stage(job_id, status="running", stage="parse", stage_label="解析输入与约束", progress=8)
    try:
        if request is None:
            request = parse_trip_request(fields)
        JOB_STORE.set_request_snapshot(job_id, request)
        JOB_STORE.mark_stage(job_id, stage="routing", stage_label="准备路线与搜索适配器", progress=20)
        route_adapter = _build_route_adapter(request)
        search_adapter = _build_search_adapter(request)
        planning_agent = _build_planning_agent()
        JOB_STORE.mark_stage(job_id, stage="search", stage_label="补齐候选池与基础数据", progress=38)
        JOB_STORE.mark_stage(job_id, stage="processing", stage_label="候选筛选、行程规划与预算处理中", progress=58)
        plan = build_plan_stub(
            request,
            route_adapter=route_adapter,
            search_adapter=search_adapter,
            planning_agent=planning_agent,
        ).to_dict()
        plan["request_context"] = _build_request_context(fields)
        if plan.get("input_snapshot") and fields.get("freeform_request"):
            ordered_snapshot = {"原始需求": str(fields.get("freeform_request", "")).strip()}
            ordered_snapshot.update(plan["input_snapshot"])
            plan["input_snapshot"] = ordered_snapshot

        plan_path = JOB_STORE.plan_path(job_id)
        html_path = JOB_STORE.html_path(job_id)
        excel_path = JOB_STORE.excel_path(job_id)

        JOB_STORE.mark_stage(job_id, stage="render", stage_label="生成结果页", progress=78)
        save_plan_json(plan_path, plan)
        html_text = render_result_page(plan, job_id=job_id)
        html_path.write_text(html_text, encoding="utf-8")
        JOB_STORE.mark_stage(job_id, stage="export", stage_label="导出 Excel", progress=92)
        export_plan_to_excel(plan, excel_path)

        save_plan_json(LATEST_PLAN_PATH, plan)
        LATEST_HTML_PATH.write_text(html_text, encoding="utf-8")
        LATEST_XLSX_PATH.write_bytes(excel_path.read_bytes())

        JOB_STORE.mark_succeeded(job_id, plan_path=plan_path, html_path=html_path, excel_path=excel_path)
    except Exception as exc:  # pragma: no cover
        JOB_STORE.mark_failed(job_id, str(exc))


def _start_generation_job(fields: dict[str, str], request: TripRequest | None = None) -> GenerationJob:
    retention_hours = max(1, int(load_runtime_config().preview_job_retention_hours or 72))
    JOB_STORE.prune_expired(retention_hours * 3600)
    preview_request = request
    if preview_request is None:
        try:
            preview_request = form_ui.build_preview_request(fields)
        except ValueError:
            raise
        except Exception:
            preview_request = None
    job = JOB_STORE.create(fields, preview_request)
    worker = threading.Thread(
        target=_run_generation_job,
        args=(job.job_id, request),
        name=f"travel-job-{job.job_id}",
        daemon=True,
    )
    worker.start()
    return job


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text or "")


KNOWN_OVERSEAS_DESTINATIONS = {
    "东京",
    "大阪",
    "京都",
    "札幌",
    "福冈",
    "名古屋",
    "冲绳",
    "首尔",
    "釜山",
    "新加坡",
    "曼谷",
    "东京市",
    "osaka",
    "tokyo",
    "kyoto",
    "fukuoka",
    "nagoya",
    "sapporo",
    "seoul",
    "busan",
    "singapore",
    "bangkok",
}


def _is_known_overseas_destination(request: TripRequest) -> bool:
    scope = f"{request.departure_city} {request.destination}".lower()
    return any(token.lower() in scope for token in KNOWN_OVERSEAS_DESTINATIONS)


def _prefer_china_provider(request: TripRequest) -> bool:
    if _is_known_overseas_destination(request):
        return False
    scope_text = f"{request.departure_city} {request.destination}"
    return _contains_cjk(scope_text)


def _build_route_adapter(request: TripRequest):
    runtime = load_runtime_config()
    if _prefer_china_provider(request):
        if runtime.amap_web_key:
            adapter = AmapRouteAdapter(runtime.amap_web_key)
            if adapter.is_available:
                return adapter
        if runtime.google_maps_api_key:
            adapter = GoogleRouteAdapter(runtime.google_maps_api_key)
            if adapter.is_available:
                return adapter
        return None

    if runtime.google_maps_api_key:
        adapter = GoogleRouteAdapter(runtime.google_maps_api_key)
        if adapter.is_available:
            return adapter
    if runtime.amap_web_key:
        adapter = AmapRouteAdapter(runtime.amap_web_key)
        if adapter.is_available:
            return adapter
    return None


def _build_search_adapter(request: TripRequest):
    if not request.enable_live_search:
        return None
    adapter = StableFlyAISearchAdapter()
    return adapter if adapter.is_available else None


def _build_planning_agent():
    runtime = load_runtime_config()
    planner_mode = (runtime.planner_mode or "auto").strip().lower()
    if planner_mode in {"rule", "fallback", "off", "disabled"}:
        return None
    if planner_mode in {"auto", "candidate", "default"}:
        if runtime.openai_api_key:
            return OpenAIPlanningAgent(
                api_key=runtime.openai_api_key,
                model=runtime.planner_model,
            )
        return None
    if planner_mode in {"codex", "codex_cli", "chatgpt"}:
        agent = CodexExecPlanningAgent(
            codex_cmd=runtime.codex_cmd,
            model=runtime.codex_planner_model,
        )
        return agent if agent.is_available() else None
    if planner_mode in {"openai", "openai_api", "api"}:
        if runtime.openai_api_key:
            return OpenAIPlanningAgent(
                api_key=runtime.openai_api_key,
                model=runtime.planner_model,
            )
        return None
    if runtime.openai_api_key:
        return OpenAIPlanningAgent(
            api_key=runtime.openai_api_key,
            model=runtime.planner_model,
        )
    return None


default_form_values = form_ui.default_form_values
parse_trip_request = form_ui.parse_trip_request
render_form_page = form_ui.render_form_page


class TravelControlTowerHandler(BaseHTTPRequestHandler):
    server_version = "TravelControlTower/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/health":
            self._write_text("ok")
            return
        if path == "/ready":
            payload, ready = _build_ready_payload()
            self._write_json(payload, status=HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path == "/preview-login":
            self._write_html(render_preview_access_page(next_path=_normalize_next_path(str((parse_qs(parsed.query).get("next") or ["/"])[0]))))
            return
        if not self._ensure_preview_access(parsed):
            return
        if path in {"/", ""}:
            self._write_html(render_form_page())
            return
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/").strip("/")
            job = JOB_STORE.get(job_id)
            if not job:
                self._write_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._write_json(job.to_api_payload())
            return
        if path == "/api/reverse-geocode":
            params = parse_qs(parsed.query, keep_blank_values=True)
            lat = str((params.get("lat") or [""])[0] or "").strip()
            lng = str((params.get("lng") or [""])[0] or "").strip()
            if not lat or not lng:
                self._write_json({"error": "missing lat/lng"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._write_json(form_ui.reverse_geocode_city(lat, lng))
            except Exception as exc:  # pragma: no cover
                self._write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path.startswith("/jobs/") and path.endswith("/plan"):
            job_id = path.removeprefix("/jobs/").removesuffix("/plan").strip("/")
            job = JOB_STORE.get(job_id)
            if not job:
                self._write_json({"error": "job plan not found"}, status=HTTPStatus.NOT_FOUND)
                return
            payload = _load_job_plan(job)
            if payload is None:
                self._write_json({"error": "job plan not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._write_json(payload)
            return
        if path.startswith("/jobs/") and path.endswith("/excel"):
            job_id = path.removeprefix("/jobs/").removesuffix("/excel").strip("/")
            job = JOB_STORE.get(job_id)
            if not job:
                self._write_html("<h1>job excel not found</h1>", status=HTTPStatus.NOT_FOUND)
                return
            target = _ensure_job_excel_path(job)
            if target is None or not target.exists():
                self._write_html("<h1>job excel not found</h1>", status=HTTPStatus.NOT_FOUND)
                return
            self._write_file(
                target.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                download_name=f"travel-control-tower-{job_id}.xlsx",
            )
            return
        if path.startswith("/results/"):
            job_id = path.removeprefix("/results/").strip("/")
            job = JOB_STORE.get(job_id)
            if not job:
                self._write_html("<h1>job not found</h1>", status=HTTPStatus.NOT_FOUND)
                return
            if job.status == "failed":
                self._write_html(render_job_failure_page(job), status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if job.status != "succeeded":
                self._write_html(render_job_page(job), status=HTTPStatus.ACCEPTED)
                return
            target = _ensure_job_html_path(job)
            if target is None or not target.exists():
                self._write_html("<h1>job html not found</h1>", status=HTTPStatus.NOT_FOUND)
                return
            self._write_html(target.read_text(encoding="utf-8"))
            return
        if path.startswith("/jobs/"):
            job_id = path.removeprefix("/jobs/").strip("/")
            job = JOB_STORE.get(job_id)
            if not job:
                self._write_html("<h1>job not found</h1>", status=HTTPStatus.NOT_FOUND)
                return
            if job.status == "failed":
                self._write_html(render_job_failure_page(job), status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._write_html(render_job_page(job), status=HTTPStatus.ACCEPTED)
            return
        if path == "/latest/plan":
            payload = _ensure_latest_plan_payload()
            if payload is None:
                self._write_json({"error": "latest plan not found"}, status=HTTPStatus.NOT_FOUND)
            else:
                self._write_json(payload)
            return
        if path == "/latest/html":
            target = _ensure_latest_html_path()
            if target is None or not target.exists():
                self._write_html("<h1>latest html not found</h1>", status=HTTPStatus.NOT_FOUND)
            else:
                self._write_html(target.read_text(encoding="utf-8"))
            return
        if path == "/latest/excel":
            target = _ensure_latest_excel_path()
            if target is not None and target.exists():
                self._write_file(
                    target.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    download_name="travel-control-tower-latest.xlsx",
                )
            else:
                self._write_html("<h1>latest excel not found</h1>", status=HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/examples/"):
            name = path.removeprefix("/examples/").strip("/")
            target = STATIC_EXAMPLES_DIR / name
            if target.exists() and target.is_file():
                if target.suffix == ".html":
                    self._write_html(target.read_text(encoding="utf-8"))
                elif target.suffix == ".json":
                    self._write_json(json.loads(target.read_text(encoding="utf-8")))
                elif target.suffix == ".xlsx":
                    self._write_file(
                        target.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        download_name=target.name,
                    )
                else:
                    self._write_text(target.read_text(encoding="utf-8"))
                return
        self._write_html("<h1>404</h1>", status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/preview-login":
            self._handle_preview_login()
            return
        if not self._ensure_preview_access(urlparse(self.path)):
            return
        if self.path != "/generate":
            self._write_html("<h1>404</h1>", status=HTTPStatus.NOT_FOUND)
            return
        runtime = load_runtime_config()
        allowed, retry_after = _preview_rate_limit_decision(
            _client_ip(self),
            now=time.time(),
            limit_count=int(runtime.preview_rate_limit_count or 0),
            window_seconds=int(runtime.preview_rate_limit_window_seconds or 0),
        )
        if not allowed:
            self._write_html(
                render_form_page(
                    error=f"当前预览站提交过于频繁，请在约 {retry_after} 秒后重试。",
                ),
                status=HTTPStatus.TOO_MANY_REQUESTS,
            )
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        parsed_form = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        fields = {key: values[0] if values else "" for key, values in parsed_form.items()}

        if not str(fields.get("freeform_request", "") or "").strip():
            self._write_html(
                render_form_page(fields, error="请先用自然语言描述你的出行需求。下方字段只负责补充约束。"),
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            job = _start_generation_job(fields)
            self._write_html(render_job_page(job), status=HTTPStatus.ACCEPTED)
        except Exception as exc:  # pragma: no cover
            self._write_html(render_form_page(fields, error=str(exc)), status=HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _ensure_preview_access(self, parsed) -> bool:
        expected_token = _preview_access_token()
        if not expected_token:
            return True
        params = parse_qs(parsed.query, keep_blank_values=True)
        query_token = str((params.get("preview_token") or [""])[0] or "").strip()
        if query_token and query_token == expected_token:
            self._redirect(
                _strip_query_param(self.path, "preview_token"),
                cookie_value=expected_token,
            )
            return False
        if _preview_access_cookie_matches(self.headers.get("Cookie", ""), expected_token):
            return True
        self._write_html(
            render_preview_access_page(next_path=self.path),
            status=HTTPStatus.UNAUTHORIZED,
        )
        return False

    def _handle_preview_login(self) -> None:
        expected_token = _preview_access_token()
        if not expected_token:
            self._redirect("/")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        parsed_form = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        submitted_token = str((parsed_form.get("preview_token") or [""])[0] or "").strip()
        next_path = _normalize_next_path(str((parsed_form.get("next") or ["/"])[0] or "/"))
        if submitted_token != expected_token:
            self._write_html(
                render_preview_access_page(next_path=next_path, error="口令不正确，请重试。"),
                status=HTTPStatus.UNAUTHORIZED,
            )
            return
        self._redirect(next_path, cookie_value=expected_token)

    def _write_html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_text(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_file(
        self,
        content: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        download_name: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(content)

    def _redirect(self, location: str, *, cookie_value: str | None = None, status: HTTPStatus = HTTPStatus.SEE_OTHER) -> None:
        self.send_response(status)
        self.send_header("Location", location or "/")
        if cookie_value is not None:
            self.send_header(
                "Set-Cookie",
                f"{PREVIEW_ACCESS_COOKIE}={cookie_value}; Path=/; HttpOnly; SameSite=Lax",
            )
        self.end_headers()


def serve(host: str = "127.0.0.1", port: int | None = None, open_browser: bool = True) -> None:
    runtime = load_runtime_config()
    port = port or runtime.web_port or 8770
    httpd = ThreadingHTTPServer((host, port), TravelControlTowerHandler)
    url = f"http://{host}:{port}/"
    print(f"Travel Control Tower Web running at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
