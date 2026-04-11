from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import requests

from ..adapters.base import HotelCandidate, POICandidate, TransportCandidate
from .day_builder import build_daily_plan
from .models import DailyPlan, DayItem, PlanningTrace, TripRequest
from .scenarios import build_scenario_daily_plan

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
ALLOWED_ITEM_CATEGORIES = {"交通", "游玩", "餐饮", "住宿"}


@dataclass
class PlanningContext:
    request: TripRequest
    selected_hotel: HotelCandidate | None
    selected_transport: TransportCandidate | None
    hotel_candidates: list[HotelCandidate]
    transport_candidates: list[TransportCandidate]
    poi_candidates: list[POICandidate]


class BasePlanningAgent:
    engine_name = "planner"

    def is_available(self) -> bool:
        return True

    def plan(self, context: PlanningContext) -> tuple[list[DailyPlan], PlanningTrace]:
        raise NotImplementedError


class RulePlanningAgent(BasePlanningAgent):
    engine_name = "rule_fallback"

    def plan(self, context: PlanningContext) -> tuple[list[DailyPlan], PlanningTrace]:
        daily_plan = build_scenario_daily_plan(context.request)
        if not daily_plan:
            daily_plan = build_daily_plan(context.request, poi_candidates=context.poi_candidates)
        trace = PlanningTrace(
            engine="基础规划",
            mode="fallback",
            used_fallback=True,
            details="当前先按基础规则生成日程，方便先确认节奏、预算和主线路；后续如果补到更多实时数据，会继续收紧方案。",
        )
        return daily_plan, trace


class CandidatePlanningAgent(BasePlanningAgent):
    engine_name = "candidate_planner"

    def plan(self, context: PlanningContext) -> tuple[list[DailyPlan], PlanningTrace]:
        daily_plan = build_scenario_daily_plan(context.request)
        if not daily_plan:
            daily_plan = build_daily_plan(context.request, poi_candidates=context.poi_candidates)
        trace = PlanningTrace(
            engine="自动规划",
            mode="candidate",
            used_fallback=False,
            details="当前优先根据实时搜到的交通、酒店和景点来排主线；没有拿到实时结果的环节，会先用保守安排补齐。",
        )
        return daily_plan, trace


class OpenAIPlanningAgent(BasePlanningAgent):
    engine_name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4.1-mini", timeout_seconds: int = 45) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or "gpt-4.1-mini").strip()
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return bool(self.api_key)

    def plan(self, context: PlanningContext) -> tuple[list[DailyPlan], PlanningTrace]:
        payload = self._call_openai(context)
        daily_plan = _convert_llm_payload_to_daily_plan(context.request, payload)
        trace = PlanningTrace(
            engine="智能规划",
            mode="llm",
            model=self.model,
            used_fallback=False,
            details="当前主线由模型结合实时候选生成；路线、预算和时间仍会经过规则校验，避免只追求表面顺滑。",
        )
        return daily_plan, trace

    def _call_openai(self, context: PlanningContext) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_body = {
            "model": self.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _planner_system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(_planner_input_payload(context), ensure_ascii=False),
                },
            ],
        }
        response = requests.post(
            OPENAI_CHAT_COMPLETIONS_URL,
            headers=headers,
            json=request_body,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("LLM planner 返回为空。")
        return _load_json_payload(content)


class CodexExecPlanningAgent(BasePlanningAgent):
    engine_name = "codex_exec"

    def __init__(
        self,
        codex_cmd: str = "codex",
        model: str = "",
        timeout_seconds: int = 180,
        reasoning_effort: str = "low",
        auth_file: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.codex_cmd = (codex_cmd or "codex").strip()
        self.model = (model or "").strip()
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = (reasoning_effort or "low").strip()
        self.auth_file = auth_file or (Path.home() / ".codex" / "auth.json")
        self.runner = runner or subprocess.run

    def is_available(self) -> bool:
        return bool(self._resolve_command()) and self.auth_file.exists()

    def plan(self, context: PlanningContext) -> tuple[list[DailyPlan], PlanningTrace]:
        payload = self._call_codex(context)
        daily_plan = _convert_llm_payload_to_daily_plan(context.request, payload)
        trace = PlanningTrace(
            engine="智能规划",
            mode="llm",
            model=self.model or "codex-default",
            used_fallback=False,
            details="当前主线由本机登录态下的模型生成；路线、预算和时间仍会经过规则校验，避免只追求表面顺滑。",
        )
        return daily_plan, trace

    def _call_codex(self, context: PlanningContext) -> dict:
        prompt = _build_codex_prompt(context)
        command_path = self._resolve_command()
        if not command_path:
            raise RuntimeError("未找到可执行的 codex 命令。")
        with tempfile.TemporaryDirectory(prefix="travel-ctt-codex-") as temp_root:
            temp_root_path = Path(temp_root)
            workdir = temp_root_path / "workspace"
            workdir.mkdir(parents=True, exist_ok=True)
            output_path = temp_root_path / "last_message.txt"
            command = [
                command_path,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "-C",
                str(workdir),
                "-s",
                "read-only",
                "--color",
                "never",
                "-c",
                f"model_reasoning_effort={json.dumps(self.reasoning_effort)}",
                "-o",
                str(output_path),
                "-",
            ]
            if self.model:
                command[2:2] = ["-m", self.model]

            completed = self.runner(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )

            response_text = ""
            if output_path.exists():
                response_text = output_path.read_text(encoding="utf-8").strip()

        if completed.returncode != 0:
            raise RuntimeError(_format_cli_error("Codex planner 执行失败", completed))

        if not response_text:
            combined_output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
            response_text = _extract_json_text(combined_output)
        if not response_text:
            raise RuntimeError("Codex planner 未返回可解析内容。")
        return _load_json_payload(response_text)

    def _resolve_command(self) -> str:
        if Path(self.codex_cmd).exists():
            return self.codex_cmd
        candidates = [self.codex_cmd]
        if Path(self.codex_cmd).suffix == "":
            candidates = [f"{self.codex_cmd}.cmd", self.codex_cmd, f"{self.codex_cmd}.exe"]
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        windows_npm_dir = Path.home() / "AppData" / "Roaming" / "npm"
        for candidate in candidates:
            direct_path = windows_npm_dir / candidate
            if direct_path.exists():
                return str(direct_path)
        return ""


def plan_daily_itinerary(
    context: PlanningContext,
    planning_agent: BasePlanningAgent | None = None,
) -> tuple[list[DailyPlan], PlanningTrace]:
    candidate_agent = CandidatePlanningAgent()
    fallback_agent = RulePlanningAgent()
    if planning_agent and planning_agent.is_available():
        try:
            return planning_agent.plan(context)
        except Exception as exc:
            try:
                daily_plan, trace = candidate_agent.plan(context)
                trace.used_fallback = True
                trace.details = f"智能生成这轮没有完成，当前先切回自动规划继续出方案：{exc}"
                return daily_plan, trace
            except Exception:
                daily_plan, trace = fallback_agent.plan(context)
                trace.details = f"智能生成这轮没有完成，当前先切回基础规划继续出方案：{exc}"
                return daily_plan, trace
    try:
        return candidate_agent.plan(context)
    except Exception:
        pass
    return fallback_agent.plan(context)


def _planner_system_prompt() -> str:
    return (
        "你是旅行规划 agent。你会基于用户需求、酒店候选、交通候选和景点候选，生成一套主方案。\n"
        "要求：\n"
        "1. 只输出 JSON，不要输出 markdown。\n"
        "2. 结果必须覆盖全部天数。\n"
        "3. 每天只给一套主线，不给并行选项。\n"
        "4. 交通 item 的 label 必须使用“前往 XXX”这类格式，便于后续路线引擎补全真实路程。\n"
        "5. item.category 只能是 交通、游玩、餐饮、住宿 之一。\n"
        "6. 每天都要填写 theme、why_this_day、transport_strategy、meal_strategy、fallback_if_fast、fallback_if_tired。\n"
        "7. 游玩时长要现实，不要塞满。默认每天 3 到 6 个 item 即可。\n"
        "8. 尽量优先使用候选池里的景点和片区，不要凭空杜撰。"
    )


def _planner_input_payload(context: PlanningContext) -> dict:
    return {
        "request": {
            "departure_city": context.request.departure_city,
            "destination": context.request.destination,
            "start_date": context.request.start_date,
            "end_date": context.request.end_date,
            "days": context.request.days,
            "nights": context.request.nights,
            "traveler_count": context.request.traveler_count,
            "budget_per_person": context.request.budget_per_person,
            "travel_style": context.request.travel_style,
            "must_go": context.request.must_go,
            "hotel_preferences": context.request.hotel_preferences,
            "transport_preferences": context.request.transport_preferences,
            "notes": context.request.notes,
        },
        "selected_hotel": _candidate_to_prompt_dict(context.selected_hotel),
        "selected_transport": _candidate_to_prompt_dict(context.selected_transport),
        "hotel_candidates": [_candidate_to_prompt_dict(item) for item in context.hotel_candidates[:5]],
        "transport_candidates": [_candidate_to_prompt_dict(item) for item in context.transport_candidates[:5]],
        "poi_candidates": [_candidate_to_prompt_dict(item) for item in context.poi_candidates[:10]],
        "output_contract": {
            "daily_plan": [
                {
                    "day_index": 1,
                    "theme": "字符串",
                    "why_this_day": "字符串",
                    "transport_strategy": "字符串",
                    "meal_strategy": "字符串",
                    "fallback_if_fast": "字符串",
                    "fallback_if_tired": "字符串",
                    "items": [
                        {
                            "label": "字符串",
                            "category": "交通|游玩|餐饮|住宿",
                            "duration_minutes": 60,
                            "notes": "字符串",
                        }
                    ],
                }
            ]
        },
    }


def _build_codex_prompt(context: PlanningContext) -> str:
    return (
        f"{_planner_system_prompt()}\n\n"
        "请严格遵守以下输出要求：\n"
        "- 最终只输出一个 JSON 对象。\n"
        "- 顶层键必须是 daily_plan。\n"
        "- 不要输出解释、前言、代码块、额外字段说明。\n\n"
        "输入 JSON：\n"
        f"{json.dumps(_planner_input_payload(context), ensure_ascii=False, indent=2)}\n"
    )


def _candidate_to_prompt_dict(candidate: object | None) -> dict:
    if not candidate:
        return {}
    if hasattr(candidate, "to_dict"):
        return candidate.to_dict()
    return {}


def _convert_llm_payload_to_daily_plan(request: TripRequest, payload: dict) -> list[DailyPlan]:
    raw_days = payload.get("daily_plan")
    if not isinstance(raw_days, list) or not raw_days:
        raise ValueError("LLM planner 缺少 daily_plan。")

    expected_days = int(request.days or 0)
    if expected_days and len(raw_days) != expected_days:
        raise ValueError(f"LLM planner 返回天数不匹配：期望 {expected_days}，实际 {len(raw_days)}。")

    date_list = _build_date_list(request)
    result: list[DailyPlan] = []
    for index, raw_day in enumerate(raw_days, start=1):
        if not isinstance(raw_day, dict):
            raise ValueError("LLM planner 返回的 daily_plan 项格式不正确。")
        items = _convert_day_items(raw_day.get("items") or [])
        if not items:
            raise ValueError("LLM planner 返回的某一天没有 items。")
        result.append(
            DailyPlan(
                day_index=int(raw_day.get("day_index") or index),
                date=date_list[index - 1] if index - 1 < len(date_list) else request.start_date,
                theme=str(raw_day.get("theme") or f"{request.destination} 第 {index} 天"),
                items=items,
                why_this_day=str(raw_day.get("why_this_day") or ""),
                transport_strategy=str(raw_day.get("transport_strategy") or ""),
                meal_strategy=str(raw_day.get("meal_strategy") or ""),
                fallback_if_fast=str(raw_day.get("fallback_if_fast") or ""),
                fallback_if_tired=str(raw_day.get("fallback_if_tired") or ""),
            )
        )
    return result


def _convert_day_items(raw_items: Iterable[dict]) -> list[DayItem]:
    items: list[DayItem] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        label = str(raw_item.get("label") or "").strip()
        category = _normalize_category(raw_item.get("category"))
        if not label or not category:
            continue
        duration_minutes = _safe_duration(raw_item.get("duration_minutes"))
        items.append(
            DayItem(
                label=label,
                category=category,
                duration_minutes=duration_minutes,
                notes=str(raw_item.get("notes") or ""),
            )
        )
    return items


def _normalize_category(raw_value: object) -> str:
    text = str(raw_value or "").strip()
    if text in ALLOWED_ITEM_CATEGORIES:
        return text
    lower = text.lower()
    if "交通" in text or "transfer" in lower or "transit" in lower or "transport" in lower:
        return "交通"
    if "餐" in text or "food" in lower or "meal" in lower or "dining" in lower:
        return "餐饮"
    if "住" in text or "hotel" in lower or "stay" in lower or "check-in" in lower:
        return "住宿"
    if text:
        return "游玩"
    return ""


def _safe_duration(value: object) -> int:
    try:
        duration = int(float(value))
    except (TypeError, ValueError):
        duration = 60
    return max(duration, 10)


def _build_date_list(request: TripRequest) -> list[str]:
    from datetime import date, timedelta

    start = date.fromisoformat(request.start_date)
    total_days = int(request.days or 0)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(total_days)]


def _load_json_payload(text: str) -> dict:
    cleaned = _strip_code_fence(text.strip())
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    extracted = _extract_json_text(cleaned)
    if extracted:
        payload = json.loads(extracted)
        if isinstance(payload, dict):
            return payload
    raise ValueError("无法从 LLM 返回中解析 JSON。")


def _strip_code_fence(text: str) -> str:
    if text.startswith("```") and text.endswith("```"):
        parts = text.splitlines()
        if len(parts) >= 3:
            return "\n".join(parts[1:-1]).strip()
    return text


def _extract_json_text(text: str) -> str:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return text[index : index + end]
    return ""


def _format_cli_error(prefix: str, completed: subprocess.CompletedProcess[str]) -> str:
    details = []
    if completed.stdout:
        details.append(completed.stdout.strip()[-600:])
    if completed.stderr:
        details.append(completed.stderr.strip()[-600:])
    tail = "\n".join(part for part in details if part)
    if tail:
        return f"{prefix}：{tail}"
    return prefix
