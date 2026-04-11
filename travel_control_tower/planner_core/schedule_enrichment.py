from __future__ import annotations

from datetime import datetime, timedelta

from ..adapters.base import TransportCandidate
from .models import DailyPlan, TripRequest


DEFAULT_DAY_STARTS = {
    1: "11:00",
    2: "08:30",
    3: "08:30",
}


def _parse_clock(candidate: str, expected_date: str) -> str | None:
    raw = (candidate or "").strip()
    if not raw:
        return None

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            value = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if value.date().isoformat() == expected_date:
            return value.strftime("%H:%M")
        return None

    if len(raw) == 5 and raw[2] == ":":
        return raw
    return None


def build_schedule_overrides(
    request: TripRequest,
    daily_plan: list[DailyPlan],
    selected_transport: TransportCandidate | None = None,
) -> tuple[dict[int, str], dict[int, str]]:
    day_start_times: dict[int, str] = {}
    day_end_times: dict[int, str] = {}

    if not daily_plan:
        return day_start_times, day_end_times

    first_day = daily_plan[0]
    last_day = daily_plan[-1]

    arrival_override = _parse_clock(request.user_arrival_at_destination, first_day.date)
    if not arrival_override and selected_transport:
        depart_same_day = _parse_clock(selected_transport.depart_at, first_day.date)
        arrive_same_day = _parse_clock(selected_transport.arrive_at, first_day.date)
        if arrive_same_day and depart_same_day:
            arrival_override = arrive_same_day
    if arrival_override:
        day_start_times[first_day.day_index] = arrival_override

    return_depart_override = _parse_clock(request.user_return_depart_at, last_day.date)
    if not return_depart_override and selected_transport:
        depart_same_day = _parse_clock(selected_transport.depart_at, last_day.date)
        arrive_same_day = _parse_clock(selected_transport.arrive_at, last_day.date)
        if depart_same_day and arrive_same_day:
            return_depart_override = depart_same_day
    if return_depart_override:
        day_end_times[last_day.day_index] = return_depart_override

    return day_start_times, day_end_times


def _total_minutes(day: DailyPlan) -> int:
    return sum(max(int(item.duration_minutes or 0), 0) for item in day.items)


def apply_schedule_blocks(
    daily_plan: list[DailyPlan],
    day_start_times: dict[int, str] | None = None,
    day_end_times: dict[int, str] | None = None,
) -> list[DailyPlan]:
    explicit_day_start_times = day_start_times or {}
    day_end_times = day_end_times or {}

    for day in daily_plan:
        if day.day_index in explicit_day_start_times:
            start_clock = explicit_day_start_times[day.day_index]
            cursor = datetime.fromisoformat(f"{day.date}T{start_clock}:00")
        elif day.day_index in day_end_times:
            end_clock = day_end_times[day.day_index]
            end_cursor = datetime.fromisoformat(f"{day.date}T{end_clock}:00")
            cursor = end_cursor - timedelta(minutes=_total_minutes(day))
        elif day.day_index in DEFAULT_DAY_STARTS:
            start_clock = DEFAULT_DAY_STARTS[day.day_index]
            cursor = datetime.fromisoformat(f"{day.date}T{start_clock}:00")
        else:
            cursor = datetime.fromisoformat(f"{day.date}T09:00:00")

        for item in day.items:
            duration = max(int(item.duration_minutes or 0), 0)
            item.start_time = cursor.strftime("%H:%M")
            if duration > 0:
                cursor = cursor + timedelta(minutes=duration)
            item.end_time = cursor.strftime("%H:%M")

    return daily_plan
