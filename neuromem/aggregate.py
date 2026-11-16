"""聚合层：回答「多少个 / 都有哪些」这类**没有单条记忆能回答**的问题。

为什么单靠 top_k 召回必然失败：这类问句的答案不在任何一条记忆里，而在
「N 条记忆的集合性质」里。「我一共养过几只宠物」「我都去过哪些城市」
「我有几个朋友」——召回再好也只能给出 3~5 条最像的，剩下的被 top_k 截掉，
模型于是用「只记得……」的口吻答一个残缺的清单，比直接说不知道更糟。

这一层的做法是**确定性的**：识别聚合意图 → 全量扫描（不截断）→ 分组统计 →
把「集合」本身作为一条候选交给生成层。不调 LLM，所以没有幻觉，也没有延迟代价。

刻意不做的事：不猜「几次礼物」这种需要语义归类的计数——规则数不准，
交给生成层看明细自己数。宁可给全量明细，也不给错误的数字。
"""
import json
import re
from datetime import datetime

from . import config, timeparse

# ---- 意图识别 --------------------------------------------------------------
# 计数/枚举/范围三类问句的共同点是「要集合」而不是「要某一条」。
_AGG_RE = re.compile(
    r"几个|多少|几次|几回|几件|几条|几种|几门|几位|"
    r"一共|总共|合计|加起来|"
    r"所有|全部|凡是|"
    r"都有哪些|有哪些|都有什么|都有谁|都有谁呢|都是谁|"
    r"分别|各个|逐个|列出|列一下|列个|清单|统计|"
    r"都(?:去|做|干|吃|看|买|玩|学|聊)过|"
    # 「我在学什么 / 最近在忙什么」：答案是一组并行的事，单条召回只会挑到一件
    r"在(学|忙|弄|搞|做|看|写|练)(?:些)?什么|(?:学|忙|做)(?:过|了)(?:些)?什么|"
    # 「我最近在做什么」也是聚合：答案是一段时间内的一组事，不是某一条
    r"最近.{0,4}(?:做|干|忙|干啥|做什么|干嘛)|这(?:段|些)时间.{0,4}(?:做|干|忙)")

# 「我有几个朋友」→ 数 relations，而不是数事件。这是唯一能精确回答的计数。
_REL_COUNT = re.compile(
    r"(?:几个|多少|几位|几门)(?:个|位|门)?\s*"
    r"(朋友|同事|亲戚|家人|同学|室友|男朋友|女朋友|孩子|兄弟姐妹)")
# 「我都有哪些朋友」→ 要的是**名单**，不是「社交」主题的事件统计。
# 旧版落到 tag「社交」，于是把「同学会比孩子和房子」「和朋友约羽毛球」这类
# 事件当成朋友清单——问的是人，答的是事。
# 必须带疑问词才算：否则「我是他朋友」这种陈述句也会被当聚合问句。
_REL_LIST = re.compile(
    r"(?:哪些|什么|谁)[^，。？?]{0,6}?"
    r"(朋友|同事|亲戚|家人|同学|室友|兄弟姐妹|长辈|发小|闺蜜)"
    r"|(朋友|同事|亲戚|家人|同学|室友|兄弟姐妹|长辈|发小|闺蜜)"
    r"(?:都|一共)?(?:有|是)(?:哪些|什么|谁)")

# ---- 概念口径（v20）-------------------------------------------------------
# 「我送过几次礼物」这类计数，规则数不准的根源**不在同义词**，在**方向**：
# 「你妈给你寄了笋干」「大伯每年给你寄橘子」都含"寄"，但那是收到，不是送出。
# 所以这里只做一件事——把方向判出来，口径写清楚，次数交给生成层看明细数。
# 不做语义聚类：宁可少算一条，也不编一个数字。
_GIVE_VERB = re.compile(r"送|寄|赠|快递|织了|刻了|绣了|手作|订了")
_RECV_FRAME = re.compile(r"(?:给|向|朝)(?:你|我|用户)[^，。；]{0,6}?(?:送|寄|赠|快递)")
# 每个概念两组词：
#   ``trig``  问句里的触发词（判"问的是不是这个概念"）
#   ``words`` 事件里的匹配词（取全集）。**必须包含动词**，否则「织了条围巾送妈」
#             这种没有"礼物"二字的送礼事件会漏——那正是旧版统计不准的主因。
_CONCEPTS = {
    "礼物": {
        "label": "送礼",
        "trig": re.compile(r"礼物|送礼|伴手礼|生日礼|红包|花束|纪念品"),
        "words": ("礼物", "送礼", "伴手礼", "生日礼", "红包", "花束", "纪念品",
                  "送", "寄", "赠"),
        "verb": True,
    },
    "生病": {"label": "生病",
             "trig": re.compile(r"生病|发烧|感冒|住院|看病|体检|牙疼|腰痛"),
             "words": ("生病", "发烧", "感冒", "住院", "看病", "体检", "牙疼", "腰痛")},
    "加班": {"label": "加班", "trig": re.compile(r"加班|熬夜|通宵"),
             "words": ("加班", "熬夜", "通宵")},
    "搬家": {"label": "搬家", "trig": re.compile(r"搬(?:过|了|家)|换房子|新家"),
             "words": ("搬家", "搬了", "搬过", "新家", "换房子")},
    "面试": {"label": "面试", "trig": re.compile(r"面试|笔试|offer"),
             "words": ("面试", "笔试", "offer")},
    "吵架": {"label": "吵架", "trig": re.compile(r"吵架|冷战|闹别"),
             "words": ("吵架", "冷战", "闹别")},
}
# 问句里剥离这些虚词后剩下的实词，当字面口径（没登记过的概念走这条）
_Q_STOP = re.compile(
    r"我|你|他|她|我们|用户|请问|帮我|告诉我|还记得|记得|"
    r"送过|寄过|买过|去过|做过|吃过|见过|"
    r"几次|几回|几个|几位|几件|多少|次|回|一共|总共|合计|"
    r"都有|有过|有|过|的|了|吗|呢|吧|是|什么|哪些")

# 明确的时间范围词：这些能精确圈定，交给 timeparse
_RANGE_HINT = re.compile(
    r"昨天|今天|前天|本周|这周|上周|这个月|上个月|本月|"
    r"今年|去年|前年|最近\d+天|最近\d+个?月|过去\d+")

_PUNCT = re.compile(r"[，,。；;、\s]+")


def _concept_of(query):
    """认出「要数的是哪个概念」，返回 (concept, spec)。

    登记过的概念按 trig 正则匹配；没登记过的把问句壳剥掉，剩下的实词当字面口径。
    """
    q = query or ""
    for key, spec in _CONCEPTS.items():
        if spec["trig"].search(q):
            return key, spec
    rest = _PUNCT.sub(" ", _Q_STOP.sub(" ", q)).strip()
    rest = re.sub(r"\s+", "", rest)
    if 2 <= len(rest) <= 6:
        return rest, {"label": rest, "words": (rest,), "verb": False}
    return "", None


def _concept_rows(conn, query, concept, spec):
    """按概念口径取全量事件；送礼这类还要判方向（送出 / 收到）。"""
    words = spec.get("words") or (concept,)
    like = " OR ".join(["gist LIKE ?"] * len(words))
    rows = conn.execute(
        "SELECT id,gist,occurred_at,created_at,place,subject FROM events "
        "WHERE archived=0 AND superseded_by IS NULL AND (%s) "
        "ORDER BY COALESCE(occurred_at,created_at) DESC" % like,
        tuple("%" + w + "%" for w in words)).fetchall()
    # 只数「我」的事：「我表哥说加班比我还狠」「我爸年轻时候也加班」都是别人的
    # 加班，算进「我加过几次班」就跑偏了。subject 由写入时逐句判定（v19）。
    rows = [r for r in rows if (r["subject"] or "") != "third_party"]
    if not rows:
        return [], False
    if not spec.get("verb"):
        return rows, False
    # 方向：先看有没有「给你/给我」框架（收到）。有则整条按"收到"处理，
    # 否则只要句中有给予动词就算送出。命中给予动词但没被收录的，
    # 是既无「给你…寄」也无 give 动词的纯名词句（"生日礼物"）——保留。
    out = []
    for r in rows:
        g = r["gist"] or ""
        if _RECV_FRAME.search(g):
            continue          # 「你妈给你寄了笋干」= 收到
        if _GIVE_VERB.search(g) or any(w in g for w in words):
            out.append(r)
    return out, True


# ---- 动词字面口径 ----------------------------------------------------------
# 动词字面匹配的去噪：**只对多义动词**（「学」还会出现在小学/同学/大学里），
# 单义动词不需要也不该加词表。
_VERB_NOISE = {
    "学": ("小学", "中学", "大学", "同学", "留学", "学校", "学期", "学年", "学生会"),
}
# 真·「在学」的句式：命中的排在前面，只是"句子里出现这个字"的排后面
_VERB_STRONG = {
    "学": ("在学", "学做", "自学", "学了", "学会", "跟着", "迷上", "报了"),
}


def _denoise_verb(rows, verb):
    noise = _VERB_NOISE.get(verb)
    if noise:
        rows = [r for r in rows if not any(n in (r["gist"] or "") for n in noise)]
    strong = _VERB_STRONG.get(verb)
    if strong:
        rows = sorted(rows, key=lambda r: 0 if any(
            k in (r["gist"] or "") for k in strong) else 1)
    return rows


# 「我在学什么 / 在忙什么」：要的是同一动作的所有实例，取动词做字面全集
_ACT_Q = re.compile(r"在(学|忙|弄|搞|做|看|写|练)(?:些)?什么|(学|忙|做)(?:过|了)(?:些)?什么")
# 「我最近在做什么」不限定主题，只要**时间上靠前**的一组事
_RECENT_RE = re.compile(r"最近|这几天|这段时间|近期|这两天")

# 聚合问句里的主题词：命中了就用「该主题的全库事件」当范围，这是最精确的
# 语义索引。「我一共养过几只宠物」→ tag 宠物 9 条 → 数出 3 只；
# 若靠向量池会算进 72 条工作/饮食记忆，计数直接失真。
_TAG_EXTRA = {
    "旅游": ("城市", "去哪", "去过", "玩过", "逛过"),
    "宠物": ("养过", "养了", "宠物"),
    "饮食": ("吃过", "吃遍"),
    "娱乐": ("看过", "玩过"),
    "工作": ("干过", "做过", "上班"),
}


def _query_tags(query):
    from . import tagging
    q = query or ""
    extra = [t for t, ws in _TAG_EXTRA.items() if any(w in q for w in ws)]
    return list(dict.fromkeys(extra + tagging.tags_for(q)))


def detect(query):
    """判断是不是聚合问句。返回 mode 或 None。

    mode:
      ``relation_count`` 数人（有几个朋友）—— 精确，走 relations 表
      ``relation_list``  列人（都有哪些朋友）—— 走 relations + 亲属人物卡
      ``count``          数事件
      ``list``           列清单
    """
    q = (query or "").strip()
    if not q:
        return None
    if _REL_COUNT.search(q):
        return "relation_count"
    if _REL_LIST.search(q) and _AGG_RE.search(q):
        return "relation_list"
    if not _AGG_RE.search(q):
        return None
    if re.search(r"几个|多少|几次|几回|几件|几条|几种|一共|总共|合计", q):
        return "count"
    return "list"


def _tags_of(conn):
    try:
        return {r["tag"] for r in conn.execute(
            "SELECT DISTINCT tag FROM event_tags WHERE tag IS NOT NULL")}
    except Exception:
        return set()


def _persona_ids_in_query(conn, query):
    """问句里点名的**真人**（非人实体不算，否则「我养的金毛」会去数狗）。"""
    try:
        from . import recall
        return recall._query_personas(conn, query)
    except Exception:
        return []


_REL_SYN = {
    # 闺蜜 / 发小 / 好友 都是「朋友」这一格：旧表只有"朋友/好朋友"，于是
    # 「我有个闺蜜叫阿岚」登记成 rel_type=闺蜜，「我都有哪些朋友」数出 0 个，
    # 整轮退回「社交」标签的事件统计——问的是人，答的是一堆饭局。
    "朋友": ("朋友", "好朋友", "好友", "闺蜜", "发小", "哥们", "兄弟"),
    "同事": ("同事", "搭档", "合作伙伴", "导师"), "同学": ("同学",),
    "室友": ("室友",), "亲戚": ("亲戚",), "家人": ("家人",),
    "男朋友": ("男朋友", "夫妻"), "女朋友": ("女朋友", "夫妻"),
    "孩子": ("儿子", "女儿"), "兄弟姐妹": ("兄弟姐妹", "哥哥", "姐姐"),
    "长辈": ("父子", "母子", "亲戚", "家人"),
    "发小": ("发小",), "闺蜜": ("闺蜜", "朋友"),
}
# 亲属称谓：这类人物卡本身就是关系证据（relations 表常抽不到 kinships）
_KIN_GROUP = {
    "家人": ("妈妈", "爸爸", "儿子", "女儿", "姐姐", "哥哥", "妹妹", "弟弟",
             "爷爷", "奶奶", "外公", "外婆", "老婆", "老公", "妻子", "丈夫"),
    "亲戚": ("表哥", "表姐", "表妹", "表弟", "堂哥", "堂姐", "堂弟", "堂妹",
             "舅舅", "姑姑", "小姨", "大伯", "叔叔", "小侄子", "侄女", "外甥",
             "外婆", "外公", "爷爷", "奶奶"),
    "长辈": ("妈妈", "爸爸", "爷爷", "奶奶", "外公", "外婆", "舅舅", "姑姑",
             "小姨", "大伯", "叔叔"),
}


def _rel_kinds(conn, kind_word):
    """某种关系的全部对象：relations 表 ∪ 亲属称谓人物卡。

    只认 bond='social' 的人——「刘慈欣」不该出现在「我都有哪些朋友」里。
    """
    syn = _REL_SYN.get(kind_word, (kind_word,))
    rows = conn.execute(
        "SELECT id,canonical_name,kind,bond FROM personas").fetchall()
    name_map = {r["id"]: r["canonical_name"] for r in rows}
    bond_map = {r["id"]: (r["bond"] or "social") for r in rows}
    kind_map = {r["id"]: (r["kind"] or "person") for r in rows}
    day = datetime.now().date().isoformat()
    out, seen = [], set()
    for r in conn.execute("SELECT a,b,rel_type,strength,until,since FROM relations"):
        if (r["rel_type"] or "") not in syn:
            continue
        until = (r["until"] or "")[:10]
        if until and until < day:
            continue
        if float(r["strength"] or 0) < 0.3:
            continue
        other = r["b"] if name_map.get(r["a"]) in ("用户", "你") else r["a"]
        nm = name_map.get(other)
        if not nm or nm in ("用户", "你") or nm in seen:
            continue      # 孤儿关系（人物卡已删）：不能把 id 当人名报出去
        if kind_map.get(other) != "person":
            continue      # 「和猫咪是朋友」不是社交名单
        if bond_map.get(other) == "mentioned":
            continue      # 只是被提及的人不进名单
        seen.add(nm)
        out.append({"name": nm, "type": r["rel_type"],
                    "since": (r["since"] or "")[:10], "source": "relations"})
    # 亲属称谓补位：妈妈 / 外婆 这些常只建了人物卡，没有 relations 边
    kset = set(_KIN_GROUP.get(kind_word, ()))
    if kset:
        for r in rows:
            nm = r["canonical_name"]
            if not nm or nm not in kset or nm in seen:
                continue
            if (r["kind"] or "person") != "person":
                continue
            if (r["bond"] or "social") != "social":
                continue
            seen.add(nm)
            out.append({"name": nm, "type": kind_word, "since": "",
                        "source": "persona"})
    return out


def _relation_count(conn, query, kind_word):
    """数「我有几个朋友」：直接数有效关系，不数事件。"""
    hits = _rel_kinds(conn, kind_word)
    if not hits:
        return None
    names = [h["name"] for h in hits]
    return {
        "kind": "rollup", "type": "rollup", "id": "rollup_rel_%s" % kind_word,
        "count": len(names),
        "gist": "关系表统计：登记为「%s」的人共 %d 位——%s。" % (
            kind_word, len(names), "、".join(names[:12])),
        "items": [], "fragments": [], "affect": {}, "salience": 0.9,
        "score": 0.0, "vec": 0.0, "kw": 0.0, "subject": "counterpart",
        "aggregate_mode": "relation_count",
    }


def _relation_list(conn, query, kind_word):
    """列「我都有哪些朋友」：给人名 + 关系 + 时间，不给事件统计。"""
    hits = _rel_kinds(conn, kind_word)
    if not hits:
        return None
    parts = []
    for h in hits[:16]:
        seg = h["name"]
        if h["type"] and h["type"] != kind_word:
            seg += "（%s）" % h["type"]
        if h["since"]:
            seg += "·%s 起" % h["since"]
        parts.append(seg)
    return {
        "kind": "rollup", "type": "rollup", "id": "rollup_relist_%s" % kind_word,
        "count": len(hits),
        "gist": "「%s」共 %d 位：%s。" % (kind_word, len(hits), "、".join(parts)),
        "items": [], "fragments": [], "affect": {}, "salience": 0.9,
        "score": 0.0, "vec": 0.0, "kw": 0.0, "subject": "counterpart",
        "aggregate_mode": "relation_list",
    }


def _event_rows(conn, query, pids, pool_ids=None):
    sql = ("SELECT id,gist,occurred_at,created_at,place,subject FROM events "
           "WHERE archived=0")
    if not getattr(config, "COEXIST_DEFAULT", False):
        sql += " AND superseded_by IS NULL"
    rows = conn.execute(sql + " ORDER BY COALESCE(occurred_at,created_at) DESC"
                        ).fetchall()
    # 相关性池：全库倒出来没有意义——「我一共养过几只宠物」会把 208 条记忆
    # 全算进去，主题分布变成「工作 23、饮食 21」。先按语义相关性收窄到
    # recall 排出的池子（通常 top_k*6 ≈ 100 条），统计才有焦点，
    # 又远大于 top_k=12，仍然保留了「不截断」的价值。
    if pool_ids:
        order = {eid: i for i, eid in enumerate(pool_ids)}
        kept = [r for r in rows if r["id"] in order]
        if len(kept) >= 3:
            kept.sort(key=lambda r: order[r["id"]])
            rows = kept
    rng = timeparse.parse_range(query) if _RANGE_HINT.search(query) else None
    # 只在句子里有明确的时间说法时才收窄。「最近」这类模糊词不收窄——
    # parse_range("最近") 会解析出一个月窗口，反而把「我都去过哪」砍成一个月。
    if rng:
        keep = []
        for r in rows:
            day = (r["occurred_at"] or r["created_at"] or "")[:10]
            if day and rng[0] <= day <= rng[1]:
                keep.append(r)
        rows = keep
    if pids:
        want = set(pids)
        ids = set()
        for pid in want:
            for e in conn.execute(
                    "SELECT source FROM edges WHERE target=? AND type='involves'", (pid,)):
                ids.add(e["source"])
        hits = [r for r in rows if r["id"] in ids]
        if hits:
            rows = hits
    return rows


def build(conn, query, limit=None, pool_ids=None):
    """聚合问句 → 一条 rollup 候选。不是聚合问句返回 None。"""
    if not getattr(config, "AGGREGATE_LAYER", True):
        return None
    mode = detect(query)
    if not mode:
        return None
    limit = int(limit or getattr(config, "AGGREGATE_MAX_ITEMS", 14))

    if mode == "relation_count":
        m = _REL_COUNT.search(query or "")
        roll = _relation_count(conn, query, m.group(1))
        if roll:
            return roll
        # relations 里没登记过 → 退回事件统计，别直接放弃
    if mode == "relation_list":
        m = _REL_LIST.search(query or "")
        kind_word = (m.group(1) or m.group(2)) if m else ""
        roll = _relation_list(conn, query, kind_word) if kind_word else None
        if roll:
            return roll

    pids = _persona_ids_in_query(conn, query)
    scope = "pool"
    tag_used = ""
    tag_rows = []
    rows = []
    concept_directional = False
    act = _ACT_Q.search(query)
    if not pids and act and not _RECENT_RE.search(query.replace("最近在", "在")):
        pass
    if not pids and _RECENT_RE.search(query) and not act:
        # 「我最近在做什么」：范围就是时间，不是主题。按发生时间倒序取最近
        # 一批（约两周），比"语义最像"更符合问句意图——问的是时间线。
        recent = conn.execute(
            "SELECT id,gist,occurred_at,created_at,place,subject FROM events "
            "WHERE archived=0 AND superseded_by IS NULL "
            "ORDER BY COALESCE(occurred_at,created_at) DESC LIMIT ?",
            (max(int(limit or 14) * 2, 24),)).fetchall()
        cutoff = (datetime.now().date()).toordinal() - 14
        kept = []
        for r in recent:
            day = (r["occurred_at"] or r["created_at"] or "")[:10]
            try:
                if datetime.strptime(day, "%Y-%m-%d").date().toordinal() >= cutoff:
                    kept.append(r)
            except ValueError:
                kept.append(r)
        if len(kept) >= 2:
            rows, scope = kept, "recent"
    if not rows and act:
        # 「我在学什么 / 在忙什么」：这里要的是「同一动作的所有实例」。
        # 靠向量池会混进大量无关记忆（实测 72 条里只有 5 条是"在学"），
        # 靠 tag 又常常没打上（「在学书法」不含「学习」这个词）。
        # 用动词字面兜底：范围小但准，且正是聚合该有的"全集"。
        verb = act.group(1) or act.group(2) or ""
        if verb:
            cand = conn.execute(
                "SELECT id,gist,occurred_at,created_at,place,subject FROM events "
                "WHERE archived=0 AND superseded_by IS NULL AND gist LIKE ? "
                "ORDER BY COALESCE(occurred_at,created_at) DESC",
                ("%" + verb + "%",)).fetchall()
            cand = _denoise_verb(cand, verb)
            if len(cand) >= 2:
                rows, scope = cand, "verb"
                tag_used = verb
    if not pids and not rows:
        # 主题词优先于相关性池：tag 是写入时打的确定性索引，范围精确；
        # 向量池只是"语义上像"，拿来计数会把工作/饮食记忆一起算进去。
        for t in _query_tags(query)[:1]:
            try:
                tag_rows = conn.execute(
                    "SELECT id,gist,occurred_at,created_at,place,subject FROM events "
                    "WHERE archived=0 AND superseded_by IS NULL AND id IN "
                    "(SELECT event_id FROM event_tags WHERE tag=?) "
                    "ORDER BY COALESCE(occurred_at,created_at) DESC", (t,)).fetchall()
            except Exception:
                tag_rows = []
            if len(tag_rows) >= 2:
                tag_used = t
                break
    if tag_rows and tag_used and not rows:
        rows, scope = list(tag_rows), "tag"
        rng = timeparse.parse_range(query) if _RANGE_HINT.search(query) else None
        if rng:
            rows = [r for r in rows
                    if rng[0] <= (r["occurred_at"] or r["created_at"] or "")[:10] <= rng[1]]
    if not rows and not pids:
        # 概念口径：「送过几次礼物」既没有主题标签也没有可用的动词，
        # 靠向量池会给出「都顾不上你，你想把他休了」这种噪声。这里按概念
        # 字面 + 方向取全集，口径写进 gist 让生成层知道这是下界。
        concept, spec = _concept_of(query)
        if spec:
            cand, directional = _concept_rows(conn, query, concept, spec)
            if len(cand) >= 1:
                rows, scope, tag_used = cand, "concept", spec.get("label") or concept
                concept_directional = directional
    if not rows:
        rows = _event_rows(conn, query, pids, pool_ids=pool_ids)
    if not rows:
        return None

    # 覆盖率闸门：落在相关性池里的比例太高，说明这次根本没界定出范围
    # （「我平时有哪些爱好」→ 72 条池子全"命中"）。这种统计没有任何信息量，
    # 还会占掉一条候选名额、把「共 72 条」这种噪声喂给生成层。
    # 没有 pool_ids 时（直接调 build）用全库条数当参照，否则闸门形同虚设。
    if scope == "pool":
        base = len(pool_ids) if pool_ids else conn.execute(
            "SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0]
        if base and len(rows) >= 0.8 * base:
            return None

    # 主题分布：用已有 tag 表，不额外推理
    tag_hit = {}
    try:
        ids = [r["id"] for r in rows]
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            q = ("SELECT event_id,tag FROM event_tags WHERE event_id IN (%s)"
                 % ",".join("?" * len(chunk)))
            for x in conn.execute(q, chunk):
                tag_hit.setdefault(x["tag"], set()).add(x["event_id"])
    except Exception:
        tag_hit = {}
    tags = sorted(tag_hit.items(), key=lambda kv: -len(kv[1]))[:5]

    # 人物分布
    pcount = {}
    try:
        name_map = {r["id"]: r["canonical_name"] for r in conn.execute(
            "SELECT id,canonical_name FROM personas")}
        ids = [r["id"] for r in rows]
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            q = ("SELECT target,COUNT(*) n FROM edges WHERE type='involves' "
                 "AND source IN (%s) GROUP BY target" % ",".join("?" * len(chunk)))
            for x in conn.execute(q, chunk):
                nm = name_map.get(x["target"])
                if nm and nm not in ("你", "用户"):
                    pcount[nm] = pcount.get(nm, 0) + int(x["n"])
    except Exception:
        pcount = {}

    days = [(r["occurred_at"] or r["created_at"] or "")[:10] for r in rows]
    days = [d for d in days if d]
    span = ("%s ~ %s" % (min(days), max(days))) if days else "时间未知"

    head = {"tag": "按主题「%s」统计，共 %d 条（%s）" % (tag_used, len(rows), span),
            "verb": "和「%s」相关的一共 %d 条（%s）" % (tag_used, len(rows), span),
            "recent": "近期（近两周）共 %d 条（%s）" % (len(rows), span),
            "pool": "在 %d 条最相关记忆里，共 %d 条命中（%s）" % (
                len(pool_ids or rows), len(rows), span),
            }.get(scope, "共 %d 条（%s）" % (len(rows), span))
    if scope == "concept":
        # 概念口径是**下界**，不能当成精确次数报出去：同义改写（"给外婆寄了件
        # 羽绒服"未必出现"礼物"）会漏，所以措辞是"至少 N 条 / 口径：…"。
        head = ("按「%s」口径，至少 %d 条（%s；口径：%s）" % (
            tag_used, len(rows), span,
            "含送出类动词，已排除「给你/给我」的收到句" if concept_directional
            else "字面出现「%s」" % tag_used))
    if tags:
        head += "；主题：" + "、".join("%s %d" % (t, len(s)) for t, s in tags)
    if pcount:
        top = sorted(pcount.items(), key=lambda kv: -kv[1])[:5]
        head += "；涉及人物：" + "、".join("%s %d" % (n, c) for n, c in top)

    detail = []
    for r in rows[:limit]:
        g = _PUNCT.sub(" ", (r["gist"] or "")).strip()
        if not g:
            continue
        d = (r["occurred_at"] or r["created_at"] or "")[:10]
        detail.append("%s %s" % (d, g[:60]))
    body = "；".join(detail)
    gist = head + ("。明细：" + body if body else "。")
    if len(rows) > limit:
        gist += "（另有 %d 条未列出）" % (len(rows) - limit)

    return {
        "kind": "rollup", "type": "rollup", "id": "rollup_agg",
        "count": len(rows),
        "gist": gist,
        "items": [{"id": r["id"], "gist": r["gist"],
                   "date": (r["occurred_at"] or r["created_at"] or "")[:10]}
                  for r in rows[:limit]],
        "fragments": [], "affect": {}, "salience": 0.9,
        "score": 0.0, "vec": 0.0, "kw": 0.0,
        "subject": config.DEFAULT_SUBJECT,
        "aggregate_mode": mode,
    }
