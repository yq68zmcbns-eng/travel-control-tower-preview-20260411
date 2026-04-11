from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MealHint:
    label: str
    notes: str


@dataclass(frozen=True)
class CityProfile:
    hotel_area_hint: str
    default_points: tuple[str, ...]
    arrival_meals: dict[str, MealHint]
    lunch_meals: dict[str, MealHint]
    dinner_meals: dict[str, MealHint]
    departure_meals: dict[str, MealHint]


CITY_PROFILES: dict[str, CityProfile] = {
    "南京": CityProfile(
        hotel_area_hint="优先看新街口或夫子庙一带，晚上回酒店和第二天出发都更顺。",
        default_points=("夫子庙", "中山陵", "南京博物院"),
        arrival_meals={
            "夫子庙": MealHint(
                label="夫子庙晚饭",
                notes="第一晚直接放在夫子庙或老门东一带，适合边逛边吃，也方便顺手看夜景。",
            ),
        },
        lunch_meals={
            "中山陵": MealHint(
                label="中山陵周边简餐",
                notes="上午景区结束后先在景区外圈或回城路上解决，避免为了吃饭再折回核心城区。",
            ),
            "南京博物院": MealHint(
                label="南京博物院或新街口午饭",
                notes="这段安排在博物院周边或新街口，午饭后继续市区活动更顺。",
            ),
        },
        dinner_meals={
            "夫子庙": MealHint(
                label="秦淮河一带晚饭",
                notes="晚饭继续留在夫子庙和秦淮河周边，适合把景点和小吃合在一起处理。",
            ),
            "南京博物院": MealHint(
                label="新街口晚饭",
                notes="从博物院出来回新街口吃晚饭，选择多，也方便直接回酒店。",
            ),
        },
        departure_meals={
            "中山陵": MealHint(
                label="返程前简餐",
                notes="最后一顿建议放在回城路上解决，不建议在中山陵周边拖太久。",
            ),
        },
    ),
    "杭州": CityProfile(
        hotel_area_hint="优先看龙翔桥、凤起路或湖滨一带，离西湖和地铁都近。",
        default_points=("西湖", "灵隐寺", "河坊街"),
        arrival_meals={
            "西湖": MealHint(
                label="湖滨晚饭",
                notes="第一晚放在湖滨或南山路一带，适合轻松开场。",
            ),
        },
        lunch_meals={},
        dinner_meals={},
        departure_meals={},
    ),
    "苏州": CityProfile(
        hotel_area_hint="优先看观前街或平江路一带，晚上步行体验更好。",
        default_points=("平江路", "拙政园", "山塘街"),
        arrival_meals={
            "平江路": MealHint(
                label="平江路晚饭",
                notes="第一晚直接放在平江路一带，适合慢逛和小吃。",
            ),
        },
        lunch_meals={},
        dinner_meals={},
        departure_meals={},
    ),
    "北京": CityProfile(
        hotel_area_hint="优先看东单、王府井或前门一带，地铁换乘和常见景点衔接更方便。",
        default_points=("天安门", "故宫", "什刹海"),
        arrival_meals={
            "天安门": MealHint(
                label="前门晚饭",
                notes="第一晚放在前门大街或大栅栏一带，离中轴线近，也适合作为北京开场。",
            ),
        },
        lunch_meals={
            "故宫": MealHint(
                label="故宫周边午饭",
                notes="上午看故宫后就近解决午饭，避免中午再横穿城区。",
            ),
        },
        dinner_meals={
            "什刹海": MealHint(
                label="什刹海晚饭",
                notes="晚饭放在后海或鼓楼一带，和晚上散步顺手连起来。",
            ),
        },
        departure_meals={
            "什刹海": MealHint(
                label="返程前简餐",
                notes="最后一顿放在回程线附近，别再专门绕路找餐厅。",
            ),
        },
    ),
    "成都": CityProfile(
        hotel_area_hint="优先看春熙路、太古里或宽窄巷子周边，夜间活动和第二天出发都更顺。",
        default_points=("春熙路", "宽窄巷子", "人民公园"),
        arrival_meals={
            "春熙路": MealHint(
                label="春熙路晚饭",
                notes="第一晚直接放在春熙路和太古里周边，吃饭和逛街可以合并处理。",
            ),
        },
        lunch_meals={
            "宽窄巷子": MealHint(
                label="宽窄巷子午饭",
                notes="这顿放在宽窄巷子附近最顺，午饭后直接接人民公园或市中心活动。",
            ),
        },
        dinner_meals={
            "人民公园": MealHint(
                label="市中心火锅晚饭",
                notes="晚饭回市中心吃火锅或川菜，更符合成都节奏。",
            ),
        },
        departure_meals={
            "人民公园": MealHint(
                label="返程前简餐",
                notes="最后一顿就近解决，避免拖着行李跨区找吃的。",
            ),
        },
    ),
    "重庆": CityProfile(
        hotel_area_hint="优先看解放碑或观音桥一带，轨道交通换乘和夜景收尾都更省事。",
        default_points=("解放碑", "洪崖洞", "李子坝"),
        arrival_meals={
            "解放碑": MealHint(
                label="解放碑晚饭",
                notes="第一晚直接放在解放碑周边，适合把夜景和晚饭放在一起。",
            ),
        },
        lunch_meals={
            "李子坝": MealHint(
                label="李子坝或鹅岭午饭",
                notes="中午就在山城步线附近解决，下午继续走观景点更顺。",
            ),
        },
        dinner_meals={
            "洪崖洞": MealHint(
                label="洪崖洞附近晚饭",
                notes="晚饭放在洪崖洞或来福士一带，晚上看夜景最方便。",
            ),
        },
        departure_meals={
            "洪崖洞": MealHint(
                label="返程前简餐",
                notes="最后一顿不再折返，用市中心简餐收尾即可。",
            ),
        },
    ),
    "西安": CityProfile(
        hotel_area_hint="优先看钟楼、小寨或永宁门一带，古城墙内外切换最方便。",
        default_points=("回民街", "西安城墙", "陕西历史博物馆"),
        arrival_meals={
            "回民街": MealHint(
                label="回民街晚饭",
                notes="第一晚直接放在钟楼和回民街一带，适合作为西安开场。",
            ),
        },
        lunch_meals={
            "陕西历史博物馆": MealHint(
                label="小寨午饭",
                notes="博物馆出来后去小寨吃饭最顺，不建议中午再绕回钟楼。",
            ),
        },
        dinner_meals={
            "西安城墙": MealHint(
                label="城墙附近晚饭",
                notes="晚饭留在永宁门或钟楼周边，方便夜景和回酒店。",
            ),
        },
        departure_meals={
            "西安城墙": MealHint(
                label="返程前简餐",
                notes="最后一顿就近解决，确保返程前有余量。",
            ),
        },
    ),
    "长沙": CityProfile(
        hotel_area_hint="优先看五一广场或太平街一带，夜里吃饭和第二天出发都方便。",
        default_points=("太平老街", "岳麓山", "橘子洲"),
        arrival_meals={
            "太平老街": MealHint(
                label="太平老街晚饭",
                notes="第一晚放在太平老街或坡子街一带，适合把小吃和散步一起解决。",
            ),
        },
        lunch_meals={},
        dinner_meals={},
        departure_meals={
            "岳麓山": MealHint(
                label="返程前简餐",
                notes="最后一顿尽量放在回城路上，不建议在山脚下拖太久。",
            ),
        },
    ),
    "武汉": CityProfile(
        hotel_area_hint="优先看江汉路或积玉桥附近，城市移动更顺。",
        default_points=("江汉路", "黄鹤楼", "东湖"),
        arrival_meals={
            "江汉路": MealHint(
                label="江汉路晚饭",
                notes="第一晚放在江汉路步行街一带，吃饭和开场散步可以合并处理。",
            ),
        },
        lunch_meals={},
        dinner_meals={},
        departure_meals={
            "黄鹤楼": MealHint(
                label="返程前简餐",
                notes="最后一顿尽量放在黄鹤楼外圈或回城路上，不建议返程前再横穿江面。",
            ),
        },
    ),
    "青岛": CityProfile(
        hotel_area_hint="优先看栈桥、中山路或五四广场之间，第一次去更好走。",
        default_points=("栈桥", "八大关", "五四广场"),
        arrival_meals={
            "栈桥": MealHint(
                label="栈桥晚饭",
                notes="第一晚放在栈桥或中山路一带，适合边走边看海边夜景。",
            ),
        },
        lunch_meals={},
        dinner_meals={},
        departure_meals={},
    ),
    "洛阳": CityProfile(
        hotel_area_hint="优先看应天门、洛邑古城或市中心交通方便的位置。",
        default_points=("洛邑古城", "龙门石窟", "白马寺"),
        arrival_meals={
            "洛邑古城": MealHint(
                label="洛邑古城晚饭",
                notes="第一晚放在洛邑古城附近，适合把夜景和晚饭一起处理。",
            ),
        },
        lunch_meals={},
        dinner_meals={},
        departure_meals={},
    ),
    "东京": CityProfile(
        hotel_area_hint="优先看上野、浅草、银座或新宿一带，第一次去东京做 3 天短途时，交通和吃饭都更顺。",
        default_points=("浅草", "银座", "涩谷"),
        arrival_meals={
            "浅草": MealHint(
                label="浅草晚饭",
                notes="第一晚直接落在浅草或上野附近，适合用轻松步行把行程拉开，不用一落地就跨区折返。",
            ),
        },
        lunch_meals={
            "银座": MealHint(
                label="银座或东京站午饭",
                notes="中段行程放在银座和东京站一带最顺，吃饭后继续走市区主线，不需要专门绕路。",
            ),
            "涩谷": MealHint(
                label="涩谷午饭",
                notes="如果当天主线放在西侧，午饭直接留在涩谷最省事，下午还能顺接原宿或表参道。",
            ),
        },
        dinner_meals={
            "涩谷": MealHint(
                label="涩谷晚饭",
                notes="晚上把吃饭和夜景放在同一片区处理，适合把涩谷十字路口、街区散步和正餐合在一起。",
            ),
            "银座": MealHint(
                label="银座晚饭",
                notes="如果当天后半段主要在银座或丸之内，晚饭就留在附近，不再跨区找餐厅。",
            ),
        },
        departure_meals={
            "浅草": MealHint(
                label="返程前简餐",
                notes="最后一顿尽量放在回机场方向上，不再硬塞新的商圈和景点。",
            ),
        },
    ),
}


def resolve_city_profile(destination: str) -> CityProfile | None:
    destination = (destination or "").strip()
    if not destination:
        return None
    for city_name, profile in CITY_PROFILES.items():
        if city_name in destination:
            return profile
    return None


def resolve_default_points(destination: str) -> list[str]:
    profile = resolve_city_profile(destination)
    if not profile:
        return []
    return list(profile.default_points)


def meal_hint_for(destination: str, point: str | None, stage: str) -> MealHint | None:
    profile = resolve_city_profile(destination)
    if not profile or not point:
        return None
    point = point.strip()
    mapping = {
        "arrival": profile.arrival_meals,
        "lunch": profile.lunch_meals,
        "dinner": profile.dinner_meals,
        "departure": profile.departure_meals,
    }.get(stage, {})
    return mapping.get(point)


def hotel_area_hint_for(destination: str) -> str:
    profile = resolve_city_profile(destination)
    if not profile:
        return ""
    return profile.hotel_area_hint
