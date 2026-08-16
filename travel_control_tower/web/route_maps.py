from __future__ import annotations

import math
from typing import Iterable

from ..adapters.route_amap import AmapRouteAdapter


EXCLUDED_CATEGORIES = {"交通", "缓冲", "机动", "返程", "到达"}
EXCLUDED_LABEL_PARTS = ("到达交通", "返程交通", "机动缓冲", "自由活动", "办理入住", "退房")


def _destination(plan: dict) -> str:
    snapshot = plan.get("input_snapshot") or {}
    return str(snapshot.get("目的地") or snapshot.get("destination") or "").strip()


def _unique(items: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = str(item or "").strip()
        key = clean.casefold()
        if clean and key not in seen:
            output.append(clean)
            seen.add(key)
    return output


def extract_day_stops(plan: dict, day: dict, *, max_stops: int = 9) -> list[str]:
    hotel = str((plan.get("selected_hotel") or {}).get("name") or "").strip()
    labels: list[str] = [hotel] if hotel else []
    for item in day.get("items") or []:
        category = str(item.get("category") or "").strip()
        label = str(item.get("label") or "").strip()
        if not label or category in EXCLUDED_CATEGORIES:
            continue
        if any(part in label for part in EXCLUDED_LABEL_PARTS):
            continue
        labels.append(label)
    return _unique(labels)[:max_stops]


def _haversine_km(a: dict, b: dict) -> float:
    lon1, lat1 = math.radians(float(a["lng"])), math.radians(float(a["lat"]))
    lon2, lat2 = math.radians(float(b["lng"])), math.radians(float(b["lat"]))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(value))


def _greedy_order(stops: list[dict]) -> list[dict]:
    if len(stops) < 3:
        return list(stops)
    remaining = list(stops[1:])
    ordered = [stops[0]]
    while remaining:
        current = ordered[-1]
        nearest = min(remaining, key=lambda item: _haversine_km(current, item))
        ordered.append(nearest)
        remaining.remove(nearest)
    return ordered


def _path_distance(stops: list[dict]) -> float:
    return sum(_haversine_km(stops[index], stops[index + 1]) for index in range(len(stops) - 1))


def _route_status(ratio: float) -> tuple[str, str]:
    if ratio <= 1.15:
        return "路线较顺", "地点顺序接近较短路线，可以按当前顺序游玩。"
    if ratio <= 1.4:
        return "有少量折返", "当前顺序可以执行，但调整一两个地点会更省路。"
    return "建议调整顺序", "当前安排有明显折返，建议参考下方推荐顺序。"


def enrich_plan_route_maps(plan: dict, adapter: AmapRouteAdapter | None = None) -> dict:
    """Add route coordinates and distance diagnostics to each day in-place."""
    adapter = adapter or AmapRouteAdapter()
    destination = _destination(plan)
    if not adapter.is_available:
        for day in plan.get("daily_plan") or []:
            day["route_map"] = {"available": False, "message": "配置高德 Web 服务 Key 后可显示每日地图。"}
        return plan

    for day in plan.get("daily_plan") or []:
        stop_labels = extract_day_stops(plan, day)
        points: list[dict] = []
        failures: list[str] = []
        for label in stop_labels:
            query = f"{destination} {label}".strip()
            try:
                geocode = adapter.geocode(query)
                lng, lat = str(geocode.get("location") or "").split(",", 1)
                points.append({"label": label, "lng": round(float(lng), 6), "lat": round(float(lat), 6)})
            except Exception:
                failures.append(label)

        if len(points) < 2:
            day["route_map"] = {
                "available": False,
                "message": "当天可识别地点不足两个，暂时无法绘制路线。",
                "unmatched": failures,
            }
            continue

        segments: list[dict] = []
        actual_total = 0.0
        for origin, target in zip(points, points[1:]):
            direct_km = _haversine_km(origin, target)
            mode = "WALK" if direct_km <= 2.5 else "DRIVE"
            distance_km = direct_km
            minutes = max(1, round(direct_km / (4.5 if mode == "WALK" else 24) * 60))
            provider = "直线估算"
            try:
                estimate = adapter.estimate_transfer(
                    f"{destination} {origin['label']}".strip(),
                    f"{destination} {target['label']}".strip(),
                    mode,
                )
                distance_km = float(estimate.distance_km)
                minutes = int(estimate.duration_minutes)
                provider = "高德路径"
            except Exception:
                pass
            actual_total += distance_km
            segments.append({
                "origin": origin["label"],
                "destination": target["label"],
                "distance_km": round(distance_km, 1),
                "minutes": minutes,
                "mode": "步行" if mode == "WALK" else "打车/驾车",
                "provider": provider,
            })

        optimized = _greedy_order(points)
        current_direct = _path_distance(points)
        optimized_direct = max(0.01, _path_distance(optimized))
        ratio = current_direct / optimized_direct
        status, advice = _route_status(ratio)
        return_km = _haversine_km(points[-1], points[0])
        day["route_map"] = {
            "available": True,
            "points": points,
            "segments": segments,
            "total_distance_km": round(actual_total, 1),
            "return_to_start_km": round(return_km, 1),
            "returns_near_start": return_km <= 1.5,
            "status": status,
            "advice": advice,
            "efficiency_ratio": round(ratio, 2),
            "recommended_order": [item["label"] for item in optimized],
            "unmatched": failures,
        }
    return plan


def static_map_params(route_map: dict, *, size: str = "760*460") -> dict[str, str]:
    points = list(route_map.get("points") or [])[:10]
    locations = [f"{float(item['lng']):.6f},{float(item['lat']):.6f}" for item in points]
    markers = "|".join(
        f"mid,0x{color},{index}:{location}"
        for index, (location, color) in enumerate(zip(locations, ["2E7DFF", "FF7A45", "33A474", "A855F7", "EAB308", "EF4444", "06B6D4", "6366F1", "F97316", "14B8A6"]), start=1)
    )
    return {
        "size": size,
        "scale": "1",
        "markers": markers,
        "paths": f"7,0x2E7DFF,0.82,,:{';'.join(locations)}",
    }
