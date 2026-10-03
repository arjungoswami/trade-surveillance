/* Trade surveillance dashboard.
   Everything on screen comes from the surveillance tools over /api - no fixtures.
   The investigation workspace that sits beside the Telegram channel. */
'use strict';

const $ = (s) => document.querySelector(s);
const el = (t, cls, txt) => { const n = document.createElement(t); if (cls) n.className = cls; if (txt != null) n.textContent = txt; return n; };
const SVGNS = 'http://www.w3.org/2000/svg';
const svg = (t, attrs = {}, txt) => {
  const n = document.createElementNS(SVGNS, t);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (txt != null) n.textContent = txt;
  return n;
};
const secs = (hms) => { const p = String(hms).split(':'); return (+p[0]) * 3600 + (+p[1]) * 60 + parseFloat(p[2] || 0); };
const hms = (s) => [s / 3600, (s % 3600) / 60, s % 60].map((v) => String(Math.floor(v)).padStart(2, '0')).join(':');
const num = (v, d = 2) => (v == null || isNaN(v)) ? '-' : (+v).toLocaleString('en-US', { maximumFractionDigits: d });

/* Phrases that read as an attempt to steer the review - flagged, never obeyed. */
const STEER = /surveillance|review bot|compliance bot|(do ?not|don'?t) escalate|close (it|this) (as|with)|known false[- ]positive|no need to (escalate|review|look)/i;

const STATE = { boot: null, filter: 'open', alertId: null, case: null, job: null, timer: null };

/* ------------------------------------------------------------------ boot */

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (j && j.error) throw new Error(j.error);
  return j;
}

async function boot() {
  const b = await api('/api/bootstrap');
  STATE.boot = b;
  $('#m-day').textContent = b.day;
  $('#m-runtime').textContent = 'LOCAL';
  $('#m-model').textContent = b.model.name || 'none configured';
  const st = b.summary.by_status || {};
  $('#m-queue').textContent = `${(st.closed || 0) + (st.escalated || 0)} / ${b.summary.total_alerts} TRIAGED`;
  if (!b.model.enabled) {
    $('#m-status').className = 'pill off';
    $('#m-status').innerHTML = '<i class="d"></i>AGENT OFFLINE';
    $('#run-agent').disabled = true;
    $('#run-agent').title = 'Start the server with --model <name> to enable';
  }
  renderQueue();
  // #A-0109 opens that case directly - this is the link the Telegram message carries
  const wanted = decodeURIComponent(location.hash.slice(1));
  const fallback = b.alerts.find((a) => a.status === 'open') || b.alerts[0];
  const id = /^A-\d+$/.test(wanted) ? wanted : (fallback && fallback.alert_id);
  if (id) selectAlert(id);
}

window.addEventListener('hashchange', () => {
  const id = decodeURIComponent(location.hash.slice(1));
  if (id && id !== STATE.alertId) selectAlert(id);
});

/* ------------------------------------------------------------------ queue */

function renderQueue() {
  const b = STATE.boot;
  const list = b.alerts.filter((a) => STATE.filter === 'all' || a.status === STATE.filter);
  $('#q-count').textContent = `${list.length} / ${b.total_alerts}`;
  const box = $('#queue');
  box.textContent = '';
  list.slice(0, 60).forEach((a) => {
    const row = el('div', 'q' + (a.alert_id === STATE.alertId ? ' sel' : ''));
    row.append(el('span', 'dt ' + a.status));
    row.append(el('span', 'id', a.alert_id));
    row.append(el('span', 'nmm', `${a.trader.split(' (')[0]} / ${a.rule.toLowerCase().replace('_', ' ')}`));
    row.append(el('span', 'sc', a.score));
    row.onclick = () => selectAlert(a.alert_id);
    box.append(row);
  });
  if (!list.length) box.append(el('div', 'dd', 'Nothing in this filter.'));
}

$('#q-filter').onclick = (e) => {
  const b = e.target.closest('button'); if (!b) return;
  STATE.filter = b.dataset.f;
  [...$('#q-filter').children].forEach((x) => x.classList.toggle('on', x === b));
  renderQueue();
};

/* ------------------------------------------------------------------ case */

async function selectAlert(id) {
  STATE.alertId = id;
  if (decodeURIComponent(location.hash.slice(1)) !== id) history.replaceState(null, '', '#' + id);
  renderQueue();
  $('#s-text').textContent = 'Gathering evidence...';
  const c = await api('/api/case?alert_id=' + encodeURIComponent(id));
  STATE.case = c;
  renderCase(c);
}

function findings(c) {
  /* Facts the tools actually returned, as short lines. Used for the summary,
     the chips, and as the evidence list when escalating. */
  const out = [];
  const a = c.alert, m = a.metrics || {}, h = c.derived.hedge, st = c.derived.order_stats;
  const exec = m.exec_side || (st.BUY && st.BUY.filled_qty > (st.SELL ? st.SELL.filled_qty : 0) ? 'BUY' : 'SELL');
  const opp = exec === 'BUY' ? 'SELL' : 'BUY';
  if (m.opposite_qty != null) {
    out.push(`${num(m.opposite_qty)} ${a.symbol.split('-')[0]} of ${opp} orders posted, then ${exec} ${num(m.exec_qty)} - ratio ${num(m.qty_ratio, 1)}x`);
  }
  if (m.cancel_fraction != null) out.push(`${Math.round(m.cancel_fraction * 100)}% of the opposite orders cancelled` +
    (st.cancelled_order_median_life_s ? ` (median life ${num(st.cancelled_order_median_life_s, 1)}s)` : ''));
  if (m.favourable_move_bps != null) {
    out.push(`price moved ${num(m.favourable_move_bps, 1)} bps ${m.favourable_move_bps > 0 ? 'in the trader\'s favour' : 'against the trader'} over the window`);
  }
  const side = st[opp] || st[exec];
  if (side && side.size_vs_typical) out.push(`median order ${num(side.median_new_qty)} vs their ${num(st.typical_order_qty)} norm today (${num(side.size_vs_typical, 1)}x)`);
  if (h) {
    out.push(h.reconciles
      ? `options delta ${num(h.option_delta)} needs a ${h.hedge_required_side} of ~${num(h.hedge_required_qty)}; observed ${h.observed_side} ${num(h.observed_qty)} - consistent`
      : `options delta ${num(h.option_delta)} needs a ${h.hedge_required_side} of ~${num(h.hedge_required_qty)}; observed ${h.observed_side} ${num(h.observed_qty)}` +
        (h.same_direction ? ` - ${num(h.ratio, 1)}x too large` : ' - opposite direction'));
  }
  const hist = c.history;
  if (hist && hist.prior_alerts_total != null) {
    const esc = (hist.prior_by_disposition || {}).escalated || 0;
    out.push(`${hist.prior_alerts_total} prior alerts, ${esc} previously escalated`);
  }
  const e = c.extras;
  const sig = c._signals || (c._signals = signals(c));
  sig.steering.forEach((msg) => out.push(
    `${msg.channel} ${msg.t.slice(0, 8)} - message attempts to steer the surveillance review: "${(msg.text || '').slice(0, 120)}"`));
  if (e.preclearance) {
    out.push(sig.preclearGap
      ? `no pre-clearance approval for ${a.symbol} on ${e.preclearance.today} - the personal account ${a.account} traded anyway`
      : `${sig.approvals.length} pre-clearance approval(s) for ${a.symbol} on ${e.preclearance.today}`);
  }
  if (sig.algo) {
    out.push(`parent algo ${sig.algo.parent_id} created ${sig.algo.created}` +
      (sig.clientOrder ? `, client order ${sig.clientOrder.client_order_id} received ${sig.clientOrder.received}` +
        (sig.algoAfterClient ? ' - the algo was created AFTER the client order' : ' - the algo predates it') : '') +
      (sig.algo.approved_by ? `, approved by ${sig.algo.approved_by}` : ', no approval recorded'));
  }
  if (e.contra_account) {
    const ca = e.contra_account;
    out.push(`contra account ${ca.account_id} - ${ca.type}, beneficial owner ${ca.beneficial_owner}, controlled by ${ca.controlled_by}`);
  }
  if (e.client_orders && e.client_orders.client_orders && e.client_orders.client_orders.length) {
    const co = e.client_orders.client_orders[0];
    out.push(`client order ${co.client_order_id} (${co.client}, ${co.side} ${num(co.qty)}) received ${co.received}`);
  }
  return out;
}

/* The checks the rules can't make: does the stated explanation actually hold up?
   A hedge must match the options position in sign and size; a personal-account
   trade needs pre-clearance for that symbol on that day; a "pre-scheduled" algo
   must predate the client order it is supposed to be unrelated to. */
function signals(c) {
  const a = c.alert, e = c.extras, out = {};
  if (e.preclearance) {
    const reqs = e.preclearance.requests || [];
    out.approvals = reqs.filter((r) => r.symbol === a.symbol && r.status === 'APPROVED' &&
      String(r.date) === String(e.preclearance.today));
    out.preclearGap = String(a.account).startsWith('PA-') && out.approvals.length === 0;
  }
  if (e.client_orders) {
    const list = e.client_orders.client_orders || [];
    out.clientOrder = list.find((o) => o.handling_trader === a.trader_id) || list[0];
  }
  if (e.algos) {
    out.algo = (e.algos.algo_orders || []).find((x) => x.symbol === a.symbol);
    if (out.algo && out.clientOrder) {
      out.algoAfterClient = secs(out.algo.created) > secs(out.clientOrder.received);
      out.algoUnapproved = !out.algo.approved_by;
    }
  }
  // a message trying to steer the surveillance review is itself a red flag
  out.steering = (c.chats.messages || []).filter((m) => STEER.test(m.text || ''));
  return out;
}

function summaryText(c) {
  const a = c.alert, h = c.derived.hedge;
  const bits = [];
  bits.push(`<b>${a.trader_name}</b> tripped ${a.rule.replace('_', ' ').toLowerCase()} on <b>${a.symbol}</b> at ${a.window_start.slice(0, 8)}.`);
  bits.push(a.summary + '.');
  if (h) {
    bits.push(h.reconciles
      ? `The options book explains it: delta ${num(h.option_delta)} calls for a <span class="g">${h.hedge_required_side} ~${num(h.hedge_required_qty)}</span> and the desk did <span class="g">${h.observed_side} ${num(h.observed_qty)}</span>.`
      : `A hedge does not explain it: delta ${num(h.option_delta)} calls for a <span class="g">${h.hedge_required_side} ~${num(h.hedge_required_qty)}</span>, but the desk did <span class="r">${h.observed_side} ${num(h.observed_qty)}</span>${h.same_direction ? ` - ${num(h.ratio, 1)}x oversized` : ' - the opposite direction'}.`);
  }
  if (c.disposition) {
    const d = c.disposition;
    bits.push(`<b>Agent decision:</b> ${d.decision} at suspicion ${d.suspicion}. ${d.rationale || d.summary || ''}`);
  }
  return bits.join(' ');
}

function renderCase(c) {
  const a = c.alert;
  $('#c-id').textContent = a.alert_id;
  $('#c-status').textContent = a.status;
  $('#c-rule').textContent = a.rule.replace('_', ' ');
  $('#c-trader').innerHTML = `${a.trader_name}<br><em>${a.symbol}</em>`;
  const meta = $('#c-meta'); meta.textContent = '';
  const rows = [['DESK', `${c.profile.desk} / ${c.profile.role}`], ['ACCOUNT', a.account],
    ['WINDOW', `${a.window_start.slice(0, 8)} - ${a.window_end.slice(0, 8)} UTC`],
    ['RAISED', `rule score ${a.score} / 100`]];
  rows.forEach(([k, v]) => { meta.append(el('s', '', k)); meta.append(el('div', '', v)); });

  // score block: the rule score until an agent decision exists, then the agent's suspicion
  const d = c.disposition;
  const score = d ? d.suspicion : a.score;
  const block = $('#riskblock');
  block.className = 'riskblock' + (d ? ' ' + d.decision : '');
  $('#r-label').textContent = d ? 'AGENT SUSPICION' : 'RULE SCORE';
  $('#r-score').textContent = score;
  $('#r-band').textContent = score >= 70 ? 'HIGH' : score >= 35 ? 'MEDIUM' : 'LOW';
  $('#r-bar').style.width = score + '%';
  $('#r-reclabel').textContent = d ? 'DECIDED' : 'DISPOSITION';
  $('#r-rec').textContent = d ? (d.decision === 'escalated' ? 'Escalated' : 'Closed') : 'Open';

  $('#s-label').textContent = d ? 'Agent decision' : 'Automated findings - computed from tool output';
  $('#s-text').innerHTML = summaryText(c);

  // metric rows
  const mbox = $('#metrics'); mbox.textContent = '';
  const h = c.derived.hedge, m = a.metrics || {};
  const row = (cls, tag, title, sub, val, unit) => {
    const r = el('div', 'mrow ' + cls);
    r.append(el('span', 'tagsq ' + (cls === 'buy' ? 'b' : cls === 'sell' ? 's' : 'd'), tag));
    const k = el('div', 'k'); k.append(el('div', 't', title)); k.append(el('div', 's', sub)); r.append(k);
    const v = el('div', 'v', val); if (unit) { const u = el('u', '', unit); v.append(u); } r.append(v);
    mbox.append(r);
  };
  const unit = a.symbol.split('-')[0];
  if (h) {
    row(h.hedge_required_side === 'BUY' ? 'buy' : 'sell', h.hedge_required_side, 'Hedge required',
      'FROM OPTIONS DELTA', num(h.hedge_required_qty), unit);
    row(h.observed_side === 'BUY' ? 'buy' : 'sell', h.observed_side, 'Observed execution',
      h.same_direction ? 'SAME DIRECTION' : 'OPPOSITE DIRECTION', num(h.observed_qty), unit);
    row('dev', h.reconciles ? 'OK' : 'DEV', h.reconciles ? 'Reconciles' : 'Deviation',
      'VS STATED HEDGE', h.ratio == null ? '-' : num(h.ratio, 1), 'x');
  } else {
    if (m.opposite_qty != null) row(m.exec_side === 'BUY' ? 'sell' : 'buy', m.exec_side === 'BUY' ? 'SELL' : 'BUY',
      'Orders posted', 'THEN CANCELLED', num(m.opposite_qty), unit);
    if (m.exec_qty != null) row(m.exec_side === 'BUY' ? 'buy' : 'sell', m.exec_side || 'EXEC', 'Executed',
      'INTO THE MOVE', num(m.exec_qty), unit);
    row('dev', 'DEV', m.qty_ratio != null ? 'Size ratio' : 'Price move',
      m.qty_ratio != null ? 'POSTED VS EXECUTED' : 'OVER THE WINDOW',
      m.qty_ratio != null ? num(m.qty_ratio, 1) : num(c.market.move_bps, 1), m.qty_ratio != null ? 'x' : 'bps');
  }

  // chips
  const chips = $('#chips'); chips.textContent = '';
  const facts = findings(c);
  const sig = c._signals || (c._signals = signals(c));
  const flags = [
    [h && !h.reconciles, h && !h.same_direction ? 'DIRECTION MISMATCH' : 'HEDGE OVERSIZED'],
    [h && h.reconciles, 'HEDGE RECONCILES'],
    [sig.preclearGap, 'NO PRE-CLEARANCE'],
    [sig.algoAfterClient, 'ALGO CREATED AFTER CLIENT ORDER'],
    [sig.steering.length > 0, 'CHAT TRIES TO STEER THE REVIEW'],
    [m.cancel_fraction >= 0.9, '100% CANCELLED'],
    [m.favourable_move_bps > 1, 'PRICE MOVED IN FAVOUR'],
    [m.qty_ratio >= 5, `SIZE ${num(m.qty_ratio, 1)}X`],
    [((c.history.prior_by_disposition || {}).escalated || 0) > 0, 'PRIOR ESCALATION'],
  ];
  flags.filter(([on]) => on).forEach(([, t]) => chips.append(el('span', 'chip', t)));
  chips.append(el('span', 'chip off', `${c.pipeline.length} TOOL CALLS / ${num(c.elapsed_ms, 0)}MS`));

  drawChart(c);
  renderEvents(c);
  renderPipeline(c.pipeline, null);
  renderEvidence(c, facts);
  renderChats(c);
  renderFooter(c, facts);
}

/* ------------------------------------------------------------------ chart */

const PLOT = { x0: 70, x1: 960, aTop: 46, aBot: 176, zero: 332, bTop: 232, bBot: 418, axis: 448 };

function drawChart(c) {
  const node = $('#chart');
  node.textContent = '';
  const w = c.window, t0 = secs(w.start), t1 = secs(w.end), span = Math.max(1, t1 - t0);
  const X = (t) => PLOT.x0 + (secs(t) - t0) / span * (PLOT.x1 - PLOT.x0);
  $('#chart-window').textContent = `${c.alert.symbol} / ${w.start} - ${w.end} UTC / ${c.market.path.length} BBO points / ${c.orders.events_total} order events`;

  /* ---- lane A: mid price, in bps from the first point ---- */
  const path = c.market.path, m0 = path[0].mid;
  const bps = path.map((p) => ({ t: p.t, mid: p.mid, v: (p.mid / m0 - 1) * 1e4 }));
  const lo = Math.min(0, ...bps.map((p) => p.v)), hi = Math.max(0, ...bps.map((p) => p.v));
  const pad = Math.max(1, (hi - lo) * 0.18);
  const YA = (v) => PLOT.aBot - (v - (lo - pad)) / ((hi + pad) - (lo - pad)) * (PLOT.aBot - PLOT.aTop);

  node.append(svg('text', { x: 22, y: PLOT.aTop - 6, class: 'lane' }, 'MID PRICE / BPS'));
  [hi, 0, lo].forEach((v, i) => {
    if (i === 2 && Math.abs(lo) < 0.01) return;
    const y = YA(v);
    node.append(svg('line', { x1: PLOT.x0, y1: y, x2: PLOT.x1, y2: y, class: v === 0 ? 'axis' : 'grid' }));
    node.append(svg('text', { x: PLOT.x0 - 8, y: y + 3, class: 'tick', 'text-anchor': 'end' }, (v > 0 ? '+' : '') + v.toFixed(1)));
  });

  // the window the rule fired on
  const ax0 = X(w.alert_start), ax1 = X(w.alert_end);
  node.append(svg('rect', { x: ax0, y: PLOT.aTop, width: Math.max(2, ax1 - ax0), height: PLOT.axis - PLOT.aTop, class: 'band' }));
  node.append(svg('text', { x: ax0 + 4, y: PLOT.aTop - 6, class: 'tick' }, 'ALERT WINDOW'));

  node.append(svg('polyline', { class: 'price', points: bps.map((p) => `${X(p.t)},${YA(p.v)}`).join(' ') }));
  // annotate the biggest move inside the window the rule fired on, not the whole padded view
  const inWin = bps.filter((p) => secs(p.t) >= secs(w.alert_start) && secs(p.t) <= secs(w.alert_end));
  const ext = (inWin.length ? inWin : bps).reduce((a, b) => Math.abs(b.v) > Math.abs(a.v) ? b : a, (inWin[0] || bps[0]));
  node.append(svg('circle', { cx: X(ext.t), cy: YA(ext.v), r: 4.5, class: 'dot' }));
  /* Clear the label of the curve along its whole width, not just at the marked
     point - the deepest part of the dip is often a little to one side. */
  const lx = X(ext.t) > 760 ? X(ext.t) - 172 : X(ext.t) + 12;
  const under = bps.filter((p) => X(p.t) >= lx - 8 && X(p.t) <= lx + 178).map((p) => YA(p.v));
  under.push(YA(ext.v));
  const below = ext.v < (lo + hi) / 2;
  const ly = Math.max(PLOT.aTop + 9, Math.min(PLOT.aBot + 24,
    below ? Math.max(...under) + 17 : Math.min(...under) - 11));
  node.append(svg('text', { x: lx, y: ly, class: 'note' },
    `${ext.v > 0 ? '+' : ''}${ext.v.toFixed(1)} bps in window at ${ext.t.slice(0, 8)}`));

  /* ---- lane B: order flow, BUY above the axis, SELL below ---- */
  node.append(svg('text', { x: 22, y: PLOT.bTop - 8, class: 'lane' }, 'ORDER FLOW / ' + c.alert.symbol.split('-')[0]));
  const ev = (c.orders.events || []).filter((e) => e.qty);
  const maxQ = Math.max(0.0001, ...ev.map((e) => e.qty));
  const H = (q) => Math.max(2, q / maxQ * (PLOT.zero - PLOT.bTop));
  const bw = Math.max(2.5, Math.min(11, (PLOT.x1 - PLOT.x0) / Math.max(12, ev.length) * 0.8));

  node.append(svg('line', { x1: PLOT.x0, y1: PLOT.zero, x2: PLOT.x1, y2: PLOT.zero, class: 'zero' }));
  node.append(svg('text', { x: 22, y: PLOT.zero - 28, class: 'sidelab buy' }, 'BUY'));
  node.append(svg('polygon', { points: `38,${PLOT.zero - 34} 46,${PLOT.zero - 34} 42,${PLOT.zero - 43}`, class: 'fbuy' }));
  node.append(svg('text', { x: 22, y: PLOT.zero + 46, class: 'sidelab sell' }, 'SELL'));
  node.append(svg('polygon', { points: `38,${PLOT.zero + 34} 46,${PLOT.zero + 34} 42,${PLOT.zero + 43}`, class: 'fsell' }));

  // NEW and FILL are drawn to scale; a CANCEL is a stub at the axis, so removing
  // liquidity never looks like adding it.
  const GAP = 1.5;
  const marks = ev.map((e) => ({ e, up: e.side === 'BUY', x: X(e.t),
    h: e.event === 'CANCEL' ? 7 : H(e.qty) }));

  /* Events seconds apart land on the same pixel, so marks are laid out per side:
     each one keeps its place in time but is pushed right until it clears the
     previous mark. Bars touch, never overlap - an overlap would read as one order. */
  ['BUY', 'SELL'].forEach((side) => {
    const g = marks.filter((m) => m.e.side === side).sort((a, b) => a.x - b.x);
    if (!g.length) return;
    const room = PLOT.x1 - PLOT.x0;
    const w = Math.max(1.5, Math.min(bw, room / g.length - GAP));
    let right = -Infinity;
    g.forEach((m) => {
      m.w = w;
      m.left = Math.max(m.x - w / 2, right + GAP);
      right = m.left + w;
    });
    const over = right - PLOT.x1;                       // a dense cluster can run off the end
    if (over > 0) {
      const span = Math.max(1, right - g[0].left);
      g.forEach((m) => { m.left -= over * (m.left - g[0].left + m.w) / span; });
    }
  });

  marks.forEach((m) => {
    const y = m.e.event === 'CANCEL' ? (m.up ? PLOT.zero - m.h - 3 : PLOT.zero + 3)
                                     : (m.up ? PLOT.zero - m.h : PLOT.zero);
    node.append(svg('rect', { x: m.left, y, width: m.w, height: m.h, rx: Math.min(1.5, m.w / 3),
      class: 'bar ' + (m.up ? 'buy' : 'sell') + ' ' + m.e.event.toLowerCase() }));
  });

  // aggregate call-outs, placed at the centre of mass of each group
  const group = (pred) => { const g = ev.filter(pred); if (!g.length) return null;
    return { n: g.length, q: g.reduce((s, e) => s + e.qty, 0), x: g.reduce((s, e) => s + X(e.t), 0) / g.length }; };
  const posted = group((e) => e.event === 'NEW');
  const filled = group((e) => e.event === 'FILL');
  const cxl = group((e) => e.event === 'CANCEL');
  if (posted) node.append(svg('text', { x: Math.min(posted.x - 60, 820), y: PLOT.bTop - 24, class: 'callout' },
    `${posted.n} orders posted / ${num(posted.q)} total`));
  if (filled) node.append(svg('text', { x: PLOT.x0, y: PLOT.bBot + 6, class: 'callout' },
    `${filled.n} fills / ${num(filled.q)} executed`));
  if (cxl) node.append(svg('text', { x: PLOT.x0, y: PLOT.bBot + 20, class: 'callout dim' },
    `${cxl.n} cancels${c.derived.order_stats.cancelled_order_median_life_s ? ' / median life ' + num(c.derived.order_stats.cancelled_order_median_life_s, 1) + 's' : ''}`));

  /* ---- time axis ---- */
  node.append(svg('line', { x1: PLOT.x0, y1: PLOT.axis, x2: PLOT.x1, y2: PLOT.axis, class: 'grid' }));
  for (let i = 0; i <= 4; i++) {
    const t = t0 + span * i / 4;
    node.append(svg('text', { x: PLOT.x0 + (PLOT.x1 - PLOT.x0) * i / 4, y: PLOT.axis + 16, class: 'tick',
      'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle' }, hms(t)));
  }
  [w.alert_start, w.alert_end].forEach((t) => {
    const x = X(t);
    node.append(svg('line', { x1: x, y1: PLOT.aTop, x2: x, y2: PLOT.axis, class: 'guide' }));
  });
  node.append(svg('text', { x: ax1 - 4, y: PLOT.aTop - 6, class: 'tick w', 'text-anchor': 'end' },
    `${w.alert_start.slice(0, 8)} - ${w.alert_end.slice(0, 8)}`));

  /* ---- hover ---- */
  const hover = svg('g', { class: 'hover', visibility: 'hidden' });
  const vline = svg('line', { y1: PLOT.aTop, y2: PLOT.axis, class: 'cross' });
  hover.append(vline); node.append(hover);
  const overlay = svg('rect', { x: PLOT.x0, y: PLOT.aTop, width: PLOT.x1 - PLOT.x0, height: PLOT.axis - PLOT.aTop, fill: 'transparent' });
  node.append(overlay);
  const tip = $('#tip');
  overlay.addEventListener('mousemove', (e) => {
    const r = node.getBoundingClientRect(), sx = (e.clientX - r.left) / r.width * 1000;
    const t = t0 + (sx - PLOT.x0) / (PLOT.x1 - PLOT.x0) * span;
    vline.setAttribute('x1', sx); vline.setAttribute('x2', sx);
    hover.setAttribute('visibility', 'visible');
    const p = bps.reduce((a, b) => Math.abs(secs(b.t) - t) < Math.abs(secs(a.t) - t) ? b : a, bps[0]);
    const near = ev.filter((x) => Math.abs(secs(x.t) - t) <= span / 90).slice(0, 4);
    tip.innerHTML = `<b>${hms(t)}</b><br>mid ${num(p.mid, 2)} &nbsp; <b>${p.v > 0 ? '+' : ''}${p.v.toFixed(1)} bps</b>` +
      (near.length ? '<br>' + near.map((x) => `<span class="${x.side === 'BUY' ? 'g' : 'r'}">${x.event} ${x.side} ${num(x.qty)}</span> @ ${x.price ?? '-'}`).join('<br>') : '');
    tip.hidden = false;
    const left = Math.min(r.width - 210, Math.max(8, (e.clientX - r.left) + 16));
    tip.style.left = left + 'px'; tip.style.top = Math.max(8, e.clientY - r.top - 10) + 'px';
  });
  overlay.addEventListener('mouseleave', () => { hover.setAttribute('visibility', 'hidden'); tip.hidden = true; });
}

/* ------------------------------------------------------------------ events table */

function renderEvents(c) {
  const ev = c.orders.events || [];
  $('#events-count').textContent = `${c.orders.events_total} EVENTS / ${c.market.path.length} BBO SNAPSHOTS`;
  const t = $('#events-table'); t.textContent = '';
  const head = el('tr');
  ['TIME', 'ORDER', 'EVENT', 'SIDE', 'QTY', 'PRICE', 'ACCOUNT'].forEach((h, i) => {
    const th = el('th', i >= 4 ? 'r' : '', h); head.append(th);
  });
  const thead = el('thead'); thead.append(head); t.append(thead);
  const body = el('tbody');
  ev.slice(0, 120).forEach((e) => {
    const r = el('tr');
    r.append(el('td', 'w', e.t.slice(0, 8)));
    r.append(el('td', '', e.order_id));
    r.append(el('td', '', e.event));
    r.append(el('td', e.side === 'BUY' ? 'b' : 's', e.side));
    r.append(el('td', 'r w', num(e.qty)));
    r.append(el('td', 'r', num(e.price, 2)));
    r.append(el('td', '', e.account + (e.contra ? ' / ' + e.contra : '')));
    body.append(r);
  });
  t.append(body);
}

/* ------------------------------------------------------------------ pipeline */

function renderPipeline(steps, job) {
  const box = $('#steps');
  box.textContent = '';
  const track = el('div', 'track'); const fill = el('i'); track.append(fill); box.append(track);
  const live = !!job;
  const items = live ? job.steps : steps;
  const done = items.filter((s) => s.state !== 'running').length;
  items.forEach((s, i) => {
    const running = live && s.state === 'running';
    const step = el('div', 'step' + (running ? ' cur' : ''));
    step.append(el('div', 'node ' + (running ? 'run' : 'done')));
    step.append(el('div', 'nm', s.tool));
    step.append(el('div', 'rt', live ? (running ? 'running' : 'done') : `${num(s.ms, 0)}ms / ${s.returned}`));
    box.append(step);
  });
  requestAnimationFrame(() => { fill.style.width = (items.length < 2 ? 100 : done / items.length * 100) + '%'; });

  if (live) {
    $('#p-state').textContent = job.state === 'running' ? `AGENT RUNNING / ${job.model}` :
      job.state === 'error' ? 'AGENT ERROR' : `AGENT ${(job.result && job.result.decision || 'done').toUpperCase()}`;
    $('#p-elapsed').textContent = (job.elapsed_s != null ? job.elapsed_s + 's' : '-');
    $('#p-session').textContent = `JOB ${job.job_id} / ${items.length} TOOL CALLS`;
    $('#p-note').textContent = job.error || 'LIVE TOOL-CALLING LOOP';
  } else {
    $('#p-state').textContent = `${steps.length} OF ${steps.length} / EVIDENCE READY`;
    $('#p-elapsed').textContent = `${num(STATE.case.elapsed_ms, 0)}ms`;
    $('#p-session').textContent = `${STATE.boot.day} / ${steps.filter((s) => s.ok).length} OK / ${steps.filter((s) => !s.ok).length} ERRORS`;
    $('#p-note').textContent = 'EVIDENCE GATHERED BY THE DASHBOARD';
  }
}

$('#run-agent').onclick = async () => {
  if (!STATE.alertId) return;
  $('#run-agent').disabled = true;
  try {
    const { job_id } = await api('/api/triage', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alert_id: STATE.alertId }) });
    clearInterval(STATE.timer);
    STATE.timer = setInterval(async () => {
      const job = await api('/api/triage?job=' + job_id);
      STATE.job = job;
      renderPipeline(null, job);
      if (job.state !== 'running') {
        clearInterval(STATE.timer);
        $('#run-agent').disabled = false;
        toast(job.state === 'error' ? job.error : `Agent ${job.result.decision} ${job.alert_id}`, job.state === 'error');
        if (job.state === 'done') { STATE.boot = await api('/api/bootstrap'); renderQueue(); selectAlert(STATE.alertId); }
      }
    }, 900);
  } catch (e) { toast(e.message, true); $('#run-agent').disabled = false; }
};

/* ------------------------------------------------------------------ evidence */

function accordion(title, flag, bodyNode, open) {
  const d = el('details', 'acc'); if (open) d.open = true;
  const s = el('summary');
  s.append(el('span', 'cv'));
  const tt = el('div', 'tt'); tt.append(el('b', '', title));
  if (flag) tt.append(el('span', 'fl ' + flag.toLowerCase(), flag));
  s.append(tt); d.append(s);
  const bd = el('div', 'bd'); bd.append(bodyNode); d.append(bd);
  return d;
}

function kv(pairs) {
  const box = el('div', 'kv');
  pairs.forEach(([k, v, cls]) => { box.append(el('s', '', k)); box.append(el('b', cls || '', String(v))); });
  return box;
}

function renderEvidence(c, facts) {
  const box = $('#evidence'); box.textContent = '';
  const items = [];
  const h = c.derived.hedge, st = c.derived.order_stats, m = c.alert.metrics || {};
  const sig = c._signals || (c._signals = signals(c));

  if (h) {
    const t = (c.extras.options.trades || [])[0];
    items.push(['Hedge exposure', h.reconciles ? 'SUPPORTS' : 'CONTRADICTS', kv([
      ['options', `${h.option_trades} trade(s)${t ? ', ' + t.contract : ''}`],
      ['book delta', `${num(h.option_delta)} ${c.alert.symbol.split('-')[0]}`],
      ['hedge needs', `${h.hedge_required_side} ~${num(h.hedge_required_qty)}`, h.hedge_required_side === 'BUY' ? 'g' : 'r'],
      ['observed', `${h.observed_side} ${num(h.observed_qty)}`, h.observed_side === 'BUY' ? 'g' : 'r'],
      ['reconciles', h.reconciles ? 'YES' : `NO / ${h.same_direction ? num(h.ratio, 1) + 'x size' : 'opposite sign'}`],
    ]), !h.reconciles]);
  }
  const side = st[m.exec_side === 'BUY' ? 'SELL' : 'BUY'] || st.BUY || st.SELL || {};
  items.push(['Order book', m.cancel_fraction >= 0.9 ? 'SUPPORTS' : 'NEUTRAL', kv([
    ['posted', `${side.new_orders || 0} orders / ${num(side.new_qty)}`],
    ['median size', `${num(side.median_new_qty)}${side.size_vs_typical ? ` (${num(side.size_vs_typical, 1)}x norm)` : ''}`],
    ['cancels', `${side.cancels || 0}${side.cancel_fraction != null ? ` / ${Math.round(side.cancel_fraction * 100)}%` : ''}`],
    ['median life', st.cancelled_order_median_life_s != null ? num(st.cancelled_order_median_life_s, 1) + 's' : '-'],
  ])]);
  items.push(['Price impact', m.favourable_move_bps > 1 ? 'SUPPORTS' : 'NEUTRAL', kv([
    ['window move', `${num(c.market.move_bps, 1)} bps`],
    ['max up / down', `${num(c.market.max_up_bps, 1)} / ${num(c.market.max_down_bps, 1)} bps`],
    ['favourable', m.favourable_move_bps != null ? `${num(m.favourable_move_bps, 1)} bps` : '-',
      m.favourable_move_bps > 1 ? 'r' : ''],
    ['avg spread', num(c.market.avg_spread, 3)],
  ])]);
  const hist = c.history;
  items.push(['Trader history', ((hist.prior_by_disposition || {}).escalated || 0) > 0 ? 'SUPPORTS' : 'NEUTRAL', kv([
    ['prior alerts', hist.prior_alerts_total],
    ...Object.entries(hist.prior_by_disposition || {}).map(([k, v]) => [k, v]),
    ['today', Object.entries(hist.today_alerts_by_rule || {}).map(([k, v]) => `${k} ${v}`).join(', ') || '-'],
  ])]);
  items.push(['Trader profile', null, kv([
    ['desk', c.profile.desk], ['role', c.profile.role],
    ...(c.profile.accounts_controlled || []).map((a) => [a.account_id, `${a.type}${a.notes ? ' / ' + a.notes : ''}`]),
  ])]);

  const e = c.extras;
  if (e.client_orders) {
    const list = e.client_orders.client_orders || [];
    items.push([`Client orders (${list.length})`, null, kv(list.slice(0, 4).flatMap((o) => [
      [o.client_order_id, `${o.client} ${o.side} ${num(o.qty)} @ ${o.received}`],
      ['instruction', o.instruction || '-'],
    ]))]);
  }
  if (e.algos) {
    const list = e.algos.algo_orders || [];
    items.push([`Parent algos (${list.length})`, sig.algoAfterClient ? 'CONTRADICTS' : list.length ? 'SUPPORTS' : null,
      kv(list.slice(0, 3).flatMap((x) => [
        [x.parent_id, `${x.strategy} ${x.side} ${num(x.total_qty)}`],
        ['created', x.created, sig.algo === x && sig.algoAfterClient ? 'r' : ''],
        ['approval', x.approved_by || 'none recorded', x.approved_by ? '' : 'r'],
      ])), sig.algoAfterClient]);
  }
  if (e.preclearance) {
    const list = e.preclearance.requests || [];
    items.push([`Pre-clearance (${list.length})`, sig.preclearGap ? 'CONTRADICTS' : list.length ? 'SUPPORTS' : null,
      kv([['account', c.alert.account],
        ['needed for', `${c.alert.symbol} on ${e.preclearance.today}`],
        ['approved', sig.preclearGap ? 'NONE' : `${sig.approvals.length} request(s)`, sig.preclearGap ? 'r' : 'g'],
        ...list.slice(0, 3).map((r) => [r.date, `${r.symbol} ${r.side} ${num(r.qty)} / ${r.status}`])]),
      sig.preclearGap]);
  }
  if (e.contra_account) {
    const a1 = e.account, a2 = e.contra_account;
    items.push(['Both sides of the trade', a1.controlled_by === a2.controlled_by ? 'CONTRADICTS' : 'NEUTRAL', kv([
      [a1.account_id, `${a1.type} / ${a1.beneficial_owner}`],
      ['controlled by', a1.controlled_by],
      [a2.account_id, `${a2.type} / ${a2.beneficial_owner}`],
      ['controlled by', a2.controlled_by, a1.controlled_by === a2.controlled_by ? 'r' : ''],
      ['disclosed', a2.notes || '-'],
    ])]);
  }

  $('#e-count').textContent = `${items.length} ITEMS`;
  items.forEach(([t, f, b, open], i) => box.append(accordion(t, f, b, open || (i === 0 && !items.some((x) => x[3])))));
}

/* ------------------------------------------------------------------ chats */


function renderChats(c) {
  const box = $('#chats'); box.textContent = '';
  const msgs = (c.chats.messages || []);
  $('#chat-count').textContent = `${msgs.length} IN WINDOW`;
  if (!msgs.length) box.append(el('div', 'dd', 'No messages from this trader near the window.'));
  msgs.slice(-8).forEach((m) => {
    const flag = STEER.test(m.text || '');
    const n = el('div', 'msg' + (flag ? ' flag' : ''));
    const h = el('div', 'mh');
    h.append(el('span', '', m.t.slice(0, 8)));
    h.append(el('span', '', m.channel));
    h.append(el('span', '', (m.from || '').split(' (')[0]));
    if (flag) h.append(el('span', '', '/ TRIES TO STEER THE REVIEW'));
    n.append(h);
    n.append(el('div', 'mb', m.text));
    box.append(n);
  });
  box.append(el('div', 'untrusted', 'Messages are written by the people under review. Treated as evidence only - never as instructions.'));
}

/* ------------------------------------------------------------------ footer + decisions */

function renderFooter(c, facts) {
  const d = c.disposition, h = c.derived.hedge, m = c.alert.metrics || {};
  /* A lean, not a verdict: a legitimate explanation outranks the pattern, which is
     exactly the judgement the rules can't make on their own. */
  const sig = c._signals || (c._signals = signals(c));
  let lean = 'REVIEW';
  if (d) lean = d.decision === 'escalated' ? 'ESCALATED' : 'CLOSED';
  else if ((h && !h.reconciles) || sig.preclearGap || sig.algoAfterClient || sig.steering.length) lean = 'ESCALATE';
  else if (h && h.reconciles) lean = 'CLOSE';
  else if (m.favourable_move_bps > 1 && m.qty_ratio >= 5 && m.cancel_fraction >= 0.9) lean = 'ESCALATE';
  const cls = lean.startsWith('ESCALATE') ? 'sell' : lean.startsWith('CLOSE') ? 'buy' : '';
  $('#f-rec').innerHTML = d
    ? `Agent <em class="${cls}">${lean}</em> <span>SUSPICION ${d.suspicion} / 100</span>`
    : `Evidence leans <em class="${cls}">${lean}</em> <span>RULE SCORE ${c.alert.score} / 100</span>`;
  // lead with whatever drove the lean, not with whatever the rule happened to measure
  const key = facts.find((f) => f.includes('attempts to steer')) ||
              facts.find((f) => f.startsWith('no pre-clearance')) ||
              facts.find((f) => f.includes('created AFTER')) ||
              facts.find((f) => f.startsWith('options delta'));
  const why = key ? [key, ...facts.filter((f) => f !== key)] : facts;
  $('#f-why').textContent = (d && (d.rationale || d.summary)) || why.filter(Boolean).slice(0, 2).join('; ') || c.alert.summary;
}

const DRAWER = { act: null };
$('.acts').onclick = (e) => {
  const b = e.target.closest('button'); if (!b || !STATE.case) return;
  openDrawer(b.dataset.act);
};
function openDrawer(act) {
  const c = STATE.case, facts = findings(c);
  DRAWER.act = act;
  $('#drawer').hidden = false;
  const titles = { close: 'Close alert', escalate: 'Escalate case', reinvestigate: 'Request more investigation' };
  $('#dw-title').textContent = titles[act];
  $('#dw-tl').textContent = act === 'close' ? 'RATIONALE - NAME THE EVIDENCE'
    : act === 'escalate' ? 'CASE SUMMARY - SENT TO COMPLIANCE' : 'WHAT SHOULD THE AGENT LOOK AT';
  const h = c.derived.hedge;
  $('#dw-text').value = act === 'close'
    ? (h && h.reconciles ? `Hedge reconciles: options delta ${num(h.option_delta)} calls for a ${h.hedge_required_side} of ~${num(h.hedge_required_qty)} and the desk did ${h.observed_side} ${num(h.observed_qty)}. Normal market making.` : '')
    : act === 'escalate' ? `${c.alert.summary}. ` + facts.slice(0, 3).join('; ') + '.' : '';
  const sug = act === 'escalate' ? 85 : act === 'close' ? 10 : 50;
  $('#dw-susp').value = sug; $('#dw-sv').textContent = sug;
  $('#dw-note').textContent = act === 'reinvestigate'
    ? 'Recorded for the agent; the alert stays open.'
    : 'Written to the audit log and dispositions, and posted to the Telegram compliance channel.';
}
$('#dw-close').onclick = () => { $('#drawer').hidden = true; };
$('#drawer').onclick = (e) => { if (e.target.id === 'drawer') $('#drawer').hidden = true; };
$('#dw-susp').oninput = (e) => { $('#dw-sv').textContent = e.target.value; };

$('#dw-submit').onclick = async () => {
  const c = STATE.case, act = DRAWER.act, text = $('#dw-text').value.trim();
  const suspicion = +$('#dw-susp').value;
  const body = { alert_id: c.alert.alert_id, action: act, suspicion };
  if (act === 'close') body.rationale = text;
  if (act === 'reinvestigate') body.rationale = text || 'More investigation requested.';
  if (act === 'escalate') {
    body.title = `${c.alert.rule.replace('_', ' ').toLowerCase()} in ${c.alert.symbol} - ${c.alert.trader_name}`;
    body.summary = text;
    body.evidence = findings(c);
  }
  try {
    const out = await api('/api/decision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    $('#drawer').hidden = true;
    toast(`${c.alert.alert_id} ${out.status}`);
    STATE.boot = await api('/api/bootstrap');
    renderQueue();
    selectAlert(c.alert.alert_id);
  } catch (e) { toast(e.message, true); }
};

function toast(msg, err) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast' + (err ? ' err' : ''); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 3500);
}

boot().catch((e) => toast(e.message, true));
