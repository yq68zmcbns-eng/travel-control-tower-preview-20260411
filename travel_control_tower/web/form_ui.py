from __future__ import annotations

import html

import requests

from ..planner_core.request_resolution import ExplicitFormOverrides, resolve_trip_request
from ..runtime_config import RuntimeConfig, load_runtime_config

PROMPT_TEMPLATES = [
    ("周末放松两天", "下周末从{departure}出发，去附近城市玩两天，预算 2000，节奏轻松一点，想吃当地特色，不要太赶。"),
    ("三天城市小旅行", "从{departure}出发去一个适合三天旅行的城市，预算 3500，想住得方便一点，景点和吃饭都要顺路。"),
    ("低价出境窗口", "未来 3 个月从{departure}飞日本玩 3 天，想找机票和酒店都便宜的时间，再给我一套详细方案。"),
    ("已有机酒补行程", "我已经订好了机票和酒店，帮我基于已有信息细化每天路线、预算、当地交通和预定事项。"),
]


def _esc(value: str) -> str:
    return html.escape(str(value or ""))


def default_form_values() -> dict[str, str]:
    return {
        "freeform_request": "",
        "enable_live_search": "",
        "scenario_id": "",
        "departure_city": "",
        "destination": "",
        "start_date": "",
        "end_date": "",
        "traveler_count": "1",
        "budget_per_person": "",
        "travel_style": "balanced",
        "must_go": "",
        "hotel_preferences": "",
        "transport_preferences": "",
        "user_hotel_name": "",
        "user_hotel_area": "",
        "user_hotel_nightly_price": "",
        "user_hotel_url": "",
        "user_transport_label": "",
        "user_transport_category": "",
        "user_transport_total_price": "",
        "user_transport_depart_at": "",
        "user_transport_arrive_at": "",
        "user_arrival_at_destination": "",
        "user_return_depart_at": "",
        "user_transport_url": "",
        "notes": "",
    }


def _split_lines(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").splitlines() if part.strip()]


def _field_float(fields: dict[str, str], name: str) -> float | None:
    raw = str(fields.get(name, "") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _build_explicit_overrides(fields: dict[str, str]) -> ExplicitFormOverrides:
    defaults = default_form_values()

    def explicit_value(name: str) -> str:
        raw = str(fields.get(name, "") or "").strip()
        return raw if raw and raw != str(defaults.get(name, "") or "").strip() else ""

    return ExplicitFormOverrides(
        departure_city=explicit_value("departure_city"),
        destination=explicit_value("destination"),
        start_date=explicit_value("start_date"),
        end_date=explicit_value("end_date"),
        traveler_count=explicit_value("traveler_count"),
        budget_per_person=explicit_value("budget_per_person"),
        travel_style=explicit_value("travel_style"),
        must_go=_split_lines(explicit_value("must_go")),
        hotel_preferences=_split_lines(explicit_value("hotel_preferences")),
        transport_preferences=_split_lines(explicit_value("transport_preferences")),
        enable_live_search=str(fields.get("enable_live_search", "") or "").strip().lower() in {"1", "true", "on", "yes"},
        scenario_id=explicit_value("scenario_id"),
        user_hotel_name=str(fields.get("user_hotel_name", "") or "").strip(),
        user_hotel_area=str(fields.get("user_hotel_area", "") or "").strip(),
        user_hotel_nightly_price=_field_float(fields, "user_hotel_nightly_price"),
        user_hotel_url=str(fields.get("user_hotel_url", "") or "").strip(),
        user_transport_label=str(fields.get("user_transport_label", "") or "").strip(),
        user_transport_category=str(fields.get("user_transport_category", "") or "").strip(),
        user_transport_total_price=_field_float(fields, "user_transport_total_price"),
        user_transport_depart_at=str(fields.get("user_transport_depart_at", "") or "").strip(),
        user_transport_arrive_at=str(fields.get("user_transport_arrive_at", "") or "").strip(),
        user_arrival_at_destination=str(fields.get("user_arrival_at_destination", "") or "").strip(),
        user_return_depart_at=str(fields.get("user_return_depart_at", "") or "").strip(),
        user_transport_url=str(fields.get("user_transport_url", "") or "").strip(),
        notes=str(fields.get("notes", "") or "").strip(),
    )


def parse_trip_request(fields: dict[str, str], today=None):
    freeform_text = str(fields.get("freeform_request", "") or "").strip()
    return resolve_trip_request(
        freeform_text=freeform_text,
        today=today,
        overrides=_build_explicit_overrides(fields),
    )


def build_preview_request(fields: dict[str, str], today=None):
    freeform_text = str(fields.get("freeform_request", "") or "").strip()
    if not freeform_text:
        return None
    return resolve_trip_request(
        freeform_text=freeform_text,
        today=today,
        overrides=_build_explicit_overrides(fields),
        runtime_config=RuntimeConfig(request_parser_mode="rule"),
    )


def reverse_geocode_city(lat: str, lng: str) -> dict[str, str]:
    runtime = load_runtime_config()
    if not runtime.amap_web_key:
        raise RuntimeError("缺少高德 key，暂时无法反查定位城市。")

    response = requests.get(
        "https://restapi.amap.com/v3/geocode/regeo",
        params={
            "key": runtime.amap_web_key,
            "location": f"{float(lng)},{float(lat)}",
            "extensions": "base",
            "radius": 1000,
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if str(data.get("status")) != "1":
        raise RuntimeError("高德逆地理接口返回失败。")

    component = ((data.get("regeocode") or {}).get("addressComponent") or {})
    city = component.get("city")
    if isinstance(city, list):
        city = city[0] if city else ""
    return {
        "city": str(city or component.get("province") or "").strip(),
        "district": str(component.get("district") or "").strip(),
        "province": str(component.get("province") or "").strip(),
    }


def locate_city_by_ip(ip: str = "") -> dict[str, str]:
    runtime = load_runtime_config()
    if not runtime.amap_web_key:
        raise RuntimeError("缺少高德 key，暂时无法按网络位置识别城市。")
    params = {"key": runtime.amap_web_key}
    clean_ip = str(ip or "").strip()
    if clean_ip and clean_ip not in {"127.0.0.1", "::1", "unknown"}:
        params["ip"] = clean_ip
    response = requests.get("https://restapi.amap.com/v3/ip", params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    if str(data.get("status")) != "1":
        raise RuntimeError(str(data.get("info") or "高德 IP 定位返回失败。"))
    city = data.get("city")
    if isinstance(city, list):
        city = city[0] if city else ""
    city = str(city or "").strip()
    if not city:
        raise RuntimeError("没有识别到城市，请手动填写。")
    return {"city": city, "province": str(data.get("province") or "").strip(), "source": "ip"}


def render_form_page(values: dict[str, str] | None = None, error: str = "") -> str:
    values = {**default_form_values(), **(values or {})}
    error_block = f"<div class='error'>{_esc(error)}</div>" if error else ""
    checked = "checked" if values.get("enable_live_search") else ""
    prompt_buttons = "".join(
        f"<button type='button' class='prompt-chip' data-template='{_esc(template)}'>{_esc(label)}</button>"
        for label, template in PROMPT_TEMPLATES
    )
    constraints_open = "open" if error or any(
        str(values.get(key, "") or "").strip()
        for key in values
        if key not in {"freeform_request", "traveler_count", "budget_per_person", "travel_style", "enable_live_search"}
    ) else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Travel Control Tower</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Manrope:wght@400;500;600;700;800&family=Noto+Serif+SC:wght@500;600;700&display=swap');
    :root {{
      --bg: #f6efe5;
      --paper: rgba(255, 251, 246, .96);
      --ink: #1b2a3b;
      --muted: #6d6257;
      --line: rgba(125, 103, 77, .16);
      --line-strong: rgba(125, 103, 77, .28);
      --accent: #a15a32;
      --accent-soft: #f3e2d1;
      --brand: #22384f;
      --chip-a: #efe3d4;
      --chip-b: #e5ece8;
      --chip-c: #ece4ee;
      --shadow: 0 22px 46px rgba(47, 35, 23, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Manrope", "PingFang SC", "Microsoft YaHei", sans-serif;
      background:
        linear-gradient(rgba(123, 100, 71, .03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(123, 100, 71, .03) 1px, transparent 1px),
        linear-gradient(180deg, #fbf7f2 0%, var(--bg) 100%);
      background-size: 26px 26px, 26px 26px, auto;
    }}
    button, input, select, textarea {{ font: inherit; }}
    .page {{
      width: min(980px, calc(100vw - 32px));
      margin: 0 auto 56px;
    }}
    .masthead {{
      padding: 22px 0 0;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 12px;
      letter-spacing: .16em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .masthead strong {{
      color: var(--brand);
      font-size: 13px;
    }}
    .hero {{
      margin-top: 18px;
      padding: 32px 32px 20px;
      border: 1px solid var(--line);
      border-radius: 32px;
      background:
        radial-gradient(circle at right top, rgba(34, 56, 79, .08), transparent 24%),
        linear-gradient(180deg, rgba(255,255,255,.88) 0%, rgba(249,242,234,.94) 100%);
      box-shadow: var(--shadow);
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      padding: 7px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      letter-spacing: .14em;
      text-transform: uppercase;
      border: 1px solid rgba(161, 90, 50, .12);
    }}
    h1 {{
      margin: 18px 0 12px;
      font-family: "Cormorant Garamond", "Noto Serif SC", "Songti SC", serif;
      font-size: clamp(46px, 7vw, 78px);
      line-height: .92;
      letter-spacing: -.05em;
    }}
    .hero p {{
      margin: 0;
      max-width: 760px;
      color: var(--muted);
      line-height: 1.85;
      font-size: 15px;
    }}
    .panel {{
      margin-top: 18px;
      padding: 28px;
      border: 1px solid var(--line);
      border-radius: 32px;
      background: var(--paper);
      box-shadow: var(--shadow);
    }}
    .create-modes {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .create-mode {{
      display: grid;
      gap: 7px;
      min-height: 126px;
      padding: 20px;
      border: 1px solid var(--line-strong);
      border-radius: 24px;
      background: rgba(255,255,255,.8);
      color: var(--ink);
      text-decoration: none;
      box-shadow: 0 12px 28px rgba(47,35,23,.05);
    }}
    .create-mode.active {{ border-color: rgba(161,90,50,.42); background: #fff9f2; }}
    .create-mode strong {{ font-size: 21px; }}
    .create-mode span {{ color: var(--muted); line-height: 1.65; }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .panel-head h2 {{
      margin: 6px 0 0;
      font-size: 30px;
      letter-spacing: -.03em;
    }}
    .panel-head p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.8;
      max-width: 420px;
      text-align: right;
    }}
    .section-kicker {{
      font-size: 12px;
      letter-spacing: .16em;
      text-transform: uppercase;
      color: var(--accent);
    }}
    label {{
      display: block;
      margin-bottom: 8px;
      font-weight: 700;
    }}
    textarea, input, select {{
      width: 100%;
      border: 1px solid rgba(169, 146, 116, .28);
      border-radius: 18px;
      background: rgba(255, 254, 251, .94);
      color: var(--ink);
      padding: 14px 16px;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.75);
      transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
    }}
    textarea {{
      min-height: 220px;
      resize: vertical;
      line-height: 1.9;
    }}
    textarea:focus, input:focus, select:focus {{
      outline: none;
      border-color: rgba(161, 90, 50, .48);
      box-shadow: 0 0 0 4px rgba(161, 90, 50, .08);
      background: #fff;
    }}
    .error {{
      margin-bottom: 16px;
      padding: 12px 14px;
      border: 1px solid #fecdd3;
      border-radius: 14px;
      background: #fff1f2;
      color: #9f1239;
    }}
    .options-row {{
      display: grid;
      grid-template-columns: 1.2fr .8fr 1fr 1fr auto;
      gap: 12px;
      margin-top: 16px;
    }}
    .option-card {{
      padding: 14px;
      border-radius: 18px;
      background: rgba(255,255,255,.72);
      border: 1px solid rgba(217, 204, 184, .78);
    }}
    .option-card.check {{
      display: flex;
      align-items: center;
      gap: 10px;
      justify-content: center;
      min-height: 84px;
    }}
    .option-card.check input {{
      width: auto;
      margin: 0;
      box-shadow: none;
    }}
    .assist-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-top: 14px;
    }}
    .ghost-btn, .prompt-chip {{
      border: 1px solid var(--line-strong);
      background: rgba(255,255,255,.86);
      color: var(--ink);
      border-radius: 999px;
      padding: 11px 14px;
      cursor: pointer;
      text-decoration: none;
      transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
    }}
    .ghost-btn:hover, .prompt-chip:hover {{
      transform: translateY(-1px);
      border-color: rgba(161, 90, 50, .36);
      box-shadow: 0 10px 22px rgba(47, 35, 23, .06);
    }}
    .prompt-grid {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }}
    .prompt-chip:nth-child(3n+1) {{ background: var(--chip-a); }}
    .prompt-chip:nth-child(3n+2) {{ background: var(--chip-b); }}
    .prompt-chip:nth-child(3n+3) {{ background: var(--chip-c); }}
    details {{
      margin-top: 22px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 26px;
      background: rgba(255,255,255,.58);
    }}
    summary {{
      list-style: none;
      cursor: pointer;
      padding: 18px 20px;
      font-weight: 700;
      background: linear-gradient(180deg, rgba(255,255,255,.64) 0%, rgba(245,236,223,.28) 100%);
    }}
    summary::-webkit-details-marker {{ display: none; }}
    .constraint-wrap {{
      padding: 0 20px 22px;
      display: grid;
      gap: 16px;
    }}
    .constraint-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .constraint-card {{
      padding: 18px;
      border-radius: 22px;
      background: rgba(255,255,255,.72);
      border: 1px solid rgba(217, 204, 184, .76);
      display: grid;
      gap: 12px;
    }}
    .constraint-card h3 {{
      margin: 0 0 2px;
      font-size: 18px;
      letter-spacing: -.02em;
    }}
    .constraint-card.full {{
      grid-column: 1 / -1;
    }}
    .mini-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .submit-row {{
      margin-top: 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }}
    .primary-btn {{
      border: 0;
      border-radius: 999px;
      padding: 13px 20px;
      background: linear-gradient(180deg, #cb8758 0%, var(--accent) 100%);
      color: #fff;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 0 16px 28px rgba(161, 90, 50, .18);
    }}
    .footnote {{
      color: var(--muted);
      line-height: 1.8;
      margin-top: 4px;
    }}
    .loading-mask {{
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(18, 24, 31, .26);
      z-index: 999;
    }}
    .loading-mask.visible {{ display: flex; }}
    .loading-card {{
      width: min(420px, calc(100vw - 32px));
      padding: 24px;
      border-radius: 24px;
      border: 1px solid var(--line);
      background: rgba(255, 251, 246, .96);
      box-shadow: var(--shadow);
    }}
    .loading-card strong {{
      display: block;
      font-size: 22px;
      margin-bottom: 8px;
    }}
    .loading-card p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.8;
    }}
    .loading-bar {{
      margin-top: 16px;
      height: 8px;
      border-radius: 999px;
      background: rgba(217, 204, 184, .72);
      overflow: hidden;
    }}
    .loading-bar span {{
      display: block;
      width: 36%;
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #cb8758 0%, var(--accent) 100%);
      animation: progress 1.1s ease-in-out infinite;
    }}
    @keyframes progress {{
      0% {{ transform: translateX(-120%); }}
      100% {{ transform: translateX(360%); }}
    }}
    @media (max-width: 820px) {{
      .page {{ width: min(100vw - 20px, 980px); }}
      .panel-head {{ display: block; }}
      .panel-head p {{ margin-top: 10px; text-align: left; }}
      .options-row, .constraint-grid, .mini-grid, .create-modes {{
        grid-template-columns: 1fr;
      }}
      .hero {{
        padding: 24px;
      }}
      h1 {{
        font-size: 42px;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <div class="masthead">
      <strong>Travel Control Tower</strong>
      <span>Natural language trip planner</span>
    </div>

    <section class="hero">
      <div class="eyebrow">travel planning</div>
      <h1>一句话说需求，系统给你一套可执行旅行方案。</h1>
      <p>首页只负责输入。把出发地、目的地、预算、节奏和硬性约束说清楚，结果页再统一展示总览、甘特图、预算拆分、详细行程和预定事项。</p>
    </section>

    <section class="create-modes" aria-label="选择制定方式">
      <div class="create-mode active"><span>方式一</span><strong>AI 帮我生成</strong><span>说清楚目的地、时间和偏好，由 AI 先生成完整攻略，再继续修改。</span></div>
      <a class="create-mode" href="/manual-plan"><span>方式二</span><strong>我自己制定</strong><span>自己填写每天去哪里和时间，系统负责整理、绘制路线并检查是否绕路。</span></a>
    </section>

    <section class="panel">
      {error_block}
      <div class="panel-head">
        <div>
          <div class="section-kicker">Primary input</div>
          <h2>自然语言需求</h2>
        </div>
      <p>先说清楚你想去哪、玩多久、预算大概多少、偏轻松还是偏满。出发地建议直接填，下面其他字段只是补充约束，不是主输入。</p>
      </div>

      <form id="trip-form" method="post" action="/generate" accept-charset="UTF-8">
        <label for="freeform_request">自然语言需求</label>
        <textarea id="freeform_request" name="freeform_request" required placeholder="例如：下周末从上海去南京两天，预算 2000，想轻松一点，去夫子庙和老门东，住得方便，不要太赶。">{_esc(values.get('freeform_request', ''))}</textarea>

        <div class="assist-row">
          <button id="fill-location-button" type="button" class="ghost-btn">用当前位置填充出发地</button>
          <span id="location-status" data-city="" class="footnote">暂未读取定位</span>
        </div>

        <div class="prompt-grid">{prompt_buttons}</div>

        <div class="options-row">
          <div class="option-card">
            <label for="departure_city">出发地</label>
            <input id="departure_city" name="departure_city" value="{_esc(values.get('departure_city', ''))}" placeholder="如 上海" />
          </div>
          <div class="option-card">
            <label for="traveler_count">人数</label>
            <input id="traveler_count" type="number" min="1" name="traveler_count" value="{_esc(values.get('traveler_count', '1'))}" />
          </div>
          <div class="option-card">
            <label for="budget_per_person">人均预算</label>
            <input id="budget_per_person" type="number" min="0" step="100" name="budget_per_person" value="{_esc(values.get('budget_per_person', ''))}" placeholder="如 3000" />
          </div>
          <div class="option-card">
            <label for="travel_style">节奏</label>
            <select id="travel_style" name="travel_style">
              <option value="relaxed" {"selected" if values.get('travel_style') == 'relaxed' else ""}>松弛</option>
              <option value="balanced" {"selected" if values.get('travel_style') == 'balanced' else ""}>均衡</option>
              <option value="packed" {"selected" if values.get('travel_style') == 'packed' else ""}>偏满</option>
            </select>
          </div>
          <div class="option-card check">
            <input id="enable_live_search" type="checkbox" name="enable_live_search" value="on" {checked} />
            <label for="enable_live_search" style="margin:0;">启用实时机酒搜索</label>
          </div>
        </div>

        <details {constraints_open}>
          <summary>展开补充约束</summary>
          <div class="constraint-wrap">
            <div class="constraint-grid">
              <section class="constraint-card">
                <h3>基础条件</h3>
                <div class="mini-grid">
                  <div>
                    <label for="destination">目的地</label>
                    <input id="destination" name="destination" value="{_esc(values.get('destination', ''))}" placeholder="如 南京" />
                  </div>
                  <div>
                    <label for="start_date">开始日期</label>
                    <input id="start_date" type="date" name="start_date" value="{_esc(values.get('start_date', ''))}" />
                  </div>
                  <div>
                    <label for="end_date">结束日期</label>
                    <input id="end_date" type="date" name="end_date" value="{_esc(values.get('end_date', ''))}" />
                  </div>
                  <div>
                    <label for="scenario_id">场景模板</label>
                    <select id="scenario_id" name="scenario_id">
                      <option value="" {"selected" if not values.get('scenario_id') else ""}>不使用模板</option>
                      <option value="japan_osaka_weekend" {"selected" if values.get('scenario_id') == 'japan_osaka_weekend' else ""}>日本大阪周末 3 天示例</option>
                    </select>
                  </div>
                </div>
              </section>

              <section class="constraint-card">
                <h3>偏好与限制</h3>
                <div>
                  <label for="must_go">必须去的点</label>
                  <textarea id="must_go" name="must_go" placeholder="每行一个">{_esc(values.get('must_go', ''))}</textarea>
                </div>
                <div class="mini-grid">
                  <div>
                    <label for="hotel_preferences">酒店偏好</label>
                    <textarea id="hotel_preferences" name="hotel_preferences" placeholder="每行一个">{_esc(values.get('hotel_preferences', ''))}</textarea>
                  </div>
                  <div>
                    <label for="transport_preferences">交通偏好</label>
                    <textarea id="transport_preferences" name="transport_preferences" placeholder="每行一个">{_esc(values.get('transport_preferences', ''))}</textarea>
                  </div>
                </div>
                <div>
                  <label for="notes">补充备注</label>
                  <textarea id="notes" name="notes" placeholder="例如：不想太赶、每天 10 点后出门、希望购物半天等。">{_esc(values.get('notes', ''))}</textarea>
                </div>
              </section>

              <section class="constraint-card full">
                <h3>已有机酒</h3>
                <div class="mini-grid">
                  <div>
                    <label for="user_hotel_name">已知酒店名称</label>
                    <input id="user_hotel_name" name="user_hotel_name" value="{_esc(values.get('user_hotel_name', ''))}" />
                  </div>
                  <div>
                    <label for="user_hotel_area">酒店区域</label>
                    <input id="user_hotel_area" name="user_hotel_area" value="{_esc(values.get('user_hotel_area', ''))}" />
                  </div>
                  <div>
                    <label for="user_hotel_nightly_price">酒店每晚价格</label>
                    <input id="user_hotel_nightly_price" type="number" min="0" step="1" name="user_hotel_nightly_price" value="{_esc(values.get('user_hotel_nightly_price', ''))}" />
                  </div>
                  <div>
                    <label for="user_hotel_url">酒店链接</label>
                    <input id="user_hotel_url" name="user_hotel_url" value="{_esc(values.get('user_hotel_url', ''))}" />
                  </div>
                  <div>
                    <label for="user_transport_label">已知主交通名称</label>
                    <input id="user_transport_label" name="user_transport_label" value="{_esc(values.get('user_transport_label', ''))}" />
                  </div>
                  <div>
                    <label for="user_transport_category">交通类型</label>
                    <input id="user_transport_category" name="user_transport_category" value="{_esc(values.get('user_transport_category', ''))}" />
                  </div>
                  <div>
                    <label for="user_transport_total_price">交通总价</label>
                    <input id="user_transport_total_price" type="number" min="0" step="1" name="user_transport_total_price" value="{_esc(values.get('user_transport_total_price', ''))}" />
                  </div>
                  <div>
                    <label for="user_transport_depart_at">交通出发时间</label>
                    <input id="user_transport_depart_at" name="user_transport_depart_at" value="{_esc(values.get('user_transport_depart_at', ''))}" />
                  </div>
                  <div>
                    <label for="user_transport_arrive_at">交通到达时间</label>
                    <input id="user_transport_arrive_at" name="user_transport_arrive_at" value="{_esc(values.get('user_transport_arrive_at', ''))}" />
                  </div>
                  <div>
                    <label for="user_arrival_at_destination">到达目的地时间</label>
                    <input id="user_arrival_at_destination" name="user_arrival_at_destination" value="{_esc(values.get('user_arrival_at_destination', ''))}" />
                  </div>
                  <div>
                    <label for="user_return_depart_at">返程出发时间</label>
                    <input id="user_return_depart_at" name="user_return_depart_at" value="{_esc(values.get('user_return_depart_at', ''))}" />
                  </div>
                  <div>
                    <label for="user_transport_url">交通链接</label>
                    <input id="user_transport_url" name="user_transport_url" value="{_esc(values.get('user_transport_url', ''))}" />
                  </div>
                </div>
              </section>
            </div>
          </div>
        </details>

        <div class="submit-row">
          <button id="submit-button" class="primary-btn" type="submit">生成方案</button>
          <a class="ghost-btn" href="/examples/japan_osaka_weekend.preview.html">查看固定示例</a>
        </div>
        <div class="footnote">首页只保留必要输入。预算拆分、路线甘特图、主酒店主交通、候选方案和数据状态都放到结果页统一展示。</div>
      </form>
    </section>
  </div>

  <div id="loading-mask" class="loading-mask" aria-hidden="true">
    <div class="loading-card">
      <strong>正在生成旅行方案</strong>
      <p>系统会先解析你的自然语言需求，再补齐候选池、路线、预算和导出文件。</p>
      <div class="loading-bar"><span></span></div>
    </div>
  </div>

  <script>
    (() => {{
      const form = document.getElementById('trip-form');
      const submitButton = document.getElementById('submit-button');
      const loadingMask = document.getElementById('loading-mask');
      const freeform = document.getElementById('freeform_request');
      const departure = document.getElementById('departure_city');
      const locationButton = document.getElementById('fill-location-button');
      const locationStatus = document.getElementById('location-status');

      const currentDeparture = () => (departure?.value || '').trim() || (locationStatus?.dataset.city || '').trim();

      document.querySelectorAll('[data-template]').forEach((chip) => {{
        chip.addEventListener('click', () => {{
          const template = chip.getAttribute('data-template') || '';
          if (!freeform) return;
          const departureValue = currentDeparture();
          let nextText = template;
          if (departureValue) {{
            nextText = template.replaceAll('{{departure}}', departureValue);
          }} else {{
            nextText = template
              .replace('从{{departure}}出发，', '')
              .replace('从{{departure}}飞', '飞')
              .replaceAll('{{departure}}', '');
            if (locationStatus) {{
              locationStatus.textContent = '建议先填写出发地，附近城市和低价机酒都需要出发地。';
            }}
            departure?.focus();
          }}
          freeform.value = nextText;
          freeform.focus();
        }});
      }});

      const fillLocationByNetwork = async () => {{
        try {{
          locationStatus.textContent = 'GPS 不可用，正在按网络位置识别城市...';
          const resp = await fetch('/api/ip-location', {{ cache: 'no-store' }});
          const payload = await resp.json();
          if (!resp.ok || !payload.city) throw new Error(payload.error || '没有识别到城市');
          if (departure) departure.value = payload.city;
          locationStatus.dataset.city = payload.city;
          locationStatus.textContent = `已按网络位置识别：${{payload.city}}（可手动修改）`;
        }} catch (error) {{
          locationStatus.textContent = `自动定位失败：${{error.message || error}}，请手动填写出发地。`;
        }}
      }};

      if (locationButton && locationStatus && navigator.geolocation) {{
        locationButton.addEventListener('click', () => {{
          locationStatus.textContent = '正在读取定位...';
          navigator.geolocation.getCurrentPosition(async (position) => {{
            try {{
              const resp = await fetch(`/api/reverse-geocode?lat=${{encodeURIComponent(String(position.coords.latitude))}}&lng=${{encodeURIComponent(String(position.coords.longitude))}}`);
              const payload = await resp.json();
              if (!resp.ok) throw new Error(payload.error || '定位失败');
              if (payload.city) {{
                if (departure && !departure.value.trim()) departure.value = payload.city;
                locationStatus.dataset.city = payload.city;
                locationStatus.textContent = `已识别当前位置：${{payload.city}}`;
              }} else {{
                locationStatus.textContent = '未识别到城市，请手动填写。';
              }}
            }} catch (error) {{
              await fillLocationByNetwork();
            }}
          }}, async () => {{
            await fillLocationByNetwork();
          }});
        }});
      }} else if (locationStatus) {{
        locationButton?.addEventListener('click', fillLocationByNetwork);
        locationStatus.textContent = '浏览器 GPS 不可用，可点击后按网络位置识别。';
      }}

      if (form) {{
        form.addEventListener('submit', () => {{
          if (submitButton) submitButton.disabled = true;
          if (loadingMask) loadingMask.classList.add('visible');
        }});
      }}
    }})();
  </script>
</body>
</html>"""
