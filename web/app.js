/* Junior AI Investigator — vanilla SPA. No build step. All data comes from /api/*; nothing is mocked. */
const $ = (s, r = document) => r.querySelector(s);
const view = $('#view');
let META = null;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = n => '$' + Math.round(n).toLocaleString();
const chip = l => `<span class="chip ${l}">${l}</span>`;
const stChip = s => `<span class="chip st ${s}">${s.replace('_', ' ')}</span>`;
const ridChips = (ids, rules) => ids.map(i => { const r = (rules || []).find(x => x.rule_id === i); return `<span class="rid ${r ? r.level : ''}" title="${esc(r ? r.name + ' — ' + r.level : i)}">${i}</span>`; }).join('');

function who() { const [name, role] = $('#persona').value.split('|'); return { name, role }; }
async function api(path, method = 'GET', body) {
  const w = who();
  const r = await fetch('/api' + path, { method, headers: { 'Content-Type': 'application/json', 'X-Actor': w.name, 'X-Role': w.role }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  return r.json();
}
function toast(msg, ms = 4000) { const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add('hidden'), ms); }
async function act(fn) { try { return await fn(); } catch (e) { toast('⚠ ' + e.message, 7000); } }
function modal(html) { $('#modalCard').innerHTML = html; $('#modal').classList.remove('hidden'); }
function closeModal() { $('#modal').classList.add('hidden'); }
$('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(); });

/* ------------------------------------------------------------------ shell */
let POLL = null;
async function loadMeta() {
  META = await api('/meta');
  const p = $('#llmPill'), b = META.batch;
  if (!META.llm.configured) { p.className = 'pill bad'; p.textContent = 'LLM not configured — rules only'; }
  else if (b.running) { p.className = 'pill'; p.innerHTML = `<span class="spin"></span> AI triage ${b.done}/${b.total}`; clearTimeout(POLL); POLL = setTimeout(async () => { await loadMeta(); if (!META.batch.running && !location.hash.startsWith('#/case')) route(); }, 6000); }
  else { p.className = 'pill ok'; p.textContent = 'LLM: ' + META.llm.models[0].split('/')[1].replace(':free', '') + (b.failed ? ` · ${b.failed} unavailable` : ''); }
  const esc_ = await api('/escalations?status=PENDING');
  const bd = $('#escBadge'); bd.textContent = esc_.length; bd.classList.toggle('hidden', !esc_.length);
}
$('#persona').addEventListener('change', () => { route(); });

let ROUTE = '';
const gone = () => ROUTE !== (location.hash || '#/briefing');
function route() {
  const h = location.hash || '#/briefing';
  const [, r, arg] = h.split('/'); ROUTE = h;
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.r === (r === 'case' ? 'queue' : r)));
  ({ briefing: viewBriefing, rules: viewRules, blocklist: viewBlocklist, queue: viewQueue, case: () => viewCase(arg), supervisor: viewSupervisor, audit: viewAudit }[r] || viewBriefing)();
}
window.addEventListener('hashchange', route);

/* ------------------------------------------------------------------ briefing */
async function viewBriefing() {
  view.innerHTML = '<div class="sub"><span class="spin"></span> Loading briefing…</div>';
  const { stats: s, headlines } = await api('/briefing'); if (gone()) return;
  const rc = s.risk_counts;
  const maxh = Math.max(...s.score_histogram.map(b => b.count), 1);
  view.innerHTML = `
  <h1>Good morning — here is your queue</h1>
  <div class="sub">${s.open_cases} open of ${s.total_cases} cases · AI reviewed ${s.ai.assessed} · ${s.ai.unavailable} AI-unavailable · ${s.ai.not_run} not yet run · ${s.ai.stale} stale (notes changed) · ${s.pending_escalations} escalations pending</div>
  <div class="grid g4">
    ${['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map(l => `<div class="card kpi" onclick="gotoQueue({levels:['${l}']})"><div class="l">${chip(l)}</div><div class="n">${rc[l].count}</div><div class="x">${money(rc[l].exposure_usd)} claimed</div></div>`).join('')}
  </div>
  <div class="grid g32" style="margin-top:14px">
    <div class="stack">
      <div class="card"><h2>Headlines <small class="mut">(computed from the data)</small></h2><ul class="tight">${headlines.map(h => `<li>${esc(h)}</li>`).join('')}</ul>
        <div id="narr" class="bar"><button id="narrBtn">✨ Generate AI briefing</button><span class="small mut">LLM narrative restricted to these stats; figures are verified.</span></div></div>
      <div class="card"><h2>Top cases to open first</h2>
        <table><thead><tr><th>Case</th><th>Care type</th><th class="num">Amount</th><th class="num">Rule</th><th class="num">AI</th><th>Rules fired</th><th>Recommended</th></tr></thead><tbody>
        ${s.top_cases.map(c => `<tr class="row" onclick="location.hash='#/case/${c.case_id}'"><td><b>${c.case_id}</b><div class="small mut">${c.state}</div></td><td>${c.care_type}</td><td class="num">${money(c.amount)}</td>
          <td class="num">${chip(c.rule_level)} ${c.rule_score}</td><td class="num">${c.ai_level ? chip(c.ai_level) + ' ' + c.ai_score : '<span class="mut">—</span>'}</td>
          <td>${c.rules.map(r => `<span class="rid">${r}</span>`).join('')}</td><td class="small">${c.action ? c.action.replace(/_/g, ' ') : '—'}</td></tr>`).join('')}</tbody></table></div>
    </div>
    <div class="stack">
      <div class="card"><h2>Score distribution (open cases)</h2>
        <div class="hist" style="margin-top:20px">${s.score_histogram.map(b => `<div style="height:${Math.max(3, 100 * b.count / maxh)}%"><span>${b.count || ''}</span></div>`).join('')}</div>
        <div class="histl">${s.score_histogram.map(b => `<div>${b.bucket.split('-')[0]}</div>`).join('')}</div>
        <div class="bar"><button onclick="quickClear()">Review low-risk (score &lt; 10): ${s.clearable_low_risk}</button></div></div>
      <div class="card"><h2>Rule clusters <small class="mut">cases sharing the same rule pattern</small></h2>
        ${s.clusters.length ? s.clusters.map(c => `<div style="margin-bottom:10px"><b>${c.size} cases</b> · avg score ${c.avg_score} · ${money(c.total_amount)}<div>${c.signature_rules.map(r => `<span class="rid">${r}</span>`).join('')}</div>
          <div class="small mut">${c.case_ids.map(i => `<a href="#/case/${i}">${i}</a>`).join(', ')}</div>
          <div class="small mut">${Object.entries(c.care_types).map(([k, v]) => k + ' ×' + v).join(', ')}</div></div>`).join('') : '<div class="mut">No clusters.</div>'}</div>
      <div class="card"><h2>Abnormal rule firing</h2><div class="small mut" style="margin-bottom:6px">Rule fire-rate in a segment vs the rest of the queue (exact binomial test). Small samples — treat as leads.</div>
        ${s.abnormal_firing.length ? `<table><thead><tr><th>Segment</th><th>Rule</th><th class="num">Rate</th><th class="num">p</th></tr></thead><tbody>${s.abnormal_firing.map(f => `<tr><td>${esc(f.segment)}<div class="small mut">${f.dimension}</div></td><td><span class="rid">${f.rule_id}</span></td><td class="num">${f.fired}/${f.of} vs ${Math.round(f.rest_rate * 100)}%</td><td class="num">${f.p_value}</td></tr>`).join('')}</tbody></table>` : '<div class="mut">Nothing unusual.</div>'}</div>
      ${'<div id="lanes"></div>'}
      ${s.ai.disagree_with_rules.length ? `<div class="card"><h2>AI disagrees with rules</h2>${s.ai.disagree_with_rules.map(d => `<a href="#/case/${d.case_id}">${d.case_id}</a> ${chip(d.rule_level)} → ${chip(d.ai_level)}<br>`).join('')}</div>` : ''}
    </div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Ask about the queue</h2><div id="qlog" class="chatlog"></div>
    <div class="bar"><input id="qin" style="flex:1" placeholder="e.g. Which cases fire both R03 and R05? What is unusual about Home Health Aide?"><button class="primary" id="qsend">Ask</button></div></div>`;
  api('/runs/summary').then(r => { const el = $('#lanes'); if (!el) return; const rows = Object.entries(r.crew).filter(([k]) => !k.startsWith('_')); if (!rows.length) return; const t = r.crew._total, a = r.agreement;
    el.innerHTML = `<div class="card"><h2>Crew cost & routing <small class="mut">mode: ${esc(r.mode)}</small></h2><table><thead><tr><th>Lane</th><th class="num">Cases</th><th class="num">Calls</th><th class="num">Tokens</th><th class="num">Ref $</th></tr></thead><tbody>${rows.map(([k, v]) => `<tr><td>${k}</td><td class="num">${v.cases}</td><td class="num">${v.calls_per_case}</td><td class="num">${v.tokens_per_case.toLocaleString()}</td><td class="num">${v.est_cost_per_case}</td></tr>`).join('')}</tbody></table><div class="small mut" style="margin-top:6px">Per case. Queue total ${t.calls} calls · ${t.tokens.toLocaleString()} tokens · ref $${t.est_cost}. ${a.contested} contested → panel; ${a.reused_specialists} specialist findings reused.</div></div>`; });
  $('#narrBtn').onclick = () => act(async () => {
    $('#narr').innerHTML = '<span class="spin"></span> Writing…';
    const n = await api('/briefing/narrative?force=true', 'POST');
    $('#narr').outerHTML = n.status === 'OK' ? `<div class="banner info" style="white-space:pre-wrap">${esc(n.text)}${n.unverified_figures.length ? `<div class="small">⚠ unverified figures: ${n.unverified_figures.join(', ')}</div>` : ''}<div class="small mut">${n.model}</div></div>` : `<div class="banner bad">AI briefing unavailable: ${esc(n.error)}</div>`;
  });
  const qlog = $('#qlog'); (await api('/queue/chat')).forEach(m => addMsg(qlog, m.role, m.content));
  const send = () => act(async () => { const q = $('#qin').value.trim(); if (!q) return; $('#qin').value = ''; addMsg(qlog, 'user', q); const pend = addMsg(qlog, 'assistant', '…'); try { const r = await api('/queue/chat', 'POST', { question: q }); pend.firstChild.textContent = r.answer; pend.querySelector('small').textContent = modelTag(r); } catch (e) { pend.remove(); throw e; } });
  $('#qsend').onclick = send; $('#qin').onkeydown = e => { if (e.key === 'Enter') send(); };
}
function modelTag(r) { return (r.model || '').split('/').pop() + (r.tools_used && r.tools_used.length ? ' · tools: ' + [...new Set(r.tools_used.map(t => t.tool))].join(', ') : ''); }
function addMsg(log, role, text, tag = '') { const d = document.createElement('div'); d.className = 'msg ' + role; d.innerHTML = `<span></span><small>${esc(tag)}</small>`; d.firstChild.textContent = text; log.appendChild(d); log.scrollTop = 1e9; return d; }
function quickClear() { QF = defaultFilter(); QF.max_score = 10; location.hash = '#/queue'; setTimeout(() => { if (location.hash === '#/queue') viewQueue(); }, 0); }
function gotoQueue(f) { QF = { ...defaultFilter(), ...f }; location.hash = '#/queue'; if (location.hash === '#/queue') viewQueue(); }

/* ------------------------------------------------------------------ queue */
const defaultFilter = () => ({ min_score: null, max_score: null, levels: [], care_types: [], states: [], statuses: [], rule_ids: [], rule_mode: 'any', ai_disagrees: false, q: '', sort: 'score', order: 'desc' });
let QF = defaultFilter(), SEL = new Set();
const clean = f => { const o = { ...f }; for (const k of Object.keys(o)) if (o[k] === '' || (Array.isArray(o[k]) && !o[k].length)) o[k] = null; return o; };

async function viewQueue() {
  const rows = await api('/cases/search', 'POST', clean(QF)); if (gone()) return;
  const opts = (arr, sel) => arr.map(x => `<option ${sel.includes(x) ? 'selected' : ''}>${x}</option>`).join('');
  view.innerHTML = `
  <h1>Case queue</h1><div class="sub">${rows.length} cases match · click a row to open · tick rows to clear or escalate them</div>
  <div class="card"><div class="filters">
    <label>Score ≥<input type="number" id="f_min" value="${QF.min_score ?? ''}"></label>
    <label>Score &lt;<input type="number" id="f_max" value="${QF.max_score ?? ''}"></label>
    <label>Level<select id="f_lvl" multiple size="4">${opts(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'], QF.levels)}</select></label>
    <label>Care type<select id="f_ct" multiple size="4">${opts(META.care_types, QF.care_types)}</select></label>
    <label>Status<select id="f_st" multiple size="4">${opts(META.statuses, QF.statuses)}</select></label>
    <label>State<select id="f_state" multiple size="4">${opts(META.states, QF.states)}</select></label>
    <label>Search<input id="f_q" placeholder="case / claim #" value="${esc(QF.q)}" style="width:130px"></label>
    <label>Rules fired (${QF.rule_mode})<div class="bar" style="margin:0;max-width:340px;gap:3px">${META.rules.map(r => `<span class="rid toggle ${QF.rule_ids.includes(r.rule_id) ? 'on' : ''}" data-rule="${r.rule_id}" title="${esc(r.name)}">${r.rule_id}</span>`).join('')}
      <span class="rid toggle" id="modeBtn">${QF.rule_mode === 'any' ? 'ANY' : 'ALL'}</span></div></label>
    <label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="f_dis" ${QF.ai_disagrees ? 'checked' : ''}> AI ≠ rules</label>
    <button id="applyF" class="primary">Apply</button><button id="resetF">Reset</button>
  </div>
  <div class="bar"><span class="small mut">Presets:</span>
    <button class="sm" onclick="preset({max_score:10})">score &lt; 10</button><button class="sm" onclick="preset({levels:['CRITICAL']})">Critical</button>
    <button class="sm" onclick="preset({rule_ids:['R01','R02','R03'],rule_mode:'all'})">R01+R02+R03</button>
    <button class="sm" onclick="preset({rule_ids:['R14']})">Silent drift (R14)</button><button class="sm" onclick="preset({ai_disagrees:true})">AI disagrees</button>
    <span style="flex:1"></span>
    <button id="bulkBtn" class="danger">Bulk clear matching (${rows.length})…</button>
    <button id="clrSel" class="ok" ${SEL.size ? '' : 'disabled'}>Clear selected (${SEL.size})</button>
    <button id="escSel" ${SEL.size ? '' : 'disabled'}>Escalate selected (${SEL.size})</button></div></div>
  <div class="card" style="margin-top:12px;padding:0;overflow:auto"><table><thead><tr>
    <th><input type="checkbox" id="selAll"></th>${[['case_id', 'Case'], ['', 'Care type'], ['', 'State'], ['amount', 'Amount'], ['score', 'Rule score'], ['', 'AI score'], ['', 'Rules fired'], ['', 'AI recommends'], ['', 'Status']].map(([k, l]) => `<th class="${k ? 's' : ''}" data-sort="${k}">${l}${QF.sort === k ? (QF.order === 'asc' ? ' ▲' : ' ▼') : ''}</th>`).join('')}</tr></thead><tbody>
    ${rows.map(c => `<tr class="row" data-id="${c.case_id}"><td onclick="event.stopPropagation()"><input type="checkbox" class="sel" data-id="${c.case_id}" ${SEL.has(c.case_id) ? 'checked' : ''}></td>
      <td><b>${c.case_id}</b><div class="small mut">${c.claim_number}${c.data.linked_case_ids.length ? ' 🔗' : ''}${c.note_count ? ' · 📝' + c.note_count : ''}</div></td><td>${c.care_type}</td><td>${c.state}</td><td class="num">${money(c.amount)}</td>
      <td class="num">${chip(c.level)} <b>${c.score}</b></td>
      <td class="num">${c.ai && c.ai.status === 'OK' ? chip(c.ai.ai_level) + ' <b>' + c.ai.ai_score + '</b>' + (c.ai.stale ? ' <span title="notes changed since AI review">⟳</span>' : '') + (c.ai.contested ? ' <span class="chip HIGH" title="contested: judge panel convened">⚖</span>' : '') : c.ai ? '<span class="mut" title="LLM unavailable at last attempt">unavailable</span>' : '<span class="mut">pending</span>'}</td>
      <td>${ridChips(c.fired_ids, c.rules)}</td><td class="small">${c.ai && c.ai.action ? c.ai.action.replace(/_/g, ' ') : ''}</td><td>${stChip(c.status)}</td></tr>`).join('') || '<tr><td colspan="10" class="mut" style="padding:20px">No cases match.</td></tr>'}</tbody></table></div>`;
  const read = () => {
    const ms = id => [...$(id).selectedOptions].map(o => o.value);
    QF.min_score = $('#f_min').value === '' ? null : +$('#f_min').value; QF.max_score = $('#f_max').value === '' ? null : +$('#f_max').value;
    QF.levels = ms('#f_lvl'); QF.care_types = ms('#f_ct'); QF.statuses = ms('#f_st'); QF.states = ms('#f_state'); QF.q = $('#f_q').value; QF.ai_disagrees = $('#f_dis').checked;
  };
  $('#applyF').onclick = () => { read(); viewQueue(); }; $('#resetF').onclick = () => { QF = defaultFilter(); viewQueue(); };
  document.querySelectorAll('[data-rule]').forEach(e => e.onclick = () => { read(); const r = e.dataset.rule; QF.rule_ids = QF.rule_ids.includes(r) ? QF.rule_ids.filter(x => x !== r) : [...QF.rule_ids, r]; viewQueue(); });
  $('#modeBtn').onclick = () => { read(); QF.rule_mode = QF.rule_mode === 'any' ? 'all' : 'any'; viewQueue(); };
  document.querySelectorAll('th.s').forEach(t => t.onclick = () => { read(); const k = t.dataset.sort; QF.order = QF.sort === k && QF.order === 'desc' ? 'asc' : 'desc'; QF.sort = k; viewQueue(); });
  document.querySelectorAll('tr.row').forEach(t => t.onclick = () => location.hash = '#/case/' + t.dataset.id);
  document.querySelectorAll('.sel').forEach(c => c.onchange = () => { c.checked ? SEL.add(c.dataset.id) : SEL.delete(c.dataset.id); $('#escSel').disabled = $('#clrSel').disabled = !SEL.size; $('#escSel').textContent = `Escalate selected (${SEL.size})`; $('#clrSel').textContent = `Clear selected (${SEL.size})`; });
  $('#selAll').onchange = e => { rows.forEach(c => e.target.checked ? SEL.add(c.case_id) : SEL.delete(c.case_id)); viewQueue(); };
  $('#bulkBtn').onclick = () => read() || bulkModal(false);
  $('#escSel').onclick = () => bulkEscalateModal(); $('#clrSel').onclick = () => bulkModal(false, [...SEL]);
}
function preset(p) { QF = { ...defaultFilter(), ...p }; viewQueue(); }

async function bulkModal(allowNoAI, ids) {
  modal('<span class="spin"></span> Evaluating guardrails…');
  const r = await act(() => api('/bulk/preview', 'POST', ids ? { case_ids: ids, allow_without_ai: allowNoAI } : { filter: clean(QF), allow_without_ai: allowNoAI })); if (!r) return closeModal();
  const noAI = r.blocked.filter(b => b.reasons.some(x => x.startsWith('no AI'))).length;
  modal(`<h2>${ids ? `Clear ${r.matched} selected case(s)` : `Bulk clear — ${r.matched} cases match your filter`}</h2>
    <div class="banner info"><b>${r.clearable.length}</b> can be cleared · <b>${r.blocked.length}</b> blocked by guardrails. A case is never bulk-cleared if a HIGH/CRITICAL rule fired, the AI rates it MEDIUM+ or recommends follow-up, or notes changed after the AI review.</div>
    ${noAI ? `<label class="small"><input type="checkbox" id="allowNo" ${allowNoAI ? 'checked' : ''}> include ${noAI} case(s) with no AI second opinion (recorded in the audit log)</label>` : ''}
    <h3>Will be cleared (${r.clearable.length})</h3>
    <div style="max-height:180px;overflow:auto"><table><tbody>${r.clearable.map(c => `<tr><td>${c.case_id}</td><td>${c.care_type}</td><td class="num">${money(c.amount)}</td><td>${chip(c.level)} ${c.score}</td><td>${c.ai_level ? 'AI: ' + c.ai_level : 'no AI review'}</td></tr>`).join('') || '<tr><td class="mut">none</td></tr>'}</tbody></table></div>
    ${r.blocked.length ? `<h3>Blocked (${r.blocked.length})</h3><div style="max-height:180px;overflow:auto"><table><tbody>${r.blocked.map(c => `<tr><td>${c.case_id}</td><td>${chip(c.level)} ${c.score}</td><td class="small">${c.reasons.map(esc).join('; ')}</td></tr>`).join('')}</tbody></table></div>` : ''}
    <h3>Reason (required, written to each case's audit trail)</h3><textarea id="bulkReason" rows="2" placeholder="e.g. Baseline signals across the board, AI concurs — false positive"></textarea>
    <div class="bar"><button class="danger" id="doBulk" ${r.clearable.length ? '' : 'disabled'}>Clear ${r.clearable.length} cases</button><button onclick="closeModal()">Cancel</button></div>`);
  const cb = $('#allowNo'); if (cb) cb.onchange = () => bulkModal(cb.checked, ids);
  $('#doBulk').onclick = () => act(async () => { const res = await api('/bulk/clear', 'POST', { case_ids: r.clearable.map(c => c.case_id), reason: $('#bulkReason').value, allow_without_ai: allowNoAI }); res.cleared.forEach(i => SEL.delete(i)); closeModal(); toast(`Cleared ${res.cleared.length} cases` + (res.blocked.length ? `, ${res.blocked.length} blocked at commit` : '')); await loadMeta(); viewQueue(); });
}
function bulkEscalateModal() {
  modal(`<h2>Escalate ${SEL.size} case(s) to supervisor</h2><textarea id="escReason" rows="3" placeholder="Reason for escalation (required)"></textarea>
   <div class="bar"><button class="primary" id="doEsc">Escalate</button><button onclick="closeModal()">Cancel</button></div>`);
  $('#doEsc').onclick = () => act(async () => { await api('/bulk/escalate', 'POST', { case_ids: [...SEL], reason: $('#escReason').value }); SEL.clear(); closeModal(); toast('Escalated'); await loadMeta(); viewQueue(); });
}

/* ------------------------------------------------------------------ case */
const SIGS = [['weekly_visit_frequency', 'Visits / week'], ['member_provider_distance_miles', 'Distance (mi)'], ['prior_claims_last_12mo', 'Prior claims 12 mo'],
  ['weekend_billing_ratio', 'Weekend billing'], ['amount_vs_peer_avg_pct', 'Amount vs peer %'], ['round_dollar_billing_ratio', 'Round-dollar ratio'], ['implied_weekly_travel_miles', 'Implied miles / wk']];
const FLAGS = [['duplicate_service_billed', 'Duplicate service'], ['service_overlap_other_provider', 'Overlaps other provider'], ['shared_contact_with_provider', 'Shared contact'], ['recent_policy_change_flag', 'Recent policy change']];
let CHAT_BUSY = false;

async function viewCase(id) {
  const c = await act(() => api('/cases/' + id)); if (!c || gone()) return;
  const d = c.data, a = c.assessment, ok = a && a.status === 'OK', cal = META.calibration.signals;
  const stale = ok && a.input_hash !== c.input_hash;
  const fb = {}; c.feedback.forEach(f => fb[f.indicator_key] = f.decision);
  const key = i => (i.rule_id || '') + ':' + i.field;
  const sig = ([k, l]) => { const t = cal[k], v = d[k], tier = v >= t.extreme_threshold ? 'HIGH' : v >= t.elevated_threshold ? 'MEDIUM' : ''; const pct = Math.max(2, Math.min(100, 100 * Math.max(v, 0) / Math.max(t.max, 1)));
    return `<div class="sigrow"><span>${l}</span><div class="track"><b class="${tier}" style="width:${pct}%"></b><i style="left:${100 * t.elevated_threshold / t.max}%" title="elevated ≥ ${t.elevated_threshold}"></i><i style="left:${100 * t.extreme_threshold / t.max}%" title="extreme ≥ ${t.extreme_threshold}"></i></div><b class="num">${v}</b></div>`; };
  view.innerHTML = `
  <div class="bar"><a href="#/queue">← Queue</a></div>
  <div class="bar" style="align-items:flex-start"><div style="flex:1"><h1>${c.case_id} · ${esc(c.care_type)} · ${esc(c.state)} · ${money(c.amount)} ${stChip(c.status)}</h1>
    <div class="sub">Claim ${esc(c.claim_number)} · ${esc(d.claim_date)} · assigned to ${esc(c.assigned_to)} · ${d.binary_flag_count}/4 binary flags</div></div>
    <div style="text-align:right"><div class="small mut">Rule engine</div>${chip(c.level)} <b style="font-size:22px">${c.score}</b>
    ${ok ? `<div class="small mut" style="margin-top:4px">AI judge</div>${chip(a.ai_level)} <b style="font-size:22px">${a.ai_score}</b>` : ''}</div></div>
  ${declineBanner(c)}
  ${c.linked.length ? `<div class="banner warn">🔗 Claim number ${esc(c.claim_number)} is also on ${c.linked.map(l => `<a href="#/case/${l.case_id}">${l.case_id}</a> (${chip(l.level)} ${l.care_type}, ${money(l.amount)}, ${l.claim_date})`).join(', ')}</div>` : ''}
  <div class="two"><div class="stack">
    <div class="card"><h2>AI assessment ${ok ? `<span class="small mut">· ${esc(a.model)} · ${esc(a.created_at.replace('T', ' ').slice(0, 16))}</span>` : ''}</h2>
      ${c.status === 'DECLINED' ? '<div class="small mut" style="margin-bottom:6px">⛔ Informational only: this case is declined by the blocklist, so this AI review does not drive any decision.</div>' : ''}
      ${!a ? `<div class="banner info">No AI assessment yet (batch triage may still be running).</div>` : !ok ? `<div class="banner bad">LLM unavailable at last attempt — showing rules only. ${esc(a.error || '')}</div>` : `
      ${stale ? `<div class="banner warn">Notes or feedback changed since this assessment. <button class="sm" onclick="reassess('${id}')">Re-assess with latest notes</button></div>` : ''}
      <p style="font-size:15px;margin:4px 0 8px">${esc(a.result.summary)}</p>
      <div class="bar"><span class="chip st">${esc(a.verdict)}${a.result.score_adjustment ? ' ' + (a.result.score_adjustment > 0 ? '+' : '') + a.result.score_adjustment : ''}</span>
        <span class="chip st">Recommend: ${esc(a.action.replace(/_/g, ' '))}</span><span class="chip st">Confidence ${esc(a.result.confidence)}</span></div>
      ${a.result.adjustment_reason ? `<div class="small mut">${esc(a.result.adjustment_reason)}</div>` : ''}
      <h3>Key indicators — accept or reject</h3>
      ${a.result.key_indicators.map(i => `<div class="ind ${fb[key(i)] || ''}"><div class="body">${i.rule_id ? `<span class="rid">${i.rule_id}</span>` : ''}<span class="fv">${esc(i.field)} = ${esc(JSON.stringify(i.value))}</span><div class="small">${esc(i.why)}</div></div>
        <button class="sm ${fb[key(i)] === 'ACCEPT' ? 'ok' : ''}" onclick='feedback(${a.id},${JSON.stringify(key(i))},${JSON.stringify(i.field + " = " + i.value + " — " + i.why)},"ACCEPT")' title="Agree">✓</button>
        <button class="sm ${fb[key(i)] === 'REJECT' ? 'danger' : ''}" onclick='feedback(${a.id},${JSON.stringify(key(i))},${JSON.stringify(i.field + " = " + i.value + " — " + i.why)},"REJECT")' title="Disagree">✗</button></div>`).join('') || '<div class="mut small">No grounded indicators.</div>'}
      ${a.result.mitigating_factors.length ? `<h3>Mitigating factors</h3>${a.result.mitigating_factors.map(i => `<div class="small"><span class="fv">${esc(i.field)} = ${esc(JSON.stringify(i.value))}</span> — ${esc(i.why)}</div>`).join('')}` : ''}
      ${a.result.next_steps.length ? `<h3>Suggested next steps</h3><ul class="tight">${a.result.next_steps.map(s => `<li>${esc(s)}</li>`).join('')}</ul>` : ''}
      ${a.result.open_questions.length ? `<h3>Open questions</h3><ul class="tight">${a.result.open_questions.map(s => `<li>${esc(s)}</li>`).join('')}</ul>` : ''}
      ${a.warnings && a.warnings.length ? `<details><summary>${a.warnings.length} guardrail note(s) — content the system removed or flagged</summary><ul class="tight small">${a.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></details>` : ''}
      ${a.trace && a.trace.length ? `<details><summary>Agent trace: ${a.trace.length} tool call(s)</summary>${a.trace.map(t => `<pre>${esc(t.tool)}(${esc(JSON.stringify(t.args))})\n→ ${esc(t.result_preview)}</pre>`).join('')}</details>` : ''}`}
      <div class="bar"><button id="reassess" onclick="reassess('${id}')">↻ Re-run AI assessment</button>${c.assessment_history.length > 1 ? `<span class="small mut">${c.assessment_history.length} versions</span>` : ''}</div></div>
    ${ok && a.result.crew ? traceCard(a.result.crew, a) : ''}
    <div class="card"><h2>Rules fired (${c.rules.length})</h2>${c.rules.length ? `<table><tbody>${c.rules.map(r => `<tr><td><span class="rid ${r.level}">${r.rule_id}</span></td><td><b>${esc(r.name)}</b> ${chip(r.level)}<div class="small mut">${esc(r.evidence)}</div></td></tr>`).join('')}</tbody></table>` : '<div class="mut">No rules fired — every signal is within the baseline range.</div>'}</div>
    <div class="card"><h2>Signals vs portfolio</h2><div class="small mut" style="margin-bottom:6px">Ticks mark the calibrated <i>elevated</i> and <i>extreme</i> thresholds.</div>${SIGS.map(sig).join('')}
      <div class="bar" style="margin-top:10px">${FLAGS.map(([k, l]) => `<span class="chip ${d[k] ? 'HIGH' : 'LOW'}">${d[k] ? '⚑' : '✓'} ${l}</span>`).join('')}</div></div>
    <div class="card"><h2>Similar cases</h2><div id="similar" class="small mut">loading…</div></div>
  </div><div class="stack">
    <div class="card"><h2>Decision</h2><div class="bar">
      <button ${c.status === 'DECLINED' ? 'disabled' : ''} onclick="setStatus('${id}','IN_REVIEW')">Mark in review</button>
      <button ${c.status === 'DECLINED' ? 'disabled' : ''} class="ok" onclick="reasonModal('${id}','CLEARED','Clear as false positive')">Clear</button>
      <button ${c.status === 'DECLINED' ? 'disabled' : ''} class="danger" onclick="reasonModal('${id}','CONFIRMED','Confirm suspicious')">Confirm</button>
      <button ${c.status === 'DECLINED' ? 'disabled' : ''} onclick="escalateModal('${id}')">Escalate ▲</button></div>
      ${who().role === 'supervisor' ? `<div class="bar"><button ${c.status === 'DECLINED' ? 'disabled' : ''} onclick="reasonModal('${id}','SIU_REFERRED','Refer to SIU')">Refer to SIU</button></div>` : ''}
      ${c.escalations.length ? `<div class="small mut">${c.escalations.map(e => `Escalation #${e.id}: ${e.status}${e.decision_note ? ' — ' + esc(e.decision_note) : ''}`).join('<br>')}</div>` : ''}</div>
    <div class="card"><h2>Outcome feedback <span class="small mut">(teaches the AI)</span></h2>
      ${c.outcome ? `<div class="banner ${c.outcome.outcome === 'FRAUD' ? 'bad' : 'info'}"><b>${c.outcome.outcome === 'FRAUD' ? '🚩 Confirmed FRAUD' : '✓ Confirmed legitimate'}</b> by ${esc(c.outcome.marked_by)}${c.outcome.missed ? ' — <b>missed by the system</b> (' + esc(c.outcome.rule_level) + (c.outcome.ai_level ? ' / AI ' + esc(c.outcome.ai_level) : '') + ')' : ''}<div class="small">${esc(c.outcome.reason)}</div>
        <div class="bar"><button class="sm" onclick="retract('${id}')">Retract</button></div></div>`
      : `<div class="small mut" style="margin-bottom:6px">Cleared it or rated it low, and it turned out to be fraud? Record it: similar cases will be flagged (R17), agents will see it as ground truth, and bulk clear will refuse lookalikes.</div>
        <div class="bar"><button class="danger" onclick="outcomeModal('${id}','FRAUD')">🚩 Mark as FRAUD</button><button onclick="outcomeModal('${id}','LEGITIMATE')">Confirm legitimate</button></div>`}</div>
    ${c.related_context && c.related_context.length ? `<div class="card"><h2>Knowledge from other cases <span class="small mut">(the AI reads this)</span></h2><div class="small mut" style="margin-bottom:6px">Linked and most-similar cases that carry notes, decisions or confirmed outcomes.</div>
      ${c.related_context.map(r => `<div class="note"><div class="meta"><a href="#/case/${r.case_id}">${r.case_id}</a> · ${esc(r.relation.replace(/_/g, ' '))} · ${chip(r.level)} · ${stChip(r.status)}</div>
        ${r.confirmed_outcome ? `<div><b>${r.confirmed_outcome.outcome === 'FRAUD' ? '🚩 Confirmed FRAUD' : '✓ Confirmed legitimate'}</b>${r.confirmed_outcome.missed_by_system ? ' (missed by system)' : ''}: ${esc(r.confirmed_outcome.reason)}</div>` : ''}
        ${r.notes.map(n => `<div class="small">“${esc(n.text)}” <span class="mut">— ${esc(n.author)}</span></div>`).join('')}</div>`).join('')}</div>` : ''}
    <div class="card"><h2>Notes <span class="small mut">(the AI and the chatbot read these)</span></h2>
      ${c.notes.map(n => `<div class="note"><div class="meta">${esc(n.author)} · ${esc(n.role)} · ${esc(n.created_at.replace('T', ' ').slice(0, 16))} <a href="#" style="float:right;color:var(--crit)" onclick="delNote('${id}',${n.id});return false" title="Delete this note everywhere: the AI, chat and audit text">delete</a></div>${esc(n.text)}</div>`).join('') || '<div class="mut small" style="margin-bottom:8px">No notes yet.</div>'}
      <textarea id="noteIn" rows="2" placeholder="e.g. Called provider — confirmed member relocated in March; visit logs requested."></textarea>
      <div class="bar"><button class="primary" id="addNote">Add note</button></div></div>
    <div class="card"><h2>Ask the AI about this case</h2><div id="chatlog" class="chatlog"></div>
      <div class="bar"><input id="chatIn" style="flex:1" placeholder="e.g. Why is this HIGH? What did the investigator note?"><button class="primary" id="chatSend">Ask</button></div>
      <div class="bar">${['Why did this get flagged?', 'What are the strongest innocent explanations?', 'Summarise the notes on this case'].map(q => `<button class="sm" onclick="askQ(${JSON.stringify(q)})">${q}</button>`).join('')}</div></div>
    <div class="card"><h2>Audit trail</h2><div class="tl">${c.audit.map(e => `<div class="e"><div class="t">${esc(e.ts.replace('T', ' ').slice(0, 19))} · ${esc(e.actor)}</div><b>${esc(e.action.replace(/_/g, ' '))}</b> <span class="small mut">${esc(Object.entries(e.detail).filter(([k]) => ['from', 'to', 'reason', 'model', 'ai_score'].includes(k)).map(([k, v]) => k + ': ' + v).join(' · '))}</span></div>`).join('') || '<div class="mut small">Empty.</div>'}</div></div>
  </div></div>`;
  $('#addNote').onclick = () => act(async () => { await api(`/cases/${id}/notes`, 'POST', { text: $('#noteIn').value }); toast('Note saved — AI marked stale until re-assessed'); viewCase(id); });
  const log = $('#chatlog'); (await api(`/cases/${id}/chat`)).forEach(m => addMsg(log, m.role, m.content));
  $('#chatSend').onclick = () => askQ($('#chatIn').value); $('#chatIn').onkeydown = e => { if (e.key === 'Enter') askQ($('#chatIn').value); };
  window.askQ = q => act(async () => { q = (q || '').trim(); if (!q || CHAT_BUSY) return; CHAT_BUSY = true; $('#chatIn').value = ''; addMsg(log, 'user', q); const pend = addMsg(log, 'assistant', '…'); try { const r = await api(`/cases/${id}/chat`, 'POST', { question: q }); pend.firstChild.textContent = r.answer; pend.querySelector('small').textContent = modelTag(r); } catch (e) { pend.remove(); throw e; } finally { CHAT_BUSY = false; } });
  loadSimilar(c); loadSpans(id);
}
function traceCard(cr, a) {
  const u = cr.usage || {};
  const conf = cr.confidence_score == null ? '' : `<span class="chip st">Calibrated confidence ${(cr.confidence_score * 100).toFixed(0)}%</span>`;
  const f = Object.entries(cr.findings || {}).map(([k, v]) => `<div class="ind"><div class="body"><b>${esc(k)}</b> ${chip(v.direction === 'suspicious' ? 'HIGH' : v.direction === 'benign' ? 'LOW' : 'MEDIUM')} strength ${v.strength}${(cr.reused_specialists || []).includes(k) ? ' <span class="chip st" title="input slice unchanged: cached finding reused, no LLM call">♻ reused</span>' : ''}
      <div class="small">${esc(v.summary)}</div><div class="small mut">${(v.evidence || []).map(e => esc(e.field + '=' + JSON.stringify(e.value))).join(' · ')}</div></div></div>`).join('');
  const ch = cr.challenger ? `<h3>Challenger (argues the benign case) — plausibility ${esc(cr.challenger.plausibility)}</h3>${(cr.challenger.arguments || []).map(x => `<div class="small"><span class="fv">${esc(x.field)} = ${esc(JSON.stringify(x.value))}</span> — ${esc(x.why)}</div>`).join('')}${(cr.challenger.missing_evidence || []).length ? `<div class="small mut">Missing: ${cr.challenger.missing_evidence.map(esc).join('; ')}</div>` : ''}` : '';
  const pn = cr.panel ? `<h3>Judge panel ×3 → meta-judge ${cr.panel.split ? '<span class="chip HIGH">SPLIT</span>' : ''}</h3><div class="small">Agreement ${(cr.panel.agreement * 100).toFixed(0)}% · median adjustment ${cr.panel.adjustment > 0 ? '+' : ''}${cr.panel.adjustment} → ${chip(cr.panel.final_band)} · action ${esc(cr.panel.action.replace(/_/g, ' '))}</div>
     ${Object.entries(cr.panel.votes).map(([k, v]) => `<div class="small"><b>${k}</b>: ${v.score_adjustment > 0 ? '+' : ''}${v.score_adjustment}, ${esc(v.recommended_action.replace(/_/g, ' '))}, conf ${v.confidence} — ${esc(v.rationale)}</div>`).join('')}` : '';
  return `<div class="card"><h2>Investigation trace <span class="small mut">· how the crew reached this</span></h2>
    <div class="bar"><span class="chip st">Lane: ${esc(cr.lane)}</span>${cr.contested ? '<span class="chip HIGH">CONTESTED</span>' : '<span class="chip LOW">uncontested</span>'}${conf}
      <span class="chip st">${u.calls} call(s) · ${(u.tokens_in + u.tokens_out).toLocaleString()} tok · ref $${(u.est_cost || 0).toFixed(4)} · ${((u.latency_ms || 0) / 1000).toFixed(0)}s${cr.batch_size > 1 ? ' · batch ×' + cr.batch_size : ''}</span></div>
    <div class="small mut">${esc(cr.lane_reason)} · agents: ${(cr.agents || []).map(x => esc(x.agent) + (x.reused ? '♻' : '')).join(' → ')}</div>
    ${cr.contested_reasons && cr.contested_reasons.length ? `<div class="banner warn"><b>Why contested:</b><ul class="tight">${cr.contested_reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>` : ''}
    ${f ? '<h3>Specialist findings</h3>' + f : ''}${ch}${pn}
    <div id="spans" class="small mut" style="margin-top:8px"></div></div>`;
}
async function loadSpans(id) {
  const runs = await api(`/cases/${id}/runs`); const el = $('#spans'); if (!el || !runs.length) return;
  const r = runs[0];
  el.innerHTML = `<details><summary>Raw spans (${r.spans.length}) — every LLM and tool call</summary><table><tbody>${r.spans.map(s => `<tr><td>${esc(s.agent)}</td><td>${esc(s.kind)}</td><td>${esc(s.name)}</td><td class="small mut">${esc(s.detail)}</td><td class="num">${s.ms ? s.ms + 'ms' : ''}</td></tr>`).join('')}</tbody></table></details>`;
}
async function loadSimilar(c) {
  const rows = (await api('/cases/search', 'POST', { sort: 'score', order: 'desc' })).filter(r => r.care_type === c.care_type && r.case_id !== c.case_id).slice(0, 5);
  const el = $('#similar'); if (!el) return;
  el.innerHTML = rows.length ? `<table><tbody>${rows.map(r => `<tr class="row" onclick="location.hash='#/case/${r.case_id}'"><td>${r.case_id}</td><td>${chip(r.level)} ${r.score}</td><td>${money(r.amount)}</td><td>${stChip(r.status)}</td></tr>`).join('')}</tbody></table><div class="small mut">Same care type, highest scores. The AI also gets signal-profile nearest neighbours.</div>` : 'none';
}
async function delNote(id, nid) {
  if (!confirm('Delete this note permanently? It is removed from the AI\'s knowledge, chat context, related-case context and audit text, and AI assessments that saw it are regenerated.')) return;
  await act(async () => { const r = await api(`/cases/${id}/notes/${nid}`, 'DELETE'); toast(`Note deleted. Purged ${r.assessments} assessment(s), ${r.chat_messages} chat message(s), redacted ${r.audit_redacted} audit row(s); AI re-assessing.`, 6000); await loadMeta(); viewCase(id); });
}
function outcomeModal(id, outcome) {
  const fraud = outcome === 'FRAUD';
  modal(`<h2>${fraud ? '🚩 Mark ' + id + ' as confirmed FRAUD' : 'Confirm ' + id + ' was legitimate'}</h2>
    <div class="small mut" style="margin-bottom:8px">${fraud ? 'This case becomes ground truth. Similar cases fire R17 and rise in risk; agents see it as a confirmed outcome (and whether the system had missed it); bulk clear refuses lookalikes.' : 'Recorded as precedent that a similar-looking case was benign.'}</div>
    <textarea id="oc" rows="3" placeholder="What was discovered? (required)"></textarea>
    <div class="bar"><button class="${fraud ? 'danger' : 'ok'}" id="ocgo">Record outcome</button><button onclick="closeModal()">Cancel</button></div>`);
  $('#ocgo').onclick = () => act(async () => { const r = await api(`/cases/${id}/outcome`, 'POST', { outcome, reason: $('#oc').value }); closeModal(); toast((fraud && r.missed_by_system ? 'Recorded as a MISS. ' : 'Recorded. ') + `${r.rescored_cases} other case(s) re-scored; AI re-assessing.`, 6000); await loadMeta(); viewCase(id); });
}
async function retract(id) { if (!confirm('Retract this outcome? Assessments quoting it are purged and regenerated.')) return; await act(async () => { await api(`/cases/${id}/outcome`, 'DELETE'); toast('Outcome retracted'); await loadMeta(); viewCase(id); }); }
async function reassess(id) { toast('Re-assessing… free models can take a minute'); await act(async () => { await api(`/cases/${id}/assess`, 'POST'); toast('Assessment updated'); viewCase(id); }); }
async function feedback(aid, k, t, dec) { await act(async () => { const id = location.hash.split('/')[2]; await api(`/cases/${id}/feedback`, 'POST', { assessment_id: aid, indicator_key: k, indicator_text: t, decision: dec }); viewCase(id); }); }
async function setStatus(id, status, reason = '') { await act(async () => { await api(`/cases/${id}/status`, 'POST', { status, reason }); toast('Status: ' + status); await loadMeta(); viewCase(id); }); }
function reasonModal(id, status, title) {
  modal(`<h2>${title}</h2><textarea id="rsn" rows="3" placeholder="Reason (recorded in the audit trail and visible to the AI)"></textarea><div class="bar"><button class="primary" id="rgo">Confirm</button><button onclick="closeModal()">Cancel</button></div>`);
  $('#rgo').onclick = () => act(async () => { await setStatus(id, status, $('#rsn').value); closeModal(); });
}
function escalateModal(id) {
  modal(`<h2>Escalate ${id} to supervisor</h2><label class="small">Reason (required)</label><textarea id="er" rows="2"></textarea>
    <label class="small">Handoff summary</label><textarea id="es" rows="5" placeholder="Write one, or let the AI draft it from the case file and notes"></textarea>
    <div class="bar"><button id="draft">✨ Draft with AI</button><button class="primary" id="ego">Escalate</button><button onclick="closeModal()">Cancel</button></div>`);
  $('#draft').onclick = () => act(async () => { $('#draft').innerHTML = '<span class="spin"></span>'; try { const r = await api(`/cases/${id}/escalate/draft`, 'POST'); $('#es').value = r.summary; } finally { $('#draft').textContent = '✨ Draft with AI'; } });
  $('#ego').onclick = () => act(async () => { await api(`/cases/${id}/escalate`, 'POST', { reason: $('#er').value, summary: $('#es').value }); closeModal(); toast('Escalated to supervisor'); await loadMeta(); viewCase(id); });
}

/* ------------------------------------------------------------------ supervisor / audit */
async function viewSupervisor() {
  const all = await api('/escalations'); const w = who();
  view.innerHTML = `<h1>Supervisor queue</h1><div class="sub">${w.role === 'supervisor' ? 'Decide on escalated cases.' : 'Read-only: switch persona to Supervisor (top right) to decide.'}</div>
  ${all.map(e => `<div class="card" style="margin-bottom:12px"><div class="bar"><b><a href="#/case/${e.case_id}">${e.case_id}</a></b> ${esc(e.care_type)} · ${money(e.amount)} · ${chip(e.level)} ${e.score} <span class="chip st ${e.status}">${e.status}</span><span class="small mut">from ${esc(e.from_user)} · ${esc(e.created_at.replace('T', ' ').slice(0, 16))}</span></div>
    <div><b>Reason:</b> ${esc(e.reason)}</div>${e.summary ? `<pre>${esc(e.summary)}</pre>` : ''}
    ${e.status === 'PENDING' ? `<div class="bar"><input id="dn${e.id}" placeholder="Note (required to return)" style="flex:1"><button class="ok" ${w.role !== 'supervisor' ? 'disabled' : ''} onclick="decide(${e.id},'APPROVE')">Approve → SIU</button><button ${w.role !== 'supervisor' ? 'disabled' : ''} onclick="decide(${e.id},'RETURN')">Return to investigator</button></div>` : `<div class="small mut">Decided by ${esc(e.decided_by)}: ${esc(e.decision_note || '')}</div>`}</div>`).join('') || '<div class="card mut">No escalations yet.</div>'}`;
}
async function decide(id, decision) { await act(async () => { await api(`/escalations/${id}/decide`, 'POST', { decision, note: $('#dn' + id).value }); toast('Recorded'); await loadMeta(); viewSupervisor(); }); }
async function viewAudit() {
  const rows = await api('/audit?limit=300');
  view.innerHTML = `<div class="bar"><h1 style="flex:1">Audit log</h1><a class="btn" href="/api/audit.csv">Export CSV</a></div><div class="sub">Every AI action and human decision, with who, what and when.</div>
  <div class="card" style="padding:0;overflow:auto"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Case</th><th>Detail</th></tr></thead><tbody>${rows.map(r => `<tr><td class="small">${esc(r.ts.replace('T', ' ').slice(0, 19))}</td><td>${esc(r.actor)} <span class="small mut">${esc(r.role)}</span></td><td>${esc(r.action)}</td><td>${r.case_id ? `<a href="#/case/${r.case_id}">${r.case_id}</a>` : ''}</td><td class="small mut">${esc((r.detail || '').slice(0, 160))}</td></tr>`).join('')}</tbody></table></div>`;
}

/* ------------------------------------------------------------------ rules tab */
const RS = { mode: 'loo', pos: 'HIGH', minl: '', sel: null, isNew: false, draft: null, params: null, fields: null, lb: null, bt: null, sort: 'f1', stale: null };
const MODE_LABEL = { loo: 'Synthetic: system risk level without this rule (leave-one-out)', ai: 'Synthetic: AI-judged level', outcomes: 'Real: confirmed outcomes only', blend: 'Confirmed outcomes, else leave-one-out' };
const pct = x => x == null ? '—' : Math.round(x * 100) + '%';
const withCI = (v, ci) => `${pct(v)} <span class="small mut">${ci && ci[0] != null ? '(' + pct(ci[0]) + '–' + pct(ci[1]) + ')' : ''}</span>`;
const blankLogic = () => ({ op: 'AND', conds: [{ field: 'weekend_billing_ratio', cmp: '>=', value: 0.4 }] });
const fieldOf = n => RS.fields.find(f => f.name === n);
const CMPS = { number: ['>=', '>', '<=', '<', '==', '!='], flag: ['==', '!='], category: ['==', '!=', 'in', 'not_in'] };

async function viewRules() {
  view.innerHTML = '<div class="sub"><span class="spin"></span> Loading rules…</div>';
  if (!RS.fields) RS.fields = (await api('/rules/fields')).fields;
  const q = `label_mode=${RS.mode}&positive_at=${RS.pos}` + (RS.minl ? `&min_level=${RS.minl}` : '');
  RS.lb = await act(() => api('/rules/leaderboard?' + q)); if (!RS.lb || gone()) return;
  drawRules();
}
function drawRules() {
  const lb = RS.lb, rows = [...lb.rows].sort((a, b) => RS.sort === 'id' ? a.rule_id.localeCompare(b.rule_id) : ((b[RS.sort] ?? -1) - (a[RS.sort] ?? -1)));
  const th = (k, l) => `<th class="s ${'num'}" onclick="RS.sort='${k}';drawRules()">${l}${RS.sort === k ? ' ▼' : ''}</th>`;
  view.innerHTML = `<div class="bar"><h1 style="flex:1">Rules</h1><button class="primary" onclick="newRule()">+ New rule</button></div>
  <div class="sub">Every rule that scores cases, its precision/recall against labels, and the tools to create or tune one. Changes apply to all cases immediately.</div>
  <div class="card"><div class="filters">
    <label style="min-width:330px">Labels<select id="r_mode">${Object.entries(MODE_LABEL).map(([k, v]) => `<option value="${k}" ${RS.mode === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    <label>Positive = risk level ≥<select id="r_pos">${['MEDIUM', 'HIGH', 'CRITICAL'].map(x => `<option ${RS.pos === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    <label>Rule counts as firing at ≥<select id="r_min"><option value="">any level</option>${['MEDIUM', 'HIGH'].map(x => `<option ${RS.minl === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    <span class="small mut">Real outcomes recorded: <b>${lb.outcomes_available}</b></span></div>
    <div class="banner ${RS.mode === 'outcomes' ? 'info' : 'warn'}" style="margin:0">${esc(lb.warning)}</div>
    <div class="bar" style="margin:8px 0 0"><input id="aiTxt" style="flex:1" placeholder="Describe a rule in plain English, e.g. “heavy weekend billing together with mostly round-dollar charges” — the AI drafts it for you to review"><button id="aiBtn">✨ Draft with AI</button></div></div>
  ${RS.stale ? `<div class="banner info" style="margin-top:12px">${RS.stale.text} ${RS.stale.ai_stale ? `<button class="sm" onclick="reassessStale()">Re-assess ${RS.stale.ai_stale} stale case(s) with AI</button>` : ''}</div>` : ''}
  <div class="two" style="margin-top:12px"><div class="card" style="padding:0;overflow:auto"><table><thead><tr><th onclick="RS.sort='id';drawRules()" class="s">Rule</th><th>Domain</th><th>Status</th>${th('fires', 'Fires')}${th('precision', 'Precision')}${th('recall', 'Recall')}${th('f1', 'F1')}${th('lift', 'Lift')}</tr></thead><tbody>
  ${rows.map(r => `<tr class="row ${RS.sel === r.rule_id && !RS.isNew ? 'sel' : ''}" onclick="selectRule('${r.rule_id}')"><td><span class="rid ${r.level || ''}">${r.rule_id}</span> <b>${esc(r.name)}</b>${r.tuned ? ' <span class="chip st" title="tuned away from the calibrated default">tuned</span>' : ''}${r.kind === 'custom' ? ' <span class="chip st">custom</span>' : ''}<div class="small mut">${esc(r.logic)}</div></td>
    <td class="small">${esc(r.domain)}</td><td onclick="event.stopPropagation()"><span class="chip ${r.status === 'active' ? 'LOW' : r.status === 'shadow' ? 'MEDIUM' : 'st'} tog" title="click to ${r.status === 'active' ? 'disable' : 'enable'}" onclick="toggleRule('${r.rule_id}','${r.kind}','${r.status}')">${r.status}</span></td>
    <td class="num">${r.fires ?? '—'}</td><td class="num">${withCI(r.precision, r.precision_ci)}</td><td class="num">${withCI(r.recall, r.recall_ci)}</td><td class="num">${pct(r.f1)}</td><td class="num">${r.lift ?? '—'}</td></tr>`).join('')}</tbody></table></div>
  <div id="rdetail"></div></div>`;
  $('#r_mode').onchange = e => { RS.mode = e.target.value; viewRules(); }; $('#r_pos').onchange = e => { RS.pos = e.target.value; viewRules(); }; $('#r_min').onchange = e => { RS.minl = e.target.value; viewRules(); };
  $('#aiBtn').onclick = aiDraft;
  drawDetail();
}
function selectRule(id) {
  EDIT_REDRAW = EDIT_AFTER = null;
  const r = RS.lb.rows.find(x => x.rule_id === id); if (!r) return;
  RS.sel = id; RS.isNew = false; RS.bt = null;
  if (r.kind === 'custom') RS.draft = { name: r.name, description: r.description, domain: r.domain, level: r.level, status: r.status === 'disabled' ? 'disabled' : r.status, logic: JSON.parse(JSON.stringify(r.logic_tree)) }, RS.params = null;
  else RS.draft = null, RS.params = { enabled: r.status === 'active', ...(r.params?.type === 'thresholds' ? { elevated: r.params.elevated, extreme: r.params.extreme } : {}), ...(r.params?.type === 'level' ? { level: r.params.level } : {}) };
  drawRules(); runBacktest();
}
function newRule(draft) {
  EDIT_REDRAW = EDIT_AFTER = null;
  RS.sel = null; RS.isNew = true; RS.params = null; RS.bt = null;
  RS.draft = draft || { name: '', description: '', domain: 'Custom', level: 'MEDIUM', status: 'active', logic: blankLogic() };
  drawRules(); runBacktest();
}
async function aiDraft() {
  const t = $('#aiTxt').value.trim(); if (!t) return;
  $('#aiBtn').innerHTML = '<span class="spin"></span> Drafting…';
  try { const d = await api('/rules/draft', 'POST', { description: t }); toast('AI drafted a rule — review it, check the backtest, then save', 6000); newRule({ name: d.name, description: d.description, domain: d.domain, level: d.level, status: 'active', logic: d.logic }); }
  catch (e) { toast('⚠ ' + e.message, 8000); $('#aiBtn').textContent = '✨ Draft with AI'; }
}
function drawDetail() {
  const el = $('#rdetail'); if (!el) return;
  if (!RS.isNew && !RS.sel) { el.innerHTML = '<div class="card mut">Select a rule to inspect, tune and backtest it — or create a new one.</div>'; return; }
  const r = RS.isNew ? null : RS.lb.rows.find(x => x.rule_id === RS.sel);
  let form = '';
  if (RS.draft) {
    const d = RS.draft;
    form = `<div class="filters"><label style="flex:1">Name<input id="d_name" value="${esc(d.name)}" style="width:100%"></label>
      <label>Domain<select id="d_dom">${['Billing', 'Utilization', 'Geography', 'Relationship', 'Timing', 'Integrity', 'Custom'].map(x => `<option ${d.domain === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
      <label>Level<select id="d_lvl">${['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map(x => `<option ${d.level === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label></div>
      <label class="small mut">Description</label><input id="d_desc" value="${esc(d.description)}" style="width:100%;margin-bottom:6px">
      <div class="small mut">A domain counts once in the score (strongest rule wins), so a rule in a domain that already has an equal-or-stronger rule fires but adds nothing — the impact preview tells you. Use “Custom” for independent evidence.</div>
      <h3>Conditions</h3>${groupHtml(d.logic, [])}`;
  } else if (r && r.params) {
    const p = r.params;
    form = `<label class="small"><input type="checkbox" id="p_en" ${RS.params.enabled ? 'checked' : ''}> Enabled</label>`;
    if (p.type === 'thresholds') form += `<div class="filters" style="margin-top:8px"><label>MEDIUM at ≥<input type="number" step="any" id="p_el" placeholder="${p.default_elevated}" value="${RS.params.elevated ?? ''}"></label><label>HIGH at ≥<input type="number" step="any" id="p_ex" placeholder="${p.default_extreme}" value="${RS.params.extreme ?? ''}"></label></div><div class="small mut">Calibrated defaults (natural breaks in the data): ${p.default_elevated} / ${p.default_extreme} for <code>${p.signal}</code>. Leave blank to keep them.</div>`;
    if (p.type === 'level') form += `<div class="filters" style="margin-top:8px"><label>Level<select id="p_lv">${['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map(x => `<option ${(RS.params.level || p.default_level) === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label></div><div class="small mut">Calibrated default: ${p.default_level}.</div>`;
  } else form = `<div class="small mut">${r ? esc(r.description) : ''}<br>This rule can be enabled or disabled from the table. ${r && r.kind === 'builtin' ? 'It is derived from other signals, so it has no thresholds of its own.' : ''}</div>`;
  const custom = RS.draft != null;
  el.innerHTML = `<div class="card"><h2>${RS.isNew ? 'New rule' : `<span class="rid">${esc(RS.sel)}</span> ${esc(r.name)}`} <span class="small mut">${custom ? (RS.isNew ? 'draft' : 'custom rule') : 'built-in'}</span></h2>${form}
    <div class="bar">${custom ? `<button class="primary" id="d_save">${RS.isNew ? 'Save & activate' : 'Save changes'}</button><button id="d_shadow">Save as shadow</button>` : (r && r.params ? '<button class="primary" id="p_save">Save tuning</button><button id="p_reset">Reset to calibrated default</button>' : '')}
      ${custom && !RS.isNew ? '<button class="danger" id="d_del">Delete</button>' : ''}${!RS.isNew ? '<button id="d_hist">History</button>' : ''}</div>
    <div id="btbox" class="small mut"><span class="spin"></span> Backtesting…</div></div>`;
  if (custom) { bindDraft(); } else { const on = () => { RS.params.enabled = $('#p_en')?.checked ?? RS.params.enabled; if ($('#p_el')) { RS.params.elevated = $('#p_el').value === '' ? null : +$('#p_el').value; RS.params.extreme = $('#p_ex').value === '' ? null : +$('#p_ex').value; } if ($('#p_lv')) RS.params.level = $('#p_lv').value; runBacktest(true); };
    ['p_en', 'p_el', 'p_ex', 'p_lv'].forEach(i => $('#' + i) && ($('#' + i).onchange = on));
    $('#p_save') && ($('#p_save').onclick = saveTuning); $('#p_reset') && ($('#p_reset').onclick = resetRule); }
  $('#d_hist') && ($('#d_hist').onclick = showHistory);
  if (RS.bt) drawBt();
}
function groupHtml(g, path) {
  const id = path.join('.');
  return `<div class="grp"><div class="bar" style="margin:0 0 4px"><span class="chip st opbtn" onclick="setOp('${id}')" title="click to switch AND/OR">${g.op}</span><span class="small mut">${g.op === 'AND' ? 'all of these' : 'any of these'}</span>
    <button class="sm" onclick="addCond('${id}')">+ condition</button>${path.length < 2 ? `<button class="sm" onclick="addGroup('${id}')">+ group</button>` : ''}${path.length ? `<span class="x" onclick="delNode('${id}')" title="remove group">✕</span>` : ''}</div>
    ${g.conds.map((c, i) => c.conds ? groupHtml(c, [...path, i]) : condHtml(c, [...path, i], g.conds.length > 1 || path.length)).join('')}</div>`;
}
function condHtml(c, path, canDel) {
  const id = path.join('.'), f = fieldOf(c.field) || RS.fields[0], t = f.type;
  const val = t === 'category' ? (c.cmp === 'in' || c.cmp === 'not_in'
      ? `<select multiple size="3" onchange="setCond('${id}','value',[...this.selectedOptions].map(o=>o.value))">${f.values.map(v => `<option ${(c.value || []).includes(v) ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`
      : `<select onchange="setCond('${id}','value',this.value)">${f.values.map(v => `<option ${c.value === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`)
    : t === 'flag' ? `<select onchange="setCond('${id}','value',+this.value)"><option value="1" ${c.value === 1 ? 'selected' : ''}>1 (yes)</option><option value="0" ${c.value === 0 ? 'selected' : ''}>0 (no)</option></select>`
    : `<input type="number" step="any" value="${c.value}" onchange="setCond('${id}','value',this.value===''?0:+this.value)">`;
  return `<div class="cond"><select onchange="setCond('${id}','field',this.value)">${RS.fields.map(x => `<option value="${x.name}" ${x.name === c.field ? 'selected' : ''}>${esc(x.label)}</option>`).join('')}</select>
    <select onchange="setCond('${id}','cmp',this.value)">${CMPS[t].map(o => `<option ${o === c.cmp ? 'selected' : ''} value="${o}">${o.replace('_', ' ')}</option>`).join('')}</select>${val}${canDel ? `<span class="x" onclick="delNode('${id}')" title="remove">✕</span>` : ''}</div>`;
}
const nodeAt = (path) => path.split('.').filter(x => x !== '').reduce((n, i) => n.conds[+i], RS.draft.logic);
let EDIT_REDRAW = null, EDIT_AFTER = null;
function editDraft(fn) { fn(); const y = window.scrollY; (EDIT_REDRAW || drawDetail)(); window.scrollTo(0, y); (EDIT_AFTER || (() => runBacktest(true)))(); }
function setOp(p) { editDraft(() => { const n = nodeAt(p); n.op = n.op === 'AND' ? 'OR' : 'AND'; }); }
function addCond(p) { editDraft(() => nodeAt(p).conds.push({ field: 'claim_amount_usd', cmp: '>=', value: 10000 })); }
function addGroup(p) { editDraft(() => nodeAt(p).conds.push({ op: 'OR', conds: [{ field: 'duplicate_service_billed', cmp: '==', value: 1 }] })); }
function delNode(p) { editDraft(() => { const parts = p.split('.'); const idx = +parts.pop(); nodeAt(parts.join('.')).conds.splice(idx, 1); }); }
function setCond(p, k, v) {
  editDraft(() => { const c = nodeAt(p); c[k] = v;
    if (k === 'field') { const f = fieldOf(v); c.cmp = f.type === 'category' ? '==' : (f.type === 'flag' ? '==' : '>='); c.value = f.type === 'category' ? f.values[0] : f.type === 'flag' ? 1 : 0; }
    if (k === 'cmp') { const f = fieldOf(c.field); if (f.type === 'category') c.value = (v === 'in' || v === 'not_in') ? [f.values[0]] : f.values[0]; } });
}
function bindDraft() {
  const sync = () => { RS.draft.name = $('#d_name').value; RS.draft.description = $('#d_desc').value; RS.draft.domain = $('#d_dom').value; RS.draft.level = $('#d_lvl').value; };
  ['d_name', 'd_desc'].forEach(i => $('#' + i).oninput = sync);
  ['d_dom', 'd_lvl'].forEach(i => $('#' + i).onchange = () => { sync(); runBacktest(true); });
  $('#d_save').onclick = () => saveRule('active'); $('#d_shadow').onclick = () => saveRule('shadow');
  $('#d_del') && ($('#d_del').onclick = deleteRule);
}
let BT_T = null;
function runBacktest(debounce) {
  clearTimeout(BT_T);
  const go = async () => {
    const body = { label_mode: RS.mode, positive_at: RS.pos, min_level: RS.minl || null };
    if (RS.draft) { body.rule_id = RS.isNew ? null : RS.sel; body.draft = { ...RS.draft, status: 'active' }; }
    else if (RS.sel) { body.rule_id = RS.sel; body.draft = { params: { level: RS.params?.level, elevated: RS.params?.elevated, extreme: RS.params?.extreme } }; }
    else return;
    try { RS.bt = await api('/rules/backtest', 'POST', body); RS.btErr = null; } catch (e) { RS.bt = null; RS.btErr = e.message; }
    drawBt();
  };
  debounce ? BT_T = setTimeout(go, 450) : go();
}
function drawBt() {
  const b = $('#btbox'); if (!b) return;
  if (!RS.bt) { b.innerHTML = RS.btErr ? `<div class="banner bad">${esc(RS.btErr)}</div>` : ''; return; }
  const t = RS.bt, chips = ids => ids.slice(0, 14).map(c => `<a class="rid" href="#/case/${c}">${c}</a>`).join('') + (ids.length > 14 ? ` <span class="small mut">+${ids.length - 14}</span>` : '') || '<span class="small mut">none</span>';
  const im = t.impact;
  b.innerHTML = `<h3>Backtest — ${esc(MODE_LABEL[t.label_mode])}, positive = ${t.positive_at}+</h3>
    ${t.warnings.map((w, i) => `<div class="banner ${i === 0 && t.label_mode !== 'outcomes' ? 'warn' : 'info'}" style="margin:4px 0">${esc(w)}</div>`).join('')}
    <div style="margin:8px 0"><div class="metric"><b>${withCI(t.precision, t.precision_ci)}</b><span>Precision</span></div><div class="metric"><b>${withCI(t.recall, t.recall_ci)}</b><span>Recall</span></div>
      <div class="metric"><b>${pct(t.f1)}</b><span>F1</span></div><div class="metric"><b>${t.lift ?? '—'}</b><span>Lift vs base ${pct(t.base_rate)}</span></div><div class="metric"><b>${t.fires_on.length}</b><span>Fires (n=${t.n})</span></div></div>
    <div class="cm"><div class="h"></div><div class="h">Label = positive</div><div class="h">Label = negative</div>
      <div class="h">Rule fires</div><div class="good"><b>${t.tp}</b> true positive<br>${chips(t.cases.tp)}</div><div class="bad"><b>${t.fp}</b> false positive<br>${chips(t.cases.fp)}</div>
      <div class="h">Rule silent</div><div class="bad"><b>${t.fn}</b> false negative (missed)<br>${chips(t.cases.fn)}</div><div class="good"><b>${t.tn}</b> true negative<br>${chips(t.cases.tn)}</div></div>
    ${im ? `<h3>Impact if saved</h3><div class="small">${im.score_changes} case score(s) change, ${im.level_changes.length} change risk level.${im.no_effect_on.length ? ` The rule fires on ${im.no_effect_on.length} case(s) that it does <b>not</b> move — an equal-or-stronger rule already covers that domain there.` : ''}</div>
      ${im.level_changes.length ? `<table><tbody>${im.level_changes.slice(0, 12).map(x => `<tr><td><a href="#/case/${x.case_id}">${x.case_id}</a></td><td>${chip(x.from)} → ${chip(x.to)}</td><td class="num small mut">${x.score_from} → ${x.score_to}</td></tr>`).join('')}</tbody></table>` : ''}` : ''}`;
}
async function afterSave(res, verb) {
  const r = res.rescore;
  RS.stale = { text: `${verb}. ${r.rules_changed} case(s) now fire a different rule set, ${r.level_changes.length} changed risk level.`, ai_stale: r.ai_stale };
  await loadMeta(); toast(RS.stale.text, 6000);
}
async function saveRule(status) {
  const d = { ...RS.draft, status };
  await act(async () => {
    const res = RS.isNew ? await api('/rules', 'POST', d) : await api('/rules/' + RS.sel, 'PUT', d);
    RS.sel = res.rule.id; RS.isNew = false; RS.draft = null; await afterSave(res, status === 'shadow' ? 'Saved as shadow (backtested, not scoring)' : 'Saved and applied to all cases');
    await viewRules(); selectRule(res.rule.id);
  });
}
async function saveTuning() {
  const p = RS.params;
  await act(async () => { const res = await api('/rules/' + RS.sel, 'PUT', { enabled: p.enabled, elevated: p.elevated, extreme: p.extreme, level: p.level || undefined }); await afterSave(res, 'Tuning saved'); const id = RS.sel; await viewRules(); selectRule(id); });
}
async function resetRule() { await act(async () => { const res = await api(`/rules/${RS.sel}/reset`, 'POST'); await afterSave(res, 'Reset to calibrated default'); const id = RS.sel; await viewRules(); selectRule(id); }); }
async function deleteRule() { if (!confirm('Delete this rule? It stops firing and all cases are re-scored.')) return; await act(async () => { const res = await api('/rules/' + RS.sel, 'DELETE'); RS.sel = null; RS.draft = null; await afterSave(res, 'Rule deleted'); await viewRules(); }); }
async function toggleRule(id, kind, status) {
  const on = status !== 'active';
  await act(async () => { const res = kind === 'custom' ? await api('/rules/' + id, 'PUT', { status: on ? 'active' : 'disabled' }) : await api('/rules/' + id, 'PUT', { enabled: on });
    await afterSave(res, `${id} ${on ? 'enabled' : 'disabled'}`); await viewRules(); });
}
async function showHistory() {
  const h = await api(`/rules/${RS.sel}/history`);
  modal(`<h2>${RS.sel} — history</h2>${h.length ? `<table><tbody>${h.map(x => `<tr><td>v${x.version}</td><td>${esc(x.note)}</td><td class="small">${esc(x.changed_by)}</td><td class="small mut">${esc(x.changed_at.replace('T', ' ').slice(0, 16))}</td><td class="small mut"><code>${esc(JSON.stringify(x.definition).slice(0, 160))}</code></td></tr>`).join('')}</tbody></table>` : '<div class="mut">No changes recorded — this is the calibrated default.</div>'}<div class="bar"><button onclick="closeModal()">Close</button></div>`);
}
async function reassessStale() { await act(async () => { await api('/assess-all', 'POST'); toast('AI re-assessing the stale cases in the background…', 5000); RS.stale = null; await loadMeta(); drawRules(); }); }

/* ------------------------------------------------------------------ blocklist */
function declineBanner(c) {
  const d = c.decline; if (!d) return '';
  const reasons = d.details.map(x => `<li><b>${esc(x.name)}</b> (${esc(x.kind.replace('_', ' '))}): ${esc(x.detail)}<div class="small">${esc(x.reason)}</div></li>`).join('');
  if (d.enforced && c.status === 'DECLINED') return `<div class="banner bad"><b>⛔ DECLINED by the blocklist</b> — hard rule(s) hit; scoring and AI review are bypassed and the case is locked.<ul class="tight">${reasons}</ul>
    ${who().role === 'supervisor' ? `<button onclick="overrideModal('${c.case_id}')">Override decline…</button>` : '<span class="small">A supervisor can override this with a written reason (switch persona, top right).</span>'}</div>`;
  if (d.enforced) return '';
  return `<div class="banner warn"><b>⚠ Blocklist match on a closed case</b> (status ${esc(c.status)}) — not re-opened automatically; please review.<ul class="tight">${reasons}</ul></div>`;
}
function overrideModal(id) {
  modal(`<h2>Override the blocklist decline on ${id}</h2><div class="small mut" style="margin-bottom:8px">The case returns to review. Your reason is saved as a case note (agents and colleagues see it) and counts as a false-positive signal against the entries that fired. A different entry hitting it later will decline it again.</div>
    <textarea id="ovr" rows="3" placeholder="Why is this decline wrong? (required)"></textarea><div class="bar"><button class="primary" id="ovrgo">Override decline</button><button onclick="closeModal()">Cancel</button></div>`);
  $('#ovrgo').onclick = () => act(async () => { await api(`/cases/${id}/decline/override`, 'POST', { reason: $('#ovr').value }); closeModal(); toast('Decline overridden — case back in review'); await loadMeta(); if (location.hash.startsWith('#/case')) viewCase(id); else viewBlocklist(); });
}
const BL = { entries: [], declined: [], sel: null, isNew: false, draft: null, pv: null, text: '', msg: null };
const KIND_LABEL = { claim_number: 'Claim numbers', provider_id: 'Provider IDs', member_id: 'Member IDs', segment: 'Segment (state / care type)', condition: 'Hard condition on case data', fraud_history: 'Fraud history' };
const METRIC_LABEL = { claim_number_frauds: 'confirmed frauds on the same claim number', provider_frauds: 'confirmed frauds on the same provider ID', member_frauds: 'confirmed frauds on the same member ID', segment_fraud_count: 'confirmed frauds in the same segment', segment_fraud_rate: 'confirmed-fraud RATE in the same segment' };
const defaultSpec = k => k === 'segment' ? { state: '', care_type: '' } : k === 'condition' ? { logic: blankLogic() } : k === 'fraud_history' ? { metric: 'segment_fraud_count', threshold: 2, min_cases: 5, dims: ['state', 'care_type'] } : { values: [] };

async function viewBlocklist() {
  if (!RS.fields) RS.fields = (await api('/rules/fields')).fields;
  const [entries, declined] = await Promise.all([api('/blocklist'), api('/declined')]); if (gone()) return;
  BL.entries = entries; BL.declined = declined; drawBlocklist();
}
function drawBlocklist() {
  const sup = who().role === 'supervisor', active = BL.entries.filter(e => e.status === 'active'), dec = BL.declined.filter(d => d.enforced && d.status === 'DECLINED'), alerts = BL.declined.filter(d => !d.enforced);
  view.innerHTML = `<div class="bar"><h1 style="flex:1">Blocklist <span class="small mut">hard rules → automatic decline</span></h1><button class="primary" onclick="newEntry()">+ New entry</button></div>
  <div class="sub">A case that hits an <b>active</b> entry is declined outright (locked, scoring and AI bypassed). Entries apply to every case immediately and release automatically if they stop matching. ${sup ? 'You are a <b>supervisor</b>: you can activate, edit, delete entries and override declines.' : 'You are an <b>investigator</b>: you can propose entries (saved paused); a supervisor activates them.'}</div>
  ${BL.msg ? `<div class="banner info">${BL.msg}</div>` : ''}
  <div class="grid g4" style="margin-bottom:14px"><div class="card"><div class="l mut small">ACTIVE ENTRIES</div><div class="n" style="font-size:28px;font-weight:700">${active.length}</div></div>
    <div class="card"><div class="l mut small">DECLINED CASES</div><div class="n" style="font-size:28px;font-weight:700;color:var(--crit)">${dec.length}</div></div>
    <div class="card"><div class="l mut small">CLOSED-CASE ALERTS</div><div class="n" style="font-size:28px;font-weight:700">${alerts.length}</div></div>
    <div class="card"><div class="l mut small">SUPERVISOR OVERRIDES</div><div class="n" style="font-size:28px;font-weight:700">${BL.entries.reduce((a, e) => a + e.overridden, 0)}</div></div></div>
  <div class="two"><div class="stack"><div class="card" style="padding:0;overflow:auto"><table><thead><tr><th>Entry</th><th>Status</th><th class="num">Hits now</th><th class="num">Overridden</th><th>Hit outcomes</th></tr></thead><tbody>
    ${BL.entries.map(e => `<tr class="row ${BL.sel === e.id && !BL.isNew ? 'sel' : ''}" onclick="selectEntry('${e.id}')"><td><span class="rid">${e.id}</span> <b>${esc(e.name)}</b> <span class="chip st">${esc(KIND_LABEL[e.kind])}</span><div class="small mut">${esc(e.summary)}</div></td>
      <td onclick="event.stopPropagation()"><span class="chip ${e.status === 'active' ? 'CRITICAL' : 'MEDIUM'} tog" title="${sup ? 'click to ' + (e.status === 'active' ? 'pause' : 'activate') : 'supervisor only'}" onclick="toggleEntry('${e.id}','${e.status}')">${e.status === 'active' ? 'active' : (sup ? 'paused' : 'proposed')}</span></td>
      <td class="num">${e.hits_now}</td><td class="num" title="supervisors who overturned a decline from this entry (false-positive signal)">${e.overridden}${e.ever_declined ? ` <span class="small mut">/ ${e.ever_declined}</span>` : ''}</td>
      <td class="small">${e.hit_confirmed_fraud ? `🚩 ${e.hit_confirmed_fraud} fraud` : ''} ${e.hit_confirmed_legit ? `✓ ${e.hit_confirmed_legit} legit` : ''}${!e.hit_confirmed_fraud && !e.hit_confirmed_legit ? '<span class="mut">—</span>' : ''}</td></tr>`).join('') || '<tr><td colspan="5" class="mut" style="padding:20px">No entries yet. Create one, e.g. “Home Health Aide claims in a state where 30% of labelled cases are confirmed fraud”.</td></tr>'}</tbody></table></div>
    <div class="card"><h2>Declined cases <span class="small mut">${dec.length} declined · ${alerts.length} closed-case alerts</span></h2>
    ${BL.declined.length ? `<table><tbody>${BL.declined.map(d => `<tr><td><a href="#/case/${d.case_id}"><b>${d.case_id}</b></a><div class="small mut">${esc(d.care_type)} · ${esc(d.state)} · $${Math.round(d.amount).toLocaleString()}</div></td>
      <td class="small">${d.details.map(x => `<div><b>${esc(x.name)}</b>: ${esc(x.detail)}</div>`).join('')}</td>
      <td>${d.enforced ? (d.status === 'DECLINED' ? '<span class="chip st DECLINED">DECLINED</span>' : `<span class="chip st">${esc(d.status)}</span>`) : `<span class="chip MEDIUM" title="already closed; not re-opened">alert · ${esc(d.status)}</span>`}</td>
      <td>${d.enforced && d.status === 'DECLINED' ? `<button class="sm" ${sup ? '' : 'disabled title="supervisor only"'} onclick="overrideModal('${d.case_id}')">Override</button>` : ''}</td></tr>`).join('')}</tbody></table>` : '<div class="mut small">Nothing declined.</div>'}</div></div>
  <div id="bdetail"></div></div>`;
  drawEntryDetail();
}
function selectEntry(id) {
  const e = BL.entries.find(x => x.id === id); if (!e) return;
  BL.sel = id; BL.isNew = false; BL.pv = null;
  BL.draft = { name: e.name, kind: e.kind, reason: e.reason, status: e.status, spec: JSON.parse(JSON.stringify(e.spec)) };
  if (['claim_number', 'provider_id', 'member_id'].includes(e.kind)) BL.text = e.spec.values.join('\n');
  drawBlocklist(); previewEntry();
}
function newEntry() { BL.sel = null; BL.isNew = true; BL.pv = null; BL.text = ''; BL.draft = { name: '', kind: 'fraud_history', reason: '', status: 'paused', spec: defaultSpec('fraud_history') }; drawBlocklist(); previewEntry(); }
function blSpec() {
  const d = BL.draft, s = d.spec;
  if (['claim_number', 'provider_id', 'member_id'].includes(d.kind)) return { values: BL.text.split(/[\n,]+/).map(x => x.trim()).filter(Boolean) };
  if (d.kind === 'segment') return Object.fromEntries(Object.entries(s).filter(([, v]) => v));
  return s;
}
function drawEntryDetail() {
  const el = $('#bdetail'); if (!el) return; if (!BL.isNew && !BL.sel) { el.innerHTML = ''; return; }
  const d = BL.draft, sup = who().role === 'supervisor', s = d.spec, kinds = Object.keys(KIND_LABEL);
  let spec = '';
  if (['claim_number', 'provider_id', 'member_id'].includes(d.kind)) spec = `<label class="small mut">One per line (case-insensitive)</label><textarea id="b_vals" rows="4" placeholder="${d.kind === 'claim_number' ? 'LTC-2034786' : d.kind === 'provider_id' ? 'PRV-00123' : 'MEM-00456'}">${esc(BL.text)}</textarea>${d.kind !== 'claim_number' ? '<div class="small mut" id="b_idhint"></div>' : ''}`;
  else if (d.kind === 'segment') spec = `<div class="filters"><label>State<select id="b_state"><option value="">any</option>${META.states.map(x => `<option ${s.state === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label><label>Care type<select id="b_ct"><option value="">any</option>${META.care_types.map(x => `<option ${s.care_type === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label></div>`;
  else if (d.kind === 'condition') spec = `<div class="small mut">Declines every case matching these conditions (same builder as the Rules tab).</div>${groupHtml(s.logic, [])}`;
  else spec = `<div class="filters"><label style="flex:1">Metric<select id="b_metric">${Object.entries(METRIC_LABEL).map(([k, v]) => `<option value="${k}" ${s.metric === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label>${s.metric === 'segment_fraud_rate' ? 'Rate ≥ (0-1)' : 'Count ≥'}<input type="number" step="any" id="b_thr" value="${s.threshold}"></label>
      ${s.metric === 'segment_fraud_rate' ? `<label>Min labelled cases<input type="number" id="b_min" value="${s.min_cases}"></label>` : ''}</div>
    ${s.metric && s.metric.startsWith('segment') ? `<div class="small">Segment = same ${['state', 'care_type'].map(x => `<label><input type="checkbox" class="b_dim" value="${x}" ${(s.dims || []).includes(x) ? 'checked' : ''}> ${x.replace('_', ' ')}</label>`).join(' ')}</div>` : ''}
    <div class="small mut" style="margin-top:6px">Uses <b>human-confirmed outcomes</b> ("Mark as FRAUD" on a case). A case is never blocked by its own label.</div>`;
  el.innerHTML = `<div class="card" style="margin-top:14px"><h2>${BL.isNew ? 'New blocklist entry' : `<span class="rid">${esc(BL.sel)}</span> ${esc(d.name)}`}</h2>
    <div class="filters"><label style="flex:1">Name<input id="b_name" value="${esc(d.name)}" style="width:100%"></label>
      <label>Type<select id="b_kind" ${BL.isNew ? '' : 'disabled'}>${kinds.map(k => `<option value="${k}" ${d.kind === k ? 'selected' : ''}>${KIND_LABEL[k]}</option>`).join('')}</select></label></div>
    <label class="small mut">Decline reason (shown on every declined case)</label><input id="b_reason" value="${esc(d.reason)}" style="width:100%;margin-bottom:8px" placeholder="e.g. Provider terminated for fraud; all claims declined pending review">
    <h3>Match</h3>${spec}
    <div class="bar"><button class="danger" id="b_save">${sup ? (d.status === 'active' || BL.isNew ? 'Save' + (BL.isNew ? ' & activate' : '') : 'Save') : 'Save proposal (paused)'}</button>
      ${sup && !BL.isNew ? `<button id="b_toggle">${d.status === 'active' ? 'Pause' : 'Activate'}</button><button id="b_del">Delete</button>` : ''}${!BL.isNew ? '<button id="b_hist">History</button>' : ''}</div>
    ${!sup ? '<div class="small mut">As an investigator your entry is saved paused. A supervisor reviews the preview below and activates it.</div>' : ''}
    <div id="bpv" class="small mut"></div></div>`;
  bindEntry(); drawPreview();
}
function bindEntry() {
  const sync = () => { const d = BL.draft; d.name = $('#b_name').value; d.reason = $('#b_reason').value; };
  ['b_name', 'b_reason'].forEach(i => $('#' + i).oninput = sync);
  $('#b_kind').onchange = e => { sync(); BL.draft.kind = e.target.value; BL.draft.spec = defaultSpec(e.target.value); BL.text = ''; if (BL.draft.kind === 'condition') RS.draft = BL.draft.spec; drawEntryDetail(); previewEntry(true); };
  if ($('#b_vals')) $('#b_vals').oninput = e => { BL.text = e.target.value; previewEntry(true); };
  if ($('#b_state')) { $('#b_state').onchange = e => { BL.draft.spec.state = e.target.value; previewEntry(true); }; $('#b_ct').onchange = e => { BL.draft.spec.care_type = e.target.value; previewEntry(true); }; }
  if ($('#b_metric')) $('#b_metric').onchange = e => { const s = BL.draft.spec; s.metric = e.target.value; if (e.target.value === 'segment_fraud_rate') { s.threshold = 0.3; s.min_cases = s.min_cases || 5; } else s.threshold = Math.max(1, Math.round(s.threshold) || 1); if (e.target.value.startsWith('segment')) s.dims = s.dims || ['state', 'care_type']; drawEntryDetail(); previewEntry(true); };
  if ($('#b_thr')) $('#b_thr').oninput = e => { BL.draft.spec.threshold = +e.target.value; previewEntry(true); };
  if ($('#b_min')) $('#b_min').oninput = e => { BL.draft.spec.min_cases = +e.target.value; previewEntry(true); };
  document.querySelectorAll('.b_dim').forEach(c => c.onchange = () => { BL.draft.spec.dims = [...document.querySelectorAll('.b_dim')].filter(x => x.checked).map(x => x.value); previewEntry(true); });
  $('#b_save').onclick = () => saveEntry(); $('#b_toggle') && ($('#b_toggle').onclick = () => saveEntry(BL.draft.status === 'active' ? 'paused' : 'active'));
  $('#b_del') && ($('#b_del').onclick = deleteEntry); $('#b_hist') && ($('#b_hist').onclick = entryHistory);
  if (BL.draft.kind === 'condition') { RS.draft = BL.draft.spec; EDIT_REDRAW = drawEntryDetail; EDIT_AFTER = () => previewEntry(true); }
}
let BL_T = null;
function previewEntry(debounce) {
  clearTimeout(BL_T);
  const go = async () => {
    const d = BL.draft; if (!d) return;
    try { BL.pv = await api('/blocklist/preview', 'POST', { kind: d.kind, spec: blSpec(), name: d.name || 'draft', reason: d.reason || 'preview' }); BL.pvErr = null; } catch (e) { BL.pv = null; BL.pvErr = e.message; }
    drawPreview();
  };
  debounce ? BL_T = setTimeout(go, 450) : go();
}
function drawPreview() {
  const el = $('#bpv'); if (!el) return; const p = BL.pv;
  if (!p) { el.innerHTML = BL.pvErr ? `<div class="banner warn">${esc(BL.pvErr)}</div>` : ''; return; }
  const hint = $('#b_idhint'); if (hint) hint.innerHTML = `${p.has_ids[BL.draft.kind] || 0} of the cases in this data carry a ${BL.draft.kind}${p.has_ids[BL.draft.kind] ? '' : ' — <b>this dataset has no such column, so this entry cannot match anything yet</b>'}.`;
  const dec = p.cases.filter(c => c.enforceable), closed = p.cases.filter(c => !c.enforceable && !c.already_declined), already = p.cases.filter(c => c.already_declined);
  el.innerHTML = `<h3>Preview — if this entry were active</h3><div class="banner ${p.share > 0.25 ? 'bad' : 'info'}" style="margin:4px 0"><b>${dec.length}</b> case(s) would be declined (${Math.round(p.share * 100)}% of the queue)${already.length ? `, ${already.length} already declined` : ''}${closed.length ? `, ${closed.length} closed case(s) would only raise an alert` : ''}.${p.share > 0.25 ? ' <b>That is a lot: saving will ask you to confirm.</b>' : ''}</div>
    ${p.cases.slice(0, 15).map(c => `<div class="small"><a class="rid" href="#/case/${c.case_id}">${c.case_id}</a> ${esc(c.status)} — ${esc(c.detail)}</div>`).join('')}${p.cases.length > 15 ? `<div class="small mut">+${p.cases.length - 15} more</div>` : ''}`;
}
async function saveEntry(status) {
  const d = BL.draft, sup = who().role === 'supervisor';
  const body = { name: d.name, kind: d.kind, spec: blSpec(), reason: d.reason, status: status || (sup ? (BL.isNew ? 'active' : d.status) : 'paused') };
  const send = async (confirm) => BL.isNew ? api('/blocklist', 'POST', { ...body, confirm_broad: confirm }) : api('/blocklist/' + BL.sel, 'PUT', { ...body, confirm_broad: confirm });
  await act(async () => {
    let res; try { res = await send(false); } catch (e) { if (e.message.startsWith('BROAD') && confirm(e.message + '\n\nThis is a hard rule that declines claims. Proceed?')) res = await send(true); else throw e; }
    const b = res.rescore.blocklist; BL.sel = res.entry.id; BL.isNew = false;
    BL.msg = `Saved ${res.entry.id} (${res.entry.status}). ${b.declined.length} case(s) declined, ${b.released.length} released${b.alerts.length ? `, ${b.alerts.length} closed-case alert(s)` : ''}.`;
    await loadMeta(); toast(BL.msg, 6000); await viewBlocklist(); selectEntry(res.entry.id);
  });
}
function toggleEntry(id, status) { if (who().role !== 'supervisor') return toast('Only a supervisor can activate or pause entries'); const e = BL.entries.find(x => x.id === id); BL.sel = id; BL.isNew = false; BL.draft = { name: e.name, kind: e.kind, reason: e.reason, status: e.status, spec: e.spec }; saveEntry(status === 'active' ? 'paused' : 'active'); }
async function deleteEntry() { if (!confirm('Delete this entry? Cases it declined are released.')) return; await act(async () => { const res = await api('/blocklist/' + BL.sel, 'DELETE'); BL.sel = null; BL.msg = `Entry deleted. ${res.rescore.blocklist.released.length} case(s) released.`; await loadMeta(); await viewBlocklist(); }); }
async function entryHistory() { const h = await api(`/blocklist/${BL.sel}/history`); modal(`<h2>${BL.sel} — history</h2><table><tbody>${h.map(x => `<tr><td>v${x.version}</td><td>${esc(x.note)}</td><td class="small">${esc(x.changed_by)}</td><td class="small mut">${esc(x.changed_at.replace('T', ' ').slice(0, 16))}</td></tr>`).join('')}</tbody></table><div class="bar"><button onclick="closeModal()">Close</button></div>`); }

(async () => { await loadMeta(); route(); })();
