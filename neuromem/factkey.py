"""结构化事实键：把「谁 · 什么属性 · 什么值」抽出来。

## 为什么需要它

`store.py` 的取代链（F1~F7）靠「内容 2-gram 重合 + 人物边 + 余弦」判断新旧
说法是不是同一件事。它不知道**属性维度**这回事，于是两类错误反复出现：

- **该取代没取代**：「我住在北京」→「我搬到上海」。两条只共享极少的 2-gram
  （住/搬 是不同字），余弦也低，被判成两条并存，用户会同时"住北京"和"住上海"。
- **不该取代却取代**：「我喜欢猫」和「我喜欢狗」共享「喜欢」，在相似度够高时
  会被当成同一偏好的改写，猫的偏好被狗顶掉。

结构化键把判断从「文字像不像」换成「是不是同一个人的同一个属性」：

    (主体, 属性, 值)   例：(self, 居住地, 上海)

## 关键设计：单值 vs 多值

同样是属性，行为完全不同：

- **单值（functional）**：一个人只有一个居住地、一个名字、一个在职状态。
  值变了 = 旧值作废 → **取代**。
- **多值（multi）**：可以同时喜欢猫和狗、去过青岛和大连、养两只猫。
  值增加 ≠ 旧值作废 → **并存，绝不取代**。

「只对单值属性做取代」是这套东西不误伤的唯一防线。宁可少取代，不可错取代。

## 保守原则

1. 抽不出（主体不明 / 值不明）就返回 None，退回原有文本判据，不硬猜。
2. 带历史标记的陈述（「小时候」「以前」「当时」）不建单值键——它在讲历史，
   不该覆盖当前值。
3. 这套键是**增强证据**，不是替代品：命中冲突时优先取代，其余情况仍走
   F1~F7 原逻辑。
"""
from __future__ import annotations

import json
import re

# (正则, 属性名, 是否单值, 无宾语时的状态值)
# 顺序即优先级：更具体的写法必须排在泛写法前面（「改名叫」先于「叫」）。
# 状态类谓词（离职/结婚）天然没有宾语，值就是状态本身，用第 4 项补。
_PREDICATES = [
    # ---- 居住地（单值）----
    (r"搬到|搬去|搬家到|搬回", "居住地", True, ""),
    (r"住在|家住(?!院)|定居在|生活在|(?<!老)家在", "居住地", True, ""),
    # ---- 名字（单值）----
    (r"改名叫|改名为|改成叫|取名叫", "名字", True, ""),
    (r"名字叫|名叫|叫做", "名字", True, ""),
    # ---- 在职状态 / 职位（单值）----
    (r"升职成|升为|晋升为|当上了|提拔成", "职位", True, ""),
    (r"跳槽到|换工作到|去了.{2,10}?(?:工作|上班)", "工作单位", True, ""),
    (r"离职|辞职|不干了|被裁了|被开除了", "在职状态", True, "离职"),
    (r"入职|正式上班|开始上班", "在职状态", True, "在职"),
    (r"在(.{2,10}?)(?:工作|上班|任职)", "工作单位", True, ""),
    # 「在X做/当Y」——真实语料里最常见的在职说法（"在云栖科技做后端工程师"）。
    # 必须用职业名词收尾：否则「我在家做饭」会被抽成 (我, 工作单位, 家)。
    (r"在(.{2,10}?)(?:公司)?(?:做|当)(?:.{0,6}?)"
     r"(?:工程师|经理|总监|主管|设计师|程序员|架构师|分析师|顾问|助理|实习生|"
     r"老师|医生|律师|会计|护士|店长|运营|销售)",
     "工作单位", True, ""),
    # ---- 关系 / 人生状态（单值）----
    (r"结婚了|领证了|办婚礼了", "婚姻状态", True, "已婚"),
    (r"离婚了|分手了", "婚姻状态", True, "单身"),
    (r"怀孕了|有宝宝了|生了", "生育状态", True, "有孩子"),
    (r"毕业了", "学业状态", True, "已毕业"),
    # ---- 身体数据（单值）----
    (r"(\d{1,2})岁了|今年\s*(\d{1,2})\s*岁", "年龄", True, ""),
    (r"身高(?:是|有)?\s*(\d{3}(?:\.\d)?)", "身高", True, ""),
    (r"体重(?:是|有)?\s*(\d{2,3})", "体重", True, ""),
    # ---- 多值属性：只用于判重，永不取代 ----
    (r"喜欢|偏爱|爱好是|迷上了", "喜好", False, ""),
    (r"讨厌|不喜欢|受不了|反感", "厌恶", False, ""),
    (r"养了|养着|有只|有一只", "饲养", False, ""),
    (r"去过|去了|到过", "行踪", False, ""),
    (r"买了|入手了|收了", "拥有物", False, ""),
    (r"打算|计划|准备要|想要", "计划", False, ""),
]

# 历史陈述：讲的是过去某时的状态，不能覆盖当前单值属性。
# 「去年」这类也收进来——它可能指"去年搬到北京、现在还住"，也可能是旧状态；
# 分不清就按不取代处理：漏取代只是两条并存，错取代是真丢事实。
_HISTORICAL = re.compile(
    r"以前|小时候|之前|那时候|当时|从前|早些年|曾经|原来|去年|上个月|上个星期")

# 否定 / 更正句整句不建键。
#
# 为什么必须整句判断： 「阿哲在银行工作」→「阿哲其实没有在银行工作，
# 他是在做软件开发」，两句抽出来的键字面完全相同 (阿哲, 工作单位, 银行)，
# 会被 same() 当成"复述"直接判重、新说法根本不入库，更正就丢了。
# 这类句子的语义方向靠规则判不准（是"没在银行"还是"银行之外还在"），
# 一律交回 F1~F7 那套专门处理更正词的逻辑。
_NEGATION = re.compile(r"其实|并没有|没有|不是|不再|并非|改成|改为|取消了|已经不在")
# 近邻否定：谓词前 6 字内有否定词，同样不建键
_NEG_NEAR = re.compile(r"[不没别无]\s*$")

# 值抽取的截断边界
_VALUE_STOP = re.compile(r"[。！？；，,;!?\n]")

# 主体的兜底代词
_SELF = re.compile(r"我(?!们)")

_SELF_ID = "self"
MAX_VALUE = 16
# 单字值一律不收：「老家住院」会匹配到「家住」，剩下一个「院」，
# 抽出来就是 (外婆, 居住地, 院) 这种脏键。两个字起才像真实的值。
MIN_VALUE = 2

# 指向「对话者本人」的各种叫法。不同时期写入的 gist 分别是「我…」「你…」
# 「用户…」，如果不在键里统一，同一个人的同一个属性永远匹配不上，
# 取代链就形同虚设。统一落到 counterpart（与 events.subject 的语义一致）。
_SELF_TOKENS = {"我", "自己", "本人", "俺", _SELF_ID}


def canonical_subject(name, conn=None):
    """把主体归一到稳定标识：对话者本人 → counterpart，其他人 → 规范名。"""
    s = str(name or "").strip()
    if not s:
        return None
    if s in _SELF_TOKENS:
        return "counterpart"
    try:
        from . import config, persona
        aliases = {config.COUNTERPART_NAME or "", config.SELF_NAME or ""}
        if conn is not None:
            try:
                from . import persona_card
                aliases.add(persona_card.call_user(conn) or "")
            except Exception:
                pass
        aliases.discard("")
        if s in aliases:
            return "counterpart"
        if conn is not None:
            p = persona.find(conn, s)
            if p:
                cn = p["canonical_name"] if not isinstance(p, dict) else p.get("canonical_name")
                if cn:
                    return "counterpart" if cn in aliases else cn
    except Exception:
        pass
    return s


def _clean_value(raw):
    """把谓词后面的片段收成干净的值：切到标点，去结构助词，限长。"""
    s = _VALUE_STOP.split(str(raw or ""))[0].strip()
    # 结构助词 + 动量/名量词：「去了趟景德镇」「养了只猫」里的「趟/只」不是值的一部分。
    s = re.sub(r"^[的了着过地把被给和与、趟次个位只条家间份张篇首杯碗瓶]+", "", s)
    # 「台」单独处理并排除「台湾」：那是地名，不能当量词切掉。
    s = re.sub(r"^台(?!湾)", "", s)
    s = re.sub(r"\s+", "", s)
    s = s.strip("的了着过")
    return s[:MAX_VALUE]


def _subject_before(text, pos, conn, names=None):
    """取谓词之前最近的主体：已知人物名优先，其次第一人称。

    只看谓词之前是关键——「我养了只猫叫团子」的主体是「我」（谁养的），
    而「团子是黑猫」的主体是团子。混了就会把人的属性安到猫身上。
    """
    head = text[:pos]
    if not head:
        return None
    if names is None:
        names = []
        if conn is not None:
            try:
                rows = conn.execute(
                    "SELECT canonical_name FROM personas WHERE canonical_name<>''").fetchall()
            except Exception:
                rows = []
            names = [r["canonical_name"] if not isinstance(r, str) else r
                     for r in rows]
    best, best_pos = None, -1
    for name in names:
        if not name or name == _SELF_ID:
            continue
        p = head.rfind(name)
        # 名字越靠后越贴近谓词，越可能是真正的主语
        if p >= 0 and (p > best_pos or (p == best_pos and len(name) > len(best or ""))):
            best, best_pos = name, p
    if best:
        return best
    if _SELF.search(head):
        return _SELF_ID
    return None


def persona_names(conn):
    """一次取全部人物名。store 批量写时传入，避免每条都查库。"""
    try:
        rows = conn.execute(
            "SELECT canonical_name FROM personas WHERE canonical_name<>''").fetchall()
    except Exception:
        return []
    return [r["canonical_name"] if not isinstance(r, str) else r for r in rows]


def extract(text, conn=None, names=None):
    """抽 (主体, 属性, 值)。抽不出返回 None —— 宁可退回文本判据，不硬猜。"""
    t = str(text or "").strip()
    if len(t) < 4:
        return None
    if _NEGATION.search(t):
        return None                      # 否定/更正句：语义方向判不准，退回原判据
    for pat, attr, functional, default in _PREDICATES:
        m = re.search(pat, t)
        if not m:
            continue
        # 近邻否定（"我不在北京住了"）：同样不建键。
        if _NEG_NEAR.search(t[max(0, m.start() - 6):m.start()]):
            return None
        # 带捕获组的（年龄/身高/体重/工作单位）直接用捕获值
        value = ""
        for g in (m.groups() or ()):
            if g:
                value = _clean_value(g)
                break
        if not value:
            value = _clean_value(t[m.end():])
        if not value:
            value = default          # 状态类：值就是状态本身
        # 单值属性要求 >=2 字：误抽出脏键会直接写坏记忆（「老家住院」→ 值「院」）。
        # 多值属性放宽到 1 字：它只用于判重、永不取代，「喜欢猫」的「猫」是合法值。
        if len(value) < (MIN_VALUE if functional else 1):
            return None
        subject = _subject_before(t, m.start(), conn, names)
        if not subject:
            return None
        subject = canonical_subject(subject, conn)
        key = {"s": subject, "a": attr, "v": value, "f": 1 if functional else 0}
        # 历史陈述不建单值键：它在描述过去的某一段，不该顶掉当前值
        if functional and _HISTORICAL.search(t):
            key["f"] = 0
        return key
    return None


def dumps(key):
    return json.dumps(key, ensure_ascii=False, sort_keys=True) if key else None


def loads(raw):
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        k = json.loads(raw)
        return k if isinstance(k, dict) and k.get("s") and k.get("a") else None
    except (TypeError, ValueError):
        return None


def _same_value(a, b):
    """值是否等价：「北京」与「北京市」、「团子」与「团子（猫）」算同一个。"""
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) >= 2 and (a in b or b in a):
        return True
    return False


def same(k1, k2):
    """同主体、同属性、同值 → 同一事实的复述（可判重复）。"""
    if not k1 or not k2:
        return False
    return (k1.get("s") == k2.get("s") and k1.get("a") == k2.get("a")
            and _same_value(k1.get("v"), k2.get("v")))


def conflict(k_new, k_old):
    """新说法是否作废旧说法：同主体 + 同属性 + 属性单值 + 值不同。

    多值属性（喜好/饲养/行踪…）永远返回 False —— 喜欢猫不妨碍喜欢狗。
    """
    if not k_new or not k_old:
        return False
    if k_new.get("s") != k_old.get("s"):
        return False
    if k_new.get("a") != k_old.get("a"):
        return False
    # 任一方被打上"历史"标记（f=0 的单值属性）就不取代：
    # 「小时候住北京」不能顶掉「现在住上海」。
    if not k_new.get("f") or not k_old.get("f"):
        return False
    if _same_value(k_new.get("v"), k_old.get("v")):
        return False          # 值相同 → 是复述，走 same() 判重
    return True


def describe(key):
    """给日志/审计一句人话。"""
    if not key:
        return "-"
    tag = "单值" if key.get("f") else "多值"
    return "%s·%s=%s（%s）" % (key.get("s"), key.get("a"), key.get("v"), tag)
