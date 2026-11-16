"""写入流程：分割 -> 抽取 -> 去重 -> 归一 -> 绑定 -> 情感 -> 索引 -> 建边 -> 落库。

关键原则：gist 忠实保存用户原话（仅把"我"换成对话者），避免模型改写失真；
结构化字段（who/when/where/affect）由模型抽取。
"""
import json
import re
import uuid
from datetime import datetime, timezone

from . import (alias, config, db, embed, entity, factkey, forgetting, identity,
               llm, persona, tagging, timeparse)


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _uid(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def _cos(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# === 记忆冲突：谁有资格取代谁 ===
# 只有"同一人物的说法更新"才算取代；纯向量相似会把"妈妈腰疼"和"我做咖啡"连到一起
_CORRECTION = ("其实", "并没有", "没有", "不是", "并不", "不再",
               "取消了", "改成了", "改为", "已经不在", "已经离开")
# 明确的"我说错了"：不要求旧句不含更正词，用于第二次、第三次更正
_RECANT = ("记错了", "说错了", "搞错了", "弄错了", "更正", "之前说错", "收回")


def _has_correction(text):
    return any(w in (text or "") for w in _CORRECTION)


def _has_recant(text):
    return any(w in (text or "") for w in _RECANT)


# === 新旧判定：按「事情什么时候发生」而不是「什么时候说出口」 ===
# 补录是常态而非例外：主库 203 条里 21 条 occurred_at 与 created_at 不同日，
# 最极端差 7 个月（"清明回老家扫墓"是 11 月才说的）。单值属性（住址/工作/婚姻）
# 若按 created_at 判新旧，用户补录一句"2025 年 3 月我搬到杭州"，就会把当前住址顶掉。
# 注意：更正链（"我说错了/其实不是"）**必须**用 created_at——那是说话顺序，
# 后说的才是否定先说的，两套口径不能混。
def _effective_at(occurred_at, created_at):
    """事实生效时间，返回可比较的 (日期, 完整串)。occurred_at 缺失回退 created_at。

    只比到「天」：occurred_at 的格式在不同来源下混用（有的带时分秒、有的只有
    日期），字符串直接比较会在跨格式时错序，截到日期前缀是安全的。
    """
    occ = str(occurred_at or "").strip()
    cre = str(created_at or "").strip()
    base = occ or cre
    if not base:
        return ("", "")
    day = base[:10]
    # occurred_at 只精确到日、又正好是录入当天 → 没有"更早"的信息，用录入时刻兜底。
    # 否则同一天上午说 A、下午补一句"今天…"，会被误判成补录旧事而反向作废。
    if occ and len(occ) <= 10 and cre[:10] == day:
        base = cre
    return (day, base)


# 不确定的说法：置信度打折（C2）
_HEDGE = ("可能", "好像", "听说", "据说", "似乎", "大概", "也许", "差不多")


def _bigrams(text):
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


# 通用 2-gram：常批量出现在大量记忆里，不能作为「共享实体」证据，
# 否则「用户…猫」和「用户…狗」会被误判为同一件事的更正。
_GENERIC_BI = set(
    "用户 你们 我们 他们 她们 它们 一只 养了 喜欢 觉得 认为 知道 好像 可能 "
    "但是 因为 所以 现在 今天 昨天 时候 这个 那个 一个 没有 不是 其实 还是 "
    "就是 自己 可以 应该 已经 最近 经常 每次 起来 出来 一些 这样 那样 什么 "
    "怎么 为什么 的话 之后 之前 一直 真的 感觉".split())


def _content_bigrams(text):
    """去掉通用 2-gram 后的「内容」2-gram 集合，用于判断是否指同一事物。"""
    return {b for b in _bigrams(text) if b not in _GENERIC_BI}


def _shares_persona(conn, old_eid, names):
    """新旧事件是否指向同一个人物 —— 关系更新的前提条件。"""
    ids = set()
    for n in names:
        p = persona.find(conn, n)
        if p:
            ids.add(p["id"])
    if not ids:
        return False
    for r in conn.execute(
            "SELECT target FROM edges WHERE source=? AND type='involves'", (old_eid,)):
        if r["target"] in ids:
            return True
    return False


MAX_SEG = 50        # 单句超过 50 字，语义往往不止一件事，按逗号细切
MIN_CHUNK = 12      # 细切后每段至少 12 字，避免"性格开朗"这种碎片单独成事件

# F4（2026-11-15 长测后）：短句里用逗号并列两个主体/两件事时，旧逻辑因为
# len(p) <= MAX_SEG 而整句入库，产生「橘猫年糕怕黄瓜，用户自己也怕打雷」
# 这种双主体 gist，后续检索会把猫的特征安到人身上，纠错 18 轮仍反复复发。
_MULTI_FACT = re.compile(
    r"[，,]\s*(?:"
    r"(?:用户|他|她|它|我们|你们|咱|[\u4e00-\u9fa5]{2,3})\s*"
    r"(?:也|还|又|则|却|倒是|自己)"
    r"|(?:也|还|另外|同时)\s*(?:提到|说|表示|有|是|会|想|怕|喜欢|用)"
    r")"
)


def segment(text):
    """切分记忆单元。

    1) 先按句末标点切成句；
    2) 句中出现主语切换 / 并列新事实时，按逗号强制细切（即使句子很短）；
    3) 单句过长（> MAX_SEG）再按逗号/分号切，并合并到 >= MIN_CHUNK，
       避免"一句话里塞了关系+性格+爱好+计划"被压成一条事件。
    """
    out = []
    for p in re.split(r"[。！？\n]+", text):
        p = p.strip()
        if not p:
            continue
        if _MULTI_FACT.search(p):
            # 不套 MIN_CHUNK：这里的目标就是让不同主体的事实各自独立成事件
            parts = [x.strip() for x in re.split(r"[，,；;]+", p) if x.strip()]
            if len(parts) > 1:
                buf = ""
                for s in parts:
                    if len(s) < 5 and buf:
                        buf += "，" + s
                    else:
                        if buf:
                            out.append(buf)
                        buf = s
                if buf:
                    out.append(buf)
                continue
        if len(p) <= MAX_SEG:
            out.append(p)
            continue
        buf = ""
        start = len(out)
        for s in [x.strip() for x in re.split(r"[，,；;]+", p) if x.strip()]:
            buf = (buf + "，" + s) if buf else s
            if len(buf) >= MIN_CHUNK:
                out.append(buf)
                buf = ""
        if buf:
            # 尾巴太短就并回本句上一段，别产出"周末还去图书馆自习"这种 9 字碎片
            if len(out) > start and len(buf) < MIN_CHUNK:
                out[-1] = out[-1] + "，" + buf
            else:
                out.append(buf)
    return out


_EXTRACT_PROMPT = (
    '你是记忆抽取器。从句子中抽取一条事件，严格只输出 JSON，不要任何解释或多余文字。\n'
    "规则：\n"
    "1. who：句子中明确出现的人名/称呼（字符串数组），没有就 []。\n"
    "2. what：用一句完整的话复述发生的事，必须保留主语，不得添加原文没有的细节、不得评价或解释。\n"
    '3. when：原文中的时间词；没有就填空字符串 ""（严禁填"未知"等词）。\n'
    '4. date：把 when 换算成绝对日期 YYYY-MM-DD（今天是 {today}）；无法确定就填空字符串 ""。\n'
    '5. where：原文中的地点；没有就填空字符串 ""。\n'
    '6. affect：{"valence":-1到1,"arousal":0到1,"label":"情绪词"}；中性则 valence=0,arousal=0,label=中性。\n'
    "示例输入：小明昨天在公园被狗吓到了。\n"
    '示例输出：{"who":["小明"],"what":"小明在公园被狗吓到了","when":"昨天","date":"2026-11-10","where":"公园","affect":{"valence":-1,"arousal":0.8,"label":"恐惧"}}\n'
    "现在处理：{text}"
)


def _norm_partial_date(s):
    """把只到「月/年」的日期补成完整日期；认不出的返回空串。

    模型常把"2025年3月"写成 ``2025-03``。留空会让补录语句被当成"刚发生"，
    宁可粗到月也要有个时间锚。
    """
    s = str(s or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{1,2}", s):
        y, m = s.split("-")
        return "%s-%02d-01" % (y, int(m))
    if re.fullmatch(r"\d{4}", s):
        return s + "-01-01"
    return ""


def extract(text, base_date=None):
    """抽取一条事件。base_date = 上一条事件日期，用于解析"第二天/次日"。"""
    ctx = None
    if base_date:
        try:
            ctx = datetime.strptime(str(base_date)[:10], "%Y-%m-%d")
        except Exception:
            ctx = None
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = _EXTRACT_PROMPT.replace("{today}", today).replace("{text}", text)
    try:
        d = llm.generate_json(prompt, max_tokens=300, timeout=45)
    except Exception:
        d = {}

    who = d.get("who") or []
    if isinstance(who, str):
        who = [who]
    aff = d.get("affect") or {}
    try:
        v = float(aff.get("valence", 0.0))
        a = float(aff.get("arousal", 0.0))
    except Exception:
        v, a = 0.0, 0.0
    v = max(-1.0, min(1.0, v))
    a = max(0.0, min(1.0, a))

    when = (d.get("when") or "").strip()
    date = (d.get("date") or "").strip()
    # 相对时间词（今天/昨天/前天/上周X/N 天前）以本地规则为准：
    # 模型跨天或算错时，"昨天"会被标成前天（实测发生过）
    parsed = timeparse.parse_date(when, ctx=ctx)
    if not parsed:
        # 只到「月」的说法（"2025年3月""上个月"）：parse_date 只给单日，这里取
        # 区间的起始日。occurred_at 只需要"大致时期"就够判新旧；留空的话，
        # 补录一句更早的事实会被当成"刚发生"，直接把当前值顶掉。
        # 注意不改 parse_date 本身——recall 用它做时间过滤，语义必须是单日。
        rng = timeparse.parse_range(when)
        if rng:
            parsed = rng[0]
    if not parsed:
        m = re.fullmatch(r"\s*(\d{4})\s*年\s*", when or "")   # 只到年
        if m:
            parsed = m.group(1) + "-01-01"
    if parsed:
        date = parsed
    else:
        date = _norm_partial_date(date)

    return {
        "who": [w for w in who if w],
        "when": when,
        "occurred_at": date,
        "where": (d.get("where") or "").strip(),
        "affect": {"valence": v, "arousal": a, "label": aff.get("label") or "中性"},
    }


def _pet_alias_names(conn):
    """别名表里已登记为宠物的名字（"团子"这种自造名只能靠它判非人）。

    entity_alias 的 (alias, target) 是「类别 → 具体名」，如 ("猫","团子")，
    所以宠物名在 **target** 这一侧，类别词在 alias 这一侧。
    """
    try:
        rows = conn.execute(
            "SELECT target FROM entity_alias WHERE alias IN "
            "('猫','猫咪','狗','狗狗','宠物','兔子','仓鼠','鹦鹉','金鱼','乌龟') "
            "OR kind IN ('pet','宠物')").fetchall()
        return {r["target"] for r in rows if r["target"]}
    except Exception:
        return set()


def resolve_persona(conn, name):
    if not name:
        return None
    # v14 护栏：第一人称代词不是实体。历史库里「我」被登记成 persona，
    # 把用户第一人称（我和周野吵架/我堂弟…）误绑到名为「我」的人物，
    # 与真正的对话者 persona 割裂。拦在唯一收敛点，gist 原文不动。
    if (getattr(config, "PERSONA_PRONOUN_GUARD", True)
            and str(name).strip() in config.SPEAKER_PRONOUNS):
        return None
    raw = str(name).strip()
    # v19：先归一再建卡。「我妈」「妈妈」「母亲」此前各自 ensure 出一张 personas，
    # 画像/关系/involves 边各记一半，哪张都不完整。归一只改实体表，
    # gist（原文证据）一个字不动。
    canon = entity.canonicalize(raw)
    if not canon:
        return None
    for r in conn.execute("SELECT id,canonical_name,aliases_json FROM personas"):
        aliases = json.loads(r["aliases_json"] or "[]")
        if canon == r["canonical_name"]:
            # 规范名命中：顺手把这次的原始叫法补进别名表，否则「母亲」永远
            # 只是靠 canonicalize 临时兜住，库里的别名表始终少一条。
            if raw != canon and raw not in aliases:
                aliases.append(raw)
                conn.execute(
                    "UPDATE personas SET aliases_json=? WHERE id=?",
                    (json.dumps(aliases, ensure_ascii=False), r["id"]))
            return r["id"]
        if raw == r["canonical_name"] or raw in aliases:
            return r["id"]
    pid = _uid("p")
    kind = entity.kind_of(canon, pet_names=_pet_alias_names(conn))
    aliases = [canon] if canon == raw else [canon, raw]
    conn.execute(
        "INSERT INTO personas(id,canonical_name,aliases_json,profile,kind) "
        "VALUES(?,?,?,?,?)",
        (pid, canon, json.dumps(aliases, ensure_ascii=False), "", kind),
    )
    return pid


def _rebond(conn, pids):
    """重算这批人物的关系性质（social / mentioned），见 entity.bond_of。

    只在写入后对本轮涉及的人物算一次：单个人物的事件数是个位到十几条，
    一次查询判得完；全库重算交给 ``tools/backfill_entities.py``。
    """
    for pid in pids or ():
        try:
            row = conn.execute(
                "SELECT canonical_name,kind FROM personas WHERE id=?", (pid,)).fetchone()
            if not row:
                continue
            if (row["kind"] or "person") != "person":
                continue
            if row["canonical_name"] in (config.COUNTERPART_NAME, "用户"):
                conn.execute("UPDATE personas SET bond='social' WHERE id=?", (pid,))
                continue
            gists = [r["gist"] for r in conn.execute(
                "SELECT e.gist FROM edges ed JOIN events e ON e.id=ed.source "
                "WHERE ed.target=? AND ed.type='involves' AND e.archived=0", (pid,))]
            has_rel = conn.execute(
                "SELECT 1 FROM relations WHERE a=? OR b=? LIMIT 1",
                (pid, pid)).fetchone() is not None
            bond = entity.bond_of(gists, has_relation=has_rel)
            conn.execute("UPDATE personas SET bond=? WHERE id=?", (bond, pid))
        except Exception:
            pass


LAST_WRITE = {"new": 0, "dup": 0, "backdated": 0}   # 上次 remember 的新增 / 重复 / 补录作废条数

# ---- v19 主体推断 -----------------------------------------------------------
# 旧实现里 event.subject 从来不是「判」出来的，而是 identity.subject_of 的
# DEFAULT_SUBJECT。实测 211 条事件 187 条 counterpart，其中大量是
# 「我外甥上幼儿园」「周野说项目忙完带你去吃啥」这类第三方事实。subject 一错，
# perspective_hint 就会把别人的事说成「你」的事——这正是「你养了只金毛」的来源。
_SELF_MARK = ("我", "自己", "俺", "咱")
# 「你」指的是对话者本人，不是第三方；「他/她」才是第三方指示代词。
# 不区分的话「你摔倒了」会被判成 third_party，透视提示反过来把对方的事说成别人的。
_COUNTERPART_WORDS = frozenset({
    "你", "您", "你们", "对方", "用户", "用户本人", "你俩",
    getattr(config, "COUNTERPART_NAME", "用户"),
})
# 「我外甥」「我的表弟」：带第一人称领属的亲属称谓是**第三方**，不是说话人。
# 不能写成 ^我[汉字]{1,4}$——那样「我们组」也会命中，把用户自己团队的事
# 判成第三方。必须是「我 + 亲属词」。
def _kin_lead(name):
    n = str(name or "")
    if not n.startswith("我"):
        return False
    rest = n[2:] if n.startswith("我的") else n[1:]
    # 「我们组 / 我们一大家子」是群体称谓，不是「我的亲属」
    if rest.startswith("们"):
        return False
    return bool(rest) and len(rest) <= 5 and entity.kin_follows(rest)
# 「我们」= 共同经历；「我们组/我们一大家子」是群体，不算
_WE_SHARED = re.compile(
    r"我们(?!组|部门|团队|班|公司|单位|宿舍|家族|一大家子|一家)")
# 省略主语的句子：时间/计划词开头，主语要从上文继承
_LEAD_OMIT = re.compile(
    r"^(?:最近|现在|目前|今天|昨天|前天|明天|后天|周末|"
    r"今年|去年|明年|上个月|这个月|下个月|上周|这周|下周|"
    r"打算|准备|计划|原本|本来|后来|然后|接着|同时)")


def _nonperson_names(conn):
    """库里已知的非人实体（宠物/群体/节日/职业泛称）名字集合。

    「团子上周绝育了」的 who 只有一个宠物名。不排除的话这条会被判成
    third_party，生成层就会用「他」去指一只猫。
    """
    out = set(_pet_alias_names(conn))
    try:
        rows = conn.execute(
            "SELECT canonical_name,aliases_json,kind FROM personas").fetchall()
    except Exception:
        return out
    for r in rows:
        kind = r["kind"] or entity.kind_of(r["canonical_name"])
        if kind == "person":
            continue
        out.add(r["canonical_name"])
        try:
            out.update(json.loads(r["aliases_json"] or "[]"))
        except Exception:
            pass
    return {n for n in out if n}


def _infer_subject(seg, who, speaker, explicit=None, nonperson=()):
    """逐句推断这条记忆属于谁。显式传入永远优先。

    核心判据只有一条：**这句话里有没有说话双方**。

    - 有「我 / 我们」→ counterpart（句首「我们」时是 shared）；
    - 有「你」→ counterpart；
    - 两者都没有、只剩第三人称 → third_party。

    这条界来自两次真实误判：
      · 「阿岚养了只狗叫布丁」不含对方，却被标 counterpart 并渲染成
        [关于对方] 阿岚养了只狗 → 模型答「你养了只金毛」。third_party 才挡得住。
      · 「周野今天给我带了早餐」含「我」，一度被判 third_party——但生成层
        该说「周野给你带了早餐」，判成第三方反而把人称搞丢。
    所以「谁在句首」不重要，「双方有没有参与」才是分界线。

    唯一例外是领属称谓：「我外甥上幼儿园」里的「我」是限定词，指第三方，
    必须排除在「我参与」之外。
    """
    if explicit is not None:
        return identity.normalize_subject(explicit)
    if str(speaker or "").strip().lower() in config.AGENT_SPEAKERS:
        return "self"
    s = seg or ""
    if not s:
        return config.DEFAULT_SUBJECT

    names = [str(w) for w in (who or []) if w]
    # 1) 句首「我们」→ 共同经历。只在句首算：句尾补一句「我们认识很久了」
    #    不算，否则整条会被改写成共同经历，gist 里的「我」也不再落到对话者。
    if _WE_SHARED.match(s):
        return "shared"
    # 2) 「我外甥…」→ 第三方。直接从句子看领属，不依赖 who 抽到了什么
    #    （回填历史 gist 时 who 里只剩归一后的「外甥」，_kin_lead 会失效）。
    if (s.startswith("我") and not s.startswith("我们")
            and entity.kin_follows(s[1:5])):
        return "third_party"
    for w in names:
        if _kin_lead(w):
            return "third_party"
    # 3) 说话人参与 → counterpart
    if first_self_of(s) >= 0:
        return "counterpart"
    # 4) 对话者参与 → counterpart
    parlance = str(speaker or "").strip()
    if "你" in s or "您" in s or (parlance and parlance in s):
        return "counterpart"
    # 4.5) 省略主语的句子：时间/计划开头、既没「我」也没「你」，主体是从上文
    #      继承来的（「周末打算和阿岚去逛花卉市场」）。判成第三方会让生成层
    #      把对方自己的计划说成别人的事。
    if _LEAD_OMIT.match(s):
        return "counterpart"
    # 5) 只剩第三人称：确实有人物实体才算第三方。否则维持默认——
    #    「清明回了老家扫墓」的 who 是误抽的时间词，不该因此变成第三方。
    skip = set(nonperson or ())
    if any(entity.is_person(w) and w not in skip
           and w not in _SELF_MARK
           and w not in _COUNTERPART_WORDS and not _kin_lead(w)
           for w in names):
        return "third_party"
    return config.DEFAULT_SUBJECT


def first_self_of(text):
    """第一人称标记在文本里的首个位置；没有返回 -1。"""
    return min([text.find(p) for p in _SELF_MARK if p in text], default=-1)


def _replace_self(seg, voice):
    """把 gist 里的第一人称「我」换成 voice，但跳过领属用法（我外甥 / 我妈）。

    旧实现用一条否定前瞻正则枚举「我」后面不能跟的字，漏了「外 / 堂 / 表 /
    侄 / 公 / 婆 / 嫂 / 夫 / 妻」——「我外甥上幼儿园」会被写成「你外甥上
    幼儿园」，第三方的事直接变成对方的事。换成按词表判定，加词就够。
    """
    if not voice:
        return seg
    out, i, n = [], 0, len(seg)
    while i < n:
        if seg[i] == "我":
            after = seg[i + 1:i + 5]
            if after.startswith("的"):
                # 「我的外甥」是第三方，「我的猫」是说话人自己的
                keep = entity.kin_follows(after[1:])
            else:
                # 「我家 / 我们」保留原样：写成「用户家猫」「用户们」都是脏 gist，
                # 这两类既不是第三方，硬换也不通顺。
                keep = (entity.kin_follows(after)
                        or after.startswith(("家", "们")))
            out.append("我" if keep else voice)
        else:
            out.append(seg[i])
        i += 1
    return "".join(out)


def remember(conn, text, speaker=None, tags=None, subject=None,
              affect_strength=None):
    """写入记忆。gist 忠实原文；speaker 把"我"解析为对话者；tags 为自定义标签。

    v13：``affect_strength``（0~1）来自皮层情绪层，强情绪提高编码显著性；
    不传时完全维持旧行为，保证存量单测与写入路径不变。

    v12：``subject`` 标注「这条记忆属于谁」，与 speaker（这话谁说的）正交。
    不传时维持旧行为（counterpart，旧系统记录的是对方）；speaker 为 agent 侧
    时自动标 self，便于陪伴 agent 沉淀自己的承诺 / 反思 / 共同经历。
    """
    created = _now()
    new_ids = []
    touched_pids = set()   # 本轮涉及的人物：写完后重算 kind 无关的 bond（社交/提及）
    LAST_WRITE["new"] = LAST_WRITE["dup"] = 0
    _GREET = {"你好", "您好", "hi", "hello", "在吗", "在么", "谢谢", "再见", "早", "晚安", "嗯", "哦"}
    carried = []       # 上文主语：细切后的子句常省略主语（"性格开朗，喜欢打篮球"）
    ctx_date = None    # 上一条事件日期："第二天/次日"要以它为基准
    pronouns = {"我", "自己", "本人", "俺"}
    for seg in segment(text):
        if len(seg) < 4 or seg.lower() in _GREET:
            continue
        ext = extract(seg, base_date=ctx_date)
        # v19：主体逐句推断，不再全段共用一次默认值。一句话里的两件事可以分属
        # 不同主体（「周野说要来。我今天摔了一跤」），整段算一次必然把其中一件记错。
        if getattr(config, "SUBJECT_VIEW", False):
            sub = _infer_subject(seg, ext["who"], speaker, explicit=subject,
                                 nonperson=_nonperson_names(conn))
        else:
            sub = identity.subject_of(speaker, subject)
        voice = identity.first_person_target(speaker, sub)
        who = [voice if (voice and w in pronouns) else w for w in ext["who"]]
        who = [w for w in who if w]
        if who:
            carried = who
        elif carried:
            who = list(carried)      # 省略主语 → 继承同一段上文
        elif voice:
            who = [voice]
        ext["who"] = who
        if ext.get("occurred_at"):
            ctx_date = ext["occurred_at"]
        emb = embed.embed(seg)

        # 第一人称落到 voice（v13 由 subject 决定），「我们」保持原样。
        # 但「我妈 / 我外甥 / 我堂弟」这类领属称谓不能替换——换掉会得到
        # 「你外甥上幼儿园」这种脏 gist，把第三方的事算到对方头上。
        gist = _replace_self(seg, voice) if voice else seg
        gist = re.sub(r"[（(]\s*(主语缺失|未知|无)\s*[)）]", "", gist).strip()

        # 只有「关于对方」的记忆才把对话者补进人物绑定；关于我自己时
        # 「我」不是第三方 persona，硬塞进去会污染画像与关系图。
        if voice and sub == "counterpart" and voice in gist and voice not in ext["who"]:
            ext["who"] = list(ext["who"]) + [voice]
        # 地名误进 who（「去大理住了五天」→ who=["大理"]）会建出一张叫「大理」
        # 的人物卡。当原文 where 抽到同一个词时按地点处理，不当人。
        where = (ext.get("where") or "").strip()
        if where:
            ext["who"] = [
                w for w in ext["who"]
                if not (w == where or (len(w) >= 2 and where.startswith(w)
                                       and entity.kind_of(w) != "person"))]
        # 模型漏抽时按库内已知人名补绑（C3："婚礼改到下周六"这类句子常漏主语）
        for n in [r["canonical_name"] for r in conn.execute(
                "SELECT canonical_name FROM personas WHERE canonical_name<>''")]:
            if n in gist and n not in ext["who"]:
                ext["who"].append(n)

        # 与已知记忆比对：既用于去重，也用于计算"惊讶"（预测误差）
        hist = conn.execute(
            "SELECT id,gist,embedding,fact_key,occurred_at,created_at FROM events "
            "WHERE embedding IS NOT NULL AND superseded_by IS NULL").fetchall()
        dup_id, best_id, best_sim, best_overlap = None, None, 0.0, 0
        corr_id = None   # 可被"更正"的旧说法（否定/改口）
        corr_overlap = -1
        gist_cbi = _content_bigrams(gist)
        # v13 结构化事实键：同主体 + 同单值属性 + 不同值 → 直接取代，不再靠文字重合。
        # 抽不出键 new_key=None，全程退回原有 F1~F7，不影响任何既有行为。
        # 主体名要把本轮 who 与 voice 一起传：首次写入时本条的 persona 还没建出来
        # （绑定在 INSERT 之后），只查表会让第一条永远抽不出键。
        _subj_names = factkey.persona_names(conn)
        if voice:
            _subj_names.append(voice)
        _subj_names.extend([w for w in ext["who"] if w])
        # conn 必须传：主体归一要读人设卡才知道「你」就是对话者，否则会写出
        # s="你" 这种键，跟存量 s="counterpart" 永远匹配不上（同值判重失效）。
        new_key = factkey.extract(gist, conn=conn, names=_subj_names)
        key_dup_id, key_conf_id = None, None
        key_conf_eff = ("", "")          # 被冲突的那条旧事实生效时间（判方向用）
        for r in hist:
            s = _cos(emb, db.unpack(r["embedding"]))
            old = r["gist"] or ""
            # F6：best 命中时同步记录它自己的内容词重合度，供相似取代分支做
            # 下限保护（原实现循环末尾的 overlap 属于最后一条历史，与 best 无关）。
            ov = len(gist_cbi & _content_bigrams(old))
            if s > best_sim:
                best_id, best_sim, best_overlap = r["id"], s, ov
            is_corr = _has_correction(gist) or _has_recant(gist)
            # 子串 → 新句已被旧句包含，算重复；更正句必须入库（要标记旧说法），不走相似去重
            if dup_id is None:
                if old == gist or (old and gist in old):
                    dup_id = r["id"]
                elif (not is_corr and old and s >= config.DUP_SIM
                      and len(gist) >= 0.6 * len(old)):
                    dup_id = r["id"]
            # 否定/改口/收回：更正句是旧说法的反义，向量相似度天生偏低，
            # 不能用余弦卡阈值（否则「黑猫→橘猫」这种反义更正会被漏判成两条并存）。
            # 改用语义判定：更正词 + 共享实体(内容 2-gram 重合) + 同人物；
            # 选重叠最高的旧事件取代，使「小黑猫」被「橘猫」精准顶替而非误伤别的。
            overlap = ov
            cand = None
            # F5（2026-11-15 富数据灌入后）：更正句（含 其实/不是/并没有…）取代旧说法，
            # 判定条件 = 旧句本身不是更正句 且 新旧共享 >= 2 个「内容 2-gram」（剔除通用词）。
            # **不依赖「同人物」**：用户几乎每条记忆都涉及对话者本人，靠人物边既脆弱
            # （「团子」常被解析成两个不同 persona，导致猫色反义更正漏判）又毫无防护
            # （「进公司时的导师」与「公司摄影比赛」经对话者本人边被误判成被更正）。
            # 反义更正天然共享 >= 2 个具体 2-gram（黑猫→橘猫：团子+黑猫；二等奖→三等奖：
            # 摄影+比赛+等奖），单字节共享（公司/摄影）正好 =1 被挡掉，精准且不误伤。
            if (_has_correction(gist) and not _has_correction(old)
                    and overlap >= 2):
                cand = r["id"]
            # "我说错了/更正"：连旧句自己就是更正句也能继续往下取代（取代链不断）。
            # 同样不依赖人物边：靠余弦（反义句天生低，不会误触发）或共享 >=3 个
            # 内容 2-gram 判同事实，避免单字节共享（公司/摄影）误伤不相关记忆。
            elif (_has_recant(gist)
                  and (s >= config.SUPERSEDE_RECANT_SIM and overlap >= 2
                       or overlap >= 3)):
                cand = r["id"]
            if cand is not None and (corr_id is None or overlap > corr_overlap):
                corr_id, corr_overlap = cand, overlap
            # v13：结构化比对。同键同值 → 复述（判重）；同键不同值且属性单值 →
            # 旧值作废（取代）。多值属性在这里永远不触发，见 factkey.conflict。
            if new_key and r["fact_key"]:
                old_key = factkey.loads(r["fact_key"])
                if old_key:
                    if factkey.conflict(new_key, old_key):
                        # 同键不同值可能命中多条（链式更正过几次）。取**有效时间最新**
                        # 的那条作当前值；否则可能挑中一条中间态，把真正的现值留在库里。
                        eff = _effective_at(r["occurred_at"], r["created_at"])
                        if key_conf_id is None or eff > key_conf_eff:
                            key_conf_id, key_conf_eff = r["id"], eff
                    elif key_dup_id is None and factkey.same(new_key, old_key):
                        key_dup_id = r["id"]
        surprise = 1.0 if not hist else round(max(0.0, 1.0 - best_sim), 3)

        dup_id = dup_id or key_dup_id
        if dup_id:
            forgetting.reinforce(conn, dup_id, amount=0.2)
            conn.commit()
            new_ids.append(dup_id)
            LAST_WRITE["dup"] += 1
            continue

        eid = _uid("evt")
        aff = ext["affect"]
        salience = round(0.5 * abs(aff["valence"]) + 0.5 * aff["arousal"], 3)
        if affect_strength is not None:
            # 情绪作用于**编码**：强情绪的事件更不容易被时间冲淡。
            salience = round(min(1.0, salience + float(affect_strength)
                                 * config.AFFECT_SALIENCE_GAIN), 3)

        conf = 0.9
        if any(w in gist for w in _HEDGE):
            conf = 0.6          # "听说/好像/可能"：不确定的说法
        if not ext["who"]:
            conf = min(conf, 0.8)  # 连主语都没抽到，可信度打折

        conn.execute(
            "INSERT INTO events(id,created_at,occurred_at,time_text,place,gist,affect_json,"
            "salience,embedding,confidence,provenance,consolidation_stage,surprise,subject,"
            "fact_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, created, ext["occurred_at"], ext["when"], ext["where"], gist,
             json.dumps(aff, ensure_ascii=False), salience, db.pack(emb),
             conf, "user_input", "raw", surprise, sub, factkey.dumps(new_key)),
        )

        # 记忆冲突：旧事件标记「已被更新」（保留历史，不删除）。
        # 必须同人物才算"同一事实"——纯相似会把"妈妈腰疼"和"我做咖啡"连成一对。
        target = None
        # 方向：默认"后写入的更可信"，但补录更早发生的事实时要反过来。
        new_eff = _effective_at(ext.get("occurred_at"), created)
        reverse = False
        # F6（2026-11-15 200 条数据后）：相似取代除了余弦+同人物，还必须有
        # >=2 个内容 2-gram 重合。否则「周野还没同意」与「周野买花道歉」这类
        # 共享单字主题词、语义无关的记忆会被 0.85 余弦+同人物误判成同一事实取代。
        # 真正的同义改写/复述天然共享 >=2 个具体内容词，不受影响。
        if key_conf_id:
            # 结构化冲突优先级最高：它知道"这是同一个人的同一个单值属性"，
            # 比"文字像不像"可靠。该取代没取代会让用户同时"住北京"和"住上海"。
            target = key_conf_id
            if new_eff < key_conf_eff:
                # 补录：新说的是更早的状态（"2025 年 3 月我搬到杭州"），
                # 它才是该被作废的那条，当前值继续有效。
                reverse = True
        elif (best_id and best_sim >= config.SUPERSEDE_SIM
                and best_overlap >= 2
                and _shares_persona(conn, best_id, ext["who"])):
            target = best_id
        elif corr_id:
            target = corr_id
        if target:
            # 反转时 (old,new) 互换：新事件指向旧值，旧值不被标记。
            victim, keeper = (eid, target) if reverse else (target, eid)
            conn.execute("UPDATE events SET superseded_by=? "
                         "WHERE id=? AND superseded_by IS NULL", (keeper, victim))
            if reverse:
                LAST_WRITE["backdated"] += 1

        frag_specs = [("event", gist)]
        if ext["where"]:
            frag_specs.append(("place", ext["where"]))
        for w in ext["who"]:
            frag_specs.append(("subject", w))
        if aff.get("label"):
            frag_specs.append(("emotion", aff["label"]))
        for seq, (role, txt) in enumerate(frag_specs, 1):
            fid = _uid("f")
            conn.execute(
                "INSERT INTO fragments(id,text,modality,embedding,salience) VALUES(?,?,?,?,?)",
                (fid, txt, "text", None, salience),
            )
            conn.execute(
                "INSERT INTO bindings(event_id,fragment_id,role,seq) VALUES(?,?,?,?)",
                (eid, fid, role, seq),
            )

        for w in ext["who"]:
            pid = resolve_persona(conn, w)
            if pid:
                touched_pids.add(pid)
                conn.execute(
                    "INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,?)",
                    (eid, pid, "involves", 1.0),
                )

        if best_id and best_sim >= config.SIM_EDGE_THRESHOLD:
            conn.execute(
                "INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,?)",
                (eid, best_id, "semantic", round(best_sim, 4)),
            )

        new_ids.append(eid)
        LAST_WRITE["new"] += 1

        # 主题标签：规则打标（旅游/工作/家庭…），自定义标签优先保留
        try:
            tagging.add(conn, eid, tagging.tags_for(gist))
            if tags:
                tagging.add(conn, eid, tags, "user")
        except Exception:
            pass
        # 实体别名：从"养了一只猫，名字叫团子"里学「猫 → 团子」
        try:
            alias.learn(conn, gist)
        except Exception:
            pass
        # 先把事件落库，再抽关系：关系抽取要调 LLM，不能占着写锁
        conn.commit()

        try:
            persona.extract_relations(conn, seg, speaker=voice or speaker)
        except Exception:
            pass

        # 目标识别：顺带登记「打算/计划/准备」（规则抽取，不额外调模型）
        try:
            from . import goals
            goals.observe(conn, gist, eid)
            conn.commit()
        except Exception:
            pass

    # 关系性质：本轮涉及的每个人物按全量证据重算一次 social / mentioned
    # （「刘慈欣」只在"看他的小说"里出现 → mentioned，不进社交图与人物过滤）
    _rebond(conn, touched_pids)

    # 情感接线：把本轮事件的平均情绪写进状态机（失败静默，不影响写入）
    try:
        from . import emotion
        affs = []
        for eid in new_ids:
            row = conn.execute("SELECT affect_json FROM events WHERE id=?", (eid,)).fetchone()
            if row and row["affect_json"]:
                affs.append(json.loads(row["affect_json"]))
        if affs:
            v = sum(float(a.get("valence", 0) or 0) for a in affs) / len(affs)
            a2 = sum(float(a.get("arousal", 0) or 0) for a in affs) / len(affs)
            emotion.EmotionState(conn).update(v, a2, weight=0.25)
    except Exception:
        pass

    conn.commit()
    return new_ids


def rebuild_factkey_conflicts(conn, dry_run=False):
    """用结构化事实键重建取代链：同主体 + 同单值属性 + 不同值，保留最新。

    与 ``rebuild_conflicts`` 的分工：

    - ``rebuild_conflicts`` 找的是**带更正词**的旧说法（"其实不是/我说错了"），
      靠人物分组 + 文本重合；它漏掉的是「用户直接换了个说法、不说'其实'」。
    - 本函数不要求更正词、不依赖人物边、不卡余弦——只要两个事件是同一个人的
      同一个单值属性且值不同，就认为新的作废旧的。

    多值属性（喜好/饲养/行踪/计划…）不参与：喜欢猫不妨碍喜欢狗。

    返回被标记的列表；``dry_run=True`` 时只返回不落库。
    """
    rows = conn.execute(
        "SELECT id,gist,created_at,occurred_at,fact_key FROM events "
        "WHERE archived=0 AND superseded_by IS NULL AND fact_key IS NOT NULL "
        "ORDER BY created_at").fetchall()
    groups = {}
    for r in rows:
        k = factkey.loads(r["fact_key"])
        if not k or not k.get("f"):
            continue                     # 多值属性不参与取代
        groups.setdefault((k.get("s"), k.get("a")), []).append((r, k))

    marked = []
    for (subject, attr), items in groups.items():
        if len(items) < 2:
            continue
        # 按事实生效时间排序，不按写入顺序：补录的旧事实不该被当成最新值。
        items.sort(key=lambda x: _effective_at(x[0]["occurred_at"], x[0]["created_at"]))
        latest_r, latest_k = items[-1]
        for r, k in items[:-1]:
            if factkey.same(k, latest_k):
                continue                  # 同值复述，交给去重逻辑
            marked.append({"old": r["id"], "new": latest_r["id"],
                           "subject": subject, "attr": attr,
                           "old_value": k.get("v"), "new_value": latest_k.get("v"),
                           "old_gist": r["gist"], "new_gist": latest_r["gist"]})
    if dry_run or not marked:
        return marked
    for m in marked:
        conn.execute(
            "UPDATE events SET superseded_by=? WHERE id=? AND superseded_by IS NULL",
            (m["new"], m["old"]))
    conn.commit()
    return marked


def backfill_factkeys(conn, limit=None, dry_run=False, force=False):
    """给存量事件回填 fact_key（v13 迁移用）。

    只写 ``fact_key`` 一列，**不动 gist、不动 superseded_by、不建删任何事件**。
    抽不出的留 NULL —— store 见到 NULL 会退回原有的文本判据，不会因为没键就
    漏判，也不会误判。

    ``force=True`` 重算已有键（口径改过之后需要刷一遍）。
    """
    sql = ("SELECT id,gist,fact_key FROM events WHERE archived=0")
    if limit:
        sql += " LIMIT %d" % int(limit)
    rows = conn.execute(sql).fetchall()
    names = factkey.persona_names(conn)
    filled, skipped, changed = 0, 0, []
    for r in rows:
        if r["fact_key"] and not force:
            continue                       # 已有键，不覆盖
        # conn 必须传：主体归一要靠它读人设卡，否则「你」认不出是对话者，
        # 存量键会写成「你·居住地=…」而新写入写成「counterpart·居住地=…」，永远匹配不上。
        k = factkey.extract(r["gist"], conn=conn, names=names)
        if not k:
            skipped += 1
            continue
        changed.append((factkey.dumps(k), r["id"]))
        filled += 1
    if not dry_run and changed:
        conn.executemany("UPDATE events SET fact_key=? WHERE id=?", changed)
        conn.commit()
    return {"total": len(rows), "filled": filled, "skipped": skipped,
            "samples": [{"gist": next((x["gist"] for x in rows if x["id"] == i), ""),
                         "key": factkey.loads(k)} for k, i in changed[:5]]}


def rebuild_conflicts(conn, max_pairs=3000):
    """重建更正链（C1）：同人物 + 带更正词 + 话题重合 → 旧说法标为已更新。

    写入路径已做一次，这里补历史漏判：第二次、第三次更正常常因为"当前权威节点"
    已被标 superseded 而与新说法比较不到，导致多条互斥事实同时活跃。
    """
    rows = conn.execute(
        "SELECT id,gist,embedding,created_at FROM events "
        "WHERE archived=0 AND embedding IS NOT NULL ORDER BY created_at").fetchall()
    groups = {}
    names = {p["id"]: p["canonical_name"] for p in conn.execute(
        "SELECT id,canonical_name FROM personas WHERE canonical_name<>''")}
    for r in rows:
        tids = {e["target"] for e in conn.execute(
            "SELECT target FROM edges WHERE source=? AND type='involves'", (r["id"],))}
        # 按"人物名"分组：同一个人可能被建成多个 persona（重复条目），用 id 分组会漏配对
        # 对话者名出现在绝大多数记忆里，按它分组必然是 O(n²)，且没有
        # "同一人物矛盾"的意义。v13：从配置取，不再写死「用户」。
        keys = {names[t] for t in tids if t in names} - {config.COUNTERPART_NAME,
                                                       config.SELF_NAME}
        if not keys:
            continue
        for k in keys:
            groups.setdefault(k, []).append(r)

    marked, pairs = 0, 0
    for evs in groups.values():
        evs = evs[-40:]                        # 只看最近 40 条，旧说法早已收口
        for i in range(len(evs)):
            for j in range(i + 1, len(evs)):
                if pairs >= max_pairs:
                    return marked
                pairs += 1
                old, new = evs[i], evs[j]          # 按 created_at 升序
                if not (_has_correction(new["gist"]) or _has_recant(new["gist"])):
                    continue
                inter = _content_bigrams(new["gist"]) & _content_bigrams(old["gist"])
                # F5：共享实体(内容 2-gram)必须 >= 2 才判旧说法被取代，
                # 单字节共享（「公司」「摄影」等）只是同主题词，不能误伤不相关记忆。
                if len(inter) < 2:
                    continue
                sim = _cos(db.unpack(old["embedding"]), db.unpack(new["embedding"]))
                recant = _has_recant(new["gist"])
                if recant:
                    # 明确"我说错了"：话题词重合 3 个就够，或重合 1 个且向量不算太远
                    if len(inter) < 3 and sim < config.SUPERSEDE_RECANT_SIM:
                        continue
                # 非 recant 的普通更正词（其实/不是/并没有…）：同组(同人物) +
                # 共享实体(内容 2-gram>=1) 即判旧说法被取代，**不卡余弦**——
                # 反义句向量相似度天生低，"黑猫→橘猫"会被漏判。
                cur = conn.execute("SELECT superseded_by FROM events WHERE id=?",
                                   (old["id"],)).fetchone()
                if cur and cur[0]:
                    continue                    # 已有更新的说法，保留原链
                conn.execute("UPDATE events SET superseded_by=? WHERE id=?",
                             (new["id"], old["id"]))
                marked += 1
    if marked:
        conn.commit()
    return marked