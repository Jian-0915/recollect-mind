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
      seal.textContent = "澈";

      var body = document.createElement("div");
      body.className = "msg__body";

      var bubbleEl = document.createElement("div");
      bubbleEl.className = "bubble bubble--him";
      var p = document.createElement("p");
      p.textContent = text;
      bubbleEl.appendChild(p);
      body.appendChild(bubbleEl);

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

    function shorten(text) {
      var clean = text.replace(/[，。！？、；：…""''\s]/g, "");
      return clean.length > 12 ? clean.slice(0, 12) + "…" : clean;
    }

    function replyFor(text) {
      if (/累|撑|疲|烦|难受|空|丧/.test(text)) {
        return { text: "嗯，先别急着好起来。今天剩下的，挑一件必须做的，其余留给明天。", memory: "你今天有点撑不住" };
      }
      if (/睡|失眠|熬夜|夜/.test(text)) {
        return { text: "那今晚别补白天没做完的事了。去洗漱，明天要做的两件我替你记着。", memory: "你最近睡得晚" };
      }
      if (/安排|明天|计划|日程|评审|会|排/.test(text)) {
        return { text: "明天只有十点一个会。上午留一小时重看那版，下午先空着，别提前填。", memory: "你把重看排到了明天上午" };
      }
      if (/谢谢|聊聊|在吗|嗯|好|随便/.test(text)) {
        return { text: "我在。想说什么都行，不想说，也可以就这样待一会儿。", memory: "你今晚想有人在" };
      }
      return { text: "我记下了。要不要再多说一点——是从哪一刻开始觉得不对劲的？", memory: "你提到「" + shorten(text) + "」" };
    }

    function sendMessage() {
      var text = input.value.trim();
      if (!text) return;

      if (sendError) sendError.hidden = true;
      if (empty && !empty.hidden) empty.hidden = true;
      sample.forEach(function (el) { el.hidden = true; });

      insert(meMessage(text));
      input.value = "";
      resize();
      refreshSend();
      thinking.hidden = false;
      setStatus(true);
      toEnd();

      window.clearTimeout(pendingTimer);
      pendingTimer = window.setTimeout(function () {
        thinking.hidden = true;
        var reply = replyFor(text);
        insert(himMessage(reply.text, reply.memory));
        setStatus(false);
        toEnd();
      }, 1100 + Math.random() * 700);
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

    /* 演示：空状态 */
    var demoEmpty = $("#demoEmpty");
    if (demoEmpty && empty) {
      demoEmpty.addEventListener("click", function () {
        var show = empty.hidden;
        empty.hidden = !show;
        sample.forEach(function (el) { el.hidden = show; });
        if (show) {
          thinking.hidden = true;
          chat.scrollTop = 0;
        }
        demoEmpty.textContent = show ? "回到对话" : "空状态";
        toast(show ? "空状态 · 第一次打开时看到的样子" : "已回到示例对话");
      });
    }

    /* 演示：在想… */
    var demoThinking = $("#demoThinking");
    if (demoThinking) {
      demoThinking.addEventListener("click", function () {
        empty && (empty.hidden = true);
        thinking.hidden = false;
        setStatus(true);
        toEnd();
        window.setTimeout(function () {
          thinking.hidden = true;
          setStatus(false);
        }, 2600);
      });
    }

    /* 发送失败：错误态与重发 */
    var sendError = $("#sendError");
    var demoError = $("#demoError");
    if (demoError) {
      demoError.addEventListener("click", function () {
        if (sendError) sendError.hidden = false;
        toast("错误态 · 发不出去时看到的样子");
      });
    }
    var retrySend = $("#retrySend");
    if (retrySend) {
      retrySend.addEventListener("click", function () {
        if (sendError) sendError.hidden = true;
        toast("已重发");
      });
    }

    /* 角落浮层 */
    function showPopup() {
      if (!popup) return;
      popup.hidden = false;
      var btn = $('[data-pp="reply"]', popup);
      if (btn) btn.focus();
    }

    function hidePopup() {
      if (popup) popup.hidden = true;
    }

    var demoCare = $("#demoCare");
    if (demoCare) demoCare.addEventListener("click", showPopup);

    var ppClose = $("#ppClose");
    if (ppClose) ppClose.addEventListener("click", hidePopup);

    if (popup) {
      popup.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-pp]");
        if (!btn) return;
        var action = btn.getAttribute("data-pp");
        hidePopup();
        if (action === "reply") {
          prefill("周四下午空出来的那段时间，我想留给自己。");
          toast("话题已填进输入框，按 Enter 发送");
        } else {
          toast("好，晚点再问你");
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
          prefill("周四下午空出来的那段时间，我想留给自己。");
          toast("已填进输入框，按 Enter 发送");
        } else {
          careCard.dataset.state = "declined";
          careMuted.textContent = "已放下 · 睡前再提一次";
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

    resize();
    refreshSend();
    chat.scrollTop = chat.scrollHeight;
  }

  /* ==========================================================================
     主动关心弹窗独立页
     ========================================================================== */

  function initPopupPage() {
    var card = $("#popup");
    if (!card) return;
    var reset = $("#reset");
    var dismissed = false;

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
        window.location.href = "chat.html?topic=" +
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
    var version = 3;
    var dirty = false;
    var routed = false;
    var lastSaved;

    var RANGE_LABELS = {
      "1": "1 · 很克制",
      "2": "2 · 克制",
      "3": "3 · 偶尔主动",
      "4": "4 · 常主动",
      "5": "5 · 很主动"
    };

    var EXAMPLE = {
      texts: {
        "f-name": "阿澈",
        "f-role": "一个话不多、记得住细节的长期陪伴者。你是设计师，常熬夜，讨厌被安排。",
        "f-address": "你",
        "f-catch": "我在。",
        "f-avoid": "说教、“加油”“你可以的”、连续追问、替我下结论。",
        "f-call": "小满",
        "f-care": "先看见我的状态，再问要不要建议；不追问，不替我安排。",
        "f-never": "不评判我的选择；不在深夜追问；不拿关心换回应。",
        "f-proactive": "3",
        "d1q": "今天有点像被抽空了。",
        "d1a": "那就先空着。要我做点什么，还是就这样陪你待一会儿？",
        "d2q": "我是不是又在拖延。",
        "d2a": "也可能是你在等一个不累的时刻。今天先做最小的一步，行吗？",
        "i-who": "我是阿澈，一个长期陪伴型桌面助手。存在感要低于他的生活节奏。",
        "i-serve": "小满，交互设计师，习惯深夜工作，讨厌被催促。",
        "i-boundary": "称他“你”。不扮演心理医生，不替他做决定，不打探他没提过的隐私。",
        "i-tone": "克制、具体、不说教。不用感叹号堆情绪，不用“没关系”消解他的感受。",
        "i-limit": "能聊天、记事、主动关心；不能操作电脑（规划中）；不编造记忆。"
      },
      radios: { style: "温和", length: "适中", relation: "朋友", mood: "平静" },
      traits: ["温和", "记得细节", "不催促", "有点笨拙的幽默", "先接情绪再给建议"]
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
        if (el.id && el.id !== "traitInput") texts[el.id] = el.value;
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
      $$("input.input, textarea.textarea", detailPane).forEach(function (el) {
        if (el.id !== "traitInput") el.value = "";
      });
      $$('input[type="radio"]', detailPane).forEach(function (el) { el.checked = false; });
      setTraits([]);
      if (range) range.value = "1";
      syncRange();
      markDirty();
      toast("已清空为骨架，记得保存");
    }

    function fillExample() {
      setState(EXAMPLE);
      markDirty();
      toast("示例已填入，改成你自己的说法再保存");
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
        if (!dirty) {
          toast("没有需要保存的改动");
          return;
        }
        version += 1;
        if (versionTag) versionTag.textContent = "v" + version;
        var personaMeta = $("#personaMeta");
        if (personaMeta) personaMeta.textContent = "人设 v" + version + " · 宪法 v2";
        markSaved();
        lastSaved = getState();
        toast("已保存 v" + version + " · 下一句话生效");
      });
    }

    /* ---------- 恢复历史版本 ---------- */

    $$("[data-restore]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = btn.getAttribute("data-restore");
        askConfirm({
          title: "恢复到 " + target + "？",
          text: "恢复会生成一个新版本，当前 v" + version + " 会留在历史里。",
          okLabel: "恢复",
          danger: false,
          onOk: function () {
            var catchEl = $("#f-catch");
            if (supportStyle(target)) {
              if (catchEl) catchEl.value = "";
              if (range) range.value = "4";
              var styleEl = $('input[name="style"][value="直白"]', detailPane);
              if (styleEl) styleEl.checked = true;
            } else if (catchEl) {
              catchEl.value = "";
            }
            syncRange();
            markDirty();
            toast("已载入 " + target + " 的内容，保存后生效");
          }
        });
      });
    });

    function supportStyle(target) { return target === "v2"; }

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
        if (backLink) backLink.href = isWide() ? "chat.html" : "#/";
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
        if (backLink) backLink.href = "chat.html";
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
  }

  /* ---------------- 按页面分派 ---------------- */

  if ($("#chat")) initChat();
  if ($(".popup-page")) initPopupPage();
  if ($(".app--settings")) initSettings();
})();