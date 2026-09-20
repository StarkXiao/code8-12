"""SQLite 存储层：建库连接与表结构."""

from __future__ import annotations

import os
import sqlite3

DEFAULT_DB = os.environ.get("SHORE_POWER_DB", "shore_power.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 港口基础配置（键值对）
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 船舶档案
CREATE TABLE IF NOT EXISTS vessels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    imo        TEXT NOT NULL DEFAULT '',
    company    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- 航次：一艘船在同一航次号下唯一
CREATE TABLE IF NOT EXISTS voyages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vessel_id   INTEGER NOT NULL REFERENCES vessels(id),
    voyage_no   TEXT NOT NULL,
    berth       TEXT NOT NULL DEFAULT '',
    arrived_at  TEXT,
    departed_at TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (vessel_id, voyage_no)
);

-- 电价方案：按生效日期取值（出具对账单时取接电时间之前最近生效的方案）
CREATE TABLE IF NOT EXISTS tariffs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    energy_price   REAL NOT NULL,            -- 电费单价，元/kWh
    service_price  REAL NOT NULL DEFAULT 0,  -- 服务费单价，元/kWh
    effective_from TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

-- 接电会话：一次接电/断电记录；disconnect_at 为空表示进行中
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    voyage_id     INTEGER NOT NULL REFERENCES voyages(id),
    meter_no      TEXT NOT NULL DEFAULT '',
    connect_at    TEXT NOT NULL,
    disconnect_at TEXT,
    meter_start   REAL NOT NULL,
    meter_end     REAL,
    kwh           REAL,                       -- 用电量 = 止码 - 起码
    duration_min  INTEGER,                    -- 接电时长，分钟
    tariff_id     INTEGER REFERENCES tariffs(id),
    remark        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

-- 对账单：按航次出具；明细为快照，出具后不随后续数据修改而变化
CREATE TABLE IF NOT EXISTS statements (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_no      TEXT NOT NULL UNIQUE,
    voyage_id         INTEGER NOT NULL REFERENCES voyages(id),
    version           INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'draft',
    total_kwh         REAL NOT NULL DEFAULT 0,
    total_minutes     INTEGER NOT NULL DEFAULT 0,
    energy_cents      INTEGER NOT NULL DEFAULT 0,  -- 电费，分
    service_cents     INTEGER NOT NULL DEFAULT 0,  -- 服务费，分
    total_cents       INTEGER NOT NULL DEFAULT 0,  -- 应付合计，分
    dispute_reason    TEXT NOT NULL DEFAULT '',
    issued_at         TEXT,
    ship_confirmed_at TEXT,
    ship_confirmer    TEXT NOT NULL DEFAULT '',
    port_confirmed_at TEXT,
    port_confirmer    TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- 对账明细快照
CREATE TABLE IF NOT EXISTS statement_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id   INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
    session_id     INTEGER NOT NULL REFERENCES sessions(id),
    meter_no       TEXT NOT NULL DEFAULT '',
    connect_at     TEXT NOT NULL,
    disconnect_at  TEXT NOT NULL,
    duration_min   INTEGER NOT NULL,
    meter_start    REAL NOT NULL,
    meter_end      REAL NOT NULL,
    kwh            REAL NOT NULL,
    energy_price   REAL NOT NULL,
    service_price  REAL NOT NULL,
    energy_cents   INTEGER NOT NULL,
    service_cents  INTEGER NOT NULL,
    amount_cents   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_voyage   ON sessions(voyage_id);
CREATE INDEX IF NOT EXISTS idx_items_statement   ON statement_items(statement_id);
CREATE INDEX IF NOT EXISTS idx_items_session     ON statement_items(session_id);
CREATE INDEX IF NOT EXISTS idx_statements_voyage ON statements(voyage_id);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    """打开（必要时创建）数据库并确保表结构存在。

    check_same_thread=False 允许 HTTP 服务在多线程间共享连接，
    并发安全由 server 层的互斥锁保证。
    """
    conn = sqlite3.connect(path or DEFAULT_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
