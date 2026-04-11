from __future__ import annotations

from ..adapters.base import POICandidate
from .city_profiles import resolve_default_points
from .models import TripRequest
from .poi_scoring import (
    HISTORIC_COMMERCE_TOKENS,
    ICONIC_COMMERCE_TOKENS,
    is_business_building,
    is_generic_commercial_street,
    is_generic_urban_park,
    is_photo_spot,
    poi_quality_score,
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
    "海洋公园",
    "海洋馆",
    "长隆",
    "宋城",
    "动物园",
    "千岛湖",
    "森湖",
)

POI_NEGATIVE_NAME_TOKENS = (
    "酒店",
    "宾馆",
    "旅舍",
    "青年旅舍",
    "停车场",
    "公交站",
    "地铁站",
    "项目部",
    "管理处",
    "公司",
    "顾问",
    "水果",
    "超市",
    "书房",
    "店",
)

POI_NEGATIVE_CATEGORY_TOKENS = (
    "公司企业",
    "商务住宅",
    "政府机构",
    "生活服务",
    "交通设施服务",
    "餐饮服务",
    "购物服务;购物相关场所",
    "购物服务;综合市场",
    "购物服务;便民商店",
    "购物服务;专卖店",
    "购物服务;商铺",
)


def select_poi_candidates(
    request: TripRequest,
    search_adapter=None,
    fallback_search_adapter=None,
    max_items: int = 8,
) -> list[POICandidate]:
    if not str(request.destination or "").strip():
        return []
    primary = (
        search_adapter
        if request.enable_live_search and search_adapter and getattr(search_adapter, "is_available", False)
        else None
    )
    fallback = (
        fallback_search_adapter
        if fallback_search_adapter
        and getattr(fallback_search_adapter, "is_available", False)
        and fallback_search_adapter is not primary
        else None
    )
    if not primary and not fallback:
        return []

    adapters = [adapter for adapter in (primary, fallback) if adapter and hasattr(adapter, "search_pois")]
    if fallback and getattr(fallback, "provider_name", "") == "amap" and _contains_cjk(request.destination):
        adapters = [adapter for adapter in (fallback, primary) if adapter and hasattr(adapter, "search_pois")]
    if not adapters:
        return []

    merged: list[POICandidate] = []
    pool_limit = max(max_items * 4, 20)

    for generic_keyword in _generic_keywords_for(request):
        for adapter in adapters:
            try:
                keyword_results = adapter.search_pois(request.destination, keyword=generic_keyword, max_items=6)
            except Exception:
                continue
            merged = _dedupe_candidates([*merged, *keyword_results])
            if len(merged) >= pool_limit:
                break
        if len(merged) >= pool_limit:
            break

    for adapter in adapters:
        try:
            city_results = adapter.search_pois(request.destination, max_items=max_items)
        except Exception:
            city_results = []
        merged = _dedupe_candidates([*merged, *city_results])
        if len(merged) >= pool_limit:
            break

    must_go_terms = [item.strip() for item in request.must_go if item.strip()][:4]
    for keyword in must_go_terms:
        if _contains_keyword_candidate(merged, keyword):
            continue
        for adapter in adapters:
            try:
                keyword_results = adapter.search_pois(request.destination, keyword=keyword, max_items=4)
            except Exception:
                continue
            merged = _dedupe_candidates([*merged, *keyword_results])
            if _contains_keyword_candidate(merged, keyword):
                break

    localized = _filter_to_destination(merged, request)
    filtered = _filter_generic_city_noise(localized, request)
    filtered = _inject_profile_anchors(filtered, request)
    filtered.sort(key=lambda item: _poi_sort_key(item, request.must_go))
    return _select_diversified_candidates(filtered, request.must_go, max_items=max_items)


def _dedupe_candidates(items: list[POICandidate]) -> list[POICandidate]:
    seen: set[str] = set()
    deduped: list[POICandidate] = []
    for item in items:
        key = _normalize_name(item.name)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _contains_keyword_candidate(items: list[POICandidate], keyword: str) -> bool:
    normalized_keyword = _normalize_name(keyword)
    for item in items:
        normalized_name = _normalize_name(item.name)
        if normalized_keyword and (normalized_keyword in normalized_name or normalized_name in normalized_keyword):
            return True
    return False


def _poi_sort_key(item: POICandidate, must_go: list[str]) -> tuple[int, int, str]:
    match_score = 0
    normalized_name = _normalize_name(item.name)
    for index, keyword in enumerate(must_go):
        normalized_keyword = _normalize_name(keyword)
        if normalized_keyword and (normalized_keyword in normalized_name or normalized_name in normalized_keyword):
            match_score = max(match_score, 100 - index * 10)
    quality_score = poi_quality_score(
        name=item.name,
        category=item.category,
        level=item.poi_level,
        notes=item.notes,
        address=item.address,
    )
    if item.provider == "profile_anchor":
        quality_score += 40
    free_score = 5 if item.free_status else 0
    return (-match_score, -(quality_score + free_score), item.name)


def _normalize_name(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def _generic_keywords_for(request: TripRequest) -> list[str]:
    if request.must_go:
        return []
    if _contains_cjk(request.destination):
        return ["历史街区", "古街", "景区", "博物馆", "步行街", "古城"]
    return ["landmark", "museum", "historic district", "old street", "scenic area"]


def _filter_generic_city_noise(items: list[POICandidate], request: TripRequest) -> list[POICandidate]:
    if not items:
        return []

    must_go_names = {_normalize_name(name) for name in request.must_go}
    kept: list[POICandidate] = []
    positive_kept: list[POICandidate] = []
    negative_only: list[POICandidate] = []

    for item in items:
        blob = " ".join([item.name or "", item.category or "", item.address or "", item.notes or ""])
        normalized_blob = _normalize_name(blob)
        if any(keyword and (keyword in normalized_blob or normalized_blob in keyword) for keyword in must_go_names):
            kept.append(item)
            continue
        if _is_obvious_non_poi(item):
            negative_only.append(item)
            continue
        if any(_normalize_name(token) in normalized_blob for token in POI_NEGATIVE_TOKENS):
            negative_only.append(item)
            continue
        if is_business_building(blob) or is_photo_spot(blob):
            negative_only.append(item)
            continue
        if is_generic_urban_park(blob) or is_generic_commercial_street(blob):
            negative_only.append(item)
            continue
        positive_kept.append(item)

    if positive_kept:
        if len(kept) + len(positive_kept) >= 4:
            return [*kept, *positive_kept]
        return [*kept, *positive_kept, *negative_only]
    return [*kept, *negative_only]


def _filter_to_destination(items: list[POICandidate], request: TripRequest) -> list[POICandidate]:
    if not items:
        return []
    matched = [item for item in items if _matches_destination(item, request.destination)]
    return matched


def _matches_destination(item: POICandidate, destination: str) -> bool:
    tokens = _destination_tokens(destination)
    blob = " ".join([item.name or "", item.city_name or "", item.address or "", item.notes or ""])
    normalized_blob = _normalize_name(blob)
    return any(_normalize_name(token) in normalized_blob for token in tokens if token)


def _destination_tokens(destination: str) -> list[str]:
    destination = str(destination or "").strip()
    tokens = [destination]
    if destination.endswith("市"):
        tokens.append(destination[:-1])
    return [token for token in tokens if token]


def _poi_bucket(item: POICandidate) -> str:
    blob = " ".join([item.name or "", item.category or "", item.address or "", item.notes or ""])
    if is_generic_commercial_street(blob):
        return "generic_commerce"
    if _contains_any(blob, ("博物馆", "museum", "展馆")):
        return "museum"
    if _contains_any(blob, ICONIC_COMMERCE_TOKENS) or _contains_any(blob, HISTORIC_COMMERCE_TOKENS):
        return "commerce"
    if _contains_any(blob, ("故宫", "宫", "院", "城墙", "古城", "寺", "神社", "塔", "地标", "景区", "风景名胜区", "遗址", "钟楼", "天安门", "西湖", "玄武湖")):
        return "heritage"
    if is_generic_urban_park(blob):
        return "park"
    return "other"


def _select_diversified_candidates(
    items: list[POICandidate],
    must_go: list[str],
    *,
    max_items: int,
) -> list[POICandidate]:
    must_go_names = {_normalize_name(name) for name in must_go}
    caps = {
        "commerce": 2,
        "generic_commerce": 1,
        "museum": 2,
        "heritage": 4,
        "park": 1,
        "other": 2,
    }
    selected: list[POICandidate] = []
    counts = {key: 0 for key in caps}
    deferred: list[POICandidate] = []

    for item in items:
        normalized_name = _normalize_name(item.name)
        if any(keyword and (keyword in normalized_name or normalized_name in keyword) for keyword in must_go_names):
            selected.append(item)
            continue

        bucket = _poi_bucket(item)
        if counts.get(bucket, 0) < caps.get(bucket, max_items):
            selected.append(item)
            counts[bucket] = counts.get(bucket, 0) + 1
        else:
            deferred.append(item)
        if len(selected) >= max_items:
            return selected[:max_items]

    for item in deferred:
        if item in selected:
            continue
        selected.append(item)
        if len(selected) >= max_items:
            break

    return selected[:max_items]


def _contains_any(value: str, tokens: tuple[str, ...] | list[str]) -> bool:
    haystack = _normalize_name(value)
    return any(_normalize_name(token) in haystack for token in tokens if token)


def _inject_profile_anchors(items: list[POICandidate], request: TripRequest) -> list[POICandidate]:
    anchors = resolve_default_points(request.destination)
    if not anchors:
        return items

    existing = {_normalize_name(item.name) for item in items}
    injected: list[POICandidate] = []
    for anchor in anchors:
        normalized_anchor = _normalize_name(anchor)
        if not normalized_anchor or normalized_anchor in existing:
            continue
        injected.append(
            POICandidate(
                name=anchor,
                city_name=request.destination,
                category="城市主线锚点",
                notes="用于稳定该城市的默认主线，后续再由路线和时间块细化。",
                provider="profile_anchor",
            )
        )
    return [*injected, *items]


def _is_obvious_non_poi(item: POICandidate) -> bool:
    name_blob = _normalize_name(item.name)
    category_blob = _normalize_name(item.category)
    notes_blob = _normalize_name(item.notes)

    if "暂停开放" in (item.name or "") or "暂停开放" in (item.notes or ""):
        return True
    if any(_normalize_name(token) in name_blob for token in POI_NEGATIVE_NAME_TOKENS if token):
        if "特色商业街" not in (item.category or ""):
            return True
    if any(_normalize_name(token) in category_blob for token in POI_NEGATIVE_CATEGORY_TOKENS if token):
        return True
    if "特色商业街" in (item.category or ""):
        return False
    if "购物服务" in (item.category or "") and not (
        _contains_any(item.name, ICONIC_COMMERCE_TOKENS) or _contains_any(item.name, HISTORIC_COMMERCE_TOKENS)
    ):
        return True
    if "停车" in notes_blob or "公交站" in notes_blob:
        return True
    return False


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))
