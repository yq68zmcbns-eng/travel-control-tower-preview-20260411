from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..adapters.base import HotelCandidate, RouteEstimate
from .models import DailyPlan, DayItem
from .scenarios import get_scenario_route_specs


def _buffer_item(minutes: int, reason: str) -> DayItem:
    return DayItem(
        label=f"机动缓冲（{minutes} 分钟）",
        category="缓冲",
        duration_minutes=minutes,
        notes=reason,
        is_buffer=True,
    )


def _round_up_to_five(minutes: int) -> int:
    if minutes <= 0:
        return 0
    return int(((minutes + 4) // 5) * 5)


def _recommended_buffer_minutes(item: DayItem, route_estimate: RouteEstimate | None, spec: dict | None) -> int:
    if spec and spec.get("buffer_minutes") is not None:
        return int(spec["buffer_minutes"])

    if not route_estimate:
        if "到达" in item.label or "返程" in item.label:
            return 30
        return 20

    duration = max(int(route_estimate.duration_minutes or 0), 1)
    if "到达" in item.label or "返程" in item.label:
        return min(40, max(20, _round_up_to_five(int(duration * 0.35))))

    if route_estimate.mode == "WALK":
        return min(15, max(5, _round_up_to_five(int(duration * 0.2))))
    if route_estimate.mode == "TRANSIT":
        return min(25, max(10, _round_up_to_five(int(duration * 0.25))))
    if route_estimate.mode == "DRIVE":
        return min(30, max(10, _round_up_to_five(int(duration * 0.3))))
    return min(20, max(10, _round_up_to_five(int(duration * 0.25))))


def _recommended_buffer_reason(item: DayItem, route_estimate: RouteEstimate | None, spec: dict | None) -> str:
    if spec and spec.get("buffer_reason"):
        return str(spec["buffer_reason"])
    if "到达" in item.label:
        return "用于入境、取行李、出站、找路和抵达后的临时延误。"
    if "返程" in item.label:
        return "用于取行李、进站或安检，以及返程前的排队和机动时间。"
    if route_estimate and route_estimate.mode == "WALK":
        return "用于步行误差、等红灯、找入口和途中短暂停留。"
    if route_estimate and route_estimate.mode == "TRANSIT":
        return "用于进出站、等车、换乘和站内步行。"
    if route_estimate and route_estimate.mode == "DRIVE":
        return "用于上落客、等车、找停车点或临时绕行。"
    return "用于路线中的临时机动。"


def _display_mode_label(mode: str) -> str:
    return {
        "WALK": "步行",
        "TRANSIT": "公交地铁",
        "DRIVE": "打车 / 驾车",
        "AUTO_LOCAL": "自动匹配",
        "MANUAL": "保守时间预留",
    }.get(mode, mode)


def _apply_route_fields(item: DayItem, route_estimate: RouteEstimate) -> None:
    item.route_origin = route_estimate.origin_label or ""
    item.route_destination = route_estimate.destination_label or ""
    item.route_mode = route_estimate.mode or ""
    item.route_mode_label = _display_mode_label(route_estimate.mode or "")
    item.route_provider = route_estimate.provider or ""
    item.route_distance_km = float(route_estimate.distance_km or 0.0)
    item.route_summary = route_estimate.notes or ""


def enrich_daily_plan_with_route_placeholders(
    request,
    daily_plan: list[DailyPlan],
    route_adapter=None,
    selected_hotel: HotelCandidate | None = None,
) -> list[DailyPlan]:
    route_specs = get_scenario_route_specs(request) or _build_generic_route_specs(
        request,
        daily_plan,
        selected_hotel=selected_hotel,
    )
    enriched: list[DailyPlan] = []

    for day in daily_plan:
        new_items: list[DayItem] = []

        for item in day.items:
            new_items.append(item)

            if item.category != "交通":
                continue

            spec = route_specs.get((day.day_index, item.label))
            route_estimate = _estimate_route(spec, day.date, route_adapter) if spec else None
            if not route_estimate:
                route_estimate = _manual_route_estimate(spec)

            if route_estimate:
                extra_note = spec.get("post_route_note", "") if spec else ""
                if (route_estimate.provider or "").lower() == "manual" or (route_estimate.mode or "").upper() == "MANUAL":
                    mode_note = "这段先按保守时间预留，后续如果补到更细的站点或入口信息，再替换成地图实况。"
                else:
                    mode_note = f"建议本段采用{_display_mode_label(route_estimate.mode)}。"
                _apply_route_fields(item, route_estimate)
                item.notes = f"{item.notes} {mode_note} {route_estimate.notes} {extra_note}".strip()
                item.duration_minutes = route_estimate.duration_minutes or int((spec or {}).get("duration_minutes", item.duration_minutes or 0))
                buffer_minutes = _recommended_buffer_minutes(item, route_estimate, spec)
                new_items.append(_buffer_item(buffer_minutes, _recommended_buffer_reason(item, route_estimate, spec)))
                continue

            if "到达" in item.label:
                item.notes = f"{item.notes} 默认还需要单独预留 30 分钟机动，用于入境、取行李或换乘。".strip()
                item.duration_minutes = item.duration_minutes or 90
                new_items.append(_buffer_item(30, "用于到达后的排队、取行李、换乘或临时延误。"))
            elif "返程" in item.label:
                item.notes = f"{item.notes} 默认还需要单独预留 30 分钟机动，用于安检、进站或机场流程。".strip()
                item.duration_minutes = item.duration_minutes or 90
                new_items.append(_buffer_item(30, "用于返程前的安检、进站、排队或额外等待。"))
            else:
                item.notes = f"{item.notes} 默认还需要单独预留 20 分钟机动，用于找路、等车或小范围调整。".strip()
                item.duration_minutes = item.duration_minutes or 40
                new_items.append(_buffer_item(20, "用于市内移动时的找路、等车、步行误差或临时停留。"))

        day.items = new_items
        enriched.append(day)

    return enriched


def _build_generic_route_specs(request, daily_plan: list[DailyPlan], selected_hotel: HotelCandidate | None) -> dict[tuple[int, str], dict]:
    anchor = _localize_place(request, _resolve_anchor(request, selected_hotel))
    mode = _preferred_mode(request)
    specs: dict[tuple[int, str], dict] = {}

    for day in daily_plan:
        previous_place = anchor

        for item in day.items:
            if item.category == "游玩":
                previous_place = _localize_place(request, _normalize_place_label(item.label))
                continue

            if item.category != "交通":
                continue

            if item.label == "到达交通":
                specs[(day.day_index, item.label)] = {
                    "manual_summary": f"这段先按抵达 {request.destination} 后进入 {anchor} 一带预留，不把出站、找车和落脚混成一段时间。",
                    "duration_minutes": item.duration_minutes or 90,
                    "buffer_minutes": 30,
                    "buffer_reason": "用于进城、出站、找路和临时延误。",
                }
                continue

            if item.label == "返程交通":
                specs[(day.day_index, item.label)] = {
                    "manual_summary": f"返程前先按回到 {anchor} 一带取行李、再前往机场或车站的节奏预留，避免把返程压得过紧。",
                    "duration_minutes": item.duration_minutes or 90,
                    "buffer_minutes": 30,
                    "buffer_reason": "用于拿行李、进站、安检和返程前余量。",
                }
                continue

            destination = _localize_place(request, _extract_transport_destination(item.label))
            if not destination:
                continue

            specs[(day.day_index, item.label)] = {
                "origin": _localize_place(request, previous_place),
                "destination": destination,
                "mode": mode,
                "duration_minutes": item.duration_minutes or 40,
                "buffer_reason": "用于找路、等车、入场和临时停留。",
            }
            previous_place = destination

    return specs


def _resolve_anchor(request, selected_hotel: HotelCandidate | None) -> str:
    if selected_hotel:
        if selected_hotel.area and not _looks_like_preference_text(selected_hotel.area):
            return selected_hotel.area
        if selected_hotel.name and not selected_hotel.name.startswith("待锁定："):
            return selected_hotel.name
    if request.must_go:
        return request.must_go[0]
    return f"{request.destination}市中心"


def _preferred_mode(request) -> str:
    prefs = [pref.lower() for pref in request.transport_preferences]
    has_walk = any(token in prefs for token in ["步行", "walk", "walking"])
    has_transit = any(token in prefs for token in ["地铁", "公交", "transit", "subway", "bus"])
    if any(token in prefs for token in ["打车", "出租车", "car", "drive", "driving", "驾车"]):
        return "DRIVE"
    if has_transit:
        return "AUTO_LOCAL"
    if has_walk:
        return "WALK"
    return "AUTO_LOCAL"


def _normalize_place_label(label: str) -> str:
    suffixes = [" 轻松逛", "早午餐", " 与返程收尾"]
    for suffix in suffixes:
        if label.endswith(suffix):
            return label[: -len(suffix)].strip()
    return label.strip()


def _localize_place(request, place: str) -> str:
    place = (place or "").strip()
    if not place:
        return ""
    if request.destination in place or request.departure_city in place:
        return place
    if any(token in place for token in ["机场", "火车站", "高铁站", "地铁站", "酒店", "市中心"]):
        return place
    return f"{request.destination}{place}"


def _looks_like_preference_text(text: str) -> bool:
    markers = ["干净", "安静", "近地铁", "市中心或主要活动片区", "主要活动片区"]
    return any(marker in (text or "") for marker in markers)


def _extract_transport_destination(label: str) -> str:
    for prefix in ["前往 ", "步行前往 "]:
        if label.startswith(prefix):
            return label[len(prefix) :].strip()
    return ""


def _estimate_route(spec: dict | None, day_date: str, route_adapter) -> RouteEstimate | None:
    if not spec or not route_adapter:
        return None
    origin_label = str(spec.get("origin") or "").strip()
    destination_label = str(spec.get("destination") or "").strip()
    same_area_estimate = _same_area_estimate(origin_label, destination_label)
    if same_area_estimate:
        return same_area_estimate
    departure_time = None
    try:
        if spec.get("departure_time"):
            departure_time = datetime.fromisoformat(f"{day_date}T{spec['departure_time']}:00")
            tz_name = spec.get("timezone")
            if tz_name:
                try:
                    departure_time = departure_time.replace(tzinfo=ZoneInfo(tz_name))
                except ZoneInfoNotFoundError:
                    if tz_name == "Asia/Tokyo":
                        departure_time = departure_time.replace(tzinfo=timezone(timedelta(hours=9)))
        mode = spec["mode"]
        if mode == "AUTO_LOCAL":
            return _estimate_auto_local_route(route_adapter, origin_label, destination_label, departure_time)
        return route_adapter.estimate_transfer(
            origin_label=origin_label,
            destination_label=destination_label,
            mode=mode,
            departure_time=departure_time,
        )
    except Exception:
        if getattr(route_adapter, "provider_name", "") == "amap" and spec.get("mode") != "DRIVE":
            try:
                return route_adapter.estimate_transfer(
                    origin_label=origin_label,
                    destination_label=destination_label,
                    mode="DRIVE",
                    departure_time=departure_time,
                )
            except Exception:
                return None
        return None


def _estimate_auto_local_route(route_adapter, origin: str, destination: str, departure_time) -> RouteEstimate | None:
    provider = getattr(route_adapter, "provider_name", "")
    mode_candidates = ["WALK", "TRANSIT", "DRIVE"] if provider == "amap" else ["WALK", "TRANSIT", "DRIVE"]
    estimates: list[RouteEstimate] = []
    for mode in mode_candidates:
        try:
            estimate = route_adapter.estimate_transfer(
                origin_label=origin,
                destination_label=destination,
                mode=mode,
                departure_time=departure_time,
            )
        except Exception:
            continue
        estimates.append(estimate)
    if not estimates:
        return None
    return _pick_best_local_estimate(estimates)


def _pick_best_local_estimate(estimates: list[RouteEstimate]) -> RouteEstimate:
    walk_estimate = next((item for item in estimates if item.mode == "WALK"), None)
    transit_estimate = next((item for item in estimates if item.mode == "TRANSIT"), None)
    drive_estimate = next((item for item in estimates if item.mode == "DRIVE"), None)

    if walk_estimate and walk_estimate.duration_minutes <= 20 and walk_estimate.distance_km <= 2.0:
        return walk_estimate
    if transit_estimate:
        if drive_estimate:
            if transit_estimate.duration_minutes <= drive_estimate.duration_minutes + 15:
                return transit_estimate
        else:
            return transit_estimate
    if drive_estimate:
        return drive_estimate
    if walk_estimate:
        return walk_estimate
    return sorted(estimates, key=lambda item: (item.duration_minutes, item.distance_km))[0]


def _same_area_estimate(origin: str, destination: str) -> RouteEstimate | None:
    normalized_origin = _normalize_place_key(origin)
    normalized_destination = _normalize_place_key(destination)
    if not normalized_origin or not normalized_destination:
        return None
    if normalized_origin == normalized_destination or normalized_origin in normalized_destination or normalized_destination in normalized_origin:
        return RouteEstimate(
            origin_label=origin,
            destination_label=destination,
            mode="WALK",
            duration_minutes=5,
            distance_km=0.3,
            provider="manual",
            notes="起点和目的地处在同一片区，默认按步行进入，约 5 分钟。",
        )
    return None


def _normalize_place_key(value: str) -> str:
    return (
        str(value or "")
        .replace("市中心", "")
        .replace("商圈", "")
        .replace("景区", "")
        .replace("一带", "")
        .replace("片区", "")
        .replace(" ", "")
        .strip()
        .lower()
    )


def _manual_route_estimate(spec: dict | None) -> RouteEstimate | None:
    if not spec or not spec.get("manual_summary"):
        return None
    return RouteEstimate(
        origin_label=spec.get("origin", ""),
        destination_label=spec.get("destination", ""),
        mode=spec.get("mode", "MANUAL"),
        duration_minutes=int(spec.get("duration_minutes", 0)),
        distance_km=0.0,
        provider="manual",
        notes=spec["manual_summary"],
    )
