from __future__ import annotations

from ..adapters.base import HotelCandidate, TransportCandidate
from .models import BudgetLineItem, BudgetSummary, TripRequest


OTHER_WEIGHTS = {
    "餐饮": 17 / 37,
    "门票": 10 / 37,
    "市内交通": 5 / 37,
    "机动": 5 / 37,
}


def build_budget_summary(
    request: TripRequest,
    selected_hotel: HotelCandidate | None = None,
    selected_transport: TransportCandidate | None = None,
) -> BudgetSummary:
    total = float(request.budget_total or 0)
    per_person = float(request.budget_per_person or 0)

    if total <= 0 and per_person > 0:
        total = per_person * request.traveler_count

    if per_person <= 0 and total > 0:
        per_person = total / request.traveler_count

    hotel_total = round((selected_hotel.nightly_price or 0.0) * float(request.nights or 0), 2) if selected_hotel else None
    transport_total = round(float(selected_transport.total_price or 0.0), 2) if selected_transport else None

    if transport_total is None:
        transport_total = round(total * 0.35, 2) if total > 0 else 0.0
        transport_note = "当前没有主交通候选时，先按预算比例估算。"
    else:
        transport_note = "已按当前主交通候选回填。"

    if hotel_total is None:
        hotel_total = round(total * 0.28, 2) if total > 0 else 0.0
        hotel_note = "当前没有主酒店候选时，先按预算比例估算。"
    else:
        hotel_note = "已按当前主酒店候选回填。"

    fixed_known = round(transport_total + hotel_total, 2)
    total = max(total, fixed_known)
    remaining = max(total - fixed_known, 0.0)

    dining_total = round(remaining * OTHER_WEIGHTS["餐饮"], 2)
    ticket_total = round(remaining * OTHER_WEIGHTS["门票"], 2)
    local_total = round(remaining * OTHER_WEIGHTS["市内交通"], 2)
    buffer_total = round(max(total - fixed_known - dining_total - ticket_total - local_total, 0.0), 2)

    line_items = [
        BudgetLineItem(
            category="长途交通",
            total=transport_total,
            per_person=round(transport_total / request.traveler_count, 2),
            notes=transport_note,
        ),
        BudgetLineItem(
            category="酒店住宿",
            total=hotel_total,
            per_person=round(hotel_total / request.traveler_count, 2),
            notes=hotel_note,
        ),
        BudgetLineItem(
            category="餐饮",
            total=dining_total,
            per_person=round(dining_total / request.traveler_count, 2),
            notes="用于三餐、咖啡和简单补给。",
        ),
        BudgetLineItem(
            category="门票",
            total=ticket_total,
            per_person=round(ticket_total / request.traveler_count, 2),
            notes="用于景点门票或上塔、展馆类付费项目。",
        ),
        BudgetLineItem(
            category="市内交通",
            total=local_total,
            per_person=round(local_total / request.traveler_count, 2),
            notes="用于地铁、打车和市内短驳。",
        ),
        BudgetLineItem(
            category="机动",
            total=buffer_total,
            per_person=round(buffer_total / request.traveler_count, 2),
            notes="用于价格波动、临时加项或现场调整。",
        ),
    ]

    return BudgetSummary(
        fixed_cost_total=round(total, 2),
        per_person_cost=round(total / request.traveler_count, 2),
        optional_upgrade_total=0.0,
        breakdown=line_items,
    )
