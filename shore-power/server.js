/**
 * 港口岸电使用核算系统
 * 记录船舶接电时长与用电量，按航次出具对账明细，供船方与港方核对确认。
 */
const express = require('express');
const db = require('./db');
const views = require('./views');

const app = express();
app.use(express.urlencoded({ extended: false }));
app.use('/static', express.static(__dirname + '/public'));

const round2 = x => Math.round(x * 100) / 100;
const normDT = s => (s || '').replace('T', ' ').slice(0, 16);
const nowLocal = () => db.now().replace(' ', 'T');

/** 渲染页面（带导航与提示消息） */
function render(res, title, active, content, req) {
  res.send(views.layout(title, active, content, req.query.msg));
}
function fail(res, message) {
  res.status(400).send(views.layout('操作失败', '', views.error(message)));
}

/* ================= 仪表盘 ================= */
app.get('/', (req, res) => {
  const month = db.now().slice(0, 7); // YYYY-MM
  const stats = {
    ongoingCount: db.get(`SELECT COUNT(*) c FROM connections WHERE status='connected'`).c,
    monthKwh: db.get(`SELECT COALESCE(SUM(kwh),0) s FROM connections WHERE status='disconnected' AND disconnect_time LIKE ?`, [month + '%']).s,
    monthAmount: db.get(`SELECT COALESCE(SUM(total_amount),0) s FROM statements WHERE status='confirmed' AND created_at LIKE ?`, [month + '%']).s,
    pendingCount: db.get(`SELECT COUNT(*) c FROM statements WHERE status IN ('pending','ship_confirmed')`).c,
  };
  const ongoing = db.all(`
    SELECT c.*, s.name ship_name, v.voyage_no,
           CAST((julianday(?) - julianday(c.connect_time)) * 1440 AS INTEGER) elapsed
    FROM connections c
    JOIN voyages v ON v.id = c.voyage_id
    JOIN ships s ON s.id = v.ship_id
    WHERE c.status='connected' ORDER BY c.connect_time`, [db.now()]);
  const recentStatements = db.all(`
    SELECT st.*, s.name ship_name, v.voyage_no FROM statements st
    JOIN voyages v ON v.id = st.voyage_id JOIN ships s ON s.id = v.ship_id
    ORDER BY st.id DESC LIMIT 5`);
  render(res, '仪表盘', '/', views.dashboard({ stats, ongoing, recentStatements, month }), req);
});

/* ================= 船舶档案 ================= */
app.get('/ships', (req, res) => {
  render(res, '船舶档案', '/ships', views.ships(db.all(`SELECT * FROM ships ORDER BY id DESC`)), req);
});

app.post('/ships', (req, res) => {
  const { name, imo = '', company = '' } = req.body;
  if (!name || !name.trim()) return fail(res, '船名不能为空');
  db.run(`INSERT INTO ships(name, imo, company, created_at) VALUES (?,?,?,?)`,
    [name.trim(), imo.trim(), company.trim(), db.now()]);
  res.redirect('/ships?msg=' + encodeURIComponent('船舶已登记'));
});

app.post('/ships/:id/delete', (req, res) => {
  const used = db.get(`SELECT COUNT(*) c FROM voyages WHERE ship_id=?`, [req.params.id]).c;
  if (used > 0) return fail(res, '该船舶存在航次记录，不可删除');
  db.run(`DELETE FROM ships WHERE id=?`, [req.params.id]);
  res.redirect('/ships?msg=' + encodeURIComponent('已删除'));
});

/* ================= 航次管理 ================= */
app.get('/voyages', (req, res) => {
  const list = db.all(`
    SELECT v.*, s.name ship_name,
      (SELECT COUNT(*) FROM connections c WHERE c.voyage_id = v.id) conn_count
    FROM voyages v JOIN ships s ON s.id = v.ship_id ORDER BY v.id DESC`);
  const shipList = db.all(`SELECT * FROM ships ORDER BY name`);
  render(res, '航次管理', '/voyages', views.voyages(list, shipList), req);
});

app.post('/voyages', (req, res) => {
  const { ship_id, voyage_no, berth = '', arrival_time = '' } = req.body;
  if (!ship_id || !voyage_no || !voyage_no.trim()) return fail(res, '请选择船舶并填写航次号');
  db.run(`INSERT INTO voyages(voyage_no, ship_id, berth, arrival_time, status, created_at) VALUES (?,?,?,?, 'in_port', ?)`,
    [voyage_no.trim(), ship_id, berth.trim(), normDT(arrival_time), db.now()]);
  res.redirect('/voyages?msg=' + encodeURIComponent('航次已登记'));
});

app.get('/voyages/:id', (req, res) => {
  const voyage = db.get(`
    SELECT v.*, s.name ship_name, s.company FROM voyages v
    JOIN ships s ON s.id = v.ship_id WHERE v.id=?`, [req.params.id]);
  if (!voyage) return fail(res, '航次不存在');
  const connections = db.all(`SELECT * FROM connections WHERE voyage_id=? ORDER BY connect_time`, [voyage.id]);
  const statement = db.get(`SELECT * FROM statements WHERE voyage_id=? ORDER BY id DESC LIMIT 1`, [voyage.id]);
  const tariff = db.get(`SELECT * FROM tariffs WHERE is_active=1`);
  render(res, `航次 ${voyage.voyage_no}`, '/voyages',
    views.voyageDetail({ voyage, connections, statement, tariff, nowLocal: nowLocal() }), req);
});

app.post('/voyages/:id/depart', (req, res) => {
  const v = db.get(`SELECT * FROM voyages WHERE id=?`, [req.params.id]);
  if (!v) return fail(res, '航次不存在');
  const ongoing = db.get(`SELECT COUNT(*) c FROM connections WHERE voyage_id=? AND status='connected'`, [v.id]).c;
  if (ongoing > 0) return fail(res, '尚有接电中的记录，请先断电结算再办理离港');
  db.run(`UPDATE voyages SET status='departed', departure_time=? WHERE id=?`, [normDT(req.body.departure_time), v.id]);
  res.redirect(`/voyages/${v.id}?msg=` + encodeURIComponent('已办理离港'));
});

/* ================= 接电 / 断电 ================= */
app.post('/voyages/:id/connect', (req, res) => {
  const v = db.get(`SELECT * FROM voyages WHERE id=?`, [req.params.id]);
  if (!v) return fail(res, '航次不存在');
  if (v.status !== 'in_port') return fail(res, '该航次已离港，不可接电');
  const { point, connect_time, meter_start, operator = '' } = req.body;
  const multiplier = parseFloat(req.body.multiplier) || 1;
  const ms = parseFloat(meter_start);
  if (!point || !point.trim()) return fail(res, '请填写接电点');
  if (!connect_time) return fail(res, '请填写接电时间');
  if (!(ms >= 0)) return fail(res, '起始表码无效');
  db.run(`INSERT INTO connections(voyage_id, point, connect_time, meter_start, multiplier, operator, status)
          VALUES (?,?,?,?,?,?, 'connected')`,
    [v.id, point.trim(), normDT(connect_time), ms, multiplier, operator.trim()]);
  res.redirect(`/voyages/${v.id}?msg=` + encodeURIComponent('已接电'));
});

/* ================= 接电记录总览 ================= */
app.get('/connections', (req, res) => {
  const ongoing = db.all(`
    SELECT c.*, s.name ship_name, v.voyage_no FROM connections c
    JOIN voyages v ON v.id = c.voyage_id JOIN ships s ON s.id = v.ship_id
    WHERE c.status='connected' ORDER BY c.connect_time`);
  const recent = db.all(`
    SELECT c.*, s.name ship_name, v.voyage_no FROM connections c
    JOIN voyages v ON v.id = c.voyage_id JOIN ships s ON s.id = v.ship_id
    WHERE c.status='disconnected' ORDER BY c.disconnect_time DESC LIMIT 20`);
  render(res, '接电记录', '/connections', views.connections(ongoing, recent, nowLocal()), req);
});

app.post('/connections/:id/disconnect', (req, res) => {
  const c = db.get(`SELECT * FROM connections WHERE id=?`, [req.params.id]);
  if (!c) return fail(res, '接电记录不存在');
  if (c.status !== 'connected') return fail(res, '该记录已断电');
  const disconnectTime = normDT(req.body.disconnect_time);
  const me = parseFloat(req.body.meter_end);
  if (!disconnectTime) return fail(res, '请填写断电时间');
  if (!(me >= 0)) return fail(res, '结束表码无效');
  if (me < c.meter_start) return fail(res, `结束表码（${me}）不能小于起始表码（${c.meter_start}）`);
  if (disconnectTime <= c.connect_time) return fail(res, '断电时间必须晚于接电时间');
  const kwh = round2((me - c.meter_start) * (c.multiplier || 1));
  const dur = Math.round((new Date(disconnectTime) - new Date(c.connect_time)) / 60000);
  db.run(`UPDATE connections SET status='disconnected', disconnect_time=?, meter_end=?, kwh=?, duration_minutes=? WHERE id=?`,
    [disconnectTime, me, kwh, dur, c.id]);
  res.redirect(`/voyages/${c.voyage_id}?msg=` + encodeURIComponent(`已断电结算：用电 ${kwh} kWh，时长 ${views.fmtDur(dur)}`));
});

app.post('/connections/:id/delete', (req, res) => {
  const c = db.get(`SELECT * FROM connections WHERE id=?`, [req.params.id]);
  if (!c) return fail(res, '接电记录不存在');
  const used = db.get(`SELECT COUNT(*) c FROM statement_items WHERE connection_id=?`, [c.id]).c;
  if (used > 0) return fail(res, '该记录已计入对账单，不可删除');
  db.run(`DELETE FROM connections WHERE id=?`, [c.id]);
  res.redirect(`/voyages/${c.voyage_id}?msg=` + encodeURIComponent('已删除'));
});

/* ================= 电价设置 ================= */
app.get('/tariffs', (req, res) => {
  render(res, '电价设置', '/tariffs', views.tariffs(db.all(`SELECT * FROM tariffs ORDER BY id DESC`)), req);
});

app.post('/tariffs', (req, res) => {
  const { name, effective_from } = req.body;
  const price = parseFloat(req.body.price);
  if (!name || !name.trim()) return fail(res, '请填写方案名称');
  if (!(price >= 0)) return fail(res, '单价无效');
  if (!effective_from) return fail(res, '请选择生效日期');
  if (req.body.activate) db.run(`UPDATE tariffs SET is_active=0`);
  db.run(`INSERT INTO tariffs(name, price, effective_from, is_active) VALUES (?,?,?,?)`,
    [name.trim(), price, effective_from, req.body.activate ? 1 : 0]);
  res.redirect('/tariffs?msg=' + encodeURIComponent('电价方案已保存'));
});

app.post('/tariffs/:id/activate', (req, res) => {
  db.run(`UPDATE tariffs SET is_active=0`);
  db.run(`UPDATE tariffs SET is_active=1 WHERE id=?`, [req.params.id]);
  res.redirect('/tariffs?msg=' + encodeURIComponent('已切换执行电价'));
});

/* ================= 对账单 ================= */
function nextStatementNo() {
  const d = db.now().slice(0, 10).replace(/-/g, '');
  const prefix = `AD${d}-`;
  const row = db.get(`SELECT statement_no FROM statements WHERE statement_no LIKE ? ORDER BY statement_no DESC LIMIT 1`, [prefix + '%']);
  const seq = row ? parseInt(row.statement_no.slice(prefix.length), 10) + 1 : 1;
  return prefix + String(seq).padStart(3, '0');
}

app.post('/voyages/:id/statement', (req, res) => {
  const v = db.get(`SELECT * FROM voyages WHERE id=?`, [req.params.id]);
  if (!v) return fail(res, '航次不存在');
  const ongoing = db.get(`SELECT COUNT(*) c FROM connections WHERE voyage_id=? AND status='connected'`, [v.id]).c;
  if (ongoing > 0) return fail(res, '存在接电中的记录，请先断电结算');
  const conns = db.all(`SELECT * FROM connections WHERE voyage_id=? AND status='disconnected' ORDER BY connect_time`, [v.id]);
  if (conns.length === 0) return fail(res, '该航次暂无已完成的接电记录');
  const tariff = db.get(`SELECT * FROM tariffs WHERE is_active=1`);
  if (!tariff) return fail(res, '未设置执行电价，请先在「电价设置」中配置');

  const existing = db.get(`SELECT * FROM statements WHERE voyage_id=? ORDER BY id DESC LIMIT 1`, [v.id]);
  if (existing && ['confirmed', 'ship_confirmed'].includes(existing.status)) {
    return fail(res, '对账单已进入确认流程，不可重新出具');
  }
  if (existing) { // 作废旧的（待确认/有异议）对账单
    db.run(`DELETE FROM statement_items WHERE statement_id=?`, [existing.id]);
    db.run(`DELETE FROM statements WHERE id=?`, [existing.id]);
  }

  const totalKwh = round2(conns.reduce((s, c) => s + c.kwh, 0));
  const totalDur = conns.reduce((s, c) => s + c.duration_minutes, 0);
  const totalAmount = round2(totalKwh * tariff.price);
  const no = nextStatementNo();
  db.run(`INSERT INTO statements(statement_no, voyage_id, total_kwh, total_duration_minutes, price, total_amount, status, created_at)
          VALUES (?,?,?,?,?,?, 'pending', ?)`,
    [no, v.id, totalKwh, totalDur, tariff.price, totalAmount, db.now()]);
  const sid = db.get(`SELECT id FROM statements WHERE statement_no=?`, [no]).id;
  for (const c of conns) {
    db.run(`INSERT INTO statement_items(statement_id, connection_id, point, connect_time, disconnect_time, duration_minutes, meter_start, meter_end, multiplier, kwh, price, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
      [sid, c.id, c.point, c.connect_time, c.disconnect_time, c.duration_minutes,
       c.meter_start, c.meter_end, c.multiplier, c.kwh, tariff.price, round2(c.kwh * tariff.price)]);
  }
  res.redirect(`/statements/${sid}?msg=` + encodeURIComponent(`对账单 ${no} 已出具，待船方确认`));
});

app.get('/statements', (req, res) => {
  const list = db.all(`
    SELECT st.*, s.name ship_name, v.voyage_no FROM statements st
    JOIN voyages v ON v.id = st.voyage_id JOIN ships s ON s.id = v.ship_id
    ORDER BY st.id DESC`);
  render(res, '对账单', '/statements', views.statements(list), req);
});

app.get('/statements/:id', (req, res) => {
  const st = db.get(`SELECT * FROM statements WHERE id=?`, [req.params.id]);
  if (!st) return fail(res, '对账单不存在');
  const items = db.all(`SELECT * FROM statement_items WHERE statement_id=? ORDER BY connect_time`, [st.id]);
  const voyage = db.get(`
    SELECT v.*, s.name ship_name, s.company FROM voyages v
    JOIN ships s ON s.id = v.ship_id WHERE v.id=?`, [st.voyage_id]);
  render(res, `对账单 ${st.statement_no}`, '/statements', views.statementDetail({ st, items, voyage }), req);
});

app.post('/statements/:id/ship-confirm', (req, res) => {
  const st = db.get(`SELECT * FROM statements WHERE id=?`, [req.params.id]);
  if (!st) return fail(res, '对账单不存在');
  if (st.status !== 'pending') return fail(res, '当前状态不可由船方确认');
  const by = (req.body.by || '').trim();
  if (!by) return fail(res, '请填写船方确认人姓名');
  db.run(`UPDATE statements SET status='ship_confirmed', ship_confirmed_by=?, ship_confirmed_at=? WHERE id=?`,
    [by, db.now(), st.id]);
  res.redirect(`/statements/${st.id}?msg=` + encodeURIComponent('船方已确认，待港方确认'));
});

app.post('/statements/:id/port-confirm', (req, res) => {
  const st = db.get(`SELECT * FROM statements WHERE id=?`, [req.params.id]);
  if (!st) return fail(res, '对账单不存在');
  if (st.status !== 'ship_confirmed') return fail(res, '需船方先确认');
  const by = (req.body.by || '').trim();
  if (!by) return fail(res, '请填写港方确认人姓名');
  db.run(`UPDATE statements SET status='confirmed', port_confirmed_by=?, port_confirmed_at=? WHERE id=?`,
    [by, db.now(), st.id]);
  res.redirect(`/statements/${st.id}?msg=` + encodeURIComponent('双方确认完成，对账单已生效'));
});

app.post('/statements/:id/dispute', (req, res) => {
  const st = db.get(`SELECT * FROM statements WHERE id=?`, [req.params.id]);
  if (!st) return fail(res, '对账单不存在');
  if (!['pending', 'ship_confirmed'].includes(st.status)) return fail(res, '当前状态不可提出异议');
  const reason = (req.body.reason || '').trim();
  if (!reason) return fail(res, '请填写异议说明');
  db.run(`UPDATE statements SET status='disputed', dispute_reason=?,
          ship_confirmed_by='', ship_confirmed_at='' WHERE id=?`, [reason, st.id]);
  res.redirect(`/statements/${st.id}?msg=` + encodeURIComponent('已登记异议，请港方核实后重新出具对账单'));
});

app.use((req, res) => res.status(404).send(views.layout('页面不存在', '', views.error('页面不存在'))));

const PORT = process.env.PORT || 3000;
db.init().then(() => {
  app.listen(PORT, () => console.log(`港口岸电使用核算系统已启动: http://localhost:${PORT}`));
});
