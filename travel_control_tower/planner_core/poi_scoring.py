from __future__ import annotations

import re

SCENIC_TOKENS = (
    "风景名胜区",
    "景区",
    "古街",
    "老街",
    "历史街区",
    "历史文化",
    "步行街",
    "夜市",
    "商圈",
    "古城",
    "城墙",
    "故宫",
    "宫",
    "院",
    "寺",
    "神社",
    "天守阁",
    "塔",
    "博物馆",
    "museum",
    "展馆",
    "地标",
    "河畔街",
    "夫子庙",
    "钟楼",
    "天安门",
    "西湖",
    "玄武湖",
    "什刹海",
    "宽窄巷子",
    "春熙路",
    "解放碑",
    "回民街",
    "前门",
    "王府井",
)

HIGH_VALUE_PARK_TOKENS = (
    "风景名胜区",
    "国家公园",
    "湿地公园",
    "森林公园",
    "遗址公园",
    "地质公园",
    "国家级",
)

GENERIC_URBAN_TOKENS = (
    "公园",
    "城市阳台",
    "世纪公园",
    "市民广场",
    "绿地",
    "滨江",
    "江滩",
    "河滨",
)

ARRIVAL_FRIENDLY_TOKENS = (
    "步行街",
    "商圈",
    "老街",
    "古街",
    "夜市",
    "街区",
    "秦淮",
    "夫子庙",
    "前门",
    "浅草",
    "银座",
    "心斋桥",
    "道顿堀",
)

HISTORIC_COMMERCE_TOKENS = (
    "步行街",
    "历史街区",
    "历史文化街区",
    "老街",
    "古街",
    "古镇",
    "商圈",
    "夜市",
    "水街",
)

ICONIC_COMMERCE_TOKENS = (
    "夫子庙",
    "老门东",
    "新街口",
    "清河坊",
    "湖滨",
    "宽窄巷子",
    "春熙路",
    "锦里",
    "解放碑",
    "观音桥",
    "回民街",
    "书院门",
    "前门",
    "王府井",
    "什刹海",
    "大兜路",
    "颐和路",
    "五柳巷",
)

GENERIC_COMMERCIAL_TOKENS = (
    "步行街",
    "商业街",
    "商业广场",
    "商业区",
    "商圈",
    "好吃街",
    "水街",
)

PHOTO_SPOT_TOKENS = (
    "打卡点",
    "拍照点",
    "观景平台",
    "机位",
    "夜景打卡",
    "观景点",
)

BUSINESS_BUILDING_TOKENS = (
    "商务住宅",
    "商务写字楼",
    "写字楼",
    "楼宇",
    "产业园",
    "项目部",
    "售楼处",
    "商业办公",
)

ROUTE_MINUTES_RE = re.compile(r"约?(\d+)分钟车程")


def normalize_text(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def contains_any(value: str, tokens: tuple[str, ...] | list[str]) -> bool:
    haystack = normalize_text(value)
    return any(normalize_text(token) in haystack for token in tokens if token)


def poi_text_blob(
    name: str = "",
    category: str = "",
    level: str = "",
    notes: str = "",
    address: str = "",
) -> str:
    return " ".join([name or "", category or "", level or "", notes or "", address or ""]).strip()


def is_generic_urban_park(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if contains_any(normalized, HIGH_VALUE_PARK_TOKENS):
        return False
    return contains_any(normalized, GENERIC_URBAN_TOKENS)


def is_photo_spot(text: str) -> bool:
    return contains_any(text, PHOTO_SPOT_TOKENS)


def is_business_building(text: str) -> bool:
    return contains_any(text, BUSINESS_BUILDING_TOKENS)


def is_generic_commercial_street(text: str) -> bool:
    if not contains_any(text, GENERIC_COMMERCIAL_TOKENS):
        return False
    if contains_any(text, ICONIC_COMMERCE_TOKENS):
        return False
    if contains_any(text, ("历史街区", "历史文化街区", "古街", "老街", "古镇")):
        return False
    return True


def extract_route_minutes(*texts: str) -> int | None:
    for text in texts:
        match = ROUTE_MINUTES_RE.search(str(text or ""))
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def poi_quality_score(
    name: str = "",
    category: str = "",
    level: str = "",
    notes: str = "",
    address: str = "",
) -> int:
    text = poi_text_blob(name, category, level, notes, address)
    score = 0
    if contains_any(text, SCENIC_TOKENS):
        score += 24
    if contains_any(text, HISTORIC_COMMERCE_TOKENS):
        score += 16
    if contains_any(text, ("博物馆", "museum", "展馆")):
        score += 12
    if contains_any(text, ("步行街", "老街", "古街", "夜市", "商圈")):
        score += 10
    if contains_any(text, ("故宫", "宫", "院", "城墙", "古城", "寺", "神社", "塔")):
        score += 10
    if contains_any(text, ICONIC_COMMERCE_TOKENS):
        score += 12
    if level:
        score += 10
        upper = (level or "").upper()
        if "5A" in upper or "AAAAA" in upper:
            score += 12
    if is_generic_urban_park(text):
        score -= 20
    if is_generic_commercial_street(text):
        score -= 24
    if is_photo_spot(text):
        score -= 24
    if is_business_building(text):
        score -= 40
    return score


def arrival_friendliness_score(
    name: str = "",
    category: str = "",
    level: str = "",
    notes: str = "",
    address: str = "",
) -> int:
    text = poi_text_blob(name, category, level, notes, address)
    score = 0
    if contains_any(text, ARRIVAL_FRIENDLY_TOKENS):
        score += 36
    if is_generic_urban_park(text):
        score -= 12
    return score
