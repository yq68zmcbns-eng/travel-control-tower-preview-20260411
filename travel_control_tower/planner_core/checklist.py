from __future__ import annotations

from urllib.parse import quote

from .city_profiles import hotel_area_hint_for
from .models import BookingItem, TripRequest


def _hotel_search_url(request: TripRequest) -> str:
    city = quote(request.destination)
    return f"https://hotels.ctrip.com/hotels/list?cityname={city}&checkin={request.start_date}&checkout={request.end_date}"


def _transport_search_url(request: TripRequest) -> str:
    prefs = [pref.lower() for pref in request.transport_preferences]
    if any(token in prefs for token in ["铁路", "高铁", "火车", "rail", "train"]):
        return "https://www.12306.cn/index/"
    return "https://www.fliggy.com/"


def _sight_search_url(request: TripRequest) -> str:
    query = " ".join(request.must_go[:2]).strip() or request.destination
    return f"https://you.ctrip.com/searchsite/Sight?query={quote(query)}"


def build_booking_checklist(request: TripRequest) -> list[BookingItem]:
    hotel_hint = hotel_area_hint_for(request.destination)
    hotel_note = "优先选择靠近核心交通节点或主要活动片区的酒店。"
    if hotel_hint:
        hotel_note = f"优先选择靠近核心交通节点或主要活动片区的酒店。{hotel_hint}"

    items = [
        BookingItem(
            name="主交通",
            category="交通",
            url=_transport_search_url(request),
            priority="required",
            timing="优先锁定",
            notes="先锁定去程和回程的大交通，避免后面整套路线失去基础。",
            why_now="大交通通常是最先波动、最容易影响整套时间线的部分。",
            risk_if_wait="拖晚后最常见的问题是价格上涨，或者只剩不合适的时段。",
        ),
        BookingItem(
            name="主酒店",
            category="住宿",
            url=_hotel_search_url(request),
            priority="required",
            timing="出发前 1-2 周",
            notes=hotel_note,
            why_now="酒店会影响每天第一段和最后一段移动效率，也会直接影响预算。",
            risk_if_wait="拖晚后常见问题是核心区域无房、价格上涨，或者只能住在不顺路的位置。",
        ),
    ]

    if request.must_go:
        must_go_text = " / ".join(request.must_go[:3])
        items.append(
            BookingItem(
                name=f"重点景点预约：{must_go_text}",
                category="门票",
                url=_sight_search_url(request),
                priority="recommended",
                timing="路线确定后",
                notes=f"当前必去点包括 {must_go_text}。只优先处理容易售罄、需要分时段预约，或会反过来卡住当天节奏的景点。",
                why_now="这一步不一定最早做，但一旦主路线稳定后就要尽快补上。",
                risk_if_wait="热门景点可能没有理想时段，只能改动当天路线。",
            )
        )

    if "car" in [pref.lower() for pref in request.transport_preferences]:
        items.append(
            BookingItem(
                name="包车或一日交通",
                category="交通",
                priority="recommended",
                timing="主路线稳定后",
                notes="等每天主路线和主要停留点确定后，再决定是否下单包车或一日交通。",
                why_now="这类服务和路线强绑定，太早订容易造成浪费。",
                risk_if_wait="旺季可能临时约不到顺路的车，或价格明显上涨。",
            )
        )

    return items
