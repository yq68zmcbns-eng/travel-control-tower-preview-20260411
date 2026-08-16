from __future__ import annotations

import html
from datetime import datetime


def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _shell(title: str, body: str, job_id: str = "") -> str:
    back = f"/results/{job_id}" if job_id else "/"
    nav = (
        f"<nav class='nav'><a href='/trips'>行程</a><a href='{back}' class='active'>攻略</a><a href='/jobs/{_e(job_id)}/orders'>订单</a><a href='/jobs/{_e(job_id)}/edit'>编辑</a></nav>"
        if job_id
        else "<nav class='nav'><a href='/trips' class='active'>我的旅行</a><a href='/'>新建攻略</a><a href='/latest/html'>最新攻略</a><a href='/latest/excel'>导出</a></nav>"
    )
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_e(title)} · 旅行工作台</title><style>
:root{{--bg:#f5f7fb;--paper:#fff;--ink:#172033;--muted:#667085;--line:#dfe5ee;--brand:#245b78;--accent:#e66a3b;--soft:#eaf4f8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
.page{{width:min(920px,calc(100% - 24px));margin:auto;padding:18px 0 96px}}.top{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}}.top a{{color:var(--brand);font-weight:700;text-decoration:none;padding:12px}}
.hero,.card{{background:var(--paper);border:1px solid var(--line);border-radius:22px;box-shadow:0 12px 30px rgba(27,48,72,.06)}}.hero{{padding:24px;background:linear-gradient(135deg,#e6f2ff,#edf9f6)}}.card{{padding:20px;margin-top:14px}}h1{{font-size:30px;margin:0 0 8px}}h2{{font-size:20px;margin:0 0 14px}}p{{color:var(--muted);line-height:1.7}}label{{display:grid;gap:7px;margin:14px 0;font-weight:700}}input,textarea,select{{width:100%;min-height:46px;border:1px solid #cbd5e1;border-radius:12px;padding:11px 13px;font:inherit;background:#fff}}textarea{{min-height:96px;resize:vertical}}button,.primary{{display:inline-flex;align-items:center;justify-content:center;min-height:46px;border:0;border-radius:13px;padding:0 18px;background:var(--brand);color:#fff;font-weight:800;text-decoration:none;cursor:pointer}}.secondary{{background:#fff;color:var(--brand);border:1px solid var(--line)}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.day{{border-left:4px solid #51a3a3}}.item{{padding:14px;background:#f8fafc;border:1px solid var(--line);border-radius:14px;margin-top:10px}}.actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}}.nav{{position:fixed;left:50%;bottom:max(10px,env(safe-area-inset-bottom));transform:translateX(-50%);width:min(680px,calc(100% - 20px));display:grid;grid-template-columns:repeat(4,1fr);padding:8px;background:rgba(255,255,255,.96);border:1px solid var(--line);border-radius:20px;box-shadow:0 14px 38px rgba(28,47,66,.16);z-index:10}}.nav a{{min-height:48px;display:flex;align-items:center;justify-content:center;color:var(--muted);text-decoration:none;font-size:13px;font-weight:700;border-radius:13px}}.nav a.active{{background:var(--soft);color:var(--brand)}}.metric{{font-size:28px;font-weight:850;color:var(--brand)}}.muted{{color:var(--muted)}}
@media(max-width:640px){{.grid{{grid-template-columns:1fr}}.page{{width:min(100% - 16px,920px)}}.hero,.card{{border-radius:18px;padding:17px}}h1{{font-size:26px}}}}
</style></head><body><main class='page'><div class='top'><a href='{back}'>‹ 返回</a><strong>旅行工作台</strong><a href='/trips'>全部行程</a></div>{body}</main>
{nav}</body></html>"""


def render_edit_page(plan: dict, job_id: str, message: str = "") -> str:
    overview = plan.get("overview") or {}
    days = []
    for di, day in enumerate(plan.get("daily_plan") or []):
        items = []
        for ii, item in enumerate(day.get("items") or []):
            items.append(f"""<div class='item'><div class='grid'><label>开始时间<input name='d{di}_i{ii}_start' value='{_e(item.get('start_time'))}'></label><label>结束时间<input name='d{di}_i{ii}_end' value='{_e(item.get('end_time'))}'></label></div><label>项目名称<input name='d{di}_i{ii}_label' value='{_e(item.get('label'))}'></label><label>说明<textarea name='d{di}_i{ii}_notes'>{_e(item.get('notes'))}</textarea></label></div>""")
        days.append(f"""<section class='card day'><h2>第 {di + 1} 天</h2><label>日期<input type='date' name='d{di}_date' value='{_e(day.get('date'))}'></label><label>当天主题<input name='d{di}_theme' value='{_e(day.get('theme'))}'></label><label>当天说明<textarea name='d{di}_why'>{_e(day.get('why_this_day'))}</textarea></label>{''.join(items)}</section>""")
    notice = f"<p style='color:#18794e'>{_e(message)}</p>" if message else ""
    body = f"""<section class='hero'><h1>编辑旅行攻略</h1><p>可以直接修改标题、每天主题、时间和活动内容。保存后结果页与 Excel 会重新生成。</p>{notice}</section><form method='post' action='/jobs/{_e(job_id)}/edit'><section class='card'><label>攻略标题<input name='title' value='{_e(overview.get('title'))}' required></label><label>攻略摘要<textarea name='summary'>{_e(overview.get('summary'))}</textarea></label></section>{''.join(days)}<div class='actions'><button type='submit'>保存修改</button><a class='primary secondary' href='/results/{_e(job_id)}'>取消</a></div></form>"""
    return _shell("编辑攻略", body, job_id)


def render_revise_page(job, plan: dict) -> str:
    original = str((plan.get("request_context") or {}).get("natural_language_request") or job.fields.get("freeform_request") or "")
    body = f"""<section class='hero'><h1>和 AI 继续完善</h1><p>告诉它想改什么。系统会保留原需求，再生成一个新版本，旧版本不会被覆盖。</p></section><section class='card'><p><strong>原需求</strong><br>{_e(original)}</p><form method='post' action='/jobs/{_e(job.job_id)}/revise'><label>这次想怎么改<textarea name='message' required placeholder='例如：第二天不要去太远，把博物馆换成适合雨天的活动，酒店附近多安排两家餐厅。'></textarea></label><button type='submit'>生成新版本</button></form></section>"""
    return _shell("AI 修改攻略", body, job.job_id)


def render_manual_plan_page(values: dict[str, str] | None = None, error: str = "") -> str:
    values = values or {}
    notice = f"<section class='card' style='border-color:#ef9a9a;color:#9f1239'>{_e(error)}</section>" if error else ""
    example = """第1天
09:00-11:00 西湖
11:30-12:30 湖滨午餐
14:00-17:00 灵隐寺

第2天
09:30-11:30 中国美术学院象山校区
14:00-17:00 宋城"""
    body = f"""<section class='hero'><h1>自己制定旅行计划</h1><p>把每天想去的地点按顺序写下来。保存后会生成正式攻略，并用高德地图检查地点距离和折返情况。</p></section>{notice}<section class='card'><form method='post' action='/manual-plan'><label>计划名称<input name='title' value='{_e(values.get('title'))}' placeholder='例如：杭州三日慢游'></label><div class='grid'><label>目的地<input name='destination' value='{_e(values.get('destination'))}' required placeholder='例如：杭州'></label><label>开始日期<input type='date' name='start_date' value='{_e(values.get('start_date'))}' required></label></div><label>每天的安排<textarea name='schedule_text' required style='min-height:300px' placeholder='{_e(example)}'>{_e(values.get('schedule_text'))}</textarea></label><p>写法很简单：先写“第1天”，下面每行写“开始时间-结束时间 地点”。备注可以接在地点后面。暂时不确定时间时，也可以只写地点。</p><div class='actions'><button type='submit'>保存并生成路线图</button><a class='primary secondary' href='/'>改用 AI 生成</a></div></form></section>"""
    return _shell("自己制定计划", body)


def render_orders_page(job_id: str, orders: list[dict]) -> str:
    total = sum(float(item.get("amount") or 0) for item in orders)
    cards = "".join(f"""<article class='card'><div class='grid'><div><strong>{_e(item.get('name'))}</strong><p>{_e(item.get('category'))} · {_e(item.get('date'))}</p></div><div><div class='metric'>¥{float(item.get('amount') or 0):,.0f}</div><div class='muted'>{_e(item.get('confirmation'))}</div></div></div>{f"<a class='primary secondary' href='{_e(item.get('url'))}' target='_blank' rel='noopener noreferrer'>打开订单</a>" if item.get('url') else ''}</article>""" for item in orders)
    empty = "<section class='card'><p>还没有订单。可以录入机票、火车、酒店、门票和租车信息。</p></section>" if not cards else cards
    body = f"""<section class='hero'><h1>订单与凭证</h1><p>把旅行订单集中放在一处，旅行中不用反复翻邮件和聊天记录。</p><div class='metric'>¥{total:,.0f}</div><div class='muted'>已录入订单金额</div></section>{empty}<section class='card'><h2>添加订单</h2><form method='post' action='/jobs/{_e(job_id)}/orders'><div class='grid'><label>类别<select name='category'><option>机票</option><option>火车</option><option>酒店</option><option>门票</option><option>租车</option><option>其他</option></select></label><label>日期<input type='date' name='date'></label></div><label>订单名称<input name='name' required placeholder='例如：上海虹桥至杭州东 G7311'></label><div class='grid'><label>金额<input type='number' step='0.01' min='0' name='amount'></label><label>订单号<input name='confirmation'></label></div><label>订单链接<input type='url' name='url' placeholder='https://'></label><label>备注<textarea name='notes' placeholder='座位、入住人、退改规则、取票方式等'></textarea></label><button type='submit'>保存订单</button></form></section>"""
    return _shell("订单管理", body, job_id)


def render_trips_page(items: list[tuple[object, dict]]) -> str:
    cards = []
    cities = set()
    days = 0
    for job, plan in items:
        overview = plan.get("overview") or {}
        daily = plan.get("daily_plan") or []
        snapshot = plan.get("input_snapshot") or {}
        city = str(snapshot.get("目的地") or snapshot.get("destination") or "").strip()
        if city: cities.add(city)
        days += len(daily)
        cards.append(f"""<article class='card'><h2>{_e(overview.get('title') or '未命名行程')}</h2><p>{_e(overview.get('summary'))}</p><div class='actions'><a class='primary' href='/results/{_e(job.job_id)}'>查看攻略</a><a class='primary secondary' href='/jobs/{_e(job.job_id)}/orders'>订单</a></div></article>""")
    body = f"""<section class='hero'><h1>我的旅行</h1><p>集中查看已生成的攻略、计划和订单。</p><div class='grid'><div><div class='metric'>{len(items)}</div><div class='muted'>旅行计划</div></div><div><div class='metric'>{len(cities)}</div><div class='muted'>目的地</div></div><div><div class='metric'>{days}</div><div class='muted'>计划天数</div></div></div></section>{''.join(cards) or '<section class="card"><p>还没有行程，先生成一份攻略吧。</p></section>'}<div class='actions'><a class='primary' href='/'>新建旅行计划</a></div>"""
    return _shell("我的旅行", body)
