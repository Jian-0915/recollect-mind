# NeuroMem 进度快照

> 最后更新：2026-11-11
> 定位：**「人物叙事 + 情感陪伴」的专用记忆层**（非通用记忆系统）

---

## 一、状态：核心模块 9/9 全部完成，可运行

目录：`D:\neuro-memory`　主库：`data/neuromem.db`

---

## 二、模块清单（全部已实现并有测试）

| # | 模块 | 文件 | 测试 |
|---|---|---|---|
| 1 | 时间规范化 | `timeparse.py` | `test_time.py` |
| 2 | 四通道检索 | `recall.py` | `test_channels.py` |
| 3 | 统一 query | `query.py` | `test_query.py` |
| 4 | 人物档案（画像/别名合并） | `persona.py` | `test_persona.py` |
| 5 | 关系边（人-人） | `persona.py`+`relations` 表 | `test_relations.py` |
| 6 | 注意力/工作记忆/目标 | `working_memory.py` | `test_wm.py` |
| 7 | 元架构/阶段解锁 | `meta.py` | `test_meta.py` |
| 8 | 成长评测/定时代谢 | `evaluation.py` `sleep.py` | `test_eval.py` |
| 9 | 情感/自我叙事/漂移 | `emotion.py` `selfnarrative.py` `drift.py` | `test_final.py` |

基础：`config` `llm` `embed` `db` `store` `graph` `answer` `consolidator` `forgetting` `evolve` `memory` `cli`

---

## 三、运行的模型分工

| 任务 | 模型 |
|---|---|
| 抽取 / 巩固 | `qwen3.5:4b` |
| 生成回答 / 画像 / 关系 / 自我叙事 | `qwen3:8b`（4B 会输出 0000 退化） |

- Ollama 若停：`Start-Process "C:\Users\21839\AppData\Local\Programs\Ollama\ollama app.exe"`
- 8B 冷启动 ~20s

---

## 四、关键实现要点

1. **gist 忠实原文**（不信模型改写，仅替换"我"→对话者）
2. 四通道：语义 0.45 + 关键词 0.20(2-gram+停用词) + 图扩散 0.25 + 实体 0.05 + 时间(命中+0.25/否则×0.25)
3. keep_thr = top×0.78；召回去重（同 gist）
4. 巩固：向量≥0.55 且共享实体；概念再抽象 L1→L2
5. 漂移：相对模长 0.005，自相似 0.995+
6. 人称：对话者=你 / 他人=他她 / 自己=我
7. 防崩：回答退化校验+重试+兜底；注入≤5条；寒暄不入库

---

## 五、可用命令

```bash
python -m neuromem chat
python -m neuromem remember "小明昨天在公园被狗吓到。"
python -m neuromem recall "小明害怕什么？"
python -m neuromem ask "小明害怕什么？"
python -m neuromem query "昨天发生了什么？"   # 注意：CLI 暂未接 query，Python 用 Memory.query
python -m neuromem evolve / growth / concepts / consolidate / decay / list / stats / personas
```

---

## 六、待工程化（非核心）

- [ ] CLI 补 `stage` / `evaluate` / `persona` / `relations` / `query` 命令
- [ ] HTTP / Function Calling / MCP 接口（接入 agent）
- [ ] 人物演化版本、关系时间边界与强度
- [ ] 多用户隔离

---

## 七、恢复步骤

1. 读本文件 + `D:\CodeArts Agent\测试\成长记忆系统-讨论记录.md`（第 45 章）
2. `cd D:\neuro-memory`
3. `python -m neuromem stats` 确认库正常
4. 从"待工程化"挑一项继续---

## 更新 2026-11-11：前端可视化已完成（P1+P2）

- `neuromem/api.py`：只读 API（overview/events/concepts/personas/relations/graph/growth/meta）
- `web/index.html`：单页（ECharts 图谱 + 成长曲线，6 个 Tab）
- `python -m neuromem serve 8000` → 浏览器打开 `http://127.0.0.1:8000`
- 验证：首页 200、8 个 API 全 200
- 待做（P3）：编辑 / 删除 / 合并人物 / 手动加记忆 / 对话界面

---

## 更新 2026-11-11：P3 可写前端完成（增删改 + 对话）

### API 新增（`neuromem/api.py`）
| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/events` | 手动加记忆 → `store.remember()`，返回 `ids` |
| PUT | `/api/events/{id}` | 改 gist / place / time_text / occurred_at / affect |
| DELETE | `/api/events/{id}` | 删事件 + 级联清 bindings / edges / concept_links |
| POST | `/api/events/{id}/archive?archived=1\|0` | 归档 / 恢复 |
| POST | `/api/personas/merge` | `{keep, drop}` 合并重复人物（别名、记忆绑定、关系转移） |
| POST | `/api/ask` | `{query, user_name}` → `{query, events, summary}`（8B 生成） |

### 前端（`web/index.html`）
- 记忆 Tab：顶部"新增记忆"面板（textarea + 说话人 + 记住），表格加操作列（编辑 / 归档 / 删除）
- 人物 Tab：顶部合并面板（保留 / 并入 两个下拉），`confirm` 后调合并接口
- 新增"对话" Tab：输入问题 → 显示模型总结 + 前 5 条命中（附 score/confidence）+ 耗时
- 快捷键：对话输入 Enter 发送；新增记忆 Ctrl+Enter 提交；toast 反馈

### 验证（`tests/test_api_write.py`，全部 PASS）
```
ADD: {'ids': ['evt_e5d92a7c56a4']}
GIST: 今天下午和小红在咖啡馆聊了旅行计划，她说想去云南   # 中文存储正常
EDIT: gist/place 均生效
ARCHIVE: archived=1 生效；DELETE 后查不到
ASK: 12.7s，命中 4 条，summary="你们去过了公园和小巷。"
HTML 14054 字节，含"对话"与"合并人物"
```
- 经验：PowerShell 里 `Invoke-RestMethod` 回显中文乱码只是控制台 GBK 编码问题，库里是正常 UTF-8（用 Python 脚本验证）
- 服务：`python -m neuromem serve 8000`，当前后台运行，浏览器刷新即可看到新界面

---

## 修正 2026-11-11：关系幻觉（小明-小红）＋「我们」被替换

### 问题
- 46 章记录里的 `小红-同事->小明` 是**模型编的**：事件只有「小红拿到 offer / 升职」，两人无任何关系
- 用户在前端加「我和小明是好朋友，我们认识很久了」→ gist 变成「用户**们**认识很久了」

### 根因
`extract_relations` 完全信任模型输出：人物可以是句外的人，关系词可以是原文没有的（同库共现 → 脑补"同事"）。
`gist = seg.replace("我", speaker)` 把「我们」中的「我」也替换了。

### 修复（改在中心点，不在下游打补丁）
`persona.extract_relations` 写入前硬校验：
1. a、b 必须出现在原文（"我/自己"等代词按 speaker 计），否则丢弃
2. 关系词必须有原文支撑，`_REL_SYNONYMS` 同义组放行（"好朋友"→"朋友"）
3. 关系词不得当人名；`a == b` 丢弃
4. 同一对 `(a,b,type)` 去重，不重复插入

`_REL_PROMPT` 加 4 条硬规则：不得引入句外人、不得推断关系词、同句≠有关系、不确定输出 `[]`。

`store.remember`：`re.sub(r"我(?!们)", speaker, seg)`，「我们」保留原样。

### 验证
| 测试 | 关键结果 |
|---|---|
| `tests/test_rel_guard.py`（独立库） | 无关系词 → `[]`；「小红升职了，同事们都很替她高兴」→ `[]`；「小红升职了，小明为她高兴」→ `[]`（不再编"同事"）；「小明和小红是同事」→ 1 条；重复抽取不重复；「我是小明的好朋友」→ `用户-好朋友-小明`，且「我」没被建成人物 |
| `tests/test_rel_e2e.py`（正式库走 API） | 写入「小红升职了，小明为她高兴」→ relations 仍只有 1 条，删除事件后恢复 |
| `tests/test_pronoun_gist.py` | gist = 「用户和小明是好朋友，我们认识很久了」，无「用户们」 |

### 数据更正
- `evt_e4e18a7279f9` 的 gist 已修回「…我们认识很久了」
- 正式库 relations 现为 **1 条**：`用户 --好朋友--> 小明`；小明与小红无任何关系

---

## 初始化 2026-11-11：清库重建（出生状态）

### 做了什么
1. 停服务 → 清空 `data\` 全部 14 个文件（neuromem.db / smoke.db / agent.db / chat_sim.db / test_*.db / dump.txt / server*.log）
2. `db.connect()` 重建 `neuromem.db`（9 张表 + 4 个索引）
3. 新增 CLI 命令 `python -m neuromem init`：幂等，建状态表（`emotion_state` / `self_narrative`）并写入默认情绪，不清记忆
4. 重启服务 `python -m neuromem serve 8000`

### 初始化后的状态
```
stage   空白 (level -1, unlocked [])
score   0.0  (记忆广度/抽象能力/社会关系/活跃度 全 0)
events 0 / fragments 0 / bindings 0 / personas 0
edges 0 / relations 0 / concepts 0 / concept_links 0 / growth_log 0
emotion {valence:0, arousal:0, label:平静}
graph   nodes [] / links []
```

### 验证
- `tests/verify_fresh.py`：9 张表全 0、阶段 `空白/-1`、评分 0.0
- `tests/verify_fresh_api.py`：9 个端点全 200，空数组正常渲染，情感已初始化
- 首页 14054 B 正常

### 从零开始的用法
```bash
python -m neuromem init                       # 初始化（幂等）
python -m neuromem remember "今天和小明在公园放风筝"
python -m neuromem evolve                     # 巩固/概念/阶段/评分
python -m neuromem serve 8000                 # 前端
```
或直接在页面「记忆」Tab 手动加、在「对话」Tab 提问。

---

## 待实装：自动进化（设计已定，未写代码）

### 现状
- ✅ 演化算法完整：`evolve.tick()` = 巩固（聚类→概念）+ L1→L2 再抽象 + 遗忘衰减 + 成长快照
- ✅ 阶段/评分读时计算，数据一变自动反映
- ❌ 无自动触发：`sleep.run()` 无人调用、无 `/api/evolve`、无前端按钮、写入后不自动巩固
- ❌ `drift` / `emotion.decay` / `selfnarrative.build` 不在 tick 里
- 结论：**手动挡**

### 方案（详见讨论记录第 50 章）
1. **三触发源**：后台 daemon 线程每 `EVOLVE_INTERVAL=600s`；新增事件 ≥ `EVOLVE_EVENT_THRESHOLD=5` 异步补一轮（60s 防抖）；`POST /api/evolve` + 前端「进化」按钮
2. **并发**：SQLite 开 WAL + `busy_timeout=30000`，进程内全局 `threading.Lock`，单 worker；多进程用 `data/evolve.lock`
3. **接口**：`POST /api/evolve`（同步跑一轮）、`GET /api/evolve/status`（last_tick/next_tick/running/events_since，last_tick 取 `growth_log` 最新 ts）
4. **前端**：顶栏「进化」按钮 + 「上次代谢 X 分钟前」
5. **短路**：事件数 < `EVOLVE_MIN_EVENTS=3` 直接跳过，不调 LLM
6. **开关**：`NEUROMEM_AUTOEVOLVE=0` 可关后台，手动按钮仍可用
7. **挂进 tick**：`drift.drift(rate=0.005)`、`EmotionState.decay()`、`selfnarrative.build()`（有条件生成）

### 分期
- P1：`/api/evolve` + 前端按钮
- P2：后台定时线程 + status + WAL
- P3：阈值触发 + drift/emotion/自传挂载

### 验证计划
- 连写 6 条同主题记忆 → 不手动 evolve，120s 内自动出现概念
- 空库观察 10 分钟 → `growth_log` 不增长
- 并发 20 条写入 + 同时 tick → 无 `database is locked`

当前仍是手动：`python -m neuromem evolve`

---

## 2026-11-11：模型配置层（云端 API + 设置页）

### 问题
`llm.py` / `embed.py` 全硬编码 Ollama，没有 provider 抽象，前端也没有配置入口——没有本地模型就完全跑不起来。

### 新增/改动
| 文件 | 内容 |
|---|---|
| `neuromem/settings.py`（新） | `settings` 表存 `models`；DEFAULTS / 6 个 PRESETS（ollama/deepseek/qwen/moonshot/siliconflow/openai）；进程内缓存；api_key 掩码；`openai_base()` 自动补 `/v1` |
| `neuromem/llm.py` | provider 分支（ollama `/api/generate` / openai `/chat/completions`）；`_resolve_model()` 把角色常量映射为真实模型（**调用点零改动**）；思考模型 `content` 为空时自动用 `reasoning_effort=none` 等参数重试 |
| `neuromem/embed.py` | 双 provider；`stored_dim()` 读库内向量维度 |
| `neuromem/api.py` | `GET/PUT /api/settings`、`POST /api/settings/test`、`GET /api/settings/models`、`GET /api/health`；维度不一致返回 warnings |
| `web/index.html` | 「设置」Tab：两组卡片 + 6 个预设一键填表 + 保存/测试对话/测试嵌入/拉模型列表；key 以掩码显示 |

### 关键坑（已解决）
思考型模型（qwen3.5:4b）在 `/v1` 下 `think:false` **无效**：`content` 为空、全文落在 `reasoning`、`finish_reason=length`。
修法：检测到 content 空 → 依次尝试 `reasoning_effort:"none"` / `enable_thinking:false` / `chat_template_kwargs`，并把 `max_tokens` 抬到 ≥256 重试。实测只有 `reasoning_effort=none` 让它正常输出。

### 验证（全部 PASS）
```
test_settings.py        默认值 / 掩码 / /v1 补全 / 保存不覆盖未提交字段
test_cloud_path.py      用 Ollama 的 /v1 模拟云端（无需真 Key）
                        cloud-llm 1.6s "好的" · cloud-embed 2.2s dim=1024 · json {"ok":true}
                        恢复 ollama 后本地 llm/embed 回归正常
test_settings_api.py    6 预设 / health / 掩码 PUT 不覆盖 / 测试 0.56s+0.06s / 6 个模型
test_regress_after...   独立库：写入→gist 正确→检索 1 条→"是的，小明今天很开心。"
```
正式库仍为初始化状态（events 0 / stage 空白），测试全部用独立库。

### 安全提示
api_key 明文存在本地 SQLite（`settings` 表），前端只显示掩码。多用户或公网部署前应改为加密存储或环境变量注入。

### 用法
页面「设置」→ 选预设（如 deepseek）→ 填 API Key → 保存 → 点「测试对话」。

---

## 修复 2026-11-11：ask 500（预设串台 + 召回不降级）

### 现象
`POST /api/ask` 500；控制台 4 条 `Password field is not contained in a form`。

### 根因
1. 前端 `applyPreset` 把预设的 `provider/base_url` **同时**写进 llm 和 embed
2. DeepSeek 没有 embeddings 接口 → `401 Authorization Required ... /v1/embeddings`
3. `recall()` 里 `embed.embed(query)` 无 try/except → 异常上抛 → 500
4. 密码框裸露在 `div` 里（浏览器的表单语义提示）

### 修复
| 位置 | 改动 |
|---|---|
| `web/index.html` | 预设分离：只有预设自带 `model` 才覆盖 embed，deepseek/moonshot 只填 LLM 并提示 |
| `neuromem/recall.py` | `embed` 包 try/except → `qv=None`，向量通道归零，关键词/实体/时间/图扩散继续工作 |
| `neuromem/query.py` | 返回 `warnings`（embed_error / answer_error 转人话） |
| `neuromem/api.py` | `ask_api` 兜底，任何异常都返回 200 + `warnings`，不再 500 |
| `web/index.html` | 对话气泡显示 ⚠ 降级提示；字段包进 `<form onsubmit="return false">` + `autocomplete` |

### 验证
```
test_ask_resilience.py
  embed → api.deepseek.com（无 key）→ PUT 200
  ASK → 200，warnings=["嵌入服务不可用，已降级为关键词/实体检索：401 …"]   ← 不再 500
  llm 也指向 deepseek → ASK 仍 200
  恢复 ollama/ollama → ASK 200，warnings 空
页面检查：2 个 form / 2 个 password input / embed 覆盖受 if(p.model) 保护
回归：settings 接口全通；空库 9 个端点全 200
```

原则：**外部依赖失败必须降级，不能崩溃。** 向量通道挂了还有三条腿。

---

## 2026-11-11：前端 P0（最小两件）

### 1. 空库引导
零数据时「记忆」Tab 显示引导卡：输入框 + 「记住第一条」+ 成长路线（1 条→感知期 / 3 条→情节期 / 5 条+同主题→evolve 出概念）。

### 2. 事件详情抽屉
新增 `GET /api/events/{eid}`：全字段（**不下发 embedding**）+ `fragments` / `personas` / `concepts` / `neighbors`；不存在返回 `{ok:false}`。

前端点表格行 → 右侧 560px 抽屉（遮罩/Esc 关闭），显示 gist、时间/地点/情绪/显著/激活/置信度/来源/上次召回、涉及人物、所属概念、语义邻居、原文碎片，底部编辑/归档/删除。操作按钮加 `stopPropagation`，不会误触抽屉。

### 验证
```
test_event_detail.py（独立库 + monkeypatch api.conn）
  gist="今天和小明在公园放风筝，他很开心" place=公园
  affect={valence:1.0, arousal:0.5, label:开心}
  personas=['小明'] · fragments=4 条 · neighbors=[] · 无 embedding
  不存在 id → {ok:false}
页面 24311B，含「还是一片空白」「记忆详情」openEvent
GET /api/events/evt_nope → {"ok":false,"error":"not found"}
```

先不重构左导航/概览页，用最小改动验证效果。

---

## 2026-11-11：概览页 + 记忆筛选

### 后端
- `meta.report` 新增 `stages[6]`：`{name, level, desc, satisfied, met, have, need, unit, note}`，连续解锁语义不变（前一阶段未达成时后续 `satisfied` 强制 false）
- `GET /api/events` 支持 `?q=`（内容/地点模糊）、`archived=0|1|-1`、`persona=人名`（走 `edges involves` 子查询），SQL 参数化拼接

### 前端
- **新增「概览」Tab（默认首页）**：4 个 KPI、6 格发育阶段进度（done/current/灰）、能力雷达（4 维×100）、情绪罗盘（效价×唤醒散点）、最近 5 条记忆（点击开抽屉）、系统信息（provider/模型/维度/库大小/情感）
- **记忆 Tab 加筛选栏**：搜索框（Enter 触发）+ 状态 + 人物下拉 + 筛选/清空；空库走引导，有筛选无结果单独提示
- 图表重建前 `dispose()`，窗口 resize 一并响应

### 验证
```
test_overview_api.py
  stages: 感知 1/1 ✓ · 情节 1/3 · 概念 0/1 · 社会 1/2 · 自我 0/2 · 成熟 1/20
  events=1 · q 无命中=0 · archived=1 → 0 · persona=用户 → 1 · q 命中=1
  页面 31577B，含 概览/发育阶段/能力雷达/情绪状态/最近记忆/搜索内容
verify_fresh_api.py  9 端点全 200（改为结构自检，不再假设空库）
```

当前数据：1 条事件 / 1 人物 / 感知期 L0 / 评分 28.0。

---

## 修复 2026-11-11：拉模型列表 401 提示 + 写入兜底

### 根因
设置页点了 deepseek 预设但**没有保存**，表单 `api_key` 为空，「拉取模型列表」直接用表单值请求 `api.deepseek.com/v1/models` → 401。已保存的配置其实仍是本地 ollama。

### 修复
| 位置 | 改动 |
|---|---|
| 前端 `loadModelList` | 预检：云端 provider + 空 key + 非本地地址 → 直接提示「先填 Key 并保存；也可跳过列表手填模型名」 |
| 后端 `settings_models` | 401/403 → `{ok:false, need_key:true, error:"HTTP 401 未授权：该端点需要 API Key"}`；其它 HTTP 错误格式化 |
| 后端 `test_settings` | 错误含 401/Authorization → `need_key:true`，前端提示检查 Key |
| 后端 `POST /api/events` | 异常兜底 `{ids:[], error:"写入失败：…"}`；前端红色提示条 |

### 验证 `tests/test_config_errors.py`
```
云端无 key 拉列表 → 200 {ok:false, need_key:true}
本地 ollama 拉列表 → 200（6 个模型）
抽取模型坏 → 写入成功（extract 有 d={} 兜底，原文照存；测试事件已删除）
嵌入也坏 → 200 {ids:[], error:"写入失败：401 …"}   ← 不 500
恢复 ollama → 测试对话 0.39s / 嵌入 0.07s dim=1024
正式库仍 1 事件 / 1 人物
```

原则：配置类失败要告诉用户“下一步怎么办”，而不是甩原始 HTTP 异常。

---

## 修复 2026-11-11：无效 Key 的可读报错 + 跨进程配置同步

### 诊断结果
直接打 DeepSeek，两个模型名都返回：
```
401 Authentication Fails, Your api key: ****91fb is invalid
```
**Key 本身无效**，不是配置没生效。且 `deepseek-flash` 不存在（只有 `deepseek-chat` / `deepseek-reasoner`），已改为 `deepseek-chat`。

### 修复
1. `llm._error_detail()`：解析 `error.message`，按状态码附提示（401 Key 无效 / 404 模型名 / 429 限流），`_call_openai` 不再直接 `raise_for_status`
2. `embed._openai_embed()`：同样解析；401 提示「Key 无效或该服务没有 embeddings」
3. `api.settings_models`：401/403 带上服务端 message
4. 前端测试对话：`need_key` 时提示“去平台重新生成并保存”
5. 前端保存：base 含 deepseek 且模型名不在白名单 → 保存时警告
6. **跨进程配置同步（关键）**：`save()` 后 touch `data/settings.ver`，`current()` 比较 `st_mtime_ns`，变了就重载。之前 CLI/脚本改配置，uvicorn 进程一直用旧缓存

### 验证
```
test_settings_sync.py  子进程改 extract_model → 服务立即看到 → 恢复同步  PASS
test_bad_key.py        TEST  ok:false, need_key:true,
                       error 含 "Authentication Fails … is invalid（openai_compat/deepseek-chat）· API Key 无效或未生效"
                       MODELS ok:false, need_key:true, error = "Authentication Fails (governor)"
```

### 当前配置
```
llm   : openai_compat · api.deepseek.com · deepseek-chat · key 已填但无效
embed : ollama · bge-large · 1024 （保持本地，检索正常）
```
用户需换有效 Key；或点 `ollama` 预设保存回本地。

---

## 修复 2026-11-11：真因是“保存的 Key 开头被截断”

### 决定性证据
给 `key_info` 加 `head` 后看到：

```
{'set': True, 'len': 36, 'head': '5bf', 'tail': '****a3ef'}
```

DeepSeek 的 Key 必然以 `sk-` 开头，存进去的却是 `5bf...` → **开头被截断**，发出去的是残缺串，服务端只能判 invalid。与用户手上的 Key 是否正确无关。

### 连带的交互坑
旧实现把掩码 `****a3ef` 回填进 input。用户在末尾粘贴新 Key → 值变成 `****a3efsk-xxxx` → 后端看到 `****` 前缀判定“掩码回传”→ **保留旧值**。用户以为换了，其实没换。

### 修复
1. `settings.clean_key()`：去掉首尾空白 / 换行 / 零宽字符（`\u200b-\u200d\ufeff`）
2. `settings.key_info()`：返回 `{set, len, head(前3位), tail(****+后4位)}`
3. `openai_headers` 先 `clean_key` 再拼 `Bearer`
4. `PUT /api/settings`：`api_key` 空 / `****` 开头 → **不修改**；新增 `clear_key` 显式清空（同时修了 `in True` 导致的 500）
5. 前端：Key 输入框始终留空（placeholder 显示“已保存 5bf…****a3ef（36 位）· 留空不修改”）；打开设置页/保存时检测非 `sk-` 开头 → 红字警告；保存后显示 Key 头尾与长度；新增「清除已保存 Key」

### 验证 `tests/test_key_handling.py`
```
原始 Key: len=36 head=5bf tail=****a3ef
1 带空格/零宽 → 清洗为 15 位, head=sk-, tail=****1234
2 空字符串     → 不修改
3 ****1234     → 不修改
4 sk-another-5678 → 覆盖为 ****5678
恢复 → ****a3ef   PASS
页面 34807B 含 warnKeyShape / clearKey
```

### 用户操作路径
1. 点「清除已保存 Key」
2. 从 DeepSeek 控制台**重新复制完整 Key**（`sk-` 开头，35~36 位）
3. 粘贴 → 保存 → 核对提示里的尾号是否与手上一致
4. 点「测试对话」
5. 仍 401 就用 curl 直接验证（排除本系统）：
   ```
   curl.exe -s -o NUL -w "%{http_code}" https://api.deepseek.com/v1/models -H "Authorization: Bearer sk-完整key"
   ```
   200 有效；401 说明该 Key 本身被拒（禁用/欠费/未实名/非官方 Key）
6. 或点 `ollama` 预设保存回本地

---

## 2026-11-11：前端模块化 · 「模型」模块（配置 + 用量看板）

### 校正一条误判
- 用户重新粘贴完整 Key（35 位 / `sk-` / 尾号 8fbd）后**实测通过**（1.16s 返回 OK），confirm 之前的 401 是 `5bf` 开头那份截断 Key 造成的
- 另：`deepseek-flash` 实测**可用**，之前断言“DeepSeek 只有 chat/reasoner”是错的，已删掉前端那条误报校验

### 新增 `neuromem/usage.py`
```sql
usage_log(id, ts, kind(llm|embed), role, provider, model,
          ok, latency_ms, prompt_tokens, completion_tokens, error)
```
- `record()`：热路径，独立长连接 + **RLock**（可重入），异常全吞不影响业务
- `summary(days)`：`totals` / `by_day`（补齐空缺日）/ `by_model` / `recent 20`
- `clear()`
- 坑：初版用 `threading.Lock`，`clear()` 持锁后进 `_db()` 再次加锁 → 死锁（PUT 超时），改 `RLock`

### 埋点
- `llm.py`：`_call_ollama` → `(text, prompt_eval_count, eval_count)`；`_call_openai` → `(content, usage.prompt_tokens, usage.completion_tokens)`；`generate()` 记录 `role=extract|answer|custom`，成功/失败/超时都写
- `embed.py`：两条路径都返回 `(embs, tokens)`，`_embed_batch` 记录调用与延迟

### 接口
```
GET    /api/usage?days=7   → {totals, by_day, by_model, recent}
DELETE /api/usage          → 清空
```

### 前端「模型」Tab（取代原「设置」）
- **用量看板**：KPI（今日调用 / 今日 Token / 平均延迟 / 失败率+次数）、近 7 天柱线双轴图（调用 + Token）、「按模型」表（调用/失败/均延迟/Token）、「最近调用」表（时间/角色/模型/ms/状态）
- **配置区**：保留预设、Key 掩码、测试连接、清除 Key、维度警告
- `renderModels()` = 先 `renderSettings()` 渲染配置，再把用量块 `insertAdjacentHTML('afterbegin')` 插到最前，复用旧表单代码

### 验证 `tests/test_usage.py`
```
1 本地 llm 成功 + embed 成功
2 指向 127.0.0.1:9 制造失败
→ totals {calls:3, ok:2, failed:1, tokens:27, fail_rate:33.3}
  by_model 3 组 · recent[0].ok=false（error 含 WinError 10061）· by_day 今日 calls=3
页面 38021B 含 models / 今日调用 / 按模型 / 最近调用 / modelsChart / 清空用量
```
演示数据（清空后实跑）：`deepseek-flash` 2 次 90 tokens + `bge-large` 1 次 6 tokens，共 96 tokens。

---

## 2026-11-11：模块化 · 认知 / 人物 / 数据

### 认知（概念 Tab 重构）
- 后端：`GET /api/concepts/{cid}`（概念 + 来源事件）、`GET /api/self`（自我叙事）
- 前端：顶部「自我叙事」卡；概念按层级 L2→L1 分组卡片；点击开抽屉看层级/置信度/来源数 + 来源事件（可再点开事件详情，形成概念→事件下钻）

### 人物（详情 + 管理）
- 后端：`GET /api/personas/{pid}`（画像/别名/50 条事件/双向关系）、`POST /api/personas/{pid}/profile`（生成画像）、`PUT /api/personas/{pid}/aliases`（别名增删）
- 前端：人物卡片可点开抽屉，含画像 + 「生成/重新生成」、别名 pill + 编辑、关系列表、相关记忆时间轴

### 数据（备份 + 危险区）
- 后端：`GET /api/export`（9 张业务表 + 掩码后的模型配置，**剔除 embedding**）、`POST /api/reset {confirm:"RESET"}`（清业务表 + 用量，保留结构与配置）
- 前端：「数据」Tab —— 库大小 KPI、导出 JSON（Blob 下载）、危险区（必须输入 `RESET` + 二次确认）

### 验证
```
test_detail_module.py（独立库）
  概念 L1 / 2 条来源；人物 2 条事件 / aliases['小明']；别名写入读回 ['小明','明明']
  self 未生成 → null；不存在 id → ok:false
test_data_api.py（正式库只读）
  export 12 个键 · events 1 · personas 2 · 无 embedding · key 掩码
  reset(confirm 错) → {ok:false, 需要 confirm="RESET"}
  页面 45641B 含 data/危险区/导出/编辑别名/自我叙事/renderConceptsTab
```

### 当前导航
概览 / 记忆 / 概念 / 人物 / 关系 / 图谱 / 成长 / 对话 / 模型 / 数据

---

## 2026-11-11：视觉重构（设计系统 + 侧边导航）

### 旧版问题
10 个 Tab 挤在顶部、卡片只靠 1px 边框堆叠、字号层级只有三档、表格行线过密、雷达图在低数据时退化成"一条线"、散点图轴名被裁。

### 新设计系统
- **Tokens**：`--bg #0A0C12` / `--surface #141822` / `--surface-2 #1A1F2B` / `--line #262D3C` / `--fg #E8EBF2` / `--fg-2 #A7AEC2` / `--fg-3 #6E768D` / `--acc #6C8CFF` / `--ok #4FD1A5` / `--danger #FF7A7A` / 圆角 14·10
- **布局**：左侧 244px 固定侧栏（分组：观察/记录/认知/系统，10 个内联 SVG 图标，底部阶段/评分/记忆状态卡）+ 毛玻璃顶栏（标题/副标题/阶段徽章/评分徽章）+ `max-width:1400` 内容区
- **组件**：卡片 surface 分层、表格去竖线只留行分隔、按钮三态 32px 高、输入 focus 蓝色光晕、阶段卡 done/cur 状态、抽屉 600px + 遮罩 blur

### 专项修正
1. 雷达图 → **4 条能力进度条**（低数据时不再像渲染失败）
2. 散点图 `nameLocation:'middle'` + `nameGap`（轴名不再被裁）+ 十字虚线 + 光晕点
3. 系统信息对比度提升、`word-break` 防模型名溢出
4. 最近记忆卡补「查看全部」

### 验证
用 Edge headless 截图（`--headless --screenshot --virtual-time-budget=6000`）后做视觉分析：
```
侧栏/顶栏正常 · 能力维度确为 4 条进度条(2/0/20/100)
效价/唤醒轴标签居中未裁切 · 「平静」点带标签 · 无重叠/溢出/错位 · 整体协调
```
JS 逻辑零改动（沿用函数名与 `#view`），仅 `loadOverview` 改为写侧栏/顶栏状态。

---

## 2026-11-11：自动进化实装（P1 + P2 + P3）

### 配置
```
AUTOEVOLVE=True · EVOLVE_INTERVAL=600s · EVOLVE_EVENT_THRESHOLD=5
EVOLVE_MIN_EVENTS=3 · EVOLVE_MIN_GAP=60s
NEUROMEM_AUTOEVOLVE=0 可关闭
```

### 并发基础
`db.connect()` 增加 `PRAGMA journal_mode=WAL` + `busy_timeout=15000` + `connect(timeout=15)`，实测生效。

### 调度器 `neuromem/autoevolve.py`
| 函数 | 作用 |
|---|---|
| `run_once(force)` | 非阻塞抢锁 → 事件数不足则 skip → `evolve.tick()` |
| `status()` | enabled/interval/threshold/running/runs/skipped/last_run/next_run/last_result/events_since |
| `maybe_trigger()` | 写入后：达阈值 + 距上次 ≥60s → 后台线程补一轮 |
| `_loop()` | daemon 每 30s 检查，事件不足不空跑 |
| `start()/stop()` | 服务启动时拉起 |

### 接口
```
POST /api/evolve?force=true   GET /api/evolve/status
```
FastAPI startup 启动调度；`POST /api/events` 成功后调用 `maybe_trigger()`。

### 前端
概览 KPI 下方工具条：「自动进化 · 每 10 分钟 · 上次 … · 待处理 N 条（阈值 5）」+「进化一次」，toast 显示 新概念/升层/归档。

### 验证 `tests/test_autoevolve.py`
```
事件不足(2<3) → skipped + reason
强制一轮      → new_concepts=1（真的长出概念）
阈值分支      → maybe_trigger=True
并发锁        → "代谢进行中，稍后再试"
开关          → NEUROMEM_AUTOEVOLVE=0 → enabled=False
页面 55900B 含进化按钮
```
截图确认控制条正常、无排版问题。

### 实际效果
2 条同主题事件已聚出 **1 个概念**，阶段从感知期向情节期推进（2/3），评分 30.5 → 44.3。

---

## 状态盘点 2026-11-11（详见 `STATUS.md`）

完整进度表与 37 条缺陷已单独记录在 **`D:\neuro-memory\STATUS.md`**，此处只留摘要。

### 完成度
| 层 | 完成度 |
|---|---|
| 核心记忆机制 | ~90% |
| 模型接入 | ~92% |
| 管理界面 | ~75% → **85%**（含自动进化与视觉重构）|
| 自主运行 | ~85%（已实装，仅短期验证）|
| 工程化/交付 | ~45% |
| **综合** | **~85%** |

### 缺陷统计（共 37 条）
| 级别 | 数量 | 代表项 |
|---|---|---|
| P0 正确性/数据 | 8 | 编辑事件不重算 embedding；情感/自传/漂移未接线；图谱含归档事件；关系强度与时间边界未用 |
| P1 功能缺口 | 11 | 关系不可增改删；概念/人物不可删除；无分页与批量；无全局搜索；无导入 |
| P2 性能/并发 | 6 | recall 全表扫描 + N+1；无向量索引；多进程重复调度 |
| P3 安全/运维 | 7 | 无鉴权；Key 明文；RESET 保护弱；无备份/迁移版本 |
| P4 测试/工程 | 5 | 无 pytest/CI；测试依赖真实 LLM；无前端自动化 |

### 一句话
写、记、想、长、管 都已经通了；**差的是把已写的模块真正接上线（情感/自传/图谱过滤）、补齐管理动作（关系/概念/人物删除）、以及工程化收尾（pytest、鉴权、索引）。**

---

## 暂停点 2026-11-11（下次从哪继续）

- **完整缺陷清单与续接手册：`STATUS.md` 第 8 节「下次继续（Next Session Playbook）」**
- 下次第一件事：**P0-1 编辑事件后重算 embedding**（`api.edit_event` 里补 `embed.embed(gist)` 并写回）
- 环境：服务 8000（后台）、库 `data/neuromem.db`（WAL）、模型云端回答 + 本地嵌入
- 回归必跑：`test_settings_api` / `test_ask_resilience` / `test_autoevolve` / `test_data_api` / `verify_fresh_api`
- 当前数据：2 事件 · 1 概念 · 2 人物 · 1 关系 · 感知期 L0 · 44.3

---

## 2026-11-12：服务管理脚本

### 新增
| 文件 | 作用 |
|---|---|
| `service.py` | start / stop / restart / status（核心逻辑） |
| `service.bat` | 通用入口：`service.bat <action>` |
| `start.bat` / `stop.bat` / `status.bat` | 双击即用，带 pause 便于看输出 |

### 关键行为
- **就绪判定**：启动后轮询 `/api/health`（最多 45s），不是"进程起来就算成功"
- **安全停止**：先读 `data/service.json` 的 PID，再用 `tasklist` 确认是 python 进程，才 `taskkill /T /F`；PID 被系统复用时拒绝执行
- **端口回退**：状态文件缺失/失效时，用 `Get-NetTCPConnection` 找监听 8000 的进程
- **冲突提示**：端口被非本服务占用时明确报错，不盲目抢占
- **状态汇总**：运行状态 / PID / 端口 / 启动时间 / 日志路径 / 数据概览 / 自动进化轮次
- PID 落盘 `data/service.json`，日志追加 `data/server.log` + `server.err.log`

### 验证
```
status  → 运行中 · pid 29600（手工启动的实例，靠端口识别）
stop    → 停止中…(29600) → 已停止
start   → 已启动 · pid 27556
status  → 运行中 · 启动于 00:55:06 · 数据 2/1/2 · 进化 开启
restart → 停止 27556 → 启动 17440
service.bat status → 正常输出
```

---

## 2026-11-12：P0 修复（6 项）

### 修了什么
| 编号 | 问题 | 修法 |
|---|---|---|
| P0-1 | 编辑事件后仍用旧向量 | `api.edit_event` 改 gist 时 `embed.embed()` 重算并写回；同时删除旧 semantic 边；失败给 warning |
| P0-2 | 情感状态从不更新 | `store.remember` 末尾按本轮 affect 均值 `EmotionState.update()`；`evolve.tick` 每轮 `decay()` |
| P0-3 | 自传永不生成 | `build(conn, None)` 自动选事件最多的人物；新增 `POST /api/self/rebuild`；tick 每 3 轮生成；概念页加「重新生成」 |
| P0-4 | 图谱含归档事件 | `build_graph` 节点加 `archived=0`；并过滤"端点不在图里"的边（`add_edge` 会自动建节点） |
| P0-9 | 遗忘过快（40 分钟归档） | `DECAY_FACTOR 0.6→0.985`、`BONUS 0.002`（约两天）；恢复归档时 `activation=1.0` |
| P0-10 | 思考模型输出被截断 | 检测 `finish_reason=length` 且内容 <12 字 → 关思考重试，`max_tokens≥512`；自传/画像 220→400 |

### 验证
```
test_p0_fixes.py
  emotion      → 低落 (-0.438, 0.35)          ✅ 情感接线
  archived     → 图中无该事件（节点 2）         ✅ 图谱过滤
  edit         → embedding 变化 + semantic 边清零 ✅ 重算向量
  narrative    → "我是小明，我怕狗…"            ✅ 自传生成
  tick keys    → 含 narrative                  ✅
test_decay.py  → 10 轮 0.8747 / 300 轮归档      ✅ 遗忘曲线
restore_archived.py → 恢复 1 条，活跃 2         ✅ 误归档已还原
回归 test_autoevolve / test_settings_api / test_ask_resilience / test_data_api / verify_fresh_api 全 PASS
```

### 附带修复
- 回归脚本会把真实 provider 改回 ollama → 已手工恢复为 deepseek-flash（并在 STATUS 记 P4-6）
- `test_settings_api` 的 `data-tab="settings"` 断言过时 → 改为 `models`

### 仍待办
P0-5/6（工作记忆、drift 仍死代码）、P1-1（关系增删改）、P1-2/3（概念/人物删除）、P4-1（pytest）。

---

## 2026-11-12：P0-5/6 工作记忆接入 + 漂移决策

### P0-5 工作记忆（已接入）
- `working_memory.recent_ids(conn, hours=24, limit=6)`：最近 24h 内写入或召回过的活跃记忆
- `recall` 对工作台内记忆 `+0.08` 加成，返回项带 `working: true`
- 事件详情接口返回 `working`，抽屉显示「工作台」标签
- config：`WM_HOURS=24 · WM_CAPACITY=6 · WM_BONUS=0.08`
- `GoalManager` / `WorkingMemory` 保留为内存工具（无目标来源），文件头注明未接入

### P0-6 表征漂移（决策不接入）
- 不挂进 tick：每轮重写全部向量（O(N) 写放大），扰动降低检索稳定性
- `drift.py` 文档串标注「实验特性、默认未接入」，给出手动试验命令

### 验证
```
test_working_memory.py
  刚写入 2 条 → 都在工作台
  改成 2020 年 → 移出工作台
  插入 8 条 → 工作台仍为 6（容量生效）
  recall 结果带 working 字段
test_wm.py（旧）→ 仍 PASS（GoalManager/WorkingMemory 未破坏）
事件详情 working=True（正式库 2 条都在 24h 内）
test_ask_resilience → PASS，且恢复后 provider 仍为 openai_compat（新增断言防污染）
```

---

## 2026-11-12：P1-1 关系增删改

### 后端
| 端点 | 说明 |
|---|---|
| `POST /api/relations` | `{a,b,type,strength?,since?,until?,evidence?}`；人物不存在自动创建；反向重复只更新强度 |
| `PUT /api/relations/{id}` | 改 type/strength/since/until/evidence（strength 自动夹到 0~1） |
| `DELETE /api/relations/{id}` | 删除 |
| `GET /api/relations` | 补 `id / since / until / evidence`，按 rowid 倒序 |

校验：`a==b` 拒绝、空字段拒绝、空关系类型拒绝。

### 前端
- 关系页顶部面板：`A 下拉 — 关系词输入 → B 下拉 + 强度 + 添加关系`
- 表格加「操作」列（编辑 / 删除），空态文案改为引导
- 编辑用 prompt 改类型与强度，删除二次确认

### 验证
```
tests/test_relations_crud.py
  新增 → rel_6dac718136bc（updated=false）
  列表 → 含 id / since="" / evidence="manual"
  反向重复 → updated=true，同 id，强度 0.6→0.8
  编辑   → 朋友 / 0.9 / since=2026-01-01，updated=1
  校验   → a==b / 空人物 / 空类型 均拒绝
  自动建人 → 王哥 被创建
  删除   → ok，剩余 1 条；删除不存在返回 ok=false
回归 test_data_api / verify_fresh_api 全 PASS
页面 58796B 含 addRelation / editRelation
```
---

## 2026-11-12：P1-2/3 概念与人物删除

### 后端
| 端点 | 说明 |
|---|---|
| DELETE /api/concepts/{id} | 先删 `concept_links` 再删概念；不存在返回 `{ok:false,error:'概念不存在'}` |
| DELETE /api/personas/{id}?force=0 或 1 | 有事件绑定时返回 `{ok:false,need_force:true,error:'该人物还有 N 条关联记忆；确认要解除绑定并删除？'}`；`force=1` 才清 `involves` 边与 `relations` 后删人物 |

原则：**只删人物与其绑定，事件本身保留**；未加 force 不做破坏性操作。

### 前端
- 概念抽屉底部：「删除概念」（二次确认，提示会连带移除链接）
- 人物抽屉底部：「删除人物」（若返回 need_force，弹二次确认后带 `force=1` 重发）

### 验证
```
tests/test_delete_crud.py（独立临时库 + monkeypatch）
  概念删除：before 1 concept_links 1 → delete {'ok': True}
  人物 no-force：{'ok': False, 'need_force': True, 'error': '该人物还有 1 条关联记忆；确认要解除绑定并删除？'}
  人物 force：{'ok': True, 'unbound': 1}
  无绑定人物：{'ok': True, 'unbound': 0}

真实服务（pid 22800）：
  page 59667B，含 deleteConcept / deletePersona
  DELETE /api/concepts/c_nope → {'ok': False, 'error': '概念不存在'}
  verify_fresh_api → 空库 API 全部 200，PASS
  数据未变：2 事件 / 1 概念 / 2 人物 / 1 关系
```

---

## 2026-11-12：P4-1 pytest 迁移（核心 5 组）

### 基础设施
| 文件 | 作用 |
|---|---|
| `pytest.ini` | `testpaths=tests/unit`；`addopts=-q -m "not integration"`（默认跳过真模型用例） |
| `tests/conftest.py` | `memdb`：每个用例一个临时 SQLite；把 `db.connect()` 默认路径与 `api.conn` 都重定向过去，**绝不碰正式库**；`conn`：给断言用的独立连接 |

### 已迁移 `tests/unit/`
| 文件 | 用例 | 原脚本 |
|---|---|---|
| `test_relations_crud.py` | 5（新增/去重/编辑夹值/校验/自动建人/删除） | test_relations_crud.py |
| `test_delete_crud.py` | 3（概念级联、force 解绑、无绑定删除） | test_delete_crud.py |
| `test_decay.py` | 2（10/300 轮行为 + 参数区间断言） | test_decay.py |
| `test_working_memory.py` | 3 + 1 integration（recall 标 working 需模型） | test_working_memory.py |
| `test_p0_fixes.py` | 1 integration（情感/图谱归档/重算向量/自传/tick） | test_p0_fixes.py |

### 结果
```
python -m pytest          → 13 passed, 1 deselected in 18.65s
python -m pytest -m integration → 2 passed（working memory recall + P0 端到端）
```
旧 5 个同名脚本保留作为手动诊断入口（带 print），不再作为唯一验证手段。

### 后续
- `test_usage` / `test_data_api` / `test_ask_resilience` 等真实服务脚本仍未迁，可加 `integration` 标记后逐步转
- P4-2 mock 尚未做，模型相关用例仍受真实模型波动影响
---

## 2026-11-12：惊讶信号（预测误差）驱动巩固

### 设计
- `surprise = 1 - max_cos(新事件, 历史事件)`；空库第一条记 `1.0`
- 与 `salience` 严格分离：salience 是情绪强度（参与遗忘补偿），surprise 是预测误差（只决定「该不该学」）
- 触发条件 = 新增条数 ≥ 5 **或** 惊讶累积 ≥ `SURPRISE_TRIGGER`（2.0）；仍受 `EVOLVE_MIN_EVENTS` / `EVOLVE_MIN_GAP` 约束
- 用 `created_at > 上次 growth_log.ts` 聚合，跨进程一致，不依赖内存状态

### 改动
| 文件 | 改动 |
|---|---|
| `db.py` | `events` 新增 `surprise REAL`（schema + `_migrate` 补列） |
| `config.py` | `SURPRISE_TRIGGER = 2.0` |
| `store.py` | 写入时一次比对同时得 dup / best_sim / surprise；删除死函数 `_find_duplicate`、`_existing_embeddings`（原两次全表扫 → 一次） |
| `autoevolve.py` | 新增 `surprise_since_last()`、`content_triggered()`；`status()` 增 `surprise_since` / `surprise_trigger` |
| `web/index.html` | 事件详情加「意外度」；进化状态文案加「意外累积 x/2.0」 |
| `tests/backfill_surprise.py` | 历史事件回填脚本（可重复运行） |

### 验证
```
python -m pytest → 17 passed（新增 test_surprise.py 4 例）
回填正式库：evt_3140… = 1.000 / evt_2029… = 0.253
/api/evolve/status → surprise_since 0.0 · surprise_trigger 2.0
/api/events/{id}   → surprise 0.253
页面 59849B，含「意外度」「意外累积」
```

### 为什么这么设计
- 空库第一条 = 1.0 是语义正确（无可比对对象，一切皆新），但 `EVOLVE_MIN_EVENTS=3` 会挡住"刚出生就巩固"
- 不给 surprise 加 salience 权重：那会把"情绪强度"和"意外程度"混成一锅，已校准的遗忘曲线要重新验证；宁可两套信号并行
- 不做"高惊讶立即同步巩固"：写入路径不能再多一次 LLM 调用，保持写入 < 2s

### 后续可做
1. 惊讶加权聚类：consolidator 优先取高 surprise 事件做种子（让"意外"更可能变成概念）
2. 情绪调制行为：valence 低 → 召回偏负面 / 扩散跳数变化
3. 概念层面的惊讶：新概念与旧概念冲突时算"认知失调"，触发重新归纳
---

## 2026-11-12（下午）：认知闭环 + 数据扩容 + 安全加固

### 一、P0-7 关系真正参与认知
| 层 | 改动 |
|---|---|
| `persona.active_relations()` | 新增：过滤 `until < 今天` 与 `strength < 0.3`，返回 `a_id/b_id` 便于匹配 |
| `recall._relation_expand()` | 查询点名人物 → 顺有效关系扩到相关人物，其事件 `+0.18 × strength`，并作为扩散种子（0.5×强度） |
| `recall` 时间过滤 | 顺带修 P0-11：`occurred_at` 为空但 `created_at` 在范围内不奖不罚；两者都无才 ×0.5 |
| `answer.build_relation_block()` | 提示词注入「人物关系（强度/起止）」；无关系时写「（无）」 |
| `query._relation_context()` | `/api/ask` 返回 `relations`，只取与问题人物相关且仍有效的关系 |

实测：问「我和小明是什么关系」→ `relations=[用户—好朋友—小明, 0.5]`；问「阿哲最近怎么样」→ 召回 2 条事件并正常作答。

### 二、P0-8 / P0-12 画像与关系类型
- `merge()`：drop 的画像拼到 keep（去重、上限 400 字）
- `normalize_type()`：最好的朋友→好朋友、大学同学/同班同学→同学、老公/妻子→夫妻；抽取与手动新增都走它
- `tests/dedupe_relations.py`：同一对人只保留强度最高的一条（实测删 1 条，剩 3 条）

### 三、惊讶加权巩固 + 概念质量闸
1. `consolidator.run` 按 `surprise DESC` 排序 → 高惊讶事件先成簇种子
2. 概念置信度 = `0.55 + 0.4 × 簇内平均惊讶`（上限 0.95）
3. 三道闸：
   - 人物一致性：有主事件不与无主事件混；多人物簇直接不生成（防张冠李戴）
   - 空话过滤：`_VAGUE` 命中 ≥2 拒；命中 1 且超 30 字拒；以「人类/人们/社会/个体」开头拒
   - 陌生人名校验：概念里出现的已知人名必须在簇文本出现过，否则重试一次再拒
4. 簇上限 6 条（原来一锅炖 16 条 → 产出「他正在经历生活中的起伏与成长」）
5. L2：≥3 个概念、相似度 ≥0.78、与下级概念文本相似度 >0.8 视为复述丢弃

效果对比（同一批数据，重建概念）：
```
旧：用户通过构建神经记忆系统获得了强烈的成就体验（与阿哲事件混簇）
    他正在经历生活中的起伏与成长（16 条）
    人类在追求成就与情感联结的过程中…（L2 空话）
新：用户通过新工具提升工作效率
    妈妈关心用户饮食是否充足
    王姨用番茄感谢帮忙搬米的人（3 条，主语正确）
    L2：0 个（宁缺毋滥）
```

### 四、数据扩容
- `tests/seed_growth.py`：42 条经历（加班/友情/家人/焦虑/意外/小成就/邻里/感官）
- 结果：成功 41 条（1 条 `database is locked`，WAL 写-写互斥），用时 221s
- 惊讶度：均值 0.20、最高 0.34 → 说明中文嵌入相似度基线偏高（0.66~0.95），`SURPRISE_TRIGGER` 由 2.0 下调至 1.2
- 关系补抽：`tests/reextract_relations.py`（10 条含关系词，新增 1 条；其余 LLM 判定无关系）
- 当前：43 事件 · 7 概念 · 9 人物 · 3 关系 · 社会期 L3 · 评分 96.5

### 五、安全三件套
| 文件 | 作用 |
|---|---|
| `neuromem/auth.py` | 令牌文件存在即启用；`X-Token` / `?token=` / cookie 任一通过；首页与静态放行 |
| `neuromem/api.py` | HTTP 中间件，未授权返回 401 JSON |
| `neuromem/secret.py` | Windows DPAPI 封装，`dpapi:` + base64；非 Windows/失败自动降级 |
| `neuromem/settings.py` | save 前加密、load 后解密；旧明文兼容 |
| `service.py` | `token` / `untoken` 子命令；status 内部请求自动带令牌 |
| `web/index.html` | 从 URL 取 token 存 localStorage；401 时显示「如何带 token 打开」而非空白 |
| `tools/backup.py` | SQLite 在线备份 + zip，保留 7 份 |

实测：
```
no token  /api/overview : 401
with tok  /api/overview : 200
query tok /api/overview : 200
no token  /             : 200
DB 中 api_key 前缀      : dpapi:
读回                    : 35 位 sk-…
备份                    : 239.3 KB
```

### 六、事故与根因：模型配置被悄悄改回 ollama
- 现象：`settings` 表里 provider 变成 `ollama / qwen3:8b`，`/api/health` 也显示 ollama
- 根因：**服务进程有配置缓存**，只在 `data/settings.ver` 变化时重载；历史上某次脚本把 DB 写成 ollama 后，缓存一直掩盖着。今天测试里的 `settings.save(conn=临时库)` 触发了版本广播 → 缓存失效 → 暴露了真实库的旧值
- 修复两层：
  1. 数据：脚本恢复 `openai_compat / deepseek-flash`，Key 顺带转为 DPAPI 密文
  2. 代码：`settings.save()` 仅在写真实库（`conn is None`）时 `_touch_version()`，测试传临时连接不再惊动运行中的服务

### 七、回归
```
python -m pytest → 30 passed（28 单元 + 2 集成）
新增用例：test_relations_effect(7) · test_consolidator_surprise(2)
          test_security(4) · test_surprise(4, 阈值改 1.2 后修正边界)
页面截图：侧边栏 + 43 记忆 + 7 概念 + 社会期 L3 + 96.5，无鉴权提示（带 token 打开）
```

### 八、下一步
1. P1-6 对话多轮（现在单轮，上下文不延续）
2. P1-4 事件分页 + 批量操作（43 条尚可，100+ 会卡）
3. P2-1 recall 全表扫描 + N+1（500 条后再做）
4. 认知层：情绪调制召回（valence 低时偏向负面记忆）
---

## 2026-11-12（傍晚）：把 token 换成管理员账号

### 需求
> "不弄 token 了，弄一个管理员账号就可以了，可以自己修改用户名密码"

### 设计
| 项 | 选择 | 理由 |
|---|---|---|
| 密码存储 | PBKDF2-SHA256，20 万次迭代 + 16 字节随机盐 | 标准库自带，无新依赖；不可逆 |
| 会话 | `nm_session` Cookie，`username|exp|hmac` 签名 | 无状态，重启不掉线，不需要 session 表 |
| Cookie 属性 | HttpOnly + SameSite=Lax + 7 天 | 防脚本读取、防跨站提交 |
| 首次使用 | 页面弹「创建管理员账号」 | 不预置默认密码，避免 admin/admin |
| 改账号 | 页面左下角「账号」，需当前密码 | 改用户名/密码后重签会话 |
| 忘记密码 | `python service.py passwd` | 需要本机文件权限，符合"能碰文件就能救" |

### 实现
| 文件 | 改动 |
|---|---|
| `neuromem/account.py` | 新增：创建/校验/改账号/重置换密码/签名令牌 |
| `neuromem/auth.py` | 重写：白名单（首页、静态、`/api/health`、`/api/auth/*`），其余校验 Cookie |
| `neuromem/api.py` | 新增 `/api/auth/me|setup|login|logout|account`；401 返回 `{auth:true}` |
| `web/index.html` | 登录遮罩、账号设置弹窗、左下角「账号」入口、401 自动弹登录 |
| `service.py` | `token/untoken` → `account/passwd`；status 改为本地读库，不再需要凭据 |
| `tests/unit/test_security.py` | token 用例 → 账号用例（7 例） |

### 验证（端到端，真实服务）
```
1 me(未登录)  : initialized False, logged_in False
2 overview    : 401
3 setup       : {'ok': True, 'username': 'testadmin'}
4 me          : logged_in True
5 overview    : 200
6 改账号      : {'ok': True, 'username': 'boss'}（旧密码 test123456 → 新密码 newpass888）
7 logout      : ok
8 overview    : 401
9 旧密码登录  : 用户名或密码错误
10 新密码登录 : ok
11 错误密码   : 用户名或密码错误
```
截图确认：首次访问显示「创建管理员账号」卡片（用户名 / 密码 / 确认密码 / 创建并进入），遮罩正常，无错位。

### 清理
- 删除 `data/access_token.txt`（旧机制）
- 删除测试账号 `account.json` / `session.key`，交付状态为「首次访问由用户自己创建」

### 回归
```
python -m pytest → 33 passed（31 单元 + 2 集成）
```
---

## 2026-11-12（晚）：时间检索修复 + 数据扩容 + 两起事故

### 一、P0-13 时间查询（用户报告）
> 昨天搭建的系统，今天问「我昨天做了什么」→ 答「不知道」

三重原因，逐层修：

| 层 | 原因 | 修复 |
|---|---|---|
| 召回 | 时间窗内事件没有词面匹配，`+0.25` 加分被 `0.78×最高分` 阈值挤掉 | 窗内保底 `max(s,0.42)+0.12`，窗外 `×0.15` |
| 召回 | 概念无时间属性，混进时间段查询 | 有 `rng` 时跳过概念通道 |
| 回答 | 记忆块只给原文"今天"，模型据此判"不是昨天" | 行首输出 `[2026-11-11]`；无发生日期用 `[记于 …]`；prompt 解释日期语义与"用户说我=他自己" |

实测：
```
我昨天做了什么 → 你昨天第一次搭建了神经记忆系统，挺有成就感的。
今天做了什么   → 你今天骑电动车摔了一跤…还决定每周留一天不加班…
```

### 二、数据扩容
- 第一版生成器（随机交叉人物/地点）产出 39 条，其中 24 条语义荒诞（"奶奶在咖啡馆和客户吵架"）→ 写 `tests/prune_events.py` 按「强特征 + 时间前缀+人物+在」双条件清理，删除 24 条
- 第二版 `tests/simulate_growth.py`：**人物专属事件表**，30 条全部成功、0 失败，104s
- 当前：94 条记忆 · 18 概念（1 L2）· 15 人物 · 3 关系 · 88 条概念链接

### 三、事故 1：模型配置被空表单覆盖（P0-14）
- 现象：`settings` 写回 `ollama / qwen3.5:4b`，概念生成全变 `000000…`（4B 退化）
- 根因：页面未登录/加载失败时点「保存并立即生效」，`PUT /api/settings` 空表单直接覆盖
- 修复：`put_settings` 校验——空体拒绝、字段白名单、provider 白名单、切换 provider 必须带 base_url
- 恢复：脚本写回 `openai_compat / deepseek-flash`

### 四、事故 2：概念归纳期间阻塞写入
- 现象：批量写入 47 条失败 34 条（`database is locked`，重试也过不去）
- 根因：`consolidator.run` 循环内「生成 → 插入 → 下个簇生成」，第一条概念插入后事务未提交，LLM 调用期间一直持写锁
- 修复：每轮 LLM 生成前 `conn.commit()`（`run` 与 `run_concepts` 各一处）；`store.remember` 在抽关系前也先提交
- 效果：同样的 45 条写入，失败 34 → 0，耗时 518s → 167s

### 五、概念去重与退化拦截
- 近义概念合并：向量相似 ≥0.88 挂到旧概念（字面法判不了"借出的钱被拖"vs"借钱被拖"，试过 0.68 与 0.5+同主语，均无效）
- 退化输出拦截：`(.)\1{5,}` 连续重复字符直接拒绝（`000000…`）

### 六、回归
```
python -m pytest → 36 passed（新增 test_time_query 3 例）
时间问答真实调用 3/3 正确
```
---

## 2026-11-12（夜）：数据饱和度压测与性能修复

### 一、工具
| 脚本 | 作用 |
|---|---|
| `tests/load_test.py` | 注入 N 条（随机 1024 维单位向量），测 recall / graph / overview / events / meta 延迟 |
| `tests/load_concurrency.py` | 多线程并发读 + 叠加写入，统计吞吐、p95、错误数、写失败数 |
| `tests/load_evolve.py` | 大库跑巩固 + 遗忘，验证分批推进 |

### 二、基线 → 优化后
| 规模 | recall（优化前） | recall（优化后） |
|---|---|---|
| 2000 | 206 ms | 100 ms |
| 5000 | — | 258 ms |
| 10000 | — | 551 ms |

优化两项：
1. **去掉 N+1**：关键词只取 `gist/place`，不再逐条查 fragments
2. **numpy 矩阵化**：`_bulk_cos()` 一次算完全部余弦（原来逐条 Python 循环）

### 三、并发
5000 条、8 读线程 + 2 写线程、12 轮：
```
96 次请求 · 0 错误 · 80 次写 0 失败
p50 3.4s · p95 3.6s · 吞吐 2.3 req/s
```
第一版瓶颈是 `db.connect` 每次 `executescript(SCHEMA)` 抢写锁 → 改为 `PRAGMA user_version` 判断后，单请求延迟下降；并发 p50 仍 3.4s，属 Python GIL 限制（写入不变慢、不失败）。

### 四、巩固在超大数据下会卡（发现并修）
- 原因：聚类 O(N×簇数)，上万条全量跑会卡死；且按 `surprise DESC LIMIT 300` 时，若前 300 条都聚不成簇，后面永远轮不到
- 修复：
  1. 每轮最多 300 条
  2. 取批按 FIFO（`last_try_at` 升序 + `created_at`），保证推进
  3. 新增 `events.last_try_at`（schema v2），尝试过的孤儿事件 7 天冷却后才重试
  4. 批内仍按惊讶排序，高惊讶事件先做簇种子
- 实测 3000 条：每轮 0.4s，连续 4 轮未尝试数 3000→2400→2100→1800

### 五、回归
```
python -m pytest → 36 passed
正式库 94 事件 / 18 概念 / 15 人物（不受压测影响，压测全部用独立库并自动删除）
```

### 六、结论
- **1 万条以内**：单次召回 0.5s，概览 26ms，可用
- **并发**：稳定（不崩、不丢、不锁死），但单进程吞吐受限；多用户需多 worker + 进化文件锁
- **巩固**：分批推进，不会因为数据量大而卡住
- 下一步若继续上量：ANn 索引（sqlite-vec）、事件分页、前端虚拟列表
---

## 2026-11-12（夜）：P1-15 近因效应修复

- 现象：写入后立刻提问，回答说"没有细节"——回答块只取前 5 条，新记忆排在后面
- 修复：`answer.prioritize()` 把 1 小时内写入的记忆提到回答块第一位，再截断到 6 条
- 兼容：调用方传 naive 时间也能比较（`tzinfo is None -> astimezone()`）
- 验证：端到端同一问题 -> 「今天你在测试端到端流程，把所有链路都走了一遍」
- 回归：pytest 40 passed；测试残留已清理（95 -> 94）

---

## 2026-11-12（深夜）：记忆时效 + retrieval 接口 + 概念合并

### 一、记忆冲突与时效（P0-15）
- `events.superseded_by`（schema v3）：新记忆与旧记忆相似度 ≥ `SUPERSEDE_SIM=0.90` → 旧的标记「已被更新」，保留历史
- 召回默认只取最新（`superseded_by IS NULL`）；问「以前/原来/曾经」时才带出旧说法
- 冲突链只指向最新那条（比对时排除已被覆盖的事件）
- 测试：`test_supersede.py`（3 例）。阈值 0.90 是为了不误伤——0.95 已经被「完全相同去重」占用

### 二、`POST /api/retrieve`（P1-13）
- 入参 `{query, top_k}`，出参 `{events, concepts, relations, timings}`，**不调用 LLM**
- 每条记忆带 `score / scores.vec / scores.kw / kind / working / occurred_at`
- 实测 5000 条库 67~84ms，可直接给 agent 用
- 仍缺 MCP 包装

### 三、概念合并（P1-2 余项）
- `POST /api/concepts/merge {keep, drop}`：drop 的来源事件并入 keep（去重），然后删除 drop
- 概念抽屉加「合并到…」：列出其它概念，输序号即可；合并后回到列表

### 四、顺带修的：概念淹没具体记忆
- 现象：`/api/retrieve` 用 top_k=5，返回 5 条全是概念，刚写入的事件一条没有
- 原因：概念分普遍高于弱命中事件，合并排序后占满
- 修复：概念最多补 3 条（`out + con_items[:3]`），记忆优先

### 五、回归
```
python -m pytest → 45 passed（新增 supersede 3 + retrieve/merge 2）
python tests/e2e_flow.py → 32/32（新增 retrieve 接口一项）
正式库 94 事件 / 18 概念 / 15 人物（e2e 的测试事件已清理）
```
---

## 2026-11-12（深夜）：按最初九项清单逐条补齐（目标 + 漂移）

对照讨论记录 45.4 的九项：

| # | 原始功能 | 状态 |
|---|---|---|
| 1 | 时间规范化 | ✅ 已修（P0-13 时间窗） |
| 2 | 四通道检索 | ✅ 已扩到五通道 + 概念 |
| 3 | 统一 query | ✅ `/api/ask` + `/api/retrieve` |
| 4 | 人物档案 | ✅ 画像 / 别名 / 合并 / 删除 |
| 5 | 关系边 | ✅ 抽取 + 强度/时间边界参与检索 |
| 6 | 工作记忆 / 目标 | ✅ 本轮补目标（见下） |
| 7 | 元架构 / 阶段 | ✅ 6 期解锁 |
| 8 | 成长评测 / 定时代谢 | ✅ 四维评分 + 自动进化 |
| 9 | 情感 / 自我叙事 / 漂移 | ✅ 本轮补漂移（见下） |

### 一、目标系统（第 6 项）
- `neuromem/goals.py`：规则抽取「打算 / 计划 / 准备 / 决定」，写入时登记
- `goals` 表（schema v4）：id / text / status / created_at / done_at / source_event
- 目标驱动注意力：查询与目标 2-gram 重合 → 来源事件 +0.05（≥2 个 +0.08）
- 回答注入：「对方进行中的目标」进提示词
- 接口：GET/POST `/api/goals`、`/{id}/done`、DELETE；概览页可看可完成

### 二、表征漂移（第 9 项）
- 旧实现全量 O(N)、逐条独立噪声（破坏相对几何）→ 重写
- 每轮 5 条、同一方向整体平移、锚点（activation 前 3）不动、自相似 <0.995 刹车
- `drift_log` 记录；`evolve.tick` 已接入

### 三、回归
```
python -m pytest → 50 passed（新增 test_goals_drift 5 例）
python tests/e2e_flow.py → 36/36（新增目标 4 项）
正式库：94 事件 / 18 概念 / 15 人物 / 0 目标（测试目标已清理）
```
---

## 2026-11-12（深夜）：账号被 e2e 覆盖的事故

- 现象：`service.py account` 显示 `e2e_admin`，正式账号 `jian` 丢失
- 根因：e2e 备份后删除账号文件、跑完再恢复；若进程被管道截断杀掉，finally 未完成，下次运行时测试账号被当成"原账号"备份，覆盖了真备份
- 修复：
  1. 立即重建 `jian`（临时密码），清除测试账号
  2. e2e 增加自愈：发现残留 `.e2e-bak` 先还原
  3. 只在没有备份时才复制，避免覆盖好备份
  4. 无备份时明确删除测试账号并提示"请在页面重新创建"
- 验证：e2e 36/36 通过，跑完账号仍为 `jian`，无残留备份

---

## 2026-11-12（深夜）：遗忘改为分层

- 问题：单系数 0.985 下几乎所有记忆 1.5~2 天归档。单人长期使用，去年的事也会忘光
- 新规则（每轮 = 一次代谢，默认 10 分钟）：
  - 琐事（salience <0.1 + 不涉及具体人物 + 从未召回）→ 0.9934，约 2 天
  - 普通 → 0.99971，约 45 天
  - 重要（salience ≥0.6 或召回 ≥3 次）→ 0.99993，约 180 天
  - 30 天内被召回过 → 0.999995，几乎不动
  - 锚点（库里第一条记忆）→ 0.999999，基本不忘
- 新增 events.recall_count（schema v5），reinforce 时 +1
- 一次 executemany 更新（5 万条也不逐条 commit）
- 测试 6 例；真实库跑 5 轮：0 归档
- 归档仍不等于删除：退出检索/图谱，可在记忆页恢复

---

## 2026-11-13：MCP 接入（给 agent 用）

- 目标：不只是毕设，让 Claude Desktop / Cursor 能直接调这份记忆
- 	ools/mcp_server.py：stdio + JSON-RPC 2.0（MCP 2024-11-05），6 个工具
  - memory_retrieve / memory_remember / memory_ask / memory_person / memory_recent / memory_status
- 直接调 neuromem 模块，不走 HTTP，不需要账号；与网页服务并存（WAL）
- 	ools/mcp_smoke.py：模拟完整会话，6 项全 PASS
- README 给出 claude_desktop_config.json 配置；STATUS 新增 3.12

---

## 2026-11-13：RESET 清理范围修复

- 反馈：清空按钮后仍有 goals / drift_log / self_narrative / emotion_state 残留
- 原因：`reset_all` 只清了 9 张业务表，派生表与运行时状态没动
- 修复：
  - `scope=all`（默认）：清 12 张表（业务 9 + 派生 3），情绪重置为平静
  - `scope=derived`：只清 goals / drift_log / self_narrative，保留记忆
  - 前端危险区加「只清派生数据」，文案写明清理范围
- 测试：`test_reset.py` 3 例（all 全清 / derived 保留记忆 / confirm 校验）
- 实际执行：goals / drift_log / self_narrative 归零，emotion 回到 0/0/平静

---

## 2026-11-13：接口层 / 运维层三处修复（MCP 参数、WAL、时间与目标口径）

### 一、memory_status 无参调用报 `Invalid tool arguments JSON`（稳定复现 3 次）

- 定位：不在工具实现，在 `tools/call` 的参数层 —— 客户端可能把无参工具的 `arguments` 传成 `""` 或 `null`，服务端原写法 `params.get("arguments") or {}` 拿到字符串后再当 dict 用，直接崩
- 修复 `tools/mcp_server.py`：
  - `arguments` 兼容 缺省 / `null` / JSON 字符串；非 dict 一律按 `{}`
  - `memory_status` schema 增加可选 `verbose`（附目标数 / 漂移记录数），减少"零参数"形态
- 验证：`python tools/mcp_smoke.py` 6 项全 PASS（含无参 `memory_status`）

### 二、WAL 未及时 checkpoint

- 现象：主库 1.2 MB、`-wal` 4.2 MB，主库 mtime 停在 16:31 —— 数据全滞留在 WAL
- 根因：全项目都是短连接，没人主动 checkpoint；WAL 只在连接关闭或阈值触发时被动推进
- 修复：
  - `db.checkpoint()`：`TRUNCATE` 优先，被读者占用时看 busy 标志退化 `PASSIVE`（不阻塞写入）
  - `evolve.tick()` 末尾调用；`autoevolve._loop()` 另做 5 分钟独立维护（空库、纯读时期也收敛）
  - `tools/backup.py` 备份前先 checkpoint（备份包里不带 WAL）
  - `/api/evolve/status` 暴露 `wal_checkpoint_at`
- 实测：重启后 30 s 内 4164 KB → 0 KB

### 三、时间与目标口径

- 时间：相对时间词（昨天/前天/上周）以本地 `timeparse.parse_date()` 为准，模型给的 `date` 仅在本地失败时兜底 —— 修掉"昨天被标成 11-10"
- 目标：只看第一人称分句；第三人称不算目标（"小林下个月要搬走""表哥结婚让我当伴郎"→ 空），并去标点、剥前后缀
- 冲突并存（阿哲辞职/没辞职两条都在）**不改**：设计上保留说法变迁，`superseded_by` + 召回默认只取最新，`ask` 已能识别冲突

### 四、回归

- `python -m pytest -q`：57 passed
- `python tests/e2e_flow.py`：36/36（数据 39→39，e2e 自清理）
- `python tools/mcp_smoke.py`：6/6

---

## 2026-11-13：人物画像与关系"不自动生成"的两个根因

### 一、画像为什么一直不出来

- 查调用点：`build_profile()` 只在 `POST /api/personas/{id}/profile`（前端「生成画像」按钮）被调用
- 也就是说**从来没有自动触发**，不点按钮就永远是空画像 —— 不是"生成慢"，是"没人叫它"
- 修复：
  - schema v6：`personas.profile_at` / `profile_events`
  - `persona.pending_profiles()`：活跃经历 ≥3 条且无画像，或距上次画像新增 ≥5 条
  - `persona.build_profiles()`：每轮代谢最多 2 个（控制 LLM 成本），失败不标记、下轮重试
  - `evolve.tick()` 末尾调用；返回 `profiles`
  - 手动接口与自动路径共用同一写入（都会回填 `profile_at`）
- 结果：7 个人物里 6 个生成画像（奶奶只有 2 条经历，未到门槛），画像内容包含性格/喜好/关系/近况

### 二、关系为什么一条都没有

- 实测定位（同一句话，只截掉后半段）：

  | 输入 | 模型输出 |
  |---|---|
  | 小明是用户大学认识的好朋友 | `[{小明,用户,好朋友}]` |
  | 小明是用户大学认识的好朋友，性格开朗，喜欢打篮球 | `[]` |

- 根因：整句喂模型 + prompt 里"不确定就输出 []" → 长句带多个分句时模型直接放弃
- 修复：
  - 按 `，。；！？\n` 分句，逐句抽（只对含关系词的分句调用）
  - 模型全空 → 本地规则兜底（3 类明确句式），不猜、只认原文
  - 词表扩充（发小/嫂子/表弟/闺蜜/姥姥…，原先"发小"连 hint 都命中不了）
  - 称谓（表哥/妈妈/嫂子）可当人名；同事/朋友/同学 仍不能
  - 粘连片段过滤（挡住 `用户大学认识`、`娶的嫂子` 这类错误切分）、`大学室友→室友` 归一
- 结果（当前 39 条记忆）：4 条关系 —— 小明-好朋友-用户、阿哲-发小-用户、小林-同学-用户、嫂子-同学-表哥
- 端到端验证（写入 3 条"小周"）：第 1 条即生成 `小周-大学室友-用户`，第 3 条后自动出画像；验证数据已清理

### 三、回归

- `python -m pytest -q`：65 passed（新增 `test_profiles_relations.py` 8 例）
- `python tests/e2e_flow.py`：36/36（39→39）
- 库：39 事件 · 8 人物 · 4 关系 · 6 画像

---

## 2026-11-13：长句处理（细切 + 主语继承 + 时间上下文）

### 一、改前实测

| 输入 | 切分 | 抽取 |
|---|---|---|
| 4 句长文（每句 15~33 字） | 4 条（按句号） | who/when/where 基本准 |
| 55 字单句（关系+性格+爱好+计划） | **1 条** | 只抽到 who=小明，其余信息压在一起 |
| "第二天早上" | — | occurred_at 空 |

### 二、改动

1. `store.segment`：先按 `。！？\n` 成句；单句 >50 字再按 `，；` 细切，合并到 ≥12 字；尾巴 <12 字并回上一段
2. `store.remember`：同段主语继承（`carried`）；日期上下文（`ctx_date`）
3. `timeparse.parse_date(ctx=...)`：`第二天 / 次日 / 隔天 / 翌日` 按**上一条事件日期**推算
4. 去重加子串/长度约束：新句是旧句子串 → 合并；高相似但更短且非子串 → 视为新信息
5. gist 含对话者（"我"→"用户"）→ 自动补人物绑定

### 三、改后实测

- 55 字单句 → **3 条**（13 / 18 / 22 字），后两条 who 正确继承「小明」
- 4 句长文 → 4 条，第 4 条 `第二天早上` → `2026-11-05`（= 上周三 11-04 + 1）
- 全库补绑：gist 含"用户"但缺用户边的事件 **14 条**已修

### 四、事故与修复

- 验证脚本误删 1 条真实记忆：细切第 1 段被 0.95 判重 → `remember` 返回旧事件 id → 清理脚本把它当"本次写入"删了
- 已按原文重建（39 条恢复），并加**子串判定**：片段是旧句一部分就合并，不会反向删旧句

### 五、回归

- `python -m pytest -q`：**75 passed**（新增 `test_long_input.py` 10 例；`test_surprise` / `test_supersede` 桩函数补 `base_date`）
- `python tests/e2e_flow.py`：36/36
- 库：39 事件 · 8 人物 · 4 关系

---

## 2026-11-13：五个线上问题（含两个 P0 误伤）

### 一、P0：supersede 把"妈妈"标成被"做咖啡"取代

- 现象：`retrieve("妈妈")` / `ask("妈妈的性格")` 都查不到；数据在库里，只是被 `superseded_by` 过滤掉了
- 实测：两条记忆余弦 **0.9027**，但**共享人物 = 0** —— bge 中文长句基线太高，0.90 的阈值根本不够
- 修复：
  - 取代必须满足**同人物**（新旧事件 `involves` 有交集），纯相似不再生效
  - 否定/改口句（"其实/没有/不是/改成了…"）同人物 + 话题重合 ≥2 个 2-gram → 0.78 即可取代（真冲突只有 0.855，够不到 0.90）
  - 配置：`SUPERSEDE_SIM=0.90`、`SUPERSEDE_CORRECTION_SIM=0.78`
- 数据修复：清空误标 1 条，按新规则重建 → 正确标出「阿哲在银行工作 ← 阿哲其实没有在银行工作」（0.855）
- 验证：`retrieve("妈妈")` 1 条，`ask("妈妈的性格怎么样")` → "温柔。"

### 二、P0：画像变成 `0000000000000000000000000000000`

- 根因：模型退化输出，`build_profile` 只挡了空串，`"0"*31` 照样写库
- 修复：`_valid_profile()` 拒绝 全数字 / 重复字符（`len(set)<=2`）/ 不足 12 字 / 汉字 <8 个
- 数据修复：清掉 2 条脏画像（用户/阿哲），重跑后内容正常（"用户性格沉稳专注…"）

### 三、memory_status 无参仍报错

- 这是客户端框架解析空 `arguments` 的问题，服务端接不到请求
- 规避：schema 把 `verbose` 设为 **required**（默认 false），框架就不会生成空参数
- 服务端容错保留（缺省 / null / `""` 都能吃）；`mcp_smoke` 6/6

### 四、ask 的"取最新"是模型自判

- 库里两条平等并存（`superseded_by` 都空），模型按提示顺序挑，顺序一变结论就反转
- 修复：`answer.mark_conflicts()` 在拼 prompt 前按日期打标 —— `[较新说法]` / `[较早说法]`；prompt 规定以较新为准，无标注却矛盾时直说"有两种说法"
- 判定：2-gram 重合 ≥3，或新句带改口词且至少一个共同词

### 五、目标误判（"当伴郎""做按摩"）

- 根因：`^我?要(.*)` 太宽，任何"要"都算目标；`_clean` 又只挡 3 字以下
- 修复：「要」分支要求计划性动词（搬/考/面试/辞职…）或临近时间；`_TRIVIAL` 扩到"当伴郎/当伴娘/做按摩/买菜…"；2 字目标（搬家/考研）放开
- 数据修复：删掉历史误判 2 条（做按摩、当伴郎），现存目标只剩「回老家看望奶奶」

### 六、回归

- `python -m pytest -q`：**85 passed**（新增 `test_conflicts.py` 9 例）
- `python tests/e2e_flow.py`：36/36（13→13）
- `python tools/mcp_smoke.py`：6/6
- 库：13 事件 · 6 人物 · 1 概念

---

## 2026-11-13：按《问题清单》修 27 项（21 项已落地）

### 一、A 组 · 召回

- **A1 概念霸榜**：概念分 ×0.45（与事件同口径）、最多补 2 条、入选线 ≈cos 0.49；事件权重改 0.40/0.30/0.20
- **A2 问法敏感**：同义组扩展（结婚↔婚礼、猫↔宠物、吃↔做饭…）+ 空结果去问句壳重试（`query._slim`）
- **A3 概念当填充**：纯符号/emoji 直接返回空；概念分缩放后不再占前排
- **A4 依据来源**：部分（关系块仍无来源事件，未做）
- **A5 并发波动**：未发现 answer 层缓存；判定为模型采样波动，已在文档要求标注并发方式

### 二、B 组 · 生成

- **B1 幻觉**：依据池先由 A1 收窄，再加「最高分 <0.15 且零关键词 → 拒答」闸门
- **B2 含答案却拒答**：根因是 `_valid_answer` 把「团子」判成退化输出（`len(set)<=2`），已改为「≥3 字且全同字符」；实测「猫叫什么」→ 团子
- **B3/B5/B6**：prompt 增三条硬约束（第三方不得答成"你"、无日期不加时间状语、保留"前/旧/曾经"）
- **B4 张冠李戴**：焦点词闸门——人名/疑问词之外的实词不命中 → ×0.55
- **B7 聚合/计数**：未做

### 三、C 组 · 冲突与存储

- **C1 取代链**：新增 `store.rebuild_conflicts()`，每轮代谢按「同人物 + 更正词 + 话题重合」补标；分组按人物名（重复 persona 也能配），排除「用户」，单组取最近 40 条
  - 实测三条阿哲职业 → 两条旧说法被标，只剩「其实在银行上班…记错了」
- **C2 confidence**：按来源落库 0.9/0.6/0.8，低置信度且从未召回按琐事档淘汰
- **C3 漏绑**：写入时按库内人物名补绑
- **C4 泛称建实体**：朋友/同事/同学等不建具名人物
- **C6/C7 时间**：三个月前 / 去年 / 前年 / next week / last month / yesterday 全部可解析
- **C8/C9 反馈**：纯符号明确提示；区分「新增 / 已存在」
- **C5 关系总量**：部分（词表扩充后仍偏少）

### 四、D/E 组

- **D1**：MCP `memory_remember` 写后调 `autoevolve.maybe_trigger()`；新增跨进程锁 `data/evolve.lock`
- **D2/D3**：概念 prompt 强制保留专名与数字；新概念 7 天 ×0.85 冷静期
- **D4 评分**：未改（评分只反映规模/抽象/关系，不代表问答准确率）
- **E1**：`memory_recent` 支持 limit/offset/include_superseded，返回 total 与续页；实测 105 条 → 本次 20 + 提示 offset=20
- **E2**：`query` 返回 `degraded` 字段

### 五、基线（116 条库）

| 问句 | 改前 | 改后 |
|---|---|---|
| 用户养的猫叫什么名字 | 兜底「再问一次」 | **团子** |
| 阿哲现在做什么工作 | 依据 8 条、答"在银行上班…"混着旧说法 | 依据 1 条（最新说法）、明确说出更正过程 |
| 妈妈的性格怎么样 | 依据 5 条 | 依据 2 条 → 温柔 |
| 用户的工资多少 | 依据若干 | 拒答 |
| !!!@@##$$ | 概念填充 | 0 条 |

### 六、回归

- `python -m pytest -q`：**95 passed**（新增 `test_issue_list.py` 10 例）
- `python tests/e2e_flow.py`：36/36（116→116）
- `python tools/mcp_smoke.py`：6/6

---

## 2026-11-13：主题标签（旅游 / 工作 / 家庭…）

- 需求：写入记忆时能打标签，例如「旅游」
- 表：`event_tags(event_id, tag, source)`，schema v7，`tag` 建索引
- 自动打标：12 组主题词（旅游 / 工作 / 家庭 / 学习 / 健康 / 饮食 / 宠物 / 消费 / 居住 / 娱乐 / 情绪 / 社交），按命中数取前 3，不调模型；标签不参与召回打分
- 自定义标签：`remember(tags=[...])`；HTTP `POST /api/events {text, tags}`；MCP `memory_remember {tags}`
- 查询：`GET /api/tags` 统计；`GET /api/events?tag=旅游`；`/api/retrieve`、`/api/ask`、MCP `memory_retrieve / ask / recent` 都支持 `tag`
- 前端：记忆表格每行显示标签 chip；工具条加「全部标签」下拉；人物详情的事件也带标签
- 回溯：`tagging.backfill()`（幂等）→ 当前库 370 条里 235 条已打标，135 条规则无命中
- 分布：旅游 66 · 饮食 59 · 家庭 39 · 学习 35 · 社交 34 · 娱乐 13 · 居住 11 · 消费 10 · 工作 8 · 健康 7 · 宠物 5 · 情绪 3
- 实测：写「下个月准备去云南旅行，订了民宿和机票」→ 自动 `["旅游"]`；MCP `tag=旅游` 只返回带该标签的记忆
- 修过一个坑：概念条目没有标签，按 tag 筛选时概念通道必须关闭，否则会漏进无关概念
- 回归：**104 passed**（新增 `test_tagging.py` 9 例）、e2e 36/36

---

## 2026-11-13：实体链接（"我的猫" ↔ "团子"）

### 一、用户报的现象

- 问「我的猫怕什么」→ 答「没有关于你的猫怕什么的记忆」
- 库里有两条：「用户养了一只猫，名字叫团子」「团子特别怕吹风机，一开就钻到沙发底下」

### 二、诊断（数据说话）

```
查询词 kws   = ['猫怕']            # 2-gram 只剩这一个
焦点词 focus = ['猫怕']
目标事件：团子特别怕吹风机…        # 不含"猫"，也不含"猫怕"
换算：向量分 0.185，排第 10，被 keep_thr(0.192) 丢掉
```

- 不是句子切分问题，事件是完整句，字段也齐
- 不是"猫不是人物所以不能记忆"：事件里有「团子」，也绑定了人物
- 真因：**"猫"与"团子"在系统里是两个孤立符号**——没有人名表条目、没有类别边、向量也不认为它们相近

### 三、修复

1. 新表 `entity_alias(alias, target, kind)`，schema v8
2. 规则学习（写入时 + `alias.backfill()`）：
   - 「养了一只猫，名字叫团子」→ 猫 → 团子
   - 「团子是一只猫」→ 猫 → 团子
3. 检索时（`recall`）：
   - 查询里出现类别词 → 取它的 target
   - 把 target 的 `involves` 事件加入实体候选（`ent_ids`）
   - 关键词补 target 的字面词（"团子"），焦点词也补 target
   - **查询原文不改写**，返回的 `query` 仍是原句
4. 顺带收口：有强事件命中时，概念分按 `top1×0.6` 收敛，不再拿弱概念凑数

### 四、前后对比

| | 改前 | 改后 |
|---|---|---|
| recall | 4 条，目标排第 10 / 无 | 2 条，逗猫棒 0.639 + 怕吹风机 0.546 |
| ask | 没有关于你的猫怕什么的记忆 | **团子特别怕吹风机，一开就钻到沙发底下。** |
| 依据纯度 | 混入摄影课、书法概念 | 只有两条团子记忆 |

### 五、回归

- `python -m pytest -q`：**110 passed**（新增 `test_alias.py` 6 例）
- `python tests/e2e_flow.py`：36/36
- 已回溯学习：`猫 → 团子` 1 条

---

## 2026-11-13（补）：别名后续自动补充

- 写入路径已自动学（`store.remember` → `alias.learn`），新的「类别 + 名字」句一入库就能用
- 代谢每轮再加一层兜底：`evolve.tick` → `alias.backfill()`，覆盖导入 / 编辑 / 历史漏网
- 句式从 2 种扩到 6 种（实测都能学到）：
  - `咪咪是只猫` → 猫 → 咪咪
  - `旺财（狗）看家很凶` → 狗 → 旺财
  - `邻居送了只仓鼠，取名球球` → 仓鼠 → 球球
- 边界：库里只出现"猫"、从没出现过名字 → 无法自动学（信息不足）；用了全新叫法（"主子"）需手动补一次
- 回归：**112 passed**、e2e 36/36

---

## 2026-11-14：嵌入模型迁移 bge-large-en → qwen3-embedding:4b

### 一、迁移前

- 旧模型是 **bge-large-en**（英文模型跑中文）：报告实测 AUC **0.317**（倒挂）、hit@1 2/18、MRR 0.22
- 后果：同人物不同事实 cos 可达 0.9929 → `superseded` 误标 **191 / 709**，大量记忆"消失"

### 二、执行

| 步骤 | 结果 |
|---|---|
| 备份 | `data/backups/neuromem-20261114-091441.db.zip`（3.5 MB） |
| 停服务 | 防止重算期间写入旧维度向量 |
| 配置 | `qwen3-embedding:4b` / dim **2560** |
| 重算 | 709 事件（68.6 s）+ 110 概念（10.4 s），0 失败 |
| 清误标 | `superseded` 191 → 0，再按更正词重建 → **4 条**（阿哲职业链 3 + 表哥婚礼 1） |
| 阈值 | 取代 0.85 / 改口 0.76 / 去重 0.90 / 语义边 0.80 / 概念 0.30 |
| 显存 | `ollama stop qwen3:8b`（回答已走云端），4B 独占 GPU |

### 三、踩坑

- 第一版 `reembed.py` 逐条 `embed.embed()` → **5.2 s/条**（200 条跑了 17 分钟）
- 改为 `embed.embed_many()` 批量 32 条 → **0.09 s/条**，819 条 79 秒
- 去重阈值一度设 0.88，把"更正句"当重复吞掉 → 改 0.90，并让更正句跳过相似去重（必须入库才能标记旧说法）

### 四、迁移后基线（8 正例 + 4 负例）

| 问句 | 结果 |
|---|---|
| 用户用什么记笔记 | Obsidian ✅ |
| 用户最讨厌吃什么 | 香菜 ✅ |
| 用户在阳台种了什么 | 薄荷 ✅ |
| 表哥的婚礼改到哪一天 | 12月12号 ✅（依据 1 条） |
| 用户的猫叫什么名字 | 团子 ✅ |
| 阿哲现在做什么工作 | **咖啡馆 ✅**（修复前答"记不清"） |
| 用户怕什么 | 怕高、玻璃栈道腿软 ✅ |
| 用户的台灯是什么颜色 | 暖黄色 ✅ |
| 工资 / 火星 / 女朋友 / 下月出差 | 4/4 正确拒答（并区分"前女友"） |

- 延迟：1.5–2.3 s（含云端回答），检索侧 4B 编码 0.1 s 级
- 修复 "阿哲工作"：工作同义组补 `开店/咖啡馆/创业`，点名人物时焦点词惩罚从 ×0.55 放宽到 ×0.80

### 五、回归

- `python -m pytest -q`：**112 passed**
- `python tests/e2e_flow.py`：36/36
- 服务 pid 5416 · 709 事件 · 110 概念 · 25 人物 · 2560 维
