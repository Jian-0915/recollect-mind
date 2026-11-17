"""全局配置。

设计原则（公开版 v22）：本文件不内置任何账号专属的模型参数或密钥。
所有模型名 / 端点 / 维度都可通过环境变量覆盖，推荐做法见仓库根目录
`.env.example`。默认值仅是「本地 Ollama 开箱基线」，不是任何人的私有配置：

  - API Key 从不写在这里。它只保存在本机 data/*.db 的 settings 表（DPAPI 加密），
    或在运行时由用户在 Web「模型」页填入；data/ 已被 .gitignore 排除。
  - 想接云端模型？复制 .env.example 为 .env（或在 Web 页配置），填自己的 Key。

环境变量统一前缀 NEUROMEM_，全部可选；不设置就用下面的通用默认值。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def _load_dotenv(path):
    """零依赖读取 .env：仅在系统环境变量未设置时填入，绝不覆盖已有值。

    这样测试/CI 用 os.environ 注入的值始终优先，.env 只是本地便利。
    语法支持 KEY=VALUE、可选 export 前缀与 # 注释。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        key, val = s.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:  # 系统环境优先
            os.environ[key] = val


_load_dotenv(BASE_DIR / ".env")

# HTTP 服务、MCP server、Memory SDK 都读这里，所以两边始终是同一个后台库。
# NEUROMEM_DB 只用于测试：指向副本库做清库 / 对照实验，不设置时就是主库。
DB_PATH = Path(os.environ.get("NEUROMEM_DB") or (DATA_DIR / "neuromem.db"))


def _env(name, default):
    """读取 NEUROMEM_<name> 环境变量；空串视同未设置。"""
    v = os.environ.get("NEUROMEM_" + name)
    return v if v not in (None, "") else default


# --- 模型端点与名称（全部可覆盖；无密钥、无账号专属值）---
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434")
LLM_MODEL = _env("LLM_MODEL", "qwen3.5:4b")
# 4B 在"生成回答"任务上会退化（输出0000），回答默认改用 8B
ANSWER_MODEL = _env("ANSWER_MODEL", "qwen3:8b")
EMBED_MODEL = _env("EMBED_MODEL", "qwen3-embedding:4b")

# --- 防卡死参数（实测 qwen3.5:4b 开 think 遇悖论会卡死）---
LLM_THINK = False
# 云端「思考模式」默认关（v21，2026-11-16）。陪伴对话要的是「听话、快、说人话」，
# 而思考会让回答跑偏：实测同一条提示词，sensenova-6.8-flash 先花 44~76 个思考
# token 才开口（首字延迟翻倍），-lite 更极端——800 的预算全烧在思考上，正文被
# 截断成非法 JSON，整轮降级成「我这边没组织好」，用户看到的就是答非所问。
# 关掉后同一提示词的回答反而更贴题（「我开的什么车」→「这个我还真不知道，
# 你之前没跟我聊过车的事」，而不是硬扯一条公路车）。要复现对照就设
# NEUROMEM_LLM_THINK=on。
LLM_THINK_OFF = os.environ.get("NEUROMEM_LLM_THINK", "off").lower() not in (
    "on", "1", "true", "yes")
# 各家「关思考」的字段名不统一，且**有的端点全部忽略**（实测上面那个端点就
# 把 reasoning_effort / enable_thinking / chat_template_kwargs 全无视）。
# 合并成一次请求全带上：认了就省，不认就 400 → 自动退回并记住该端点。
LLM_NO_THINK_PARAMS = (
    {"reasoning_effort": "none"},                    # OpenAI / DeepSeek / 多家兼容
    {"thinking": {"type": "disabled"}},              # 商汤 / 智谱 / Anthropic 风格
    {"enable_thinking": False},                      # Qwen / DashScope
    {"chat_template_kwargs": {"enable_thinking": False}},  # vLLM 部署
)

LLM_MAX_TOKENS = 512
# 开深度思考时给正文留的额外预算。max_tokens 是「思考 + 正文」的总池子，
# 不抬高就等于让思考吃掉输出：实测画像生成 max_tokens=400、要求 100 字中文，
# 开思考后正文只剩几十字并漂成英文碎片（2026-11-16 用户反馈的正是这个）。
LLM_THINK_RESERVE = 1024
# 开思考后首字延迟翻倍：超时下限跟着抬，否则调用方传的 90s 会卡在思考结束前
LLM_THINK_TIMEOUT_MIN = 150
LLM_TIMEOUT = 60
LLM_TEMPERATURE = 0.2
LLM_NUM_CTX = 4096

EMBED_DIM = int(_env("EMBED_DIM", "2560"))
EMBED_TIMEOUT = int(_env("EMBED_TIMEOUT", "60"))   # 4B 嵌入冷加载实测 ~14s，30s 余量太小
EMBED_KEEP_ALIVE = _env("EMBED_KEEP_ALIVE", "30m")  # ollama 常驻时长：不设则每次冷加载付 50~70s

# --- 召回参数 ---
RECALL_TOP_K = 20
RECALL_SEED = 5
RECALL_KEEP_MIN = 0.15        # 绝对保底分：低于它宁可返回空依据，也不拿弱相关记忆凑数
# 相对阈值（top1×0.65）的上限。没有上限时，top1 一旦被词面巧合匹配到高分，
# 阈值会水涨船高，把真正相关的中等分候选全砍掉：实测「谁养了金毛布丁」
# 正解 0.643 被 0.6435 的阈值砍掉，整轮返回 0 条候选。
RECALL_KEEP_MAX = 0.45
# 相对阈值基准取第 N 名而不是 top1，抵抗单条异常值（同一条词面巧合命中）。
RECALL_KEEP_RANK = 4
# 焦点词失配的降权系数（非关系命中档）。旧值 0.55 对同义表述惩罚过重：
# 「哪家公司上班」里的焦点词「公司」不在「在云栖科技做后端工程师」这条正解里，
# 正解被打到 0.195，直接被阈值砍掉。
FOCUS_MISS_PENALTY = 0.72
# 人物过滤的软降权（保留常量供 A/B 对照，默认不启用：见 recall 里的硬过滤注释）。
# 中文子串识别太脆——「男朋友」包含「朋友」，而「朋友」也是一张人物卡。
# 既然线索不可靠，就只让它微调排序，真正的取舍交给生成层（候选最多 12 条）。
PERSON_FILTER_SOFT = 0.80
# 非人实体：不该作为「点名人物」参与召回的硬过滤。构建期把品种、节日、职业、
# 群体都登记成了 personas，实测「金毛」把「谁养了金毛布丁」的候选砍到 1 条，
# 「朋友」把「我男朋友是谁」劫持成「和朋友打球露营」。这里是显式声明，
# 更彻底的做法是清理 personas 表（见 README 的已知问题）。
GENERIC_PERSON_NAMES = frozenset([
    "金毛", "橘猫", "猫", "狗", "医生", "朋友", "同事", "室友", "前同事",
    "我们组", "我们一大家子", "大学室友", "清明", "中秋", "过年", "团建",
])
CONCEPT_SCORE_SCALE = 0.45    # 概念分与事件同口径缩放（否则概念 0.5~0.8 天然霸榜）
CONCEPT_MIN_SCORE = 0.30      # 缩放后的概念入选下限（4B 负例 top1≈0.59×0.45≈0.26，0.22 会放垃圾）
ANSWER_MIN_SCORE = 0.15       # 回答闸门：最高依据分低于此且零关键词命中 → 直接拒答
SPREAD_HOPS = 1
SPREAD_DECAY = 0.6
SIM_EDGE_THRESHOLD = 0.80      # 4B 随机句对 p99=0.7265，0.75 会连出噪声边
DUP_SIM = 0.90                 # 去重阈值：4B 同义中位 0.9047；0.88 会误并"更正句"，所以取 0.90

# --- 冷启动召回修复（2026-11-15，清库后主库 18 轮实测定位）---
# 修复前：personas 自动登记「我/你」→ 代词命中使 pids 非空 → 人物硬过滤
# 把没有建边的新记忆全部丢掉，正确答案（余弦 0.638）连候选都进不去；
# 2-gram 噪声词「儿工」又对剩余候选乘 0.55。
# 0 = 完整复现修复前行为，专供同剧本 A/B 对照；默认是 1。
RECALL_COLDSTART_FIX = os.environ.get("NEUROMEM_RECALL_FIX", "1") != "0"

# --- 工作记忆（短期注意）---
WM_HOURS = 24                  # 窗口：最近 24 小时内写入/召回的算"在工作台"
WM_CAPACITY = 6                # 工作台容量
WM_BONUS = 0.08                # 工作台内记忆的召回加成
WINDOW_BOOST = 1.15              # 查询时间窗内记忆的排序提升（只微调，不改序）
WINDOW_FLOOR = 0.20              # 窗内记忆的最低分：保证「昨天做了什么」不会被 0 分扔掉

# --- 聚合层（v19）：回答「几个 / 都有哪些」这类需要集合而非单条的问题 ---
# top_k 召回对这类问句结构性无能：答案不在任何一条记忆里，被截断的候选
# 会让模型用「只记得…」的口吻给出残缺清单。开启后追加一条确定性统计候选。
AGGREGATE_LAYER = os.environ.get("NEUROMEM_AGGREGATE", "1") != "0"
AGGREGATE_MAX_ITEMS = 14        # 汇总里列出的明细条数上限（其余只报总数）

# --- 遗忘（分层）---
DECAY_THRESHOLD = 0.15         # 低于该激活值归档（数据保留，可恢复）
DECAY_TRIVIAL = 0.9934         # 琐事（salience <0.2 且从未召回）：约 2 天到阈值
DECAY_NORMAL = 0.99971         # 普通：约 45 天
DECAY_IMPORTANT = 0.99993      # 重要（salience ≥0.6 或召回 ≥3 次）：约 180 天
DECAY_RECALLED = 0.999995      # 近 30 天被召回过：几乎不动
DECAY_ANCHOR = 0.999999        # 锚点（库里第一条记忆）：基本不忘

# 旧参数（时间锚点后不再参与 decay，保留以免旧脚本/单测报错）
DECAY_FACTOR = 0.985
DECAY_SALIENCE_BONUS = 0.002

# --- 时间锚点遗忘（v11，2026-11-14）---
# 旧版每轮乘固定系数：同样过去 30 天，服务常驻 / 只开 1 小时会得到
# 激活均值 0.387 / 0.810、归档 148 / 0 —— 忘不忘取决于进程在不在线。
# 新版 act *= exp(-Δt/τ)：Δt 取真实时间，τ 连续，重复调用幂等。
# 用真实 709 条库标定：琐事 τ≈16 天、普通≈51 天、强情绪重要记忆≈260 天；
# +30 天只淡出琐事，+90 天普通仍约 0.17、重要约 0.71，不再两极分化。
TAU_MIN_DAYS = 4.0              # 最低半衰期量级（天）
TAU_MAX_DAYS = 420.0            # 最高（强情绪 + 高显著性 + 反复采用）
TAU_CURVE = 1.45                # importance → τ 的凸性：低价值掉得快
TAU_REVIEW_GAIN = 0.50          # 复习效应：τ *= 1 + gain*ln(1+recall_count)
DECAY_EMOTION_HALFLIFE_HOURS = 6.0  # 情绪回落到中性的半衰期（按真实时间）

# --- 再巩固：被采用的记忆按当时上下文定向重编码 ---
RECONSOLIDATION = True          # L2 显式 feedback 时启用（L1 自动信号不动向量）
RECONSOLIDATION_ALPHA = 0.12    # 上下文向量并入比例，保守以免冲掉原语义
RECONSOLIDATION_MIN_GAP_HOURS = 1.0   # 同一条记忆两次重编码的最小间隔
RECONSOLIDATION_MAX_REVISIONS = 12    # 版本链上限，防止长期问答写爆
DRIFT_ENABLED = False           # 随机向量漂移与「定向再巩固」冲突，默认关闭

# --- 情绪 / 图式：两条调节通路 ---
EMOTION_BIAS = True             # 当前心境对同情绪记忆的召回偏置
EMOTION_MIN_INTENSITY = 0.20    # |valence| 低于此视为平静，不偏置
EMOTION_MATCH_GAIN = 0.12       # 心境与记忆同号时的最大加成
EMOTION_MISMATCH_GAIN = 0.06    # 异号时的最大降权
EMOTION_TURN_WEIGHT = 0.30      # 一轮对话的情绪写入权重
SCHEMA_BIAS = True              # 概念改为对簇内事件加权，不再自己进依据池
SCHEMA_AS_EVIDENCE = False      # True 时恢复旧行为（所有问句都让概念进依据池）
SCHEMA_GENERIC_AS_EVIDENCE = True  # 概括性问句（「是个怎样的人」）由语义图式回答
SCHEMA_MIN_SCORE = 0.20         # 图式偏置的概念门槛（低于进依据池的 0.30）
SCHEMA_BIAS_GAIN = 0.18         # boost = 概念分 × gain

# --- 惊讶（预测误差）---
SURPRISE_TRIGGER = 1.2         # 距上次代谢的惊讶累积达到该值即触发巩固（实测中文嵌入基线 sim 0.7~0.9）

# --- 记忆冲突 ---
SUPERSEDE_SIM = 0.85           # 4B 实测：同义改写 A 组中位 0.9047、不同事实 B 组最高 0.8376
SUPERSEDE_CORRECTION_SIM = 0.76  # C 组最低 0.7819，0.78 卡边界
SUPERSEDE_RECANT_SIM = 0.70      # "我说错了/更正"可沿链继续取代（第二次、第三次更正）

# --- 自动进化 ---
AUTOEVOLVE = True              # 环境变量 NEUROMEM_AUTOEVOLVE=0 可关
EVOLVE_INTERVAL = 600          # 定时代谢间隔（秒）
EVOLVE_EVENT_THRESHOLD = 5     # 新增事件达到该数量触发一轮
EVOLVE_MIN_EVENTS = 3          # 事件少于该数不代谢（省 LLM 调用）
EVOLVE_MIN_GAP = 60            # 两次代谢最小间隔（秒，防抖）

# --- 陪伴层（2026-11-14）：把认知状态接进提取与表达 ---
COMPANION_STATE = True          # 情绪 / 自叙事 / 画像 / 会话态进入回答提示词与检索结果
ACTIVATION_WEIGHT = 0.15       # 召回基础分的「提取容易度」调制：s *= (1-w) + w*activation
FUZZY_ACTIVATION = 0.65        # 低于此激活值的依据标 [印象模糊]，用不确定语气表达
COEXIST_DEFAULT = False        # True 时旧说法默认进依据池（关系史），不覆盖新说法
SURFACE_RECENT_DAYS = 30       # 主动浮现的近因窗口（天）

# --- 采纳反馈（2026-11-14）：曝光 ≠ 采纳 ---
ADOPT_AUTO = True              # 问答链路自动写 adoption_log 并弱强化；关掉则只剩显式 feedback
ADOPT_WEAK_GAIN = 0.05        # L1 进提示词的收敛增益：amount = gain * (1 - activation)
ADOPT_STRONG_GAIN = 0.20      # L2 agent 明确采用的收敛增益
ADOPT_REJECT_PENALTY = 0.03   # L2 明确拒绝时 activation 的降幅
ADOPT_MIN_ACTIVATION = 0.20   # 降权下限：记忆可以变模糊，但不该被抹掉

# --- 长测修复（2026-11-15）：近期使用疲劳度 ---
# 65 轮无人值守长测里，最热的一条记忆被连续采纳 23 次，回复塌缩成固定句式。
# 根因是 search_text 污染（已在 reconsolidation.py 修）+ 排序层缺「刚用过」抑制。
RECENT_USED_PENALTY = 0.25    # 最近 N 轮用过的记忆，召回分乘 (1 - penalty)
RECENT_USED_TURNS = 6         # 「最近」的轮次窗口（取 conversation_turns 与 adoption_log）

# --- v12：主体归属（L0 固定身份层配套）-------------------------------
# 「我」不再等于「对话者」：每条记忆带 subject，speaker 只表示「这话谁说的」。
# 存量库平滑迁移：历史记忆整体标 counterpart（旧系统确实都在记录对方），
# 不删数据、不改 gist。阶段 2 把散落的 "用户" 常量换成下面的主体词典。
SUBJECTS = ("self", "counterpart", "shared", "third_party")
SUBJECT_LABELS = {
    "self": "我", "counterpart": "对方", "shared": "我们", "third_party": "第三方",
}
DEFAULT_SUBJECT = "counterpart"

# v13 主体词典：所有写死的「用户」都从这里取，不再散落在 9 个文件里。
# 默认值刻意保持旧行为（COUNTERPART_NAME='用户'），因此 133 个存量单测不破；
# 陪伴 agent 走 Brain/MCP 时显式给 subject=self，第一人称就归位到 agent 自己。
COUNTERPART_NAME = "用户"       # 对话对象在 gist 里的落点（旧库兼容）
SELF_NAME = "我"                # agent 自己；替换后等于保留原话，不再被写成「用户」
AGENT_SPEAKERS = frozenset({"agent", "assistant", "self", "brain", "companion"})
# 主体翻转总开关：关掉则完全回到 v12 行为，便于 A/B 对照。
SUBJECT_VIEW = True

# --- 人物实体归一（v14，2026-11-15）---
# 第一人称代词永远不是独立实体：写路径里若不拦，「我」会被 resolve_persona
# 登记成一个 persona，把用户第一人称（"我和周野吵架"）误绑到名为「我」的人物，
# 与真正的对话者 persona「你」割裂。开启后 resolve_persona 对纯代词返回 None，
# 不建 persona、不写 involves 边；gist 原文不受影响。关掉可复现旧行为。
PERSONA_PRONOUN_GUARD = os.environ.get(
    "NEUROMEM_PRONOUN_GUARD", "1") != "0"
# 纯第一人称代词（含方言）：这些字符串决不能成为 persona。
SPEAKER_PRONOUNS = frozenset({"我", "自己", "本人", "俺", "咱", "我们", "俺们"})

# --- v13 皮层情感层（affect.py）-------------------------------
# 旧 emotion.py 只有 valence/arousal 两维且只作用于召回；这里补离散情绪、
# 慢变量心境、按人关系情感、能量节律，并同时作用于编码 / 召回 / 表达决策。
AFFECT_SALIENCE_GAIN = 0.15     # 强情绪写入时的 salience 加成上限
AFFECT_HALFLIFE_HOURS = 6.0     # 快变量（当下情绪）真实时间半衰期
AFFECT_MOOD_HALFLIFE_HOURS = 72.0  # 慢变量（心境）半衰期
AFFECT_MOOD_ALPHA = 0.08        # 快变量并入慢心境的权重
AFFECT_VECTOR_INERTIA = 0.55    # 新情绪到来时旧向量的保留比例
AFFECT_MIN_INTENSITY = 0.05     # 低于此视为无情绪
AFFECT_INITIATE_ENERGY = 0.55   # 主动发起所需的最低能量
AFFECT_INITIATE_GAP_HOURS = 4.0 # 距上次对话多久才考虑主动开口

# --- v13 Brain / Cortex ---
BRAIN_TOP_K = 12                # 每轮给 agent 的候选上限
CORTEX_ENABLED = True
CORTEX_MODULES = tuple(
    x.strip() for x in os.environ.get(
        "NEUROMEM_MODULES", "proactive,reflection").split(",") if x.strip())