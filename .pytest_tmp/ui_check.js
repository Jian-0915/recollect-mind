
/* === 管理员账号：登录 / 首次初始化 / 改账号 === */
let AUTH = { initialized: true, logged_in: false, username: '' };
let authMode = 'login';

function authEl(id){ return document.getElementById(id); }

function showAuth(title, hint, btn, mode){
  authMode = mode;
  const ov = authEl('authOverlay'); if(!ov) return;
  ov.classList.remove('hidden');
  authEl('authTitle').textContent = title || 'NeuroMem';
  authEl('authSub').textContent = hint || '';
  authEl('authBtn').textContent = btn || '登录';
  authEl('authPass2Wrap').classList.toggle('hidden', mode !== 'setup');
  authEl('authUser').value = '';
  authEl('authPass').value = '';
  authEl('authPass2').value = '';
  authEl('authErr').textContent = '';
  setTimeout(()=>{ try{ authEl('authUser').focus(); }catch(e){} }, 100);
}

function hideAuth(){ const ov = authEl('authOverlay'); if(ov) ov.classList.add('hidden'); }

function promptLogin(){ showAuth('NeuroMem', '登录已过期，请重新登录', '登录', 'login'); }

async function checkAuth(){
  try{
    const r = await fetch('/api/auth/me');
    AUTH = await r.json();
  }catch(e){
    AUTH = { initialized: true, logged_in: false, username: '' };
  }
  const su = authEl('sideUser');
  if(AUTH.logged_in){
    hideAuth();
    if(su) su.textContent = AUTH.username;
    return true;
  }
  if(su) su.textContent = '未登录';
  if(!AUTH.initialized) showAuth('NeuroMem', '首次使用：创建管理员账号', '创建并进入', 'setup');
  else showAuth('NeuroMem', '登录记忆管家', '登录', 'login');
  return false;
}

async function submitAuth(e){
  if(e) e.preventDefault();
  const u = authEl('authUser').value.trim();
  const p = authEl('authPass').value;
  if(authMode === 'setup' && p !== authEl('authPass2').value){
    authEl('authErr').textContent = '两次输入的密码不一致';
    return false;
  }
  const path = authMode === 'setup' ? '/api/auth/setup' : '/api/auth/login';
  const btn = authEl('authBtn');
  btn.disabled = true;
  try{
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                 body: JSON.stringify({username:u, password:p})});
    const d = await r.json();
    if(!d.ok){ authEl('authErr').textContent = d.error || '登录失败'; return false; }
    hideAuth();
    AUTH = { initialized:true, logged_in:true, username:d.username };
    if(authEl('sideUser')) authEl('sideUser').textContent = d.username;
    loadOverview();
    switchTab('overview');
    toast('欢迎，' + d.username);
  }catch(err){
    authEl('authErr').textContent = '请求失败：' + err;
  }finally{
    btn.disabled = false;
  }
  return false;
}

async function logout(){
  try{ await fetch('/api/auth/logout', {method:'POST'}); }catch(e){}
  AUTH.logged_in = false;
  if(authEl('view')) authEl('view').innerHTML = '';
  showAuth('NeuroMem', '已退出登录', '登录', 'login');
}

function openAcct(){
  const m = authEl('acctModal'); if(!m) return;
  m.classList.remove('hidden');
  authEl('acUser').value = AUTH.username || '';
  authEl('acOld').value = '';
  authEl('acPass').value = '';
  authEl('acErr').textContent = '';
}

function hideAcct(){ const m = authEl('acctModal'); if(m) m.classList.add('hidden'); }

async function saveAccount(e){
  if(e) e.preventDefault();
  const r = await post('/api/auth/account', {
    old_password: authEl('acOld').value,
    username: authEl('acUser').value.trim(),
    password: authEl('acPass').value,
  });
  if(!r.ok){ authEl('acErr').textContent = r.error || '保存失败'; return false; }
  AUTH.username = r.username;
  if(authEl('sideUser')) authEl('sideUser').textContent = r.username;
  hideAcct();
  toast('账号已更新');
  return false;
}

const $ = s => document.querySelector(s);

async function api(p){
  const r = await fetch(p);
  if(r.status === 401){ promptLogin(); return new Promise(()=>{}); }
  return r.json();
}
async function post(p, body){
  const r = await fetch(p, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
  if(r.status === 401){ promptLogin(); return new Promise(()=>{}); }
  return r.json();
}
async function put(p, body){
  const r = await fetch(p, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
  if(r.status === 401){ promptLogin(); return new Promise(()=>{}); }
  return r.json();
}
async function del(p){
  const r = await fetch(p, {method:'DELETE'});
  if(r.status === 401){ promptLogin(); return new Promise(()=>{}); }
  return r.json();
}
function esc(s){ return (s||'').toString().replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
let toastTimer=null;
function toast(msg){
  const t=$('#toast'); t.textContent=msg; t.style.display='block';
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>t.style.display='none', 2200);
}

const PAGE_META = {
  overview: ['概览', '发育状态与近期动态'],
  events: ['记忆', '写入、编辑、删除与检索'],
  concepts: ['认知', '概念层级与自我叙事'],
  personas: ['人物', '画像、别名与相关记忆'],
  relations: ['关系', '人与人之间的连接'],
  graph: ['图谱', '事件 · 人物 · 概念'],
  growth: ['成长', '阶段、评分与代谢快照'],
  chat: ['对话', '基于记忆的回答'],
  models: ['模型', '配置与调用用量'],
  persona: ['人设', '陪伴 agent 的人格与说话方式（L0，人工设定）'],
  data: ['数据', '备份、导出与危险操作'],
};

async function loadOverview(){
  const o = await api('/api/overview');
  const score = (o.evaluation || {}).score;
  const stage = o.stage + (o.level >= 0 ? ' · L' + o.level : '');
  const set = (id, v) => { const el = $(id); if(el) el.textContent = v; };
  set('#sideStage', o.stage);
  set('#sideScore', score != null ? score : '—');
  set('#sideEvents', o.active_events);
  set('#topStage', stage);
  set('#topScore', '评分 ' + (score != null ? score : '—'));
}

// ---------------- 记忆 ----------------
const TAG_OPTIONS = ['旅游','工作','家庭','学习','健康','饮食','宠物','消费','居住','娱乐','情绪','社交'];
let EVENT_FILTER = {q:'', archived:-1, persona:'', tag:''};

function hasFilter(){
  return !!(EVENT_FILTER.q || EVENT_FILTER.archived !== -1 || EVENT_FILTER.persona || EVENT_FILTER.tag);
}

function evQuery(){
  const p = new URLSearchParams();
  if(EVENT_FILTER.q) p.set('q', EVENT_FILTER.q);
  if(EVENT_FILTER.archived !== -1) p.set('archived', String(EVENT_FILTER.archived));
  if(EVENT_FILTER.persona) p.set('persona', EVENT_FILTER.persona);
  if(EVENT_FILTER.tag) p.set('tag', EVENT_FILTER.tag);
  const s = p.toString();
  return s ? '?' + s : '';
}

function eventsToolbar(personas){
  const opts = (personas || []).map(p =>
    `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('');
  return `
  <div class="panel">
    <input id="evQ" placeholder="搜索内容 / 地点…" style="flex:1;min-width:180px"
           onkeydown="if(event.key==='Enter'){applyEventFilter()}">
    <select id="evArch">
      <option value="-1">全部状态</option>
      <option value="0">仅活跃</option>
      <option value="1">仅归档</option>
    </select>
    <select id="evPersona"><option value="">全部人物</option>${opts}</select>
    <select id="evTag"><option value="">全部标签</option>${TAG_OPTIONS.map(t=>`<option>${t}</option>`).join('')}</select>
    <button class="act" onclick="applyEventFilter()">筛选</button>
    <button class="act" onclick="clearEventFilter()">清空</button>
  </div>
  <div class="panel">
    <textarea id="newText" placeholder="输入一条记忆，例如：今天和小明去公园放风筝，他很开心"></textarea>
    <select id="newSpeaker">
      <option value="用户">用户说</option>
      <option value="我">我说</option>
    </select>
    <button class="act primary" onclick="addMemory()">记住</button>
  </div>`;
}

function syncFilterUI(){
  const q = $('#evQ'); if(q) q.value = EVENT_FILTER.q;
  const a = $('#evArch'); if(a) a.value = String(EVENT_FILTER.archived);
  const p = $('#evPersona'); if(p) p.value = EVENT_FILTER.persona;
}

function applyEventFilter(){
  const q = $('#evQ'), a = $('#evArch'), p = $('#evPersona'), t = $('#evTag');
  EVENT_FILTER.q = q ? q.value.trim() : '';
  EVENT_FILTER.archived = a ? parseInt(a.value, 10) : -1;
  EVENT_FILTER.persona = p ? p.value : '';
  EVENT_FILTER.tag = t ? t.value : '';
  switchTab('events');
}

function clearEventFilter(){
  EVENT_FILTER = {q:'', archived:-1, persona:'', tag:''};
  switchTab('events');
}

function emptyGuide(){
  return `<div class="card" style="max-width:780px">
    <div class="v">还是一片空白 —— 先给它第一条记忆</div>
    <div class="hint" style="margin:8px 0 14px">不用写得像档案，一句话就够。它会先“感知”，再慢慢长出概念和人物。</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <textarea id="newText" style="flex:1;min-width:260px;min-height:44px" placeholder="例如：今天和小明去公园放风筝，风很大，他笑得特别开心"></textarea>
      <select id="newSpeaker"><option value="用户">用户说</option><option value="我">我说</option></select>
      <button class="act primary" onclick="addMemory()">记住第一条</button>
    </div>
    <div class="hint" style="margin-top:16px">成长路线</div>
    <ol style="color:#c9cbe0;font-size:13px;line-height:1.9;margin:6px 0 0 18px">
      <li>写第 1 条 → 进入「感知期」</li>
      <li>写够 3 条 → 进入「情节期」，能回忆具体事件</li>
      <li>积累 5 条以上同主题 → 跑一次 <code>python -m neuromem evolve</code>，长出「概念」</li>
    </ol>
  </div>`;
}

function kv(k, v){ return `<tr><th style="width:84px">${k}</th><td>${esc(v)}</td></tr>`; }

function closeDrawer(){
  const d = $('#drawer'); if(d) d.remove();
  const m = $('#drawerMask'); if(m) m.remove();
  document.removeEventListener('keydown', drawerEsc);
}
function drawerEsc(e){ if(e.key === 'Escape') closeDrawer(); }

async function openEvent(id){
  closeDrawer();
  const d = await api('/api/events/' + id);
  if(d.ok === false){ toast('事件不存在'); return; }
  const a = d.affect || {};
  const list = (arr, tpl, empty='无') =>
    (arr && arr.length) ? arr.map(tpl).join('') : `<span class="hint">${empty}</span>`;
  const val = a.valence != null ? ` (${a.valence > 0 ? '+' : ''}${a.valence})` : '';
  document.body.insertAdjacentHTML('beforeend', `
  <div id="drawerMask" class="drawer-mask" onclick="closeDrawer()"></div>
  <div id="drawer" class="drawer">
    <div class="drawer-head">
      <h3>记忆详情</h3>
      <button class="act" onclick="closeDrawer()">关闭</button>
    </div>
    <div style="margin-top:14px;font-size:15px;line-height:1.7">${esc(d.gist)}</div>
    <div class="hint" style="margin-top:6px;font-size:11px">${esc(d.id)}
      ${d.working ? '<span class="tag" style="background:rgba(108,140,255,.14);color:#9FB4FF">工作台</span>' : ''}</div>
    <table style="margin-top:14px"><tbody>
      ${kv('时间', d.occurred_at || d.time_text || '-')}
      ${kv('地点', d.place || '-')}
      ${kv('情绪', (a.label || '-') + val)}
      ${kv('显著度', Number(d.salience || 0).toFixed(2))}
      ${kv('意外度', d.surprise == null ? '—' : Number(d.surprise).toFixed(2))}
      ${kv('激活度', Number(d.activation || 0).toFixed(2) + (d.archived ? ' · 已归档' : ''))}
      ${kv('置信度', d.confidence != null ? Number(d.confidence).toFixed(2) : '-')}
      ${kv('来源', d.provenance || '-')}
      ${kv('上次召回', d.last_recalled_at || '-')}
    </tbody></table>

    <div class="hint" style="margin-top:18px">涉及人物</div>
    <div style="margin-top:6px">${list(d.personas, p => `<span class="pill">${esc(p)}</span>`)}</div>

    <div class="hint" style="margin-top:18px">所属概念</div>
    <div style="margin-top:6px">${list(d.concepts, c => `<span class="pill concept">L${c.abstraction_level} ${esc(c.text)}</span>`)}</div>

    <div class="hint" style="margin-top:18px">语义邻居</div>
    <div style="margin-top:6px">${list(d.neighbors, n => `<div class="hit">${esc(n.gist || n.id)} <span class="hint">${Number(n.weight || 0).toFixed(2)}</span></div>`)}</div>

    <div class="hint" style="margin-top:18px">原文碎片</div>
    <div style="margin-top:6px">${list(d.fragments, f => `<div class="hit">${esc(f.text)}</div>`)}</div>

    <div style="margin-top:22px;display:flex;gap:8px">
      <button class="act" onclick="closeDrawer();editMemory('${d.id}')">编辑</button>
      <button class="act" onclick="closeDrawer();toggleArchive('${d.id}', ${d.archived ? 0 : 1})">${d.archived ? '恢复' : '归档'}</button>
      <button class="act danger" onclick="closeDrawer();deleteMemory('${d.id}')">删除</button>
    </div>
  </div>`);
  document.addEventListener('keydown', drawerEsc);
}

function renderEvents(rows, personas){
  if(!rows.length && !hasFilter()) return emptyGuide();
  const trs = rows.map(r=>{
    const a = r.affect||{};
    const st = r.archived ? '<span class="tag">已归档</span>' : '';
    const tags = (r.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ');
    return `<tr style="cursor:pointer" onclick="openEvent('${r.id}')">
      <td>${esc(r.gist)} ${st} ${tags}</td>
      <td>${esc(r.occurred_at||r.time_text||'-')}</td>
      <td>${esc(r.place||'-')}</td>
      <td>${esc(a.label||'-')} ${a.valence!=null?`(${a.valence>0?'+':''}${a.valence})`:''}</td>
      <td>${(r.salience||0).toFixed(2)}</td>
      <td>${(r.activation||0).toFixed(2)}</td>
      <td>
        <button class="act" onclick="event.stopPropagation();editMemory('${r.id}')">编辑</button>
        <button class="act" onclick="event.stopPropagation();toggleArchive('${r.id}', ${r.archived?0:1})">${r.archived?'恢复':'归档'}</button>
        <button class="act danger" onclick="event.stopPropagation();deleteMemory('${r.id}')">删除</button>
      </td>
    </tr>`;
  }).join('');
  const table = rows.length
    ? `<table><thead><tr><th>事件</th><th>时间</th><th>地点</th><th>情绪</th><th>显著</th><th>激活</th><th>操作</th></tr></thead><tbody>${trs}</tbody></table>`
    : `<div class="empty">没有匹配的记忆 —— 换个关键词，或点「清空」重置筛选</div>`;
  return eventsToolbar(personas) + table;
}

async function addMemory(){
  const text = $('#newText').value.trim();
  if(!text) return toast('请输入记忆内容');
  const speaker = $('#newSpeaker').value;
  toast('抽取中…');
  const r = await post('/api/events', {text, speaker});
  if(r.error){ toast('写入失败'); $('#view').insertAdjacentHTML('afterbegin',
    `<div class="panel" style="border-color:#4a2630;color:#ffb3b3">${esc(r.error)}</div>`); return; }
  if(r.ids && r.ids.length){ toast(`已记住，生成 ${r.ids.length} 条事件`); }
  else { toast('未抽取到有效事件'); }
  await loadOverview();
  switchTab('events');
}

async function editMemory(id){
  const rows = await api('/api/events');
  const row = rows.find(x=>x.id===id);
  const gist = prompt('修改事件内容：', row ? row.gist : '');
  if(gist===null) return;
  const place = prompt('地点（留空不变）：', row ? (row.place||'') : '');
  const body = {gist: gist.trim()};
  if(place!==null && place.trim()!==(row&&row.place||'')) body.place = place.trim();
  const r = await put('/api/events/'+id, body);
  toast('已保存');
  if(r && r.warnings && r.warnings.length){ setTimeout(() => toast(r.warnings[0]), 900); }
  switchTab('events');
}

async function toggleArchive(id, v){
  await post(`/api/events/${id}/archive?archived=${v}`);
  toast(v?'已归档':'已恢复');
  await loadOverview(); switchTab('events');
}

async function deleteMemory(id){
  if(!confirm('确定删除这条记忆？不可撤销。')) return;
  await del('/api/events/'+id);
  toast('已删除');
  await loadOverview(); switchTab('events');
}

// ---------------- 概览 ----------------
let emoChart = null;

function stageBar(stages, cur){
  return `<div class="stagebar">${(stages || []).map(s=>{
    const cls = s.name === cur ? 'cur' : (s.satisfied ? 'done' : '');
    const note = s.note ? ' · ' + s.note : '';
    return `<div class="stage ${cls}">
      <div class="sn">${esc(s.name)}</div>
      <div class="sv">${s.have}/${s.need} ${esc(s.unit)}${esc(note)}</div>
    </div>`;
  }).join('')}</div>`;
}

function kpiCard(k, v, extra){
  return `<div class="card"><div class="k">${k}</div><div class="v">${v}</div>
    ${extra ? `<div class="hint">${extra}</div>` : ''}</div>`;
}

async function renderOverview(){
  if(emoChart){ emoChart.dispose(); emoChart = null; }

  const [o, mt, latest, health, st, gl] = await Promise.all([
    api('/api/overview'), api('/api/meta'),
    api('/api/events?limit=5'), api('/api/health'), api('/api/evolve/status'),
    api('/api/goals')
  ]);
  const ev = o.evaluation || {};
  const glActive = (gl && gl.active) || [];
  const goalsHtml = glActive.length
    ? `<div class="panel" style="display:block">
        <div class="hint" style="margin-bottom:8px">进行中的目标</div>
        ${glActive.map(g=>`<div class="hit" style="display:flex;justify-content:space-between;align-items:center;gap:12px">
          <span>${esc(g.text)}</span>
          <button class="act" onclick="finishGoal('${g.id}')">完成</button></div>`).join('')}
      </div>`
    : '';
  const dims = ev.dimensions || {};
  const emo = o.emotion || {};
  const DIM_ORDER = ['记忆广度', '抽象能力', '社会关系', '活跃度'];
  const dimBars = DIM_ORDER.map(k => {
    const val = Math.round((dims[k] || 0) * 100);
    return `<div class="meter"><div class="meter-top"><span>${k}</span><b>${val}</b></div>
      <div class="bar"><i style="width:${Math.min(100, Math.max(0, val))}%"></i></div></div>`;
  }).join('');

  const recent = latest.length
    ? latest.map(r=>`<div class="hit" style="cursor:pointer" onclick="openEvent('${r.id}')">
        ${esc(r.gist)} <span class="hint">${esc(r.occurred_at || r.time_text || '')}</span></div>`).join('')
    : `<div class="hint">还没有记忆 —— 去「记忆」页写第一条</div>`;

  $('#view').innerHTML = `
  <div class="kpi">
    ${kpiCard('活跃记忆', o.active_events, o.archived_events ? '归档 ' + o.archived_events : '暂无归档')}
    ${kpiCard('概念', o.concepts, '最高 L' + ((ev.metrics || {}).max_level || 0))}
    ${kpiCard('人物 / 关系', o.personas + ' / ' + o.relations, '合并后计数')}
    ${kpiCard('成长评分', ev.score != null ? ev.score : '-', esc(o.stage))}
  </div>

  ${goalsHtml}

  <div class="panel" style="justify-content:space-between">
    <span class="hint">${evolveText(st)}</span>
    <button class="act" onclick="evolveNow()">进化一次</button>
  </div>

  <div class="ov-grid">
    <div class="card" style="grid-column:1/-1">
      <div class="k">发育阶段</div>
      <div class="v" style="font-size:16px">${esc(o.stage)}（L${o.level}）</div>
      ${stageBar(mt.stages, o.stage)}
    </div>

    <div class="card">
      <div class="k">能力维度</div>
      ${dimBars}
    </div>

    <div class="card">
      <div class="k">情绪状态（效价 × 唤醒）</div>
      <div id="emoChart" style="height:260px"></div>
    </div>

    <div class="card">
      <div class="k">最近记忆</div>
      <div style="margin-top:8px">${recent}</div>
      <button class="act" style="margin-top:12px" onclick="switchTab('events')">查看全部</button>
    </div>

    <div class="card">
      <div class="k">系统</div>
      <div style="margin-top:8px;font-size:12px;line-height:1.95;color:var(--fg-2);word-break:break-word">
        <div>对话：${esc(health.provider)} · 抽取 ${esc(health.extract_model)} · 回答 ${esc(health.answer_model)}</div>
        <div>嵌入：${esc(health.embed_provider)} · ${esc(health.embed_model)}（${health.embed_dim} 维）</div>
        <div>数据库：${Math.round((health.db_size || 0) / 1024)} KB</div>
        <div>情感：${esc(emo.label || '-')}（${emo.valence != null ? emo.valence : 0}, ${emo.arousal != null ? emo.arousal : 0}）</div>
      </div>
    </div>
  </div>`;

  emoChart = echarts.init($('#emoChart'));
  const v = emo.valence || 0, a = emo.arousal || 0;
  emoChart.setOption({
    grid: {left: 54, right: 30, top: 26, bottom: 44},
    xAxis: {min: -1, max: 1, name: '效价', nameLocation: 'middle', nameGap: 26,
            nameTextStyle: {color: '#6E768D', fontSize: 11},
            axisLabel: {color: '#6E768D', fontSize: 11},
            axisLine: {lineStyle: {color: '#262D3C'}},
            splitLine: {lineStyle: {color: '#1E2331'}}},
    yAxis: {min: 0, max: 1, name: '唤醒', nameLocation: 'middle', nameGap: 34,
            nameTextStyle: {color: '#6E768D', fontSize: 11},
            axisLabel: {color: '#6E768D', fontSize: 11},
            axisLine: {lineStyle: {color: '#262D3C'}},
            splitLine: {lineStyle: {color: '#1E2331'}}},
    series: [{
      type: 'scatter', symbolSize: 18,
      data: [{value: [v, a]}],
      itemStyle: {color: '#6C8CFF', shadowBlur: 12, shadowColor: 'rgba(108,140,255,.45)'},
      label: {show: true, position: 'right', offset: [8, 0],
              formatter: () => emo.label || '当前', color: '#E8EBF2', fontSize: 12},
      markLine: {silent: true, symbol: 'none',
        lineStyle: {color: '#262D3C', type: 'dashed', width: 1},
        data: [{xAxis: 0}, {yAxis: 0.5}]}
    }]
  });
}

function evolveText(s){
  if(!s) return '';
  if(!s.enabled) return '自动进化已关闭（NEUROMEM_AUTOEVOLVE=0）';
  const last = s.last_run ? s.last_run.slice(0, 16).replace('T', ' ') : '尚未代谢';
  const mins = Math.round((s.interval || 600) / 60);
  const pending = `待处理 ${s.events_since} 条（阈值 ${s.threshold}）`;
  const sur = (s.surprise_since == null) ? '' : ` · 意外累积 ${s.surprise_since}/${s.surprise_trigger}`;
  return `自动进化 · 每 ${mins} 分钟 · 上次 ${last} · ${pending}${sur}${s.running ? ' · 进行中…' : ''}`;
}

async function finishGoal(id){
  const r = await post('/api/goals/' + id + '/done', {});
  toast(r.ok ? '目标已完成' : (r.error || '操作失败'));
  if(r.ok) renderOverview();
}

async function evolveNow(){
  toast('进化中…（可能需要十几秒）');
  const r = await post('/api/evolve', {});
  if(r.ok && r.skipped){ toast(r.reason || '事件不足，已跳过'); }
  else if(r.ok){ toast(`新概念 ${r.new_concepts} · 升层 ${r.abstracted} · 归档 ${r.archived}${r.narrative ? ' · 自传已更新' : ''}`); }
  else { toast(r.error || '进化失败'); }
  await loadOverview();
  renderOverview();
}

async function rebuildSelf(){
  toast('生成中…（约 5~20 秒）');
  const r = await post('/api/self/rebuild', {});
  if(r.ok && r.text){ toast('自我叙事已更新'); renderConceptsTab(); }
  else { toast(r.error || '生成失败'); }
}

// ---------------- 概念 ----------------
async function renderConceptsTab(){
  const [rows, self] = await Promise.all([api('/api/concepts'), api('/api/self')]);
  const selfHtml = `<div class="card" style="margin-bottom:14px">
    <div class="k">自我叙事</div>
    <div style="margin-top:8px;color:#c9cbe0;line-height:1.8">${
      self.text ? esc(self.text) : '<span class="hint">还没有自述 —— 经历足够多后会自动形成</span>'}</div>
    ${self.updated_at ? `<div class="hint" style="margin-top:6px">更新于 ${esc(self.updated_at)}</div>` : ''}
    <div style="margin-top:10px"><button class="act" onclick="rebuildSelf()">重新生成</button></div>
  </div>`;
  if(!rows.length){
    $('#view').innerHTML = selfHtml +
      '<div class="empty">暂无概念 —— 写够 5 条同主题经历后跑一次 evolve 就会归纳出来</div>';
    return;
  }
  const groups = {};
  rows.forEach(r => {
    const k = r.abstraction_level;
    if(!groups[k]) groups[k] = [];
    groups[k].push(r);
  });
  const levels = Object.keys(groups).map(Number).sort((a, b) => b - a);
  const body = levels.map(lv => `
    <div class="hint" style="margin:14px 0 6px">L${lv} · ${groups[lv].length} 个</div>
    ${groups[lv].map(r => `
      <div class="card" style="margin-bottom:10px;cursor:pointer" onclick="openConcept('${r.id}')">
        <div style="font-size:14px;line-height:1.7">${esc(r.text)}</div>
        <div class="hint" style="margin-top:6px">来源 ${r.n} 条经历 · 置信度 ${(r.confidence || 0).toFixed(2)}</div>
      </div>`).join('')}
  `).join('');
  $('#view').innerHTML = selfHtml + body;
}

async function openConcept(id){
  closeDrawer();
  const d = await api('/api/concepts/' + id);
  if(d.ok === false){ toast('概念不存在'); return; }
  const evs = (d.events || []).map(e => `
    <div class="hit" style="cursor:pointer" onclick="openEvent('${e.id}')">
      ${esc(e.gist)} <span class="hint">${esc(e.occurred_at || e.time_text || '')}</span>
    </div>`).join('') || '<span class="hint">无来源事件</span>';
  document.body.insertAdjacentHTML('beforeend', `
  <div id="drawerMask" class="drawer-mask" onclick="closeDrawer()"></div>
  <div id="drawer" class="drawer">
    <div class="drawer-head">
      <h3>概念详情</h3>
      <button class="act" onclick="closeDrawer()">关闭</button>
    </div>
    <div style="margin-top:14px;font-size:15px;line-height:1.8">${esc(d.text)}</div>
    <table style="margin-top:12px"><tbody>
      ${kv('层级', 'L' + d.abstraction_level)}
      ${kv('置信度', (d.confidence || 0).toFixed(2))}
      ${kv('来源经历', (d.events || []).length + ' 条')}
    </tbody></table>
    <div class="hint" style="margin-top:16px">来源事件（点击查看）</div>
    <div style="margin-top:6px">${evs}</div>
    <div style="margin-top:22px;display:flex;gap:8px">
      <button class="act" onclick="mergeConcept('${d.id}')">合并到…</button>
      <button class="act danger" onclick="deleteConcept('${d.id}')">删除概念</button>
    </div>
  </div>`);
  document.addEventListener('keydown', drawerEsc);
}

// ---------------- 人物 ----------------
function renderPersonas(rows){
  if(!rows.length) return '<div class="empty">暂无人物</div>';
  const opts = rows.map(p=>`<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('');
  const panel = `<div class="panel">
    <label>保留</label><select id="mergeKeep">${opts}</select>
    <label>并入（将被合并）</label><select id="mergeDrop">${opts}</select>
    <button class="act primary" onclick="doMerge()">合并人物</button>
    <span style="color:#8b90a8;font-size:12px">把重复的别名人物合并到主人物上，记忆与关系会转移</span>
  </div>`;
  return panel + rows.map(p=>`<div class="card" style="margin-bottom:12px;cursor:pointer" onclick="openPersona('${p.id}')">
    <div class="v">${esc(p.name)}</div>
    <div class="k">别名：${(p.aliases||[]).map(esc).join('、')||'-'}</div>
    <div style="margin-top:8px;color:#c9cbe0">${esc(p.profile)||'（暂无画像，点开可生成）'}</div>
  </div>`).join('');
}

async function openPersona(id){
  closeDrawer();
  const d = await api('/api/personas/' + id);
  if(d.ok === false){ toast('人物不存在'); return; }
  const rels = (d.relations || []).map(r =>
    `<div class="hit">${esc(r.other)} <span class="tag">${esc(r.type)}</span>
      <span class="hint">${(r.strength || 0).toFixed(2)}</span></div>`).join('')
    || '<span class="hint">无</span>';
  const evs = (d.events || []).map(e => `
    <div class="hit" style="cursor:pointer" onclick="openEvent('${e.id}')">
      ${esc(e.gist)}
      <span class="hint">${esc(e.occurred_at || e.time_text || '')}</span>
      ${(e.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ')}
      ${e.affect && e.affect.label ? `<span class="tag">${esc(e.affect.label)}</span>` : ''}
    </div>`).join('') || '<span class="hint">还没有与该人物相关的记忆</span>';
  const aliasHtml = (d.aliases || []).map(a => `<span class="pill">${esc(a)}</span>`).join('')
    || '<span class="hint">无</span>';

  document.body.insertAdjacentHTML('beforeend', `
  <div id="drawerMask" class="drawer-mask" onclick="closeDrawer()"></div>
  <div id="drawer" class="drawer">
    <div class="drawer-head">
      <h3>${esc(d.canonical_name)}</h3>
      <button class="act" onclick="closeDrawer()">关闭</button>
    </div>
    <div class="hint" style="margin-top:4px;font-size:11px">${esc(d.id)}</div>

    <div class="hint" style="margin-top:16px">人物画像</div>
    <div id="personaProfile" style="margin-top:6px;line-height:1.8;color:#c9cbe0">${
      esc(d.profile) || '<span class="hint">暂无画像</span>'}</div>
    <div style="margin-top:8px"><button class="act" onclick="genProfile('${d.id}')">生成 / 重新生成画像</button></div>

    <div class="hint" style="margin-top:18px">别名</div>
    <div style="margin-top:6px">${aliasHtml}
      <button class="act" style="margin-left:6px" onclick="editAliases('${d.id}')">编辑别名</button></div>

    <div class="hint" style="margin-top:18px">关系</div>
    <div style="margin-top:6px">${rels}</div>

    <div class="hint" style="margin-top:18px">相关记忆（${(d.events || []).length}）</div>
    <div style="margin-top:6px">${evs}</div>
    <div style="margin-top:22px">
      <button class="act danger" onclick="deletePersona('${d.id}', ${(d.events || []).length})">删除人物</button>
    </div>
  </div>`);
  document.addEventListener('keydown', drawerEsc);
}

async function genProfile(id){
  const box = $('#personaProfile');
  if(box) box.textContent = '生成中…（约 5~20 秒）';
  const r = await post('/api/personas/' + id + '/profile', {});
  if(r.ok){
    if(box) box.textContent = r.profile;
    toast('画像已更新');
  }else{
    if(box) box.textContent = r.error || '生成失败';
    toast('生成失败');
  }
}

async function editAliases(id){
  const d = await api('/api/personas/' + id);
  const cur = (d.aliases || []).join(', ');
  const v = prompt('别名（逗号分隔，留空表示无别名）：', cur);
  if(v === null) return;
  const aliases = v.split(/[,，、]/).map(s => s.trim()).filter(Boolean);
  const r = await put('/api/personas/' + id + '/aliases', {aliases});
  if(r.ok){ toast('别名已保存'); closeDrawer(); switchTab('personas'); }
  else { toast('保存失败'); }
}

async function doMerge(){
  const keep = $('#mergeKeep').value, drop = $('#mergeDrop').value;
  if(keep===drop) return toast('请选择两个不同的人物');
  if(!confirm(`把「${drop}」合并进「${keep}」？`)) return;
  const r = await post('/api/personas/merge', {keep, drop});
  toast(r.ok ? '合并完成' : '合并失败（人物不存在）');
  await loadOverview(); switchTab('personas');
}

// ---------------- 关系 ----------------
function renderRelations(rows, personas){
  const opts = (personas || []).map(p =>
    `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('')
    || '<option value="">（暂无人物）</option>';
  const panel = `<div class="panel">
    <select id="relA">${opts}</select>
    <input id="relType" placeholder="关系，如 朋友 / 同事" style="width:160px" autocomplete="off">
    <span class="hint">→</span>
    <select id="relB">${opts}</select>
    <input id="relStrength" type="number" min="0" max="1" step="0.1" value="0.5"
           style="width:80px" title="强度 0~1">
    <button class="act primary" onclick="addRelation()">添加关系</button>
    <span class="hint">A —关系→ B · 强度 0~1</span>
  </div>`;
  const trs = rows.map(r => `<tr>
      <td>${esc(r.a)}</td>
      <td><span class="tag">${esc(r.type)}</span></td>
      <td>${esc(r.b)}</td>
      <td>${(r.strength || 0).toFixed(2)}</td>
      <td>
        <button class="act" onclick="editRelation('${r.id}')">编辑</button>
        <button class="act danger" onclick="deleteRelation('${r.id}')">删除</button>
      </td>
    </tr>`).join('');
  const table = rows.length
    ? `<table><thead><tr><th>A</th><th>关系</th><th>B</th><th>强度</th><th>操作</th></tr></thead><tbody>${trs}</tbody></table>`
    : '<div class="empty">还没有关系 —— 在上面选两个人、填个关系词就能建立</div>';
  return panel + table;
}

async function addRelation(){
  const a = v('#relA'), b = v('#relB'), t = v('#relType');
  if(!a || !b || !t) return toast('请选择两个人物并填写关系');
  if(a === b) return toast('不能和自己建立关系');
  const s = parseFloat(v('#relStrength') || '0.5');
  const r = await post('/api/relations',
    {a, b, type: t, strength: isNaN(s) ? 0.5 : Math.max(0, Math.min(1, s))});
  if(r.ok){ toast(r.updated ? '关系已存在，强度已更新' : '关系已添加'); switchTab('relations'); }
  else { toast(r.error || '添加失败'); }
}

async function editRelation(id){
  const rows = await api('/api/relations');
  const r = rows.find(x => x.id === id);
  if(!r) return;
  const t = prompt('关系类型：', r.type);
  if(t === null) return;
  const sv = prompt('强度（0~1）：', String(r.strength != null ? r.strength : 0.5));
  if(sv === null) return;
  const body = {};
  if(t.trim()) body.type = t.trim();
  const sn = parseFloat(sv);
  if(!isNaN(sn)) body.strength = Math.max(0, Math.min(1, sn));
  const res = await put('/api/relations/' + id, body);
  toast(res.ok ? '已保存' : (res.error || '保存失败'));
  if(res.ok) switchTab('relations');
}

async function deleteRelation(id){
  if(!confirm('删除这条关系？')) return;
  const r = await del('/api/relations/' + id);
  toast(r.ok ? '已删除' : '删除失败');
  if(r.ok) switchTab('relations');
}

async function deleteConcept(id){
  if(!confirm('删除这个概念？来源事件会保留。')) return;
  const r = await del('/api/concepts/' + id);
  if(r.ok){ toast('已删除'); closeDrawer(); switchTab('concepts'); }
  else { toast(r.error || '删除失败'); }
}

async function mergeConcept(id){
  const rows = await api('/api/concepts');
  const others = (rows || []).filter(x => x.id !== id);
  if(!others.length){ toast('没有可合并的目标概念'); return; }
  const list = others.slice(0, 12).map((x,i)=>`${i+1}. ${(x.text||'').slice(0,18)}`).join('\n');
  const pick = prompt('合并到哪个概念？输入序号：\n' + list);
  const idx = parseInt(pick, 10) - 1;
  if(!(idx >= 0 && idx < others.length)) return;
  const target = others[idx];
  if(!confirm(`把当前概念合并到「${target.text}」？合并后当前概念会被删除，来源事件保留。`)) return;
  const r = await post('/api/concepts/merge', {keep: target.id, drop: id});
  toast(r.ok ? `已合并，共 ${r.events} 条来源` : (r.error || '合并失败'));
  if(r.ok){ closeDrawer(); switchTab('concepts'); }
}

async function deletePersona(id, n){
  let force = 0;
  if(n > 0){
    if(!confirm(`该人物关联 ${n} 条记忆，删除会解除绑定（记忆本身保留）。继续？`)) return;
    force = 1;
  }else{
    if(!confirm('删除这个人物？')) return;
  }
  const r = await del(`/api/personas/${id}?force=${force}`);
  if(r.ok){ toast('已删除'); closeDrawer(); switchTab('personas'); }
  else { toast(r.error || '删除失败'); }
}

let graphChart=null, growthChart=null;

async function renderGraph(){
  $('#view').innerHTML = '<div class="chart" id="graphChart"></div>';
  const data = await api('/api/graph');
  graphChart = echarts.init($('#graphChart'));
  const cats = [{name:'事件'},{name:'人物'},{name:'概念'}];
  const map = {event:0, persona:1, concept:2};
  graphChart.setOption({
    tooltip:{},
    legend:[{data:cats.map(c=>c.name)}],
    series:[{
      type:'graph', layout:'force', roam:true, draggable:true,
      force:{repulsion:120, edgeLength:80},
      categories:cats,
      label:{show:true,fontSize:10,color:'#cfd3e6'},
      edgeSymbol:['none','arrow'], edgeSymbolSize:6,
      data:data.nodes.map(n=>({id:n.id,name:n.name,category:map[n.category]||0})),
      links:data.links,
      lineStyle:{color:'#3a4060',width:1,curveness:0.1},
      itemStyle:{opacity:0.9}
    }]
  });
}

async function renderGrowth(){
  $('#view').innerHTML = '<div class="chart" id="growthChart"></div>';
  const rows = await api('/api/growth');
  growthChart = echarts.init($('#growthChart'));
  growthChart.setOption({
    tooltip:{trigger:'axis'},
    legend:{data:['事件','概念','最高层']},
    xAxis:{type:'category',data:rows.map((r,i)=>'#'+(i+1)),axisLabel:{color:'#8b90a8'}},
    yAxis:{type:'value',axisLabel:{color:'#8b90a8'}},
    series:[
      {name:'事件',type:'line',smooth:true,data:rows.map(r=>r.events)},
      {name:'概念',type:'line',smooth:true,data:rows.map(r=>r.concepts)},
      {name:'最高层',type:'line',smooth:true,data:rows.map(r=>r.max_level)},
    ]
  });
}

// ---------------- 对话 ----------------
let chatHistory = [];

function renderChat(){
  return `<div class="panel">
      <input id="askInput" style="flex:1;min-width:240px" placeholder="问它：上周我和小明去哪了？最喜欢什么？">
      <input id="askName" style="width:110px" value="用户" title="对话者称呼">
      <button class="act primary" onclick="doAsk()">发送</button>
    </div>
    <div class="chatbox" id="chatbox">
      ${chatHistory.length? chatHistory.map(m=>`<div class="msg ${m.role}">${m.role==='user'?'<b>你：</b>':'<b>NeuroMem：</b>'}${esc(m.text)}${m.hits?m.hits.map(h=>`<div class="hit">${esc(h)}</div>`).join(''):''}</div>`).join('') : '<div class="empty">还没有对话，先问一句试试</div>'}
    </div>`;
}

async function doAsk(){
  const q = ($('#askInput').value||'').trim();
  if(!q) return;
  const name = $('#askName').value||'用户';
  chatHistory.push({role:'user', text:q});
  $('#view').innerHTML = renderChat();
  toast('检索与生成中…');
  const t0 = Date.now();
  let r;
  try { r = await post('/api/ask', {query:q, user_name:name}); }
  catch(e){ r = {summary:'请求失败：'+e}; }
  const hits = (r.events||[]).slice(0,5).map(e=>{
    const g = e.gist||e.text||'';
    const sc = e.score!=null? ` [${Number(e.score).toFixed(2)}]`:'';
    const cf = e.confidence? ` (${e.confidence})`:'';
    return g+sc+cf;
  });
  const warn = (r.warnings || []).join('\n');
  const botText = (r.summary || '（没有找到相关记忆）') + (warn ? '\n\n⚠ ' + warn : '');
  chatHistory.push({role:'bot', text: botText, hits});
  const dt = ((Date.now()-t0)/1000).toFixed(1);
  toast(`完成 · ${dt}s · 命中 ${(r.events||[]).length} 条`);
  $('#view').innerHTML = renderChat();
  const box=$('#chatbox'); if(box) box.scrollTop = box.scrollHeight;
}

// ---------------- 设置（模型配置） ----------------
let SETTINGS = null, SETTINGS_PRESETS = {};

function v(id){ const el = $(id); return el ? el.value.trim() : ''; }

function keyHint(info){
  if(!info || !info.set) return '';
  return `已保存 ${info.head || ''}…${info.tail}（${info.len} 位）· 留空不修改`;
}

async function clearKey(which){
  if(!confirm('清除已保存的 ' + (which === 'llm' ? '对话' : '嵌入') + ' Key？')) return;
  const r = await put('/api/settings', {clear_key: which});
  SETTINGS = r.settings; fillSettings(r.settings);
  $('#setResult').textContent = '已清除。填入新 Key 后点「保存并立即生效」。';
  toast('已清除');
}

function fillSettings(s){
  if(!s) return;
  const L = s.llm || {}, E = s.embed || {};
  const put = (id, val) => { const el = $(id); if(el && val != null) el.value = val; };
  put('#llmProvider', L.provider); put('#llmBase', L.base_url);
  put('#llmExtract', L.extract_model); put('#llmAnswer', L.answer_model);
  put('#embedProvider', E.provider); put('#embedBase', E.base_url);
  put('#embedModel', E.model); put('#embedDim', E.dim);
  // Key 输入框始终留空：留空=不修改，避免把掩码回填后“追加”成无效值
  const lk = $('#llmKey'), ek = $('#embedKey');
  if(lk){ lk.value = ''; lk.placeholder = keyHint(L.api_key_info) || '本地 Ollama 可留空'; }
  if(ek){ ek.value = ''; ek.placeholder = keyHint(E.api_key_info) || '本地 Ollama 可留空'; }
}

async function renderSettings(){
  const d = await api('/api/settings');
  SETTINGS = d.settings; SETTINGS_PRESETS = d.presets || {};
  const btns = Object.keys(SETTINGS_PRESETS).map(k =>
    `<button class="act" onclick="applyPreset('${k}')">${k}</button>`).join('');
  $('#view').innerHTML = `
  <div class="panel"><span class="hint">预设</span>${btns}
    <span class="hint">点一下填表，再点「保存」</span></div>

  <div class="card" style="margin-bottom:12px">
    <div class="v">对话 / 抽取模型</div>
    <form class="grid2" onsubmit="return false" autocomplete="off">
      <label>接口类型
        <select id="llmProvider">
          <option value="ollama">Ollama 本地</option>
          <option value="openai_compat">OpenAI 兼容（云端）</option>
        </select></label>
      <label>Base URL<input id="llmBase" placeholder="https://api.deepseek.com" autocomplete="off"></label>
      <label>API Key<input id="llmKey" type="password" autocomplete="new-password" placeholder="本地 Ollama 可留空"></label>
      <label>抽取模型<input id="llmExtract" placeholder="qwen3.5:4b / deepseek-chat" autocomplete="off"></label>
      <label>回答模型<input id="llmAnswer" placeholder="qwen3:8b / gpt-4o-mini" autocomplete="off"></label>
    </form>
    <div style="margin-top:8px"><button class="act danger" onclick="clearKey('llm')">清除已保存 Key</button></div>
  </div>

  <div class="card" style="margin-bottom:12px">
    <div class="v">嵌入模型（决定检索质量）</div>
    <form class="grid2" onsubmit="return false" autocomplete="off">
      <label>接口类型
        <select id="embedProvider">
          <option value="ollama">Ollama 本地</option>
          <option value="openai_compat">OpenAI 兼容（云端）</option>
        </select></label>
      <label>Base URL<input id="embedBase" autocomplete="off"></label>
      <label>API Key<input id="embedKey" type="password" autocomplete="new-password"></label>
      <label>模型名<input id="embedModel" placeholder="bge-large:latest" autocomplete="off"></label>
      <label>向量维度<input id="embedDim" placeholder="1024" autocomplete="off"></label>
    </form>
    <div style="margin-top:8px"><button class="act danger" onclick="clearKey('embed')">清除已保存 Key</button></div>
    <div class="hint" style="margin-top:8px">切换嵌入模型必须维度一致，否则旧记忆无法被检索到。</div>
  </div>

  <div class="panel">
    <button class="act primary" onclick="saveSettings()">保存并立即生效</button>
    <button class="act" onclick="testModel('llm')">测试对话</button>
    <button class="act" onclick="testModel('embed')">测试嵌入</button>
    <button class="act" onclick="loadModelList()">拉取模型列表</button>
    <span class="hint">测试使用的是「已保存」的配置</span>
  </div>
  <div id="setResult" class="result"></div>`;
  fillSettings(SETTINGS);
  warnKeyShape(SETTINGS);
}

function warnKeyShape(s){
  const li = (s && s.llm && s.llm.api_key_info) || {};
  const base = ((s && s.llm && s.llm.base_url) || '').toLowerCase();
  if(li.set && li.head && li.head !== 'sk-' && (base.includes('deepseek') || base.includes('openai'))){
    $('#setResult').textContent =
      `⚠ 已保存的 Key 以「${li.head}」开头，但 DeepSeek / OpenAI 的 Key 都应以 sk- 开头——` +
      `很可能粘贴时开头被截断了。点「清除已保存 Key」后重新完整粘贴。`;
  }
}

function applyPreset(name){
  const p = SETTINGS_PRESETS[name];
  if(!p){ return; }
  const set = (id, val) => { const el = $(id); if(el && val != null) el.value = val; };
  // LLM / 抽取部分
  set('#llmProvider', p.provider); set('#llmBase', p.base_url);
  set('#llmExtract', p.extract_model); set('#llmAnswer', p.answer_model);
  // 嵌入部分：只有预设显式提供嵌入模型时才覆盖，
  // 否则会把 LLM 的 base_url 串到嵌入上（如 DeepSeek 没有 embeddings 接口）
  let msg = `已填入预设「${name}」，记得点「保存并立即生效」。`;
  if(p.model){
    set('#embedProvider', p.provider); set('#embedBase', p.base_url);
    set('#embedModel', p.model); set('#embedDim', p.dim);
  }else{
    msg += '\n该预设没有嵌入模型，嵌入保持当前配置（本地 Ollama bge-large 即可）。';
  }
  $('#setResult').textContent = msg;
}

function collectSettings(){
  const dim = parseInt(v('#embedDim') || '0', 10);
  const llm = {provider: v('#llmProvider'), base_url: v('#llmBase'),
               extract_model: v('#llmExtract'), answer_model: v('#llmAnswer')};
  const lk = v('#llmKey'); if(lk) llm.api_key = lk;      // 留空=不修改
  const embed = {provider: v('#embedProvider'), base_url: v('#embedBase'),
                 model: v('#embedModel'), dim: dim || undefined};
  const ek = v('#embedKey'); if(ek) embed.api_key = ek;
  return {llm, embed};
}

async function saveSettings(){
  const s = collectSettings();
  const tips = [];
  const base = (s.llm.base_url || '').toLowerCase();
  const freshKey = v('#llmKey');
  if(freshKey && !freshKey.startsWith('sk-') && (base.includes('deepseek') || base.includes('openai'))){
    tips.push(`Key 以「${freshKey.slice(0, 3)}」开头，但该服务的 Key 应以 sk- 开头，可能是粘贴不完整`);
  }
  const r = await put('/api/settings', s);
  if(!r.ok){ $('#setResult').textContent = '保存失败'; toast('保存失败'); return; }
  SETTINGS = r.settings;
  const lines = ['已保存，立即生效。'];
  const li = ((r.settings.llm || {}).api_key_info) || {};
  const ei = ((r.settings.embed || {}).api_key_info) || {};
  lines.push(`对话 Key：${li.set ? li.tail + '（' + li.len + ' 位）' : '未设置'} · 嵌入 Key：${ei.set ? ei.tail + '（' + ei.len + ' 位）' : '未设置'}`);
  for(const w of (r.warnings || [])) lines.push('⚠ ' + w);
  for(const t of tips) lines.push('⚠ ' + t);
  $('#setResult').textContent = lines.join('\n');
  fillSettings(r.settings);
  toast('设置已保存');
}

async function testModel(role){
  $('#setResult').textContent = `正在测试 ${role === 'embed' ? '嵌入' : '对话'}…`;
  const r = await post('/api/settings/test', {role});
  if(r.ok){
    $('#setResult').textContent = r.dim != null
      ? `嵌入连接成功 · ${r.latency}s · 维度 ${r.dim}`
      : `对话连接成功 · ${r.latency}s\n回复：${r.reply || '(空)'}`;
    toast('连接正常');
  }else{
    let msg = `失败（${r.latency}s）：${r.error}`;
    if(r.need_key) msg += '\n\n→ API Key 被服务端判定无效或已过期：请到平台重新生成，填入后点「保存并立即生效」（本地 Ollama 可留空）';
    $('#setResult').textContent = msg;
    toast('连接失败');
  }
}

async function loadModelList(){
  const s = collectSettings();
  const savedKey = !!(SETTINGS && SETTINGS.llm && SETTINGS.llm.api_key_info && SETTINGS.llm.api_key_info.set);
  const isLocal = /^(https?:\/\/)?(127\.0\.0\.1|localhost)/i.test(s.llm.base_url || '');
  if(s.llm.provider !== 'ollama' && !s.llm.api_key && !savedKey && !isLocal){
    $('#setResult').textContent =
      '这个云端地址需要 API Key：先在「API Key」填入密钥，点「保存并立即生效」，再拉取模型列表。\n' +
      '也可以跳过列表，直接手填模型名（如 deepseek-chat / deepseek-reasoner）。';
    return;
  }
  $('#setResult').textContent = '正在拉取模型列表…';
  const q = `?provider=${encodeURIComponent(s.llm.provider)}`
          + `&base_url=${encodeURIComponent(s.llm.base_url)}`
          + `&api_key=${encodeURIComponent(s.llm.api_key || '')}`;
  const r = await fetch('/api/settings/models' + q).then(x => x.json());
  if(!r.ok){
    $('#setResult').textContent = '获取失败：' + (r.error || '') +
      (r.need_key ? '\n\n→ 需要有效 API Key：填入后先「保存」，再重试；也可以直接手填模型名。' : '');
    return;
  }
  $('#setResult').textContent = '可用模型：\n' + (r.models || []).join('\n');
}

// ---------------- 模型（用量 + 配置） ----------------
let modelsChart = null;

function usageSection(u){
  const t = u.totals || {};
  const models = (u.by_model || []).map(m => `<tr>
    <td>${esc(m.model)}</td>
    <td><span class="tag">${esc(m.kind)}</span></td>
    <td>${m.calls}</td>
    <td>${m.failed ? `<span style="color:#ff9b9b">${m.failed}</span>` : 0}</td>
    <td>${m.avg_ms}</td>
    <td>${m.tokens}</td></tr>`).join('') || '<tr><td colspan="6" class="hint">暂无数据</td></tr>';
  const recent = (u.recent || []).map(r => `<tr>
    <td>${esc((r.ts || '').slice(11, 19))}</td>
    <td>${esc(r.role || '')}</td>
    <td>${esc(r.model || '')}</td>
    <td>${r.ms}</td>
    <td>${r.ok ? '<span class="tag">OK</span>'
               : '<span class="tag" style="background:#4a2630;color:#ff9b9b">失败</span>'}</td></tr>`).join('')
    || '<tr><td colspan="5" class="hint">暂无调用</td></tr>';
  return `
  <div class="kpi">
    ${kpiCard('今日调用', t.today_calls || 0, `近 ${u.days} 天 ${t.calls || 0} 次`)}
    ${kpiCard('今日 Token', t.today_tokens || 0, `近 ${u.days} 天 ${t.tokens || 0}`)}
    ${kpiCard('平均延迟', (t.avg_ms || 0) + ' ms')}
    ${kpiCard('失败率', (t.fail_rate || 0) + ' %', `失败 ${t.failed || 0} 次`)}
  </div>
  <div class="ov-grid">
    <div class="card" style="grid-column:1/-1">
      <div class="k">近 ${u.days} 天调用趋势</div>
      <div id="modelsChart" style="height:230px"></div>
    </div>
    <div class="card">
      <div class="k">按模型</div>
      <table style="margin-top:8px"><thead><tr><th>模型</th><th>类型</th><th>调用</th><th>失败</th><th>均延迟</th><th>Token</th></tr></thead>
      <tbody>${models}</tbody></table>
    </div>
    <div class="card">
      <div class="k">最近调用</div>
      <table style="margin-top:8px"><thead><tr><th>时间</th><th>角色</th><th>模型</th><th>ms</th><th>状态</th></tr></thead>
      <tbody>${recent}</tbody></table>
    </div>
  </div>
  <div class="panel">
    <button class="act" onclick="renderModels()">刷新用量</button>
    <button class="act danger" onclick="clearUsage()">清空用量</button>
    <span class="hint">用量来自本系统每次 LLM / 嵌入调用的记录</span>
  </div>
  <div class="panel"><span class="hint">模型配置 · 改完点「保存并立即生效」</span></div>`;
}

function drawUsageChart(u){
  if(modelsChart){ modelsChart.dispose(); modelsChart = null; }
  const el = $('#modelsChart');
  if(!el){ return; }
  modelsChart = echarts.init(el);
  modelsChart.setOption({
    tooltip: {trigger: 'axis'},
    legend: {data: ['调用', 'Token'], textStyle: {color: '#8b90a8'}},
    grid: {left: 48, right: 56, top: 34, bottom: 30},
    xAxis: {type: 'category', data: (u.by_day || []).map(d => d.day),
            axisLabel: {color: '#8b90a8'}},
    yAxis: [{type: 'value', name: '调用', axisLabel: {color: '#8b90a8'},
             splitLine: {lineStyle: {color: '#2c3150'}}},
            {type: 'value', name: 'Token', axisLabel: {color: '#8b90a8'},
             splitLine: {show: false}}],
    series: [
      {name: '调用', type: 'bar', barMaxWidth: 26,
       data: (u.by_day || []).map(d => d.calls), itemStyle: {color: '#5b8cff'}},
      {name: 'Token', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6,
       data: (u.by_day || []).map(d => d.tokens), itemStyle: {color: '#b9a6ff'}}
    ]
  });
}

async function renderModels(){
  await renderSettings();                       // 先渲染配置区
  const u = await api('/api/usage?days=7');
  $('#view').insertAdjacentHTML('afterbegin', usageSection(u));
  drawUsageChart(u);
}

async function clearUsage(){
  if(!confirm('清空全部模型用量记录？')) return;
  await del('/api/usage');
  toast('用量已清空');
  renderModels();
}

// ---------------- 人设（L0，后台统一编辑） ----------------
let PERSONA = null;
const _pv = (id, v) => { const el = $('#' + id); if (el) el.value = v == null ? '' : String(v); };
const _gv = id => { const el = $('#' + id); return el ? el.value.trim() : ''; };
const _ja = v => Array.isArray(v) ? v.join('，') : (v || '');

function fillPersonaForm(c) {
  const sp = c.speech || {}, rel = c.relationship || {};
  const ex = Array.isArray(c.examples) ? c.examples : [];
  _pv('pc_name', c.name === '（未命名）' ? '' : c.name);
  _pv('pc_role', c.role); _pv('pc_traits', _ja(c.traits));
  _pv('pc_style', sp.style); _pv('pc_length', sp.length);
  _pv('pc_address', sp.address); _pv('pc_catch', _ja(sp.catchphrases));
  _pv('pc_avoid', _ja(sp.avoid));
  _pv('pc_call', rel.call_user); _pv('pc_bond', rel.bond); _pv('pc_init', rel.initiative);
  _pv('pc_care', c.care); _pv('pc_mood', c.emotion_baseline);
  _pv('pc_bounds', _ja(c.boundaries));
  for (let i = 1; i <= 2; i++) {
    const e = ex[i - 1] || {};
    _pv('pc_ex' + i + 'u', e.user); _pv('pc_ex' + i + 'r', e.reply);
  }
}

function personaFormCard() {
  const ex = [];
  for (let i = 1; i <= 2; i++) {
    const u = _gv('pc_ex' + i + 'u'), r = _gv('pc_ex' + i + 'r');
    if (u || r) ex.push({user: u, reply: r});
  }
  return {
    name: _gv('pc_name'), role: _gv('pc_role'), traits: _gv('pc_traits'),
    speech: {
      style: _gv('pc_style'), address: _gv('pc_address'), length: _gv('pc_length'),
      catchphrases: _gv('pc_catch'), avoid: _gv('pc_avoid')
    },
    relationship: {
      call_user: _gv('pc_call'), bond: _gv('pc_bond'), initiative: _gv('pc_init')
    },
    care: _gv('pc_care'), boundaries: _gv('pc_bounds'),
    emotion_baseline: _gv('pc_mood'), examples: ex
  };
}

async function renderPersona() {
  const d = await api('/api/persona');
  PERSONA = d;
  const ident = d.identity || {};
  $('#view').innerHTML = `
  <div class="card">
    <div class="k">当前人设 · L0 固定层</div>
    <div class="pkv">
      <div><span>版本</span><b>v${esc(d.version)}</b></div>
      <div><span>校验</span><b style="color:${d.ok ? 'var(--ok)' : 'var(--danger)'}">${esc(d.verify)}</b></div>
      <div><span>状态</span><b>${d.placeholder ? '未设置（用默认人格）' : '已生效'}</b></div>
      <div><span>对方称呼</span><b>${esc(d.call_user || '沿用默认「用户」')}</b></div>
      <div><span>checksum</span><b>${esc(d.checksum)}</b></div>
      <div><span>更新于</span><b>${esc((d.updated_at || '').slice(0, 16))}</b></div>
    </div>
    <div class="hint" style="margin-top:12px">人设与宪法同属 L0：<b>清库不清、导出包含、对话不能改写</b>；保存后下一轮对话立即生效，无需重启。生效通道：陪伴 agent（Brain 与 A/B 对照组读同一份）、<code>/api/ask</code>、MCP 的 <code>memory_ask</code> / <code>memory_state</code>。人设与宪法冲突时以宪法为准；agent 侧只有只读视图，不能自己改人格。</div>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="k">编辑人设（保存为新版本，旧版自动归档）</div>
    <div class="pform" style="margin-top:12px">
      <label class="pfield">名字<input id="pc_name" placeholder="小满"></label>
      <label class="pfield">身份一句话<input id="pc_role" placeholder="一个记性不错、话不多的老朋友"></label>
      <label class="pfield wide">性格（逗号分隔，最多 8 条）<input id="pc_traits" placeholder="慢热，细心，不轻易下结论"></label>

      <div class="psec wide">说话方式</div>
      <label class="pfield wide">风格<textarea id="pc_style" placeholder="短句、口语，少用感叹号；先回应情绪，再说事"></textarea></label>
      <label class="pfield">长度<input id="pc_length" placeholder="1~3 句，说完就停"></label>
      <label class="pfield">怎么称呼对方<input id="pc_address" placeholder="叫对方「你」，不用「您」"></label>
      <label class="pfield">口头禅（逗号分隔）<input id="pc_catch" placeholder="嗯，我记得，后来呢"></label>
      <label class="pfield">避免（逗号分隔）<input id="pc_avoid" placeholder="书面腔，说教，连续追问"></label>

      <div class="psec wide">关系与相处</div>
      <label class="pfield">对方称呼（留空 = 沿用默认）<input id="pc_call" placeholder="你"></label>
      <label class="pfield">关系定位<input id="pc_bond" placeholder="认识很久、彼此不打扰也不生分的朋友"></label>
      <label class="pfield wide">主动性<textarea id="pc_init" placeholder="只在有依据时轻轻接一句没聊完的事"></textarea></label>
      <label class="pfield wide">关心方式<textarea id="pc_care" placeholder="对方难过时先陪着，不急着分析"></textarea></label>
      <label class="pfield">情绪基线<input id="pc_mood" placeholder="平静偏暖，情绪跟着对方走但不被带跑"></label>
      <label class="pfield">绝对不做（逗号分隔）<input id="pc_bounds" placeholder="不假装记得没发生过的事，不替对方做决定"></label>

      <div class="psec wide">示范语气（只学语气，模型不会把这些当成真实经历）</div>
      <label class="pfield">对方说<input id="pc_ex1u" placeholder="今天好累啊"></label>
      <label class="pfield">我回<input id="pc_ex1r" placeholder="嗯，先歇会儿。不着急说。"></label>
      <label class="pfield">对方说<input id="pc_ex2u"></label>
      <label class="pfield">我回<input id="pc_ex2r"></label>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
      <button class="act primary" onclick="savePersona()">保存新版本</button>
      <button class="act" onclick="previewPersona()">预览注入文本</button>
      <button class="act" onclick="fillPersonaExample()">填入示例</button>
      <button class="act" onclick="fillPersonaBlank()">清空为骨架</button>
      <button class="act" onclick="renderPersona()">放弃修改 / 重新读取</button>
    </div>
    <div class="hint" style="margin:12px 0 4px">预览：下面就是 agent 每轮 system 里实际会收到的内容（服务端渲染，不写库）。<b id="personaStatus">—</b></div>
    <pre class="result" id="personaPreview" style="background:var(--bg-2);border:1px solid var(--line-soft);border-radius:9px;padding:12px;max-height:340px;overflow:auto">点击「预览注入文本」查看</pre>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="k">固定身份宪法 · v${esc(d.identity_version)}（校验：${esc(d.identity_verify)}）</div>
    <div class="hint" style="margin:6px 0 10px">这 5 项是不可逾越的底线，优先级高于人设；同样人工版本化、清库豁免、对话不能改写。改完保存，旧版自动归档。</div>
    <div class="pform">
      <label class="pfield wide">我是谁（who）<textarea id="id_who"></textarea></label>
      <label class="pfield wide">我服务谁（serve）<textarea id="id_serve"></textarea></label>
      <label class="pfield wide">称呼与边界（address）<textarea id="id_address"></textarea></label>
      <label class="pfield wide">语气与禁忌（tone）<textarea id="id_tone"></textarea></label>
      <label class="pfield wide">能力边界（limits）<textarea id="id_limits"></textarea></label>
    </div>
    <button class="act" onclick="saveIdentity()">保存宪法新版本</button>
    <span class="hint" id="identityStatus" style="margin-left:8px"></span>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="k">版本历史 · 回滚</div>
    <div id="personaHistory" style="margin-top:10px"><div class="hint">加载中…</div></div>
  </div>`;
  fillPersonaForm(d.card || {});
  fillIdentityForm(ident);
  loadPersonaHistory();
}

function fillIdentityForm(p) {
  ['who', 'serve', 'address', 'tone', 'limits'].forEach(k => _pv('id_' + k, p[k]));
}

async function saveIdentity() {
  const p = {};
  ['who', 'serve', 'address', 'tone', 'limits'].forEach(k => p[k] = _gv('id_' + k));
  const empty = Object.keys(p).filter(k => !p[k]);
  if (empty.length) { toast('宪法字段不能为空：' + empty.join('、')); return; }
  if (!confirm('保存宪法新版本？旧版会归档；人设与宪法冲突时以宪法为准。')) return;
  const r = await put('/api/identity', {
    confirm: 'IDENTITY', actor: 'human', reason: '后台管理修改', profile: p
  });
  if (r.ok) { toast('宪法已更新为 v' + r.version); renderPersona(); }
  else toast(r.error || '保存失败');
}

async function previewPersona() {
  const r = await post('/api/persona/preview', {card: personaFormCard()});
  const el = $('#personaPreview'), st = $('#personaStatus');
  if (!el) return;
  if (r.ok) {
    el.textContent = r.render || '（名字留空时人设不生效，生成层沿用默认人格）';
    st.textContent = '校验通过 · 尚未保存';
    st.style.color = 'var(--warn)';
  } else {
    el.textContent = '校验未通过：\n- ' + (r.errors || []).join('\n- ');
    st.textContent = '校验未通过';
    st.style.color = 'var(--danger)';
  }
  return r;
}

async function savePersona() {
  const card = personaFormCard();
  const pre = await previewPersona();
  if (!pre || !pre.ok) { toast('校验未通过，未保存'); return; }
  if (!confirm('保存为新版本？旧版会先归档到历史，可随时回滚。')) return;
  const r = await put('/api/persona', {
    confirm: 'PERSONA', actor: 'human', reason: '后台管理修改', card: card
  });
  if (r.ok) { toast('已保存为 v' + r.version + '，下一轮对话生效'); renderPersona(); }
  else toast(r.error || '保存失败');
}

async function fillPersonaExample() {
  const r = await api('/api/persona/template?example=true');
  if (r.template) { fillPersonaForm(r.template); toast('已填入示例，改几个字就能保存'); previewPersona(); }
}
async function fillPersonaBlank() {
  const r = await api('/api/persona/template?example=false');
  if (r.template) { fillPersonaForm(r.template); toast('已填入中性骨架'); previewPersona(); }
}

async function loadPersonaHistory() {
  const h = await api('/api/persona/history?limit=10');
  const items = h.items || [];
  const rows = items.map(x =>
    '<tr><td>v' + esc(x.version) + '</td><td>' + esc(x.name || '（未命名）') +
    '</td><td>' + esc((x.updated_at || '').slice(0, 16)) + '</td><td>' + esc(x.updated_by || '') +
    '</td><td>' + esc(x.reason || '') + '</td><td>' + esc(x.checksum || '') +
    '</td><td><button class="act" onclick="rollbackPersona(' + Number(x.version) + ')">恢复此版</button></td></tr>'
  ).join('');
  const el = $('#personaHistory');
  if (!el) return;
  el.innerHTML = rows
    ? '<table><thead><tr><th>版本</th><th>名字</th><th>归档时间</th><th>操作人</th><th>说明</th><th>checksum</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table>'
    : '<div class="hint">还没有历史版本。保存一次后，旧版会先归档到这里。</div>';
}

async function rollbackPersona(v) {
  if (!confirm('把 v' + v + ' 恢复为新的当前版本？当前人设会先归档，历史不会丢。')) return;
  const r = await post('/api/persona/rollback', {
    confirm: 'PERSONA', version: v, actor: 'human', reason: '后台回滚到 v' + v
  });
  if (r.ok) { toast('已恢复为 v' + r.version); renderPersona(); }
  else toast(r.error || '回滚失败');
}

// ---------------- 数据（导出 / 重置） ----------------
async function renderData(){
  const [o, h] = await Promise.all([api('/api/overview'), api('/api/health')]);
  $('#view').innerHTML = `
  <div class="kpi">
    ${kpiCard('事件', o.active_events, o.archived_events ? '归档 ' + o.archived_events : '无归档')}
    ${kpiCard('概念', o.concepts)}
    ${kpiCard('人物 / 关系', o.personas + ' / ' + o.relations)}
    ${kpiCard('数据库', Math.round((h.db_size || 0) / 1024) + ' KB', esc(h.db_path))}
  </div>

  <div class="card" style="margin-top:14px">
    <div class="k">备份</div>
    <div class="hint" style="margin:6px 0 12px">导出全部事件、碎片、人物、关系、概念与模型配置（向量已省略，可由文本重建）。</div>
    <button class="act primary" onclick="exportData()">导出 JSON</button>
  </div>

  <div class="card" style="margin-top:14px;border-color:#4a2630">
    <div class="k" style="color:#ff9b9b">危险区</div>
    <div class="hint" style="margin:6px 0 12px">「清空数据」会清掉全部业务表：记忆、碎片、概念、人物、关系、目标、成长 / 漂移日志、自叙事、标签与别名，以及 v9~v11 新增的<b>会话轮次、陪伴心境、L2 采纳日志、再巩固版本链</b>、情绪与用量；自增计数也归零。新增机制表会自动纳入，不必手工维护清单。模型配置与账号保留，不可恢复。<br><b>L0 人设卡与固定身份宪法不会被清掉</b>：清库清的是经历，不是「我是谁 / 我怎么说话」；要改请去「人设」页。</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <input id="resetConfirm" placeholder="输入 RESET 确认" style="width:200px" autocomplete="off">
      <button class="act danger" onclick="resetAll()">清空数据</button>
      <button class="act" onclick="resetDerived()">只清派生数据</button>
    </div>
  </div>`;
}

async function exportData(){
  const d = await api('/api/export');
  const blob = new Blob([JSON.stringify(d, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'neuromem-' + new Date().toISOString().slice(0, 10) + '.json';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 3000);
  toast('已导出');
}

async function resetAll(){
  const el = $('#resetConfirm');
  const v = el ? el.value.trim() : '';
  if(v !== 'RESET'){ toast('请输入 RESET 确认'); return; }
  if(!confirm('确定清空全部数据？不可恢复。')) return;
  const r = await post('/api/reset', {confirm: 'RESET'});
  if(r.ok){
    const n = Object.keys(r.counts || {}).length;
    const hit = ['adoption_log','conversation_turns','memory_versions','companion_state']
      .filter(t => (r.cleared || []).includes(t)).length;
    toast('已清空 ' + n + ' 张表（含 ' + hit + '/4 张新机制表）');
    await loadOverview();
    switchTab('overview');
  }else{
    toast(r.error || '清空失败');
  }
}

async function resetDerived(){
  if(!confirm('清空派生数据（目标、漂移日志、自我叙事）？记忆本身保留，不可恢复。')) return;
  const r = await post('/api/reset', {confirm: 'RESET', scope: 'derived'});
  toast(r.ok ? '已清理派生数据' : (r.error || '失败'));
  if(r.ok){ await loadOverview(); switchTab('data'); }
}

async function switchTab(tab){
  const m = PAGE_META[tab] || [tab, ''];
  const t = $('#pageTitle'); if(t) t.textContent = m[0];
  const s = $('#pageSub'); if(s) s.textContent = m[1];
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on', b.dataset.tab===tab));
  if(tab==='overview'){ renderOverview(); return; }
  if(tab==='graph'){ renderGraph(); return; }
  if(tab==='growth'){ renderGrowth(); return; }
  if(tab==='chat'){ $('#view').innerHTML = renderChat(); return; }
  if(tab==='models'){ renderModels(); return; }
  if(tab==='persona'){ renderPersona(); return; }
  if(tab==='data'){ renderData(); return; }
  if(tab==='events'){
    const [rows, ps] = await Promise.all([api('/api/events' + evQuery()), api('/api/personas')]);
    $('#view').innerHTML = renderEvents(rows, ps);
    syncFilterUI();
    return;
  }
  if(tab==='concepts'){ renderConceptsTab(); return; }
  if(tab==='relations'){
    const [rows, ps] = await Promise.all([api('/api/relations'), api('/api/personas')]);
    $('#view').innerHTML = renderRelations(rows, ps);
    return;
  }
  let html='';
  if(tab==='personas') html = renderPersonas(await api('/api/personas'));
  $('#view').innerHTML = html;
}

document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
document.addEventListener('keydown', e=>{
  if(e.key==='Enter' && e.target && e.target.id==='askInput' && !e.shiftKey){ e.preventDefault(); doAsk(); }
  if(e.key==='Enter' && e.ctrlKey && e.target && e.target.id==='newText'){ e.preventDefault(); addMemory(); }
});
(async () => {
  const ok = await checkAuth();
  if(!ok) return;
  loadOverview();
  switchTab('overview');
})();
window.addEventListener('resize',()=>{
  graphChart&&graphChart.resize(); growthChart&&growthChart.resize();
  emoChart&&emoChart.resize(); modelsChart&&modelsChart.resize();
});
