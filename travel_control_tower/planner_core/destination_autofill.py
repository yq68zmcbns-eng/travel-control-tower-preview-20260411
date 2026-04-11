from __future__ import annotations


AUTO_DESTINATION_RULES: dict[str, dict[str, str]] = {
    "上海": {
        "weekend_relaxed": "苏州",
        "city_three_day": "南京",
    },
    "南京": {
        "weekend_relaxed": "扬州",
        "city_three_day": "苏州",
    },
    "杭州": {
        "weekend_relaxed": "绍兴",
        "city_three_day": "南京",
    },
    "北京": {
        "weekend_relaxed": "天津",
        "city_three_day": "青岛",
    },
    "广州": {
        "weekend_relaxed": "珠海",
        "city_three_day": "长沙",
    },
    "深圳": {
        "weekend_relaxed": "珠海",
        "city_three_day": "厦门",
    },
    "成都": {
        "weekend_relaxed": "重庆",
        "city_three_day": "西安",
    },
    "武汉": {
        "weekend_relaxed": "长沙",
        "city_three_day": "南京",
    },
}

POI_CITY_HINTS: dict[str, str] = {
    "夫子庙": "南京",
    "老门东": "南京",
    "中山陵": "南京",
    "平江路": "苏州",
    "山塘街": "苏州",
    "拙政园": "苏州",
    "西湖": "杭州",
    "灵隐寺": "杭州",
    "太平老街": "长沙",
    "岳麓山": "长沙",
    "洪崖洞": "重庆",
    "解放碑": "重庆",
    "宽窄巷子": "成都",
    "春熙路": "成都",
}


def _canonical_city_name(value: str) -> str:
    text = str(value or "").strip()
    if text.endswith("市") and len(text) > 2:
        return text[:-1]
    return text


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def suggest_destination(
    *,
    freeform_text: str,
    departure_city: str,
    travel_style: str = "",
    target_days: int | None = None,
    must_go: list[str] | None = None,
) -> str:
    text = str(freeform_text or "").strip()
    departure = _canonical_city_name(departure_city)
    if not departure:
        return ""

    for keyword in must_go or []:
        key = str(keyword or "").strip()
        if key in POI_CITY_HINTS:
            return POI_CITY_HINTS[key]

    if not text:
        return ""

    style = str(travel_style or "").strip().lower()
    is_relaxed = style == "relaxed" or _contains_any(text, ("轻松", "放松", "悠闲", "不要太赶", "附近城市"))
    is_three_day = (
        (target_days or 0) >= 3
        or _contains_any(text, ("三天城市小旅行", "适合三天旅行", "三天旅行", "一个适合三天旅行的城市"))
    )
    if is_relaxed and _contains_any(text, ("周末", "两天", "附近城市")):
        return AUTO_DESTINATION_RULES.get(departure, {}).get("weekend_relaxed", "")
    if is_three_day:
        return AUTO_DESTINATION_RULES.get(departure, {}).get("city_three_day", "")
    if _contains_any(text, ("附近城市", "周末")):
        return AUTO_DESTINATION_RULES.get(departure, {}).get("weekend_relaxed", "")
    return ""
