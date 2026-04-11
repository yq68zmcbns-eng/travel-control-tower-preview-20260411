from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

from ..adapters.base import HotelCandidate, POICandidate, TransportCandidate


@dataclass
class TripRequest:
    departure_city: str
    destination: str
    start_date: str
    end_date: str
    traveler_count: int
    enable_live_search: bool = False
    scenario_id: str = ""
    request_mode: str = "itinerary"
    flexible_window_start: str = ""
    flexible_window_end: str = ""
    target_trip_days: Optional[int] = None
    target_trip_nights: Optional[int] = None
    price_priority: str = "balanced"
    days: Optional[int] = None
    nights: Optional[int] = None
    budget_total: Optional[float] = None
    budget_per_person: Optional[float] = None
    travel_style: str = "balanced"
    must_go: List[str] = field(default_factory=list)
    hotel_preferences: List[str] = field(default_factory=list)
    transport_preferences: List[str] = field(default_factory=list)
    user_hotel_name: str = ""
    user_hotel_area: str = ""
    user_hotel_nightly_price: Optional[float] = None
    user_hotel_url: str = ""
    user_transport_label: str = ""
    user_transport_category: str = ""
    user_transport_total_price: Optional[float] = None
    user_transport_depart_at: str = ""
    user_transport_arrive_at: str = ""
    user_arrival_at_destination: str = ""
    user_return_depart_at: str = ""
    user_transport_url: str = ""
    needs_exports: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DayItem:
    label: str
    category: str
    start_time: str = ""
    end_time: str = ""
    duration_minutes: int = 0
    notes: str = ""
    is_buffer: bool = False
    route_origin: str = ""
    route_destination: str = ""
    route_mode: str = ""
    route_mode_label: str = ""
    route_provider: str = ""
    route_distance_km: float = 0.0
    route_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DailyPlan:
    day_index: int
    date: str
    theme: str
    items: List[DayItem] = field(default_factory=list)
    why_this_day: str = ""
    transport_strategy: str = ""
    meal_strategy: str = ""
    fallback_if_fast: str = ""
    fallback_if_tired: str = ""
    estimated_cost_total: float = 0.0
    estimated_cost_notes: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["items"] = [item.to_dict() for item in self.items]
        return data


@dataclass
class BudgetLineItem:
    category: str
    total: float
    per_person: float
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BudgetSummary:
    fixed_cost_total: float = 0.0
    per_person_cost: float = 0.0
    optional_upgrade_total: float = 0.0
    breakdown: List[BudgetLineItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["breakdown"] = [item.to_dict() for item in self.breakdown]
        return data


@dataclass
class BookingItem:
    name: str
    category: str
    url: str = ""
    priority: str = "recommended"
    timing: str = ""
    notes: str = ""
    why_now: str = ""
    risk_if_wait: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProviderStatus:
    name: str
    status: str
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlanningTrace:
    engine: str
    mode: str
    model: str = ""
    used_fallback: bool = False
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TripPlan:
    status: str
    overview_title: str
    overview_summary: str
    input_snapshot: dict[str, object]
    assumptions: List[str]
    daily_plan: List[DailyPlan]
    budget: BudgetSummary
    booking_items: List[BookingItem]
    planning_trace: PlanningTrace | None = None
    provider_statuses: List[ProviderStatus] = field(default_factory=list)
    selected_hotel: HotelCandidate | None = None
    selected_transport: TransportCandidate | None = None
    hotel_candidates: List[HotelCandidate] = field(default_factory=list)
    transport_candidates: List[TransportCandidate] = field(default_factory=list)
    poi_candidates: List[POICandidate] = field(default_factory=list)
    price_scan_summary: dict | None = None
    price_scan_candidates: List[TransportCandidate] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "overview": {
                "title": self.overview_title,
                "summary": self.overview_summary,
            },
            "input_snapshot": self.input_snapshot,
            "assumptions": self.assumptions,
            "daily_plan": [day.to_dict() for day in self.daily_plan],
            "budget": self.budget.to_dict(),
            "booking_items": [item.to_dict() for item in self.booking_items],
            "planning_trace": self.planning_trace.to_dict() if self.planning_trace else None,
            "provider_statuses": [item.to_dict() for item in self.provider_statuses],
            "selected_hotel": self.selected_hotel.to_dict() if self.selected_hotel else None,
            "selected_transport": self.selected_transport.to_dict() if self.selected_transport else None,
            "hotel_candidates": [item.to_dict() for item in self.hotel_candidates],
            "transport_candidates": [item.to_dict() for item in self.transport_candidates],
            "poi_candidates": [item.to_dict() for item in self.poi_candidates],
            "price_scan_summary": self.price_scan_summary,
            "price_scan_candidates": [item.to_dict() for item in self.price_scan_candidates],
            "open_questions": self.open_questions,
        }
