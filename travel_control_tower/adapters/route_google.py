from __future__ import annotations

import os
from datetime import datetime

import requests

from ..runtime_config import load_runtime_config
from .base import RouteEstimate


PLACE_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
DEFAULT_REQUEST_TIMEOUT = 6


class GoogleRouteAdapter:
    provider_name = "google_maps"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None) -> None:
        runtime = load_runtime_config()
        self.api_key = (api_key or runtime.google_maps_api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")).strip()
        self.session = session or requests.Session()
        self._place_cache: dict[str, dict] = {}
        self._route_cache: dict[tuple[str, str, str, str], dict] = {}

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def estimate_transfer(
        self,
        origin_label: str,
        destination_label: str,
        mode: str,
        departure_time: datetime | None = None,
    ) -> RouteEstimate:
        if not self.api_key:
            raise RuntimeError("缺少 GOOGLE_MAPS_API_KEY，无法查询真实路线。")

        origin = self._resolve_place(origin_label)
        destination = self._resolve_place(destination_label)
        route = self._compute_route(origin, destination, mode, departure_time)
        return RouteEstimate(
            origin_label=origin_label,
            destination_label=destination_label,
            mode=mode,
            duration_minutes=route["duration_minutes"],
            distance_km=route["distance_km"],
            provider=self.provider_name,
            notes=route["notes"],
        )

    def _resolve_place(self, query: str) -> dict:
        if query in self._place_cache:
            return self._place_cache[query]

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        }
        response = self.session.post(
            PLACE_URL,
            headers=headers,
            json={"textQuery": query, "languageCode": "en"},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        places = data.get("places") or []
        if not places:
            raise RuntimeError(f"Google 无法匹配地点：{query}")

        place = places[0]
        resolved = {
            "display_name": place.get("displayName", {}).get("text", query),
            "formatted_address": place.get("formattedAddress", ""),
            "latitude": place["location"]["latitude"],
            "longitude": place["location"]["longitude"],
        }
        self._place_cache[query] = resolved
        return resolved

    def _compute_route(self, origin: dict, destination: dict, mode: str, departure_time: datetime | None) -> dict:
        departure_key = departure_time.isoformat() if departure_time is not None else ""
        cache_key = (
            f"{origin['latitude']},{origin['longitude']}",
            f"{destination['latitude']},{destination['longitude']}",
            mode,
            departure_key,
        )
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
        }
        payload: dict = {
            "origin": {"location": {"latLng": {"latitude": origin["latitude"], "longitude": origin["longitude"]}}},
            "destination": {
                "location": {"latLng": {"latitude": destination["latitude"], "longitude": destination["longitude"]}}
            },
            "travelMode": mode,
            "computeAlternativeRoutes": False,
            "languageCode": "en",
            "units": "METRIC",
        }
        if mode == "DRIVE":
            payload["routingPreference"] = "TRAFFIC_AWARE"
        if mode == "TRANSIT" and departure_time is not None:
            payload["departureTime"] = departure_time.isoformat()

        response = self.session.post(ROUTES_URL, headers=headers, json=payload, timeout=DEFAULT_REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        routes = data.get("routes") or []
        if not routes:
            raise RuntimeError(f"Google 没有返回可用路线：{origin['display_name']} -> {destination['display_name']}")

        route = routes[0]
        duration_text = str(route.get("duration", "0s"))
        seconds = int(float(duration_text[:-1])) if duration_text.endswith("s") else 0
        distance_km = round(route.get("distanceMeters", 0) / 1000.0, 1)
        duration_minutes = max(1, round(seconds / 60))
        mode_label = {"WALK": "步行", "TRANSIT": "公共交通", "DRIVE": "驾车"}.get(mode, mode)
        result = {
            "duration_minutes": duration_minutes,
            "distance_km": distance_km,
            "notes": f"Google {mode_label}路线约 {duration_minutes} 分钟，约 {distance_km} 公里。",
        }
        self._route_cache[cache_key] = result
        return result
