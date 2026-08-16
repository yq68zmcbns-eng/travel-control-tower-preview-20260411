from __future__ import annotations

import os

import requests

from ..runtime_config import load_runtime_config
from .base import POICandidate, RouteEstimate


BASE_URL = "https://restapi.amap.com"
DEFAULT_REQUEST_TIMEOUT = 6


class AmapRouteAdapter:
    provider_name = "amap"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None) -> None:
        runtime = load_runtime_config()
        self.api_key = (api_key or runtime.amap_web_key or os.environ.get("AMAP_WEB_KEY", "")).strip()
        self.session = session or requests.Session()
        self._geo_cache: dict[str, dict] = {}
        self._route_cache: dict[tuple[str, str], dict] = {}
        self._walking_cache: dict[tuple[str, str], dict] = {}
        self._transit_cache: dict[tuple[str, str, str, str], dict] = {}

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def geocode(self, query: str) -> dict:
        """Return the first AMap geocode match without exposing the API key."""
        if not self.api_key:
            raise RuntimeError("缺少 AMAP_WEB_KEY，无法匹配地点坐标。")
        return dict(self._geocode(str(query or "").strip()))

    def estimate_transfer(self, origin_label: str, destination_label: str, mode: str, departure_time=None) -> RouteEstimate:
        if not self.api_key:
            raise RuntimeError("缺少 AMAP_WEB_KEY，无法查询真实路线。")

        origin = self._geocode(origin_label)
        destination = self._geocode(destination_label)
        if mode == "WALK":
            route = self._walking(origin["location"], destination["location"])
            minutes = max(1, round(int(route["duration"]) / 60))
            distance_km = round(int(route["distance"]) / 1000.0, 1)
            notes = f"高德步行路线约 {minutes} 分钟，约 {distance_km} 公里。"
        elif mode == "TRANSIT":
            route = self._transit(origin, destination)
            minutes = max(1, round(int(route["duration"]) / 60))
            distance_km = round(int(route["distance"]) / 1000.0, 1)
            cost = str(route.get("cost", "") or "").strip()
            walking_distance = str(route.get("walking_distance", "") or "").strip()
            cost_text = f"，约 {float(cost):.0f} 元" if cost else ""
            walk_text = f"，步行约 {round(int(walking_distance) / 1000.0, 1)} 公里" if walking_distance else ""
            notes = f"高德公交地铁路线约 {minutes} 分钟，约 {distance_km} 公里{cost_text}{walk_text}。"
        elif mode == "DRIVE":
            route = self._driving(origin["location"], destination["location"])
            minutes = max(1, round(int(route["duration"]) / 60))
            distance_km = round(int(route["distance"]) / 1000.0, 1)
            notes = f"高德驾车路线约 {minutes} 分钟，约 {distance_km} 公里。"
        else:
            raise RuntimeError(f"当前高德适配器不支持模式：{mode}")
        return RouteEstimate(
            origin_label=origin_label,
            destination_label=destination_label,
            mode=mode,
            duration_minutes=minutes,
            distance_km=distance_km,
            provider=self.provider_name,
            notes=notes,
        )

    def search_pois(self, city_name: str, keyword: str = "", max_items: int = 8) -> list[POICandidate]:
        if not self.api_key:
            raise RuntimeError("缺少 AMAP_WEB_KEY，无法搜索 POI。")

        query = keyword.strip() or "景区"
        queries = [f"{city_name}{query}"] if _contains_cjk(city_name) else [query]
        if query not in queries:
            queries.append(query)

        data: dict | None = None
        pois: list[dict] = []
        for effective_query in queries:
            response = self.session.get(
                f"{BASE_URL}/v3/place/text",
                params={
                    "key": self.api_key,
                    "keywords": effective_query,
                    "city": city_name,
                    "citylimit": "true",
                    "offset": max_items,
                    "page": 1,
                    "extensions": "base",
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            if str(data.get("status")) != "1":
                continue
            pois = data.get("pois") or []
            if pois:
                break

        if data is None or str(data.get("status")) != "1":
            raise RuntimeError(f"高德 POI 搜索失败：{city_name} / {query}")

        candidates: list[POICandidate] = []
        for item in pois[:max_items]:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            candidates.append(
                POICandidate(
                    name=name,
                    city_name=str(item.get("cityname", "")).strip() or city_name,
                    category=str(item.get("type", "")).strip(),
                    address=str(item.get("address", "")).strip(),
                    notes=_build_poi_notes(item),
                    provider=self.provider_name,
                )
            )
        return candidates

    def _geocode(self, query: str) -> dict:
        if query in self._geo_cache:
            return self._geo_cache[query]

        response = self.session.get(
            f"{BASE_URL}/v3/geocode/geo",
            params={"key": self.api_key, "address": query},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        geocodes = data.get("geocodes") or []
        if str(data.get("status")) != "1" or not geocodes:
            raise RuntimeError(f"高德无法匹配地点：{query}")

        result = geocodes[0]
        self._geo_cache[query] = result
        return result

    def _driving(self, origin_location: str, destination_location: str) -> dict:
        cache_key = (origin_location, destination_location)
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]

        response = self.session.get(
            f"{BASE_URL}/v3/direction/driving",
            params={
                "key": self.api_key,
                "origin": origin_location,
                "destination": destination_location,
                "strategy": 0,
                "extensions": "base",
            },
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        paths = ((data.get("route") or {}).get("paths") or [])
        if str(data.get("status")) != "1" or not paths:
            raise RuntimeError("高德没有返回可用路线。")
        self._route_cache[cache_key] = paths[0]
        return paths[0]

    def _walking(self, origin_location: str, destination_location: str) -> dict:
        cache_key = (origin_location, destination_location)
        if cache_key in self._walking_cache:
            return self._walking_cache[cache_key]

        response = self.session.get(
            f"{BASE_URL}/v3/direction/walking",
            params={
                "key": self.api_key,
                "origin": origin_location,
                "destination": destination_location,
            },
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        paths = ((data.get("route") or {}).get("paths") or [])
        if str(data.get("status")) != "1" or not paths:
            raise RuntimeError("高德没有返回可用步行路线。")
        self._walking_cache[cache_key] = paths[0]
        return paths[0]

    def _transit(self, origin: dict, destination: dict) -> dict:
        city = str(origin.get("city") or origin.get("province") or "").strip()
        cityd = str(destination.get("city") or destination.get("province") or city).strip()
        cache_key = (origin["location"], destination["location"], city, cityd)
        if cache_key in self._transit_cache:
            return self._transit_cache[cache_key]

        response = self.session.get(
            f"{BASE_URL}/v3/direction/transit/integrated",
            params={
                "key": self.api_key,
                "origin": origin["location"],
                "destination": destination["location"],
                "city": city,
                "cityd": cityd,
                "strategy": 0,
            },
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        transits = ((data.get("route") or {}).get("transits") or [])
        if str(data.get("status")) != "1" or not transits:
            raise RuntimeError("高德没有返回可用公交地铁路线。")
        self._transit_cache[cache_key] = transits[0]
        return transits[0]


def _build_poi_notes(item: dict) -> str:
    parts: list[str] = []
    if item.get("pname"):
        parts.append(f"省份：{item['pname']}")
    if item.get("cityname"):
        parts.append(f"城市：{item['cityname']}")
    if item.get("adname"):
        parts.append(f"区县：{item['adname']}")
    if item.get("type"):
        parts.append(f"类型：{item['type']}")
    if item.get("address"):
        parts.append(f"地址：{item['address']}")
    biz_ext = item.get("biz_ext") or {}
    if biz_ext.get("rating"):
        parts.append(f"评分：{biz_ext['rating']}")
    return "；".join(parts)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))
