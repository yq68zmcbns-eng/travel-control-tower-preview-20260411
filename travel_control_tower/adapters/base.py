from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class RouteEstimate:
    origin_label: str
    destination_label: str
    mode: str
    duration_minutes: int
    distance_km: float = 0.0
    provider: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HotelCandidate:
    name: str
    nightly_price: float
    currency: str = "CNY"
    area: str = ""
    notes: str = ""
    booking_url: str = ""
    provider: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TransportCandidate:
    label: str
    category: str
    total_price: float
    currency: str = "CNY"
    depart_at: str = ""
    arrive_at: str = ""
    outbound_arrive_at: str = ""
    return_depart_at: str = ""
    trip_start_date: str = ""
    trip_end_date: str = ""
    booking_url: str = ""
    provider: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class POICandidate:
    name: str
    city_name: str = ""
    category: str = ""
    poi_level: str = ""
    free_status: str = ""
    address: str = ""
    notes: str = ""
    booking_url: str = ""
    provider: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
