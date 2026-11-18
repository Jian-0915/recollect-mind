/* ==========================================================================
   阿澈 · 交互层（原型，无依赖）
   对话页 / 设置页 / 主动关心弹窗共用一份脚本，按 DOM 存在性分派
   ========================================================================== */

(function () {
  "use strict";

  var $ = function (selector, root) { return (root || document).querySelector(selector); };
  var $$ = function (selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  };

  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

  /* ---------------- 主题 ---------------- */

  function syncThemeUI() {
    var dark = document.documentElement.dataset.theme === "dark";
    var item = $("#themeItem");
    if (item) item.setAttribute("aria-checked", dark ? "true" : "false");
    $$("[data-theme-toggle]").forEach(function (btn) {
      var label = btn.querySelector("[data-theme-label]");
      if (label) label.textContent = dark ? "切到晨光" : "切到暖夜";
    });
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("ache.theme", theme); } catch (e) {}
    syncThemeUI();
  }

  $$("[data-theme-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
  });
  syncThemeUI();

  /* ---------------- Toast ---------------- */

  var toastTimer = null;

  function toast(message) {
    var el = $("#toast");
    var text = $("#toastText");
    if (!el || !text) return;
    el.hidden = false;
    text.textContent = message;
    el.style.animation = "none";
    void el.offsetWidth;
    el.style.animation = "";
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { el.hidden = true; }, 3600);
  }

  /* ---------------- 浮层层栈（抽屉 / 确认 / 动作面板） ---------------- */

  var openLayers = [];

  function openLayer(layer, focusEl) {
    if (!layer) return;
    layer._returnFocus = document.activeElement;
    layer.hidden = false;
    openLayers.push(layer);
    document.addEventListener("keydown", onLayerKey);
    var target = focusEl || $(FOCUSABLE, layer);
    if (target) {
      window.setTimeout(function () {
        try { target.focus(); } catch (e) {}
      }, 30);
    }
  }

  function closeLayer(layer) {
    if (!layer || layer.hidden) return;
    layer.hidden = true;
    openLayers = openLayers.filter(function (item) { return item !== layer; });
    if (!openLayers.length) document.removeEventListener("keydown", onLayerKey);
    var back = layer._returnFocus;
    if (back && document.contains(back)) {
      try { back.focus(); } catch (e) {}
    }
  }

  function onLayerKey(event) {
    var top = openLayers[openLayers.length - 1];
    if (!top) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeLayer(top);
      return;
    }
    if (event.key !== "Tab") return;
    var items = $$(FOCUSABLE, top).filter(function (el) { return el.offsetParent !== null; });
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  $$(".sheet, .drawer, .overlay").forEach(function (layer) {
    layer.addEventListener("click", function (event) {
      if (event.target.hasAttribute && event.target.hasAttribute("data-close")) {
        closeLayer(layer);
      }
    });
  });

  /* ---------------- 外部知识抽屉（她自己查来的） ---------------- */

  function knowledgeRow(item) {
    var row = document.createElement("div");
    row.className = "knowledge-item";
    var title = document.createElement("a");
    title.className = "knowledge-item__title";
    title.textContent = item.title || "未命名";
    title.href = item.url || "#";
    title.addEventListener("click", function (event) {
      event.preventDefault();
      if (window.DeskAPI && item.url) {
        // 桌面端用系统默认浏览器打开来源，不劫持 WebView
        try { window.open(item.url, "_blank"); } catch (e) {}
      }
    });
    row.appendChild(title);
    if (item.snippet) {
      var snip = document.createElement("p");
      snip.className = "knowledge-item__snippet";
      snip.textContent = item.snippet;
      row.appendChild(snip);
    }
    var meta = document.createElement("p");
    meta.className = "knowledge-item__meta";
    var when = (item.fetched_at || "").replace("T", " ").slice(0, 16);
    meta.textContent = (item.provider || "anysearch") + " · 抓取 " + (when || "未知时间");
    row.appendChild(meta);
    return row;
  }

  function renderKnowledge(items, statusText) {
    var list = $("#knowledgeList");
    var status = $("#knowledgeStatus");
    if (!list) return;
    list.innerHTML = "";
    if (status) {
      status.hidden = !statusText;
      if (statusText) status.textContent = statusText;
    }
    if (!items || !items.length) {
      var empty = document.createElement("p");
      empty.className = "knowledge-empty";
      empty.textContent = "这里还空着。跟她聊到不懂的东西，她自己去查了才会存进来。";
      list.appendChild(empty);
      return;
    }
    items.forEach(function (item) { list.appendChild(knowledgeRow(item)); });
  }

  async function loadKnowledge(query) {
    if (!window.DeskAPI) return;
    try {
      var data = query
        ? await DeskAPI.knowledgeFind(query)
        : await DeskAPI.knowledge(60);
      renderKnowledge(data && data.items, query
        ? "搜到 " + ((data && data.items) || []).length + " 条"
        : "已收录 " + ((data && data.count) || 0) + " 条");
    } catch (e) {
      renderKnowledge([], "读取失败：" + ((e && e.error) || e));
    }
  }

  function openKnowledgeDrawer() {
    var drawer = $("#knowledgeDrawer");
    if (!drawer) return;
    openLayer(drawer, $("#knowledgeQuery"));
    loadKnowledge("");
  }

  function initKnowledge() {
    var drawer = $("#knowledgeDrawer");
    if (!drawer) return;
    var form = $("#knowledgeSearchForm");
    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var q = ($("#knowledgeQuery") || {}).value || "";
        loadKnowledge(q.trim());
      });
    }
    initAutoSection();
  }

  /* F5：抽屉里的「她自己去查」——开关 + 审计日志 */
  function initAutoSection() {
    var toggle = $("#autoToggle");
    var label = $("#autoToggleLabel");
    var line = $("#autoStatusLine");
    var logEl = $("#autoLog");

    function render(st) {
      if (toggle) toggle.checked = !!(st && st.enabled);
      if (label) label.textContent = st && st.enabled ? "开启" : "已关闭";
      if (line) {
        var today = (st && st.today_count) || 0;
        var cap = (st && st.daily_cap) || 0;
        var last = (st && st.last_at || "").replace("T", " ").slice(0, 16);
        line.textContent = "今天自己查了 " + today + "/" + cap + " 次"
          + (last ? " · 最近 " + last : " · 还没查过");
      }
    }
    function loadStatus() {
      if (!window.DeskAPI || !DeskAPI.autoStatus) return;
      DeskAPI.autoStatus().then(function (st) {
        render(st);
        if (st && st.enabled && DeskAPI.autoLog) {
          DeskAPI.autoLog(10).then(function (r) {
            if (!logEl) return;
            var items = (r && r.items) || [];
            if (!items.length) { logEl.hidden = true; return; }
            logEl.hidden = false;
            logEl.innerHTML = "";
            items.forEach(function (row) {
              var line2 = document.createElement("p");
              line2.className = "auto-log__row";
              var when = (row.created_at || "").replace("T", " ").slice(0, 16);
              line2.textContent = when + " · " + (row.entity || "")
                + " · " + (row.hit_count || 0) + " 条"
                + (row.reason ? " · " + row.reason : "");
              logEl.appendChild(line2);
            });
          }).catch(function () {});
        } else if (logEl) logEl.hidden = true;
      }).catch(function () { if (line) line.textContent = "状态读取失败"; });
    }
    if (toggle) {
      toggle.addEventListener("change", function () {
        if (!window.DeskAPI || !DeskAPI.autoToggle) return;
        var on = toggle.checked;
        DeskAPI.autoToggle(on).then(function (r) {
          if (r && r.ok) loadStatus();
          else { toggle.checked = !on; if (line) line.textContent = "设置失败"; }
        }).catch(function () { toggle.checked = !on; });
      });
    }
    loadStatus();
  }

  initKnowledge();

  /* ---------------- 顶栏菜单 ---------------- */

  function initMenu() {
    var wrap = $(".menu-wrap");
    var btn = $("#menuBtn");
    var menu = $("#appMenu");
    if (!btn || !menu) return;

    function items() {
      return $$(".menu__item", menu).filter(function (el) { return !el.disabled; });
    }

    function open() {
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      var first = items()[0];
      if (first) first.focus();
      document.addEventListener("keydown", onKey);
      document.addEventListener("click", onDocClick, true);
    }

    function close(restoreFocus) {
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onDocClick, true);
      if (restoreFocus) btn.focus();
    }

    function onDocClick(event) {
      if (wrap && !wrap.contains(event.target)) close(false);
    }

    function onKey(event) {
      if (menu.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close(true);
        return;
      }
      var list = items();
      if (!list.length) return;
      var index = list.indexOf(document.activeElement);
      if (event.key === "ArrowDown") {
        event.preventDefault();
        list[index < 0 ? 0 : (index + 1) % list.length].focus();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        list[index < 0 ? list.length - 1 : (index - 1 + list.length) % list.length].focus();
      }
    }

    btn.addEventListener("click", function () {
      if (menu.hidden) open();
      else close(false);
    });

    menu.addEventListener("click", function (event) {
      var item = event.target.closest(".menu__item");
      if (!item || item.disabled) return;
      var hadFocus = item.contains(document.activeElement);
      close(false);
      if (hadFocus) btn.focus();
    });

    var themeItem = $("#themeItem");
    if (themeItem) {
      themeItem.addEventListener("click", function () {
        applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
      });
    }

    // 查看记忆：打开网页后台并直达「记忆」页（记忆 = events）
    var memoryItem = $("#memoryItem");
    if (memoryItem) {
      memoryItem.addEventListener("click", function () {
        if (window.DeskAPI) DeskAPI.openWeb("events");
      });
    }

    // 她知道了什么：外部知识抽屉（独立库，与上面的陪伴记忆分开）
    var knowledgeItem = $("#knowledgeItem");
    if (knowledgeItem) {
      knowledgeItem.addEventListener("click", function () {
        openKnowledgeDrawer();
      });
    }
  }

  initMenu();

  /* ---------------- 对话页版式 ---------------- */

  function initLayout() {
    var options = $$("[data-layout-option]");
    if (!options.length) return;

    var param = null;
    try { param = new URLSearchParams(window.location.search).get("layout"); } catch (e) {}
    if (param && options.some(function (option) { return option.getAttribute("data-layout-option") === param; })) {
      document.documentElement.dataset.layout = param;
      try { localStorage.setItem("ache.layout", param); } catch (e) {}
    }

    function sync() {
      var current = document.documentElement.dataset.layout || "air";
      options.forEach(function (btn) {
        btn.setAttribute("aria-checked", btn.getAttribute("data-layout-option") === current ? "true" : "false");
      });
    }

    options.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var next = btn.getAttribute("data-layout-option");
        if (!next) return;
        document.documentElement.dataset.layout = next;
        try { localStorage.setItem("ache.layout", next); } catch (e) {}
        sync();
        var chat = $("#chat");
        if (chat) chat.scrollTop = chat.scrollHeight;
        toast(next === "air" ? "版式：舒展" : next === "letter" ? "版式：信件" : "版式：极简");
      });
    });

    sync();
  }

  initLayout();

  /* ==========================================================================
     对话页
     ========================================================================== */



/* ---------------- 语音播放（本地 TTS · 预合成缓存直读） ----------------
   回复一产生就后台合成好 wav 落盘；点播用原生 <audio> 直读缓存文件——
   零等待、零卡顿（不再走 Web Audio 解码，也不用等现场合成）。
   语速 playbackRate、音量 volume 都是 <audio> 原生属性。
   缓存没就绪时（首次/排队）显示 preparing，就绪后自动播。 */
var ttsPlayer = {
  audio: null,
  state: "idle",     // idle / preparing / playing
  token: 0,          // 递增令牌：新播放/打断让旧请求失效
  onState: null,     // 由按钮注入状态回调
  rate: 1.0,         // 语速，设置页写入
  volume: 1.0,       // 音量，设置页写入

  _el: function () {
    // 每次播放新建元素：复用同一个 <audio> 换 src 时，WebView2/Chromium
    // 偶发沿用旧解码缓冲，导致换了音色却听到上一次的声音。新建最稳。
    if (this.audio) {
      try { this.audio.pause(); this.audio.src = ""; this.audio.load(); } catch (e) {}
    }
    var a = new Audio();
    a.preload = "auto";
    a.playbackRate = this.rate;
    a.volume = Math.max(0, Math.min(1, this.volume));
    this.audio = a;
    return a;
  },

  _setState: function (s) {
    this.state = s;
    if (this.onState) this.onState(s);
  },

  stop: function () {
    this.token++;                      // 让进行中的 ensure 回调失效
    if (this.audio) {
      try { this.audio.pause(); this.audio.currentTime = 0; } catch (e) {}
    }
    if (this.state !== "idle") this._setState("idle");
  },

  /* 确保缓存就绪 → <audio> 直读播放。返回 Promise<boolean>。 */
  speak: function (text, voice, onState) {
    var self = this;
    this.stop();
    this.onState = onState || null;
    var token = this.token;
    if (!window.DeskAPI || !DeskAPI.ttsEnsure) return Promise.resolve(false);
    this._setState("preparing");
    return DeskAPI.ttsEnsure(text, voice).then(function (r) {
      if (token !== self.token) return false;
      if (!r || !r.ok || !r.key) {
        self._setState("idle");
        var msg = (r && r.error) || "语音不可用";
        if (toast) toast(msg);
        return false;
      }
      var a = self._el();
      a.src = DeskAPI.ttsAudioUrl(r.key);
      a.onplaying = function () { if (token === self.token) self._setState("playing"); };
      a.onended = function () { if (token === self.token) self._setState("idle"); };
      a.onerror = function () {
        if (token === self.token) { self._setState("idle"); if (toast) toast("语音播放失败"); }
      };
      return a.play().then(function () { return true; }).catch(function () {
        if (token === self.token) { self._setState("idle"); if (toast) toast("语音播放失败"); }
        return false;
      });
    }).catch(function (e) {
      if (token !== self.token) return false;
      self._setState("idle");
      if (toast) toast((e && e.error) || "语音不可用");
      return false;
    });
  }
};

/* 回复产生即预热：入队后台合成，失败静默（不影响文字对话）。 */
function ttsWarm(text, voice) {
  if (!window.DeskAPI || !DeskAPI.ttsPrepare || !text) return;
  DeskAPI.ttsPrepare(text, voice).catch(function () {});
}

/* 语音偏好（音色/语速/音量）独立存 localStorage，不进人设版本系统——
   这是设备播放偏好，不是人格，改它不该产生人设新版本。两页共用同一份。 */
var TTS_PREFS_KEY = "ache.tts";
var TTS_VOICE_CHANGE_KEY = "ache.tts.voiceChange";  // 音色变更计数器，跨页通知重新预热
function getTtsPrefs() {
  var d = { voice: "vivian", rate: 1.0, volume: 1.0 };
  try {
    var saved = JSON.parse(localStorage.getItem(TTS_PREFS_KEY) || "{}");
    if (saved && typeof saved === "object") {
      if (saved.voice) d.voice = saved.voice;
      if (typeof saved.rate === "number") d.rate = saved.rate;
      if (typeof saved.volume === "number") d.volume = saved.volume;
    }
  } catch (e) {}
  return d;
}
function setTtsPrefs(p) {
  var old = getTtsPrefs();
  try { localStorage.setItem(TTS_PREFS_KEY, JSON.stringify(p)); } catch (e) {}
  // 音色真变了才计数，触发对话页按新音色重新预热所有气泡
  if (p && p.voice && p.voice !== old.voice) {
    try {
      var n = parseInt(localStorage.getItem(TTS_VOICE_CHANGE_KEY) || "0", 10) || 0;
      localStorage.setItem(TTS_VOICE_CHANGE_KEY, String(n + 1));
    } catch (e) {}
  }
}

// 按当前音色重新预热对话页所有已有气泡（音色变更后调用）。
function rewarmAllBubbles() {
  var voice = getTtsPrefs().voice;
  var btns = document.querySelectorAll(".speak-btn[data-tts-text]");
  for (var i = 0; i < btns.length; i++) {
    ttsWarm(btns[i].getAttribute("data-tts-text"), voice);
  }
}

// 语音按钮恒定显示：可用性由点播时的接口返回决定（失败 toast），不再用
// 一次性异步探测把按钮藏掉——那正是"只出现一次、离开再进来消失"的根因。

// 用户开口（发送新消息）时打断正在念的语音
function stopSpeaking() { ttsPlayer.stop(); }

// 回复就绪才弹系统通知：文字已渲染 + 语音缓存已落盘。
// ttsEnsure 会等后台合成完成（正常几秒），所以通知天然晚于两者；
// 序号防止连续发消息时旧回复的通知迟到覆盖新的。语音服务不可用时降级为
// 文字即通知——否则没装模型就永远收不到提醒，违背「像微信来信息」的初衷。
var replyNoticeSeq = 0;
function fireReplyNotice(text, voice) {
  if (!text || !window.DeskAPI || !DeskAPI.notifyReply) return;
  var seq = ++replyNoticeSeq;
  var title = (window.__botName || "").trim() ||
    (function () { try { return (localStorage.getItem("ache.name") || "").trim(); } catch (e) { return ""; } })() ||
    "阿澈";
  function fire() {
    if (seq !== replyNoticeSeq) return;
    try { DeskAPI.notifyReply(title, text); } catch (e) {}
  }
  try {
    if (window.DeskAPI.ttsEnsure) {
      DeskAPI.ttsEnsure(text, voice || getTtsPrefs().voice).then(fire, fire);
    } else {
      fire();
    }
  } catch (e) { fire(); }
}

  function initChat() {
    var chat = $("#chat");
    var stream = $("#stream");
    var empty = $("#empty");
    var thinking = $("#thinking");
    var input = $("#input");
    var send = $("#send");
    var composer = $("#composer");
    var popup = $("#proactivePopup");
    var status = $("#status");
    var statusLabel = $("#statusLabel");
    var careCard = $("#careCard");
    var careActions = $("#careActions");
    var careMuted = $("#careMuted");
    var sample = $$("[data-sample]");
    var pendingTimer = null;

    function toEnd() {
      window.requestAnimationFrame(function () { chat.scrollTop = chat.scrollHeight; });
    }

    function setStatus(isThinking) {
      if (status) status.dataset.state = isThinking ? "think" : "online";
      if (statusLabel) statusLabel.textContent = isThinking ? "在想…" : "在";
    }

    function resize() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 132) + "px";
    }

    function refreshSend() {
      send.disabled = input.value.trim().length === 0;
    }

    function prefill(text) {
      input.value = text;
      resize();
      refreshSend();
      input.focus();
      try { input.setSelectionRange(text.length, text.length); } catch (e) {}
      toEnd();
    }

    function meMessage(text) {
      var article = document.createElement("article");
      article.className = "msg msg--me";
      var bubbleEl = document.createElement("div");
      bubbleEl.className = "bubble bubble--me";
      var p = document.createElement("p");
      p.textContent = text;
      bubbleEl.appendChild(p);
      article.appendChild(bubbleEl);
      return article;
    }

    function himMessage(text, memoryText) {
      var article = document.createElement("article");
      article.className = "msg msg--him";

      var seal = document.createElement("span");
      seal.className = "seal seal--xs";
      seal.setAttribute("aria-hidden", "true");
      seal.textContent = botName().slice(-1);

      var body = document.createElement("div");
      body.className = "msg__body";

      var bubbleEl = document.createElement("div");
      bubbleEl.className = "bubble bubble--him";
      var p = document.createElement("p");
      p.textContent = text;
      bubbleEl.appendChild(p);
      body.appendChild(bubbleEl);

      // 语音按钮：恒定显示，手动点播不自动念。渲染即后台预热合成。
      var speakBtn = document.createElement("button");
      speakBtn.className = "speak-btn";
      speakBtn.type = "button";
      speakBtn.setAttribute("data-tts-text", text);   // 音色变更后按新音色重新预热用
      speakBtn.setAttribute("aria-label", "念出来");
      speakBtn.title = "念出来";
      function speakIcon(state) {
        // 喇叭 / 转圈(准备中) / 停止方块，纯 SVG
        if (state === "playing")
          return '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>';
        if (state === "preparing")
          return '<svg class="speak-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9"></path></svg>';
        return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5L6 9H3v6h3l5 4V5z"></path><path d="M15.5 8.5a5 5 0 0 1 0 7"></path></svg>';
      }
      function setSpeakState(s) {
        speakBtn.innerHTML = speakIcon(s);
        speakBtn.classList.toggle("is-playing", s === "playing");
        speakBtn.classList.toggle("is-preparing", s === "preparing");
        var label = s === "playing" ? "停止" : (s === "preparing" ? "正在准备语音" : "念出来");
        speakBtn.setAttribute("aria-label", label);
        speakBtn.title = label;
      }
      setSpeakState("idle");
      speakBtn.addEventListener("click", function () {
        if (ttsPlayer.state !== "idle") { ttsPlayer.stop(); return; }
        var prefs = getTtsPrefs();
        ttsPlayer.rate = prefs.rate;
        ttsPlayer.volume = prefs.volume;
        ttsPlayer.speak(text, prefs.voice, setSpeakState);
      });
      // 渲染即预热：后台合成好，等用户点时就秒播。
      var warmVoice = getTtsPrefs().voice;
      ttsWarm(text, warmVoice);
      body.appendChild(speakBtn);

      // 查证按钮：点它才让「她自己上网查」。不挂在每轮自动触发，避免把
      // 私人闲聊当搜索词、也避免程序式打扰。查到的东西进独立知识库。
      var researchBtn = document.createElement("button");
      researchBtn.className = "speak-btn research-btn";
      researchBtn.type = "button";
      researchBtn.setAttribute("aria-label", "查一下这个");
      researchBtn.title = "查一下这个";
      researchBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.5-3.5"></path></svg>';
      researchBtn.addEventListener("click", function () {
        runResearch(text, body, researchBtn);
      });
      body.appendChild(researchBtn);

      if (memoryText) {
        var mem = document.createElement("p");
        mem.className = "memory";
        mem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4h10v16l-5-3.4L7 20V4z"></path></svg><span></span>';
        mem.querySelector("span").textContent = "已记住 · " + memoryText;
        body.appendChild(mem);
      }

      article.appendChild(seal);
      article.appendChild(body);
      return article;
    }

    function insert(node) {
      stream.insertBefore(node, thinking);
    }

    /* 点「查一下」：让外部知识链接管这一句，结果作为带来源的新气泡插入。
     * 按钮自身进入 loading 态；失败用 toast 说明，绝不静默编答案。 */
    function runResearch(text, body, btn) {
      if (!window.DeskAPI || btn.disabled) return;
      btn.disabled = true;
      btn.classList.add("is-preparing");
      btn.title = "正在查…";
      if (thinking) thinking.hidden = false;
      setStatus(true);

      DeskAPI.research(text, false).then(function (r) {
        if (thinking) thinking.hidden = true;
        setStatus(false);
        btn.disabled = false;
        btn.classList.remove("is-preparing");
        btn.title = "查一下这个";
        if (!r || !r.ok || (!r.answer && !(r.sources || []).length)) {
          toast((r && r.note) || "这次没查到，她也不好瞎说");
          return;
        }
        var card = himMessage(r.answer || "我查到这些，你看看：", "");
        var sources = (r.sources || []).slice(0, 5);
        if (sources.length) {
          var box = document.createElement("div");
          box.className = "msg__sources";
          sources.forEach(function (s, n) {
            var a = document.createElement("a");
            a.className = "msg__source";
            a.textContent = "[" + (n + 1) + "] " + (s.title || "来源");
            a.href = s.url || "#";
            a.title = s.url || "";
            a.addEventListener("click", function (event) {
              event.preventDefault();
              if (s.url) { try { window.open(s.url, "_blank"); } catch (e) {} }
            });
            box.appendChild(a);
          });
          card.querySelector(".msg__body").appendChild(box);
        }
        insert(card);
        toEnd();
      }).catch(function (e) {
        if (thinking) thinking.hidden = true;
        setStatus(false);
        btn.disabled = false;
        btn.classList.remove("is-preparing");
        btn.title = "查一下这个";
        toast("查的时候出错了：" + ((e && e.error) || "稍后再试"));
      });
    }

    function shorten(text) {
      var clean = text.replace(/[，。！？、；：…""''\s]/g, "");
      return clean.length > 12 ? clean.slice(0, 12) + "…" : clean;
    }

    /* 后端只回报「这一轮记了什么」，不回内部召回结构。
     * 新增 > 合并到已有 > 补录作废，三种措辞分开，避免「写没写进去」像在骗人。 */
    function memoryLabel(r) {
      var items = (r && r.remembered) || [];
      var first = "";
      if (items.length) {
        var it = items[0];
        first = typeof it === "string" ? it : (it && (it.text || it.summary || it.title)) || "";
      }
      if ((r.remembered_count || 0) > 0) return first ? shorten(first) : "记下了一件事";
      if ((r.remembered_merged || 0) > 0) return "这件事之前记过";
      if ((r.remembered_backdated || 0) > 0) return "补上了之前漏掉的";
      return "";
    }

    /* 极简版把逐条回执收成一行累计，其余版式靠 CSS 隐藏这一行。
     * 后端只回「这一轮记了几件」，累计是前端做的，所以不说「今晚」避免骗人。 */
    var memoryTotal = 0;
    var summaryEl = null;

    function bumpSummary(r) {
      var n = (r && r.remembered_count) || 0;
      if (!n) return;
      memoryTotal += n;
      if (!summaryEl || !summaryEl.isConnected) {
        summaryEl = document.createElement("p");
        summaryEl.className = "memory-summary";
        summaryEl.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4h10v16l-5-3.4L7 20V4z"></path></svg><span></span>';
      }
      var span = summaryEl.querySelector("span");
      if (span) span.textContent = "已记住 " + memoryTotal + " 件";
      // 每次挪到末尾，跟着最新一条消息走
      stream.insertBefore(summaryEl, thinking);
    }

    /* 走真写通道 /api/live：不是只读问答，否则「你答它不记得」会复发。 */
    var sending = false;

    function showSendError(msg) {
      if (!sendError) return;
      sendError.hidden = false;
      var span = sendError.querySelector("span");
      if (span && msg) span.textContent = msg;
    }

    function sendMessage() {
      var text = input.value.trim();
      if (!text || sending) return;
      if (!window.DeskAPI) { showSendError("逻辑层没加载，刷新一下试试"); return; }

      stopSpeaking();   // 用户开口就停，避免边念边打字互相干扰
      sending = true;
      if (sendError) sendError.hidden = true;
      if (empty) empty.hidden = true;
      sample.forEach(function (el) { el.hidden = true; });

      var mine = meMessage(text);
      insert(mine);
      input.value = "";
      resize();
      refreshSend();
      thinking.hidden = false;
      setStatus(true);
      toEnd();

      DeskAPI.chat(text).then(function (r) {
        thinking.hidden = true;
        setStatus(false);
        if (!r || !r.ok) {
          // 发不出去就收回气泡、原文放回输入框，避免出现两条一样的话
          mine.remove();
          input.value = text;
          resize();
          refreshSend();
          showSendError((r && (r.hint || r.error)) || "没发出去，" + botName() + "没收到。");
          return;
        }
        // 文字气泡已插入、语音交给 himMessage 预热；等缓存真正落盘后弹系统通知。
        var replyText = r.reply || "……";
        insert(himMessage(replyText, memoryLabel(r)));
        fireReplyNotice(replyText, getTtsPrefs().voice);
        bumpSummary(r);
        toEnd();
      }).catch(function () {
        thinking.hidden = true;
        setStatus(false);
        mine.remove();
        input.value = text;
        resize();
        refreshSend();
        showSendError("连不上服务，" + botName() + "没收到。");
      }).then(function () {
        sending = false;
      });
    }

    /* ---------- 历史回填 ---------- */

    var sendError = $("#sendError");
    var lastStampAt = 0;

    /* 相邻两条间隔不到 30 分钟就不重复插时间戳，避免满屏都是 20:41 */
    function stampOf(ts) {
      var t = Date.parse(ts || "");
      if (!t) return 0;
      if (lastStampAt && t - lastStampAt < 30 * 60 * 1000) { lastStampAt = t; return 0; }
      lastStampAt = t;
      return t;
    }

    function addStamp(t) {
      var d = new Date(t);
      var hh = ("0" + d.getHours()).slice(-2);
      var mm = ("0" + d.getMinutes()).slice(-2);
      var div = document.createElement("div");
      div.className = "stamp";
      var span = document.createElement("span");
      span.textContent = hh + ":" + mm;
      div.appendChild(span);
      insert(div);
    }

    function renderTurn(turn) {
      var t = stampOf(turn.ts);
      if (t) addStamp(t);
      if (turn.role === "agent") insert(himMessage(turn.text, ""));
      else insert(meMessage(turn.text));
    }

    function loadHistory() {
      if (!window.DeskAPI) return;
      DeskAPI.history(40).then(function (r) {
        var turns = (r && r.turns) || [];
        if (!turns.length) {
          if (empty) empty.hidden = false;
          return;
        }
        if (empty) empty.hidden = true;
        turns.forEach(renderTurn);
        toEnd();
        var mark = botName().slice(-1);
        $$(".seal").forEach(function (el) { el.textContent = mark; });
      }).catch(function () {
        if (empty) empty.hidden = false;
      });
    }

    composer.addEventListener("submit", function (event) {
      event.preventDefault();
      sendMessage();
    });

    input.addEventListener("input", function () {
      resize();
      refreshSend();
    });

    /* 点输入框空白处也能落焦点 */
    var composerField = $(".composer__field");
    if (composerField) {
      composerField.addEventListener("click", function (event) {
        if (!event.target.closest("button")) input.focus();
      });
    }

    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        if (!send.disabled) sendMessage();
      }
    });

    $$(".suggestion").forEach(function (btn) {
      btn.addEventListener("click", function () {
        prefill(btn.getAttribute("data-say") || btn.textContent.trim());
      });
    });

    /* 发送失败：重发 = 真的再发一次（原文还在输入框里） */
    var retrySend = $("#retrySend");
    if (retrySend) {
      retrySend.addEventListener("click", function () {
        if (sendError) sendError.hidden = true;
        if (!input.value.trim()) return;
        sendMessage();
      });
    }

    /* ---------- 主动关心：角落浮层 ---------- */

    var careTopic = "";

    function hidePopup() {
      if (popup) popup.hidden = true;
    }

    /* 非模态：只浮出来，不抢焦点（设计稿 §6）。 */
    /* 不同的来由，接话的方式也不一样：问事可以说「先不用」，
       质问和吃醋就不是「问」了，只能回或者当没看见。 */
    var PP_COPY = {
      missing: { later: "先不用", reply: "回应", fill: "想接着说说「%s」。" },
      ignored: { later: "装没看见", reply: "回一句", fill: "" },
      jealous: { later: "装没看见", reply: "回一句", fill: "" },
      sulk_end: { later: "先不理", reply: "回一句", fill: "" }
    };

    function showProactive(data) {
      if (!popup || !data || !data.text) return;
      careTopic = data.topic || "";
      var copy = PP_COPY[data.kind] || PP_COPY.missing;
      var textEl = $(".popup__text", popup);
      if (textEl) textEl.textContent = data.text;
      var timeEl = $(".popup__time", popup);
      if (timeEl) timeEl.textContent = "刚刚";
      var laterBtn = $('[data-pp="later"]', popup);
      var replyBtn = $('[data-pp="reply"]', popup);
      if (laterBtn) laterBtn.textContent = copy.later;
      if (replyBtn) replyBtn.textContent = copy.reply;
      popup.dataset.kind = data.kind || "missing";
      popup.hidden = false;
      if (window.DeskAPI) DeskAPI.notify(botName(), data.text);
    }

    /* GET 即消费：后端在取回时就标记了 consumed，不会重复弹。 */
    function pollProactive() {
      if (!window.DeskAPI || document.hidden) return;
      if (popup && !popup.hidden) return;          // 同时最多一张
      DeskAPI.proactive().then(function (r) {
        if (r && r.available && r.text) showProactive(r);
      }).catch(function () {});
    }

    var ppClose = $("#ppClose");
    if (ppClose) ppClose.addEventListener("click", hidePopup);

    if (popup) {
      popup.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-pp]");
        if (!btn) return;
        var action = btn.getAttribute("data-pp");
        var kind = popup.dataset.kind || "missing";
        var copy = PP_COPY[kind] || PP_COPY.missing;
        hidePopup();
        if (action === "reply") {
          var fill = (careTopic && copy.fill) ? copy.fill.replace("%s", careTopic) : "";
          prefill(fill);
          toast(fill ? "话题已填进输入框，按 Enter 发送" : "想说什么就写，Enter 发送");
        } else {
          toast(kind === "missing" ? "好，晚点再问你" : "嗯，那就先这样");
        }
      });

      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !popup.hidden) hidePopup();
      });
    }

    /* 主动关心气泡：回应 / 忽略 */
    if (careCard && careActions && careMuted) {
      careCard.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-care]");
        if (!btn) return;
        var action = btn.getAttribute("data-care");
        careActions.hidden = true;
        careMuted.hidden = false;
        if (action === "reply") {
          careCard.dataset.state = "replied";
          careMuted.textContent = "已回应 · 话题已填进输入框";
          prefill(careTopic ? "想接着说说「" + careTopic + "」。" : "");
          toast("已填进输入框，按 Enter 发送");
        } else {
          careCard.dataset.state = "declined";
          careMuted.textContent = "已放下";
          toast("好，晚点再问你");
        }
      });
    }

    /* 从弹窗带回的话题 */
    try {
      var topic = new URLSearchParams(window.location.search).get("topic");
      if (topic) {
        prefill(topic);
        toast("话题已填进输入框，按 Enter 发送");
      }
    } catch (e) {}

    /* ---------- 顶栏名字跟人设走 ---------- */

    /* 人设名字由库里决定，HTML 里写死的只是首帧占位。
     * 所有出现名字的地方（顶栏、印章、占位符、空状态、弹窗标题、报错）都跟着它走。 */
    function botName() {
      var cached = "";
      try { cached = localStorage.getItem("ache.name") || ""; } catch (e) {}
      return (window.__botName || cached || "").trim() || "它";
    }

    /* 一处改，处处改：名字读不出来就保持 HTML 的首帧占位。 */
    function applyName(raw) {
      var name = (raw || "").trim();
      if (!name) return;
      window.__botName = name;
      try { localStorage.setItem("ache.name", name); } catch (e) {}

      var nameEl = $(".identity__name");
      if (nameEl) nameEl.textContent = name;

      // 印章取名字最后一个字：两字以上取末字，单字就用它自己
      // 所有印章一起换，否则会出现顶栏「满」、气泡「澈」这种自相矛盾
      var mark = name.slice(-1);
      $$(".seal").forEach(function (el) { el.textContent = mark; });

      var inputEl = $("#input");
      if (inputEl) inputEl.setAttribute("placeholder", "跟" + name + "说点什么…");
      var emptyTitle = $("#emptyTitle");
      if (emptyTitle) emptyTitle.textContent = "跟" + name + "说句话吧";
      var ppName = $("#ppName");
      if (ppName) ppName.textContent = name;

      // 无障碍标签与报错文案里的名字也一起换
      var root = document.querySelector("h1.sr-only");
      if (root) root.textContent = "与" + name + "的对话";
      var identityLink = document.querySelector(".identity");
      if (identityLink) identityLink.setAttribute("aria-label", name + " · 打开人设设置");
      var inputLabel = document.querySelector('label[for="input"]');
      if (inputLabel) inputLabel.textContent = "对" + name + "说";
      if (sendError) {
        var errSpan = sendError.querySelector("span");
        if (errSpan) errSpan.textContent = "没发出去，" + name + "没收到。";
      }

      document.title = name + " · 对话";
    }

    function syncName() {
      if (!window.DeskAPI) return Promise.resolve();
      return DeskAPI.persona().then(function (r) {
        var card = (r && r.card) || {};
        applyName(card.name || "");
      }).catch(function () {});
    }

    /* ---------- 启动 ---------- */

    /* 信件/极简版把「Enter 发送」那行提示隐藏了，第一次用给一次 toast，
     * 之后不再打扰——否则新用户不知道回车就能发。 */
    (function hintEnterOnce() {
      var layout = document.documentElement.dataset.layout || "air";
      if (layout === "air") return;
      try {
        if (localStorage.getItem("ache.enterHint")) return;
        localStorage.setItem("ache.enterHint", "1");
      } catch (e) { return; }
      window.setTimeout(function () {
        toast("Enter 发送 · Shift + Enter 换行");
      }, 1600);
    })();

    resize();
    refreshSend();
    // 首帧先用上次记住的名字铺一遍，再向服务端确认
    try { applyName(localStorage.getItem("ache.name") || ""); } catch (e) {}
    // 先确定名字再回填历史：气泡头像是渲染时定死的，晚到就会写着错字
    syncName().then(loadHistory, loadHistory);

    // 首次延后 10 秒，别和回填历史抢启动；之后每 60 秒问一次
    window.setTimeout(pollProactive, 10000);
    window.setInterval(pollProactive, 60000);

    // 设置页改音色后，按新音色重新预热所有历史气泡：
    // 跨标签页/页面走 storage 事件；同页（本窗口跳设置再返回）用 focus 兜底。
    window.addEventListener("storage", function (e) {
      if (e && e.key === TTS_VOICE_CHANGE_KEY) rewarmAllBubbles();
    });
    window.addEventListener("focus", rewarmAllBubbles);

    // 通知点击回跳时若刚重载页面，把光标放回输入框、滚到最新一条。
    if (/[?&]from=notify\b/.test(window.location.search)) {
      window.setTimeout(function () {
        try { input.focus(); } catch (e) {}
        toEnd();
      }, 300);
    }
  }

  /* ==========================================================================
     主动关心弹窗独立页
     ========================================================================== */

  function initPopupPage() {
    var card = $("#popup");
    if (!card) return;
    var reset = $("#reset");
    var dismissed = false;

    /* 弹窗页也要跟人设名字走：它可能被单独打开成小窗。 */
    (function nameIntoPopup() {
      if (!window.DeskAPI) return;
      DeskAPI.persona().then(function (r) {
        var name = ((r && r.card && r.card.name) || "").trim();
        if (!name) return;
        var nameEl = $(".popup__name", card);
        if (nameEl) nameEl.textContent = name;
        $$(".seal", card).forEach(function (el) { el.textContent = name.slice(-1); });
        if (!card.hidden) document.title = name + " · 想跟你说句话";
      }).catch(function () {});
    })();

    function dismiss() {
      if (dismissed) return;
      dismissed = true;
      card.style.transition = "opacity 240ms ease, transform 240ms ease";
      card.style.opacity = "0";
      card.style.transform = "translateY(6px)";
      window.setTimeout(function () {
        card.hidden = true;
        if (reset) reset.hidden = false;
      }, 260);
    }

    card.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-pp]");
      if (!btn) return;
      if (btn.getAttribute("data-pp") === "reply") {
        window.location.href = "index.html?topic=" +
          encodeURIComponent("周四下午空出来的那段时间，我想留给自己。");
      } else {
        dismiss();
      }
    });

    var closeBtn = $("#ppClose");
    if (closeBtn) closeBtn.addEventListener("click", dismiss);

    if (reset) {
      reset.addEventListener("click", function () {
        dismissed = false;
        card.hidden = false;
        card.style.opacity = "";
        card.style.transform = "";
        reset.hidden = true;
        var btn = $('[data-pp="reply"]', card);
        if (btn) btn.focus();
      });
    }
  }

  /* ==========================================================================
     Agent 设置页
     ========================================================================== */

  function initSettings() {
    var root = $(".app--settings");
    if (!root) return;

    var GROUPS = {
      basic: { title: "基本", sub: "名字、身份和性格，是它每句话的底子。" },
      voice: { title: "说话方式", sub: "风格、长度、称呼与口头禅，决定它一句话怎么说。" },
      audio: { title: "语音播放", sub: "音色、语速和音量——她开口念出来时的声音。" },
      mcp: { title: "搜索服务", sub: "她查资料用的 MCP 服务，可填密钥、可换成自建服务。" },
      relation: { title: "关系与相处", sub: "它多久找你一次、怎么关心你、什么绝对不做。" },
      examples: { title: "示范语气", sub: "两组对话，只学语气，不当真实经历。" },
      identity: { title: "固定身份宪法", sub: "优先级高于人设，冲突时以这里为准。" },
      history: { title: "版本历史", sub: "每次保存都会留一版，可以随时恢复。" }
    };

    var indexPane = $("#indexPane");
    var detailPane = $("#detailPane");
    var detailBody = $("#detailBody");
    var sections = $$(".detail__section");
    var barTitle = $("#barTitle");
    var backLink = $("#backLink");
    var backLabel = $("#backLabel");
    var detailTitle = $("#detailTitle");
    var detailSub = $("#detailSub");
    var savedState = $("#savedState");
    var savedText = $("#savedText");
    var saveHint = $("#saveHint");
    var versionTag = $("#versionTag");
    var traits = $("#traits");
    var traitInput = $("#traitInput");
    var traitCount = $("#traitCount");
    var traitAdd = $("#traitAdd");
    var range = $("#f-proactive");
    var rangeOut = $("#proactiveOut");
    var saveBtn = $("#saveBtn");
    var version = 1;
    var identityVersion = 1;
    var updatedAt = "";          // 人设最后保存时间
    var rawCard = null;          // 服务端原文：单选没匹配到时用它原样写回
    var rawIdentity = null;
    var dirty = false;
    var routed = false;
    var lastSaved;

    /* ---------- 语音播放偏好（独立保存，不进人设版本） ---------- */
    function initTtsSettings() {
      var voice = $("#tts-voice");
      var rate = $("#tts-rate");
      var volume = $("#tts-volume");
      var rateOut = $("#ttsRateOut");
      var volumeOut = $("#ttsVolumeOut");
      var tryBtn = $("#ttsTryBtn");
      var tryHint = $("#ttsTryHint");
      if (!voice || !rate || !volume) return;

      var prefs = getTtsPrefs();
      voice.value = prefs.voice;
      rate.value = prefs.rate;
      volume.value = prefs.volume;

      function rateLabel(v) {
        if (v < 0.85) return "偏慢";
        if (v < 0.97) return "稍慢";
        if (v <= 1.03) return "正常";
        if (v <= 1.2) return "稍快";
        return "偏快";
      }
      function syncOut() {
        if (rateOut) rateOut.textContent = rateLabel(parseFloat(rate.value));
        if (volumeOut) volumeOut.textContent = Math.round(parseFloat(volume.value) * 100) + "%";
      }
      function save() {
        setTtsPrefs({
          voice: voice.value,
          rate: parseFloat(rate.value),
          volume: parseFloat(volume.value)
        });
        ttsPlayer.rate = parseFloat(rate.value);
        ttsPlayer.volume = parseFloat(volume.value);
      }
      [voice, rate, volume].forEach(function (el) {
        el.addEventListener("change", function () { syncOut(); save(); });
        el.addEventListener("input", syncOut);
      });
      syncOut();

      if (tryBtn) {
        tryBtn.addEventListener("click", function () {
          if (ttsPlayer.state !== "idle") { ttsPlayer.stop(); return; }
          if (!window.DeskAPI || !DeskAPI.ttsStatus) return;
          DeskAPI.ttsStatus().then(function (s) {
            if (s && s.ready) doTry();
            else if (s && s.available) {
              if (tryHint) tryHint.textContent = "正在启动语音服务，请稍等…";
              return DeskAPI.ttsStart().then(function (r) {
                if (r && r.ok) doTry();
                else if (tryHint) tryHint.textContent = (r && r.error) || "语音服务没起来";
              });
            } else if (tryHint) tryHint.textContent = (s && s.reason) || "语音不可用";
          }).catch(function () {
            if (tryHint) tryHint.textContent = "语音服务连不上";
          });
        });
      }
      function doTry() {
        if (tryHint) tryHint.textContent = "正在合成，请稍等…";
        save();
        var pre = getTtsPrefs();
        ttsPlayer.rate = pre.rate;
        ttsPlayer.volume = pre.volume;
        if (tryBtn) tryBtn.textContent = "停止试听";
        ttsPlayer.speak("你好，我是" + (localStorage.getItem("ache.name") || "她") + "。这是我在说话。", pre.voice, function (state) {
          if (state === "idle" && tryBtn) tryBtn.textContent = "试听一句";
          if (state === "playing" && tryHint) tryHint.textContent = "正在播放，再点按钮可以停。";
        });
      }
    }

    /* ---------- 搜索服务（通用 MCP 管理器） ----------
     * 密钥只从后端拿掩码，前端永不接触明文；保存时留空/掩码 = 沿用旧值。
     * 与陪伴记忆无关，纯能力配置。 */
    function initMcpSettings() {
      var listEl = $("#mcpList");
      var editor = $("#mcpEditor");
      if (!listEl || !editor) return;

      var fName = $("#mcpName");
      var fUrl = $("#mcpUrl");
      var fKey = $("#mcpKey");
      var fTool = $("#mcpTool");
      var editorTitle = $("#mcpEditorTitle");
      var keyInfo = $("#mcpKeyInfo");
      var testResult = $("#mcpTestResult");
      var addBtn = $("#mcpAddBtn");
      var saveBtn = $("#mcpSaveBtn");
      var testBtn = $("#mcpTestBtn");
      var cancelBtn = $("#mcpCancelBtn");

      var servers = [];       // 服务端返回的掩码版列表
      var activeId = null;
      var editingId = null;   // null = 新增

      function api() { return window.DeskAPI && DeskAPI.mcpServers && window.DeskAPI; }
      function say(text, bad) {
        if (!testResult) return;
        testResult.textContent = text || "";
        testResult.classList.toggle("is-bad", !!bad);
      }
      function keyLabel(info) {
        if (!info || !info.set) return "未设置（匿名，限流更严）";
        return (info.len || 0) + " 位 · " + (info.tail || "****");
      }

      function render() {
        listEl.textContent = "";
        if (!servers.length) {
          var empty = document.createElement("p");
          empty.className = "field__hint";
          empty.textContent = "还没有服务。点下面的「添加服务」填一个 MCP 端点。";
          listEl.appendChild(empty);
          return;
        }
        servers.forEach(function (s) {
          var row = document.createElement("div");
          row.className = "mcp-item";
          if (s.id === activeId) row.classList.add("is-active");

          var head = document.createElement("div");
          head.className = "mcp-item__head";
          var name = document.createElement("span");
          name.className = "mcp-item__name";
          name.textContent = s.name || s.id;
          head.appendChild(name);
          if (s.id === activeId) {
            var badge = document.createElement("span");
            badge.className = "tag mcp-item__badge";
            badge.textContent = "使用中";
            head.appendChild(badge);
          }
          row.appendChild(head);

          var meta = document.createElement("p");
          meta.className = "mcp-item__meta";
          meta.textContent = (s.url || "") + "  ·  工具 " + (s.tool || "search") +
            "  ·  密钥 " + keyLabel(s.api_key_info);
          row.appendChild(meta);

          var acts = document.createElement("div");
          acts.className = "mcp-item__actions";
          function actBtn(label, cls, fn) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "btn btn--" + cls + " btn--sm";
            b.textContent = label;
            b.addEventListener("click", fn);
            acts.appendChild(b);
          }
          if (s.id !== activeId) {
            actBtn("设为默认", "ghost", function () {
              var a = api(); if (!a) return;
              a.mcpActive(s.id).then(function (r) {
                if (r && r.ok) apply(r); else say((r && r.error) || "设置失败", true);
              }).catch(function () { say("设置失败", true); });
            });
          }
          actBtn("编辑", "quiet", function () { openEditor(s); });
          actBtn("删除", "quiet", function () {
            if (!window.confirm("删除服务「" + (s.name || s.id) + "」？")) return;
            var a = api(); if (!a) return;
            var next = servers.filter(function (x) { return x.id !== s.id; });
            a.mcpSave(next).then(function (r) {
              if (r && r.ok) { apply(r); }
              else say((r && r.error) || "删除失败", true);
            }).catch(function () { say("删除失败", true); });
          });
          row.appendChild(acts);
          listEl.appendChild(row);
        });
      }

      function apply(r) {
        servers = (r && r.servers) || [];
        activeId = (r && r.active_id) || null;
        render();
      }

      function load() {
        var a = api();
        if (!a) { say("接口尚未就绪，刷新页面重试", true); return; }
        a.mcpServers().then(function (r) {
          if (r && r.ok) apply(r);
          else say((r && r.error) || "读取失败", true);
        }).catch(function () { say("读取失败，请刷新重试", true); });
      }

      function openEditor(s) {
        editingId = s ? s.id : null;
        if (editorTitle) editorTitle.textContent = s ? ("编辑 · " + (s.name || s.id)) : "添加服务";
        fName.value = s ? (s.name || "") : "";
        fUrl.value = s ? (s.url || "") : "";
        fKey.value = "";   // 永远不清空显示明文：留空 = 沿用旧密钥
        fTool.value = s ? (s.tool || "search") : "search";
        if (keyInfo) keyInfo.textContent = s ? keyLabel(s.api_key_info) : "可选";
        say("");
        editor.hidden = false;
        try { fName.focus(); } catch (e) {}
      }
      function closeEditor() {
        editor.hidden = true;
        editingId = null;
        say("");
      }

      function formServer() {
        return {
          id: editingId || "",
          name: (fName.value || "").trim(),
          url: (fUrl.value || "").trim(),
          api_key: fKey.value || "",
          tool: (fTool.value || "").trim() || "search",
          enabled: editingId ? (activeId === editingId) : false
        };
      }

      if (addBtn) addBtn.addEventListener("click", function () { openEditor(null); });
      if (cancelBtn) cancelBtn.addEventListener("click", closeEditor);

      if (saveBtn) {
        saveBtn.addEventListener("click", function () {
          var a = api(); if (!a) return;
          var row = formServer();
          if (!/^https?:\/\//.test(row.url)) { say("服务地址必须是 http(s) 开头", true); return; }
          var next = servers.slice();
          var idx = -1;
          for (var i = 0; i < next.length; i++) {
            if (next[i].id === row.id) { idx = i; break; }
          }
          if (idx >= 0) next[idx] = row;
          else { row.id = "srv_" + Date.now().toString(36); next.push(row); }
          saveBtn.disabled = true;
          say("正在保存…");
          a.mcpSave(next).then(function (r) {
            saveBtn.disabled = false;
            if (r && r.ok) { apply(r); closeEditor(); }
            else say((r && r.error) || "保存失败", true);
          }).catch(function () { saveBtn.disabled = false; say("保存失败", true); });
        });
      }

      if (testBtn) {
        testBtn.addEventListener("click", function () {
          var a = api(); if (!a) return;
          var cfg = formServer();
          if (!/^https?:\/\//.test(cfg.url)) { say("先填一个合法的 http(s) 服务地址", true); return; }
          testBtn.disabled = true;
          say("正在测试连接…");
          a.mcpTest(cfg).then(function (r) {
            testBtn.disabled = false;
            if (r && r.ok) {
              var msg = "连接正常";
              if (r.latency != null) msg += " · " + r.latency + "s";
              msg += " · 工具 " + ((r.tools || []).length) + " 个";
              if (r.has_search === false) msg += "（工具名可能不对）";
              say(msg, r.has_search === false);
            } else {
              say((r && r.error) || "连接失败", true);
            }
          }).catch(function () { testBtn.disabled = false; say("连接失败", true); });
        });
      }

      load();
    }

    var RANGE_LABELS = {
      "1": "1 · 很克制",
      "2": "2 · 克制",
      "3": "3 · 偶尔主动",
      "4": "4 · 常主动",
      "5": "5 · 很主动"
    };

    /* 主动性滑杆 = 人设里的 relationship.initiative，不是弹窗频率。
     * 文案刻意写成「说话时多主动」，避免被误读成「多久打扰一次」。 */
    var INITIATIVE = {
      "1": "几乎不主动，等对方先开口",
      "2": "很少主动，只在明显没聊完时接一句",
      "3": "偶尔主动，有依据才提",
      "4": "常主动，看到相关的事会问一句",
      "5": "很主动，会主动找话题"
    };

    var OPTIONS = {
      style: {
        "温和": "温和，先接住情绪再谈事",
        "简洁": "短句、口语，少形容词",
        "直白": "直接说结论，不绕弯",
        "俏皮": "偶尔逗一下，但不油"
      },
      length: {
        "短句": "1~2 句，说完就停",
        "适中": "1~3 句",
        "展开": "可以多说两句，但别写成小作文"
      },
      relation: {
        "朋友": "慢慢熟悉、但不越界的朋友",
        "搭档": "一起做事的搭档，能互相提醒",
        "树洞": "可以放心说难听话的树洞",
        "前辈": "比你多走了几步，但不摆资历"
      },
      mood: {
        "平静": "平静偏暖，不忽冷忽热",
        "明亮": "偏明亮，但不亢奋",
        "低沉": "偏安静，不主动起高调"
      }
    };

    /* ---------- 状态读写 ---------- */

    function getTraitNames() {
      if (!traits) return [];
      return $$(".chip", traits).map(function (chip) {
        return (chip.firstChild && chip.firstChild.textContent || "").trim();
      }).filter(Boolean);
    }

    function getState() {
      var texts = {};
      $$("input.input, textarea.textarea, input.range", detailPane).forEach(function (el) {
        // traitInput 是临时输入框；tts-* 是设备偏好，都不属于人设卡，不能混进保存状态
        if (el.id && el.id !== "traitInput" && el.id.indexOf("tts-") !== 0) texts[el.id] = el.value;
      });
      var radios = {};
      $$('input[type="radio"]', detailPane).forEach(function (el) {
        if (el.checked) radios[el.name] = el.value;
      });
      return { texts: texts, radios: radios, traits: getTraitNames() };
    }

    function setState(state) {
      if (!state) return;
      Object.keys(state.texts || {}).forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.value = state.texts[id];
      });
      Object.keys(state.radios || {}).forEach(function (name) {
        $$('input[name="' + name + '"]', detailPane).forEach(function (el) {
          el.checked = el.value === state.radios[name];
        });
      });
      setTraits(state.traits || []);
      syncRange();
    }

    /* ---------- 性格芯片 ---------- */

    function makeChip(name) {
      var span = document.createElement("span");
      span.className = "chip";
      span.setAttribute("role", "listitem");
      span.appendChild(document.createTextNode(name));
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip__x";
      btn.setAttribute("aria-label", "移除性格：" + name);
      btn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>';
      span.appendChild(btn);
      return span;
    }

    function syncCount() {
      if (!traits) return;
      var count = $$(".chip", traits).length;
      if (traitCount) traitCount.textContent = count;
      if (traitInput) traitInput.disabled = count >= 8;
      if (traitAdd) traitAdd.disabled = count >= 8;
    }

    function setTraits(names) {
      if (!traits) return;
      traits.innerHTML = "";
      (names || []).slice(0, 8).forEach(function (name) {
        traits.appendChild(makeChip(name));
      });
      syncCount();
    }

    function addTrait() {
      if (!traitInput) return;
      var name = traitInput.value.trim();
      if (!name) { traitInput.focus(); return; }
      var names = getTraitNames();
      if (names.length >= 8) { toast("最多 8 条性格"); return; }
      if (names.indexOf(name) >= 0) { toast("这条已经有了"); traitInput.select(); return; }
      traits.appendChild(makeChip(name));
      traitInput.value = "";
      syncCount();
      markDirty();
      traitInput.focus();
    }

    if (traitAdd) traitAdd.addEventListener("click", addTrait);
    if (traitInput) {
      traitInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          event.preventDefault();
          addTrait();
        }
      });
    }
    if (traits) {
      traits.addEventListener("click", function (event) {
        var btn = event.target.closest(".chip__x");
        if (!btn) return;
        btn.parentElement.remove();
        syncCount();
        markDirty();
      });
    }

    /* ---------- 主动性 ---------- */

    function syncRange() {
      if (!range || !rangeOut) return;
      var label = RANGE_LABELS[range.value] || range.value;
      rangeOut.textContent = label;
      range.setAttribute("aria-valuetext", label);
    }

    if (range) range.addEventListener("input", syncRange);

    /* ---------- 人设卡 <-> 表单 ---------- */

    function splitList(text) {
      return String(text || "")
        .split(/[、，,;；\n]/)
        .map(function (x) { return x.trim(); })
        .filter(Boolean);
    }

    function fillRadio(name, map, text) {
      var raw = String(text || "").trim();
      var hit = "";
      Object.keys(map).forEach(function (k) {
        if (map[k] === raw) hit = k;
      });
      var inputs = $$('input[name="' + name + '"]', detailPane);
      inputs.forEach(function (el) {
        el.checked = !!hit && el.value === hit;
      });

      // 库里是自由文本、界面没有对应档位时明确说出来，
      // 别让人以为「一个都没选中」是坏了
      var wrap = inputs.length ? inputs[0].closest(".pills") : null;
      var host = wrap ? wrap.parentElement : null;
      var note = host ? host.querySelector('[data-custom="' + name + '"]') : null;
      if (host && !hit && raw) {
        if (!note) {
          note = document.createElement("p");
          note.className = "field__hint";
          note.setAttribute("data-custom", name);
          host.appendChild(note);
        }
        note.textContent = "当前设定：" + raw + "（不是预设档位，保存时按原文保留）";
      } else if (note) {
        note.remove();
      }
      return hit;
    }

    /* 没选中任何药丸时返回原文，保证「界面表达不了」的设定不被覆盖 */
    function readRadio(name, map, rawValue) {
      var el = $('input[name="' + name + '"]:checked', detailPane);
      if (el && map[el.value]) return map[el.value];
      return String(rawValue || "");
    }

    function initiativeLevel(text) {
      text = String(text || "");
      var hit = 3;
      Object.keys(INITIATIVE).forEach(function (k) {
        if (INITIATIVE[k] === text) hit = Number(k);
      });
      if (text && !Object.keys(INITIATIVE).some(function (k) { return INITIATIVE[k] === text; })) {
        if (/几乎不|很少/.test(text)) hit = 2;
        else if (/很主动|主动找话题/.test(text)) hit = 5;
        else if (/常主动/.test(text)) hit = 4;
      }
      return hit;
    }

    function fromCard(card) {
      card = card || {};
      rawCard = card;
      var sp = card.speech || {};
      var rel = card.relationship || {};
      var ex = card.examples || [];

      setState({
        texts: {
          "f-name": card.name || "",
          "f-role": card.role || "",
          "f-address": sp.address || "",
          "f-catch": (sp.catchphrases || []).join("、"),
          "f-avoid": (sp.avoid || []).join("、"),
          "f-call": rel.call_user || "",
          "f-care": card.care || "",
          "f-never": (card.boundaries || []).join("、"),
          "d1q": (ex[0] && ex[0].user) || "",
          "d1a": (ex[0] && ex[0].reply) || "",
          "d2q": (ex[1] && ex[1].user) || "",
          "d2a": (ex[1] && ex[1].reply) || ""
        },
        radios: {},
        traits: card.traits || []
      });

      fillRadio("style", OPTIONS.style, sp.style);
      fillRadio("length", OPTIONS.length, sp.length);
      fillRadio("relation", OPTIONS.relation, rel.bond);
      fillRadio("mood", OPTIONS.mood, card.emotion_baseline);

      if (range) {
        range.value = String(initiativeLevel(rel.initiative));
        syncRange();
      }
      syncCount();
    }

    function toCard() {
      var raw = rawCard || {};
      var rs = raw.speech || {};
      var rr = raw.relationship || {};
      var t = {};
      $$("input.input, textarea.textarea", detailPane).forEach(function (el) {
        if (el.id && el.id !== "traitInput") t[el.id] = (el.value || "").trim();
      });

      return {
        name: t["f-name"] || "",
        role: t["f-role"] || "",
        traits: getTraitNames(),
        speech: {
          style: readRadio("style", OPTIONS.style, rs.style),
          address: t["f-address"] || "",
          length: readRadio("length", OPTIONS.length, rs.length),
          catchphrases: splitList(t["f-catch"]).slice(0, 5),
          avoid: splitList(t["f-avoid"])
        },
        relationship: {
          call_user: t["f-call"] || "",
          bond: readRadio("relation", OPTIONS.relation, rr.bond),
          initiative: (function () {
            var lv = range ? range.value : "3";
            // 滑杆没动过就保留原文，动了才覆盖成档位话术
            if (rr.initiative && String(initiativeLevel(rr.initiative)) === lv) return rr.initiative;
            return INITIATIVE[lv] || "";
          })()
        },
        care: t["f-care"] || "",
        boundaries: splitList(t["f-never"]),
        emotion_baseline: readRadio("mood", OPTIONS.mood, raw.emotion_baseline),
        examples: (function () {
          var out = [];
          if (t["d1q"] || t["d1a"]) out.push({ user: t["d1q"] || "", reply: t["d1a"] || "" });
          if (t["d2q"] || t["d2a"]) out.push({ user: t["d2q"] || "", reply: t["d2a"] || "" });
          return out;
        })()
      };
    }

    /* ---------- 宪法五项 ---------- */

    var IDENTITY_MAP = {
      "i-who": "who",
      "i-serve": "serve",
      "i-boundary": "address",
      "i-tone": "tone",
      "i-limit": "limits"
    };

    function loadIdentity(profile) {
      rawIdentity = profile || {};
      Object.keys(IDENTITY_MAP).forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.value = rawIdentity[IDENTITY_MAP[id]] || "";
      });
    }

    function toIdentity() {
      var out = {};
      Object.keys(IDENTITY_MAP).forEach(function (id) {
        var el = document.getElementById(id);
        out[IDENTITY_MAP[id]] = el ? (el.value || "").trim() : "";
      });
      return out;
    }

    /* ---------- 版本号与历史 ---------- */

    function setVersionUI() {
      var card = rawCard || {};
      if (versionTag) versionTag.textContent = "v" + version;
      var meta = $("#personaMeta");
      if (meta) meta.textContent = "人设 v" + version + " · 宪法 v" + identityVersion;

      // 摘要卡右下角的保存时间、历史行的版本小标：原来都是写死的
      var timeEl = $(".persona-card__meta span:last-child");
      if (timeEl && updatedAt) timeEl.textContent = fmtStamp(updatedAt) + " 保存";
      var histMeta = $('a[href="#/history"] .index__meta');
      if (histMeta) histMeta.textContent = "v" + version;

      var nameEl = $(".persona-card__name");
      if (nameEl && card.name && nameEl.firstChild) {
        nameEl.firstChild.textContent = card.name + " ";
      }
      // 摘要卡印章与页面标题也跟人设走（原来是写死的「澈」）
      if (card.name) {
        $$(".seal").forEach(function (el) { el.textContent = card.name.slice(-1); });
        document.title = card.name + " · 人设设置";
      }
      var lineEl = $(".persona-card__line");
      if (lineEl && card.traits && card.traits.length) {
        lineEl.textContent = card.traits.slice(0, 3).join("、");
      }
    }

    function fmtStamp(ts) {
      var d = new Date(ts || "");
      if (isNaN(d.getTime())) return "";
      var mm = ("0" + (d.getMonth() + 1)).slice(-2);
      var dd = ("0" + d.getDate()).slice(-2);
      var hh = ("0" + d.getHours()).slice(-2);
      var mi = ("0" + d.getMinutes()).slice(-2);
      return mm + "-" + dd + " " + hh + ":" + mi;
    }

    function renderVersions() {
      var box = $(".versions");
      if (!box || !window.DeskAPI) return;
      DeskAPI.personaHistory(20).then(function (r) {
        var items = (r && r.items) || [];
        // 后端 history 只存「被换下来的旧版」，当前版不在里面，
        // 所以这里补一行真正的当前版，否则会把上一版错标成「当前」
        var rows = [{
          version: version,
          name: (rawCard && rawCard.name) || "",
          updated_at: updatedAt,
          reason: "当前正在用的版本",
          updated_by: "",
          current: true
        }].concat(items.filter(function (it) { return it.version !== version; }));

        if (!rows.length) {
          box.innerHTML = '<p class="field__hint">还没有历史版本。</p>';
          return;
        }
        box.innerHTML = "";
        rows.forEach(function (it) {
          var row = document.createElement("div");
          row.className = "version" + (it.current ? " version--current" : "");

          var main = document.createElement("div");
          main.className = "version__main";
          var top = document.createElement("div");
          top.className = "version__top";

          if (it.current) {
            var dot = document.createElement("span");
            dot.className = "version__dot";
            dot.setAttribute("aria-hidden", "true");
            top.appendChild(dot);
          }

          var nm = document.createElement("span");
          nm.className = "version__name";
          nm.textContent = "v" + it.version + " · " + (it.name || "（未命名）");
          top.appendChild(nm);

          if (it.current) {
            var cur = document.createElement("span");
            cur.className = "tag tag--muted";
            cur.textContent = "当前";
            top.appendChild(cur);
          }

          var time = document.createElement("span");
          time.className = "version__time";
          time.textContent = fmtStamp(it.updated_at);
          top.appendChild(time);

          var note = document.createElement("p");
          note.className = "version__note";
          note.textContent = (it.reason || "人工保存") + (it.updated_by ? " · " + it.updated_by : "");

          main.appendChild(top);
          main.appendChild(note);
          row.appendChild(main);

          if (!it.current) {
            var btn = document.createElement("button");
            btn.className = "btn btn--ghost btn--sm";
            btn.type = "button";
            btn.textContent = "恢复";
            btn.addEventListener("click", function () { askRestore(it.version); });
            row.appendChild(btn);
          }

          box.appendChild(row);
        });
      }).catch(function () {});
    }

    function saveIdentity() {
      if (!window.DeskAPI) return;
      var next = toIdentity();
      var changed = Object.keys(next).some(function (k) {
        return String((rawIdentity || {})[k] || "") !== next[k];
      });
      if (!changed) return;
      DeskAPI.identitySave(next).then(function (r) {
        if (r && r.ok) {
          rawIdentity = next;
          identityVersion = r.version || identityVersion + 1;
          setVersionUI();
          toast("宪法已保存 · v" + identityVersion);
        } else {
          var err = (r && (r.error || (r.errors || []).join("；"))) || "校验没通过";
          toast("宪法没存上：" + String(err).slice(0, 40));
        }
      }).catch(function () { toast("宪法没存上：服务没响应"); });
    }

    function loadPersona() {
      if (!window.DeskAPI) return;
      DeskAPI.persona().then(function (r) {
        if (!r || !r.card) { toast("人设没加载出来"); return; }
        version = r.version || 1;
        identityVersion = r.identity_version || 1;
        updatedAt = r.updated_at || "";
        fromCard(r.card);
        loadIdentity(r.identity);
        setVersionUI();
        renderVersions();
        lastSaved = getState();
        if (dirty === false) markSaved();
      }).catch(function () {
        toast("连不上服务，人设没加载出来");
      });
    }

    /* ---------- 脏状态 ---------- */

    function markDirty() {
      dirty = true;
      if (savedState) savedState.dataset.state = "dirty";
      if (savedText) savedText.textContent = "未保存的改动";
      if (saveHint) saveHint.textContent = "有改动还没保存，保存后下一句话生效";
    }

    function markSaved() {
      dirty = false;
      if (savedState) savedState.dataset.state = "saved";
      if (savedText) savedText.textContent = "已保存 · v" + version;
      if (saveHint) saveHint.textContent = "改动保存后，下一句话就生效";
    }

    detailPane.addEventListener("input", function (event) {
      if (event.target && event.target.id === "traitInput") return;
      markDirty();
    });
    detailPane.addEventListener("change", function (event) {
      if (event.target && event.target.id === "traitInput") return;
      markDirty();
    });

    /* ---------- 折叠：身份宪法 ---------- */

    var accBtn = $("#identityToggle");
    var accPanel = $("#identityPanel");
    var accLabel = $("#identityToggleLabel");
    if (accBtn && accPanel) {
      accBtn.addEventListener("click", function () {
        var open = accBtn.getAttribute("aria-expanded") === "true";
        accBtn.setAttribute("aria-expanded", String(!open));
        accPanel.hidden = open;
        if (accLabel) accLabel.textContent = open ? "展开五项" : "收起五项";
      });
    }

    /* ---------- 注入文本预览 ---------- */

    function line(label, value) {
      var text = (value || "").trim();
      return label + "：" + (text || "—");
    }

    function buildInjection() {
      var state = getState();
      var t = state.texts;
      var r = state.radios;
      return [
        "【固定身份 · 优先级最高】",
        line("我是谁", t["i-who"]),
        line("我服务谁", t["i-serve"]),
        line("称呼与边界", t["i-boundary"]),
        line("语气与禁忌", t["i-tone"]),
        line("能力边界", t["i-limit"]),
        "",
        "【人设 · 当前编辑】",
        line("名字", t["f-name"]),
        line("身份", t["f-role"]),
        line("性格", (state.traits || []).join("、")),
        line("风格", r.style),
        line("长度", r.length),
        line("称呼", t["f-address"]),
        line("口头禅", t["f-catch"]),
        line("避免", t["f-avoid"]),
        line("对方的称呼", t["f-call"]),
        line("关系", r.relation),
        line("主动性", RANGE_LABELS[range ? range.value : "3"]),
        line("关心方式", t["f-care"]),
        line("情绪基线", r.mood),
        line("绝对不做", t["f-never"]),
        "",
        "【示范语气 · 只学语气，不当经历】",
        "对方：" + (t["d1q"] || "—"),
        "我：" + (t["d1a"] || "—"),
        "对方：" + (t["d2q"] || "—"),
        "我：" + (t["d2a"] || "—")
      ].join("\n");
    }

    function openPreview() {
      var pre = $("#previewText");
      if (pre) pre.textContent = buildInjection();
      openLayer($("#previewDrawer"), $("#copyPreview"));
      if (!window.DeskAPI) return;
      // 真预览：后端按当前表单校验 + 渲染，和下一轮实际注入的文本一致
      DeskAPI.personaPreview(toCard()).then(function (r) {
        if (!pre) return;
        if (r && r.ok && r.render) {
          pre.textContent = r.render;
        } else {
          pre.textContent = "校验没通过，先把这几处改掉：\n\n" +
            ((r && (r.errors || []).join("\n")) || (r && r.error) || "未知原因");
        }
      }).catch(function () {
        if (pre) pre.textContent = "预览没生成出来（服务没响应），上面显示的是本地拼的样子。";
      });
    }

    var copyBtn = $("#copyPreview");
    if (copyBtn) {
      copyBtn.addEventListener("click", function () {
        var pre = $("#previewText");
        var text = pre ? pre.textContent : "";
        function fallback() {
          var area = document.createElement("textarea");
          area.value = text;
          area.style.position = "fixed";
          area.style.opacity = "0";
          document.body.appendChild(area);
          area.select();
          try {
            document.execCommand("copy");
            toast("已复制注入文本");
          } catch (e) {
            toast("复制失败，请手动选择");
          }
          document.body.removeChild(area);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            toast("已复制注入文本");
          }, fallback);
        } else {
          fallback();
        }
      });
    }

    /* ---------- 确认弹层 ---------- */

    var confirmLayer = $("#confirm");
    var confirmTitle = $("#confirmTitle");
    var confirmText = $("#confirmText");
    var confirmOk = $("#confirmOk");
    var confirmAction = null;

    function askConfirm(opts) {
      if (!confirmLayer) {
        if (opts.onOk) opts.onOk();
        return;
      }
      if (confirmTitle) confirmTitle.textContent = opts.title;
      if (confirmText) confirmText.textContent = opts.text;
      if (confirmOk) {
        confirmOk.textContent = opts.okLabel || "确定";
        confirmOk.className = "btn " + (opts.danger ? "btn--danger" : "btn--primary");
      }
      confirmAction = opts.onOk || null;
      openLayer(confirmLayer, $(".modal-card__actions [data-close]", confirmLayer));
    }

    if (confirmOk) {
      confirmOk.addEventListener("click", function () {
        var action = confirmAction;
        confirmAction = null;
        closeLayer(confirmLayer);
        if (action) action();
      });
    }

    /* ---------- 五个底部操作 ---------- */

    function discard() {
      setState(lastSaved);
      markSaved();
      toast("已放弃改动，回到 v" + version);
    }

    function clearSkeleton() {
      if (!window.DeskAPI) return;
      DeskAPI.personaTpl(false).then(function (r) {
        fromCard((r && r.template) || {});
        markDirty();
        toast("已清空为骨架，记得保存");
      }).catch(function () { toast("模板没取到"); });
    }

    function fillExample() {
      if (!window.DeskAPI) return;
      DeskAPI.personaTpl(true).then(function (r) {
        fromCard((r && r.template) || {});
        markDirty();
        toast("示例已填入，改成你自己的说法再保存");
      }).catch(function () { toast("示例没取到"); });
    }

    function askExample() {
      askConfirm({
        title: "用示例填满表单？",
        text: "会覆盖当前所有字段。示例只是起点，保存前请换成你自己的说法。",
        okLabel: "填入示例",
        danger: false,
        onOk: fillExample
      });
    }

    function runAction(action) {
      if (action === "preview") {
        openPreview();
      } else if (action === "example") {
        askExample();
      } else if (action === "discard") {
        askConfirm({
          title: "放弃这次修改？",
          text: "会回到上次保存的 v" + version + "，没保存的内容不保留。",
          okLabel: "放弃",
          danger: true,
          onOk: discard
        });
      } else if (action === "clear") {
        askConfirm({
          title: "清空为骨架？",
          text: "会清空所有字段，只保留结构。清空后仍需保存才会生效。",
          okLabel: "清空",
          danger: true,
          onOk: clearSkeleton
        });
      }
    }

    ["previewBtn", "exampleBtn", "clearBtn", "discardBtn"].forEach(function (id) {
      var btn = document.getElementById(id);
      if (!btn) return;
      btn.addEventListener("click", function () { runAction(id.replace("Btn", "")); });
    });

    /* ---------- 窄窗口动作面板 ---------- */

    var sheet = $("#moreSheet");
    var moreBtn = $("#moreBtn");
    if (moreBtn && sheet) {
      moreBtn.addEventListener("click", function () {
        openLayer(sheet, $(".sheet__item", sheet));
      });
      sheet.addEventListener("click", function (event) {
        var item = event.target.closest("[data-act]");
        if (!item) return;
        var action = item.getAttribute("data-act");
        closeLayer(sheet);
        runAction(action);
      });
    }

    /* ---------- 保存新版本 ---------- */

    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        var nameEl = $("#f-name");
        if (nameEl && !nameEl.value.trim()) {
          toast("名字不能为空");
          nameEl.focus();
          return;
        }
        if (!window.DeskAPI) { toast("逻辑层没加载，刷新一下"); return; }
        if (!dirty) { toast("没有需要保存的改动"); return; }

        saveBtn.disabled = true;
        var card = toCard();
        // personaSave 内部已经做了「校验 → 保存 → reset 常驻实例」，
        // 这里别再单独调 resetLive，否则同一件事做两遍
        DeskAPI.personaSave(card).then(function (r) {
          if (!r || !r.ok) {
            var err = (r && (r.errors || []).join("；")) || (r && r.error) || "校验没通过";
            toast(String(err).slice(0, 46));
            return;
          }
          version = r.version || (version + 1);
          rawCard = card;
          setVersionUI();
          renderVersions();
          markSaved();
          lastSaved = getState();
          toast("已保存 v" + version + " · 下一句话生效");
          saveIdentity();
        }).catch(function () {
          toast("保存失败，服务没响应");
        }).then(function () {
          saveBtn.disabled = false;
        });
      });
    }

    /* ---------- 恢复历史版本 ---------- */

    /* history 只回版本号/时间/原因，不含卡片正文，所以只能走 rollback：
     * 它会用那一版重新生成一个新版本，当前版仍留在历史里。 */
    function askRestore(target) {
      askConfirm({
        title: "恢复到 v" + target + "？",
        text: "会用那一版生成一个新的人设版本（当前 v" + version + " 仍留在历史里）。",
        okLabel: "恢复",
        danger: false,
        onOk: function () {
          if (!window.DeskAPI) return;
          DeskAPI.personaRollback(target).then(function (r) {
            if (!r || !r.ok) {
              toast("恢复失败：" + ((r && r.error) || "未知原因"));
              return;
            }
            dirty = false;
            loadPersona();
            toast("已恢复到 v" + target + " · 下一句话生效");
          }).catch(function () { toast("恢复失败：服务没响应"); });
        }
      });
    }

    /* ---------- 路由：列表 / 详情 ---------- */

    function isWide() {
      return window.matchMedia("(min-width: 980px)").matches;
    }

    function route(focus) {
      var match = (window.location.hash || "").match(/^#\/([a-z]+)$/);
      var key = match && GROUPS[match[1]] ? match[1] : null;

      if (key) {
        sections.forEach(function (section) {
          section.classList.toggle("is-active", section.dataset.section === key);
        });
        if (detailTitle) detailTitle.textContent = GROUPS[key].title;
        if (detailSub) detailSub.textContent = GROUPS[key].sub;
        if (barTitle) barTitle.textContent = GROUPS[key].title;
        if (indexPane) indexPane.hidden = !isWide();
        if (detailPane) detailPane.hidden = false;
        root.dataset.view = "detail";
        if (backLink) backLink.href = isWide() ? "index.html" : "#/";
        if (backLabel) backLabel.textContent = isWide() ? "对话" : "设置";
        if (focus && routed && detailTitle) {
          if (detailPane) detailPane.scrollTop = 0;
          detailTitle.tabIndex = -1;
          try { detailTitle.focus({ preventScroll: true }); } catch (e) { detailTitle.focus(); }
        }
      } else {
        var active = sections.filter(function (section) {
          return section.classList.contains("is-active");
        });
        if (!active.length && sections.length) sections[0].classList.add("is-active");
        if (barTitle) barTitle.textContent = "设置";
        if (indexPane) indexPane.hidden = false;
        if (detailPane) detailPane.hidden = !isWide();
        root.dataset.view = "index";
        if (backLink) backLink.href = "index.html";
        if (backLabel) backLabel.textContent = "对话";
      }
      routed = true;
    }

    window.addEventListener("hashchange", function () { route(true); });

    var resizeFrame = null;
    window.addEventListener("resize", function () {
      if (resizeFrame) return;
      resizeFrame = window.requestAnimationFrame(function () {
        resizeFrame = null;
        route(false);
      });
    });

    syncRange();
    syncCount();
    route(false);
    lastSaved = getState();

    loadPersona();
    initTtsSettings();
    initMcpSettings();

    // 关窗/离开对话页时停掉语音，不让声音留在后台
    window.addEventListener("pagehide", function () { stopSpeaking(); });
  }

  /* ---------------- 按页面分派 ---------------- */

  if ($("#chat")) initChat();
  if ($(".popup-page")) initPopupPage();
  if ($(".app--settings")) initSettings();
})();