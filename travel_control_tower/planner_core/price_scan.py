from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ..adapters.base import TransportCandidate
from .models import TripRequest


def resolve_price_scan_request(
    request: TripRequest,
    search_adapter=None,
    max_samples: int = 6,
) -> tuple[TripRequest, list[TransportCandidate], dict | None, str]:
    if request.request_mode != "price_scan":
        return request, [], None, ""

    trip_days = request.target_trip_days or request.days or 3
    trip_nights = request.target_trip_nights
    if trip_nights is None:
        trip_nights = max(trip_days - 1, 0)

    if not request.enable_live_search:
        return request, [], None, "未开启实时搜索，当前只生成固定日期的样例方案。"

    if not search_adapter or not getattr(search_adapter, "is_available", False):
        return request, [], None, "实时搜索适配器不可用，当前无法执行时间窗口比价。"

    if not request.flexible_window_start or not request.flexible_window_end:
        return request, [], None, "缺少比价窗口，当前无法执行时间窗口比价。"

    try:
        candidates = search_adapter.scan_transport_windows(
            departure_city=request.departure_city,
            destination=request.destination,
            window_start=request.flexible_window_start,
            window_end=request.flexible_window_end,
            trip_days=trip_days,
            max_samples=max_samples,
        )
    except Exception as exc:
        return request, [], None, str(exc).strip() or "时间窗口比价失败。"

    if not candidates:
        return request, [], None, "时间窗口内没有拿到可用交通候选，当前仍使用默认日期样例。"

    best = candidates[0]
    chosen_start = best.trip_start_date or _date_part(best.depart_at) or request.start_date
    chosen_end = best.trip_end_date or request.end_date

    adjusted = replace(
        request,
        start_date=chosen_start,
        end_date=chosen_end,
        days=trip_days,
        nights=trip_nights,
        user_arrival_at_destination=best.outbound_arrive_at or request.user_arrival_at_destination,
        user_return_depart_at=best.return_depart_at or request.user_return_depart_at,
    )

    summary = {
        "window_start": request.flexible_window_start,
        "window_end": request.flexible_window_end,
        "trip_days": trip_days,
        "trip_nights": trip_nights,
        "sample_count": len(candidates),
        "chosen_start_date": chosen_start,
        "chosen_end_date": chosen_end,
        "chosen_price": best.total_price,
        "chosen_label": best.label,
    }
    return adjusted, candidates, summary, ""


def _date_part(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value[:10] if len(value) >= 10 else ""
