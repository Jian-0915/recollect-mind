# Recollect（中文说明）

> English: [README.md](README.md) ｜ 演进细节: [docs/DEVLOG.md](docs/DEVLOG.md)

**一个从空白出发、会记、会联想、会忘、也分得清"这是谁的事"的长期记忆层。**
SQLite 上的一层记忆机制，同时提供 MCP server、HTTP API + Web 管理界面、进程内 SDK 三种接法。

真实记忆、会话与 trace 不进仓库（见 `.gitignore`）；想先看效果，跑 `python tools/seed_demo.py` 灌一组虚构记忆。

---

## 关于「干净版」与 API Key

**本项目不内置任何账号、API Key 或私有模型端点。** 这是一家可以直接公开克隆的干净版本：

- **没有密钥**：源码里搜不到任何 `sk-` 开头的真实 Key。你自己的 Key 只保存在本机
  `data/*.db` 的 settings 表（Windows DPAPI 加密，`dpapi:` 前缀），而 `data/`、`*.db`
  已被 `.gitignore` 排除——所以克隆、提交、推送都不会带上它。
- **没有专属模型参数**：模型名与端点全部可通过环境变量覆盖，模板见 [`.env.example`](.env.example)。
  不设置时用一组通用的「本地 Ollama」默认值（见 `config.py`），与任何人无关。
- **首次引导**：首次打开（库里还没有任何记忆）时，概览页顶部会出现引导卡片，
  按你当前的模式给出下一步——本地 Ollama 用户看到拉模型命令，云端模式则提示填自己的 Key。
  一旦库里有记忆就自动消失；也可手动关掉。

两种用法任选其一：

1. **本地 Ollama（无需 Key，推荐先用）**：安装 Ollama 并 pull 下列模型，开箱即用。
2. **云端 OpenAI 兼容端点（商汤 SenseNova / DeepSeek / 通义 / Moonshot / OpenAI 等）**：在 Web「模型」页选预设或手填
   base_url、模型名，并填入**你自己的 API Key**。Key 只留在你本机。

> 想用环境变量预置模型名（不写 Key）：复制 `.env.example` 为 `.env`，改对应 `NEUROMEM_*` 值即可。
> 系统环境里的同名变量优先于 `.env`，方便 CI / 测试注入。`.env` 本身被 gitignore。

## 模型与获取

系统需要两类模型，二者可分别选本地或云端（推荐组合见文末）：

| 角色 | 作用 | 默认（本地） | 要求 |
|---|---|---|---|
| LLM 抽取模型 | 把对话提炼成结构化记忆、情感、画像 | `qwen3.5:4b` | 快、便宜、能出 JSON |
| LLM 回答模型 | 生成回答、人物画像 | `qwen3:8b` | 比抽取模型强，生成更稳 |
| 嵌入模型 | 把记忆变成向量，决定检索质量 | `qwen3-embedding:4b` | **维度必须和库一致（2560）** |

> 嵌入模型是整个系统里**最不该频繁换**的一环：换模型/换维度会让已入库的旧记忆无法被检索，
> 且余弦计算会静默按短维截断而不报错。除非愿意清库重灌，否则建议一直用本地 4B。

### 嵌入模型怎么选（推荐）

| 方案 | 模型 | 维度 | 适合场景 |
|---|---|---|---|
| **本地 Ollama（推荐）** | `qwen3-embedding:4b` | 2560 | 中文记忆检索最好、免费、数据不出本机；本项目默认 |
| 本地 Ollama | `bge-large:latest` | 1024 | 机器较弱、追求更快更省资源 |
| 通义千问 | `text-embedding-v3` | 1024 | 已在用阿里百炼，中文好、国内直连 |
| 硅基流动 | `BAAI/bge-large-zh-v1.5` | 1024 | 一个 Key 同时用云端 LLM 与嵌入 |
| OpenAI | `text-embedding-3-small` | 1536 | 全 OpenAI 技术栈、英文/多语言 |

在 Web「模型」页的「嵌入模型」卡片里，**点对应推荐按钮即可一键填表**（只填不保存），
确认无误再点「保存并立即生效」，最后用「测试嵌入」验证。换维度请先在「数据」页清库重灌。

> 商汤日日新 Token Plan 的 OpenAI 兼容网关（`https://token.sensenova.cn/v1`）**不提供 embeddings 接口**；
> 其「Piccolo Embedding」属于另一套大装置 API，需要单独的 Key 与端点。因此用商汤对话/抽取模型时，
> 嵌入请继续用本地 Ollama，不要在同一个 base_url 上调 `/embeddings`。

### 方案 A：本地 Ollama（免费、无需 Key，推荐先用）

1. 安装 Ollama：https://ollama.com （Windows/macOS/Linux，一路下一步）。
2. 拉取模型（首次会下载，嵌入模型约 2GB）：
   ```bash
   ollama pull qwen3-embedding:4b    # 嵌入，2560 维
   ollama pull qwen3.5:4b            # 抽取
   ollama pull qwen3:8b              # 回答（可选，追求更快可只用 4B）
   ```
3. 不用填任何 Key，直接 `python -m neuromem serve 8000` 即可。

> 注意：**换了嵌入模型必须维度一致**，否则旧记忆检索不到。默认库是 2560 维（4B）。
> 想换嵌入模型，请在「模型」页同步修改维度，或清库重建。

### 方案 B：云端 OpenAI 兼容端点（填你自己的 Key）

在 Web「模型」页点对应预设（会自动填好 base_url 和模型名），再粘贴**你自己的 API Key** 即可。
各平台均兼容 OpenAI 接口，只需 base_url + Key + 模型名三项：

| 平台 | 申请 Key（控制台） | base_url | 推荐模型（抽取 / 回答） | 备注 |
|---|---|---|---|---|
| **DeepSeek**（推荐） | https://platform.deepseek.com | `https://api.deepseek.com` | `deepseek-chat` / `deepseek-chat` | 国内直连、便宜，注册有额度 |
| **商汤日日新 SenseNova**（作者自用） | https://platform.sensenova.cn | `https://token.sensenova.cn/v1` | `sensenova-6.8-flash-lite` / `sensenova-6.8-flash` | 国内直连；注册送积分，Flash-Lite 有专属积分池 |
| 通义千问（阿里百炼） | https://bailian.console.aliyun.com | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-max` | 稳定，新用户有免费额度 |
| 月之暗面 Moonshot | https://platform.moonshot.cn | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | Kimi 系列 |
| 硅基流动 | https://cloud.siliconflow.cn | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` / `Qwen/Qwen2.5-72B-Instruct` | 一个 Key 试多种模型，有免费模型 |
| OpenAI | https://platform.openai.com/api-keys | `https://api.openai.com/v1` | `gpt-4o-mini` / `gpt-4o-mini` | 需海外网络与支付方式 |

**作者自用的模型搭配（商汤日日新）** —— 本项目实际调校用的组合，可直接照抄：

| 用途 | 模型 ID | 说明 |
|---|---|---|
| 抽取模型（extract） | `sensenova-6.8-flash-lite` | 快、积分消耗低，负责把对话提炼成结构化记忆 |
| 对话模型（answer） | `sensenova-6.8-flash` | 生成回答与人物画像更稳 |

在 Web「模型」页点 `sensenova` 预设，会自动填好 base_url 和这两个模型名，只需粘贴**自己的 Key**。
Key 在 https://platform.sensenova.cn 控制台 → 「API Keys」创建（`sk-` 开头）。

> Key 获取通用步骤：注册账号 → 进控制台「API Keys / API 密钥」页 → 创建并复制（通常只显示一次，务必当场保存）。
> 平台名 / 模型 ID 可能随时间调整，以各平台控制台当前显示为准；报错 `model not found` 时去模型列表页复制准确 ID。

**推荐组合**：入门用「本地 Ollama 全家桶」零成本跑通；要更强生成质量，用
「`sensenova-6.8-flash-lite` 抽取 + `sensenova-6.8-flash` 对话」（作者自用、国内直连、一个 Key 搞定），
或「DeepSeek 抽取 + 回答」。嵌入模型建议始终用本地 4B，避免云端嵌入的维度与费用问题。

## 依赖

- Python 3.11+
- Ollama（本地），模型：
  - `qwen3.5:4b`（LLM：抽取 / 情感；也可在管理界面切到 OpenAI 兼容的云端模型）
  - `qwen3-embedding:4b`（嵌入，**2560 维**，中文记忆检索显著优于 `bge-large`）

```bash
ollama pull qwen3-embedding:4b
pip install -r requirements.txt
```

> 换嵌入模型必须**重建全库向量**：维度不同则旧向量无法参与比较，且余弦计算会静默按短维截断而不报错。
> 当前库已全量迁移到 4B；管理界面的「Ollama 预设」现在从 `config.py` 读取模型与维度，不要再手工写回 1024。

## 结构

```
neuromem/
  config.py   配置（模型、路径、召回/遗忘/惊讶/进化参数）
  llm.py      Ollama / OpenAI 兼容 LLM 封装（默认关思考、限输出、强制 JSON、超时）
  embed.py    文本嵌入（qwen3-embedding:4b / 2560 维）
  db.py       SQLite 数据层（events/fragments/personas/concepts/companion_state/adoption_log/memory_versions/identity，schema v13）
  identity.py L0 固定身份层：人工版本化、运行时只读、清库豁免、subject 标签 + 主体解析
  store.py    写入：分割→抽取→归一→绑定→情感→索引→建边→惊讶
  graph.py    构图 + 扩散激活
  recall.py   召回：向量 + 实体 + 图扩散 + 融合 + 可信度分层
  consolidator.py 巩固：实体聚类 → 概念归纳 → L1 → L2
  forgetting.py   遗忘：激活衰减 + 显著性补偿 + 归档
  working_memory.py 工作台：24h 窗口 / 容量 6 / 召回加成
  autoevolve.py   自动进化：定时 / 条数 / 惊讶累积 / 手动
  evolve.py       一轮代谢：巩固 + 再抽象 + 遗忘 + 自传
  companion.py 陪伴层：情绪/自叙事/画像/会话态 + 主动浮现
  adoption.py  采纳反馈：adoption_log + 按采纳强化（曝光 ≠ 采纳）
  affect.py    v13 皮层情感：八维离散情绪 / 慢心境 / 按人关系情感 / 能量 / 日志
  brain.py     v13 Brain：ContextPack + turn()/commit()，陪伴 agent 的统一入口
  cortex.py    v13 扩展总线：BrainModule 生命周期 + 插件注册表
  memory.py    Memory SDK：agent 进程内直连接口（L0 人工写入口之一）
  cli.py      命令行
agent/          陪伴 agent 实验台（进程内直连 Brain，不走 MCP）
  companion.py  一轮完整链路：turn→LLM→自主 used_ids→remember→commit
  prompts.py    结构化输出契约 + 对照组提示词
  bootstrap.py   实验库引导：主库配置只读复制、运行时指向实验库
  report.py     真实使用数据验收（行为指标 + 库内状态 + A/B 对比）
  script.py     可复现剧本（default 18 轮 / smoke 4 轮）
  __main__.py   script / chat / report 命令
```

> 惊讶（prediction error）= `1 - 与历史记忆的最大余弦相似度`，写入时落库。
> 意料之外的事会更快触发巩固，重复的日常靠条数阈值——两种信号分开，不互相干扰。

## 用法

```bash
# 初始化（幂等：建表 + 状态表 + 默认情绪，不清已有记忆）
python -m neuromem init

# 记忆（可多次）
python -m neuromem remember "小明昨天下午在公园被狗吓到了，他很害怕。"

# 召回
python -m neuromem recall "小明害怕什么？"

# 查看 / 统计
python -m neuromem list
python -m neuromem stats
python -m neuromem personas

# 管理界面（http://127.0.0.1:8000）
python -m neuromem serve 8000
```

## 测试

```bash
python -m pytest                  # 单元：临时库，20s 内跑完，不碰正式数据
python -m pytest -m integration   # 真模型用例（约 1~2 分钟）
python tests/smoke_test.py        # 旧的手工冒烟脚本（保留）
python tools/check_reset.py       # 清库覆盖 + MCP 空库自检（只读主库，跑副本）
python tools/check_v12.py          # v12 迁移 / 固定身份豁免 / MCP 端到端（只读主库，跑副本）
python tools/check_v12.py --full  # 额外跑 MCP 写入与 L2 回传（需 Ollama）
python tools/check_v13.py         # v13 主体翻转 / 情感 / Brain / Cortex / 清库 / MCP（只读主库，跑副本）
```

> 注：默认用本地 `qwen3.5:4b` 抽取（开箱即用，无需 Key）。若你在「模型」页切到云端端点，
> 抽取/回答模型以你填的为准，Key 也用你自己的。部分模型开 think 遇悖论会卡死或跑偏，
> 因此**云端思考默认关**（`think=False`，可在设置页单独打开），并对输出长度与超时做了限制。

## 账号与备份

```bash
python service.py account    # 查看管理员账号
python service.py passwd     # 忘记密码时重置（CLI，需本机权限）
python tools/backup.py       # 在线备份到 data/backups/，默认保留 7 份
```

- 首次打开页面会要求**创建管理员账号**（用户名 + 密码，至少 6 位）
- 之后每次打开需登录；用户名 / 密码在页面左下角「账号」里修改（需输入当前密码）
- 密码以 PBKDF2-SHA256 哈希存储（20 万次迭代 + 随机盐），会话是 HttpOnly 签名 Cookie，7 天有效
- API Key 用 Windows DPAPI 加密存库（`dpapi:` 前缀），换用户/换机器解不开时会提示重填

## 清库与重训（MCP / HTTP 共用同一个后台）

HTTP 服务、`tools/mcp_server.py`、`Memory` SDK 都通过 `config.DB_PATH` 连**同一个 SQLite 文件**；MCP 每次工具调用新建短连接。所以在 HTTP 端清库后，MCP 下一次调用立刻看到空库，**无需重启 MCP**（个别宿主若缓存了旧进程，重连是为了加载新代码，不是为了刷新数据）。

三种可直接用的清库方式：

```bash
# 1) 管理界面：「数据」Tab → 危险区 → 输入 RESET → 清空数据（推荐）
# 2) 命令行（不经 HTTP，作用于同一个库）
python -c "from neuromem import api; print(api.reset_all({'confirm':'RESET'}))"
# 3) HTTP：登录后浏览器控制台执行
#    fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},
#      body:JSON.stringify({confirm:'RESET'})}).then(r=>r.json()).then(console.log)
```

清库前可先只读预检：`GET /api/reset/plan?scope=all` 返回 `will_clear` / `will_keep` / `rows`，确认新表都在清单里。

请求体：`confirm` 必须是 `"RESET"`；可选 `scope:"derived"`（只清目标 / 漂移日志 / 自叙事，保留记忆与使用痕迹）、`vacuum:true`（物理重建 DB 文件，需独占，失败不影响逻辑清库）、`keep:["某张表"]`（额外保留）。

**v12 起固定身份豁免**：`identity` / `identity_history` 属于 L0 人工设定的「宪法」，不参与清库、不参与遗忘；`/api/reset/plan` 会把它们列进 `will_keep`，`/api/export` 也会带上。清库清的是经历，不是「我是谁」。

**覆盖范围（动态枚举，不再手工维护清单）**：除 `settings`、账号类表与固定身份层外，`sqlite_master` 里的所有业务表一律清空，包含 v9~v11 新增的 `conversation_turns`、`companion_state`、`adoption_log`、`memory_versions`、v13 的 `affect_state` / `affect_relations` / `affect_events` / `reflection_log` / `proactive_suggestions`，以及此前会残留的 `event_tags`、`entity_alias`、`usage_log`、`emotion_state`、`growth_log`。同时把 `sqlite_sequence` 自增计数归零、情绪重置为「平静」、失效进程内图缓存、做一次 WAL checkpoint。**以后再加机制表会自动纳入清库范围**；`/api/export` 同样补齐了这些表（embedding 列仍剔除）。

返回体带 `cleared`（表清单）、`counts`（各表删除行数）、`kept`、`errors`、`graph_invalidated`、`checkpointed`、`usage_cleared`，可用来确认新表确实清干净了。验收陪伴 agent 真实数据时看 `adoption_log.source='agent'` 与 `memory_versions > 0`；清库后两者应为 0。

自检（**只读主库、在临时副本上跑，绝不改主库**）：

```bash
python tools/check_reset.py            # 清库覆盖 + MCP 空库 10 工具 + 二次清库
python tools/check_v12.py              # v12：存量迁移 + 固定身份豁免 + MCP 10 工具
python tools/check_v13.py              # v13：主体翻转 + Brain + 情感 + Cortex + 清库 + MCP
python tools/check_reset.py --full     # 额外跑 memory_remember / memory_ask（需 Ollama）
python tools/mcp_smoke.py              # MCP 工具列表冒烟
# 清库前预检（HTTP，需登录）：GET /api/reset/plan?scope=all
```

> - MCP **没有**清库工具，是刻意隔离：陪伴 agent 不该拿到删全库的权限，清库走管理端。
> - 图缓存的 key 已加入 `MAX(events.created_at)`，即使 HTTP 与 MCP 是两个进程，清库后也不会命中旧图。
> - 清库后自动进化线程可能写回一条 `growth_log`，属于正常代谢记录，不是残留记忆。
> - `NEUROMEM_DB` 环境变量可把 HTTP / MCP / SDK 整体指向另一个库文件，用于副本实验；不设置时就是 `data/neuromem.db`。

## 接入 AI agent（MCP）

Claude Desktop / Cursor 可以直接调用这份记忆（stdio JSON-RPC）。

配置文件（Windows）：`%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "neuromem": {
      "command": "<你的 python 可执行文件绝对路径>",
      "args": ["<本仓库绝对路径>/tools/mcp_server.py"]
    }
  }
}
```

改完重启 Claude Desktop，对话里就能让它「问我的记忆」或「记住这件事」。

| 工具 | 作用 |
|---|---|
| `memory_retrieve` | 纯检索：记忆 + 关系 + 目标，可直接当上下文 |
| `memory_remember` | 写入一条记忆（自动抽取/去重/建关系） |
| `memory_ask` | 检索 + 生成一句回答 |
| `memory_person` | 人物画像 / 别名 / 关系 / 相关记忆 |
| `memory_recent` | 最近 N 天的记忆 |
| `memory_status` | 库规模 / 阶段 / 评分 |
| `memory_state` | 情绪基调 / 自叙事 / 画像 / 会话态 |
| `memory_turn` | 记录一轮对话（说话人 + 原话 + 情绪 + 引用了哪些记忆） |
| `memory_surface` | 无问句时主动浮现值得提起的记忆 |
| `memory_feedback` | 回传本轮真正采用了哪几条 id（L2，触发再巩固与版本链） |

> `memory_retrieve` 纯只读、不写库；`memory_ask` 走完整问答链路，会写一行 `adoption_log`（L1 弱信号）。
> `memory_retrieve` / `memory_ask` / `memory_recent` / `memory_surface` 的每条记忆都带 `[id:evt_xxx]`，
> 上层 agent 拿这个 id 调 `memory_feedback` 回传采纳结果 —— 这是「曝光 ≠ 采纳」闭环的入口。
> 只调 `memory_ask` 不会产生 `source=agent` 的 L2 数据，再巩固与复习效应也拿不到输入。
> `memory_status(verbose=true)` 会额外输出 `agent回传` / `已再巩固` 等真实使用计数，便于自验收。

验证：`python tools/mcp_smoke.py`

## 进程内直连（推荐给陪伴型 agent）

MCP 返回的是拼接好的中文文本；上层 agent 若需要 `id / score / fuzzy / 来源 / 时间` 等字段，直接 import SDK 更合适：

```python
from neuromem import Memory

m = Memory(user_name="小明")
m.remember("我特别怕狗。")

ans, items = m.ask("我害怕什么？")        # 检索 + 生成，自动写 L1 采纳日志
items = m.search("我害怕什么？", top_k=8) # 纯只读检索，不改库
full = m.query("我害怕什么？")            # {events, relations, goals, state,
                                          #  summary, warnings, adopted_ids, degraded}
m.feedback(used_ids=[items[0]["id"]],    # L2 强信号：本轮真正采用了哪几条
           rejected_ids=[],
           context_text="小明怕的是邻居家的狗")  # 触发再巩固
m.adoptions(limit=10)                    # 回放采纳记录，可攒成评测集
m.versions(event_id)                     # 再巩固版本链（可回放）
m.revert(event_id)                       # 回滚到历史版本
```

**契约**：记忆层给出的是**相关候选**（含分数、置信度、模糊标记），是否相关、是否写进回答由上层 agent 判断；内部 LLM 只负责语句拆分 / 碎片化重组 / 联想，不替 agent 下结论。`feedback()` 回传的采纳结果才会强化记忆，单纯被检索到不会。

## 记忆机制（schema v12）

旧版只是「存取 + 分层衰减」，衰减由**代谢轮数**决定：同样过去 30 天，服务常驻 / 只开 1 小时会得到激活均值 0.387 / 0.810、归档 148 / 0。v11 把四条调节通路真正接上：

| 机制 | 实现 | 关键开关 |
|---|---|---|
| 时间锚点遗忘 | `act *= exp(-Δt/τ)`，Δt 取自 `max(last_decay_at, last_recalled_at, created_at)`；服务停机再启动一次补算，重复调用幂等 | `TAU_MIN_DAYS` / `TAU_MAX_DAYS` / `TAU_CURVE` / `TAU_REVIEW_GAIN` |
| 连续 τ | 由 salience / 情绪强度 / surprise / 置信度 / 人物关联 / 复习次数连续计算，取代三档阶梯；不再给「库里第一条」永久免疫 | `config.py` 的 `TAU_*` |
| 再巩固 | L2 `feedback()` 时把旧 gist/向量存进 `memory_versions`，按当时上下文以 α=0.12 定向重编码，1 小时频控 + 12 版上限，可 `revert()` | `RECONSOLIDATION` / `DRIFT_ENABLED=False` |
| 情绪 | `remember_turn(affect=...)` 更新心境；心境按**真实时间**指数回落；同号情绪记忆在召回中获得一致性偏置 | `EMOTION_BIAS` / `EMOTION_MATCH_GAIN` |
| 图式 | 概念不再独立占依据名额，改为给簇内事件加权；概括性问句（「是个怎样的人」）才让概念作为「理解」进依据池 | `SCHEMA_BIAS` / `SCHEMA_GENERIC_AS_EVIDENCE` |

**注意**：`embed.provider=ollama` 时 `base_url` 不能是云端 `.../v1`，否则请求会打到 `api.openai.com/v1/api/embed` 并静默降级为纯关键词检索。`settings.ollama_base()` 已自动识别并回退到本地。

## L0 固定身份层与主体归属（schema v12）

v12 把「我是谁」与「我记得的事」彻底分层。旧系统把两者混在 `self_narrative` 里由 LLM 从经历生成自传，于是出现第三人称旧人设与工具痕迹噪声；同时 `"我"` 被写死解析成对话者，agent 没有第一人称。

**L0 固定层 = 宪法，不是一段被记住的话**（`neuromem/identity.py`）：人工版本化写入、运行时只读；禁止 LLM / 漂移 / 再巩固 / 自叙事改写；清库豁免；不参与遗忘归档。五个字段：`who`（我是谁）、`serve`（我服务谁）、`address`（称呼与边界）、`tone`（语气与禁忌）、`limits`（能力边界）。每次升级先把旧版整份存入 `identity_history`，`checksum` 可发现绕过人工入口直接改库的行为。

**L1 持久化层 = 只增不删的经历**：`events.subject` 标注每条记忆属于谁，与 `speaker`（这话谁说的）正交。

| subject | 含义 | 典型内容 |
|---|---|---|
| `self` | 关于 agent 自己 | 我答应过什么、上次误会了什么、我学会了什么 |
| `counterpart` | 关于对方（默认） | 名字、偏好、害怕的东西 |
| `shared` | 共同经历 | 我们一起去过的地方 |
| `third_party` | 第三方 | 朋友、宠物 |

v11 → v12 **平滑迁移**：存量记忆整体标 `counterpart`，不改 gist、不动向量、不删数据；下一次新代码进程连库时自动完成。

```bash
# 人工设定身份（唯一写入口；actor=llm/agent/auto/evolve 一律拒绝）
# 首次运行会把库平滑迁移到 v12（只加列，不动任何记忆内容）
python -c "from neuromem import Memory; m=Memory(); print(m.set_identity({'who':'...','serve':'...'}, actor='human'))"
python -c "from neuromem import Memory; m=Memory(); print(m.identity_block())"
```

- **MCP**：`memory_state` 每轮先返回固定身份块；`memory_remember` / `memory_turn(remember=true)` 可传 `subject`；`memory_retrieve` 可传 `subject` 只看某主体；每条候选尾部标「关于我 / 对方 / 我们」。
- **HTTP**：`GET /api/identity`（含校验与紧凑文本）、`GET /api/identity/history`、`PUT /api/identity`（需 `confirm:"IDENTITY"`）；`POST /api/events` 与 `POST /api/retrieve` 支持 `subject`。
- **SDK**：`m.identity()` / `m.identity_block()` / `m.set_identity()` / `m.identity_history()` / `m.remember(text, subject=...)`。

v12 的边界是「只立结构」：`"用户"` 常量仍在，`answer.py` 仍是旧档案库视角。这些在 v13 已经翻完，见下一节。固定身份目前是「待人工设定」的中性模板，请按你的 agent 人设填写。


---

## 支持本项目

如果 Recollect 帮到了你，Web 界面侧栏的「支持」页有收款二维码（作者自愿维护，功能完全免费）。
二维码图片放在 `web/donate/`（文件名 `wechat.png` / `alipay.png` / `coffee.png`），
会随仓库一起发布，别人 clone 后即可看到并支持作者。

想贡献而不是打赏？提交 Issue、补充中文长对话测试用例、或写 benchmark 适配，
对项目的价值更大 —— 详见下方 Contributing。
