import unittest

from travel_control_tower.adapters.base import HotelCandidate, POICandidate, RouteEstimate, TransportCandidate
from travel_control_tower.planner_core.candidate_refinement import refine_candidates_with_routes
from travel_control_tower.planner_core.models import DayItem, DailyPlan, PlanningTrace, TripRequest
from travel_control_tower.planner_core.normalizer import normalize_request
from travel_control_tower.planner_core.pipeline import build_plan_stub
from travel_control_tower.planner_core.poi_enrichment import select_poi_candidates


class PlannerCoreTests(unittest.TestCase):
    def test_build_plan_stub_can_use_planning_agent(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
        )

        class FakePlanningAgent:
            def is_available(self):
                return True

            def plan(self, context):
                return (
                    [
                        DailyPlan(
                            day_index=1,
                            date="2026-04-11",
                            theme="LLM 生成的第一天",
                            why_this_day="先走城市主线。",
                            transport_strategy="地铁为主。",
                            meal_strategy="午饭贴着主片区。",
                            fallback_if_fast="补一个同区小点。",
                            fallback_if_tired="删掉第二段。",
                            items=[
                                DayItem(label="前往 夫子庙", category="交通", duration_minutes=30, notes="LLM 交通块"),
                                DayItem(label="夫子庙轻松逛", category="游玩", duration_minutes=120, notes="LLM 主活动"),
                                DayItem(label="夫子庙晚饭", category="餐饮", duration_minutes=60, notes="LLM 餐食"),
                            ],
                        ),
                        DailyPlan(
                            day_index=2,
                            date="2026-04-12",
                            theme="LLM 生成的第二天",
                            why_this_day="返程日只保留一个主点。",
                            transport_strategy="单线移动。",
                            meal_strategy="返程前简餐。",
                            fallback_if_fast="补一个轻点。",
                            fallback_if_tired="直接返程。",
                            items=[
                                DayItem(label="退房与行李处理", category="住宿", duration_minutes=40, notes="LLM 退房"),
                                DayItem(label="前往 中山陵", category="交通", duration_minutes=30, notes="LLM 交通块"),
                                DayItem(label="中山陵", category="游玩", duration_minutes=150, notes="LLM 主活动"),
                                DayItem(label="返程前简餐", category="餐饮", duration_minutes=50, notes="LLM 餐食"),
                                DayItem(label="返程交通", category="交通", duration_minutes=90, notes="LLM 返程"),
                            ],
                        ),
                    ],
                    PlanningTrace(
                        engine="LLM planner",
                        mode="llm",
                        model="fake-model",
                        details="测试用 LLM planner。",
                    ),
                )

        plan = build_plan_stub(request, planning_agent=FakePlanningAgent())

        self.assertEqual(plan.daily_plan[0].theme, "LLM 生成的第一天")
        self.assertIsNotNone(plan.planning_trace)
        self.assertEqual(plan.planning_trace.mode, "llm")
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["规划引擎"].status, "智能生成")

    def test_build_plan_stub_planning_agent_failure_falls_back(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
        )

        class FailingPlanningAgent:
            def is_available(self):
                return True

            def plan(self, context):
                raise RuntimeError("planner unavailable")

        plan = build_plan_stub(request, planning_agent=FailingPlanningAgent())

        self.assertNotEqual(plan.daily_plan[0].theme, "LLM 生成的第一天")
        self.assertIsNotNone(plan.planning_trace)
        self.assertTrue(plan.planning_trace.used_fallback)
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["规划引擎"].status, "实时候选优先")
        self.assertIn("planner unavailable", provider_map["规划引擎"].details)

    def test_normalize_request_fills_days_nights_and_total_budget(self) -> None:
        request = TripRequest(
            scenario_id="japan_osaka_weekend",
            departure_city="Shanghai",
            destination="Osaka",
            start_date="2026-06-09",
            end_date="2026-06-11",
            traveler_count=2,
            budget_per_person=3800,
        )

        normalized = normalize_request(request)

        self.assertEqual(normalized.days, 3)
        self.assertEqual(normalized.nights, 2)
        self.assertEqual(normalized.budget_total, 7600)

    def test_build_plan_stub_creates_daily_plan_for_each_day(self) -> None:
        request = TripRequest(
            scenario_id="japan_osaka_weekend",
            departure_city="Shanghai",
            destination="Osaka",
            start_date="2026-06-09",
            end_date="2026-06-11",
            traveler_count=1,
            budget_per_person=3800,
            must_go=["Dotonbori"],
        )

        plan = build_plan_stub(request)

        self.assertEqual(plan.status, "draft")
        self.assertEqual(len(plan.daily_plan), 3)
        self.assertEqual(plan.daily_plan[0].date, "2026-06-09")
        self.assertEqual(plan.daily_plan[-1].date, "2026-06-11")
        self.assertEqual(plan.input_snapshot["出发地"], "Shanghai")
        self.assertGreaterEqual(len(plan.booking_items), 3)
        self.assertTrue(plan.daily_plan[0].why_this_day)
        self.assertTrue(plan.daily_plan[1].transport_strategy)
        self.assertGreater(len(plan.budget.breakdown), 0)
        self.assertTrue(any(item.is_buffer for item in plan.daily_plan[0].items))
        self.assertTrue(any(item.category == "缓冲" for item in plan.daily_plan[1].items))
        self.assertTrue(any("Dotonbori" in item.name for item in plan.booking_items))
        self.assertEqual(plan.daily_plan[0].theme, "心斋桥与道顿堀")
        self.assertTrue(any(item.label == "前往道顿堀片区" for item in plan.daily_plan[0].items))
        self.assertEqual(plan.daily_plan[0].items[0].start_time, "11:00")
        self.assertEqual(plan.daily_plan[0].items[0].end_time, "12:15")
        self.assertGreater(plan.daily_plan[0].estimated_cost_total, 0)
        self.assertTrue(plan.daily_plan[0].estimated_cost_notes)
        self.assertEqual(plan.input_snapshot["实时搜索"], "关闭")
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["机酒搜索"].status, "未开启实时搜索")

    def test_build_plan_stub_can_merge_selected_choices(self) -> None:
        request = TripRequest(
            scenario_id="japan_osaka_weekend",
            departure_city="Shanghai",
            destination="Osaka",
            start_date="2026-06-09",
            end_date="2026-06-11",
            traveler_count=1,
            budget_per_person=3800,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True

            def search_hotels(self, destination, check_in, check_out, keyword="", max_price=1000):
                return [
                    HotelCandidate(
                        name="测试酒店",
                        nightly_price=600,
                        area="近心斋桥",
                        notes="测试酒店说明",
                        booking_url="https://example.com/hotel",
                        provider="fake",
                    )
                ]

            def keyword_search_hotels(self, query):
                return []

            def search_transport(self, departure_city, destination, start_date, end_date):
                return [
                    TransportCandidate(
                        label="测试往返机票",
                        category="往返机票",
                        total_price=1500,
                        depart_at="2026-06-09 06:15:00",
                        arrive_at="2026-06-12 00:05:00",
                        booking_url="https://example.com/flight",
                        provider="fake",
                    )
                ]

            def search_pois(self, city_name, keyword="", max_items=8):
                return []

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        self.assertIsNotNone(plan.selected_hotel)
        self.assertIsNotNone(plan.selected_transport)
        self.assertEqual(plan.selected_hotel.name, "测试酒店")
        self.assertEqual(plan.selected_transport.label, "测试往返机票")
        self.assertTrue(any(item.url == "https://example.com/hotel" for item in plan.booking_items))
        self.assertTrue(any(item.url == "https://example.com/flight" for item in plan.booking_items))
        self.assertTrue(any(item.timing for item in plan.booking_items))
        budget_lines = {item.category: item for item in plan.budget.breakdown}
        self.assertEqual(budget_lines["长途交通"].total, 1500)
        self.assertEqual(budget_lines["酒店住宿"].total, 1200)
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["机酒搜索"].status, "已接入实时结果")

    def test_build_plan_stub_backfills_missing_hotel_price_and_discards_wrong_city_hotel(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="苏州",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True

            def search_hotels(self, destination, check_in, check_out, keyword="", max_price=1000):
                return [
                    HotelCandidate(
                        name="松果酒店(郑州桐柏路市中心医院地铁站店)",
                        nightly_price=0,
                        area="山塘老街",
                        notes="星级：3",
                        booking_url="https://example.com/wrong-city",
                        provider="fake",
                    ),
                    HotelCandidate(
                        name="苏州山塘街测试酒店",
                        nightly_price=0,
                        area="山塘老街",
                        notes="星级：3",
                        booking_url="https://example.com/right-city",
                        provider="fake",
                    ),
                ]

            def keyword_search_hotels(self, query):
                return []

            def search_transport(self, departure_city, destination, start_date, end_date):
                return [
                    TransportCandidate(
                        label="上海 - 苏州 高铁往返",
                        category="往返高铁",
                        total_price=320,
                        depart_at="2026-04-11 07:00",
                        arrive_at="2026-04-12 21:00",
                        booking_url="https://example.com/train",
                        provider="fake",
                    )
                ]

            def search_pois(self, city_name, keyword="", max_items=8):
                return []

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())

        self.assertIsNotNone(plan.selected_hotel)
        self.assertEqual(plan.selected_hotel.name, "苏州山塘街测试酒店")
        self.assertGreater(plan.selected_hotel.nightly_price, 0)
        self.assertIn("估算每晚约", plan.selected_hotel.notes)
        budget_lines = {item.category: item for item in plan.budget.breakdown}
        self.assertGreater(budget_lines["酒店住宿"].total, 0)
        self.assertGreater(plan.budget.fixed_cost_total, budget_lines["长途交通"].total)

    def test_build_plan_stub_prefers_priced_real_hotel_over_unpriced_keyword_hotel(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="苏州",
            start_date="2026-04-18",
            end_date="2026-04-19",
            traveler_count=2,
            enable_live_search=True,
            must_go=["山塘老街"],
        )

        class FakeSearchAdapter:
            is_available = True

            def search_hotels(
                self,
                destination,
                check_in,
                check_out,
                keyword="",
                max_price=1000,
                sort="price_asc",
                hotel_types="",
                hotel_stars="",
            ):
                if sort == "price_asc":
                    return []
                return [
                    HotelCandidate(
                        name="苏州虞桥酒店（观前街平江路店）",
                        nightly_price=653,
                        area="近观前街",
                        notes="星级：4",
                        booking_url="https://example.com/priced-hotel",
                        provider="fake",
                    )
                ]

            def keyword_search_hotels(self, query):
                return [
                    HotelCandidate(
                        name="LBED精选酒店(苏州山塘街石路地铁站店)",
                        nightly_price=0,
                        area="山塘老街",
                        notes="星级：3",
                        booking_url="https://example.com/anchor-hotel",
                        provider="fake",
                    )
                ]

            def search_transport(self, departure_city, destination, start_date, end_date):
                return [
                    TransportCandidate(
                        label="上海 - 苏州 高铁往返",
                        category="往返高铁",
                        total_price=320,
                        depart_at="2026-04-18 08:00",
                        arrive_at="2026-04-19 21:00",
                        booking_url="https://example.com/train",
                        provider="fake",
                    )
                ]

            def search_pois(self, city_name, keyword="", max_items=8):
                return []

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())

        self.assertIsNotNone(plan.selected_hotel)
        self.assertEqual(plan.selected_hotel.name, "苏州虞桥酒店（观前街平江路店）")
        self.assertEqual(plan.selected_hotel.nightly_price, 653)

    def test_build_plan_stub_prefers_user_supplied_hotel_and_transport(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="大阪",
            start_date="2026-06-09",
            end_date="2026-06-11",
            traveler_count=1,
            budget_per_person=3800,
            user_hotel_name="KOKO HOTEL 大阪心斋桥",
            user_hotel_area="心斋桥",
            user_hotel_nightly_price=386,
            user_hotel_url="https://example.com/hotel",
            user_transport_label="MM080 + MM079",
            user_transport_category="往返机票",
            user_transport_total_price=1558,
            user_transport_depart_at="2026-06-09 06:15",
            user_transport_arrive_at="2026-06-11 00:05",
            user_transport_url="https://example.com/flight",
        )

        plan = build_plan_stub(request)

        self.assertIsNotNone(plan.selected_hotel)
        self.assertIsNotNone(plan.selected_transport)
        self.assertEqual(plan.selected_hotel.provider, "user_input")
        self.assertEqual(plan.selected_transport.provider, "user_input")
        self.assertEqual(plan.selected_hotel.name, "KOKO HOTEL 大阪心斋桥")
        self.assertEqual(plan.selected_transport.label, "MM080 + MM079")
        self.assertEqual(plan.input_snapshot["已知酒店"], "KOKO HOTEL 大阪心斋桥")
        self.assertEqual(plan.input_snapshot["已知交通"], "MM080 + MM079")
        self.assertTrue(any(item.url == "https://example.com/hotel" for item in plan.booking_items))
        self.assertTrue(any(item.url == "https://example.com/flight" for item in plan.booking_items))
        budget_lines = {item.category: item for item in plan.budget.breakdown}
        self.assertEqual(budget_lines["长途交通"].total, 1558)
        self.assertEqual(budget_lines["酒店住宿"].total, 772)
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["机酒搜索"].status, "已使用手动信息")

    def test_build_plan_stub_can_use_explicit_arrival_and_return_times_for_schedule(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="大阪",
            start_date="2026-06-09",
            end_date="2026-06-11",
            traveler_count=1,
            budget_per_person=3800,
            user_transport_label="MM080 + MM079",
            user_transport_category="往返机票",
            user_transport_total_price=1558,
            user_arrival_at_destination="2026-06-09 09:35",
            user_return_depart_at="2026-06-11 22:20",
        )

        plan = build_plan_stub(request)

        self.assertEqual(plan.daily_plan[0].items[0].start_time, "09:35")
        self.assertEqual(plan.daily_plan[-1].items[-1].end_time, "22:20")

    def test_build_plan_stub_generic_city_can_use_real_transport_candidates(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True

            def search_hotels(self, *args, **kwargs):
                return []

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_transport(self, *args, **kwargs):
                return [
                    TransportCandidate(
                        label="上海 - 南京 高铁往返",
                        category="往返高铁",
                        total_price=320,
                        depart_at="2026-04-11 07:00",
                        arrive_at="2026-04-12 21:00",
                        booking_url="https://example.com/train",
                        provider="fake",
                    )
                ]

            def search_pois(self, city_name, keyword="", max_items=8):
                return []

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        self.assertIsNotNone(plan.selected_transport)
        self.assertEqual(plan.selected_transport.provider, "fake")
        self.assertEqual(plan.selected_transport.label, "上海 - 南京 高铁往返")
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["机酒搜索"].status, "已接入实时结果")

    def test_build_plan_stub_skips_live_search_when_destination_is_missing(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
        )

        class ExplodingSearchAdapter:
            is_available = True

            def search_hotels(self, *args, **kwargs):
                raise AssertionError("hotel search should not run")

            def keyword_search_hotels(self, *args, **kwargs):
                raise AssertionError("keyword hotel search should not run")

            def search_transport(self, *args, **kwargs):
                raise AssertionError("transport search should not run")

            def search_pois(self, *args, **kwargs):
                raise AssertionError("poi search should not run")

        plan = build_plan_stub(request, search_adapter=ExplodingSearchAdapter())

        self.assertIn("待定目的地", plan.overview_title)
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertIn("目的地", provider_map["机酒搜索"].details)

    def test_build_plan_stub_generic_city_has_rule_fallback_choices(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            transport_preferences=["铁路", "步行"],
            must_go=["夫子庙", "中山陵"],
            enable_live_search=True,
        )

        plan = build_plan_stub(request)
        self.assertIsNotNone(plan.selected_hotel)
        self.assertIsNotNone(plan.selected_transport)
        self.assertIn("南京", plan.selected_hotel.name)
        self.assertEqual(plan.selected_hotel.area, "南京市中心")
        self.assertIn("高铁", plan.selected_transport.label)
        self.assertTrue(any("ctrip" in (item.url or "").lower() for item in plan.booking_items))
        self.assertTrue(any("12306" in (item.url or "").lower() for item in plan.booking_items))

    def test_build_plan_stub_discards_foreign_hotel_results_and_falls_back(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="杭州",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return [
                    HotelCandidate(
                        name="嘉逸·悦和酒店(长沙市中心医院铁道学院店)",
                        nightly_price=188,
                        area="长沙市中心",
                        notes="外地脏结果",
                        booking_url="https://example.com/wrong-city-hotel",
                        provider="fake",
                    )
                ]

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_transport(self, *args, **kwargs):
                return []

            def search_pois(self, *args, **kwargs):
                return []

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        self.assertIsNotNone(plan.selected_hotel)
        self.assertEqual(plan.selected_hotel.provider, "rule_fallback")
        self.assertIn("杭州", plan.selected_hotel.name)
        self.assertEqual(plan.selected_hotel.area, "杭州市中心")

    def test_select_poi_candidates_uses_generic_keyword_fallback_once(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="杭州",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True

            def __init__(self):
                self.calls = []

            def search_pois(self, city_name, keyword="", max_items=8):
                self.calls.append((city_name, keyword, max_items))
                if keyword == "":
                    return []
                if keyword == "景区":
                    return [
                        POICandidate(
                            name="西湖风景名胜区",
                            city_name=city_name,
                            category="景区",
                            poi_level="5A",
                            free_status="部分免费",
                            address="杭州",
                            notes="热门主线",
                            booking_url="https://example.com/xihu",
                            provider="fake",
                        )
                    ]
                return []

        adapter = FakeSearchAdapter()
        candidates = select_poi_candidates(request, search_adapter=adapter)
        self.assertIn("西湖风景名胜区", [item.name for item in candidates])
        self.assertEqual(adapter.calls[0][1], "历史街区")
        self.assertIn(("杭州", "景区", 6), adapter.calls)

    def test_select_poi_candidates_can_use_route_adapter_without_live_search(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="杭州",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            enable_live_search=False,
        )

        class RouteOnlyAdapter:
            is_available = True
            provider_name = "amap"

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(
                        name="西湖风景名胜区",
                        city_name=city_name,
                        category="景区",
                        poi_level="5A",
                        free_status="部分免费",
                        address="杭州",
                        provider="amap",
                    )
                ]

        candidates = select_poi_candidates(
            request,
            search_adapter=None,
            fallback_search_adapter=RouteOnlyAdapter(),
        )
        self.assertIn("西湖风景名胜区", [item.name for item in candidates])

    def test_select_poi_candidates_can_fallback_to_route_adapter_search(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="杭州",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            enable_live_search=True,
        )

        class EmptySearchAdapter:
            is_available = True

            def search_pois(self, city_name, keyword="", max_items=8):
                return []

        class FallbackRouteAdapter:
            is_available = True

            def __init__(self):
                self.calls = []

            def search_pois(self, city_name, keyword="", max_items=8):
                self.calls.append((city_name, keyword, max_items))
                return [
                    POICandidate(
                        name="西湖风景名胜区",
                        city_name=city_name,
                        category="景区",
                        poi_level="5A",
                        free_status="部分免费",
                        address="杭州",
                        notes="高德补充",
                        booking_url="",
                        provider="amap",
                    )
                ]

        route_adapter = FallbackRouteAdapter()
        candidates = select_poi_candidates(
            request,
            search_adapter=EmptySearchAdapter(),
            fallback_search_adapter=route_adapter,
        )
        self.assertIn("西湖风景名胜区", [item.name for item in candidates])
        self.assertEqual(route_adapter.calls[0][1], "历史街区")

    def test_build_plan_stub_generic_city_routes_use_selected_hotel_anchor(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            transport_preferences=["铁路", "步行"],
            must_go=["夫子庙", "中山陵"],
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True

            def search_hotels(self, destination, check_in, check_out, keyword="", max_price=1000):
                return [
                    HotelCandidate(
                        name="南京测试酒店",
                        nightly_price=480,
                        area="夫子庙商圈",
                        notes="靠近第一天主片区。",
                        booking_url="https://example.com/nanjing-hotel",
                        provider="fake",
                    )
                ]

            def keyword_search_hotels(self, query):
                return []

            def search_transport(self, departure_city, destination, start_date, end_date):
                return [
                    TransportCandidate(
                        label="上海 - 南京 高铁往返",
                        category="往返高铁",
                        total_price=320,
                        depart_at="2026-04-11 07:00:00",
                        arrive_at="2026-04-12 21:00:00",
                        booking_url="https://example.com/nanjing-train",
                        provider="fake",
                    )
                ]

            def search_pois(self, city_name, keyword="", max_items=8):
                return []

        class FakeRouteAdapter:
            provider_name = "fake"

            def __init__(self):
                self.calls = []

            def estimate_transfer(self, origin_label, destination_label, mode, departure_time=None):
                self.calls.append((origin_label, destination_label, mode))
                return RouteEstimate(
                    origin_label=origin_label,
                    destination_label=destination_label,
                    mode=mode,
                    duration_minutes=18,
                    distance_km=3.2,
                    provider="fake",
                    notes="测试路线约 18 分钟，约 3.2 公里。",
                )

        route_adapter = FakeRouteAdapter()
        plan = build_plan_stub(request, route_adapter=route_adapter, search_adapter=FakeSearchAdapter())

        self.assertTrue(route_adapter.calls)
        self.assertTrue(any(call[0] == "南京夫子庙商圈" for call in route_adapter.calls))
        self.assertTrue(any("中山陵" in call[1] for call in route_adapter.calls))

        first_day_items = plan.daily_plan[0].items
        route_index = next(index for index, item in enumerate(first_day_items) if item.label == "前往 夫子庙")
        route_item = first_day_items[route_index]
        route_buffer = first_day_items[route_index + 1]
        self.assertEqual(route_item.duration_minutes, 5)
        self.assertIn("同一片区", route_item.notes)
        self.assertEqual(route_item.route_mode, "WALK")
        self.assertEqual(route_item.route_mode_label, "步行")
        self.assertEqual(route_item.route_provider, "manual")
        self.assertTrue(route_item.route_origin)
        self.assertTrue(route_item.route_destination)
        self.assertTrue(route_buffer.is_buffer)
        self.assertEqual(route_buffer.duration_minutes, 5)

    def test_build_plan_stub_generic_city_prefers_real_poi_candidates(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="长沙",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2200,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return []

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_transport(self, *args, **kwargs):
                return []

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(
                        name="岳麓山",
                        city_name=city_name,
                        category="自然景观",
                        poi_level="5A",
                        free_status="免费",
                        address="长沙市岳麓区",
                        provider="fake",
                    ),
                    POICandidate(
                        name="橘子洲",
                        city_name=city_name,
                        category="城市地标",
                        poi_level="5A",
                        free_status="免费",
                        address="长沙市岳麓区",
                        provider="fake",
                    ),
                ]

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())

        self.assertTrue({"岳麓山", "橘子洲"}.issubset({item.name for item in plan.poi_candidates}))
        self.assertTrue(any(name in plan.daily_plan[0].theme for name in ("太平老街", "岳麓山", "橘子洲")))
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["景点候选"].status, "已接入实时候选")

    def test_build_plan_stub_can_auto_pick_local_transport_mode(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
            transport_preferences=["地铁", "步行"],
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_warning = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return [
                    HotelCandidate(
                        name="南京新街口酒店",
                        nightly_price=420,
                        area="夫子庙商圈",
                        provider="fake",
                    )
                ]

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_transport(self, *args, **kwargs):
                return []

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(name="夫子庙", city_name=city_name, category="老街商圈", provider="fake"),
                    POICandidate(name="中山陵景区", city_name=city_name, category="景区", poi_level="5A", provider="fake"),
                ]

        class FakeRouteAdapter:
            provider_name = "fake"

            def estimate_transfer(self, origin_label, destination_label, mode, departure_time=None):
                duration_map = {"WALK": 105, "TRANSIT": 46, "DRIVE": 34}
                return RouteEstimate(
                    origin_label=origin_label,
                    destination_label=destination_label,
                    mode=mode,
                    duration_minutes=duration_map.get(mode, 60),
                    distance_km=8.0,
                    provider="fake",
                    notes=f"测试 {mode} 路线。",
                )

        plan = build_plan_stub(request, route_adapter=FakeRouteAdapter(), search_adapter=FakeSearchAdapter())
        transport_notes = [
            item.notes
            for day in plan.daily_plan
            for item in day.items
            if item.category == "交通" and item.label.startswith("前往 ")
        ]
        self.assertTrue(any("建议本段采用公交地铁" in note for note in transport_notes))
        transport_items = [
            item
            for day in plan.daily_plan
            for item in day.items
            if item.category == "交通" and item.label.startswith("前往 ")
        ]
        self.assertTrue(any(item.route_mode == "TRANSIT" for item in transport_items))
        self.assertTrue(any(item.route_mode_label == "公交地铁" for item in transport_items))
        self.assertTrue(any(item.route_provider == "fake" for item in transport_items))
        self.assertTrue(any(item.name == "市内交通准备" for item in plan.booking_items))

    def test_build_plan_stub_uses_must_go_to_rank_poi_candidates(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="长沙",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2200,
            enable_live_search=True,
            must_go=["橘子洲"],
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return []

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_transport(self, *args, **kwargs):
                return []

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(name="岳麓山", city_name=city_name, provider="fake"),
                    POICandidate(name="橘子洲", city_name=city_name, provider="fake"),
                ]

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())

        self.assertEqual(plan.daily_plan[0].theme, "抵达后先逛 橘子洲")

    def test_build_plan_stub_generic_city_uses_profile_points_and_meals(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            transport_preferences=["铁路", "步行"],
            enable_live_search=True,
        )

        plan = build_plan_stub(request)

        self.assertEqual(plan.daily_plan[0].theme, "抵达后先逛 夫子庙")
        self.assertEqual(plan.daily_plan[1].theme, "中山陵 与返程收尾")
        self.assertTrue(any(item.label == "夫子庙晚饭" for item in plan.daily_plan[0].items))
        self.assertTrue(any(item.label == "返程前简餐" for item in plan.daily_plan[1].items))

    def test_build_plan_stub_exposes_provider_statuses_for_fallback(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            transport_preferences=["铁路", "步行"],
            enable_live_search=True,
        )

        class FailingSearchAdapter:
            is_available = True
            last_error = "FlyAI 当前试用额度已用尽"

            def search_hotels(self, *args, **kwargs):
                raise RuntimeError(self.last_error)

            def keyword_search_hotels(self, *args, **kwargs):
                raise RuntimeError(self.last_error)

            def search_transport(self, *args, **kwargs):
                raise RuntimeError(self.last_error)

        plan = build_plan_stub(request, search_adapter=FailingSearchAdapter())

        status_map = {item.name: item for item in plan.provider_statuses}
        self.assertIn("机酒搜索", status_map)
        self.assertEqual(status_map["机酒搜索"].status, "实时搜索暂时不可用")
        self.assertIn("FlyAI 当前试用额度已用尽", status_map["机酒搜索"].details)
        self.assertTrue(any("实时搜索" in item for item in plan.assumptions))

    def test_build_plan_stub_generic_city_keeps_beijing_core_line(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="北京",
            start_date="2026-05-01",
            end_date="2026-05-03",
            traveler_count=1,
            budget_per_person=2600,
        )

        plan = build_plan_stub(request)

        self.assertTrue(any(name in plan.daily_plan[0].theme for name in ("天安门", "故宫", "什刹海")))
        self.assertTrue(any("晚饭" in item.label for item in plan.daily_plan[0].items))
        self.assertIsNotNone(plan.selected_hotel)
        self.assertIn("东单、王府井或前门", plan.selected_hotel.notes)

    def test_build_plan_stub_surfaces_search_provider_failure(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            transport_preferences=["铁路", "步行"],
            enable_live_search=True,
        )

        class FailingSearchAdapter:
            is_available = True

            def __init__(self) -> None:
                self.last_error = ""

            def search_hotels(self, destination, check_in, check_out, keyword="", max_price=1000):
                self.last_error = "FlyAI 当前试用额度已用尽，暂时无法返回真实搜索结果。"
                raise RuntimeError(self.last_error)

            def keyword_search_hotels(self, query):
                raise RuntimeError(self.last_error)

            def search_transport(self, departure_city, destination, start_date, end_date):
                raise RuntimeError(self.last_error)

        plan = build_plan_stub(request, search_adapter=FailingSearchAdapter())
        self.assertIsNotNone(plan.selected_hotel)
        self.assertIn("试用额度已用尽", plan.selected_hotel.notes)
        self.assertTrue(any("实时搜索" in item for item in plan.assumptions))

    def test_build_plan_stub_surfaces_price_scan_mode(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="东京",
            start_date="2026-04-08",
            end_date="2026-04-10",
            traveler_count=1,
            budget_per_person=4000,
            request_mode="price_scan",
            flexible_window_start="2026-04-08",
            flexible_window_end="2026-07-07",
            target_trip_days=3,
            target_trip_nights=2,
            price_priority="low",
        )

        plan = build_plan_stub(request)

        self.assertIn("时间窗口比价需求", plan.overview_summary)
        self.assertEqual(plan.input_snapshot["需求模式"], "时间窗口比价")
        self.assertEqual(plan.input_snapshot["窗口开始"], "2026-04-08")
        self.assertEqual(plan.input_snapshot["窗口结束"], "2026-07-07")
        self.assertEqual(plan.input_snapshot["目标天数"], 3)
        self.assertEqual(plan.input_snapshot["价格倾向"], "尽量便宜")
        self.assertTrue(any("时间窗口" in item for item in plan.assumptions))


    def test_build_plan_stub_price_scan_keeps_window_candidates(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="东京",
            start_date="2026-04-08",
            end_date="2026-04-10",
            traveler_count=1,
            budget_per_person=4000,
            request_mode="price_scan",
            flexible_window_start="2026-04-08",
            flexible_window_end="2026-07-07",
            target_trip_days=3,
            target_trip_nights=2,
            price_priority="low",
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return []

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_transport(self, departure_city, destination, start_date, end_date):
                return []

            def scan_transport_windows(self, *args, **kwargs):
                return [
                    TransportCandidate(
                        label="东京窗口 A",
                        category="往返机票",
                        total_price=1800,
                        depart_at="2026-04-15 08:00",
                        arrive_at="2026-04-17 22:00",
                        outbound_arrive_at="2026-04-15 12:00",
                        return_depart_at="2026-04-17 18:00",
                        trip_start_date="2026-04-15",
                        trip_end_date="2026-04-17",
                        booking_url="https://example.com/flight-a",
                        provider="fake",
                    ),
                    TransportCandidate(
                        label="东京窗口 B",
                        category="往返机票",
                        total_price=2100,
                        depart_at="2026-04-22 08:00",
                        arrive_at="2026-04-24 22:00",
                        outbound_arrive_at="2026-04-22 12:00",
                        return_depart_at="2026-04-24 18:00",
                        trip_start_date="2026-04-22",
                        trip_end_date="2026-04-24",
                        booking_url="https://example.com/flight-b",
                        provider="fake",
                    ),
                ]

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        self.assertIsNotNone(plan.price_scan_summary)
        self.assertEqual(plan.price_scan_summary["chosen_start_date"], "2026-04-15")
        self.assertEqual(plan.price_scan_summary["chosen_price"], 1800)
        self.assertEqual(len(plan.price_scan_candidates), 2)
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["低价窗口"].status, "已完成首轮比价")

    def test_build_plan_stub_prefers_real_hotel_over_hostel(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return [
                    HotelCandidate(
                        name="南京晚上好青年旅社",
                        nightly_price=68,
                        area="新街口",
                        notes="经济型",
                        booking_url="https://example.com/hostel",
                        provider="fake",
                    ),
                    HotelCandidate(
                        name="南京商茂国际酒店",
                        nightly_price=398,
                        area="新街口",
                        notes="四星 酒店",
                        booking_url="https://example.com/hotel",
                        provider="fake",
                    ),
                ]

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_transport(self, *args, **kwargs):
                return []

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(name="夫子庙", city_name=city_name, category="老街商圈", provider="fake"),
                    POICandidate(name="中山陵", city_name=city_name, category="景区", poi_level="5A", provider="fake"),
                ]

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        self.assertIsNotNone(plan.selected_hotel)
        self.assertEqual(plan.selected_hotel.name, "南京商茂国际酒店")

    def test_build_plan_stub_prefers_train_for_generic_domestic_trip(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
            transport_preferences=["高铁"],
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return []

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_trains(self, *args, **kwargs):
                return [
                    TransportCandidate(
                        label="高铁G1786 去程 / 高铁G7019 回程（06:58 - 19:34）",
                        category="往返高铁",
                        total_price=301,
                        depart_at="2026-04-11 06:58",
                        arrive_at="2026-04-12 19:34",
                        outbound_arrive_at="2026-04-11 08:28",
                        return_depart_at="2026-04-12 18:00",
                        trip_start_date="2026-04-11",
                        trip_end_date="2026-04-12",
                        booking_url="https://example.com/train",
                        provider="fake",
                    )
                ]

            def search_transport(self, *args, **kwargs):
                return [
                    TransportCandidate(
                        label="上海 - 南京 往返机票",
                        category="往返机票",
                        total_price=980,
                        depart_at="2026-04-11 09:00",
                        arrive_at="2026-04-12 22:00",
                        booking_url="https://example.com/flight",
                        provider="fake",
                    )
                ]

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(name="夫子庙", city_name=city_name, category="老街商圈", provider="fake"),
                    POICandidate(name="中山陵", city_name=city_name, category="景区", poi_level="5A", provider="fake"),
                ]

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        self.assertIsNotNone(plan.selected_transport)
        self.assertEqual(plan.selected_transport.category, "往返高铁")
        self.assertIn("高铁", plan.selected_transport.label)

    def test_build_plan_stub_merges_train_and_flight_candidates_for_domestic_trip(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_warning = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return []

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_trains(self, *args, **kwargs):
                return [
                    TransportCandidate(
                        label="高铁G1786 去程 / 高铁G7019 回程（06:58 - 19:34）",
                        category="往返高铁",
                        total_price=301,
                        depart_at="2026-04-11 06:58",
                        arrive_at="2026-04-12 19:34",
                        outbound_arrive_at="2026-04-11 08:28",
                        return_depart_at="2026-04-12 18:00",
                        trip_start_date="2026-04-11",
                        trip_end_date="2026-04-12",
                        booking_url="https://example.com/train",
                        provider="fake",
                    )
                ]

            def search_transport(self, *args, **kwargs):
                return [
                    TransportCandidate(
                        label="上海 - 南京 往返机票",
                        category="往返机票",
                        total_price=980,
                        depart_at="2026-04-11 09:00",
                        arrive_at="2026-04-12 22:00",
                        booking_url="https://example.com/flight",
                        provider="fake",
                    )
                ]

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(name="夫子庙", city_name=city_name, category="老街商圈", provider="fake"),
                    POICandidate(name="中山陵", city_name=city_name, category="景区", poi_level="5A", provider="fake"),
                ]

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        self.assertIsNotNone(plan.selected_transport)
        self.assertEqual(plan.selected_transport.category, "往返高铁")
        self.assertEqual(len(plan.transport_candidates), 2)

    def test_build_plan_stub_keeps_partial_transport_failure_as_warning(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
            transport_preferences=["高铁"],
        )

        class FakeSearchAdapter:
            is_available = True

            def __init__(self) -> None:
                self.last_error = ""
                self.last_warning = ""
                self.last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return []

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_trains(self, *args, **kwargs):
                return [
                    TransportCandidate(
                        label="高铁G1786 去程 / 高铁G7019 回程（06:58 - 19:34）",
                        category="往返高铁",
                        total_price=301,
                        depart_at="2026-04-11 06:58",
                        arrive_at="2026-04-12 19:34",
                        outbound_arrive_at="2026-04-11 08:28",
                        return_depart_at="2026-04-12 18:00",
                        trip_start_date="2026-04-11",
                        trip_end_date="2026-04-12",
                        booking_url="https://example.com/train",
                        provider="fake",
                    )
                ]

            def search_transport(self, *args, **kwargs):
                raise RuntimeError("flight provider temporarily failed")

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(name="夫子庙", city_name=city_name, category="老街商圈", provider="fake"),
                    POICandidate(name="中山陵", city_name=city_name, category="景区", poi_level="5A", provider="fake"),
                ]

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        provider_map = {item.name: item for item in plan.provider_statuses}
        self.assertEqual(provider_map["机酒搜索"].status, "已接入实时结果（部分请求失败）")
        self.assertIn("flight provider temporarily failed", provider_map["机酒搜索"].details)

    def test_refine_candidates_with_routes_skips_large_pool_route_ranking(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
        )
        hotel_candidates = [
            HotelCandidate(name=f"南京测试酒店{i}", nightly_price=300 + i * 10, area="新街口", provider="fake")
            for i in range(4)
        ]
        poi_candidates = [
            POICandidate(name=f"测试景点{i}", city_name="南京", category="景区", provider="fake")
            for i in range(5)
        ]

        class FakeRouteAdapter:
            provider_name = "fake"

            def __init__(self) -> None:
                self.calls = []

            def estimate_transfer(self, origin_label, destination_label, mode, departure_time=None):
                self.calls.append((origin_label, destination_label, mode))
                return RouteEstimate(
                    origin_label=origin_label,
                    destination_label=destination_label,
                    mode=mode,
                    duration_minutes=15,
                    distance_km=3.0,
                    provider="fake",
                    notes="测试路线 15 分钟。",
                )

        route_adapter = FakeRouteAdapter()
        refine_candidates_with_routes(request, hotel_candidates, poi_candidates, route_adapter=route_adapter)
        self.assertEqual(route_adapter.calls, [])

    def test_build_plan_stub_reorders_pois_by_day_stage(self) -> None:
        request = TripRequest(
            departure_city="上海",
            destination="南京",
            start_date="2026-04-11",
            end_date="2026-04-12",
            traveler_count=1,
            budget_per_person=2000,
            enable_live_search=True,
        )

        class FakeSearchAdapter:
            is_available = True
            last_error = ""
            last_source = "live"

            def search_hotels(self, *args, **kwargs):
                return []

            def keyword_search_hotels(self, *args, **kwargs):
                return []

            def search_transport(self, *args, **kwargs):
                return []

            def search_pois(self, city_name, keyword="", max_items=8):
                return [
                    POICandidate(name="中山陵", city_name=city_name, category="景区", poi_level="5A", provider="fake"),
                    POICandidate(name="南京博物院", city_name=city_name, category="博物馆", poi_level="5A", provider="fake"),
                    POICandidate(name="夫子庙", city_name=city_name, category="老街商圈", provider="fake"),
                ]

        plan = build_plan_stub(request, search_adapter=FakeSearchAdapter())
        self.assertEqual(plan.daily_plan[0].theme, "抵达后先逛 夫子庙")
        self.assertEqual(plan.daily_plan[1].theme, "中山陵 与返程收尾")


if __name__ == "__main__":
    unittest.main()
