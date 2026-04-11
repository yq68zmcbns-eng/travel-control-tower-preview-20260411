from __future__ import annotations

from .models import BudgetSummary, DailyPlan


def apply_daily_cost_estimates(daily_plan: list[DailyPlan], budget: BudgetSummary) -> list[DailyPlan]:
    if not daily_plan:
        return daily_plan

    breakdown = {item.category: float(item.total or 0) for item in budget.breakdown}
    dining_total = breakdown.get("餐饮", 0.0)
    ticket_total = breakdown.get("门票", 0.0)
    local_total = breakdown.get("市内交通", 0.0)
    buffer_total = breakdown.get("机动", 0.0)

    meal_weights = [sum(1 for item in day.items if item.category == "餐饮") for day in daily_plan]
    play_weights = [sum(1 for item in day.items if item.category == "游玩") for day in daily_plan]
    move_weights = [sum(1 for item in day.items if item.category == "交通") for day in daily_plan]

    total_meal_weight = sum(meal_weights) or len(daily_plan)
    total_play_weight = sum(play_weights) or len(daily_plan)
    total_move_weight = sum(move_weights) or len(daily_plan)

    for index, day in enumerate(daily_plan):
        meal_share = dining_total * ((meal_weights[index] or 1) / total_meal_weight)
        ticket_share = ticket_total * ((play_weights[index] or 1) / total_play_weight)
        local_share = local_total * ((move_weights[index] or 1) / total_move_weight)
        buffer_share = buffer_total / len(daily_plan)
        total = round(meal_share + ticket_share + local_share + buffer_share, 2)

        notes = []
        if meal_share:
            notes.append(f"餐饮约 {round(meal_share)}")
        if ticket_share:
            notes.append(f"门票约 {round(ticket_share)}")
        if local_share:
            notes.append(f"市内交通约 {round(local_share)}")
        if buffer_share:
            notes.append(f"机动约 {round(buffer_share)}")

        day.estimated_cost_total = total
        day.estimated_cost_notes = "；".join(notes)

    return daily_plan
