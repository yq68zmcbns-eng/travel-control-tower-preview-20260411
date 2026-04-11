from __future__ import annotations

from datetime import date, timedelta

from ..adapters.base import HotelCandidate, TransportCandidate
from .models import DailyPlan, DayItem, TripRequest


OSAKA_WEEKEND_SCENARIO = "japan_osaka_weekend"


def build_scenario_daily_plan(request: TripRequest) -> list[DailyPlan] | None:
    if request.scenario_id != OSAKA_WEEKEND_SCENARIO:
        return None

    start = date.fromisoformat(request.start_date)

    return [
        DailyPlan(
            day_index=1,
            date=start.isoformat(),
            theme="心斋桥与道顿堀",
            items=[
                DayItem("到达交通", "交通", duration_minutes=75, notes="从关西机场进市区，先到酒店放行李。"),
                DayItem(
                    "酒店入住或寄存行李",
                    "住宿",
                    duration_minutes=30,
                    notes="这段时间和机场进城分开计算，避免把路上时间和入住时间混在一起。",
                ),
                DayItem(
                    "心斋桥商圈慢逛",
                    "游玩",
                    duration_minutes=150,
                    notes="第一天只放轻松商圈，不安排重景点，给到达日留余量。",
                ),
                DayItem(
                    "前往道顿堀片区",
                    "交通",
                    duration_minutes=15,
                    notes="酒店到道顿堀这段适合步行，边走边熟悉心斋桥和难波片区。",
                ),
                DayItem(
                    "道顿堀与戎桥夜景",
                    "游玩",
                    duration_minutes=150,
                    notes="重点看道顿堀运河、格力高招牌、戎桥一带夜景。",
                ),
                DayItem("道顿堀晚饭", "餐食", duration_minutes=80, notes="第一晚直接放在道顿堀解决，避免折返。"),
            ],
            why_this_day="到达日不做大跨区移动，直接收在心斋桥到道顿堀一条线上，节奏最稳。",
            transport_strategy="机场进城用一段主交通，市内剩余部分尽量用步行解决。",
            meal_strategy="午后以轻食和咖啡为主，晚饭放在道顿堀。",
            fallback_if_fast="如果进城更快，可以把美国村一起补进去。",
            fallback_if_tired="如果状态一般，心斋桥商圈可直接压缩，只保留道顿堀。",
        ),
        DailyPlan(
            day_index=2,
            date=(start + timedelta(days=1)).isoformat(),
            theme="大阪城与梅田",
            items=[
                DayItem("前往大阪城", "交通", duration_minutes=35, notes="上午先去大阪城，避开中午以后的人流。"),
                DayItem(
                    "大阪城公园与天守阁",
                    "游玩",
                    duration_minutes=150,
                    notes="先看外圈石墙和护城河，再进天守阁，最后回公园收尾。",
                ),
                DayItem(
                    "JO-TERRACE 午餐",
                    "餐食",
                    duration_minutes=60,
                    notes="午饭放在大阪城公园外圈，下午转梅田最顺。",
                ),
                DayItem(
                    "前往梅田蓝天大厦",
                    "交通",
                    duration_minutes=30,
                    notes="从大阪城片区去梅田，适合地铁或 JR 组合。",
                ),
                DayItem(
                    "梅田蓝天大厦与梅田商圈",
                    "游玩",
                    duration_minutes=180,
                    notes="上展望台后，剩余时间放在梅田商圈内活动。",
                ),
                DayItem("梅田晚饭", "餐食", duration_minutes=80, notes="这顿直接留在梅田，晚上不用再换区。"),
            ],
            why_this_day="第二天做一条经典城市线：上午历史地标，下午现代商圈，移动逻辑清楚。",
            transport_strategy="全天只用两个片区，大阪城和梅田，不来回折返。",
            meal_strategy="午饭放在大阪城外圈，晚饭放在梅田。",
            fallback_if_fast="如果大阪城结束得快，可以把中之岛补进去。",
            fallback_if_tired="如果下午状态一般，只保留蓝天大厦和晚饭，不再补逛梅田外围。",
        ),
        DailyPlan(
            day_index=3,
            date=(start + timedelta(days=2)).isoformat(),
            theme="黑门市场与返程",
            items=[
                DayItem(
                    "退房与行李处理",
                    "住宿",
                    duration_minutes=40,
                    notes="先退房寄存，避免拖着行李去黑门市场。",
                ),
                DayItem("前往黑门市场", "交通", duration_minutes=25, notes="这段适合步行前往，路程不长。"),
                DayItem(
                    "黑门市场早午餐",
                    "餐食",
                    duration_minutes=80,
                    notes="早上市场更适合吃海鲜、寿司和小份补给。",
                ),
                DayItem(
                    "步行前往难波八阪神社",
                    "交通",
                    duration_minutes=20,
                    notes="黑门到神社可以直接步行串起来。",
                ),
                DayItem(
                    "难波八阪神社与难波补逛",
                    "游玩",
                    duration_minutes=140,
                    notes="难波八阪神社本体停留不长，剩余时间放在难波片区补逛。",
                ),
                DayItem(
                    "返程前最后补给",
                    "餐食",
                    duration_minutes=60,
                    notes="返程前在难波或心斋桥补最后一顿，不专门绕路。",
                ),
                DayItem(
                    "返程交通",
                    "交通",
                    duration_minutes=75,
                    notes="从酒店取回行李后前往关西机场，返程段按保守余量处理。",
                ),
            ],
            why_this_day="最后一天把活动压在难波周边，方便收尾和去机场。",
            transport_strategy="白天以步行为主，返程回酒店取行李后再去机场。",
            meal_strategy="早午餐放黑门市场，返程前再补一顿轻松收尾。",
            fallback_if_fast="如果上午进度快，可以补法善寺横丁或千日前一带。",
            fallback_if_tired="如果不想再逛，黑门市场结束后直接回酒店拿行李去机场。",
        ),
    ]


def get_scenario_route_specs(request: TripRequest) -> dict[tuple[int, str], dict]:
    if request.scenario_id != OSAKA_WEEKEND_SCENARIO:
        return {}

    return {
        (1, "到达交通"): {
            "manual_summary": "这段默认按机场摆渡 + 南海电铁或关空快速进难波理解，铁路主段约 40 到 45 分钟，再加 15 到 20 分钟换乘和步行到酒店。",
            "duration_minutes": 75,
            "buffer_minutes": 30,
            "buffer_reason": "用于入境、取行李、机场航站楼换乘和站内找路。",
        },
        (1, "前往道顿堀片区"): {
            "origin": "KOKO HOTEL Osaka Shinsaibashi",
            "destination": "Dotonbori",
            "mode": "WALK",
            "duration_minutes": 15,
            "buffer_minutes": 10,
            "buffer_reason": "用于商圈内找路、等电梯或途中停留。",
        },
        (2, "前往大阪城"): {
            "origin": "KOKO HOTEL Osaka Shinsaibashi",
            "destination": "Osaka Castle Main Tower",
            "mode": "TRANSIT",
            "departure_time": "08:45",
            "timezone": "Asia/Tokyo",
            "duration_minutes": 35,
            "buffer_minutes": 15,
            "buffer_reason": "用于站内换乘、出站后步行和找入口。",
        },
        (2, "前往梅田蓝天大厦"): {
            "origin": "Osaka Business Park Station",
            "destination": "Osaka Station",
            "mode": "TRANSIT",
            "departure_time": "13:20",
            "timezone": "Asia/Tokyo",
            "duration_minutes": 30,
            "buffer_minutes": 15,
            "buffer_reason": "用于出园区、等车和梅田站内步行。",
            "post_route_note": "到大阪站后，再步行约 10 分钟到梅田蓝天大厦。",
        },
        (3, "前往黑门市场"): {
            "origin": "KOKO HOTEL Osaka Shinsaibashi",
            "destination": "Kuromon Ichiba Market",
            "mode": "WALK",
            "duration_minutes": 25,
            "buffer_minutes": 10,
            "buffer_reason": "用于步行误差和途中临时停留。",
        },
        (3, "步行前往难波八阪神社"): {
            "origin": "Kuromon Ichiba Market",
            "destination": "Namba Yasaka Shrine",
            "mode": "WALK",
            "duration_minutes": 20,
            "buffer_minutes": 10,
            "buffer_reason": "用于市场内穿行和路口等待。",
        },
        (3, "返程交通"): {
            "manual_summary": "返程默认按酒店回难波，再坐南海或关空快速去机场理解，主铁路段约 40 到 45 分钟，另加酒店取行李和机场内移动时间。",
            "duration_minutes": 75,
            "buffer_minutes": 30,
            "buffer_reason": "用于酒店取行李、进站、机场内移动和安检前置余量。",
        },
    }


def get_scenario_search_specs(request: TripRequest) -> dict:
    if request.scenario_id != OSAKA_WEEKEND_SCENARIO:
        return {}

    return {
        "hotel": {
            "destination": "大阪",
            "keyword": "KOKO HOTEL 大阪心斋桥",
            "max_price": 1000,
            "fallback_query": f"{request.start_date}到{request.end_date} 大阪 心斋桥 干净 地铁方便 酒店",
        },
        "transport": {
            "departure_city": request.departure_city,
            "destination": request.destination,
            "start_date": request.start_date,
            "end_date": request.end_date,
        },
    }


def get_scenario_search_fallbacks(
    request: TripRequest,
) -> tuple[list[HotelCandidate], list[TransportCandidate]]:
    if request.scenario_id != OSAKA_WEEKEND_SCENARIO:
        return [], []

    hotel_candidates = [
        HotelCandidate(
            name="KOKO HOTEL 大阪心斋桥",
            nightly_price=702.0,
            area="近心斋桥",
            notes="高档型；装修：2022；地址：3 Chome-3-17 Minamisenba。",
            booking_url="https://a.feizhu.com/0GrJdY",
            provider="flyai_snapshot",
        ),
        HotelCandidate(
            name="大阪心斋桥舒适酒店",
            nightly_price=405.0,
            area="近心斋桥",
            notes="舒适型；装修：2016；地址：1-15-15, Higashishinsaibashi。",
            booking_url="https://a.feizhu.com/3YZrSy",
            provider="flyai_snapshot",
        ),
    ]
    transport_candidates = [
        TransportCandidate(
            label="乐桃 MM080 去程 / 乐桃 MM079 回程（2026-06-09 06:15 - 2026-06-12 00:05）",
            category="往返机票",
            total_price=1561.0,
            depart_at="2026-06-09 06:15:00",
            arrive_at="2026-06-12 00:05:00",
            booking_url="https://a.feizhu.com/29yXyu",
            provider="flyai_snapshot",
        )
    ]
    return hotel_candidates, transport_candidates
