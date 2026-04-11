from __future__ import annotations

from dataclasses import replace

from ..adapters.base import HotelCandidate, POICandidate, RouteEstimate
from .city_profiles import hotel_area_hint_for
from .models import TripRequest
from .poi_scoring import arrival_friendliness_score, poi_quality_score


ARRIVAL_FRIENDLY_TOKENS = (
    "步行街",
    "商圈",
    "老街",
    "古街",
    "夜市",
    "秦淮",
    "夫子庙",
    "新街口",
    "解放碑",
    "春熙路",
    "前门",
    "浅草",
    "银座",
    "心斋桥",
)

POI_POSITIVE_TOKENS = (
    "景区",
    "景点",
    "地标",
    "古城",
    "城墙",
    "寺",
    "塔",
    "宫",
    "陵",
    "园",
    "公园",
    "博物馆",
    "老街",
    "步行街",
    "商圈",
    "historic",
    "museum",
)

POI_NEGATIVE_TOKENS = (
    "野生动物世界",
    "欢乐谷",
    "度假区",
    "滑雪场",
    "游乐园",
    "乐园",
    "温泉",
    "影视城",
    "海洋王国",
    "千岛湖",
    "海洋公园",
    "海洋馆",
    "长乔",
    "宋城",
    "动物园",
)

HOTEL_POSITIVE_TOKENS = (
    "酒店",
    "宾馆",
    "hotel",
    "全季",
    "亚朵",
    "欢朋",
    "汉庭",
    "如家",
    "锦江",
    "希尔顿",
    "智选",
    "假日",
    "美居",
    "桔子",
    "宜必思",
)

HOTEL_NEGATIVE_TOKENS = (
    "青年旅社",
    "青旅",
    "旅社",
    "民宿",
    "客栈",
    "公寓",
    "电竞",
    "太空舱",
    "capsule",
    "hostel",
    "guesthouse",
    "客房",
    "公寓式",
)

SUSPICIOUS_CITY_ROUTE_MINUTES = 180
SUSPICIOUS_CITY_ROUTE_DISTANCE_KM = 120
HOTEL_ROUTE_RERANK_LIMIT = 4
POI_ROUTE_RERANK_LIMIT = 6
MAX_HOTELS_FOR_ROUTE_RANKING = 3
MAX_POIS_FOR_ROUTE_RANKING = 4


def refine_candidates_with_routes(
    request: TripRequest,
    hotel_candidates: list[HotelCandidate],
    poi_candidates: list[POICandidate],
    route_adapter=None,
) -> tuple[list[HotelCandidate], list[POICandidate]]:
    if not hotel_candidates and not poi_candidates:
        return hotel_candidates, poi_candidates

    ranking_route_adapter = route_adapter
    if len(hotel_candidates) > MAX_HOTELS_FOR_ROUTE_RANKING or len(poi_candidates) > MAX_POIS_FOR_ROUTE_RANKING:
        ranking_route_adapter = None

    hotel_anchor = _resolve_hotel_anchor(request, poi_candidates)
    ranked_hotels = _rerank_hotels(request, hotel_candidates, hotel_anchor, route_adapter=ranking_route_adapter)
    selected_hotel = ranked_hotels[0] if ranked_hotels else None
    ranked_pois = _rerank_pois(request, poi_candidates, selected_hotel, route_adapter=ranking_route_adapter)
    return ranked_hotels, ranked_pois


def _normalize(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    haystack = _normalize(value)
    return any(_normalize(token) in haystack for token in tokens if token)


def _hotel_blob(candidate: HotelCandidate) -> str:
    return " ".join([candidate.name or "", candidate.area or "", candidate.notes or ""]).strip()


def _poi_blob(candidate: POICandidate) -> str:
    return " ".join(
        [
            candidate.name or "",
            candidate.category or "",
            candidate.poi_level or "",
            candidate.address or "",
            candidate.notes or "",
        ]
    ).strip()


def _resolve_hotel_anchor(request: TripRequest, poi_candidates: list[POICandidate]) -> str:
    if request.must_go:
        return request.must_go[0]
    arrival_ranked = sorted(
        poi_candidates,
        key=lambda item: -arrival_friendliness_score(
            name=item.name,
            category=item.category,
            level=item.poi_level,
            notes=item.notes,
            address=item.address,
        ),
    )
    if arrival_ranked:
        first = arrival_ranked[0]
        if arrival_friendliness_score(
            name=first.name,
            category=first.category,
            level=first.poi_level,
            notes=first.notes,
            address=first.address,
        ) > 0:
            return first.name
    return f"{request.destination}市中心"


def _route_minutes(route_adapter, origin: str, destination: str) -> RouteEstimate | None:
    if not route_adapter or not origin or not destination:
        return None
    try:
        estimate = route_adapter.estimate_transfer(origin, destination, "DRIVE")
    except Exception:
        return None
    if estimate.duration_minutes >= SUSPICIOUS_CITY_ROUTE_MINUTES or estimate.distance_km >= SUSPICIOUS_CITY_ROUTE_DISTANCE_KM:
        return None
    return estimate


def _destination_tokens(destination: str) -> list[str]:
    destination = (destination or "").strip()
    tokens = [destination]
    if destination.endswith("市"):
        tokens.append(destination[:-1])
    return [token for token in tokens if token]


def _localize_place(request: TripRequest, place: str) -> str:
    place = (place or "").strip()
    if not place:
        return ""
    if request.destination in place or request.departure_city in place:
        return place
    if any(token in place for token in ["机场", "火车站", "高铁站", "地铁站"]):
        return place
    return f"{request.destination}{place}"


def _hotel_route_origin(request: TripRequest, candidate: HotelCandidate) -> str:
    name = (candidate.name or "").strip()
    area = (candidate.area or "").strip()
    destination_tokens = _destination_tokens(request.destination)

    if any(token in name for token in destination_tokens):
        return name
    if any(token in area for token in destination_tokens) and "近" not in area:
        return _localize_place(request, area)
    if name:
        return _localize_place(request, name)
    return _localize_place(request, area)


def _rerank_hotels(
    request: TripRequest,
    hotel_candidates: list[HotelCandidate],
    anchor: str,
    *,
    route_adapter=None,
) -> list[HotelCandidate]:
    if len(hotel_candidates) <= 1:
        return hotel_candidates

    provisional: list[tuple[int, int, HotelCandidate]] = []
    for index, candidate in enumerate(hotel_candidates):
        blob = _hotel_blob(candidate)
        score = 0
        if _contains_any(blob, HOTEL_POSITIVE_TOKENS):
            score += 35
        if _contains_any(blob, HOTEL_NEGATIVE_TOKENS):
            score -= 120
        if candidate.provider == "user_input":
            score += 200
        if candidate.provider == "rule_fallback":
            score -= 30
        if 120 <= float(candidate.nightly_price or 0) <= 1200:
            score += 8
        provisional.append((score - index, index, candidate))

    route_ranked_indexes = {
        index
        for _, index, _ in sorted(
            provisional,
            key=lambda item: (-item[0], item[2].nightly_price if item[2].nightly_price > 0 else 10_000, item[2].name),
        )[:HOTEL_ROUTE_RERANK_LIMIT]
    }

    scored: list[tuple[int, HotelCandidate]] = []
    for base_score, index, candidate in provisional:
        notes = candidate.notes or ""
        score = base_score
        if index in route_ranked_indexes:
            route = _route_minutes(
                route_adapter,
                _hotel_route_origin(request, candidate),
                _localize_place(request, anchor),
            )
            if route:
                if route.duration_minutes <= 20:
                    score += 40
                elif route.duration_minutes <= 35:
                    score += 22
                elif route.duration_minutes <= 50:
                    score += 8
                elif route.duration_minutes <= 75:
                    score -= 10
                else:
                    score -= 35
                route_note = f" 距主片区约{route.duration_minutes}分钟车程。"
                if route_note.strip() not in notes:
                    notes = f"{notes}{route_note}".strip()
        scored.append((score, replace(candidate, notes=notes)))

    scored.sort(key=lambda item: (-item[0], item[1].nightly_price if item[1].nightly_price > 0 else 10_000, item[1].name))
    return [candidate for _, candidate in scored]


def _rerank_pois(
    request: TripRequest,
    poi_candidates: list[POICandidate],
    selected_hotel: HotelCandidate | None,
    *,
    route_adapter=None,
) -> list[POICandidate]:
    if len(poi_candidates) <= 1:
        return poi_candidates
    if not route_adapter and not request.must_go:
        return poi_candidates

    origin = ""
    if selected_hotel:
        origin = _localize_place(request, selected_hotel.area or selected_hotel.name)
    if not origin:
        origin = _localize_place(request, hotel_area_hint_for(request.destination) or f"{request.destination}市中心")

    provisional: list[tuple[int, int, POICandidate]] = []
    for index, poi in enumerate(poi_candidates):
        blob = _poi_blob(poi)
        score = _must_go_priority(poi.name, request)
        score += poi_quality_score(
            name=poi.name,
            category=poi.category,
            level=poi.poi_level,
            notes=poi.notes,
            address=poi.address,
        )
        if _contains_any(blob, POI_POSITIVE_TOKENS):
            score += 8
        if _contains_any(blob, POI_NEGATIVE_TOKENS):
            score -= 40
        provisional.append((score - index, index, poi))

    route_ranked_indexes = {
        index
        for _, index, _ in sorted(provisional, key=lambda item: (-item[0], item[2].name))[:POI_ROUTE_RERANK_LIMIT]
    }

    scored: list[tuple[int, POICandidate]] = []
    for base_score, index, poi in provisional:
        notes = poi.notes or ""
        score = base_score
        if index in route_ranked_indexes:
            route = _route_minutes(
                route_adapter,
                origin,
                f"{request.destination}{poi.name}" if request.destination not in poi.name else poi.name,
            )
            if route:
                if route.duration_minutes <= 20:
                    score += 40
                elif route.duration_minutes <= 35:
                    score += 25
                elif route.duration_minutes <= 50:
                    score += 12
                elif route.duration_minutes <= 75:
                    score -= 10
                elif route.duration_minutes <= 100:
                    score -= 40
                else:
                    score -= 100
                route_note = f" 距住宿锚点约{route.duration_minutes}分钟车程。"
                if route_note.strip() not in notes:
                    notes = f"{notes}{route_note}".strip()
        scored.append((score, replace(poi, notes=notes)))

    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [poi for _, poi in scored]


def _must_go_priority(point: str, request: TripRequest) -> int:
    normalized_point = _normalize(point)
    for index, keyword in enumerate(request.must_go):
        normalized_keyword = _normalize(keyword)
        if normalized_keyword and (normalized_keyword in normalized_point or normalized_point in normalized_keyword):
            return 120 - index * 10
    return 0
