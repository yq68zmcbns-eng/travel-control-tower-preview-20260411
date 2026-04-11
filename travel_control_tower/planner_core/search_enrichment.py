from __future__ import annotations

from collections import Counter

from ..adapters.base import HotelCandidate, POICandidate, TransportCandidate
from .checklist import _hotel_search_url, _transport_search_url
from .city_profiles import hotel_area_hint_for, resolve_default_points
from .models import BookingItem, TripRequest
from .scenarios import get_scenario_search_fallbacks, get_scenario_search_specs

RAIL_TOKENS = {"铁路", "高铁", "火车", "rail", "train"}
AIR_TOKENS = {"飞机", "航班", "机票", "air", "flight", "plane"}
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
    "万豪",
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

KNOWN_CITY_TOKENS = (
    "上海",
    "南京",
    "北京",
    "东京",
    "大阪",
    "杭州",
    "苏州",
    "成都",
    "重庆",
    "西安",
    "长沙",
)


def _normalize(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def _contains_any(value: str, tokens: tuple[str, ...] | list[str] | set[str]) -> bool:
    haystack = _normalize(value)
    return any(_normalize(token) in haystack for token in tokens if token)


def _text_blob(*values: str) -> str:
    return " ".join(str(value or "").strip() for value in values if str(value or "").strip())


def _prefers_rail(request: TripRequest) -> bool:
    prefs = {_normalize(item) for item in request.transport_preferences}
    return any(_normalize(token) in prefs for token in RAIL_TOKENS)


def _prefers_air(request: TripRequest) -> bool:
    prefs = {_normalize(item) for item in request.transport_preferences}
    return any(_normalize(token) in prefs for token in AIR_TOKENS)


def _city_center_fallback(destination: str) -> str:
    destination = (destination or "").strip()
    return f"{destination}市中心" if destination else "市中心"


def _destination_tokens(destination: str) -> list[str]:
    destination = (destination or "").strip()
    tokens = [destination]
    if destination.endswith("市"):
        tokens.append(destination[:-1])
    if destination.endswith("区"):
        tokens.append(destination[:-1])
    return [token for token in tokens if token]


def _must_go_priority(point: str, request: TripRequest) -> int:
    normalized_point = _normalize(point)
    for index, keyword in enumerate(request.must_go):
        normalized_keyword = _normalize(keyword)
        if normalized_keyword and (
            normalized_keyword in normalized_point or normalized_point in normalized_keyword
        ):
            return 100 - index * 10
    return 0


def _destination_match_score(candidate: HotelCandidate, destination: str) -> int:
    haystack = _text_blob(candidate.name, candidate.area, candidate.notes)
    return sum(1 for token in _destination_tokens(destination) if _normalize(token) in _normalize(haystack))


def _has_conflicting_city_token(candidate: HotelCandidate, destination: str) -> bool:
    haystack = _normalize(_text_blob(candidate.name, candidate.area, candidate.notes))
    destination_tokens = {_normalize(token) for token in _destination_tokens(destination)}
    for token in KNOWN_CITY_TOKENS:
        normalized_token = _normalize(token)
        if not normalized_token or normalized_token in destination_tokens:
            continue
        if normalized_token in haystack:
            return True
    return False


def _filter_hotel_candidates_for_destination(
    request: TripRequest,
    candidates: list[HotelCandidate],
) -> list[HotelCandidate]:
    if not candidates:
        return []
    filtered = [
        candidate
        for candidate in candidates
        if not _has_conflicting_city_token(candidate, request.destination)
    ]
    if not filtered:
        return []
    matched = [candidate for candidate in filtered if _destination_match_score(candidate, request.destination) > 0]
    return matched or filtered


def _estimated_hotel_nightly_price(request: TripRequest, candidate: HotelCandidate) -> float:
    if candidate.nightly_price and candidate.nightly_price > 0:
        return float(candidate.nightly_price)

    if request.budget_total and request.nights:
        return round((float(request.budget_total) * 0.28) / max(int(request.nights), 1), 2)

    text = _text_blob(candidate.name, candidate.area, candidate.notes).lower()
    star_lookup = [
        (("豪华", "奢华", "五星", "5星", "star：5", "star:5"), 780.0),
        (("高档", "四星", "4星", "star：4", "star:4"), 520.0),
        (("舒适", "三星", "3星", "star：3", "star:3"), 360.0),
        (("经济", "二星", "2星", "star：2", "star:2"), 260.0),
    ]
    for tokens, price in star_lookup:
        if any(token in text for token in tokens):
            return price

    if request.destination and any(token in _normalize(request.destination) for token in ("东京", "大阪", "京都", "新加坡", "首尔")):
        return 620.0
    return 320.0


def _backfill_missing_hotel_prices(request: TripRequest, candidates: list[HotelCandidate]) -> list[HotelCandidate]:
    for candidate in candidates:
        if candidate.nightly_price and candidate.nightly_price > 0:
            continue
        estimate = _estimated_hotel_nightly_price(request, candidate)
        if estimate <= 0:
            continue
        candidate.nightly_price = estimate
        note = str(candidate.notes or "").strip()
        estimated_note = f"价格未直接返回，当前按同片区同档位酒店估算每晚约 {estimate:.0f} 元。"
        if estimated_note not in note:
            candidate.notes = f"{estimated_note} {note}".strip()
    return candidates


def _hotel_anchor_from_pois(request: TripRequest, poi_candidates: list[POICandidate] | None) -> str:
    if request.must_go:
        return request.must_go[0]

    for poi in poi_candidates or []:
        haystack = _text_blob(poi.name, poi.category, poi.notes)
        if _contains_any(haystack, ARRIVAL_FRIENDLY_TOKENS):
            return poi.name

    return _city_center_fallback(request.destination)


def _is_likely_real_hotel(candidate: HotelCandidate) -> bool:
    haystack = _text_blob(candidate.name, candidate.area, candidate.notes)
    if _contains_any(haystack, HOTEL_NEGATIVE_TOKENS):
        return False
    if _contains_any(haystack, HOTEL_POSITIVE_TOKENS):
        return True
    if "经济型" in haystack:
        return False
    return "酒店" in candidate.name or "宾馆" in candidate.name


def _has_direct_hotel_price(candidate: HotelCandidate) -> bool:
    return bool(candidate.nightly_price and candidate.nightly_price > 0)


def _matches_hotel_anchor(candidate: HotelCandidate, anchor: str) -> bool:
    if not anchor:
        return False
    haystack = _normalize(_text_blob(candidate.name, candidate.area, candidate.notes))
    return _normalize(anchor) in haystack


def _preferred_hotel_area_tokens(destination: str) -> list[str]:
    tokens = [token for token in resolve_default_points(destination) if token]
    hint = hotel_area_hint_for(destination)
    for token in ("观前街", "平江路", "山塘街", "心斋桥", "难波", "东京站", "王府井", "前门", "东单"):
        if token in hint and token not in tokens:
            tokens.append(token)
    return tokens


def _is_strong_direct_hotel(candidate: HotelCandidate) -> bool:
    if not (_has_direct_hotel_price(candidate) and _is_likely_real_hotel(candidate)):
        return False
    haystack = _text_blob(candidate.name, candidate.area, candidate.notes)
    if candidate.nightly_price >= 150:
        return True
    return any(token in haystack for token in ("舒适型", "高档型", "豪华型", "四星", "五星"))


def _score_hotel_candidate(candidate: HotelCandidate, request: TripRequest, anchor: str) -> int:
    score = 0
    haystack = _text_blob(candidate.name, candidate.area, candidate.notes)
    destination_tokens = _destination_tokens(request.destination)

    if _contains_any(haystack, destination_tokens):
        score += 40

    if _contains_any(haystack, _preferred_hotel_area_tokens(request.destination)):
        score += 22

    if anchor and anchor not in (hotel_area_hint_for(request.destination) or ""):
        if _normalize(anchor) in _normalize(haystack):
            score += 25

    if _contains_any(haystack, HOTEL_POSITIVE_TOKENS):
        score += 35

    if _contains_any(haystack, HOTEL_NEGATIVE_TOKENS):
        score -= 120

    if "经济型" in haystack:
        score -= 15

    if candidate.nightly_price:
        if candidate.nightly_price < 80:
            score -= 25
        elif candidate.nightly_price < 150:
            score -= 10
        elif candidate.nightly_price <= 1200:
            score += 8
    else:
        score -= 30

    if any(token in haystack for token in ("装修", "翻新", "星", "口碑")):
        score += 8

    if candidate.provider == "rule_fallback":
        score -= 40

    return score


def _dedupe_hotels(candidates: list[HotelCandidate]) -> list[HotelCandidate]:
    seen: set[str] = set()
    deduped: list[HotelCandidate] = []
    for candidate in candidates:
        key = _normalize(candidate.name)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _merge_hotel_candidates(
    request: TripRequest,
    anchor: str,
    *candidate_groups: list[HotelCandidate],
) -> list[HotelCandidate]:
    merged: list[HotelCandidate] = []
    for group in candidate_groups:
        merged.extend(group or [])
    deduped = _dedupe_hotels(merged)
    scored = sorted(
        deduped,
        key=lambda item: (
            -_score_hotel_candidate(item, request, anchor),
            0 if _is_likely_real_hotel(item) else 1,
            item.nightly_price if item.nightly_price > 0 else 10_000,
            item.name,
        ),
    )
    return scored


def _prefer_real_hotels(candidates: list[HotelCandidate]) -> list[HotelCandidate]:
    if not candidates:
        return []
    real_candidates = [candidate for candidate in candidates if _is_likely_real_hotel(candidate)]
    return real_candidates or candidates


def _search_hotels_with_strategy(
    search_adapter,
    *,
    destination: str,
    check_in: str,
    check_out: str,
    keyword: str = "",
    max_price: int = 1000,
    sort: str = "price_asc",
    hotel_types: str = "酒店",
    hotel_stars: str = "3,4,5",
) -> list[HotelCandidate]:
    try:
        return search_adapter.search_hotels(
            destination=destination,
            check_in=check_in,
            check_out=check_out,
            keyword=keyword,
            max_price=max_price,
            sort=sort,
            hotel_types=hotel_types,
            hotel_stars=hotel_stars,
        )
    except TypeError:
        try:
            return search_adapter.search_hotels(
                destination=destination,
                check_in=check_in,
                check_out=check_out,
                keyword=keyword,
                max_price=max_price,
                hotel_types=hotel_types,
                hotel_stars=hotel_stars,
            )
        except TypeError:
            return search_adapter.search_hotels(
                destination=destination,
                check_in=check_in,
                check_out=check_out,
                keyword=keyword,
                max_price=max_price,
            )


def _build_user_hotel_candidate(request: TripRequest) -> HotelCandidate | None:
    if not any(
        [
            request.user_hotel_name.strip(),
            request.user_hotel_url.strip(),
            request.user_hotel_nightly_price is not None,
        ]
    ):
        return None

    area = request.user_hotel_area.strip() or hotel_area_hint_for(request.destination) or _city_center_fallback(
        request.destination
    )
    nightly_price = float(request.user_hotel_nightly_price or 0)
    notes = "用户已提供酒店信息，当前方案直接围绕这家酒店安排路线。"
    if request.user_hotel_area.strip():
        notes = f"{notes} 区域：{request.user_hotel_area.strip()}。"
    return HotelCandidate(
        name=request.user_hotel_name.strip() or f"{request.destination} 已选酒店",
        nightly_price=nightly_price,
        area=area,
        notes=notes,
        booking_url=request.user_hotel_url.strip() or _hotel_search_url(request),
        provider="user_input",
    )


def _build_user_transport_candidate(request: TripRequest) -> TransportCandidate | None:
    if not any(
        [
            request.user_transport_label.strip(),
            request.user_transport_url.strip(),
            request.user_transport_total_price is not None,
            request.user_transport_category.strip(),
        ]
    ):
        return None

    category = request.user_transport_category.strip() or "主交通"
    label = request.user_transport_label.strip() or f"{request.departure_city} - {request.destination} 已选交通"
    return TransportCandidate(
        label=label,
        category=category,
        total_price=float(request.user_transport_total_price or 0),
        depart_at=request.user_transport_depart_at.strip() or request.start_date,
        arrive_at=request.user_transport_arrive_at.strip() or request.end_date,
        outbound_arrive_at=request.user_arrival_at_destination.strip(),
        return_depart_at=request.user_return_depart_at.strip(),
        trip_start_date=request.start_date,
        trip_end_date=request.end_date,
        booking_url=request.user_transport_url.strip() or _transport_search_url(request),
        provider="user_input",
    )


def _build_generic_hotel_candidates(request: TripRequest, provider_error: str = "") -> list[HotelCandidate]:
    nightly_price = 0.0
    if request.budget_total and request.nights:
        nightly_price = round((float(request.budget_total) * 0.28) / max(int(request.nights), 1), 2)

    area_hint = _city_center_fallback(request.destination)
    profile_area_hint = hotel_area_hint_for(request.destination)
    preference_text = ""
    if request.hotel_preferences:
        preference_text = f"当前偏好：{'、'.join(request.hotel_preferences[:3])}。"
    if profile_area_hint:
        preference_text = f"{preference_text} 建议区域：{profile_area_hint}".strip()
    error_text = f" 外部搜索状态：{provider_error}" if provider_error else ""

    return [
        HotelCandidate(
            name=f"待锁定：{request.destination} 酒店",
            nightly_price=nightly_price,
            area=area_hint,
            notes=(
                "当前外部酒店搜索不可用，先给出一组可执行的占位候选，后续再替换成真实酒店。"
                f"{preference_text}{error_text}"
            ).strip(),
            booking_url=_hotel_search_url(request),
            provider="rule_fallback",
        )
    ]


def _build_generic_transport_candidates(request: TripRequest, provider_error: str = "") -> list[TransportCandidate]:
    if _prefers_rail(request):
        label = f"建议优先查看 {request.departure_city} - {request.destination} 的高铁往返"
        category = "往返高铁"
    else:
        label = f"待锁定：{request.departure_city} - {request.destination} 主交通"
        category = "主交通"

    estimated_total = round(float(request.budget_total or 0) * 0.35, 2) if request.budget_total else 0.0
    notes = "当前先给出通用交通入口，后续再替换成具体车次、机票或班次。"
    if provider_error:
        notes = f"{notes} 外部搜索状态：{provider_error}"
    return [
        TransportCandidate(
            label=label,
            category=category,
            total_price=estimated_total,
            depart_at=request.start_date,
            arrive_at=request.end_date,
            trip_start_date=request.start_date,
            trip_end_date=request.end_date,
            booking_url=_transport_search_url(request),
            provider="rule_fallback",
            outbound_arrive_at=request.user_arrival_at_destination.strip(),
            return_depart_at=request.user_return_depart_at.strip(),
        )
    ]


def _dedupe_transport_candidates(candidates: list[TransportCandidate]) -> list[TransportCandidate]:
    seen: set[str] = set()
    deduped: list[TransportCandidate] = []
    for candidate in candidates:
        key = _normalize(
            "|".join(
                [
                    candidate.label,
                    candidate.category,
                    str(candidate.total_price),
                    candidate.depart_at,
                    candidate.arrive_at,
                ]
            )
        )
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _rank_transport_candidates(request: TripRequest, candidates: list[TransportCandidate]) -> list[TransportCandidate]:
    if not candidates:
        return []

    prefers_rail = _prefers_rail(request)
    prefers_air = _prefers_air(request)

    def sort_key(candidate: TransportCandidate):
        category_text = _normalize(candidate.category)
        rail_flag = "高铁" in category_text or "火车" in category_text or "rail" in category_text or "train" in category_text
        air_flag = "机票" in category_text or "航班" in category_text or "flight" in category_text or "air" in category_text
        preference_rank = 0
        if prefers_rail:
            preference_rank = 0 if rail_flag else 1
        elif prefers_air:
            preference_rank = 0 if air_flag else 1
        else:
            preference_rank = 0 if rail_flag and candidate.total_price and candidate.total_price <= 1200 else 1 if rail_flag else 2
        total_price = candidate.total_price if candidate.total_price > 0 else 999999
        return (preference_rank, total_price, candidate.depart_at, candidate.label)

    return sorted(_dedupe_transport_candidates(candidates), key=sort_key)


def select_search_results(
    request: TripRequest,
    search_adapter=None,
    route_adapter=None,
    preselected_hotel_candidates: list[HotelCandidate] | None = None,
    preselected_transport_candidates: list[TransportCandidate] | None = None,
    poi_candidates: list[POICandidate] | None = None,
) -> tuple[HotelCandidate | None, list[HotelCandidate], TransportCandidate | None, list[TransportCandidate]]:
    destination_ready = bool(str(request.destination or "").strip())
    departure_ready = bool(str(request.departure_city or "").strip())
    search_available = bool(
        request.enable_live_search
        and search_adapter
        and getattr(search_adapter, "is_available", False)
        and destination_ready
    )
    provider_error = ""
    if request.enable_live_search and not destination_ready:
        provider_error = "需要先确定目的地后，才能执行实时机酒搜索。"
    elif request.enable_live_search and not departure_ready:
        provider_error = "需要先确定出发地后，才能执行实时交通搜索。"
    user_hotel = _build_user_hotel_candidate(request)
    user_transport = _build_user_transport_candidate(request)
    anchor = _hotel_anchor_from_pois(request, poi_candidates or [])

    specs = get_scenario_search_specs(request)
    hotel_spec = specs.get("hotel") or {}
    transport_spec = specs.get("transport") or {}

    if not hotel_spec and request.destination:
        hotel_spec = {
            "destination": request.destination,
            "keyword": anchor if anchor and anchor != request.destination else "",
            "max_price": 1000,
            "fallback_query": f"{request.destination} {anchor} 酒店".strip(),
        }

    if not transport_spec and request.departure_city and request.destination:
        transport_spec = {
            "departure_city": request.departure_city,
            "destination": request.destination,
            "start_date": request.start_date,
            "end_date": request.end_date,
        }

    hotel_candidates: list[HotelCandidate] = [user_hotel] if user_hotel else list(preselected_hotel_candidates or [])
    transport_candidates: list[TransportCandidate] = [user_transport] if user_transport else list(preselected_transport_candidates or [])
    fallback_hotels, fallback_transports = get_scenario_search_fallbacks(request)

    if hotel_spec and search_available and not user_hotel and not hotel_candidates:
        structured_results: list[HotelCandidate] = []
        keyword_results: list[HotelCandidate] = []

        try:
            structured_results = _search_hotels_with_strategy(
                search_adapter,
                destination=hotel_spec.get("destination", request.destination),
                check_in=request.start_date,
                check_out=request.end_date,
                keyword=hotel_spec.get("keyword", ""),
                max_price=int(hotel_spec.get("max_price", 1000)),
                sort="price_asc",
            )
        except Exception as exc:
            try:
                structured_results = _search_hotels_with_strategy(
                    search_adapter,
                    destination=hotel_spec.get("destination", request.destination),
                    check_in=request.start_date,
                    check_out=request.end_date,
                    keyword="",
                    max_price=int(hotel_spec.get("max_price", 1000)),
                    sort="rate_desc",
                )
            except Exception:
                provider_error = str(exc).strip() or provider_error

        if not any(_is_strong_direct_hotel(item) for item in structured_results):
            for extra_sort in ("rate_desc", "price_desc"):
                try:
                    structured_results.extend(
                        _search_hotels_with_strategy(
                            search_adapter,
                            destination=hotel_spec.get("destination", request.destination),
                            check_in=request.start_date,
                            check_out=request.end_date,
                            keyword="",
                            max_price=int(hotel_spec.get("max_price", 1000)),
                            sort=extra_sort,
                        )
                    )
                except Exception as exc:
                    provider_error = str(exc).strip() or provider_error

        keyword_queries = []
        fallback_query = hotel_spec.get("fallback_query", "").strip()
        if fallback_query:
            keyword_queries.append(fallback_query)
        city_center_query = f"{request.destination} 市中心 酒店".strip()
        if city_center_query not in keyword_queries:
            keyword_queries.append(city_center_query)

        if not any(
            _is_likely_real_hotel(item)
            and _has_direct_hotel_price(item)
            and _matches_hotel_anchor(item, anchor)
            for item in structured_results
        ):
            for query in keyword_queries:
                try:
                    keyword_results = [*keyword_results, *search_adapter.keyword_search_hotels(query)]
                except Exception as exc:
                    provider_error = str(exc).strip() or provider_error

        structured_results = _filter_hotel_candidates_for_destination(request, structured_results)
        keyword_results = _filter_hotel_candidates_for_destination(request, keyword_results)
        hotel_candidates = _merge_hotel_candidates(request, anchor, structured_results, keyword_results)
        hotel_candidates = [
            candidate
            for candidate in hotel_candidates
            if _is_plausible_hotel_candidate(request, candidate, route_adapter=route_adapter)
        ]
        hotel_candidates = _prefer_real_hotels(hotel_candidates)
        if hotel_candidates and not any(_is_likely_real_hotel(candidate) for candidate in hotel_candidates):
            hotel_candidates = []

        if not hotel_candidates and fallback_hotels:
            hotel_candidates = fallback_hotels

    if transport_spec and search_available and not user_transport and not transport_candidates:
        live_transport_candidates: list[TransportCandidate] = []
        if hasattr(search_adapter, "search_trains"):
            try:
                live_transport_candidates.extend(
                    search_adapter.search_trains(
                        departure_city=transport_spec.get("departure_city", request.departure_city),
                        destination=transport_spec.get("destination", request.destination),
                        start_date=transport_spec.get("start_date", request.start_date),
                        end_date=transport_spec.get("end_date", request.end_date),
                    )
                )
            except Exception as exc:
                provider_error = str(exc).strip() or provider_error

        try:
            live_transport_candidates.extend(
                search_adapter.search_transport(
                    departure_city=transport_spec.get("departure_city", request.departure_city),
                    destination=transport_spec.get("destination", request.destination),
                    start_date=transport_spec.get("start_date", request.start_date),
                    end_date=transport_spec.get("end_date", request.end_date),
                )
            )
        except Exception as exc:
            provider_error = str(exc).strip() or provider_error

        transport_candidates = _rank_transport_candidates(request, live_transport_candidates)

    if not transport_candidates and fallback_transports:
        transport_candidates = fallback_transports

    if provider_error and search_adapter:
        if hotel_candidates or transport_candidates:
            setattr(search_adapter, "last_warning", provider_error)
            setattr(search_adapter, "last_error", "")
        else:
            setattr(search_adapter, "last_error", provider_error)

    if not hotel_candidates and not request.scenario_id:
        hotel_candidates = _build_generic_hotel_candidates(request, provider_error=provider_error)

    if not transport_candidates and not request.scenario_id:
        transport_candidates = _build_generic_transport_candidates(request, provider_error=provider_error)

    if user_hotel and hotel_candidates and hotel_candidates[0].provider != "user_input":
        hotel_candidates = [user_hotel, *hotel_candidates]
    if user_transport and transport_candidates and transport_candidates[0].provider != "user_input":
        transport_candidates = [user_transport, *transport_candidates]

    hotel_candidates = _backfill_missing_hotel_prices(request, hotel_candidates)
    selected_hotel = hotel_candidates[0] if hotel_candidates else None
    selected_transport = transport_candidates[0] if transport_candidates else None

    if provider_error and selected_hotel and selected_hotel.provider == "rule_fallback" and provider_error not in selected_hotel.notes:
        selected_hotel.notes = f"{selected_hotel.notes} 外部搜索状态：{provider_error}".strip()
    if provider_error and selected_transport and selected_transport.provider == "rule_fallback":
        transport_notes = getattr(selected_transport, "notes", "") if hasattr(selected_transport, "notes") else ""
        if provider_error not in transport_notes:
            selected_transport.notes = f"{transport_notes} 外部搜索状态：{provider_error}".strip()

    return selected_hotel, hotel_candidates, selected_transport, transport_candidates


def _is_plausible_hotel_candidate(
    request: TripRequest,
    candidate: HotelCandidate,
    *,
    route_adapter=None,
) -> bool:
    if candidate.provider in {"user_input", "rule_fallback"}:
        return True
    if _has_conflicting_city_token(candidate, request.destination):
        return False
    if _destination_match_score(candidate, request.destination) > 0:
        return True
    if candidate.provider != "flyai" and not route_adapter:
        return True
    if route_adapter:
        try:
            estimate = route_adapter.estimate_transfer(candidate.name, _city_center_fallback(request.destination), "DRIVE")
        except Exception:
            estimate = None
        if estimate and estimate.duration_minutes <= 60:
            return True
    return False


def _route_mode_counts(daily_plan) -> tuple[Counter, Counter]:
    mode_counter: Counter = Counter()
    provider_counter: Counter = Counter()
    for day in daily_plan or []:
        items = getattr(day, "items", None)
        if items is None and isinstance(day, dict):
            items = day.get("items", [])
        for item in items or []:
            route_mode = getattr(item, "route_mode", None)
            route_provider = getattr(item, "route_provider", None)
            if isinstance(item, dict):
                route_mode = item.get("route_mode", route_mode)
                route_provider = item.get("route_provider", route_provider)
            if route_mode:
                mode_counter[str(route_mode)] += 1
            if route_provider:
                provider_counter[str(route_provider)] += 1
    return mode_counter, provider_counter


def _maps_prep_url(provider: str) -> str:
    text = str(provider or "").strip().lower()
    if text == "google":
        return "https://www.google.com/maps/"
    if text == "amap":
        return "https://ditu.amap.com/"
    return ""


def _maps_provider_label(provider: str) -> str:
    text = str(provider or "").strip().lower()
    mapping = {
        "amap": "高德地图",
        "google": "Google Maps",
        "manual": "保守时间预留",
    }
    return mapping.get(text, provider)


def _build_local_transport_booking_item(request: TripRequest, daily_plan) -> BookingItem | None:
    mode_counter, provider_counter = _route_mode_counts(daily_plan)
    if not mode_counter:
        return None

    primary_mode = mode_counter.most_common(1)[0][0]
    primary_provider = provider_counter.most_common(1)[0][0] if provider_counter else ""
    provider_label = _maps_provider_label(primary_provider) if primary_provider else ""

    notes = ""
    why_now = "这一步不一定先下单，但会直接影响首日到达后的执行效率。"
    risk_if_wait = "现场再处理很容易出现找错出入口、换乘判断慢，或者高峰期临时决策失误。"
    if primary_mode == "TRANSIT":
        notes = f"当前方案市内移动以公交地铁为主，建议提前确认 {request.destination} 的乘车码、换乘站和首日到酒店路线。"
    elif primary_mode == "DRIVE":
        notes = f"当前方案市内移动以打车 / 驾车为主，建议提前确认 {request.destination} 的上落客点、网约车候车区和高峰时段。"
    elif primary_mode == "WALK":
        notes = f"当前方案包含多段步行，建议提前确认 {request.destination} 片区之间是否适合拖行李步行，并准备舒适鞋和雨具。"
    else:
        notes = f"当前方案的市内交通需要单独准备，建议提前把 {request.destination} 的地图和首末段路线先跑一遍。"
    if provider_label:
        if provider_label == "保守时间预留":
            notes = f"{notes} 目前少量路段还是按保守时间预留，出发前再用地图跑一遍即可。"
        else:
            notes = f"{notes} 当前路线时间主要参考 {provider_label}。"

    return BookingItem(
        name="市内交通准备",
        category="交通",
        url=_maps_prep_url(primary_provider),
        priority="recommended",
        timing="出发前 1-2 天",
        notes=notes,
        why_now=why_now,
        risk_if_wait=risk_if_wait,
    )


def merge_booking_items(
    request: TripRequest,
    existing_items: list[BookingItem],
    selected_hotel: HotelCandidate | None,
    selected_transport: TransportCandidate | None,
    daily_plan=None,
) -> list[BookingItem]:
    merged = list(existing_items)
    if selected_transport:
        merged = [item for item in merged if item.category != "交通" or item.name != "主交通"]
    if selected_hotel:
        merged = [item for item in merged if item.category != "住宿" or item.name != "主酒店"]

    if selected_transport:
        transport_note = f"当前主交通候选，总价约 {selected_transport.total_price:.0f} 元。"
        why_now = "主交通会直接决定到达日和返程日的时间线，应先锁定。"
        risk_if_wait = "拖晚后常见风险是价格上涨，或者只剩不合适的时段。"
        if selected_transport.provider == "user_input":
            transport_note = (
                f"用户已手动填写主交通，总价约 {selected_transport.total_price:.0f} 元。"
                " 当前主要任务是核对退改规则、出发到达时间和链接是否准确。"
            )
            why_now = "这条交通已经选定，当前主要任务是核对信息是否正确。"
            risk_if_wait = "如果最后才核对，可能到执行前才发现时间或规则不对。"
        elif selected_transport.provider == "rule_fallback":
            transport_note = (
                f"当前还是通用交通入口，总价先按约 {selected_transport.total_price:.0f} 元估算。"
                " 后续需要替换成真实车次、机票或班次。"
            )
            why_now = "现在至少要把交通方向先定下来，避免后面整套方案没有锚点。"
            risk_if_wait = "不先锁交通方向，后面的预算和时间块都会持续漂移。"
        elif selected_transport.provider == "flyai":
            transport_note = (
                f"当前主交通来自实时搜索，总价约 {selected_transport.total_price:.0f} 元。"
                " 建议尽快二次确认价格、退改政策和时间段。"
            )
        merged.insert(
            0,
            BookingItem(
                name=selected_transport.label,
                category=selected_transport.category,
                url=selected_transport.booking_url,
                priority="required",
                timing="优先锁定",
                notes=transport_note,
                why_now=why_now,
                risk_if_wait=risk_if_wait,
            ),
        )
    elif request.departure_city and request.destination:
        merged.insert(
            0,
            BookingItem(
                name="主交通",
                category="交通",
                priority="required",
                timing="优先锁定",
                notes="当前还没有接入可用的主交通候选链接。",
                why_now="主交通会决定整套时间线。",
                risk_if_wait="交通不确定时，后续行程只能停留在粗排层。",
            ),
        )

    if selected_hotel:
        hotel_note = f"当前主酒店候选，每晚约 {selected_hotel.nightly_price:.0f} 元。{selected_hotel.notes}".strip()
        why_now = "酒店位置会影响每天第一段和最后一段移动效率，也会直接影响预算。"
        risk_if_wait = "拖晚后常见问题是核心区域无房、价格上涨，或者只剩不顺路的位置。"
        if selected_hotel.provider == "user_input":
            hotel_note = (
                f"用户已手动填写酒店，每晚约 {selected_hotel.nightly_price:.0f} 元。"
                f" {selected_hotel.notes} 后续主要检查取消政策、入住人数和位置是否准确。"
            ).strip()
            why_now = "酒店已经选定，当前主要任务是核对房型、入住人数和退改条件。"
            risk_if_wait = "如果信息没核准，后面可能出现人数不匹配或位置理解错误。"
        elif selected_hotel.provider == "rule_fallback":
            hotel_note = (
                f"当前还是通用酒店入口，每晚先按约 {selected_hotel.nightly_price:.0f} 元估算。"
                f" {selected_hotel.notes} 后续需要替换成真实酒店。"
            ).strip()
            why_now = "至少要先把住宿片区定下来，路线才能排顺。"
            risk_if_wait = "如果酒店一直未定，后续路线和预算都只能停留在估算层。"
        elif selected_hotel.provider == "flyai":
            hotel_note = (
                f"当前主酒店来自实时搜索，每晚约 {selected_hotel.nightly_price:.0f} 元。"
                f" {selected_hotel.notes}"
            ).strip()
        merged.insert(
            1,
            BookingItem(
                name=selected_hotel.name,
                category="住宿",
                url=selected_hotel.booking_url,
                priority="required",
                timing="出发前 1-2 周",
                notes=hotel_note,
                why_now=why_now,
                risk_if_wait=risk_if_wait,
            ),
        )

    local_transport_item = _build_local_transport_booking_item(request, daily_plan)
    if local_transport_item and not any(item.name == local_transport_item.name for item in merged):
        merged.append(local_transport_item)

    return merged
