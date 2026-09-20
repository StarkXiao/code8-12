"""对账单渲染：纯文本（终端/打印）与 HTML（浏览器查看/打印签字）."""

from __future__ import annotations

import html
import unicodedata

from .services import fmt_duration, fmt_yuan, now_str


def _dw(s: str) -> int:
    """显示宽度（中文按 2 列计）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, width: int) -> str:
    s = str(s)
    return s + " " * max(0, width - _dw(s))


def pad_left(s, width: int) -> str:
    s = str(s)
    return " " * max(0, width - _dw(s)) + s


# ---------------------------------------------------------------- 文本版

def render_text(detail: dict) -> str:
    st, voy, items = detail["statement"], detail["voyage"], detail["items"]
    W = 76
    line = "=" * W
    thin = "-" * W
    out = [
        line,
        "港口岸电使用对账单".center(W - 8),
        line,
        f"单号：{st['statement_no']}"
        f"    状态：{detail['status_label']}"
        f"    版本：V{st['version']}",
        f"港口：{detail['port_name']}",
        thin,
        f"船名：{voy['vessel_name']}    IMO：{voy['imo'] or '-'}    船公司：{voy['company'] or '-'}",
        f"航次：{voy['voyage_no']}    泊位：{voy['berth'] or '-'}",
        f"到泊：{voy['arrived_at'] or '-'}    离泊：{voy['departed_at'] or '-'}",
        thin,
        "接电明细：",
        pad("序号", 5) + pad("电表", 8) + pad("接电时间", 18) + pad("断电时间", 18)
        + pad("时长", 11) + pad_left("电量(kWh)", 11) + pad_left("金额(元)", 11),
    ]
    for i, it in enumerate(items, 1):
        out.append(
            pad(i, 5) + pad(it["meter_no"] or "-", 8)
            + pad(it["connect_at"][:16], 18) + pad(it["disconnect_at"][:16], 18)
            + pad(fmt_duration(it["duration_min"]), 11)
            + pad_left(f"{it['kwh']:,.2f}", 11)
            + pad_left(fmt_yuan(it["amount_cents"]), 11)
        )
    out += [
        thin,
        f"合计接电时长：{fmt_duration(st['total_minutes'])}"
        f"    合计用电量：{st['total_kwh']:,.2f} kWh",
    ]
    prices = sorted({(it["energy_price"], it["service_price"]) for it in items})
    if len(prices) == 1:
        ep, sp = prices[0]
        out.append(f"电费单价：{ep:.4f} 元/kWh    服务费单价：{sp:.4f} 元/kWh")
    out += [
        f"电费：{fmt_yuan(st['energy_cents'])} 元"
        f"    服务费：{fmt_yuan(st['service_cents'])} 元",
        f"应付合计：{fmt_yuan(st['total_cents'])} 元",
        thin,
        f"出具时间：{st['issued_at'] or '-'}",
        f"船方确认：{st['ship_confirmer'] or '-'}  {st['ship_confirmed_at'] or ''}",
        f"港方确认：{st['port_confirmer'] or '-'}  {st['port_confirmed_at'] or ''}",
    ]
    if st["dispute_reason"]:
        out.append(f"异议说明：{st['dispute_reason']}")
    out += [
        thin,
        "船方签字：__________________  日期：____________",
        "港方签字：__________________  日期：____________",
        line,
    ]
    return "\n".join(out)


# ---------------------------------------------------------------- HTML 版

_CSS = """
* { box-sizing: border-box; }
body { font-family: "PingFang SC", "Microsoft YaHei", sans-serif; color: #1a1a1a;
       background: #f0f2f5; margin: 0; padding: 24px; }
.sheet { max-width: 900px; margin: 0 auto; background: #fff; padding: 40px 48px;
         box-shadow: 0 2px 12px rgba(0,0,0,.08); }
h1 { text-align: center; font-size: 24px; letter-spacing: 6px; margin: 0 0 4px; }
.sub { text-align: center; color: #888; font-size: 12px; margin-bottom: 20px; }
.meta { display: flex; justify-content: space-between; align-items: center;
        border-top: 3px double #333; border-bottom: 1px solid #333;
        padding: 8px 2px; margin-bottom: 16px; font-size: 14px; }
.badge { display: inline-block; padding: 2px 12px; border-radius: 12px;
         font-size: 13px; color: #fff; }
.badge.draft { background: #8c8c8c; } .badge.issued { background: #fa8c16; }
.badge.ship_confirmed { background: #1677ff; } .badge.disputed { background: #f5222d; }
.badge.closed { background: #52c41a; } .badge.cancelled { background: #bfbfbf; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
.info td { padding: 5px 8px; } .info td.k { color: #666; width: 90px; }
.items { margin-top: 14px; }
.items th, .items td { border: 1px solid #999; padding: 7px 8px; text-align: center; }
.items th { background: #f5f5f5; }
.items td.num { text-align: right; font-variant-numeric: tabular-nums; }
.totals { margin-top: 14px; background: #fafafa; border: 1px solid #999; }
.totals td { padding: 8px 12px; font-size: 14px; }
.totals .grand { font-size: 17px; font-weight: 700; color: #c41d7f; }
.confirm { margin-top: 18px; font-size: 14px; line-height: 1.9; }
.dispute { color: #f5222d; }
.sign { display: flex; justify-content: space-between; margin-top: 36px; font-size: 14px; }
.sign .box { width: 45%; border-top: 1px solid #333; padding-top: 6px; color: #555; }
.foot { margin-top: 28px; text-align: center; color: #aaa; font-size: 12px; }
@media print { body { background: #fff; padding: 0; } .sheet { box-shadow: none; } }
"""


def render_html(detail: dict) -> str:
    st, voy, items = detail["statement"], detail["voyage"], detail["items"]
    e = html.escape

    rows = []
    for i, it in enumerate(items, 1):
        rows.append(
            "<tr>"
            f"<td>{i}</td><td>{e(it['meter_no'] or '-')}</td>"
            f"<td>{e(it['connect_at'][:16])}</td><td>{e(it['disconnect_at'][:16])}</td>"
            f"<td>{fmt_duration(it['duration_min'])}</td>"
            f"<td class='num'>{it['meter_start']:,.2f}</td>"
            f"<td class='num'>{it['meter_end']:,.2f}</td>"
            f"<td class='num'>{it['kwh']:,.2f}</td>"
            f"<td class='num'>{it['energy_price']:.4f}</td>"
            f"<td class='num'>{it['service_price']:.4f}</td>"
            f"<td class='num'>{fmt_yuan(it['amount_cents'])}</td>"
            "</tr>"
        )

    dispute_html = ""
    if st["dispute_reason"]:
        dispute_html = (
            f"<div class='dispute'>异议说明：{e(st['dispute_reason'])}</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>岸电对账单 {e(st['statement_no'])}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="sheet">
  <h1>港口岸电使用对账单</h1>
  <div class="sub">SHORE POWER RECONCILIATION STATEMENT</div>
  <div class="meta">
    <span>单号：<b>{e(st['statement_no'])}</b>（V{st['version']}）</span>
    <span class="badge {e(st['status'])}">{e(detail['status_label'])}</span>
  </div>
  <table class="info">
    <tr><td class="k">港口</td><td>{e(detail['port_name'])}</td>
        <td class="k">泊位</td><td>{e(voy['berth'] or '-')}</td></tr>
    <tr><td class="k">船名</td><td>{e(voy['vessel_name'])}</td>
        <td class="k">IMO</td><td>{e(voy['imo'] or '-')}</td></tr>
    <tr><td class="k">船公司</td><td>{e(voy['company'] or '-')}</td>
        <td class="k">航次</td><td>{e(voy['voyage_no'])}</td></tr>
    <tr><td class="k">到泊时间</td><td>{e(voy['arrived_at'] or '-')}</td>
        <td class="k">离泊时间</td><td>{e(voy['departed_at'] or '-')}</td></tr>
  </table>
  <table class="items">
    <thead><tr>
      <th>序号</th><th>电表号</th><th>接电时间</th><th>断电时间</th><th>接电时长</th>
      <th>起码</th><th>止码</th><th>用电量(kWh)</th>
      <th>电费单价<br>(元/kWh)</th><th>服务费单价<br>(元/kWh)</th><th>小计(元)</th>
    </tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <table class="totals">
    <tr>
      <td>合计接电时长：<b>{fmt_duration(st['total_minutes'])}</b></td>
      <td>合计用电量：<b>{st['total_kwh']:,.2f} kWh</b></td>
      <td>电费：{fmt_yuan(st['energy_cents'])} 元</td>
      <td>服务费：{fmt_yuan(st['service_cents'])} 元</td>
      <td class="grand">应付合计：{fmt_yuan(st['total_cents'])} 元</td>
    </tr>
  </table>
  <div class="confirm">
    <div>出具时间：{e(st['issued_at'] or '-')}</div>
    <div>船方确认：{e(st['ship_confirmer'] or '-')}　{e(st['ship_confirmed_at'] or '')}</div>
    <div>港方确认：{e(st['port_confirmer'] or '-')}　{e(st['port_confirmed_at'] or '')}</div>
    {dispute_html}
  </div>
  <div class="sign">
    <div class="box">船方签字 / 日期</div>
    <div class="box">港方签字 / 日期</div>
  </div>
  <div class="foot">本单由港口岸电使用核算系统生成 · {now_str()}</div>
</div>
</body>
</html>"""
