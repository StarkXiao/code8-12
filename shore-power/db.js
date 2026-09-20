/**
 * 数据层：sql.js (WASM SQLite) + 文件持久化
 * 每次写操作后将数据库导出到 data/shorepower.db
 */
const fs = require('fs');
const path = require('path');
const initSqlJs = require('sql.js');

const DB_PATH = path.join(__dirname, 'data', 'shorepower.db');

let db;

const SCHEMA = `
-- 船舶档案
CREATE TABLE IF NOT EXISTS ships (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,                -- 船名
  imo        TEXT DEFAULT '',              -- IMO 编号
  company    TEXT DEFAULT '',              -- 船公司
  created_at TEXT DEFAULT ''
);

-- 航次
CREATE TABLE IF NOT EXISTS voyages (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  voyage_no      TEXT NOT NULL,            -- 航次号
  ship_id        INTEGER NOT NULL REFERENCES ships(id),
  berth          TEXT DEFAULT '',          -- 泊位
  arrival_time   TEXT DEFAULT '',          -- 靠泊时间
  departure_time TEXT DEFAULT '',          -- 离泊时间
  status         TEXT DEFAULT 'in_port',   -- in_port 在港 / departed 已离港
  created_at     TEXT DEFAULT ''
);

-- 岸电接电记录（一次接电-断电为一条）
CREATE TABLE IF NOT EXISTS connections (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  voyage_id        INTEGER NOT NULL REFERENCES voyages(id),
  point            TEXT DEFAULT '',        -- 接电点（岸电桩编号）
  connect_time     TEXT NOT NULL,          -- 接电时间
  disconnect_time  TEXT,                   -- 断电时间
  meter_start      REAL NOT NULL,          -- 起始表码 kWh
  meter_end        REAL,                   -- 结束表码 kWh
  multiplier       REAL DEFAULT 1,         -- 互感器倍率
  kwh              REAL,                   -- 用电量 = (meter_end - meter_start) * multiplier
  duration_minutes INTEGER,                -- 接电时长（分钟）
  operator         TEXT DEFAULT '',        -- 操作人员
  remark           TEXT DEFAULT '',
  status           TEXT DEFAULT 'connected' -- connected 接电中 / disconnected 已断电
);

-- 电价方案（元/kWh，含服务费）
CREATE TABLE IF NOT EXISTS tariffs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  name           TEXT NOT NULL,
  price          REAL NOT NULL,
  effective_from TEXT NOT NULL,
  is_active      INTEGER DEFAULT 0
);

-- 对账单（按航次出具）
CREATE TABLE IF NOT EXISTS statements (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  statement_no         TEXT UNIQUE NOT NULL,  -- 对账单号
  voyage_id            INTEGER NOT NULL REFERENCES voyages(id),
  total_kwh            REAL NOT NULL,
  total_duration_minutes INTEGER NOT NULL,
  price                REAL NOT NULL,         -- 电价快照（元/kWh）
  total_amount         REAL NOT NULL,         -- 应收电费（元）
  status               TEXT DEFAULT 'pending',
    -- pending 待船方确认 / ship_confirmed 船方已确认 / confirmed 双方已确认 / disputed 有异议
  dispute_reason       TEXT DEFAULT '',
  ship_confirmed_by    TEXT DEFAULT '',
  ship_confirmed_at    TEXT DEFAULT '',
  port_confirmed_by    TEXT DEFAULT '',
  port_confirmed_at    TEXT DEFAULT '',
  created_at           TEXT DEFAULT ''
);

-- 对账单明细（接电记录快照，出具后不受原始记录变更影响）
CREATE TABLE IF NOT EXISTS statement_items (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  statement_id     INTEGER NOT NULL REFERENCES statements(id),
  connection_id    INTEGER NOT NULL,
  point            TEXT DEFAULT '',
  connect_time     TEXT,
  disconnect_time  TEXT,
  duration_minutes INTEGER,
  meter_start      REAL,
  meter_end        REAL,
  multiplier       REAL,
  kwh              REAL,
  price            REAL,
  amount           REAL
);
`;

function all(sql, params = []) {
  const stmt = db.prepare(sql);
  stmt.bind(params);
  const rows = [];
  while (stmt.step()) rows.push(stmt.getAsObject());
  stmt.free();
  return rows;
}

function get(sql, params = []) {
  return all(sql, params)[0];
}

function run(sql, params = []) {
  db.run(sql, params);
  persist();
}

function persist() {
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  fs.writeFileSync(DB_PATH, Buffer.from(db.export()));
}

/** 当前本地时间 'YYYY-MM-DD HH:MM' */
function now() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function seed() {
  const t = now();
  run(`INSERT INTO ships(name, imo, company) VALUES
    ('远洋和谐号', 'IMO 9081723', '中远海运散货运输有限公司'),
    ('江海明珠号', 'IMO 9456201', '招商局能源运输股份有限公司')`);
  run(`INSERT INTO tariffs(name, price, effective_from, is_active) VALUES
    ('港口岸电综合电价（含服务费）', 0.85, '2026-01-01', 1)`);
  // 航次一：已离港，两段时间均已断电 —— 可出具对账单
  run(`INSERT INTO voyages(voyage_no, ship_id, berth, arrival_time, departure_time, status) VALUES
    ('V2026-0910', 2, '2# 泊位', '2026-09-10 08:20', '2026-09-12 17:40', 'departed')`);
  run(`INSERT INTO connections(voyage_id, point, connect_time, disconnect_time, meter_start, meter_end, multiplier, kwh, duration_minutes, operator, status) VALUES
    (1, '2#泊位-1号桩', '2026-09-10 10:05', '2026-09-11 06:30', 20115.0, 20548.6, 1, 433.6, 1225, '张工', 'disconnected'),
    (1, '2#泊位-1号桩', '2026-09-11 09:10', '2026-09-12 15:50', 20548.6, 21162.9, 1, 614.3, 1840, '张工', 'disconnected')`);
  // 航次二：在港，一段已断电、一段接电中
  run(`INSERT INTO voyages(voyage_no, ship_id, berth, arrival_time, status) VALUES
    ('V2026-0918', 1, '3# 泊位', '2026-09-18 14:10', 'in_port')`);
  run(`INSERT INTO connections(voyage_id, point, connect_time, disconnect_time, meter_start, meter_end, multiplier, kwh, duration_minutes, operator, status) VALUES
    (2, '3#泊位-2号桩', '2026-09-18 15:00', '2026-09-19 08:30', 10234.5, 10789.2, 1, 554.7, 1050, '李工', 'disconnected')`);
  run(`INSERT INTO connections(voyage_id, point, connect_time, meter_start, multiplier, operator, status) VALUES
    (2, '3#泊位-2号桩', '2026-09-20 07:40', 10789.2, 1, '李工', 'connected')`);
  // 为已离港航次预生成一张对账单（待船方确认）
  const conns = all(`SELECT * FROM connections WHERE voyage_id = 1 AND status = 'disconnected'`);
  const totalKwh = Math.round(conns.reduce((s, c) => s + c.kwh, 0) * 100) / 100;
  const totalDur = conns.reduce((s, c) => s + c.duration_minutes, 0);
  const price = 0.85;
  run(`INSERT INTO statements(statement_no, voyage_id, total_kwh, total_duration_minutes, price, total_amount, status, created_at)
       VALUES ('AD20260912-001', 1, ?, ?, ?, ?, 'pending', ?)`,
    [totalKwh, totalDur, price, Math.round(totalKwh * price * 100) / 100, t]);
  const sid = get(`SELECT id FROM statements WHERE statement_no = 'AD20260912-001'`).id;
  for (const c of conns) {
    run(`INSERT INTO statement_items(statement_id, connection_id, point, connect_time, disconnect_time, duration_minutes, meter_start, meter_end, multiplier, kwh, price, amount)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [sid, c.id, c.point, c.connect_time, c.disconnect_time, c.duration_minutes,
       c.meter_start, c.meter_end, c.multiplier, c.kwh, price, Math.round(c.kwh * price * 100) / 100]);
  }
}

async function init() {
  const SQL = await initSqlJs();
  if (fs.existsSync(DB_PATH)) {
    db = new SQL.Database(fs.readFileSync(DB_PATH));
  } else {
    db = new SQL.Database();
  }
  db.exec(SCHEMA);
  if (!get('SELECT id FROM ships LIMIT 1')) seed();
  persist();
  return module.exports;
}

module.exports = { init, all, get, run, now, DB_PATH };
