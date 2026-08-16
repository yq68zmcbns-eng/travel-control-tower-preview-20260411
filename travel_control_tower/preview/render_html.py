from __future__ import annotations

import html
import json
from pathlib import Path


COLORS = {
    "交通": ("#dbeafe", "#1d4ed8"),
    "游玩": ("#dcfce7", "#166534"),
    "餐食": ("#ffedd5", "#9a3412"),
    "住宿": ("#ede9fe", "#6d28d9"),
    "缓冲": ("#e5e7eb", "#374151"),
}

STYLE_LABELS = {
    "relaxed": "松弛",
    "balanced": "均衡",
    "packed": "偏满",
}

REQUEST_MODE_LABELS = {
    "itinerary": "固定日期行程",
    "price_scan": "时间窗口比价",
}

TRACE_MODE_LABELS = {
    "candidate": "实时候选优先",
    "llm": "智能生成",
    "fallback": "基础规划",
}

TRACE_ENGINE_LABELS = {
    "候选池规划器": "自动规划",
    "自动规划": "自动规划",
    "规则规划器": "基础规划",
    "基础规划": "基础规划",
    "LLM planner": "智能规划",
    "Codex planner": "智能规划",
    "智能规划": "智能规划",
}


def _esc(value: object) -> str:
    return html.escape(str(value or ""))


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, list):
        return "、".join(_stringify(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _fmt_money(value: object, currency: str = "CNY") -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "-"
    symbol = "¥" if str(currency).upper() in {"CNY", "JPY"} else f"{currency} "
    return f"{symbol}{amount:,.0f}" if amount >= 100 else f"{symbol}{amount:.1f}".rstrip("0").rstrip(".")


def _has_estimated_price(item: dict | None) -> bool:
    if not item:
        return False
    notes = _stringify(item.get("notes", ""))
    return "价格未直接返回" in notes or "估算" in notes


def _fmt_display_price(item: dict | None, amount_key: str, fallback: str = "暂无结果") -> str:
    if not item:
        return fallback
    try:
        amount = float(item.get(amount_key, 0) or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return fallback
    display = _fmt_money(amount, item.get("currency", "CNY"))
    if _has_estimated_price(item):
        return f"约 {display}（估）"
    return display


def _display_value(key: object, value: object) -> str:
    label = str(key or "").strip().lower()
    text = _stringify(value)
    if not text:
        return ""
    lowered = text.lower()
    if label in {"节奏", "travel_style"}:
        return STYLE_LABELS.get(lowered, text)
    if label in {"需求模式", "request_mode"}:
        return REQUEST_MODE_LABELS.get(lowered, text)
    if label in {"模式", "mode"}:
        return TRACE_MODE_LABELS.get(lowered, text)
    return text


def _clean_display_text(value: object) -> str:
    text = _stringify(value).strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return ""
    return text


def _display_trace_engine(value: object) -> str:
    text = _stringify(value).strip()
    if not text:
        return ""
    return TRACE_ENGINE_LABELS.get(text, text)


def _plan_to_dict(plan: dict | object) -> dict:
    if isinstance(plan, dict):
        return plan
    if hasattr(plan, "to_dict"):
        return plan.to_dict()
    raise TypeError("plan must be dict-like or implement to_dict()")


def _parse_minutes(text: str) -> int | None:
    raw = str(text or "").strip()
    if ":" not in raw:
        return None
    try:
        hour, minute = raw.split(":", 1)
        return int(hour) * 60 + int(minute)
    except ValueError:
        return None


def _overview_metrics(plan: dict) -> str:
    daily_plan = plan.get("daily_plan", []) or []
    snapshot = plan.get("input_snapshot", {}) or {}
    budget = plan.get("budget", {}) or {}
    hotel = plan.get("selected_hotel") or {}
    transport = plan.get("selected_transport") or {}
    cards = [
        ("行程天数", str(len(daily_plan) or "-"), _stringify(snapshot.get("目的地") or snapshot.get("destination") or "")),
        ("出行人数", _stringify(snapshot.get("人数") or snapshot.get("traveler_count") or "-"), "支持按人数拆预算"),
        ("基础预算", _fmt_money(budget.get("fixed_cost_total", 0)), f"人均 {_fmt_money(budget.get('per_person_cost', 0))}"),
        ("主酒店", _stringify(hotel.get("name") or "待补齐"), _fmt_display_price(hotel, "nightly_price")),
        ("主交通", _stringify(transport.get("label") or "待补齐"), _fmt_money(transport.get("total_price", 0), transport.get("currency", "CNY")) if transport else "暂无结果"),
    ]
    return "".join(
        "<article class='metric-card'>"
        f"<div class='metric-label'>{_esc(label)}</div>"
        f"<div class='metric-value'>{_esc(value)}</div>"
        f"<div class='metric-detail'>{_esc(detail)}</div>"
        "</article>"
        for label, value, detail in cards
    )


def _render_budget_summary(budget: dict) -> str:
    rows = []
    for item in (budget.get("breakdown", []) or [])[:6]:
        rows.append(
            "<tr>"
            f"<td>{_esc(_stringify(item.get('category', '')))}</td>"
            f"<td>{_esc(_fmt_money(item.get('total', 0)))}</td>"
            f"<td>{_esc(_stringify(item.get('notes', '')))}</td>"
            "</tr>"
        )
    rows_html = "".join(rows) or "<tr><td colspan='3'>预算明细尚未生成。</td></tr>"
    return (
        "<section>"
        "<div class='budget-top'><div><div class='eyebrow'>预算</div><h2>先看总价，再看拆分</h2></div>"
        f"<div class='budget-total'>{_esc(_fmt_money(budget.get('fixed_cost_total', 0)))}</div></div>"
        "<div class='budget-inline'>"
        f"<div><span>人均</span><strong>{_esc(_fmt_money(budget.get('per_person_cost', 0)))}</strong></div>"
        f"<div><span>可升级项</span><strong>{_esc(_fmt_money(budget.get('optional_upgrade_total', 0)))}</strong></div>"
        "</div>"
        "<table class='budget-table'><thead><tr><th>分类</th><th>金额</th><th>说明</th></tr></thead><tbody>"
        f"{rows_html}</tbody></table></section>"
    )


def _gantt(daily_plan: list[dict]) -> str:
    if not daily_plan:
        return "<div class='empty'>还没有逐日行程。</div>"
    start_axis = 8 * 60
    end_axis = 22 * 60
    empty_block_html = '<div class="empty-inline">当天暂无时间块</div>'
    rows = []
    for day in daily_plan:
        blocks = []
        for item in day.get("items", []) or []:
            start = _parse_minutes(item.get("start_time", ""))
            end = _parse_minutes(item.get("end_time", ""))
            if start is None or end is None or end <= start:
                continue
            left = max(0, min(100, ((start - start_axis) / (end_axis - start_axis)) * 100))
            width = max(3, min(100 - left, ((end - start) / (end_axis - start_axis)) * 100))
            bg, fg = COLORS.get(str(item.get("category", "")), ("#f3f4f6", "#1f2937"))
            blocks.append(
                f"<div class='gantt-block' style='left:{left:.2f}%;width:{width:.2f}%;background:{bg};color:{fg};'>{_esc(item.get('label', ''))}</div>"
            )
        rows.append(
            "<div class='gantt-row'>"
            f"<div class='gantt-label'><strong>D{int(day.get('day_index', 0) or 0)}</strong><span>{_esc(day.get('theme', ''))}</span></div>"
            f"<div class='gantt-track'>{''.join(blocks) or empty_block_html}</div>"
            "</div>"
        )
    return "".join(rows)


def _day_cards(daily_plan: list[dict]) -> str:
    if not daily_plan:
        return "<div class='empty'>暂无摘要。</div>"
    cards = []
    for day in daily_plan:
        cards.append(
            "<article class='day-card'>"
            f"<div class='pill'>D{int(day.get('day_index', 0) or 0)}</div>"
            f"<h3>{_esc(day.get('theme', ''))}</h3>"
            f"<div class='sub'>{_esc(day.get('date', ''))} · {_esc(_fmt_money(day.get('estimated_cost_total', 0)))}</div>"
            f"<p>{_esc(day.get('why_this_day', '') or day.get('transport_strategy', '') or '按主线路执行')}</p>"
            "</article>"
        )
    return "".join(cards)


def _detail_days(daily_plan: list[dict]) -> str:
    if not daily_plan:
        return "<section class='panel'><div class='empty'>暂无日程明细。</div></section>"
    empty_item_html = '<div class="empty">当天没有项目。</div>'
    days = []
    for day in daily_plan:
        items = []
        for item in day.get("items", []) or []:
            bg, fg = COLORS.get(str(item.get("category", "")), ("#f3f4f6", "#1f2937"))
            route = ""
            route_mode = _stringify(item.get("route_mode_label") or item.get("route_mode"))
            if route_mode:
                route_origin = _stringify(item.get("route_origin", ""))
                route_destination = _stringify(item.get("route_destination", ""))
                route_path = ""
                if route_origin and route_destination:
                    route_path = f"<span>{_esc(route_origin)} → {_esc(route_destination)}</span>"
                route = (
                    "<div class='route-box'>"
                    f"<strong>{_esc(route_mode)}</strong>"
                    f"{route_path}"
                    f"<span>{_esc(item.get('route_summary', ''))}</span>"
                    "</div>"
                )
            items.append(
                "<article class='timeline-item'>"
                f"<div class='time'>{_esc(item.get('start_time', ''))}<span>{_esc(item.get('end_time', ''))}</span></div>"
                "<div class='body'>"
                f"<div class='tag' style='background:{bg};color:{fg};'>{_esc(item.get('category', ''))}</div>"
                f"<h4>{_esc(item.get('label', ''))}</h4>"
                f"<p>{_esc(item.get('notes', ''))}</p>"
                f"{route}</div></article>"
            )
        days.append(
            "<section class='panel day-detail'>"
            f"<div class='pill'>D{int(day.get('day_index', 0) or 0)}</div>"
            f"<h3>{_esc(day.get('theme', ''))}</h3>"
            f"<div class='sub'>{_esc(day.get('date', ''))}</div>"
            "<div class='meta-grid'>"
            f"<div><span>交通策略</span><strong>{_esc(day.get('transport_strategy', '') or '按主线路推进')}</strong></div>"
            f"<div><span>餐饮策略</span><strong>{_esc(day.get('meal_strategy', '') or '就近安排')}</strong></div>"
            f"<div><span>快进替代</span><strong>{_esc(day.get('fallback_if_fast', '') or '灵活补点')}</strong></div>"
            f"<div><span>疲劳替代</span><strong>{_esc(day.get('fallback_if_tired', '') or '压缩外围活动')}</strong></div>"
            "</div>"
            f"<div class='timeline'>{''.join(items) or empty_item_html}</div>"
            "</section>"
        )
    return "".join(days)


def _candidate_list(items: list[dict], kind: str) -> str:
    if not items:
        return "<div class='empty'>暂无候选。</div>"
    cards = []
    for item in items[:6]:
        if kind == "hotel":
            title = _stringify(item.get("name", ""))
            detail = _stringify(item.get("area", ""))
            price = _fmt_display_price(item, "nightly_price")
            note = _stringify(item.get("notes", ""))
        elif kind == "transport":
            title = _stringify(item.get("label", ""))
            detail = _stringify(item.get("category", ""))
            price = _fmt_money(item.get("total_price", 0), item.get("currency", "CNY"))
            note = f"{_stringify(item.get('depart_at', ''))} → {_stringify(item.get('arrive_at', ''))}"
        else:
            title = _stringify(item.get("name", ""))
            detail = _clean_display_text(item.get("category", ""))
            price = ""
            note = _stringify(item.get("notes", "") or item.get("address", ""))
        booking_url = _stringify(item.get("booking_url") or item.get("url") or "").strip()
        action_label = "去飞猪查看酒店" if kind == "hotel" else "去飞猪查看班次" if kind == "transport" else "查看详情"
        action = (
            f"<a class='booking-link' href='{_esc(booking_url)}' target='_blank' rel='noopener noreferrer'>{action_label}</a>"
            if booking_url else "<span class='link-missing'>当前结果没有可用预订链接</span>"
        )
        cards.append(f"<article class='side-card'><strong>{_esc(title)}</strong><div>{_esc(detail)}</div><div>{_esc(price)}</div><p>{_esc(note)}</p>{action}</article>")
    return "".join(cards)


def _kv(data: dict) -> str:
    if not data:
        return "<div class='empty'>暂无内容。</div>"
    rows = []
    for k, v in data.items():
        display = _display_value(k, v)
        if not display:
            continue
        rows.append(f"<div class='kv'><span>{_esc(k)}</span><strong>{_esc(display)}</strong></div>")
    return "".join(rows) or "<div class='empty'>暂无内容。</div>"


def render_plan_html(plan: dict | object) -> str:
    plan = _plan_to_dict(plan)
    overview = plan.get("overview", {}) or {}
    budget = plan.get("budget", {}) or {}
    daily_plan = plan.get("daily_plan", []) or []
    hotel = plan.get("selected_hotel") or {}
    transport = plan.get("selected_transport") or {}
    booking_items = plan.get("booking_items", []) or []
    assumptions = plan.get("assumptions", []) or []
    provider_statuses = plan.get("provider_statuses", []) or []
    open_questions = plan.get("open_questions", []) or []
    trace = plan.get("planning_trace") or {}
    request_context = plan.get("request_context", {}) or {}
    price_scan_summary = plan.get("price_scan_summary") or {}
    price_scan_candidates = plan.get("price_scan_candidates", []) or []

    assumptions_html = "".join(f"<li>{_esc(_stringify(item))}</li>" for item in assumptions) or "<li>暂无额外假设。</li>"
    booking_cards: list[str] = []
    for item in booking_items:
        url = _stringify(item.get("url", "")).strip()
        link_html = (
            f"<a class='link' href='{_esc(url)}' target='_blank' rel='noreferrer'>打开链接</a>"
            if url
            else ""
        )
        booking_cards.append(
            "<article class='side-card'>"
            f"<strong>{_esc(item.get('name', ''))}</strong>"
            f"<div>{_esc(item.get('category', ''))} · {_esc(item.get('timing', ''))}</div>"
            f"<p>{_esc(item.get('notes', '') or item.get('why_now', ''))}</p>"
            f"{link_html}"
            "</article>"
        )
    booking_html = "".join(booking_cards) or "<div class='empty'>暂无预定事项。</div>"
    status_html = "".join(
        f"<article class='side-card'><strong>{_esc(item.get('name', ''))}</strong><div>{_esc(item.get('status', ''))}</div><p>{_esc(item.get('details', ''))}</p></article>"
        for item in provider_statuses
    ) or "<div class='empty'>暂无数据源状态。</div>"
    price_scan_html = ""
    if price_scan_summary or price_scan_candidates:
        candidate_html = "".join(
            f"<article class='side-card'><strong>{_esc(item.get('label', ''))}</strong><div>{_esc(_stringify(item.get('trip_start_date', '')))} - {_esc(_stringify(item.get('trip_end_date', '')))}</div><p>{_esc(_fmt_money(item.get('total_price', 0)))}</p>" + (f"<a class='booking-link' href='{_esc(_stringify(item.get('booking_url', '')))}' target='_blank' rel='noopener noreferrer'>去飞猪查看</a>" if _stringify(item.get('booking_url', '')).strip() else "") + "</article>"
            for item in price_scan_candidates[:4]
        ) or "<div class='empty'>暂无低价窗口候选。</div>"
        price_scan_html = (
            "<section class='panel'><h3>低价窗口结果</h3>"
            f"<div class='kv-grid'>{_kv(price_scan_summary)}</div>"
            f"<div class='side-list' style='margin-top:14px'>{candidate_html}</div></section>"
        )
    request_context_html = ""
    if request_context:
        constraint_list = "".join(
            f"<li>{_esc(_stringify(item))}</li>" for item in (request_context.get("manual_constraints", []) or [])
        ) or "<li>暂无手动补充约束。</li>"
        request_context_html = (
            "<section class='panel'><h3>本次输入来源</h3>"
            f"<div class='kv-grid'><div class='kv'><span>自然语言原文</span><strong>{_esc(_stringify(request_context.get('natural_language_request', '')) or '未提供')}</strong></div></div>"
            f"<div style='margin-top:14px'><div class='sub' style='margin-bottom:8px'>手动补充约束</div><ul class='clean-list'>{constraint_list}</ul></div>"
            "</section>"
        )
    trace_html = ""
    if trace:
        trace_html = (
            "<section class='panel'><h3>生成方式</h3><div class='kv-grid'>"
            f"<div class='kv'><span>引擎</span><strong>{_esc(_display_trace_engine(trace.get('engine', '')))}</strong></div>"
            f"<div class='kv'><span>模式</span><strong>{_esc(_display_value('mode', trace.get('mode', '')))}</strong></div>"
            f"<div class='kv'><span>模型</span><strong>{_esc(_stringify(trace.get('model', '')) or '-')}</strong></div>"
            f"<div class='kv'><span>回退</span><strong>{_esc('是' if trace.get('used_fallback') else '否')}</strong></div>"
            "</div>"
            f"<p class='trace'>{_esc(_stringify(trace.get('details', '')))}</p></section>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(_stringify(overview.get("title", "旅行方案")))} · Travel Control Tower</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Noto+Serif+SC:wght@500;600;700&display=swap');
    :root{{--bg:#f6f1e8;--panel:#fffdfa;--line:#e6dccf;--ink:#14263a;--muted:#6b6258;--brand:#1f3b57;--shadow:0 18px 40px rgba(36,28,20,.08);--r:24px;}}
    *{{box-sizing:border-box}} body{{margin:0;font-family:"Manrope","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);background:linear-gradient(rgba(117,96,70,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(117,96,70,.04) 1px,transparent 1px),linear-gradient(180deg,#fbf8f3 0%,var(--bg) 100%);background-size:24px 24px,24px 24px,auto}}
    .page{{width:min(1320px,calc(100vw - 36px));margin:0 auto;padding:24px 0 56px}}
    .hero,.overview,.layout{{display:grid;gap:20px}} .hero{{grid-template-columns:minmax(0,1.45fr) 360px}} .overview{{grid-template-columns:minmax(0,1.6fr) 400px;margin-top:20px}} .layout{{grid-template-columns:minmax(0,1.7fr) 360px;margin-top:20px;align-items:start}}
    .panel,.hero-main,.hero-side .side-card{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow)}} .hero-main,.panel{{padding:24px}} .hero-main{{position:relative;overflow:hidden}} .hero-main:before{{content:"";position:absolute;inset:0 0 auto 0;height:5px;background:linear-gradient(90deg,#d29764,#5f7b90)}}
    .eyebrow,.pill,.tag,.booking-top span{{display:inline-flex;padding:6px 10px;border-radius:999px;font-size:12px;font-weight:700}} .eyebrow{{background:#f4ede3;color:#8a4d2f;letter-spacing:.14em;text-transform:uppercase;border:1px solid #ecd9c4}} .pill,.booking-top span{{background:var(--brand);color:#fff}}
    h1{{margin:16px 0 10px;font-family:"Noto Serif SC","Songti SC",serif;font-size:clamp(34px,4vw,56px);line-height:1.12}} h2{{margin:0;font-size:26px}} h3{{margin:12px 0 8px;font-size:20px}} h4{{margin:0 0 8px;font-size:18px}}
    .summary,.sub,p,.route-box span,.trace{{margin:0;color:var(--muted);line-height:1.8}} .summary{{font-size:15px}} .sub{{font-size:13px}} .section-title{{display:block;margin-bottom:10px;font-size:12px;letter-spacing:.12em;color:#7d7060;text-transform:uppercase}} .metrics,.budget-inline,.meta-grid,.kv-grid{{display:grid;gap:12px}} .metrics{{grid-template-columns:repeat(5,minmax(0,1fr));margin-top:24px}} .budget-inline,.meta-grid,.kv-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}
    .metric-card,.day-card,.meta-grid>div,.side-card,.kv{{background:#fbf7f1;border:1px solid var(--line);border-radius:18px;padding:16px}} .metric-label,.kv span{{font-size:12px;color:#7d7060}} .metric-value,.price,.budget-total{{font-size:26px;font-weight:800;color:var(--brand)}} .metric-detail{{margin-top:8px;font-size:12px;color:var(--muted)}}
    .hero-side,.stack,.day-grid,.timeline,.side-list{{display:grid;gap:14px}} .day-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}
    .gantt-row{{display:grid;grid-template-columns:210px minmax(0,1fr);gap:14px;padding:12px 0;border-top:1px solid #efe7dc}} .gantt-row:first-child,.timeline-item:first-child{{border-top:0;padding-top:0}} .gantt-label strong{{display:block}} .gantt-track{{position:relative;min-height:48px;border-radius:16px;background:#f8f2e8;border:1px solid var(--line);overflow:hidden}} .gantt-block{{position:absolute;top:8px;bottom:8px;border-radius:12px;padding:0 10px;display:flex;align-items:center;font-size:12px;font-weight:700;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}} .empty-inline{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:13px}}
    .budget-top{{display:flex;justify-content:space-between;gap:12px;align-items:end}} .budget-table{{width:100%;border-collapse:collapse;font-size:13px}} .budget-table th,.budget-table td{{padding:10px 0;text-align:left;border-top:1px solid #efe7dc;vertical-align:top}}
    .timeline-item{{display:grid;grid-template-columns:90px minmax(0,1fr);gap:14px;padding-top:14px;border-top:1px solid #efe7dc}} .time{{font-size:16px;font-weight:800}} .time span{{display:block;margin-top:6px;font-size:12px;color:var(--muted);font-weight:600}} .tag{{margin-bottom:10px}} .route-box{{display:grid;gap:4px;margin-top:10px;padding:12px 14px;border-radius:14px;background:#f8f2e8;border:1px solid var(--line)}} .route-box strong{{font-size:13px;color:var(--brand)}}
    details.panel>summary{{cursor:pointer;list-style:none;font-size:20px;font-weight:800}} details.panel>summary::-webkit-details-marker{{display:none}} .booking-top{{display:flex;justify-content:space-between;gap:12px;align-items:center}} .link,.booking-link{{display:inline-flex;margin-top:10px;padding:10px 14px;border-radius:12px;background:var(--brand);color:#fff;text-decoration:none;font-weight:700}} .link-missing{{display:block;margin-top:10px;color:var(--muted);font-size:12px}} .empty{{padding:16px;border-radius:16px;background:#f8f2e8;border:1px dashed #e3d7c8;color:var(--muted)}}
    @media (max-width:1180px){{.hero,.overview,.layout,.day-grid{{grid-template-columns:1fr}} .metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
    @media (max-width:820px){{.page{{width:min(100vw - 18px,1320px)}} .metrics,.budget-inline,.meta-grid,.kv-grid,.gantt-row,.timeline-item{{grid-template-columns:1fr}} .budget-top,.booking-top{{align-items:flex-start;flex-direction:column}}}}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <article class="hero-main">
        <div class="eyebrow">travel plan / overview first</div>
        <h1>{_esc(_stringify(overview.get("title", "旅行方案")))}</h1>
        <p class="summary">{_esc(_stringify(overview.get("summary", "")) or "先看总览、预算和主线路，再决定是否展开细节。")}</p>
        <div class="metrics">
          {_overview_metrics(plan)}
        </div>
      </article>
      <div class="hero-side">
        <div class="section-title">当前主选择</div>
        <article class="side-card"><div class="eyebrow">主酒店</div><h3>{_esc(_stringify(hotel.get("name", "待补齐")))}</h3><div class="sub">{_esc(_stringify(hotel.get("area", "")) or "位置待确认")}</div><div class="price">{_esc(_fmt_display_price(hotel, "nightly_price"))}</div><p>{_esc(_stringify(hotel.get("notes", "")))}</p></article>
        <article class="side-card"><div class="eyebrow">主交通</div><h3>{_esc(_stringify(transport.get("label", "待补齐")))}</h3><div class="sub">{_esc(_stringify(transport.get("category", "")) or "交通方式待确认")}</div><div class="price">{_esc(_fmt_money(transport.get("total_price", 0), transport.get("currency", "CNY")))}</div><p>{_esc(_stringify(transport.get("depart_at", "")))} {_esc(_stringify(transport.get("arrive_at", "")))}</p></article>
      </div>
    </section>

    <section class="overview">
      <section class="panel">
        <div class="eyebrow">甘特图</div>
        <h2>先看每天节奏和时间分布</h2>
        {_gantt(daily_plan)}
      </section>
      <section class="panel">
        {_render_budget_summary(budget)}
      </section>
    </section>

    <section class="panel" style="margin-top:20px">
      <div class="eyebrow">主线路</div>
      <h2>逐日摘要</h2>
      <div class="day-grid">{_day_cards(daily_plan)}</div>
    </section>

    <section class="layout">
      <section class="stack">
        {_detail_days(daily_plan)}
      </section>
      <aside class="stack">
        <details class="panel" open>
          <summary>预定事项</summary>
          <div class="side-list" style="margin-top:14px">{booking_html}</div>
        </details>
        <details class="panel" open>
          <summary>当前假设</summary>
          <ul class="clean-list">{assumptions_html}</ul>
        </details>
        <details class="panel">
          <summary>酒店候选</summary>
          <div class="side-list" style="margin-top:14px">{_candidate_list(plan.get("hotel_candidates", []) or [], "hotel")}</div>
        </details>
        <details class="panel">
          <summary>交通候选</summary>
          <div class="side-list" style="margin-top:14px">{_candidate_list(plan.get("transport_candidates", []) or [], "transport")}</div>
        </details>
        <details class="panel">
          <summary>景点候选</summary>
          <div class="side-list" style="margin-top:14px">{_candidate_list(plan.get("poi_candidates", []) or [], "poi")}</div>
        </details>
        <details class="panel">
          <summary>数据状态</summary>
          <div class="side-list" style="margin-top:14px">{status_html}</div>
        </details>
        {trace_html}
        {price_scan_html}
        {request_context_html}
        <section class="panel"><h3>输入快照</h3><div class="kv-grid">{_kv(plan.get("input_snapshot", {}) or {})}</div></section>
        <section class="panel"><h3>待确认问题</h3><ul class="clean-list">{''.join(f'<li>{_esc(_stringify(item))}</li>' for item in open_questions) or '<li>当前没有待确认问题。</li>'}</ul></section>
      </aside>
    </section>
  </main>
</body>
</html>"""


def render_plan_file(input_path: Path, output_path: Path) -> Path:
    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    html_text = render_plan_html(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return output_path
