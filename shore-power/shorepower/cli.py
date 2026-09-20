"""命令行入口：python -m shorepower <命令> [参数]."""

from __future__ import annotations

import argparse
import sys

from . import db as dbmod
from .report import pad, render_html, render_text
from .services import (
    STATUS_LABELS,
    ServiceError,
    ShorePowerService,
    fmt_duration,
    fmt_yuan,
)


def _table(rows: list[dict], columns: list[tuple[str, object]]) -> str:
    """简单等宽表格（中文按两列宽对齐）。columns: [(表头, key 或 fn(row))]"""
    def cell(row, col):
        key = col[1]
        return str(key(row) if callable(key) else row.get(key, ""))

    widths = []
    for i, col in enumerate(columns):
        w = len(str(col[0]).encode("gbk", "replace"))  # 表头近似宽度
        for r in rows:
            v = cell(r, col)
            w = max(w, sum(2 if ord(c) > 127 else 1 for c in v))
        widths.append(w + 2)
    lines = ["".join(pad(str(c[0]), widths[i]) for i, c in enumerate(columns))]
    lines.append("".join("-" * (w - 1) + " " for w in widths))
    for r in rows:
        lines.append("".join(pad(cell(r, c), widths[i]) for i, c in enumerate(columns)))
    return "\n".join(lines)


def _print(data) -> None:
    print(data)


# ---------------------------------------------------------------- 各命令处理

def cmd_config_port(svc, a):
    svc.set_port_name(a.name)
    _print(f"港口名称已设置为：{a.name}")


def cmd_vessel_add(svc, a):
    v = svc.add_vessel(a.name, a.imo or "", a.company or "")
    _print(f"已登记船舶 #{v['id']}：{v['name']}")


def cmd_vessel_list(svc, a):
    rows = svc.list_vessels()
    if not rows:
        return _print("（暂无船舶，请先用 vessel-add 登记）")
    _print(_table(rows, [("ID", "id"), ("船名", "name"), ("IMO", "imo"), ("船公司", "company")]))


def cmd_voyage_add(svc, a):
    vessel = svc.find_vessel(a.vessel)
    v = svc.add_voyage(vessel["id"], a.voyage_no, a.berth or "", a.arrived_at, a.departed_at)
    _print(f"已登记航次 #{v['id']}：{v['vessel_name']} / {v['voyage_no']}")


def cmd_voyage_list(svc, a):
    rows = svc.list_voyages()
    if not rows:
        return _print("（暂无航次）")
    _print(_table(rows, [
        ("ID", "id"), ("船名", "vessel_name"), ("航次号", "voyage_no"),
        ("泊位", "berth"), ("到泊时间", lambda r: r["arrived_at"] or "-"),
        ("离泊时间", lambda r: r["departed_at"] or "-"),
    ]))


def cmd_tariff_add(svc, a):
    t = svc.add_tariff(a.name, a.energy_price, a.service_price or 0.0, a.effective_from)
    _print(f"已添加电价方案 #{t['id']}：{t['name']}（{t['effective_from']} 起生效）")


def cmd_tariff_list(svc, a):
    rows = svc.list_tariffs()
    if not rows:
        return _print("（暂无电价方案，请先用 tariff-add 添加）")
    _print(_table(rows, [
        ("ID", "id"), ("名称", "name"),
        ("电费(元/kWh)", lambda r: f"{r['energy_price']:.4f}"),
        ("服务费(元/kWh)", lambda r: f"{r['service_price']:.4f}"),
        ("生效时间", "effective_from"),
    ]))


def cmd_connect(svc, a):
    s = svc.start_session(a.voyage_id, a.meter_no or "", a.at, a.meter_start, a.remark or "")
    _print(f"接电开始：会话 #{s['id']}（航次 {s['voyage_no']}，电表起码 {s['meter_start']}）")


def cmd_disconnect(svc, a):
    s = svc.stop_session(a.session_id, a.at, a.meter_end)
    _print(
        f"断电结束：会话 #{s['id']}，接电时长 {fmt_duration(s['duration_min'])}，"
        f"用电量 {s['kwh']:,.2f} kWh"
    )


def cmd_session_list(svc, a):
    rows = svc.list_sessions(a.voyage_id)
    if not rows:
        return _print("（暂无接电记录）")
    _print(_table(rows, [
        ("ID", "id"), ("航次", "voyage_no"), ("电表", lambda r: r["meter_no"] or "-"),
        ("接电时间", "connect_at"),
        ("断电时间", lambda r: r["disconnect_at"] or "（进行中）"),
        ("时长", lambda r: fmt_duration(r["duration_min"])),
        ("用电量(kWh)", lambda r: f"{r['kwh']:,.2f}" if r["kwh"] is not None else "-"),
    ]))


def cmd_session_edit(svc, a):
    fields = {
        k: v for k, v in {
            "connect_at": a.connect_at, "disconnect_at": a.disconnect_at,
            "meter_start": a.meter_start, "meter_end": a.meter_end,
            "meter_no": a.meter_no, "remark": a.remark,
        }.items() if v is not None
    }
    if not fields:
        raise ServiceError("未提供任何要修改的字段")
    svc.update_session(a.id, **fields)
    _print(f"会话 #{a.id} 已更新")


def cmd_session_delete(svc, a):
    svc.delete_session(a.id)
    _print(f"会话 #{a.id} 已删除")


def cmd_statement_create(svc, a):
    d = svc.create_statement(a.voyage_id)
    st = d["statement"]
    _print(f"已生成对账单 {st['statement_no']}（草稿），明细 {len(d['items'])} 条，"
           f"应付合计 {fmt_yuan(st['total_cents'])} 元")


def cmd_statement_list(svc, a):
    rows = svc.list_statements(a.voyage_id)
    if not rows:
        return _print("（暂无对账单）")
    _print(_table(rows, [
        ("ID", "id"), ("单号", "statement_no"), ("船名", "vessel_name"),
        ("航次", "voyage_no"), ("版本", lambda r: f"V{r['version']}"),
        ("状态", "status_label"),
        ("用电量(kWh)", lambda r: f"{r['total_kwh']:,.2f}"),
        ("应付(元)", lambda r: fmt_yuan(r["total_cents"])),
    ]))


def cmd_statement_show(svc, a):
    detail = svc.get_statement(a.id)
    _print(render_text(detail))
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(render_html(detail))
        _print(f"HTML 对账单已导出：{a.html}")


def cmd_statement_issue(svc, a):
    d = svc.issue_statement(a.id)
    _print(f"对账单 {d['statement']['statement_no']} 已出具（V{d['statement']['version']}），"
           "等待船方确认")


def cmd_statement_ship_confirm(svc, a):
    d = svc.ship_confirm(a.id, a.by)
    _print(f"船方已确认：{d['statement']['statement_no']}（确认人：{a.by}），等待港方确认")


def cmd_statement_ship_dispute(svc, a):
    d = svc.ship_dispute(a.id, a.reason)
    _print(f"船方已提出异议：{d['statement']['statement_no']}，退回港方处理")


def cmd_statement_port_confirm(svc, a):
    d = svc.port_confirm(a.id, a.by)
    _print(f"港方已确认：{d['statement']['statement_no']}，对账办结")


def cmd_statement_cancel(svc, a):
    d = svc.cancel_statement(a.id)
    _print(f"对账单 {d['statement']['statement_no']} 已作废")


def cmd_statement_refresh(svc, a):
    d = svc.refresh_statement(a.id)
    _print(f"对账单 {d['statement']['statement_no']} 已重新汇总，明细 {len(d['items'])} 条")


def cmd_serve(svc, a):
    from .server import run
    run(svc, a.host, a.port)


# ---------------------------------------------------------------- 参数解析

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shorepower",
        description="港口岸电使用核算系统：记录接电时长与用电量，按航次出具对账明细",
    )
    p.add_argument("--db", default=None,
                   help="数据库文件路径（默认 shore_power.db，可用环境变量 SHORE_POWER_DB 指定）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, handler, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(handler=handler)
        return sp

    sp = add("config-port", cmd_config_port, "设置港口名称（显示在对账单上）")
    sp.add_argument("--name", required=True)

    sp = add("vessel-add", cmd_vessel_add, "登记船舶")
    sp.add_argument("--name", required=True, help="船名")
    sp.add_argument("--imo", help="IMO 编号")
    sp.add_argument("--company", help="船公司")
    add("vessel-list", cmd_vessel_list, "船舶列表")

    sp = add("voyage-add", cmd_voyage_add, "登记航次")
    sp.add_argument("--vessel", required=True, help="船舶 id 或船名")
    sp.add_argument("--voyage-no", required=True, help="航次号")
    sp.add_argument("--berth", help="泊位")
    sp.add_argument("--arrived-at", help="到泊时间 'YYYY-MM-DD HH:MM'")
    sp.add_argument("--departed-at", help="离泊时间 'YYYY-MM-DD HH:MM'")
    add("voyage-list", cmd_voyage_list, "航次列表")

    sp = add("tariff-add", cmd_tariff_add, "添加电价方案")
    sp.add_argument("--name", required=True)
    sp.add_argument("--energy-price", type=float, required=True, help="电费单价，元/kWh")
    sp.add_argument("--service-price", type=float, default=0.0, help="服务费单价，元/kWh")
    sp.add_argument("--effective-from", required=True, help="生效时间 'YYYY-MM-DD [HH:MM]'")
    add("tariff-list", cmd_tariff_list, "电价方案列表")

    sp = add("connect", cmd_connect, "接电开始（抄起码）")
    sp.add_argument("--voyage-id", type=int, required=True)
    sp.add_argument("--meter-no", help="电表编号")
    sp.add_argument("--at", required=True, help="接电时间 'YYYY-MM-DD HH:MM'")
    sp.add_argument("--meter-start", type=float, required=True, help="电表起码 kWh")
    sp.add_argument("--remark", help="备注")

    sp = add("disconnect", cmd_disconnect, "断电结束（抄止码，自动计算时长与用电量）")
    sp.add_argument("--session-id", type=int, required=True)
    sp.add_argument("--at", required=True, help="断电时间 'YYYY-MM-DD HH:MM'")
    sp.add_argument("--meter-end", type=float, required=True, help="电表止码 kWh")

    sp = add("session-list", cmd_session_list, "接电记录列表")
    sp.add_argument("--voyage-id", type=int, help="按航次过滤")

    sp = add("session-edit", cmd_session_edit, "修改接电记录（未入账的）")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--connect-at")
    sp.add_argument("--disconnect-at")
    sp.add_argument("--meter-start", type=float)
    sp.add_argument("--meter-end", type=float)
    sp.add_argument("--meter-no")
    sp.add_argument("--remark")

    sp = add("session-delete", cmd_session_delete, "删除接电记录（未入账的）")
    sp.add_argument("--id", type=int, required=True)

    sp = add("statement-create", cmd_statement_create, "按航次生成对账单（草稿）")
    sp.add_argument("--voyage-id", type=int, required=True)

    sp = add("statement-list", cmd_statement_list, "对账单列表")
    sp.add_argument("--voyage-id", type=int)

    sp = add("statement-show", cmd_statement_show, "查看对账明细")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--html", help="同时导出 HTML 文件到指定路径")

    sp = add("statement-refresh", cmd_statement_refresh, "重新汇总明细（草稿/异议状态）")
    sp.add_argument("--id", type=int, required=True)

    sp = add("statement-issue", cmd_statement_issue, "出具对账单，提交船方确认")
    sp.add_argument("--id", type=int, required=True)

    sp = add("statement-ship-confirm", cmd_statement_ship_confirm, "船方确认")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--by", required=True, help="船方确认人")

    sp = add("statement-ship-dispute", cmd_statement_ship_dispute, "船方提出异议")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--reason", required=True, help="异议原因")

    sp = add("statement-port-confirm", cmd_statement_port_confirm, "港方确认办结")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--by", required=True, help="港方确认人")

    sp = add("statement-cancel", cmd_statement_cancel, "作废对账单（草稿/异议状态）")
    sp.add_argument("--id", type=int, required=True)

    sp = add("serve", cmd_serve, "启动 HTTP API 服务")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = dbmod.connect(args.db)
    svc = ShorePowerService(conn)
    try:
        args.handler(svc, args)
    except ServiceError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
