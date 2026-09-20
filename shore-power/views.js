/** 视图层：服务端渲染 HTML */

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const fmtDT = s => s ? esc(s).slice(0, 16) : '<span class="muted">—</span>';
const fmtKwh = n => n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmtMoney = n => n == null ? '—' : '¥' + Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function fmtDur(min) {
  if (min == null) return '—';
  const h = Math.floor(min / 60), m = min % 60;
  return h > 0 ? `${h}小时${m > 0 ? m + '分' : ''}` : `${m}分钟`;
}

const CONN_STATUS = {
  connected: ['接电中', 'green'],
  disconnected: ['已断电', 'gray'],
};
const ST_STATUS = {
  pending: ['待船方确认', 'orange'],
  ship_confirmed: ['船方已确认', 'blue'],
  confirmed: ['双方已确认', 'green'],
  disputed: ['有异议', 'red'],
};
const VOY_STATUS = {
  in_port: ['在港', 'green'],
  departed: ['已离港', 'gray'],
};

const badge = ([label, color]) => `<span class="badge badge-${color}">${label}</span>`;

const NAV = [
  ['/', '仪表盘'],
  ['/voyages', '航次管理'],
  ['/connections', '接电记录'],
  ['/statements', '对账单'],
  ['/ships', '船舶档案'],
  ['/tariffs', '电价设置'],
];

function layout(title, active, content, msg) {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · 港口岸电使用核算系统</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header class="topbar">
  <div class="brand">⚡ 港口岸电使用核算系统</div>
  <nav>${NAV.map(([href, label]) =>
    `<a href="${href}" class="${active === href ? 'active' : ''}">${label}</a>`).join('')}
  </nav>
</header>
<main class="container">
${msg ? `<div class="flash">${esc(msg)}</div>` : ''}
${content}
</main>
<footer class="footer">港口岸电使用核算系统 · 供船方与港方核对确认用电明细</footer>
</body>
</html>`;
}

/* ---------- 仪表盘 ---------- */
function dashboard({ stats, ongoing, recentStatements, month }) {
  return `
<h1>仪表盘</h1>
<div class="cards">
  <div class="card"><div class="card-num">${stats.ongoingCount}</div><div class="card-label">当前接电中（条）</div></div>
  <div class="card"><div class="card-num">${fmtKwh(stats.monthKwh)}</div><div class="card-label">本月用电量（kWh）</div></div>
  <div class="card"><div class="card-num">${fmtMoney(stats.monthAmount)}</div><div class="card-label">本月已确认电费</div></div>
  <div class="card"><div class="card-num">${stats.pendingCount}</div><div class="card-label">待确认对账单</div></div>
</div>

<h2>接电中的船舶</h2>
${ongoing.length ? `<table>
<thead><tr><th>船名</th><th>航次</th><th>接电点</th><th>接电时间</th><th>起始表码</th><th>已接电时长</th><th>操作员</th></tr></thead>
<tbody>${ongoing.map(c => `<tr>
  <td>${esc(c.ship_name)}</td><td>${esc(c.voyage_no)}</td><td>${esc(c.point)}</td>
  <td>${fmtDT(c.connect_time)}</td><td>${c.meter_start.toFixed(1)}</td>
  <td>${fmtDur(c.elapsed)}</td><td>${esc(c.operator)}</td></tr>`).join('')}
</tbody></table>` : '<p class="muted">当前没有接电中的船舶。</p>'}

<h2>最近对账单</h2>
${recentStatements.length ? statementTable(recentStatements) : '<p class="muted">暂无对账单。</p>'}
`;
}

function statementTable(list) {
  return `<table>
<thead><tr><th>对账单号</th><th>船名 / 航次</th><th>用电量(kWh)</th><th>接电时长</th><th>金额</th><th>状态</th><th>出具时间</th><th></th></tr></thead>
<tbody>${list.map(s => `<tr>
  <td>${esc(s.statement_no)}</td>
  <td>${esc(s.ship_name)} / ${esc(s.voyage_no)}</td>
  <td class="num">${fmtKwh(s.total_kwh)}</td>
  <td>${fmtDur(s.total_duration_minutes)}</td>
  <td class="num">${fmtMoney(s.total_amount)}</td>
  <td>${badge(ST_STATUS[s.status])}</td>
  <td>${fmtDT(s.created_at)}</td>
  <td><a class="btn btn-sm" href="/statements/${s.id}">查看</a></td></tr>`).join('')}
</tbody></table>`;
}

/* ---------- 船舶档案 ---------- */
function ships(list, msg) {
  return `
<h1>船舶档案</h1>
<div class="panel">
  <h2>登记船舶</h2>
  <form method="post" action="/ships" class="form-inline">
    <input name="name" placeholder="船名 *" required>
    <input name="imo" placeholder="IMO 编号">
    <input name="company" placeholder="船公司">
    <button class="btn btn-primary">登记</button>
  </form>
</div>
<table>
<thead><tr><th>船名</th><th>IMO 编号</th><th>船公司</th><th>登记时间</th><th></th></tr></thead>
<tbody>${list.map(s => `<tr>
  <td>${esc(s.name)}</td><td>${esc(s.imo)}</td><td>${esc(s.company)}</td><td>${fmtDT(s.created_at)}</td>
  <td><form method="post" action="/ships/${s.id}/delete" onsubmit="return confirm('确定删除该船舶？')"><button class="btn btn-sm btn-danger">删除</button></form></td>
</tr>`).join('')}
</tbody></table>`;
}

/* ---------- 航次管理 ---------- */
function voyages(list, shipList) {
  return `
<h1>航次管理</h1>
<div class="panel">
  <h2>登记航次（船舶靠港）</h2>
  <form method="post" action="/voyages" class="form-inline">
    <select name="ship_id" required>
      <option value="">选择船舶 *</option>
      ${shipList.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}
    </select>
    <input name="voyage_no" placeholder="航次号 *" required>
    <input name="berth" placeholder="泊位，如 3# 泊位">
    <label>靠泊时间 <input type="datetime-local" name="arrival_time"></label>
    <button class="btn btn-primary">登记</button>
  </form>
</div>
<table>
<thead><tr><th>航次号</th><th>船名</th><th>泊位</th><th>靠泊时间</th><th>离泊时间</th><th>状态</th><th>接电次数</th><th></th></tr></thead>
<tbody>${list.map(v => `<tr>
  <td>${esc(v.voyage_no)}</td><td>${esc(v.ship_name)}</td><td>${esc(v.berth)}</td>
  <td>${fmtDT(v.arrival_time)}</td><td>${fmtDT(v.departure_time)}</td>
  <td>${badge(VOY_STATUS[v.status])}</td><td class="num">${v.conn_count}</td>
  <td><a class="btn btn-sm" href="/voyages/${v.id}">详情 / 接电</a></td></tr>`).join('')}
</tbody></table>`;
}

/* ---------- 航次详情（接电操作 + 出具对账单） ---------- */
function voyageDetail({ voyage, connections, statement, tariff, nowLocal }) {
  const canStatement = connections.some(c => c.status === 'disconnected');
  const hasOngoing = connections.some(c => c.status === 'connected');
  return `
<p><a href="/voyages">← 返回航次列表</a></p>
<h1>航次 ${esc(voyage.voyage_no)} <small>${esc(voyage.ship_name)}（${esc(voyage.company || '—')}）</small></h1>
<div class="cards">
  <div class="card"><div class="card-label">泊位</div><div class="card-num sm">${esc(voyage.berth || '—')}</div></div>
  <div class="card"><div class="card-label">靠泊时间</div><div class="card-num sm">${esc(voyage.arrival_time || '—')}</div></div>
  <div class="card"><div class="card-label">离泊时间</div><div class="card-num sm">${esc(voyage.departure_time || '—')}</div></div>
  <div class="card"><div class="card-label">状态</div><div class="card-num sm">${badge(VOY_STATUS[voyage.status])}</div></div>
</div>

${voyage.status === 'in_port' ? `
<div class="panel">
  <h2>办理接电</h2>
  <form method="post" action="/voyages/${voyage.id}/connect" class="form-inline">
    <input name="point" placeholder="接电点（岸电桩编号）*" required>
    <label>接电时间 <input type="datetime-local" name="connect_time" value="${nowLocal}" required></label>
    <input name="meter_start" type="number" step="0.1" min="0" placeholder="起始表码 kWh *" required>
    <input name="multiplier" type="number" step="1" min="1" value="1" title="互感器倍率" style="width:5em">
    <input name="operator" placeholder="操作员">
    <button class="btn btn-primary">接电</button>
  </form>
  <p class="muted">当前执行电价：${tariff ? `${esc(tariff.name)} ${tariff.price.toFixed(2)} 元/kWh` : '<b>未设置电价，请先到「电价设置」配置</b>'}</p>
</div>` : ''}

<h2>接电记录</h2>
${connections.length ? `<table>
<thead><tr><th>接电点</th><th>接电时间</th><th>断电时间</th><th>时长</th><th>表码 起→止</th><th>倍率</th><th>用电量(kWh)</th><th>操作员</th><th>状态</th><th></th></tr></thead>
<tbody>${connections.map(c => `<tr>
  <td>${esc(c.point)}</td><td>${fmtDT(c.connect_time)}</td><td>${fmtDT(c.disconnect_time)}</td>
  <td>${fmtDur(c.duration_minutes)}</td>
  <td class="num">${c.meter_start.toFixed(1)} → ${c.meter_end == null ? '—' : c.meter_end.toFixed(1)}</td>
  <td class="num">${c.multiplier}</td>
  <td class="num"><b>${fmtKwh(c.kwh)}</b></td>
  <td>${esc(c.operator)}</td><td>${badge(CONN_STATUS[c.status])}</td>
  <td>${c.status === 'connected' ? `
    <form method="post" action="/connections/${c.id}/disconnect" class="form-inline tight">
      <input type="datetime-local" name="disconnect_time" value="${nowLocal}" required>
      <input name="meter_end" type="number" step="0.1" min="${c.meter_start}" placeholder="止码" required style="width:7em">
      <button class="btn btn-sm btn-primary">断电</button>
    </form>` : `
    <form method="post" action="/connections/${c.id}/delete" onsubmit="return confirm('删除该接电记录？')"><button class="btn btn-sm btn-danger">删除</button></form>`}
  </td></tr>`).join('')}
</tbody></table>` : '<p class="muted">暂无接电记录。</p>'}

<h2>对账单</h2>
${statement ? `
<p>最新对账单：<a href="/statements/${statement.id}"><b>${esc(statement.statement_no)}</b></a>
　状态：${badge(ST_STATUS[statement.status])}　用电量：${fmtKwh(statement.total_kwh)} kWh　金额：${fmtMoney(statement.total_amount)}</p>` : ''}
${['confirmed', 'ship_confirmed'].includes(statement?.status)
  ? `<p class="muted">对账单已进入确认流程（${ST_STATUS[statement.status][0]}），不可重新出具。</p>`
  : `<form method="post" action="/voyages/${voyage.id}/statement" onsubmit="return confirm('${statement ? '将作废现有对账单并重新出具，' : ''}按当前已完成接电记录出具对账单？')">
     <button class="btn btn-primary" ${canStatement && !hasOngoing ? '' : 'disabled'}>${statement ? '重新出具对账单' : '出具对账单'}</button>
     ${hasOngoing ? '<span class="warn">存在接电中的记录，请先断电结算后再出具对账单。</span>' : ''}
     ${!canStatement ? '<span class="muted">暂无已完成的接电记录。</span>' : ''}
   </form>`}

${voyage.status === 'in_port' ? `
<h2>离港</h2>
<form method="post" action="/voyages/${voyage.id}/depart" class="form-inline">
  <label>离泊时间 <input type="datetime-local" name="departure_time" value="${nowLocal}" required></label>
  <button class="btn">办理离港</button>
</form>` : ''}
`;
}

/* ---------- 接电记录总览 ---------- */
function connections(ongoing, recent, nowLocal) {
  return `
<h1>接电记录</h1>
<h2>接电中（${ongoing.length}）</h2>
${ongoing.length ? `<table>
<thead><tr><th>船名 / 航次</th><th>接电点</th><th>接电时间</th><th>起始表码</th><th>操作员</th><th>断电操作</th></tr></thead>
<tbody>${ongoing.map(c => `<tr>
  <td><a href="/voyages/${c.voyage_id}">${esc(c.ship_name)} / ${esc(c.voyage_no)}</a></td>
  <td>${esc(c.point)}</td><td>${fmtDT(c.connect_time)}</td>
  <td class="num">${c.meter_start.toFixed(1)}</td><td>${esc(c.operator)}</td>
  <td><form method="post" action="/connections/${c.id}/disconnect" class="form-inline tight">
    <input type="datetime-local" name="disconnect_time" value="${nowLocal}" required>
    <input name="meter_end" type="number" step="0.1" min="${c.meter_start}" placeholder="结束表码" required style="width:8em">
    <button class="btn btn-sm btn-primary">断电结算</button>
  </form></td></tr>`).join('')}
</tbody></table>` : '<p class="muted">当前没有接电中的记录。</p>'}

<h2>最近断电记录</h2>
${recent.length ? `<table>
<thead><tr><th>船名 / 航次</th><th>接电点</th><th>接电时间</th><th>断电时间</th><th>时长</th><th>用电量(kWh)</th></tr></thead>
<tbody>${recent.map(c => `<tr>
  <td><a href="/voyages/${c.voyage_id}">${esc(c.ship_name)} / ${esc(c.voyage_no)}</a></td>
  <td>${esc(c.point)}</td><td>${fmtDT(c.connect_time)}</td><td>${fmtDT(c.disconnect_time)}</td>
  <td>${fmtDur(c.duration_minutes)}</td><td class="num"><b>${fmtKwh(c.kwh)}</b></td></tr>`).join('')}
</tbody></table>` : '<p class="muted">暂无。</p>'}
`;
}

/* ---------- 电价设置 ---------- */
function tariffs(list) {
  return `
<h1>电价设置</h1>
<div class="panel">
  <h2>新增电价方案</h2>
  <form method="post" action="/tariffs" class="form-inline">
    <input name="name" placeholder="方案名称 *" required>
    <input name="price" type="number" step="0.01" min="0" placeholder="单价 元/kWh *" required style="width:9em">
    <label>生效日期 <input type="date" name="effective_from" required></label>
    <label><input type="checkbox" name="activate" checked> 设为当前执行电价</label>
    <button class="btn btn-primary">保存</button>
  </form>
  <p class="muted">出具对账单时按当前执行电价计费，并将电价快照保存在对账单中。</p>
</div>
<table>
<thead><tr><th>方案名称</th><th>单价（元/kWh）</th><th>生效日期</th><th>状态</th><th></th></tr></thead>
<tbody>${list.map(t => `<tr>
  <td>${esc(t.name)}</td><td class="num">${t.price.toFixed(2)}</td><td>${esc(t.effective_from)}</td>
  <td>${t.is_active ? '<span class="badge badge-green">执行中</span>' : '<span class="badge badge-gray">停用</span>'}</td>
  <td>${t.is_active ? '' : `<form method="post" action="/tariffs/${t.id}/activate"><button class="btn btn-sm">启用</button></form>`}</td>
</tr>`).join('')}
</tbody></table>`;
}

/* ---------- 对账单列表 ---------- */
function statements(list) {
  return `
<h1>对账单</h1>
${list.length ? statementTable(list) : '<p class="muted">暂无对账单。请在「航次管理 → 航次详情」中出具。</p>'}
`;
}

/* ---------- 对账单详情（对账确认单） ---------- */
function statementDetail({ st, items, voyage }) {
  const act = (label, action, color, extra = '') => `
    <form method="post" action="/statements/${st.id}/${action}" class="confirm-box ${color}">
      <h3>${label}</h3>${extra}
    </form>`;
  return `
<p class="no-print"><a href="/statements">← 返回对账单列表</a>　<button class="btn" onclick="window.print()">打印对账单</button></p>
<div class="doc">
  <h1 class="doc-title">港口岸电使用对账单</h1>
  <div class="doc-meta">
    <span>对账单号：<b>${esc(st.statement_no)}</b></span>
    <span>出具时间：${esc(st.created_at)}</span>
    <span>状态：${badge(ST_STATUS[st.status])}</span>
  </div>
  <table class="doc-info">
    <tr><td>船名</td><td>${esc(voyage.ship_name)}</td><td>船公司</td><td>${esc(voyage.company || '—')}</td></tr>
    <tr><td>航次</td><td>${esc(voyage.voyage_no)}</td><td>泊位</td><td>${esc(voyage.berth || '—')}</td></tr>
    <tr><td>靠泊时间</td><td>${esc(voyage.arrival_time || '—')}</td><td>离泊时间</td><td>${esc(voyage.departure_time || '—')}</td></tr>
  </table>

  <table class="doc-items">
    <thead><tr><th>序号</th><th>接电点</th><th>接电时间</th><th>断电时间</th><th>接电时长</th><th>表码起→止</th><th>倍率</th><th>用电量(kWh)</th><th>单价(元)</th><th>金额(元)</th></tr></thead>
    <tbody>${items.map((it, i) => `<tr>
      <td>${i + 1}</td><td>${esc(it.point)}</td><td>${esc(it.connect_time)}</td><td>${esc(it.disconnect_time)}</td>
      <td>${fmtDur(it.duration_minutes)}</td>
      <td class="num">${it.meter_start.toFixed(1)} → ${it.meter_end.toFixed(1)}</td>
      <td class="num">${it.multiplier}</td>
      <td class="num">${fmtKwh(it.kwh)}</td><td class="num">${it.price.toFixed(2)}</td>
      <td class="num">${it.amount.toFixed(2)}</td></tr>`).join('')}
    </tbody>
    <tfoot><tr>
      <td colspan="4"><b>合计</b></td>
      <td><b>${fmtDur(st.total_duration_minutes)}</b></td><td colspan="2"></td>
      <td class="num"><b>${fmtKwh(st.total_kwh)}</b></td><td></td>
      <td class="num"><b>${st.total_amount.toFixed(2)}</b></td>
    </tr></tfoot>
  </table>
  <p class="doc-total">应收电费合计：<b>${fmtMoney(st.total_amount)}</b>（按 ${st.price.toFixed(2)} 元/kWh 计）</p>

  ${st.status === 'disputed' ? `<div class="dispute">异议说明：${esc(st.dispute_reason)}</div>` : ''}

  <div class="sign-row">
    <div class="sign-box">
      <h3>船方确认</h3>
      ${st.ship_confirmed_by
        ? `<p>确认人：${esc(st.ship_confirmed_by)}</p><p>确认时间：${esc(st.ship_confirmed_at)}</p><p class="ok">✔ 已确认，用电量与金额无误</p>`
        : st.status === 'pending' ? `
        <form method="post" action="/statements/${st.id}/ship-confirm" class="form-inline tight no-print">
          <input name="by" placeholder="船方确认人姓名 *" required>
          <button class="btn btn-primary">确认无误</button>
        </form>
        <form method="post" action="/statements/${st.id}/dispute" class="form-inline tight no-print">
          <input name="reason" placeholder="异议说明 *" required>
          <button class="btn btn-danger">提出异议</button>
        </form>` : '<p class="muted">待确认</p>'}
    </div>
    <div class="sign-box">
      <h3>港方确认</h3>
      ${st.port_confirmed_by
        ? `<p>确认人：${esc(st.port_confirmed_by)}</p><p>确认时间：${esc(st.port_confirmed_at)}</p><p class="ok">✔ 已确认</p>`
        : st.status === 'ship_confirmed' ? `
        <form method="post" action="/statements/${st.id}/port-confirm" class="form-inline tight no-print">
          <input name="by" placeholder="港方确认人姓名 *" required>
          <button class="btn btn-primary">确认</button>
        </form>
        <form method="post" action="/statements/${st.id}/dispute" class="form-inline tight no-print">
          <input name="reason" placeholder="异议说明 *" required>
          <button class="btn btn-danger">提出异议</button>
        </form>` : '<p class="muted">待船方确认后由港方确认</p>'}
    </div>
  </div>
</div>`;
}

function error(message) {
  return `<h1>操作失败</h1><div class="flash error">${esc(message)}</div><p><a href="javascript:history.back()">← 返回</a></p>`;
}

module.exports = {
  layout, dashboard, ships, voyages, voyageDetail, connections,
  tariffs, statements, statementDetail, error,
  fmtDur, fmtKwh, fmtMoney,
};
