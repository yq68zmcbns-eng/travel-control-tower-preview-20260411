from __future__ import annotations

from datetime import date

from .models import TripRequest


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def normalize_request(request: TripRequest) -> TripRequest:
    start = _parse_date(request.start_date)
    end = _parse_date(request.end_date)

    if end < start:
        raise ValueError("end_date must be on or after start_date")

    if request.days is None:
        request.days = (end - start).days + 1

    if request.nights is None:
        request.nights = max(request.days - 1, 0)

    if request.target_trip_days is None:
        request.target_trip_days = request.days

    if request.target_trip_nights is None and request.target_trip_days is not None:
        request.target_trip_nights = max(request.target_trip_days - 1, 0)

    if request.budget_per_person is None and request.budget_total is not None:
        request.budget_per_person = request.budget_total / request.traveler_count

    if request.budget_total is None and request.budget_per_person is not None:
        request.budget_total = request.budget_per_person * request.traveler_count

    if not request.flexible_window_start and request.request_mode == "price_scan":
        request.flexible_window_start = request.start_date

    if not request.flexible_window_end and request.request_mode == "price_scan":
        request.flexible_window_end = request.end_date

    return request
