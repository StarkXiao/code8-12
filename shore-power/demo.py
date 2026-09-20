"""端到端演示：从登记资料到双方确认办结的完整流程."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shorepower import db
from shorepower.report import render_html, render_text
from shorepower.services import ShorePowerService

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.db")
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_statement.html")


def main() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    svc = ShorePowerService(db.connect(DB_PATH))

    print("== 1. 基础资料 ==")
    svc.set_port_name("滨海港国际集装箱码头")
    svc.add_tariff("岸电标准电价", 0.85, 0.20, "2026-01-01")
    vessel = svc.add_vessel("远洋之星", "9876543", "蓝海航运有限公司")
    voyage = svc.add_voyage(vessel["id"], "VY2026-0918", "3号泊位",
                            arrived_at="2026-09-18 14:00")
    print(f"船舶：{vessel['name']}（IMO {vessel['imo']}）  航次：{voyage['voyage_no']}")

    print("\n== 2. 接电记录 ==")
    s1 = svc.start_session(voyage["id"], "M-3A-01", "2026-09-18 15:10", 10234.50)
    s1 = svc.stop_session(s1["id"], "2026-09-18 23:40", 10512.80)
    print(f"会话 #{s1['id']}：{s1['connect_at']} → {s1['disconnect_at']}，"
          f"{s1['kwh']} kWh")
    s2 = svc.start_session(voyage["id"], "M-3A-01", "2026-09-19 06:20", 10512.80)
    s2 = svc.stop_session(s2["id"], "2026-09-19 12:05", 10711.40)
    print(f"会话 #{s2['id']}：{s2['connect_at']} → {s2['disconnect_at']}，"
          f"{s2['kwh']} kWh")

    print("\n== 3. 生成对账单并出具 ==")
    detail = svc.create_statement(voyage["id"])
    sid = detail["statement"]["id"]
    svc.issue_statement(sid)
    print(f"对账单 {detail['statement']['statement_no']} 已出具")

    print("\n== 4. 双方确认 ==")
    svc.ship_confirm(sid, "王船长")
    print("船方已确认（王船长）")
    svc.port_confirm(sid, "李调度")
    print("港方已确认（李调度），对账办结")

    print("\n== 5. 对账明细 ==")
    detail = svc.get_statement(sid)
    print(render_text(detail))

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(render_html(detail))
    print(f"HTML 对账单已导出：{HTML_PATH}")
    print(f"演示数据库：{DB_PATH}")


if __name__ == "__main__":
    main()
