from __future__ import annotations

from datetime import date, timedelta

from ..adapters.base import POICandidate
from .city_profiles import meal_hint_for, resolve_default_points
from .models import DailyPlan, DayItem, TripRequest
from .poi_scoring import (
    arrival_friendliness_score,
    extract_route_minutes,
    is_business_building,
    is_generic_urban_park,
    is_photo_spot,
    poi_quality_score,
)


def build_daily_plan(request: TripRequest, poi_candidates: list[POICandidate] | None = None) -> list[DailyPlan]:
    start = date.fromisoformat(request.start_date)
    total_days = int(request.days or 0)
    if total_days <= 0:
        return []

    effective_points, poi_meta = _resolve_points(request, poi_candidates or [])
    remaining_points = list(effective_points)
    used_points: list[str] = []
    plans: list[DailyPlan] = []

    for offset in range(total_days):
        current = start + timedelta(days=offset)
        day_index = offset + 1
        stage = _day_stage(day_index, total_days)
        capacity = _stage_capacity(stage)
        points = _pick_points_for_stage(remaining_points, request, poi_meta, stage, capacity, used_points)
        for point in points:
            if point in remaining_points:
                remaining_points.remove(point)
            if point not in used_points:
                used_points.append(point)

        if stage == "arrival":
            items, why, transport_strategy, meal_strategy, fallback_if_fast, fallback_if_tired = _arrival_day(
                request, points, poi_meta
            )
        elif stage in {"departure", "compact_departure"}:
            items, why, transport_strategy, meal_strategy, fallback_if_fast, fallback_if_tired = _departure_day(
                request, points, poi_meta, stage=stage
            )
        else:
            items, why, transport_strategy, meal_strategy, fallback_if_fast, fallback_if_tired = _core_day(
                request, points, poi_meta
            )

        plans.append(
            DailyPlan(
                day_index=day_index,
                date=current.isoformat(),
                theme=_build_day_theme(day_index, total_days, points, request.destination),
                items=items,
                why_this_day=why,
                transport_strategy=transport_strategy,
                meal_strategy=meal_strategy,
                fallback_if_fast=fallback_if_fast,
                fallback_if_tired=fallback_if_tired,
            )
        )

    return plans


def _resolve_points(
    request: TripRequest,
    poi_candidates: list[POICandidate],
) -> tuple[list[str], dict[str, POICandidate]]:
    poi_meta = {item.name: item for item in poi_candidates if item.name}
    points: list[str] = []

    if request.must_go:
        for raw in request.must_go:
            point = _match_must_go(raw, poi_candidates) or raw.strip()
            if point and point not in points:
                points.append(point)

    if poi_candidates:
        for candidate in poi_candidates[:8]:
            if candidate.name and candidate.name not in points:
                points.append(candidate.name)

    default_points = resolve_default_points(request.destination)
    for point in default_points:
        if point and point not in points:
            points.append(point)

    if not points:
        points = default_points

    return points, poi_meta


def _match_must_go(keyword: str, poi_candidates: list[POICandidate]) -> str:
    normalized_keyword = _normalize(keyword)
    if not normalized_keyword:
        return ""
    for candidate in poi_candidates:
        normalized_name = _normalize(candidate.name)
        if normalized_keyword in normalized_name or normalized_name in normalized_keyword:
            return candidate.name
    return ""


def _normalize(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def _day_stage(day_index: int, total_days: int) -> str:
    if day_index == 1:
        return "arrival"
    if day_index == total_days:
        return "compact_departure" if total_days == 2 else "departure"
    return "core"


def _stage_capacity(stage: str) -> int:
    if stage == "arrival":
        return 1
    if stage == "core":
        return 2
    return 1


def _pick_points_for_stage(
    remaining_points: list[str],
    request: TripRequest,
    poi_meta: dict[str, POICandidate],
    stage: str,
    capacity: int,
    used_points: list[str],
) -> list[str]:
    if not remaining_points or capacity <= 0:
        return []

    if not request.must_go and stage in {"arrival", "compact_departure", "departure"} and len(poi_meta) < 2:
        anchor_pick = _pick_profile_anchor_for_stage(remaining_points, request.destination, stage)
        if anchor_pick:
            return [anchor_pick]

    if stage == "core" and capacity >= 2:
        primary = _pick_single_point(remaining_points, request, poi_meta, stage, used_points)
        if not primary:
            return []
        leftovers = [point for point in remaining_points if point != primary]
        if not leftovers:
            return [primary]
        secondary = sorted(
            leftovers,
            key=lambda point: (
                -(
                    _score_point_for_stage(point, request, poi_meta.get(point), stage, used_points, poi_meta)
                    + _pair_diversity_bonus(primary, point, poi_meta)
                ),
                leftovers.index(point),
            ),
        )[0]
        return [primary, secondary]

    return _pick_single_points(remaining_points, request, poi_meta, stage, capacity, used_points)


def _pick_single_points(
    remaining_points: list[str],
    request: TripRequest,
    poi_meta: dict[str, POICandidate],
    stage: str,
    capacity: int,
    used_points: list[str],
) -> list[str]:
    ranked = sorted(
        remaining_points,
        key=lambda point: (
            -_score_point_for_stage(point, request, poi_meta.get(point), stage, used_points, poi_meta),
            remaining_points.index(point),
        ),
    )
    return ranked[:capacity]


def _pick_profile_anchor_for_stage(remaining_points: list[str], destination: str, stage: str) -> str:
    anchors = resolve_default_points(destination)
    if not anchors:
        return ""

    target_index = 0
    if stage == "compact_departure":
        target_index = 1 if len(anchors) >= 2 else 0
    elif stage == "departure":
        target_index = min(max(len(anchors) - 1, 0), 2)

    preferred = anchors[target_index] if target_index < len(anchors) else anchors[0]
    preferred_key = _normalize(preferred)
    for point in remaining_points:
        point_key = _normalize(point)
        if preferred_key and (preferred_key == point_key or preferred_key in point_key or point_key in preferred_key):
            return point
    return ""


def _pick_single_point(
    remaining_points: list[str],
    request: TripRequest,
    poi_meta: dict[str, POICandidate],
    stage: str,
    used_points: list[str],
) -> str:
    points = _pick_single_points(remaining_points, request, poi_meta, stage, 1, used_points)
    return points[0] if points else ""


def _score_point_for_stage(
    point: str,
    request: TripRequest,
    poi: POICandidate | None,
    stage: str,
    used_points: list[str],
    poi_meta: dict[str, POICandidate],
) -> int:
    score = _must_go_priority(point, request)
    haystack = " ".join(
        [
            point,
            poi.category if poi else "",
            poi.poi_level if poi else "",
            poi.notes if poi else "",
            poi.address if poi else "",
        ]
    )

    score += poi_quality_score(
        name=point,
        category=poi.category if poi else "",
        level=poi.poi_level if poi else "",
        notes=poi.notes if poi else "",
        address=poi.address if poi else "",
    )
    score += _profile_anchor_score(point, request.destination, stage)
    route_minutes = extract_route_minutes(poi.notes if poi else "", poi.address if poi else "")

    if stage == "arrival":
        score += arrival_friendliness_score(
            name=point,
            category=poi.category if poi else "",
            level=poi.poi_level if poi else "",
            notes=poi.notes if poi else "",
            address=poi.address if poi else "",
        )
        if _contains_any(haystack, ("博物馆", "museum")):
            score -= 18
        if route_minutes is not None:
            if route_minutes <= 20:
                score += 10
            elif route_minutes >= 45:
                score -= 18
    elif stage == "core":
        if _contains_any(haystack, ("步行街", "商圈", "夜市")):
            score += 8
        if route_minutes is not None:
            if route_minutes <= 25:
                score += 8
            elif route_minutes >= 55:
                score -= 14
    elif stage == "compact_departure":
        if _contains_any(haystack, ("景区", "宫", "陵", "古城", "城墙", "地标", "5a", "aaaaa", "公园", "寺", "塔")):
            score += 36
        if _contains_any(haystack, ("陵",)):
            score += 12
        if _contains_any(haystack, ("博物馆", "museum")):
            score -= 30
        if _contains_any(haystack, ("山", "自然", "森林", "郊野")):
            score -= 8
        if route_minutes is not None:
            if route_minutes <= 25:
                score += 14
            elif route_minutes >= 45:
                score -= 20
    else:  # departure
        if _contains_any(haystack, ("商圈", "步行街", "老街", "博物馆", "museum", "地标")):
            score += 20
        if _contains_any(haystack, ("山", "自然", "森林", "景区", "陵")):
            score -= 18
        if route_minutes is not None:
            if route_minutes <= 25:
                score += 10
            elif route_minutes >= 45:
                score -= 18
    if is_generic_urban_park(haystack):
        score -= 12
    if is_photo_spot(haystack):
        score -= 14
    if is_business_building(haystack):
        score -= 25
    score += _cross_day_diversity_adjustment(point, poi, used_points, poi_meta, stage)

    return score


def _cross_day_diversity_adjustment(
    point: str,
    poi: POICandidate | None,
    used_points: list[str],
    poi_meta: dict[str, POICandidate],
    stage: str,
) -> int:
    if not used_points:
        return 0

    adjustment = 0
    point_district = _extract_district(poi.notes if poi else "")
    point_text = " ".join([point, poi.category if poi else "", poi.notes if poi else "", poi.address if poi else ""])

    for used_point in used_points[-2:]:
        used_poi = poi_meta.get(used_point)
        used_district = _extract_district(used_poi.notes if used_poi else "")
        used_text = " ".join(
            [
                used_point,
                used_poi.category if used_poi else "",
                used_poi.notes if used_poi else "",
                used_poi.address if used_poi else "",
            ]
        )

        if point_district and used_district and point_district == used_district:
            adjustment -= 18
        if _contains_any(point_text, ("步行街", "商圈", "老街", "古街", "博物馆", "museum")) and _contains_any(
            used_text, ("步行街", "商圈", "老街", "古街", "博物馆", "museum")
        ):
            adjustment -= 10
        if stage in {"departure", "compact_departure"} and _contains_any(point_text, ("陵", "宫", "寺", "塔", "景区", "古城", "城墙")):
            adjustment += 8

    return adjustment


def _profile_anchor_score(point: str, destination: str, stage: str) -> int:
    anchors = resolve_default_points(destination)
    normalized_point = _normalize(point)
    for index, anchor in enumerate(anchors[:4]):
        normalized_anchor = _normalize(anchor)
        if not normalized_anchor:
            continue
        if normalized_anchor == normalized_point or normalized_anchor in normalized_point or normalized_point in normalized_anchor:
            if stage == "arrival":
                return 44 - index * 8
            if stage in {"departure", "compact_departure"}:
                return 54 - index * 10
            return 22 - index * 4
    return 0


def _pair_diversity_bonus(primary: str, candidate: str, poi_meta: dict[str, POICandidate]) -> int:
    primary_poi = poi_meta.get(primary)
    candidate_poi = poi_meta.get(candidate)
    primary_text = " ".join(
        [
            primary,
            primary_poi.category if primary_poi else "",
            primary_poi.address if primary_poi else "",
        ]
    )
    candidate_text = " ".join(
        [
            candidate,
            candidate_poi.category if candidate_poi else "",
            candidate_poi.address if candidate_poi else "",
        ]
    )

    score = 0
    if any(token in primary_text and token in candidate_text for token in ("塔", "观景平台", "打卡点", "步行街", "商圈", "博物馆")):
        score -= 18
    if any(token in primary_text and token in candidate_text for token in ("历史街区", "古街", "老街")):
        score -= 12
    if _normalize(primary) in _normalize(candidate) or _normalize(candidate) in _normalize(primary):
        score -= 25
    if _contains_any(primary_text, ("步行街", "商圈", "老街", "古街")) and _contains_any(
        candidate_text, ("步行街", "商圈", "老街", "古街")
    ):
        score -= 10
    if _contains_any(primary_text, ("博物馆", "museum")) and _contains_any(candidate_text, ("博物馆", "museum")):
        score -= 12
    if _contains_any(primary_text, ("步行街", "商圈", "老街", "古街")) and _contains_any(
        candidate_text, ("寺", "宫", "城墙", "景区", "博物馆", "museum", "古城")
    ):
        score += 8
    if _contains_any(primary_text, ("景区", "宫", "城墙", "古城", "寺")) and _contains_any(
        candidate_text, ("步行街", "商圈", "老街", "古街", "博物馆", "museum")
    ):
        score += 8
    return score


def _must_go_priority(point: str, request: TripRequest) -> int:
    normalized_point = _normalize(point)
    for index, keyword in enumerate(request.must_go):
        normalized_keyword = _normalize(keyword)
        if normalized_keyword and (
            normalized_keyword in normalized_point or normalized_point in normalized_keyword
        ):
            return 220 - index * 20
    return 0


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    haystack = _normalize(value)
    return any(_normalize(token) in haystack for token in tokens)


def _extract_district(notes: str) -> str:
    raw = str(notes or "")
    marker = "区县："
    if marker not in raw:
        return ""
    segment = raw.split(marker, 1)[1]
    return segment.split("；", 1)[0].strip()


def _build_day_theme(day_index: int, total_days: int, points: list[str], destination: str) -> str:
    if points:
        if day_index == 1:
            return f"抵达后先逛 {points[0]}"
        if day_index == total_days:
            return f"{points[0]} 与返程收尾"
        if len(points) >= 2:
            return f"{points[0]} 与 {points[1]}"
        return points[0]
    if day_index == 1:
        return "抵达与开场"
    if day_index == total_days:
        return "收尾与返程"
    return f"{destination} 市区主线"


def _arrival_day(
    request: TripRequest,
    points: list[str],
    poi_meta: dict[str, POICandidate],
) -> tuple[list[DayItem], str, str, str, str, str]:
    first_point = points[0] if points else ""
    meal_hint = meal_hint_for(request.destination, first_point, "arrival")
    meal_label, meal_notes = _resolve_meal_hint(
        meal_hint,
        stage="arrival",
        point=first_point,
        fallback_label="第一顿正餐",
        fallback_notes="第一顿放在当天主片区，避免到达后立刻跨区折返。",
    )

    items = [
        DayItem("到达交通", "交通", duration_minutes=90, notes="先完成到达、进城和落脚，再开始当天活动。"),
        DayItem("酒店入住或寄存行李", "住宿", duration_minutes=30, notes="入住、寄存和整理行李单独计算，不与路上时间混在一起。"),
    ]
    if first_point:
        items.append(DayItem(f"前往 {first_point}", "交通", duration_minutes=35, notes=f"第一天先去 {first_point}，把到达日压在同一片区。"))
        items.append(
            DayItem(
                f"{first_point} 轻松逛",
                "游玩",
                duration_minutes=_poi_duration(first_point, poi_meta, is_primary=False),
                notes=_build_poi_play_note(first_point, poi_meta, "第一天不再叠第二个重点景点，重点把到达后的节奏稳住。"),
            )
        )
    else:
        items.append(
            DayItem(
                "酒店周边轻松逛",
                "游玩",
                duration_minutes=120,
                notes="到达日优先适应城市节奏，不建议再安排跨区景点。",
            )
        )
    items.append(DayItem(meal_label, "餐饮", duration_minutes=75, notes=meal_notes))

    why = "到达日先把活动压在一条线内，给大交通、入住和找路留余量。"
    if first_point:
        why = f"到达日先把重心放在 {first_point}，不额外拆第二片区，这样更稳。"
    transport_strategy = "先用一段主交通进入住宿片区，之后尽量用步行或一小段短驳解决。"
    meal_strategy = "第一顿正餐直接放在当天主片区，少一次折返。"
    fallback_if_fast = "如果到得更早，可以在同一片区补一个轻松散步点，不临时跨区。"
    fallback_if_tired = "如果状态一般，只保留入住、主片区和晚饭。"
    return items, why, transport_strategy, meal_strategy, fallback_if_fast, fallback_if_tired


def _core_day(
    request: TripRequest,
    points: list[str],
    poi_meta: dict[str, POICandidate],
) -> tuple[list[DayItem], str, str, str, str, str]:
    primary = points[0] if points else f"{request.destination} 核心片区"
    secondary = points[1] if len(points) > 1 else ""

    lunch_hint = meal_hint_for(request.destination, primary, "lunch")
    lunch_label, lunch_notes = _resolve_meal_hint(
        lunch_hint,
        stage="lunch",
        point=primary,
        fallback_label="午饭",
        fallback_notes="午饭放在当天第一段附近，避免为了吃饭再折返。",
    )

    dinner_focus = secondary or primary
    dinner_hint = meal_hint_for(request.destination, dinner_focus, "dinner")
    dinner_label, dinner_notes = _resolve_meal_hint(
        dinner_hint,
        stage="dinner",
        point=dinner_focus,
        fallback_label="晚饭",
        fallback_notes="晚饭放在当天最后一段活动附近，方便收尾回酒店。",
    )

    items = [
        DayItem(f"前往 {primary}", "交通", duration_minutes=40, notes=f"先去 {primary}，把当天最重的一段活动放在白天。"),
        DayItem(primary, "游玩", duration_minutes=_poi_duration(primary, poi_meta, is_primary=True), notes=_build_poi_play_note(primary, poi_meta, "这是当天第一段主活动，优先安排在上午到中午。")),
        DayItem(lunch_label, "餐饮", duration_minutes=60, notes=lunch_notes),
    ]

    if secondary:
        items.append(DayItem(f"前往 {secondary}", "交通", duration_minutes=30, notes=f"下午转去 {secondary}，控制在相邻片区内移动。"))
        items.append(
            DayItem(
                secondary,
                "游玩",
                duration_minutes=_poi_duration(secondary, poi_meta, is_primary=False),
                notes=_build_poi_play_note(secondary, poi_meta, "第二段尽量和第一段保持相邻，避免大跨区移动。"),
            )
        )

    items.append(DayItem(dinner_label, "餐饮", duration_minutes=75, notes=dinner_notes))

    point_text = "、".join(points[:2]) if points else f"{request.destination} 主片区"
    why = f"这一天围绕 {point_text} 展开，尽量不做跨城或跨大片区折返。"
    transport_strategy = "全天控制在一到两个相邻片区内，先主片区，后补充片区。"
    meal_strategy = "午饭跟着第一段主活动走，晚饭放在最后一段活动附近。"
    fallback_if_fast = "如果进度更快，优先补同一片区的小点，不临时换区。"
    fallback_if_tired = "如果状态一般，直接删掉第二段活动，只保留晚饭。"
    return items, why, transport_strategy, meal_strategy, fallback_if_fast, fallback_if_tired


def _departure_day(
    request: TripRequest,
    points: list[str],
    poi_meta: dict[str, POICandidate],
    *,
    stage: str,
) -> tuple[list[DayItem], str, str, str, str, str]:
    last_point = points[0] if points else ""
    meal_hint = meal_hint_for(request.destination, last_point, "departure")
    meal_label, meal_notes = _resolve_meal_hint(
        meal_hint,
        stage="departure",
        point=last_point,
        fallback_label="返程前简餐",
        fallback_notes="最后一顿尽量放在回程线附近，不再绕路找餐厅。",
    )

    items = [
        DayItem("退房与行李处理", "住宿", duration_minutes=40, notes="先退房和寄存，最后再回来取行李。"),
    ]
    if last_point:
        items.append(DayItem(f"前往 {last_point}", "交通", duration_minutes=30, notes=f"最后一天只保留 {last_point} 这一段，避免返程前折返。"))
        items.append(
            DayItem(
                last_point,
                "游玩",
                duration_minutes=_poi_duration(last_point, poi_meta, is_primary=(stage == "compact_departure")),
                notes=_build_poi_play_note(last_point, poi_meta, "最后半天只保留一个主点，不再临时扩张行程。"),
            )
        )
    else:
        items.append(
            DayItem("返程前收尾活动", "游玩", duration_minutes=120, notes="最后半天只做轻量活动，不再安排远距离景点。")
        )

    items.append(DayItem(meal_label, "餐饮", duration_minutes=60, notes=meal_notes))
    items.append(DayItem("返程交通", "交通", duration_minutes=90, notes="返程段按保守口径处理，路上时间和缓冲时间分开计算。"))

    why = "最后一天先给退房和返程留余量，再决定还能保留哪一段活动。"
    if last_point:
        why = f"最后一天只保留 {last_point} 这一段，避免返程前还在跨区折返。"
    transport_strategy = "最后一天只做一段主移动，确保返程前可控。"
    meal_strategy = "最后一顿尽量贴着返程线安排，不专门绕路。"
    fallback_if_fast = "如果返程较晚，可以在同一片区补一个轻量小点。"
    fallback_if_tired = "如果不想再走，直接在酒店附近收尾后前往机场或车站。"
    return items, why, transport_strategy, meal_strategy, fallback_if_fast, fallback_if_tired


def _build_poi_play_note(point: str, poi_meta: dict[str, POICandidate], fallback: str) -> str:
    poi = poi_meta.get(point)
    if not poi:
        return fallback

    parts = [fallback]
    category = _display_poi_category(poi.category)
    if category:
        parts.append(f"类型：{category}")
    poi_level = _display_poi_level(poi.poi_level)
    if poi_level:
        parts.append(f"等级：{poi_level}")
    free_status = _display_free_status(poi.free_status)
    if free_status:
        parts.append(f"门票：{free_status}")
    if poi.address:
        parts.append(f"位置：{poi.address}")
    return " ".join(parts)


def _display_poi_level(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return ""
    return text


def _display_poi_category(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return ""
    return text


def _display_free_status(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.lower()
    if normalized in {"unknown", "none", "null"}:
        return ""
    if normalized == "free":
        return "免费"
    if normalized == "not_free":
        return "需购票"
    return text


def _resolve_meal_hint(
    meal_hint,
    *,
    stage: str,
    point: str,
    fallback_label: str,
    fallback_notes: str,
) -> tuple[str, str]:
    if meal_hint:
        return meal_hint.label, meal_hint.notes
    if not point:
        return fallback_label, fallback_notes

    generic_labels = {
        "arrival": f"{point} 周边晚饭",
        "lunch": f"{point} 周边午饭",
        "dinner": f"{point} 周边晚饭",
        "departure": "返程前简餐",
    }
    generic_notes = {
        "arrival": f"到达后直接在 {point} 周边解决第一顿，减少跨区折返。",
        "lunch": f"午饭放在 {point} 附近，吃完继续当天主线最顺。",
        "dinner": f"晚饭放在 {point} 一带，吃完直接收尾回酒店。",
        "departure": f"最后一顿尽量贴着 {point} 或返程线路解决，留出回程余量。",
    }
    return generic_labels.get(stage, fallback_label), generic_notes.get(stage, fallback_notes)


def _poi_duration(point: str, poi_meta: dict[str, POICandidate], is_primary: bool) -> int:
    poi = poi_meta.get(point)
    base = 180 if is_primary else 140
    if not poi:
        return base
    category = (poi.category or "").lower()
    full_text = " ".join([point, poi.category or "", poi.notes or "", poi.address or ""])
    if any(token in category for token in ["博物", "museum", "展馆"]):
        return 180 if is_primary else 135
    if any(token in category for token in ["步行街", "商街", "夜市", "商圈"]):
        return 140 if is_primary else 110
    if is_generic_urban_park(full_text):
        return 120 if is_primary else 90
    if any(token in category for token in ["自然", "公园", "山", "湖", "海滨"]):
        return 210 if is_primary else 160
    if "景区" in category:
        return 200 if is_primary else 150
    return base
