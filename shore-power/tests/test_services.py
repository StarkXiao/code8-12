"""核心业务逻辑单元测试."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shorepower import db
from shorepower.services import (
    STATUS_CLOSED,
    STATUS_DISPUTED,
    STATUS_DRAFT,
    STATUS_ISSUED,
    STATUS_SHIP_CONFIRMED,
    ServiceError,
    ShorePowerService,
    money_cents,
    round_kwh,
)


class Base(unittest.TestCase):
    def setUp(self):
        self.svc = ShorePowerService(db.connect(":memory:"))
        self.svc.add_tariff("标准电价", 0.85, 0.20, "2026-01-01")
        self.vessel = self.svc.add_vessel("远洋之星", "9876543", "蓝海航运")
        self.voyage = self.svc.add_voyage(self.vessel["id"], "VY001", "3号泊位")

    def make_session(self, connect="2026-09-18 15:10", disconnect="2026-09-18 23:40",
                     start=100.0, end=378.30):
        s = self.svc.start_session(self.voyage["id"], "M-01", connect, start)
        return self.svc.stop_session(s["id"], disconnect, end)


class TestSession(Base):
    def test_kwh_and_duration(self):
        s = self.make_session()
        self.assertAlmostEqual(s["kwh"], 278.30, places=2)
        self.assertEqual(s["duration_min"], 510)  # 15:10 → 23:40 = 8小时30分

    def test_meter_end_less_than_start_rejected(self):
        s = self.svc.start_session(self.voyage["id"], "M-01", "2026-09-18 08:00", 500.0)
        with self.assertRaises(ServiceError):
            self.svc.stop_session(s["id"], "2026-09-18 10:00", 499.0)

    def test_disconnect_before_connect_rejected(self):
        s = self.svc.start_session(self.voyage["id"], "M-01", "2026-09-18 08:00", 0.0)
        with self.assertRaises(ServiceError):
            self.svc.stop_session(s["id"], "2026-09-18 07:00", 10.0)

    def test_one_open_session_per_voyage(self):
        self.svc.start_session(self.voyage["id"], "M-01", "2026-09-18 08:00", 0.0)
        with self.assertRaises(ServiceError):
            self.svc.start_session(self.voyage["id"], "M-02", "2026-09-18 09:00", 0.0)

    def test_no_effective_tariff_rejected(self):
        with self.assertRaises(ServiceError):
            s = self.svc.start_session(self.voyage["id"], "M-01", "2020-01-01 08:00", 0.0)
            self.svc.stop_session(s["id"], "2020-01-01 09:00", 10.0)


class TestTariffResolution(Base):
    def test_effective_date_selection(self):
        self.svc.add_tariff("新电价", 1.00, 0.00, "2026-09-01")
        # 接电时间在新电价生效前 → 用旧电价
        old = self.make_session(connect="2026-08-31 22:00", disconnect="2026-09-01 02:00")
        self.assertEqual(old["tariff_id"], 1)
        # 接电时间在新电价生效后 → 用新电价
        new = self.make_session(connect="2026-09-02 08:00", disconnect="2026-09-02 10:00")
        self.assertEqual(new["tariff_id"], 2)


class TestStatement(Base):
    def test_totals_and_snapshot(self):
        self.make_session()
        self.make_session(connect="2026-09-19 06:20", disconnect="2026-09-19 12:05",
                          start=378.30, end=576.90)
        d = self.svc.create_statement(self.voyage["id"])
        st = d["statement"]
        self.assertEqual(len(d["items"]), 2)
        self.assertAlmostEqual(st["total_kwh"], 278.30 + 198.60, places=2)
        self.assertEqual(st["total_minutes"], 510 + 345)
        # 电费 = 476.90 × 0.85 = 405.365 → 405.37（分位四舍五入按明细逐条计算）
        # 明细1：278.30×0.85=236.555→236.56（银行家？不，ROUND_HALF_UP）+ 278.30×0.20=55.66
        # 明细2：198.60×0.85=168.81 + 198.60×0.20=39.72
        self.assertEqual(st["energy_cents"], 23656 + 16881)
        self.assertEqual(st["service_cents"], 5566 + 3972)
        self.assertEqual(st["total_cents"], st["energy_cents"] + st["service_cents"])
        self.assertTrue(st["statement_no"].startswith("APS-"))

    def test_open_session_not_billable(self):
        self.svc.start_session(self.voyage["id"], "M-01", "2026-09-18 08:00", 0.0)
        with self.assertRaises(ServiceError):
            self.svc.create_statement(self.voyage["id"])

    def test_one_active_statement_per_voyage(self):
        self.make_session()
        self.svc.create_statement(self.voyage["id"])
        with self.assertRaises(ServiceError):
            self.svc.create_statement(self.voyage["id"])

    def test_full_confirm_flow(self):
        self.make_session()
        d = self.svc.create_statement(self.voyage["id"])
        sid = d["statement"]["id"]
        self.assertEqual(d["statement"]["status"], STATUS_DRAFT)

        d = self.svc.issue_statement(sid)
        self.assertEqual(d["statement"]["status"], STATUS_ISSUED)
        self.assertIsNotNone(d["statement"]["issued_at"])

        d = self.svc.ship_confirm(sid, "王船长")
        self.assertEqual(d["statement"]["status"], STATUS_SHIP_CONFIRMED)
        self.assertEqual(d["statement"]["ship_confirmer"], "王船长")

        d = self.svc.port_confirm(sid, "李调度")
        self.assertEqual(d["statement"]["status"], STATUS_CLOSED)
        self.assertEqual(d["statement"]["port_confirmer"], "李调度")

    def test_invalid_transitions(self):
        self.make_session()
        d = self.svc.create_statement(self.voyage["id"])
        sid = d["statement"]["id"]
        with self.assertRaises(ServiceError):
            self.svc.ship_confirm(sid, "王船长")   # 草稿不能直接船方确认
        with self.assertRaises(ServiceError):
            self.svc.port_confirm(sid, "李调度")   # 未船方确认不能办结
        self.svc.issue_statement(sid)
        with self.assertRaises(ServiceError):
            self.svc.issue_statement(sid)          # 不能重复出具
        with self.assertRaises(ServiceError):
            self.svc.cancel_statement(sid)         # 已出具不能作废

    def test_dispute_and_reissue(self):
        s = self.make_session()
        d = self.svc.create_statement(self.voyage["id"])
        sid = d["statement"]["id"]
        self.svc.issue_statement(sid)

        d = self.svc.ship_dispute(sid, "第二次接电止码抄录有误")
        self.assertEqual(d["statement"]["status"], STATUS_DISPUTED)
        self.assertEqual(d["statement"]["dispute_reason"], "第二次接电止码抄录有误")

        # 异议状态下可修正接电记录，对账单自动重算
        self.svc.update_session(s["id"], meter_end=400.0)
        d = self.svc.get_statement(sid)
        self.assertAlmostEqual(d["statement"]["total_kwh"], 300.0, places=2)

        # 重新出具，版本 +1
        d = self.svc.issue_statement(sid)
        self.assertEqual(d["statement"]["version"], 2)
        self.assertEqual(d["statement"]["status"], STATUS_ISSUED)
        self.assertEqual(d["statement"]["dispute_reason"], "")

    def test_issued_statement_locks_sessions(self):
        s = self.make_session()
        d = self.svc.create_statement(self.voyage["id"])
        sid = d["statement"]["id"]
        self.svc.issue_statement(sid)
        with self.assertRaises(ServiceError):
            self.svc.update_session(s["id"], meter_end=999.0)
        with self.assertRaises(ServiceError):
            self.svc.delete_session(s["id"])

    def test_cancel_releases_sessions(self):
        self.make_session()
        d = self.svc.create_statement(self.voyage["id"])
        self.svc.cancel_statement(d["statement"]["id"])
        # 作废后可重新生成
        d2 = self.svc.create_statement(self.voyage["id"])
        self.assertEqual(len(d2["items"]), 1)

    def test_supplementary_statement_after_closed(self):
        self.make_session()
        d = self.svc.create_statement(self.voyage["id"])
        sid = d["statement"]["id"]
        self.svc.issue_statement(sid)
        self.svc.ship_confirm(sid, "王船长")
        self.svc.port_confirm(sid, "李调度")
        # 办结后补录一段接电 → 可再出补充对账单，且不含已入账明细
        self.make_session(connect="2026-09-19 06:20", disconnect="2026-09-19 08:20",
                          start=378.30, end=478.30)
        d2 = self.svc.create_statement(self.voyage["id"])
        self.assertEqual(len(d2["items"]), 1)
        self.assertAlmostEqual(d2["statement"]["total_kwh"], 100.0, places=2)


class TestMoney(unittest.TestCase):
    def test_round_half_up(self):
        self.assertEqual(money_cents(278.30, 0.85), 23656)   # 236.555 → 236.56
        self.assertEqual(money_cents(0.005, 1.0), 1)         # 0.005 → 0.01
        self.assertEqual(money_cents(100.0, 0.2), 2000)

    def test_round_kwh(self):
        self.assertEqual(round_kwh(278.299999), 278.3)
        self.assertEqual(round_kwh(0.005), 0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
