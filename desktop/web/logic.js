/* 阿澈桌面端 · 逻辑桥接层
 * 纯接口/数据层，零 DOM 依赖、零样式。设计稿的 UI 只管调用这里的方法。
 * 所有方法返回 Promise<json>；网络/解析异常由调用方 catch。
 * 桌面端与网页共用同一个 FastAPI app，所以 /api/* 全部可用。
 */
(function (global) {
  'use strict';

  async function request(path, opts) {
    const res = await fetch(path, opts);
    return res.json();
  }
  const get = p => request(p);
  const body = (p, data, method) => request(p, {
    method: method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {})
  });
  const post = (p, d) => body(p, d, 'POST');
  const put  = (p, d) => body(p, d, 'PUT');

  /* ==================== 对话 ==================== */
  // 真写通道：每轮都会写会话轮次 + 沉淀事实 + 回写情感。
  // 绝不能改用 /api/ask（只读，会让"它问你答它不记得"复发）。
  const chat       = q => post('/api/live', { query: q });
  const history    = (limit) => get('/api/live/history?limit=' + (limit || 60)); // 桌面端 app.py 注册
  const liveStatus = () => get('/api/live/status');
  // 改人设/模型后必须调：陪伴通道常驻单例缓存旧人格，不 reset 则"改了不生效"。
  const resetLive  = () => post('/api/live/reset', {});

  /* ==================== 主动关心 ==================== */
  // GET 即消费：取回后前端展示即视为已消费，无需额外确认接口。
  const proactive     = () => get('/api/proactive');
  const proactiveStat = () => get('/api/proactive/status');

  /* ==================== Agent 人设（L0，人工版本化） ==================== */
  const persona      = () => get('/api/persona');                       // 当前人设 + identity + 校验
  const personaTpl   = (example) => get('/api/persona/template?example=' + (example ? 'true' : 'false'));
  const personaPreview = card => post('/api/persona/preview', { card }); // 校验并渲染注入文本，不写库
  // 一步到位：先校验（不通过直接返回 errors）→ 保存 → reset 常驻单例。
  // UI 只需调这一个方法，"保存后不生效"的坑在这里就堵住了。
  async function personaSave(card) {
    const pre = await personaPreview(card);
    if (!pre || !pre.ok) return pre || { ok: false, error: '校验失败' };
    const r = await put('/api/persona', {
      confirm: 'PERSONA', actor: 'human', reason: '桌面端设置修改', card: card
    });
    if (r && r.ok) { try { await resetLive(); } catch (e) { /* reset 失败不拦保存 */ } }
    return r;
  }
  const personaHistory = (limit) => get('/api/persona/history?limit=' + (limit || 10));
  const personaRollback = v => post('/api/persona/rollback', {
    confirm: 'PERSONA', version: v, actor: 'human', reason: '桌面端回滚到 v' + v
  });

  /* ==================== 固定身份宪法（L0，优先级高于人设） ==================== */
  const identity      = () => get('/api/identity');
  const identityHist  = (limit) => get('/api/identity/history?limit=' + (limit || 10));
  const identitySave  = profile => put('/api/identity', {
    confirm: 'IDENTITY', actor: 'human', reason: '桌面端设置修改', profile: profile
  }); // profile 必含 who/serve/address/tone/limits 五项且非空

  /* ==================== 模型设置（未来模块预留） ==================== */
  const settings     = () => get('/api/settings');
  const settingsSave = payload => put('/api/settings', payload);
  const models       = () => get('/api/settings/models');

  /* ==================== 语音（本地神经 TTS，手动触发） ==================== */
  // 服务可用性：available=模型齐全；ready=服务已加载完可合成。
  const ttsStatus = () => get('/api/tts/status');
  // 按需拉起服务（模型已装时约 2~4 秒）；首次点播或 status.starting 时调用。
  const ttsStart  = () => post('/api/tts/start', {});
  // 合成请求返回流式 audio/pcm（24k 16bit 单声道）。调用方拿 response.body 边收边播。
  // 用 request() 不合适（它强制 res.json()），这里单独走 fetch 返回原始响应。
  async function ttsSpeak(text, voice) {
    const res = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text, voice: voice || '' })
    });
    if (!res.ok) {
      let err = { ok: false, error: 'HTTP ' + res.status };
      try { err = await res.json(); } catch (e) {}
      throw err;
    }
    return res; // 调用方负责读流并播放
  }
  // 收齐整段 PCM 再交回（ArrayBuffer）。CPU 合成比实时慢（RTF~1.8），边生成
  // 边播会断音卡顿；一次性拿到全段再播虽然多等一两秒，但播放是连续的。
  // 保留给实时兜底：缓存还没生成好时点播，现场合成一次。
  async function ttsSpeakBuffered(text, voice) {
    const res = await ttsSpeak(text, voice);
    return res.arrayBuffer();
  }
  // 预合成：回复一产生就入队，后台合成成 wav 落盘，立即返回缓存 key（不阻塞对话）。
  async function ttsPrepare(text, voice) {
    return post('/api/tts/precache', { text: text, voice: voice || '' });
  }
  // 点播时确保缓存就绪（回复产生时通常已预热好，这里秒回）；按钮可显示准备中。
  async function ttsEnsure(text, voice) {
    return post('/api/tts/ensure', { text: text, voice: voice || '' });
  }
  // 缓存音频地址：<audio> 直接 src 过来播，命中即秒开。
  const ttsAudioUrl = (key) => '/api/tts/audio/' + encodeURIComponent(key);

  /* ==================== 外部知识（独立库 + AnySearch MCP） ==================== */
  // 带出处答问：识别实体 → 查独立知识库 → 必要时联网 → 带来源回答。
  // 首次可能十几秒（联网+生成），调用方需给 loading 态。
  const research     = (text, force) => post('/api/research', { text: text, force: !!force });
  // 「她知道了什么」面板：列出最近收录的外部知识 / 按关键词检索。
  const knowledge    = (limit) => get('/api/knowledge?limit=' + (limit || 30));
  const knowledgeFind = (q, limit) => get('/api/knowledge/search?q=' + encodeURIComponent(q || '') + '&limit=' + (limit || 8));

  /* ==================== 桌面壳能力 ==================== */
  const meta = () => get('/api/desktop/meta');
  // 系统通知与打开网页：pywebview 未就绪时静默降级。
  function notify(title, message) {
    try { if (global.pywebview && global.pywebview.api) global.pywebview.api.notify(title, message); } catch (e) {}
  }
  // 回复就绪通知：文字与语音都准备好才由前端调用，点击回聊天页。
  function notifyReply(title, message) {
    try { if (global.pywebview && global.pywebview.api) global.pywebview.api.notify_reply(title, message); } catch (e) {}
  }
  function openWeb(tab) {
    try {
      if (global.pywebview && global.pywebview.api) global.pywebview.api.open_web(tab || '');
      else {
        var url = meta.web_url || '/';
        if (tab) url += (url.indexOf('?') >= 0 ? '&' : '?') + 'tab=' + encodeURIComponent(tab);
        global.open(url, '_blank');
      }
    } catch (e) {}
  }

  global.DeskAPI = {
    request, get, post, put,
    chat, history, liveStatus, resetLive,
    proactive, proactiveStat,
    persona, personaTpl, personaPreview, personaSave, personaHistory, personaRollback,
    identity, identityHist, identitySave,
    ttsStatus, ttsStart, ttsSpeak, ttsSpeakBuffered, ttsPrepare, ttsEnsure, ttsAudioUrl,
    settings, settingsSave, models,
    research, knowledge, knowledgeFind,
    meta, notify, notifyReply, openWeb
  };
})(window);
