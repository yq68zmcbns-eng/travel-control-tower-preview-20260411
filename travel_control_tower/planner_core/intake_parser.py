from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta


CHINESE_NUMERALS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

RELATIVE_PREFIX_PATTERN = r"(?:这周末|本周末|下周末|下下周末|这周|本周|下周)"
CITY_PATTERN = r"[\u4e00-\u9fffA-Za-z]{2,16}"
CITY_ROUTE_BOUNDARY = r"(?=(?:[,.;，。；:\s]|待[一二两三四五六七八九十\d]+天|[一二两三四五六七八九十\d]+天|预算|想去|必去|一定要去|$))"


@dataclass
class ParsedRequest:
    departure_city: str = ""
    destination: str = ""
    start_date: str = ""
    end_date: str = ""
    days: int | None = None
    nights: int | None = None
    target_trip_days: int | None = None
    target_trip_nights: int | None = None
    traveler_count: int | None = None
    budget_per_person: float | None = None
    travel_style: str = ""
    request_mode: str = "itinerary"
    flexible_window_start: str = ""
    flexible_window_end: str = ""
    price_priority: str = "balanced"
    must_go: list[str] = field(default_factory=list)
    hotel_preferences: list[str] = field(default_factory=list)
    transport_preferences: list[str] = field(default_factory=list)


def parse_freeform_request(raw: str, today: date | None = None) -> ParsedRequest:
    text = _normalize_text(raw)
    if not text:
        return ParsedRequest()

    current_day = today or date.today()
    parsed = ParsedRequest()

    _fill_mode_and_price_priority(parsed, text, current_day)
    _fill_cities(parsed, text)
    _fill_dates(parsed, text, current_day)
    _fill_duration(parsed, text)
    _fill_budget(parsed, text)
    _fill_travelers(parsed, text)
    _fill_style(parsed, text)
    _fill_must_go(parsed, text)
    _fill_transport_preferences(parsed, text)
    _fill_hotel_preferences(parsed, text)
    _backfill_dates_from_duration(parsed, current_day)

    return parsed


def _normalize_text(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    return (
        text.replace("，", ",")
        .replace("。", ".")
        .replace("；", ";")
        .replace("：", ":")
        .replace("（", "(")
        .replace("）", ")")
        .replace("　", " ")
    )


def _fill_cities(parsed: ParsedRequest, text: str) -> None:
    cleaned = _strip_price_scan_window_prefix(text)
    cleaned = re.sub(rf"^{RELATIVE_PREFIX_PATTERN}", "", cleaned).strip()
    patterns = [
        rf"从(?P<dep>{CITY_PATTERN})出发(?:[\s,，。.;；]*)?(?:去|到|飞|前往)(?P<dest>{CITY_PATTERN}?){CITY_ROUTE_BOUNDARY}",
        rf"(?P<dep>{CITY_PATTERN})(?:出发)?(?:[\s,，。.;；]*)?(?:去|到|飞|前往)(?P<dest>{CITY_PATTERN}?){CITY_ROUTE_BOUNDARY}",
        rf"(?P<dep>{CITY_PATTERN})\s*[-到至]\s*(?P<dest>{CITY_PATTERN}?)(?=(?:[,.;，。；:\s]|$))",
    ]

    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if not match:
            continue
        parsed.departure_city = _clean_city_name(match.group("dep"))
        parsed.destination = _clean_city_name(match.group("dest"))
        return


def _strip_price_scan_window_prefix(text: str) -> str:
    return re.sub(
        r"^(?:未来|最近)\s*[一二两三四五六七八九十\d]+\s*(?:个月|天)(?:内)?",
        "",
        text,
    ).strip()


def _clean_city_name(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^从", "", text)
    text = re.sub(rf"^{RELATIVE_PREFIX_PATTERN}", "", text)
    text = re.sub(r"(?:出发|过去|前往)$", "", text)
    text = re.sub(r"(?:玩|逛|待)$", "", text)
    text = re.sub(r"(?:[一二两三四五六七八九十\d]+天.*)$", "", text)
    text = re.sub(r"(?:预算.*)$", "", text)
    return text.strip()


def _fill_dates(parsed: ParsedRequest, text: str, today: date) -> None:
    full_dates = re.findall(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?", text)
    if len(full_dates) >= 2:
        parsed.start_date = _build_iso(full_dates[0])
        parsed.end_date = _build_iso(full_dates[1])
        return
    if len(full_dates) == 1:
        parsed.start_date = _build_iso(full_dates[0])

    month_day_dates = re.findall(r"(\d{1,2})月(\d{1,2})日?", text)
    if len(month_day_dates) >= 2:
        parsed.start_date = _build_iso((today.year, month_day_dates[0][0], month_day_dates[0][1]))
        parsed.end_date = _build_iso((today.year, month_day_dates[1][0], month_day_dates[1][1]))
        return
    if len(month_day_dates) == 1 and not parsed.start_date:
        parsed.start_date = _build_iso((today.year, month_day_dates[0][0], month_day_dates[0][1]))

    relative = _resolve_relative_window(text, today)
    if relative:
        parsed.start_date = relative[0].isoformat()
        parsed.end_date = relative[1].isoformat()


def _resolve_relative_window(text: str, today: date) -> tuple[date, date] | None:
    next_saturday = _next_weekday(today, 5)
    if "这周末" in text or "本周末" in text:
        current_saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        return current_saturday, current_saturday + timedelta(days=1)
    if "下下周末" in text:
        start = next_saturday + timedelta(days=7)
        return start, start + timedelta(days=1)
    if "下周末" in text:
        return next_saturday, next_saturday + timedelta(days=1)
    return None


def _fill_duration(parsed: ParsedRequest, text: str) -> None:
    match = re.search(r"(\d+)\s*天\s*(\d+)\s*晚", text)
    if match:
        parsed.days = int(match.group(1))
        parsed.nights = int(match.group(2))
        parsed.target_trip_days = parsed.days
        parsed.target_trip_nights = parsed.nights
        return

    match = re.search(r"([一二两三四五六七八九十\d]+)\s*天\s*([一二两三四五六七八九十\d]+)\s*晚", text)
    if match:
        parsed.days = _to_int(match.group(1))
        parsed.nights = _to_int(match.group(2))
        parsed.target_trip_days = parsed.days
        parsed.target_trip_nights = parsed.nights
        return

    match = re.search(r"(\d+)\s*天", text)
    if match:
        parsed.days = int(match.group(1))
        parsed.nights = max(parsed.days - 1, 0)
        parsed.target_trip_days = parsed.days
        parsed.target_trip_nights = parsed.nights
        return

    match = re.search(r"([一二两三四五六七八九十]+)\s*天", text)
    if match:
        parsed.days = _to_int(match.group(1))
        parsed.nights = max(parsed.days - 1, 0)
        parsed.target_trip_days = parsed.days
        parsed.target_trip_nights = parsed.nights


def _fill_budget(parsed: ParsedRequest, text: str) -> None:
    match = re.search(r"(?:预算|人均预算|预算人均)\s*(\d{3,6})", text)
    if match:
        parsed.budget_per_person = float(match.group(1))


def _fill_travelers(parsed: ParsedRequest, text: str) -> None:
    match = re.search(r"(\d+)\s*人", text)
    if match:
        parsed.traveler_count = int(match.group(1))
        return
    match = re.search(r"([一二两三四五六七八九十]+)\s*人", text)
    if match:
        parsed.traveler_count = _to_int(match.group(1))


def _fill_style(parsed: ParsedRequest, text: str) -> None:
    if any(token in text for token in ["轻松", "松弛", "悠闲", "慢慢逛", "不要太赶"]):
        parsed.travel_style = "relaxed"
        return
    if any(token in text for token in ["特种兵", "赶一点", "尽量多玩", "多去几个"]):
        parsed.travel_style = "packed"
        return
    parsed.travel_style = "balanced"


def _fill_mode_and_price_priority(parsed: ParsedRequest, text: str, today: date) -> None:
    if any(token in text for token in ["低价", "便宜", "最便宜", "价格低", "什么时候便宜"]):
        parsed.price_priority = "low"

    month_window = re.search(r"(?:未来|最近)\s*([一二两三四五六七八九十\d]+)\s*个月(?:内)?", text)
    if month_window:
        months = _to_int(month_window.group(1))
        window_days = max(months * 30, 30)
        parsed.request_mode = "price_scan"
        parsed.flexible_window_start = today.isoformat()
        parsed.flexible_window_end = (today + timedelta(days=window_days)).isoformat()
        return

    day_window = re.search(r"(?:未来|最近)\s*([一二两三四五六七八九十\d]+)\s*天(?:内)?", text)
    if day_window:
        window_days = _to_int(day_window.group(1))
        parsed.request_mode = "price_scan"
        parsed.flexible_window_start = today.isoformat()
        parsed.flexible_window_end = (today + timedelta(days=max(window_days, 7))).isoformat()


def _fill_must_go(parsed: ParsedRequest, text: str) -> None:
    match = re.search(r"(?:想去|必去|一定要去|想玩)([^.;]+)", text)
    if not match:
        return
    chunk = match.group(1)
    chunk = re.split(r"(?:预算|酒店|住宿|机票|高铁|火车)", chunk)[0]
    points = [item.strip() for item in re.split(r"[,、和及]", chunk) if item.strip()]
    parsed.must_go = [item for item in points if len(item) <= 20]


def _fill_transport_preferences(parsed: ParsedRequest, text: str) -> None:
    values: list[str] = []
    mapping = [
        ("高铁", "铁路"),
        ("火车", "铁路"),
        ("铁路", "铁路"),
        ("地铁", "地铁"),
        ("步行", "步行"),
        ("打车", "打车"),
        ("自驾", "自驾"),
        ("飞机", "飞机"),
    ]
    for token, value in mapping:
        if token in text and value not in values:
            values.append(value)
    parsed.transport_preferences = values


def _fill_hotel_preferences(parsed: ParsedRequest, text: str) -> None:
    values: list[str] = []
    mapping = [
        ("干净", "干净"),
        ("卫生", "干净"),
        ("安静", "安静"),
        ("近地铁", "近地铁"),
        ("市中心", "市中心"),
        ("方便", "交通方便"),
    ]
    for token, value in mapping:
        if token in text and value not in values:
            values.append(value)
    parsed.hotel_preferences = values


def _backfill_dates_from_duration(parsed: ParsedRequest, today: date) -> None:
    days = _extract_days(parsed) or parsed.target_trip_days
    if not days:
        return

    if parsed.request_mode == "price_scan" and parsed.flexible_window_start and not parsed.start_date:
        start = date.fromisoformat(parsed.flexible_window_start)
        parsed.start_date = start.isoformat()
        parsed.end_date = (start + timedelta(days=days - 1)).isoformat()
        return

    if parsed.start_date and not parsed.end_date:
        start = date.fromisoformat(parsed.start_date)
        parsed.end_date = (start + timedelta(days=days - 1)).isoformat()
    elif not parsed.start_date and not parsed.end_date:
        start = today + timedelta(days=7)
        parsed.start_date = start.isoformat()
        parsed.end_date = (start + timedelta(days=days - 1)).isoformat()


def _extract_days(parsed: ParsedRequest) -> int | None:
    if parsed.start_date and parsed.end_date:
        return (date.fromisoformat(parsed.end_date) - date.fromisoformat(parsed.start_date)).days + 1
    return parsed.days


def _build_iso(parts) -> str:
    year, month, day = [int(part) for part in parts]
    return date(year, month, day).isoformat()


def _next_weekday(today: date, weekday: int) -> date:
    delta = (weekday - today.weekday()) % 7
    delta = 7 if delta == 0 else delta
    return today + timedelta(days=delta)


def _to_int(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if len(raw) == 2 and raw.startswith("十"):
        return 10 + CHINESE_NUMERALS.get(raw[1], 0)
    if len(raw) == 2 and raw.endswith("十"):
        return CHINESE_NUMERALS.get(raw[0], 0) * 10
    if len(raw) == 3 and raw[1] == "十":
        return CHINESE_NUMERALS.get(raw[0], 0) * 10 + CHINESE_NUMERALS.get(raw[2], 0)
    return CHINESE_NUMERALS.get(raw, 0)
