from __future__ import annotations

from .budget import build_budget_summary
from .candidate_refinement import refine_candidates_with_routes
from .checklist import build_booking_checklist
from .daily_costs import apply_daily_cost_estimates
from .models import POICandidate, ProviderStatus, TripPlan, TripRequest
from .normalizer import normalize_request
from .planning_agent import PlanningContext, plan_daily_itinerary
from .poi_enrichment import select_poi_candidates
from .price_scan import resolve_price_scan_request
from .route_enrichment import enrich_daily_plan_with_route_placeholders
from .schedule_enrichment import apply_schedule_blocks, build_schedule_overrides
from .scenarios import OSAKA_WEEKEND_SCENARIO
from .search_enrichment import merge_booking_items, select_search_results


def build_plan_stub(
    request: TripRequest,
    route_adapter=None,
    search_adapter=None,
    planning_agent=None,
) -> TripPlan:
    normalized_request = normalize_request(request)

    effective_request = normalized_request
    price_scan_candidates = []
    price_scan_summary = None
    price_scan_error = ""
    if normalized_request.request_mode == "price_scan":
        effective_request, price_scan_candidates, price_scan_summary, price_scan_error = resolve_price_scan_request(
            normalized_request,
            search_adapter=search_adapter,
            max_samples=6,
        )

    poi_candidates = select_poi_candidates(
        effective_request,
        search_adapter=search_adapter,
        fallback_search_adapter=route_adapter,
    )
    selected_hotel, hotel_candidates, selected_transport, transport_candidates = select_search_results(
        effective_request,
        search_adapter=search_adapter,
        route_adapter=route_adapter,
        preselected_transport_candidates=price_scan_candidates,
        poi_candidates=poi_candidates,
    )
    hotel_candidates, poi_candidates = refine_candidates_with_routes(
        effective_request,
        hotel_candidates,
        poi_candidates,
        route_adapter=route_adapter,
    )
    selected_hotel = hotel_candidates[0] if hotel_candidates else selected_hotel
    search_error = str(getattr(search_adapter, "last_error", "") or "").strip()

    planning_context = PlanningContext(
        request=effective_request,
        selected_hotel=selected_hotel,
        selected_transport=selected_transport,
        hotel_candidates=hotel_candidates,
        transport_candidates=transport_candidates,
        poi_candidates=poi_candidates,
    )
    daily_plan, planning_trace = plan_daily_itinerary(planning_context, planning_agent=planning_agent)

    daily_plan = enrich_daily_plan_with_route_placeholders(
        effective_request,
        daily_plan,
        route_adapter=route_adapter,
        selected_hotel=selected_hotel,
    )

    day_start_times, day_end_times = build_schedule_overrides(
        effective_request,
        daily_plan,
        selected_transport=selected_transport,
    )
    if effective_request.scenario_id == OSAKA_WEEKEND_SCENARIO:
        for index, value in {1: "11:00", 2: "08:30", 3: "08:30"}.items():
            day_start_times.setdefault(index, value)

    daily_plan = apply_schedule_blocks(
        daily_plan,
        day_start_times=day_start_times or None,
        day_end_times=day_end_times or None,
    )

    budget = build_budget_summary(
        effective_request,
        selected_hotel=selected_hotel,
        selected_transport=selected_transport,
    )
    daily_plan = apply_daily_cost_estimates(daily_plan, budget)

    booking_items = build_booking_checklist(effective_request)
    booking_items = merge_booking_items(
        effective_request,
        booking_items,
        selected_hotel,
        selected_transport,
        daily_plan=daily_plan,
    )

    provider_statuses = _build_provider_statuses(
        request=effective_request,
        route_adapter=route_adapter,
        search_adapter=search_adapter,
        search_error=search_error,
        selected_hotel=selected_hotel,
        selected_transport=selected_transport,
        booking_items=booking_items,
        daily_plan=daily_plan,
        poi_candidates=poi_candidates,
        price_scan_summary=price_scan_summary,
        price_scan_error=price_scan_error,
        planning_trace=planning_trace,
    )

    return TripPlan(
        status="draft",
        overview_title=_build_title(effective_request),
        overview_summary=_build_summary(
            effective_request,
            selected_hotel=selected_hotel,
            selected_transport=selected_transport,
            poi_candidates=poi_candidates,
            price_scan_summary=price_scan_summary,
            price_scan_error=price_scan_error,
            search_error=search_error,
            planning_trace=planning_trace,
        ),
        input_snapshot=_build_input_snapshot(effective_request, price_scan_summary, planning_trace),
        assumptions=_build_assumptions(
            effective_request,
            route_adapter=route_adapter,
            search_error=search_error,
            poi_candidates=poi_candidates,
            price_scan_summary=price_scan_summary,
            price_scan_error=price_scan_error,
            selected_hotel=selected_hotel,
            selected_transport=selected_transport,
            planning_trace=planning_trace,
        ),
        daily_plan=daily_plan,
        budget=budget,
        booking_items=booking_items,
        planning_trace=planning_trace,
        provider_statuses=provider_statuses,
        selected_hotel=selected_hotel,
        selected_transport=selected_transport,
        hotel_candidates=hotel_candidates,
        transport_candidates=transport_candidates,
        poi_candidates=poi_candidates,
        price_scan_summary=price_scan_summary,
        price_scan_candidates=price_scan_candidates,
        open_questions=_build_open_questions(
            effective_request,
            selected_hotel=selected_hotel,
            selected_transport=selected_transport,
            poi_candidates=poi_candidates,
            price_scan_summary=price_scan_summary,
            planning_trace=planning_trace,
        ),
    )


def _build_title(request: TripRequest) -> str:
    departure = str(request.departure_city or "").strip() or "待定出发地"
    destination = str(request.destination or "").strip() or "待定目的地"
    return f"{departure} 出发，前往 {destination} 的旅行方案"


def _build_summary(
    request: TripRequest,
    selected_hotel,
    selected_transport,
    poi_candidates: list[POICandidate],
    price_scan_summary: dict | None,
    price_scan_error: str,
    search_error: str,
    planning_trace,
) -> str:
    search_error = _friendly_search_issue(search_error)
    price_scan_error = _friendly_search_issue(price_scan_error)
    if request.request_mode == "price_scan":
        if price_scan_summary:
            return (
                f"已在 {price_scan_summary['window_start']} 到 {price_scan_summary['window_end']} 的时间窗口里做了首轮比价，"
                f"当前先推荐 {price_scan_summary['chosen_start_date']} 到 {price_scan_summary['chosen_end_date']} 这组"
                f"{price_scan_summary['trip_days']} 天 {price_scan_summary['trip_nights']} 晚行程，"
                f"主交通候选约 {price_scan_summary['chosen_price']:.0f} 元。"
            )
        return (
            f"当前识别为 {request.flexible_window_start} 到 {request.flexible_window_end} 的时间窗口比价需求。"
            f"目标是找出 {request.target_trip_days or request.days} 天 {request.target_trip_nights or request.nights} 晚里更划算的一组。"
            f" {price_scan_error or '这轮先用现有候选给出首版方案，后续还可以继续扩样本。'}"
        )

    parts = [f"共 {request.days} 天 {request.nights} 晚，{request.traveler_count} 人出行。"]
    if planning_trace and planning_trace.mode == "llm":
        parts.append("这版方案由智能规划生成，优先参考实时搜到的交通、酒店和景点；没有拿到实时结果的环节，会先用保守安排补齐。")
    elif planning_trace and planning_trace.mode == "candidate":
        parts.append("这版方案优先根据实时搜到的交通、酒店和景点生成；如果某一项暂时没拿到实时结果，会先用保守安排补齐。")
    else:
        parts.append("这版方案先按基础规则生成，方便先确认节奏、预算和主线路，后续再继续替换成更实的数据。")
    if selected_transport and selected_transport.provider == "user_input":
        parts.append("主交通已经按你提供的信息带入，当前重点是核对时间、路线和预算是否能顺下来。")
    elif selected_transport:
        parts.append("主交通已经先锁了一版候选，基本可以直接判断到达日和返程日是否顺。")
    if selected_hotel and selected_hotel.provider == "user_input":
        parts.append("住宿已经按你提供的信息带入，系统会围绕这家酒店安排每天第一段和最后一段移动。")
    elif selected_hotel:
        parts.append("住宿已经先锁了一版主候选，路线和预算会优先按这家酒店回填。")
    if poi_candidates:
        parts.append(f"景点主线已接入 {len(poi_candidates)} 个候选，已经不是只靠城市通用模板在排。")
    elif search_error:
        parts.append(f"这轮实时搜索没有完全跑通：{search_error}。景点部分先按通用热门点补齐。")
    else:
        parts.append("景点部分先按通用热门点补齐，后续还可以继续补实时景点候选。")
    return " ".join(parts)


def _build_input_snapshot(request: TripRequest, price_scan_summary: dict | None, planning_trace) -> dict[str, object]:
    snapshot = {
        "出发地": request.departure_city,
        "目的地": request.destination,
        "开始日期": request.start_date,
        "结束日期": request.end_date,
        "需求模式": "时间窗口比价" if request.request_mode == "price_scan" else "固定日期行程",
        "人数": request.traveler_count,
        "人均预算": request.budget_per_person or "",
        "节奏": request.travel_style,
        "实时搜索": "开启" if request.enable_live_search else "关闭",
        "规划引擎": planning_trace.engine if planning_trace else "规则规划器",
        "必去点": request.must_go,
        "酒店偏好": request.hotel_preferences,
        "交通偏好": request.transport_preferences,
        "已知酒店": request.user_hotel_name,
        "酒店区域": request.user_hotel_area,
        "酒店每晚价格": request.user_hotel_nightly_price or "",
        "已知交通": request.user_transport_label,
        "交通类型": request.user_transport_category,
        "交通总价": request.user_transport_total_price or "",
        "交通出发时间": request.user_transport_depart_at,
        "交通到达时间": request.user_transport_arrive_at,
        "到达目的地时间": request.user_arrival_at_destination,
        "返程出发时间": request.user_return_depart_at,
        "备注": request.notes,
    }
    if request.request_mode == "price_scan":
        snapshot.update(
            {
                "窗口开始": request.flexible_window_start,
                "窗口结束": request.flexible_window_end,
                "目标天数": request.target_trip_days or "",
                "目标晚数": request.target_trip_nights or "",
                "价格倾向": "尽量便宜" if request.price_priority == "low" else "均衡",
                "已选窗口": price_scan_summary["chosen_start_date"] if price_scan_summary else "",
                "窗口总价": round(price_scan_summary["chosen_price"], 2) if price_scan_summary else "",
            }
        )
    return snapshot


def _build_assumptions(
    request: TripRequest,
    route_adapter,
    search_error: str,
    poi_candidates: list[POICandidate],
    price_scan_summary: dict | None,
    price_scan_error: str,
    selected_hotel,
    selected_transport,
    planning_trace,
) -> list[str]:
    search_error = _friendly_search_issue(search_error)
    price_scan_error = _friendly_search_issue(price_scan_error)
    assumptions = [
        "路上时间和机动缓冲已经拆开显示，不会混在同一条说明里。",
        "每天第一段和最后一段移动会优先围绕主酒店或已知住宿来处理。",
    ]
    if planning_trace and planning_trace.mode == "llm":
        assumptions.insert(0, "当前主线由智能规划生成；路线、预算和时间仍会经过规则校验。")
    elif planning_trace and planning_trace.mode == "candidate":
        assumptions.insert(0, "当前主线优先根据实时搜到的候选生成；没有命中的部分会先用保守安排补齐。")
    else:
        assumptions.insert(0, "当前这版先按基础规则生成，方便先确认主线路和预算框架。")
    if route_adapter:
        provider_name = getattr(route_adapter, "provider_name", "地图引擎")
        provider_label = "高德地图" if provider_name == "amap" else "Google Maps" if provider_name == "google" else provider_name
        assumptions.append(f"部分市内路线已接入 {provider_label} 的实际时间；没拿到结果的路段会先保守预留。")
    else:
        assumptions.append("当前还没有接入地图实况，移动时间先按保守口径预留。")

    if request.enable_live_search:
        if search_error:
            assumptions.append(f"实时搜索这轮存在回退：{search_error}")
        else:
            assumptions.append("已开启实时搜索；如果命中缓存，会优先复用缓存减少外部调用。")
    else:
        assumptions.append("当前未开启实时机酒搜索，候选优先使用已知信息和基础规则补齐。")

    if poi_candidates:
        assumptions.append("当前景点主线优先使用实时搜到的候选，不再只依赖通用城市画像。")
    else:
        assumptions.append("当前景点候选不足，部分时段仍可能继续补点。")

    if request.request_mode == "price_scan":
        if price_scan_summary:
            assumptions.append("时间窗口比价当前只做了首轮采样，后续还可以继续扩大样本。")
        else:
            assumptions.append(f"时间窗口还未正式锁定：{price_scan_error or '当前仍在用默认窗口方案。'}")

    if selected_transport and selected_transport.provider == "user_input":
        assumptions.append("主交通来自手动输入，价格和链接默认视为已确认。")
    if selected_hotel and selected_hotel.provider == "user_input":
        assumptions.append("主酒店来自手动输入，系统不会再尝试替换这家酒店。")
    return assumptions


def _build_open_questions(
    request: TripRequest,
    selected_hotel,
    selected_transport,
    poi_candidates: list[POICandidate],
    price_scan_summary: dict | None,
    planning_trace,
) -> list[str]:
    if request.request_mode == "price_scan" and not price_scan_summary:
        return [
            "是否要扩大时间窗口采样范围，继续比较更多日期组合？",
            "当前更优先最低机票，还是更优先机票和酒店的总价？",
            "如果窗口内出现多个低价日期，结果页是否要保留 2 到 3 个候补窗口？",
        ]

    questions: list[str] = []
    if not selected_transport:
        questions.append("主交通还没锁定，要不要先补一轮高铁或机票的真实候选？")
    if not selected_hotel:
        questions.append("主酒店还没锁定，要不要先多看 2 到 3 家位置相近的备选？")
    if not poi_candidates:
        questions.append("当前景点信息还不够细，要不要继续补开放时间、门票和预约入口？")
    if planning_trace and planning_trace.mode == "fallback":
        questions.append("这版先按基础节奏排好了，要不要再补一轮更细的实时信息？")
    return questions


def _build_provider_statuses(
    request: TripRequest,
    route_adapter,
    search_adapter,
    search_error: str,
    selected_hotel,
    selected_transport,
    booking_items,
    daily_plan,
    poi_candidates: list[POICandidate],
    price_scan_summary: dict | None,
    price_scan_error: str,
    planning_trace,
) -> list[ProviderStatus]:
    statuses: list[ProviderStatus] = []

    if planning_trace:
        statuses.append(
            ProviderStatus(
                name="规划引擎",
                status=(
                    "智能生成"
                    if planning_trace.mode == "llm"
                    else "实时候选优先"
                    if planning_trace.mode == "candidate"
                    else "基础规划"
                ),
                details=planning_trace.details or "",
            )
        )

    if request.request_mode == "price_scan":
        if price_scan_summary:
            statuses.append(
                ProviderStatus(
                    name="低价窗口",
                    status="已完成首轮比价",
                    details=(
                        f"窗口 {price_scan_summary['window_start']} 到 {price_scan_summary['window_end']}，"
                        f"当前锁定 {price_scan_summary['chosen_start_date']} 到 {price_scan_summary['chosen_end_date']}，"
                        f"样本数 {price_scan_summary['sample_count']}。"
                    ),
                )
            )
        else:
            statuses.append(
                ProviderStatus(
                    name="低价窗口",
                    status="待继续补样本",
                    details=price_scan_error or "当前还没有拿到稳定的时间窗口比价结果。",
                )
            )

    statuses.append(
        ProviderStatus(
            name="路线数据",
            status="已接入地图实况" if _contains_real_route_data(daily_plan) else "部分路段先保守预留",
            details=(
                f"当前优先使用 {'高德地图' if getattr(route_adapter, 'provider_name', '') == 'amap' else 'Google Maps' if getattr(route_adapter, 'provider_name', '') == 'google' else getattr(route_adapter, 'provider_name', '地图服务')} 的路线时间；拿不到结果的路段先保守预留。"
                if route_adapter
                else "当前还没有接入地图实况，移动时间先按保守口径预留。"
            ),
        )
    )

    statuses.append(
        ProviderStatus(
            name="机酒搜索",
            status=_hotel_transport_status(request, selected_hotel, selected_transport, search_error, search_adapter),
            details=_hotel_transport_detail(request, selected_hotel, selected_transport, search_error, search_adapter),
        )
    )

    statuses.append(
        ProviderStatus(
            name="景点候选",
            status="已接入实时候选" if poi_candidates else "先按通用热门点补齐",
            details=(
                f"当前已接入 {len(poi_candidates)} 个景点候选，日程主线会优先根据这些候选生成。"
                if poi_candidates
                else "当前还没有拿到稳定的实时景点候选，先按通用热门点补齐。"
            ),
        )
    )

    booking_with_links = [item for item in booking_items if getattr(item, "url", "")]
    statuses.append(
        ProviderStatus(
            name="预定入口",
            status="已生成预定链接" if booking_with_links else "待补预定链接",
            details="当前结果页里的预定事项已经带链接，可直接跳转。" if booking_with_links else "当前还没有可点击的预定入口。",
        )
    )
    return statuses


def _hotel_transport_status(request: TripRequest, selected_hotel, selected_transport, search_error: str, search_adapter) -> str:
    warning = str(getattr(search_adapter, "last_warning", "") or "").strip()
    if any((provider == "user_input") for provider in [getattr(selected_hotel, "provider", ""), getattr(selected_transport, "provider", "")]):
        return "已使用手动信息"
    if not request.enable_live_search:
        return "未开启实时搜索"
    if search_error:
        return "实时搜索暂时不可用"
    if any(
        provider and provider not in {"rule_fallback", "flyai_snapshot"}
        for provider in [getattr(selected_hotel, "provider", ""), getattr(selected_transport, "provider", "")]
    ):
        return "已接入实时结果（部分请求失败）" if warning else "已接入实时结果"
    if getattr(search_adapter, "last_source", "") == "cache":
        return "已使用缓存结果（部分请求失败）" if warning else "已使用缓存结果"
    if selected_hotel or selected_transport:
        return "已接入候选（部分请求失败）" if warning else "已接入候选"
    return "先按基础候选规划"


def _hotel_transport_detail(request: TripRequest, selected_hotel, selected_transport, search_error: str, search_adapter) -> str:
    warning = _friendly_search_issue(str(getattr(search_adapter, "last_warning", "") or "").strip())
    search_error = _friendly_search_issue(search_error)
    if any((provider == "user_input") for provider in [getattr(selected_hotel, "provider", ""), getattr(selected_transport, "provider", "")]):
        return "主酒店或主交通来自手动输入，系统会直接围绕这些已知信息排路线和预算。"
    if not request.enable_live_search:
        return "当前未开启实时机酒搜索，先用已知信息和基础候选完成方案。"
    if search_error:
        return f"这轮实时搜索没有完全跑通，当前先用已拿到的候选继续完成方案。原因：{search_error}"
    if getattr(search_adapter, "last_source", "") == "cache":
        detail = "当前机酒候选来自本地缓存，没有再次请求外部服务。"
        if warning:
            detail = f"{detail} 另外有部分补充请求失败：{warning}"
        return detail
    detail = "当前机酒候选已经接入，可以继续用于预算和预定清单。"
    if warning:
        detail = f"{detail} 另外有部分补充请求失败：{warning}"
    return detail


def _friendly_search_issue(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if "uv_handle_closing" in lowered or "assertion failed" in lowered:
        return "FlyAI 这轮补充请求没有完成，当前先使用已拿到的候选或缓存结果。"
    return raw


def _contains_real_route_data(daily_plan) -> bool:
    for day in daily_plan:
        for item in day.items:
            if item.category != "交通":
                continue
            provider = str(getattr(item, "route_provider", "") or "").lower()
            if provider in {"amap", "google"}:
                return True
            note = str(getattr(item, "route_summary", "") or item.notes or "")
            if "高德" in note or "Google" in note:
                return True
    return False
