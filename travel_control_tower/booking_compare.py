from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlencode


AIRPORT_CODES = {
    "杭州": "HGH",
    "新加坡": "SIN",
    "槟城": "PEN",
    "巴厘岛": "DPS",
    "登巴萨": "DPS",
    "吉隆坡": "KUL",
    "上海": "SHA",
    "北京": "BJS",
    "广州": "CAN",
    "深圳": "SZX",
    "大阪": "OSA",
    "东京": "TYO",
}


def _text(value) -> str:
    return str(value or "").strip()


def _snapshot_value(plan: dict, *keys: str) -> str:
    snapshot = plan.get("input_snapshot") or {}
    for key in keys:
        value = _text(snapshot.get(key))
        if value:
            return value
    return ""


def _airport_code(value: str) -> str:
    text = _text(value)
    matches = re.findall(r"\b[A-Z]{3}\b", text.upper())
    if matches:
        return matches[-1]
    for city, code in AIRPORT_CODES.items():
        if city in text:
            return code
    return ""


def _iso_date(value: str) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", _text(value))
    return match.group(0) if match else ""


def _stay_dates(group: dict, plan: dict) -> tuple[str, str]:
    raw = _text(group.get("dates"))
    matches = re.findall(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}", raw)
    check_in = matches[0] if matches else ""
    if check_in and len(check_in) == 5:
        year = _snapshot_value(plan, "开始日期", "start_date")[:4]
        check_in = f"{year}-{check_in}" if year else ""
    check_out = matches[1] if len(matches) > 1 else ""
    if check_out and len(check_out) == 5 and check_in:
        check_out = f"{check_in[:4]}-{check_out}"
    if check_in and not check_out:
        try:
            check_out = (date.fromisoformat(check_in) + timedelta(days=max(1, int(group.get("nights") or 1)))).isoformat()
        except ValueError:
            check_out = ""
    return check_in, check_out


def hotel_provider_links(city: str, hotel_name: str, check_in: str, check_out: str) -> dict[str, str]:
    params = {
        "cityname": _text(city),
        "checkin": _iso_date(check_in),
        "checkout": _iso_date(check_out),
        "keyword": _text(hotel_name),
    }
    clean = {key: value for key, value in params.items() if value}
    fliggy_params = {
        "cityName": clean.get("cityname", ""),
        "checkIn": clean.get("checkin", ""),
        "checkOut": clean.get("checkout", ""),
        "keywords": clean.get("keyword", ""),
    }
    return {
        "ctrip_url": f"https://hotels.ctrip.com/hotels/list?{urlencode(clean)}",
        "fliggy_url": f"https://www.fliggy.com/jiudian/?{urlencode({key: value for key, value in fliggy_params.items() if value})}",
    }


def flight_provider_links(origin: str, destination: str, depart_date: str, adults: int = 1) -> dict[str, str]:
    origin_code = _airport_code(origin)
    destination_code = _airport_code(destination)
    travel_date = _iso_date(depart_date)
    adults = max(1, int(adults or 1))
    if not origin_code or not destination_code or not travel_date:
        return {
            "ctrip_url": "https://flights.ctrip.com/",
            "fliggy_url": "https://sjipiao.fliggy.com/",
        }
    ctrip_query = urlencode({"depdate": travel_date, "cabin": "y_s_c_f", "adult": adults, "child": 0, "infant": 0})
    fliggy_query = urlencode(
        {
            "tripType": 0,
            "depCity": origin_code,
            "arrCity": destination_code,
            "depDate": travel_date,
            "adultNum": adults,
            "childNum": 0,
        }
    )
    return {
        "ctrip_url": f"https://flights.ctrip.com/online/list/oneway-{origin_code.lower()}-{destination_code.lower()}?{ctrip_query}",
        "fliggy_url": f"https://sjipiao.fliggy.com/flight_search_result.htm?{fliggy_query}",
    }


def _day_date(plan: dict, day_index: int) -> str:
    for day in plan.get("daily_plan") or []:
        if int(day.get("day_index") or 0) == int(day_index or 0):
            return _iso_date(day.get("date"))
    return ""


def _flight_date_for_route(plan: dict, origin_code: str, destination_code: str) -> str:
    for segment in plan.get("route_segments") or []:
        if _text(segment.get("mode")).lower() != "flight":
            continue
        segment_origin = _airport_code(segment.get("origin"))
        segment_destination = _airport_code(segment.get("destination"))
        if segment_origin == origin_code and segment_destination == destination_code:
            return _day_date(plan, int(segment.get("day_index") or 0))
    return ""


def build_provider_comparison_rows(plan: dict) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    intake = plan.get("intake") or {}
    travelers_raw = (
        _snapshot_value(plan, "人数", "traveler_count")
        or _text((intake.get("confirmed_fields") or {}).get("travelers"))
        or _text((plan.get("overview") or {}).get("travelers"))
        or _text(intake.get("travelers"))
    )
    traveler_match = re.search(r"\d+", travelers_raw)
    adults = int(traveler_match.group(0)) if traveler_match else 1

    stay_groups = plan.get("hotel_stay_groups") or []
    if stay_groups:
        for group in stay_groups:
            option = group.get("recommended_option") or {}
            name = _text(option.get("name") or option.get("selected"))
            city = _text(group.get("city") or option.get("city"))
            if not name:
                continue
            check_in, check_out = _stay_dates(group, plan)
            links = hotel_provider_links(city, name, check_in, check_out)
            rows.append(
                {
                    "kind": "酒店",
                    "title": name,
                    "detail": f"{city} · {check_in or '日期待定'} 至 {check_out or '日期待定'} · {int(group.get('nights') or 1)}晚",
                    "price": _text(option.get("price_cny_per_night") or option.get("price") or "价格待平台复核"),
                    "readiness": "仅搜索入口",
                    **links,
                }
            )
    else:
        check_in = _snapshot_value(plan, "开始日期", "start_date")
        check_out = _snapshot_value(plan, "结束日期", "end_date")
        city_default = _snapshot_value(plan, "目的地", "destination")
        for option in (plan.get("hotel_candidates") or [])[:6]:
            name = _text(option.get("name") or option.get("selected"))
            if not name:
                continue
            city = _text(option.get("city") or city_default)
            rows.append(
                {
                    "kind": "酒店",
                    "title": name,
                    "detail": f"{city} · {check_in or '日期待定'} 至 {check_out or '日期待定'}",
                    "price": _text(option.get("nightly_price") or option.get("price_cny_per_night") or "价格待平台复核"),
                    "readiness": "仅搜索入口",
                    **hotel_provider_links(city, name, check_in, check_out),
                }
            )

    transports = plan.get("transport_candidates") or []
    start_default = _snapshot_value(plan, "开始日期", "start_date")
    origin_default = _snapshot_value(plan, "出发地", "departure_city")
    destination_default = _snapshot_value(plan, "目的地", "destination")
    for option in transports[:8]:
        route = _text(option.get("route"))
        route_codes = re.findall(r"\b[A-Z]{3}\b", route.upper())
        origin = route_codes[0] if route_codes else origin_default
        destination = route_codes[-1] if route_codes else destination_default
        origin_code = _airport_code(origin)
        destination_code = _airport_code(destination)
        depart_date = _flight_date_for_route(plan, origin_code, destination_code) or start_default
        title = _text(option.get("name") or option.get("label"))
        if not title:
            continue
        rows.append(
            {
                "kind": "机票",
                "title": title,
                "detail": f"{origin_code or origin} → {destination_code or destination} · {depart_date or '日期待定'} · {adults}位成人",
                "price": _text(option.get("price") or option.get("total_price") or "价格待平台复核"),
                "readiness": "仅搜索入口",
                **flight_provider_links(origin, destination, depart_date, adults),
            }
        )

    if not transports and plan.get("selected_transport"):
        option = plan.get("selected_transport") or {}
        title = _text(option.get("label"))
        rows.append(
            {
                "kind": "机票/交通",
                "title": title or f"{origin_default}至{destination_default}",
                "detail": f"{origin_default} → {destination_default} · {start_default or '日期待定'} · {adults}位成人",
                "price": _text(option.get("total_price") or "价格待平台复核"),
                "readiness": "仅搜索入口",
                **flight_provider_links(origin_default, destination_default, start_default, adults),
            }
        )
    return rows
