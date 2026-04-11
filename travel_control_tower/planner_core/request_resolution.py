from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Protocol

import requests

from ..runtime_config import RuntimeConfig, load_runtime_config
from .destination_autofill import suggest_destination
from .intake_parser import ParsedRequest, parse_freeform_request
from .models import TripRequest

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_PARSE_CACHE_DIR = Path.home() / ".codex" / "travel-control-tower" / "request-parser-cache"


@dataclass
class ExplicitFormOverrides:
    departure_city: str = ""
    destination: str = ""
    start_date: str = ""
    end_date: str = ""
    traveler_count: str = ""
    budget_per_person: str = ""
    travel_style: str = ""
    must_go: list[str] | None = None
    hotel_preferences: list[str] | None = None
    transport_preferences: list[str] | None = None
    enable_live_search: bool = False
    scenario_id: str = ""
    user_hotel_name: str = ""
    user_hotel_area: str = ""
    user_hotel_nightly_price: float | None = None
    user_hotel_url: str = ""
    user_transport_label: str = ""
    user_transport_category: str = ""
    user_transport_total_price: float | None = None
    user_transport_depart_at: str = ""
    user_transport_arrive_at: str = ""
    user_arrival_at_destination: str = ""
    user_return_depart_at: str = ""
    user_transport_url: str = ""
    notes: str = ""


class RequestSlotExtractor(Protocol):
    def is_available(self) -> bool: ...

    def extract(self, raw: str, *, today: date | None = None) -> ParsedRequest: ...


class OpenAIRequestSlotExtractor:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 20) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or "gpt-4.1-mini").strip()
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return bool(self.api_key)

    def extract(self, raw: str, *, today: date | None = None) -> ParsedRequest:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _request_parser_system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "today": (today or date.today()).isoformat(),
                            "request": raw,
                        },
                        ensure_ascii=False,
                    ),
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
        content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise ValueError("request parser 返回为空。")
        return _parsed_request_from_json(_load_json_payload(content))


class CodexRequestSlotExtractor:
    def __init__(
        self,
        codex_cmd: str = "codex",
        model: str = "",
        timeout_seconds: int = 60,
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

    def extract(self, raw: str, *, today: date | None = None) -> ParsedRequest:
        prompt = _build_codex_request_parser_prompt(raw, today=today)
        command_path = self._resolve_command()
        if not command_path:
            raise RuntimeError("未找到可执行的 codex 命令。")

        with tempfile.TemporaryDirectory(prefix="travel-ctt-parser-") as temp_root:
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
            response_text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""

        if completed.returncode != 0:
            raise RuntimeError(_format_cli_error("Codex request parser 执行失败", completed))

        if not response_text:
            combined_output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
            response_text = _extract_json_text(combined_output)
        if not response_text:
            raise RuntimeError("Codex request parser 未返回可解析内容。")
        return _parsed_request_from_json(_load_json_payload(response_text))

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


def resolve_trip_request(
    *,
    freeform_text: str,
    overrides: ExplicitFormOverrides,
    today=None,
    llm_extractor: RequestSlotExtractor | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> TripRequest:
    text = str(freeform_text or "").strip()
    if not text:
        raise ValueError("请先用自然语言描述你的出行需求。下方字段只负责补充约束。")

    extracted = _extract_request_slots(
        text,
        today=today,
        overrides=overrides,
        llm_extractor=llm_extractor,
        runtime_config=runtime_config,
    )
    merged = _merge_sources(extracted, overrides, text)
    if _requires_explicit_departure(text, merged):
        raise ValueError("请先补充出发地。附近城市、低价机酒这类需求需要先知道你从哪里出发。")
    return _build_trip_request(merged, overrides)


def _extract_request_slots(
    raw: str,
    *,
    today=None,
    overrides: ExplicitFormOverrides | None = None,
    llm_extractor: RequestSlotExtractor | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> ParsedRequest:
    rule_extracted = _seed_deterministic_slots(
        parse_freeform_request(raw, today=today),
        freeform_text=raw,
        departure_hint=str((overrides.departure_city if overrides else "") or "").strip(),
    )
    effective_extracted = _overlay_override_hints(rule_extracted, overrides)
    effective_extracted = _seed_deterministic_slots(
        effective_extracted,
        freeform_text=raw,
        departure_hint=str(effective_extracted.departure_city or "").strip(),
    )
    config = runtime_config or load_runtime_config()
    extractor = llm_extractor or _build_llm_request_extractor(config)
    if not extractor or not extractor.is_available():
        return effective_extracted
    if not _should_attempt_llm(raw, effective_extracted, config):
        return effective_extracted
    cache_path = _request_parse_cache_path(
        raw=raw,
        today=today,
        extractor_name=extractor.__class__.__name__,
        model=getattr(extractor, "model", "") or "",
    )
    cached = _load_cached_parsed_request(cache_path)
    if cached is not None:
        return _merge_rule_and_llm(effective_extracted, cached, prefer_llm=_prefer_llm_slots(config))
    try:
        llm_extracted = extractor.extract(raw, today=today)
    except Exception:
        return effective_extracted
    _save_cached_parsed_request(cache_path, llm_extracted)
    return _merge_rule_and_llm(effective_extracted, llm_extracted, prefer_llm=_prefer_llm_slots(config))


def _seed_deterministic_slots(parsed: ParsedRequest, *, freeform_text: str, departure_hint: str = "") -> ParsedRequest:
    departure = _normalize_departure_text(str(parsed.departure_city or "").strip()) or _normalize_departure_text(departure_hint)
    destination = _normalize_destination_text(parsed.destination)
    if not destination:
        destination = suggest_destination(
            freeform_text=freeform_text,
            departure_city=departure,
            travel_style=str(parsed.travel_style or "").strip(),
            target_days=parsed.target_trip_days or parsed.days,
            must_go=parsed.must_go,
        )
    return ParsedRequest(
        departure_city=departure,
        destination=str(destination or "").strip(),
        start_date=str(parsed.start_date or "").strip(),
        end_date=str(parsed.end_date or "").strip(),
        days=parsed.days,
        nights=parsed.nights,
        target_trip_days=parsed.target_trip_days,
        target_trip_nights=parsed.target_trip_nights,
        traveler_count=parsed.traveler_count,
        budget_per_person=parsed.budget_per_person,
        travel_style=str(parsed.travel_style or "").strip(),
        request_mode=str(parsed.request_mode or "itinerary").strip() or "itinerary",
        flexible_window_start=str(parsed.flexible_window_start or "").strip(),
        flexible_window_end=str(parsed.flexible_window_end or "").strip(),
        price_priority=str(parsed.price_priority or "balanced").strip() or "balanced",
        must_go=list(parsed.must_go or []),
        hotel_preferences=list(parsed.hotel_preferences or []),
        transport_preferences=list(parsed.transport_preferences or []),
    )


def _overlay_override_hints(parsed: ParsedRequest, overrides: ExplicitFormOverrides | None) -> ParsedRequest:
    if overrides is None:
        return parsed
    destination = _normalize_destination_text(str(overrides.destination or "").strip()) or parsed.destination
    departure = _normalize_departure_text(str(overrides.departure_city or "").strip()) or _normalize_departure_text(str(parsed.departure_city or "").strip())
    return ParsedRequest(
        departure_city=departure,
        destination=str(destination or "").strip(),
        start_date=str(overrides.start_date or "").strip() or str(parsed.start_date or "").strip(),
        end_date=str(overrides.end_date or "").strip() or str(parsed.end_date or "").strip(),
        days=parsed.days,
        nights=parsed.nights,
        target_trip_days=parsed.target_trip_days,
        target_trip_nights=parsed.target_trip_nights,
        traveler_count=_prefer_int(str(overrides.traveler_count or "").strip(), parsed.traveler_count),
        budget_per_person=_prefer_float(str(overrides.budget_per_person or "").strip(), parsed.budget_per_person),
        travel_style=str(overrides.travel_style or "").strip() or str(parsed.travel_style or "").strip(),
        request_mode=str(parsed.request_mode or "itinerary").strip() or "itinerary",
        flexible_window_start=str(parsed.flexible_window_start or "").strip(),
        flexible_window_end=str(parsed.flexible_window_end or "").strip(),
        price_priority=str(parsed.price_priority or "balanced").strip() or "balanced",
        must_go=list(overrides.must_go or []) or list(parsed.must_go or []),
        hotel_preferences=list(overrides.hotel_preferences or []) or list(parsed.hotel_preferences or []),
        transport_preferences=list(overrides.transport_preferences or []) or list(parsed.transport_preferences or []),
    )


def _build_llm_request_extractor(config: RuntimeConfig) -> RequestSlotExtractor | None:
    mode = str(config.request_parser_mode or "auto").strip().lower()
    if mode in {"rule", "disabled", "off"}:
        return None
    if mode in {"openai", "api"}:
        if not str(config.openai_api_key or "").strip():
            return None
        model = str(config.request_parser_model or config.planner_model or "gpt-4.1-mini").strip()
        return OpenAIRequestSlotExtractor(api_key=config.openai_api_key, model=model)
    if mode in {"codex", "codex_cli"}:
        return CodexRequestSlotExtractor(
            codex_cmd=config.codex_cmd,
            model=config.request_parser_model or config.codex_planner_model or "",
        )
    if str(config.openai_api_key or "").strip():
        model = str(config.request_parser_model or config.planner_model or "gpt-4.1-mini").strip()
        return OpenAIRequestSlotExtractor(api_key=config.openai_api_key, model=model)
    return CodexRequestSlotExtractor(
        codex_cmd=config.codex_cmd,
        model=config.request_parser_model or config.codex_planner_model or "",
    )


def _should_attempt_llm(raw: str, extracted: ParsedRequest, config: RuntimeConfig) -> bool:
    mode = str(config.request_parser_mode or "auto").strip().lower()
    if mode in {"openai", "llm", "always"}:
        return True
    if mode in {"rule", "disabled", "off"}:
        return False
    if _requires_explicit_departure(raw, extracted):
        return False

    if not _normalize_departure_text(str(extracted.departure_city or "").strip()):
        return True
    if not str(extracted.destination or "").strip():
        return True
    if not str(extracted.start_date or "").strip() and not str(extracted.flexible_window_start or "").strip():
        return True
    if extracted.target_trip_days is None and extracted.days is None:
        return True
    if extracted.budget_per_person is None:
        return True
    return False


def _prefer_llm_slots(config: RuntimeConfig) -> bool:
    mode = str(config.request_parser_mode or "auto").strip().lower()
    return mode in {"openai", "api", "llm", "always", "codex", "codex_cli"}


def _merge_rule_and_llm(rule: ParsedRequest, llm: ParsedRequest, *, prefer_llm: bool) -> ParsedRequest:
    def pick_text(rule_value: str, llm_value: str) -> str:
        llm_clean = str(llm_value or "").strip()
        rule_clean = str(rule_value or "").strip()
        if prefer_llm and llm_clean:
            return llm_clean
        return rule_clean or llm_clean

    def pick_number(rule_value, llm_value):
        if prefer_llm and llm_value not in ("", None):
            return llm_value
        return rule_value if rule_value not in ("", None) else llm_value

    def pick_list(rule_value: list[str], llm_value: list[str]) -> list[str]:
        if prefer_llm and llm_value:
            return list(llm_value)
        return list(rule_value or []) or list(llm_value or [])

    return ParsedRequest(
        departure_city=pick_text(rule.departure_city, llm.departure_city),
        destination=_normalize_destination_text(pick_text(rule.destination, llm.destination)),
        start_date=pick_text(rule.start_date, llm.start_date),
        end_date=pick_text(rule.end_date, llm.end_date),
        days=pick_number(rule.days, llm.days),
        nights=pick_number(rule.nights, llm.nights),
        target_trip_days=pick_number(rule.target_trip_days, llm.target_trip_days),
        target_trip_nights=pick_number(rule.target_trip_nights, llm.target_trip_nights),
        traveler_count=pick_number(rule.traveler_count, llm.traveler_count),
        budget_per_person=pick_number(rule.budget_per_person, llm.budget_per_person),
        travel_style=pick_text(rule.travel_style, llm.travel_style),
        request_mode=pick_text(rule.request_mode, llm.request_mode) or "itinerary",
        flexible_window_start=pick_text(rule.flexible_window_start, llm.flexible_window_start),
        flexible_window_end=pick_text(rule.flexible_window_end, llm.flexible_window_end),
        price_priority=pick_text(rule.price_priority, llm.price_priority) or "balanced",
        must_go=pick_list(rule.must_go, llm.must_go),
        hotel_preferences=pick_list(rule.hotel_preferences, llm.hotel_preferences),
        transport_preferences=pick_list(rule.transport_preferences, llm.transport_preferences),
    )


def _merge_sources(
    extracted: ParsedRequest,
    overrides: ExplicitFormOverrides,
    freeform_text: str,
) -> ParsedRequest:
    extracted_destination = _normalize_destination_text(extracted.destination)
    extracted_departure = _normalize_departure_text(extracted.departure_city)
    override_departure = _normalize_departure_text(str(overrides.departure_city or "").strip())
    merged = ParsedRequest(
        departure_city=override_departure or extracted_departure,
        destination=str(overrides.destination or "").strip() or extracted_destination,
        start_date=str(overrides.start_date or "").strip() or extracted.start_date,
        end_date=str(overrides.end_date or "").strip() or extracted.end_date,
        days=extracted.days,
        nights=extracted.nights,
        target_trip_days=extracted.target_trip_days,
        target_trip_nights=extracted.target_trip_nights,
        traveler_count=_prefer_int(overrides.traveler_count, extracted.traveler_count),
        budget_per_person=_prefer_float(overrides.budget_per_person, extracted.budget_per_person),
        travel_style=str(overrides.travel_style or "").strip() or extracted.travel_style or "balanced",
        request_mode=extracted.request_mode or "itinerary",
        flexible_window_start=extracted.flexible_window_start or "",
        flexible_window_end=extracted.flexible_window_end or "",
        price_priority=extracted.price_priority or "balanced",
        must_go=list(overrides.must_go or []) or list(extracted.must_go or []),
        hotel_preferences=list(overrides.hotel_preferences or []) or list(extracted.hotel_preferences or []),
        transport_preferences=list(overrides.transport_preferences or []) or list(extracted.transport_preferences or []),
    )

    if not str(merged.destination or "").strip():
        merged.destination = suggest_destination(
            freeform_text=freeform_text,
            departure_city=str(merged.departure_city or "").strip(),
            travel_style=str(merged.travel_style or "").strip(),
            target_days=merged.target_trip_days or merged.days,
            must_go=merged.must_go,
        )

    return merged


def _requires_explicit_departure(raw: str, extracted: ParsedRequest) -> bool:
    text = str(raw or "").strip()
    if _normalize_departure_text(str(extracted.departure_city or "").strip()):
        return False
    trigger_tokens = (
        "附近城市",
        "周边城市",
        "适合周末的城市",
        "周末城市",
        "飞日本",
        "飞东京",
        "飞大阪",
        "机票",
        "机酒",
        "低价",
        "便宜",
    )
    return any(token in text for token in trigger_tokens)


def _normalize_departure_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    generic_tokens = ("我所在城市", "所在城市", "当前位置", "当前城市", "本地", "出发地")
    if any(token == text for token in generic_tokens):
        return ""
    return text


def _build_trip_request(extracted: ParsedRequest, overrides: ExplicitFormOverrides) -> TripRequest:
    return TripRequest(
        departure_city=str(extracted.departure_city or "").strip(),
        destination=str(extracted.destination or "").strip(),
        start_date=str(extracted.start_date or "").strip(),
        end_date=str(extracted.end_date or "").strip(),
        traveler_count=int(extracted.traveler_count or 1),
        enable_live_search=bool(overrides.enable_live_search),
        scenario_id=str(overrides.scenario_id or "").strip(),
        request_mode=extracted.request_mode or "itinerary",
        flexible_window_start=extracted.flexible_window_start or "",
        flexible_window_end=extracted.flexible_window_end or "",
        target_trip_days=extracted.target_trip_days,
        target_trip_nights=extracted.target_trip_nights,
        price_priority=extracted.price_priority or "balanced",
        days=extracted.days,
        nights=extracted.nights,
        budget_per_person=extracted.budget_per_person,
        travel_style=str(extracted.travel_style or "balanced"),
        must_go=list(extracted.must_go or []),
        hotel_preferences=list(extracted.hotel_preferences or []),
        transport_preferences=list(extracted.transport_preferences or []),
        user_hotel_name=str(overrides.user_hotel_name or "").strip(),
        user_hotel_area=str(overrides.user_hotel_area or "").strip(),
        user_hotel_nightly_price=overrides.user_hotel_nightly_price,
        user_hotel_url=str(overrides.user_hotel_url or "").strip(),
        user_transport_label=str(overrides.user_transport_label or "").strip(),
        user_transport_category=str(overrides.user_transport_category or "").strip(),
        user_transport_total_price=overrides.user_transport_total_price,
        user_transport_depart_at=str(overrides.user_transport_depart_at or "").strip(),
        user_transport_arrive_at=str(overrides.user_transport_arrive_at or "").strip(),
        user_arrival_at_destination=str(overrides.user_arrival_at_destination or "").strip(),
        user_return_depart_at=str(overrides.user_return_depart_at or "").strip(),
        user_transport_url=str(overrides.user_transport_url or "").strip(),
        needs_exports=["excel"],
        notes=str(overrides.notes or "").strip(),
    )


def _parsed_request_from_json(payload: dict) -> ParsedRequest:
    must_go = _coerce_str_list(payload.get("must_go"))
    hotel_preferences = _coerce_str_list(payload.get("hotel_preferences"))
    transport_preferences = _coerce_str_list(payload.get("transport_preferences"))
    return ParsedRequest(
        departure_city=_coerce_text(payload.get("departure_city")),
        destination=_coerce_text(payload.get("destination")),
        start_date=_coerce_date(payload.get("start_date")),
        end_date=_coerce_date(payload.get("end_date")),
        days=_coerce_int(payload.get("days")),
        nights=_coerce_int(payload.get("nights")),
        target_trip_days=_coerce_int(payload.get("target_trip_days")),
        target_trip_nights=_coerce_int(payload.get("target_trip_nights")),
        traveler_count=_coerce_int(payload.get("traveler_count")),
        budget_per_person=_coerce_float(payload.get("budget_per_person")),
        travel_style=_normalize_style(_coerce_text(payload.get("travel_style"))),
        request_mode=_normalize_request_mode(_coerce_text(payload.get("request_mode"))),
        flexible_window_start=_coerce_date(payload.get("flexible_window_start")),
        flexible_window_end=_coerce_date(payload.get("flexible_window_end")),
        price_priority=_normalize_price_priority(_coerce_text(payload.get("price_priority"))),
        must_go=must_go,
        hotel_preferences=hotel_preferences,
        transport_preferences=transport_preferences,
    )


def _request_parser_system_prompt() -> str:
    return (
        "你是旅行需求参数抽取器。"
        "根据用户的一段自然语言，抽取旅行规划所需参数并返回 JSON。"
        "如果用户说去附近城市、周边城市、适合周末的城市、随便哪里但不要太远，"
        "你需要结合出发地、预算、风格，替用户选一个具体城市，不能返回'附近城市'这种泛词。"
        "日期必须按传入的 today 解析成 YYYY-MM-DD。"
        "未知字段返回空字符串、null 或空数组。"
        "只返回 JSON，不要解释。"
        "JSON keys: departure_city, destination, start_date, end_date, days, nights, target_trip_days, "
        "target_trip_nights, traveler_count, budget_per_person, travel_style, request_mode, "
        "flexible_window_start, flexible_window_end, price_priority, must_go, hotel_preferences, transport_preferences. "
        "travel_style 只能是 relaxed/balanced/packed。"
        "request_mode 只能是 itinerary/price_scan。"
        "price_priority 只能是 balanced/low。"
    )


def _build_codex_request_parser_prompt(raw: str, *, today: date | None = None) -> str:
    return (
        f"{_request_parser_system_prompt()}\n\n"
        "今天日期:\n"
        f"{(today or date.today()).isoformat()}\n\n"
        "用户自然语言需求:\n"
        f"{raw.strip()}\n"
    )


def _load_json_payload(content: str) -> dict:
    text = str(content or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = _extract_json_text(text)
        if extracted:
            return json.loads(extracted)
        raise


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


def _coerce_text(value) -> str:
    return str(value or "").strip()


def _request_parse_cache_path(*, raw: str, today, extractor_name: str, model: str) -> Path:
    REQUEST_PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "raw": str(raw or "").strip(),
            "today": (today or date.today()).isoformat() if hasattr(today, "isoformat") else str(today or ""),
            "extractor": str(extractor_name or "").strip(),
            "model": str(model or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return REQUEST_PARSE_CACHE_DIR / f"{digest}.json"


def _load_cached_parsed_request(path: Path) -> ParsedRequest | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return _parsed_request_from_json(payload)
    except Exception:
        return None


def _save_cached_parsed_request(path: Path, parsed: ParsedRequest) -> None:
    try:
        path.write_text(
            json.dumps(
                {
                    "departure_city": parsed.departure_city,
                    "destination": parsed.destination,
                    "start_date": parsed.start_date,
                    "end_date": parsed.end_date,
                    "days": parsed.days,
                    "nights": parsed.nights,
                    "target_trip_days": parsed.target_trip_days,
                    "target_trip_nights": parsed.target_trip_nights,
                    "traveler_count": parsed.traveler_count,
                    "budget_per_person": parsed.budget_per_person,
                    "travel_style": parsed.travel_style,
                    "request_mode": parsed.request_mode,
                    "flexible_window_start": parsed.flexible_window_start,
                    "flexible_window_end": parsed.flexible_window_end,
                    "price_priority": parsed.price_priority,
                    "must_go": parsed.must_go,
                    "hotel_preferences": parsed.hotel_preferences,
                    "transport_preferences": parsed.transport_preferences,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        return


def _normalize_destination_text(value: str) -> str:
    text = str(value or "").strip()
    generic_tokens = ("附近城市", "周边城市", "周末城市", "适合周末的城市", "近一点的地方", "随便哪里")
    if not text:
        return ""
    if any(token in text for token in generic_tokens):
        return ""
    return text


def _coerce_int(value) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_date(value) -> str:
    text = _coerce_text(value)
    if len(text) == 10:
        return text
    return ""


def _coerce_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _normalize_style(value: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"relaxed", "balanced", "packed"} else ""


def _normalize_request_mode(value: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"itinerary", "price_scan"} else ""


def _normalize_price_priority(value: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"balanced", "low"} else ""


def _prefer_int(raw: str, fallback: int | None) -> int | None:
    value = str(raw or "").strip()
    if not value:
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def _prefer_float(raw: str, fallback: float | None) -> float | None:
    value = str(raw or "").strip()
    if not value:
        return fallback
    try:
        return float(value)
    except ValueError:
        return fallback
