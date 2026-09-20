"""业务逻辑层：船舶/航次/电价/接电会话/对账单及双方确认流程."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

# 对账单状态
STATUS_DRAFT = "draft"                  # 草稿（港方编制中）
STATUS_ISSUED = "issued"                # 已出具，待船方确认
STATUS_SHIP_CONFIRMED = "ship_confirmed"  # 船方已确认，待港方确认
STATUS_DISPUTED = "disputed"            # 船方异议，待港方处理
STATUS_CLOSED = "closed"                # 双方确认完成，已办结
STATUS_CANCELLED = "cancelled"          # 已作废

STATUS_LABELS = {
    STATUS_DRAFT: "草稿",
    STATUS_ISSUED: "已出具（待船方确认）",
    STATUS_SHIP_CONFIRMED: "船方已确认（待港方确认）",
    STATUS_DISPUTED: "船方异议（待港方处理）",
    STATUS_CLOSED: "双方确认完成",
    STATUS_CANCELLED: "已作废",
}

# 每个航次同一时间只允许一个“进行中”的对账单
_ACTIVE_STATUSES = (STATUS_DRAFT, STATUS_ISSUED, STATUS_SHIP_CONFIRMED, STATUS_DISPUTED)
# 处于这些状态的对账单会锁定其引用的接电记录，禁止修改/删除
_LOCKING_STATUSES = (STATUS_ISSUED, STATUS_SHIP_CONFIRMED, STATUS_CLOSED)


class ServiceError(Exception):
    """业务规则校验失败，消息面向用户（中文）。"""


# ---------------------------------------------------------------- 工具函数

def now_str() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def parse_dt(value: str) -> str:
    """解析用户输入的时间，统一为 'YYYY-MM-DD HH:MM:SS'。"""
    v = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ServiceError(f"时间格式不正确：{value!r}，请使用 'YYYY-MM-DD HH:MM'")


def minutes_between(start: str, end: str) -> int:
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return int(round(delta.total_seconds() / 60))


def round_kwh(value: float) -> float:
    """用电量保留两位小数（四舍五入到 0.01 kWh）。"""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def money_cents(kwh: float, price: float) -> int:
    """金额 = 电量 × 单价，四舍五入到分。"""
    yuan = (Decimal(str(kwh)) * Decimal(str(price))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return int(yuan * 100)


def fmt_duration(minutes: int | None) -> str:
    if minutes is None:
        return "-"
    h, m = divmod(int(minutes), 60)
    return f"{h}小时{m:02d}分"


def fmt_yuan(cents: int) -> str:
    return f"{cents / 100:,.2f}"


# ---------------------------------------------------------------- 服务主体

class ShorePowerService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------ 港口配置

    def set_port_name(self, name: str) -> None:
        if not name.strip():
            raise ServiceError("港口名称不能为空")
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES('port_name', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (name.strip(),),
        )
        self.conn.commit()

    def get_port_name(self) -> str:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = 'port_name'"
        ).fetchone()
        return row["value"] if row else "（未设置港口名称）"

    # ------------------------------------------------------------ 船舶

    def add_vessel(self, name: str, imo: str = "", company: str = "") -> dict:
        if not name.strip():
            raise ServiceError("船名不能为空")
        cur = self.conn.execute(
            "INSERT INTO vessels(name, imo, company, created_at) VALUES (?,?,?,?)",
            (name.strip(), imo.strip(), company.strip(), now_str()),
        )
        self.conn.commit()
        return self.get_vessel(cur.lastrowid)

    def get_vessel(self, vessel_id: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM vessels WHERE id = ?", (vessel_id,)
        ).fetchone()
        if not row:
            raise ServiceError(f"船舶不存在：#{vessel_id}")
        return dict(row)

    def find_vessel(self, ref: str) -> dict:
        """按 id 或船名精确查找（CLI 便捷入口）。"""
        try:
            return self.get_vessel(int(ref))
        except ValueError:
            pass
        rows = self.conn.execute(
            "SELECT * FROM vessels WHERE name = ? ORDER BY id", (ref.strip(),)
        ).fetchall()
        if not rows:
            raise ServiceError(f"找不到船舶：{ref!r}（可按 id 或船名查找）")
        if len(rows) > 1:
            ids = ", ".join(f"#{r['id']}" for r in rows)
            raise ServiceError(f"船名 {ref!r} 对应多条记录（{ids}），请改用 id")
        return dict(rows[0])

    def list_vessels(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM vessels ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 航次

    def add_voyage(
        self,
        vessel_id: int,
        voyage_no: str,
        berth: str = "",
        arrived_at: str | None = None,
        departed_at: str | None = None,
    ) -> dict:
        self.get_vessel(vessel_id)
        if not voyage_no.strip():
            raise ServiceError("航次号不能为空")
        arrived = parse_dt(arrived_at) if arrived_at else None
        departed = parse_dt(departed_at) if departed_at else None
        if arrived and departed and departed < arrived:
            raise ServiceError("离泊时间不能早于到泊时间")
        try:
            cur = self.conn.execute(
                "INSERT INTO voyages(vessel_id, voyage_no, berth, arrived_at, departed_at, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (vessel_id, voyage_no.strip(), berth.strip(), arrived, departed, now_str()),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            raise ServiceError(f"该船舶已存在航次 {voyage_no!r}，请勿重复登记")
        return self.get_voyage(cur.lastrowid)

    def get_voyage(self, voyage_id: int) -> dict:
        row = self.conn.execute(
            "SELECT v.*, s.name AS vessel_name, s.imo, s.company "
            "FROM voyages v JOIN vessels s ON s.id = v.vessel_id WHERE v.id = ?",
            (voyage_id,),
        ).fetchone()
        if not row:
            raise ServiceError(f"航次不存在：#{voyage_id}")
        return dict(row)

    def list_voyages(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT v.*, s.name AS vessel_name FROM voyages v "
            "JOIN vessels s ON s.id = v.vessel_id ORDER BY v.id"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 电价方案

    def add_tariff(
        self, name: str, energy_price: float, service_price: float, effective_from: str
    ) -> dict:
        if not name.strip():
            raise ServiceError("电价方案名称不能为空")
        if energy_price < 0 or service_price < 0:
            raise ServiceError("单价不能为负数")
        eff = parse_dt(effective_from)
        cur = self.conn.execute(
            "INSERT INTO tariffs(name, energy_price, service_price, effective_from, created_at)"
            " VALUES (?,?,?,?,?)",
            (name.strip(), float(energy_price), float(service_price), eff, now_str()),
        )
        self.conn.commit()
        return self.get_tariff(cur.lastrowid)

    def get_tariff(self, tariff_id: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM tariffs WHERE id = ?", (tariff_id,)
        ).fetchone()
        if not row:
            raise ServiceError(f"电价方案不存在：#{tariff_id}")
        return dict(row)

    def list_tariffs(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tariffs ORDER BY effective_from, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def _resolve_tariff(self, at: str) -> dict:
        """取指定时间之前最近生效的电价方案。"""
        row = self.conn.execute(
            "SELECT * FROM tariffs WHERE effective_from <= ? "
            "ORDER BY effective_from DESC, id DESC LIMIT 1",
            (at,),
        ).fetchone()
        if not row:
            raise ServiceError(
                f"在 {at} 之前没有已生效的电价方案，请先添加电价方案"
            )
        return dict(row)

    # ------------------------------------------------------------ 接电会话

    def start_session(
        self,
        voyage_id: int,
        meter_no: str,
        connect_at: str,
        meter_start: float,
        remark: str = "",
    ) -> dict:
        voyage = self.get_voyage(voyage_id)
        connect_at = parse_dt(connect_at)
        open_row = self.conn.execute(
            "SELECT id FROM sessions WHERE voyage_id = ? AND disconnect_at IS NULL",
            (voyage_id,),
        ).fetchone()
        if open_row:
            raise ServiceError(
                f"航次 {voyage['voyage_no']} 已有进行中的接电记录（会话 #{open_row['id']}），"
                "请先断电结束"
            )
        cur = self.conn.execute(
            "INSERT INTO sessions(voyage_id, meter_no, connect_at, meter_start, remark, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (voyage_id, meter_no.strip(), connect_at, float(meter_start), remark.strip(), now_str()),
        )
        self.conn.commit()
        return self.get_session(cur.lastrowid)

    def stop_session(self, session_id: int, disconnect_at: str, meter_end: float) -> dict:
        s = self._get_session(session_id)
        if s["disconnect_at"]:
            raise ServiceError(f"会话 #{session_id} 已结束，请勿重复断电")
        disconnect_at = parse_dt(disconnect_at)
        if disconnect_at <= s["connect_at"]:
            raise ServiceError("断电时间必须晚于接电时间")
        meter_end = float(meter_end)
        if meter_end < s["meter_start"]:
            raise ServiceError(
                f"电表止码 {meter_end} 小于起码 {s['meter_start']}，请核对抄表数据"
            )
        kwh = round_kwh(meter_end - s["meter_start"])
        duration = minutes_between(s["connect_at"], disconnect_at)
        tariff = self._resolve_tariff(s["connect_at"])
        self.conn.execute(
            "UPDATE sessions SET disconnect_at = ?, meter_end = ?, kwh = ?, "
            "duration_min = ?, tariff_id = ? WHERE id = ?",
            (disconnect_at, meter_end, kwh, duration, tariff["id"], session_id),
        )
        self.conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: int) -> dict:
        return self._get_session(session_id)

    def _get_session(self, session_id: int) -> dict:
        row = self.conn.execute(
            "SELECT s.*, v.voyage_no FROM sessions s "
            "JOIN voyages v ON v.id = s.voyage_id WHERE s.id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            raise ServiceError(f"接电会话不存在：#{session_id}")
        return dict(row)

    def list_sessions(self, voyage_id: int | None = None) -> list[dict]:
        sql = (
            "SELECT s.*, v.voyage_no FROM sessions s "
            "JOIN voyages v ON v.id = s.voyage_id"
        )
        params: tuple = ()
        if voyage_id is not None:
            sql += " WHERE s.voyage_id = ?"
            params = (voyage_id,)
        sql += " ORDER BY s.connect_at, s.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def update_session(self, session_id: int, **fields) -> dict:
        """修改接电记录；已被出具/确认的对账单引用的记录禁止修改。"""
        s = self._get_session(session_id)
        self._assert_session_unlocked(session_id)

        new = dict(s)
        if fields.get("connect_at") is not None:
            new["connect_at"] = parse_dt(fields["connect_at"])
        if fields.get("disconnect_at") is not None:
            new["disconnect_at"] = parse_dt(fields["disconnect_at"])
        if fields.get("meter_start") is not None:
            new["meter_start"] = float(fields["meter_start"])
        if fields.get("meter_end") is not None:
            new["meter_end"] = float(fields["meter_end"])
        if fields.get("meter_no") is not None:
            new["meter_no"] = str(fields["meter_no"]).strip()
        if fields.get("remark") is not None:
            new["remark"] = str(fields["remark"]).strip()

        if new["disconnect_at"]:
            if new["disconnect_at"] <= new["connect_at"]:
                raise ServiceError("断电时间必须晚于接电时间")
            if new["meter_end"] is None:
                raise ServiceError("已结束的会话缺少电表止码")
            if new["meter_end"] < new["meter_start"]:
                raise ServiceError("电表止码不能小于起码")
            new["kwh"] = round_kwh(new["meter_end"] - new["meter_start"])
            new["duration_min"] = minutes_between(new["connect_at"], new["disconnect_at"])
            new["tariff_id"] = self._resolve_tariff(new["connect_at"])["id"]

        self.conn.execute(
            "UPDATE sessions SET connect_at=?, disconnect_at=?, meter_no=?, "
            "meter_start=?, meter_end=?, kwh=?, duration_min=?, tariff_id=?, remark=? "
            "WHERE id=?",
            (
                new["connect_at"], new["disconnect_at"], new["meter_no"],
                new["meter_start"], new["meter_end"], new["kwh"],
                new["duration_min"], new["tariff_id"], new["remark"], session_id,
            ),
        )
        self._refresh_open_statements(s["voyage_id"])
        self.conn.commit()
        return self.get_session(session_id)

    def delete_session(self, session_id: int) -> None:
        s = self._get_session(session_id)
        self._assert_session_unlocked(session_id)
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._refresh_open_statements(s["voyage_id"])
        self.conn.commit()

    def _assert_session_unlocked(self, session_id: int) -> None:
        placeholders = ",".join("?" for _ in _LOCKING_STATUSES)
        row = self.conn.execute(
            f"SELECT st.id, st.statement_no, st.status FROM statement_items si "
            f"JOIN statements st ON st.id = si.statement_id "
            f"WHERE si.session_id = ? AND st.status IN ({placeholders}) LIMIT 1",
            (session_id, *_LOCKING_STATUSES),
        ).fetchone()
        if row:
            raise ServiceError(
                f"该接电记录已计入对账单 {row['statement_no']}"
                f"（{STATUS_LABELS[row['status']]}），不可修改或删除；"
                "如需调整，请由船方提出异议退回后修订"
            )

    # ------------------------------------------------------------ 对账单

    def create_statement(self, voyage_id: int) -> dict:
        voyage = self.get_voyage(voyage_id)
        active = self.conn.execute(
            f"SELECT statement_no FROM statements WHERE voyage_id = ? "
            f"AND status IN ({','.join('?' for _ in _ACTIVE_STATUSES)})",
            (voyage_id, *_ACTIVE_STATUSES),
        ).fetchone()
        if active:
            raise ServiceError(
                f"该航次已有进行中的对账单 {active['statement_no']}，请先办结或作废"
            )
        sessions = self._billable_sessions(voyage_id, exclude_statement_id=-1)
        if not sessions:
            raise ServiceError("该航次没有可入账的已结束接电记录")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO statements(statement_no, voyage_id, status, created_at, updated_at)"
                " VALUES ('', ?, ?, ?, ?)",
                (voyage_id, STATUS_DRAFT, now_str(), now_str()),
            )
            sid = cur.lastrowid
            no = f"APS-{datetime.now().year}-{sid:04d}"
            self.conn.execute(
                "UPDATE statements SET statement_no = ? WHERE id = ?", (no, sid)
            )
            self._rebuild_items(sid, sessions)
        return self.get_statement(sid)

    def refresh_statement(self, statement_id: int) -> dict:
        """草稿/异议状态下重新汇总明细（补录或修正接电记录后使用）。"""
        st = self._get_statement_row(statement_id)
        if st["status"] not in (STATUS_DRAFT, STATUS_DISPUTED):
            raise ServiceError(
                f"对账单当前状态为「{STATUS_LABELS[st['status']]}」，不能重新汇总"
            )
        sessions = self._billable_sessions(
            st["voyage_id"], exclude_statement_id=statement_id
        )
        with self.conn:
            self._rebuild_items(statement_id, sessions)
        return self.get_statement(statement_id)

    def issue_statement(self, statement_id: int) -> dict:
        """出具对账单：草稿 → 已出具；异议修订后重新出具（版本 +1）。"""
        st = self._get_statement_row(statement_id)
        if st["status"] not in (STATUS_DRAFT, STATUS_DISPUTED):
            raise ServiceError(
                f"只有草稿或异议状态的对账单可以出具（当前：{STATUS_LABELS[st['status']]}）"
            )
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM statement_items WHERE statement_id = ?",
            (statement_id,),
        ).fetchone()["c"]
        if count == 0:
            raise ServiceError("对账单没有明细，无法出具")
        version = st["version"] + 1 if st["status"] == STATUS_DISPUTED else st["version"]
        self.conn.execute(
            "UPDATE statements SET status = ?, version = ?, dispute_reason = '', "
            "issued_at = ?, updated_at = ? WHERE id = ?",
            (STATUS_ISSUED, version, now_str(), now_str(), statement_id),
        )
        self.conn.commit()
        return self.get_statement(statement_id)

    def ship_confirm(self, statement_id: int, confirmer: str) -> dict:
        """船方确认。"""
        st = self._get_statement_row(statement_id)
        if st["status"] != STATUS_ISSUED:
            raise ServiceError(
                f"只有「已出具」状态的对账单可由船方确认（当前：{STATUS_LABELS[st['status']]}）"
            )
        if not confirmer.strip():
            raise ServiceError("请填写船方确认人")
        self.conn.execute(
            "UPDATE statements SET status = ?, ship_confirmer = ?, "
            "ship_confirmed_at = ?, updated_at = ? WHERE id = ?",
            (STATUS_SHIP_CONFIRMED, confirmer.strip(), now_str(), now_str(), statement_id),
        )
        self.conn.commit()
        return self.get_statement(statement_id)

    def ship_dispute(self, statement_id: int, reason: str) -> dict:
        """船方提出异议，退回港方处理。"""
        st = self._get_statement_row(statement_id)
        if st["status"] != STATUS_ISSUED:
            raise ServiceError(
                f"只有「已出具」状态的对账单可提出异议（当前：{STATUS_LABELS[st['status']]}）"
            )
        if not reason.strip():
            raise ServiceError("提出异议必须填写异议原因")
        self.conn.execute(
            "UPDATE statements SET status = ?, dispute_reason = ?, updated_at = ? WHERE id = ?",
            (STATUS_DISPUTED, reason.strip(), now_str(), statement_id),
        )
        self.conn.commit()
        return self.get_statement(statement_id)

    def port_confirm(self, statement_id: int, confirmer: str) -> dict:
        """港方最终确认，对账办结。"""
        st = self._get_statement_row(statement_id)
        if st["status"] != STATUS_SHIP_CONFIRMED:
            raise ServiceError(
                f"需船方先确认，港方才能办结（当前：{STATUS_LABELS[st['status']]}）"
            )
        if not confirmer.strip():
            raise ServiceError("请填写港方确认人")
        self.conn.execute(
            "UPDATE statements SET status = ?, port_confirmer = ?, "
            "port_confirmed_at = ?, updated_at = ? WHERE id = ?",
            (STATUS_CLOSED, confirmer.strip(), now_str(), now_str(), statement_id),
        )
        self.conn.commit()
        return self.get_statement(statement_id)

    def cancel_statement(self, statement_id: int) -> dict:
        """作废对账单（仅草稿/异议状态可作废），明细引用随之释放。"""
        st = self._get_statement_row(statement_id)
        if st["status"] not in (STATUS_DRAFT, STATUS_DISPUTED):
            raise ServiceError(
                f"只有草稿或异议状态的对账单可以作废（当前：{STATUS_LABELS[st['status']]}）"
            )
        self.conn.execute(
            "UPDATE statements SET status = ?, updated_at = ? WHERE id = ?",
            (STATUS_CANCELLED, now_str(), statement_id),
        )
        self.conn.commit()
        return self.get_statement(statement_id)

    def get_statement(self, statement_id: int) -> dict:
        st = self._get_statement_row(statement_id)
        voyage = self.get_voyage(st["voyage_id"])
        items = self.conn.execute(
            "SELECT * FROM statement_items WHERE statement_id = ? ORDER BY connect_at, id",
            (statement_id,),
        ).fetchall()
        return {
            "statement": st,
            "status_label": STATUS_LABELS[st["status"]],
            "voyage": voyage,
            "items": [dict(i) for i in items],
            "port_name": self.get_port_name(),
        }

    def list_statements(self, voyage_id: int | None = None) -> list[dict]:
        sql = (
            "SELECT st.*, v.voyage_no, s.name AS vessel_name FROM statements st "
            "JOIN voyages v ON v.id = st.voyage_id "
            "JOIN vessels s ON s.id = v.vessel_id"
        )
        params: tuple = ()
        if voyage_id is not None:
            sql += " WHERE st.voyage_id = ?"
            params = (voyage_id,)
        sql += " ORDER BY st.id"
        rows = [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        for r in rows:
            r["status_label"] = STATUS_LABELS[r["status"]]
        return rows

    # ------------------------------------------------------------ 对账内部

    def _get_statement_row(self, statement_id: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM statements WHERE id = ?", (statement_id,)
        ).fetchone()
        if not row:
            raise ServiceError(f"对账单不存在：#{statement_id}")
        return dict(row)

    def _billable_sessions(self, voyage_id: int, exclude_statement_id: int) -> list[dict]:
        """已结束且未被任何未作废对账单占用的接电记录。"""
        rows = self.conn.execute(
            "SELECT * FROM sessions s WHERE s.voyage_id = ? AND s.disconnect_at IS NOT NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM statement_items si JOIN statements st ON st.id = si.statement_id "
            "  WHERE si.session_id = s.id AND st.status != ? AND st.id != ?"
            ") ORDER BY s.connect_at, s.id",
            (voyage_id, STATUS_CANCELLED, exclude_statement_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def _rebuild_items(self, statement_id: int, sessions: list[dict]) -> None:
        """根据接电记录重建对账明细快照并更新合计（不提交事务）。"""
        self.conn.execute(
            "DELETE FROM statement_items WHERE statement_id = ?", (statement_id,)
        )
        total_kwh = total_min = 0
        energy_cents = service_cents = 0
        for s in sessions:
            tariff = self.get_tariff(s["tariff_id"]) if s["tariff_id"] else None
            if tariff is None:
                tariff = self._resolve_tariff(s["connect_at"])
            e_cents = money_cents(s["kwh"], tariff["energy_price"])
            s_cents = money_cents(s["kwh"], tariff["service_price"])
            self.conn.execute(
                "INSERT INTO statement_items(statement_id, session_id, meter_no, "
                "connect_at, disconnect_at, duration_min, meter_start, meter_end, kwh, "
                "energy_price, service_price, energy_cents, service_cents, amount_cents)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    statement_id, s["id"], s["meter_no"], s["connect_at"],
                    s["disconnect_at"], s["duration_min"], s["meter_start"],
                    s["meter_end"], s["kwh"], tariff["energy_price"],
                    tariff["service_price"], e_cents, s_cents, e_cents + s_cents,
                ),
            )
            total_kwh += s["kwh"]
            total_min += s["duration_min"]
            energy_cents += e_cents
            service_cents += s_cents
        self.conn.execute(
            "UPDATE statements SET total_kwh = ?, total_minutes = ?, energy_cents = ?, "
            "service_cents = ?, total_cents = ?, updated_at = ? WHERE id = ?",
            (
                round_kwh(total_kwh), total_min, energy_cents, service_cents,
                energy_cents + service_cents, now_str(), statement_id,
            ),
        )

    def _refresh_open_statements(self, voyage_id: int) -> None:
        """接电记录变更后，自动重算仍处于草稿/异议状态的对账单。"""
        rows = self.conn.execute(
            "SELECT id FROM statements WHERE voyage_id = ? AND status IN (?, ?)",
            (voyage_id, STATUS_DRAFT, STATUS_DISPUTED),
        ).fetchall()
        for r in rows:
            sessions = self._billable_sessions(voyage_id, exclude_statement_id=r["id"])
            self._rebuild_items(r["id"], sessions)
