"""召回：向量 + 关键词 + 实体 + 时间 + 图扩散 + 概念 + 融合 + 可信度。"""
import json
import re
from datetime import datetime, timedelta

from . import alias, config, db, embed, entity, timeparse
from .graph import build_graph, spread


def _row_subject(r):
    """v12 前旧库没有 subject 列，取不到时回落到默认主体。"""
    try:
        return r["subject"] or config.DEFAULT_SUBJECT
    except Exception:
        return config.DEFAULT_SUBJECT


def _cos(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_STOP_WORDS = {
    config.COUNTERPART_NAME, "什么", "怎么", "为什么", "哪里", "多少", "请问", "告诉", "一下",
    "喜欢", "可以", "是否", "有没有", "如何",
}
_STOP_CHARS = set("的了吗呢吧啊什怎么哪多少为何谁几这那就也都狠太要会能可以对把被给让从到向于")

# 2-gram 会把「哪儿工作」切成「儿工」这类虚字开头的噪声词：它不会命中任何
# 记忆，却会稀释关键词得分、并在 focus 惩罚里对全部候选乘 0.55。
_NOISE_HEADS = set("儿么啥呗啦")
# 指代说话双方与人称的词。实体抽取会把它们登记进 personas，但「我叫什么」
# 里的「我」并不是在「点名某个人物」，不该触发人物硬过滤。
_PRONOUN_NAMES = {
    "我", "你", "他", "她", "它", "咱", "俺", "我们", "你们", "他们", "她们",
    "自己", "对方", "用户", "用户本人", "助手", "助理", "user", "assistant",
}
_PRONOUN_NAMES |= {getattr(config, "SELF_NAME", "我"),
                   getattr(config, "COUNTERPART_NAME", "用户")}
_PRONOUN_NAMES.discard("")


# 同义扩展（A2）：同一件事的不同说法要能对上，否则"结婚"搜不到"婚礼"
_SYN_GROUPS = (
    {"结婚", "婚礼", "婚宴", "喜酒", "改期", "伴郎", "新娘"},
    {"猫", "猫咪", "宠物", "养猫"},
    {"狗", "狗狗", "宠物", "养狗"},
    {"吃", "做饭", "美食", "蛋糕", "菜", "食谱", "厨房"},
    {"旅行", "旅游", "出去玩", "出游", "景点", "西湖", "栈道"},
    {"怕", "害怕", "恐惧", "恐高", "胆小", "腿软"},
    {"工作", "职业", "上班", "公司", "上班族", "开店", "咖啡馆", "创业",
     "做生意", "个体户", "自由职业", "打工"},
    {"考研", "备考", "考试", "复习"},
)


# 查"焦点词"：去掉人名、疑问词、泛时间词后剩下的实词（A3/B4 的关键闸门）
# 概括性问句没有单一事件能回答，应由语义记忆（概念 / 图式）作答
_GENERIC_Q = re.compile(
    r"怎样的人|什么样的人|是什么样|平时(?:喜欢|习惯|都|会)|性格|为人|"
    r"整体印象|总结一下|总体(?:来说|上)|大概是个|是什么性格")

_STOP_FOCUS = {"现在", "最近", "什么", "怎么", "怎样", "怎么样", "如何", "哪些",
               "是否", "有没有", "一下", "告诉", "知道", "记得", "可以", "多少",
               "这个", "那个", "时候", "一起", "觉得"}


def _focus_terms(kws, names):
    return {k for k in kws
            if k not in _STOP_FOCUS and not any(k in n or n in k for n in names)}


def _expand(kws):
    out = set(kws)
    for g in _SYN_GROUPS:
        if kws & g:
            out |= g
    return out


def _keywords(query):
    """中文按 2-gram 切（过滤停用词），英文/数字整词。

    RECALL_COLDSTART_FIX 开启时额外丢掉虚字开头的切分碎片（「儿工」），
    它们不携带语义，只会稀释关键词得分并让 focus 惩罚误伤全部候选。
    """
    fix = getattr(config, "RECALL_COLDSTART_FIX", True)
    segs = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", query or "")
    kws = set()
    for s in segs:
        if re.match(r"[\u4e00-\u9fff]", s):
            if len(s) == 2:
                if s not in _STOP_WORDS and not (fix and s[0] in _NOISE_HEADS):
                    kws.add(s)
            elif len(s) >= 3:
                for i in range(len(s) - 1):
                    g = s[i:i + 2]
                    if g in _STOP_WORDS:
                        continue
                    if any(ch in _STOP_CHARS for ch in g):
                        continue
                    if fix and g[0] in _NOISE_HEADS:
                        continue
                    kws.add(g)
        elif len(s) >= 2:
            kws.add(s.lower())
    return kws


def _kw_score(kws, text):
    if not kws or not text:
        return 0.0
    t = text.lower()
    hit = sum(1 for k in kws if k in t)
    return hit / len(kws)


def _bulk_cos(qv, rows):
    """一次算完所有事件的余弦相似度。

    直接用 ``np.frombuffer`` 解析 SQLite BLOB（float32 小端），避免逐条 Python list：
    5 万条时 Python 解码会产生 5000 万个 float 对象（>1GB、十几秒），现在是 200MB 一次拷贝。
    """
    if not qv:
        return {r["id"]: 0.0 for r in rows}
    try:
        import numpy as np
    except Exception:
        return {r["id"]: _cos(qv, db.unpack(r["embedding"])) for r in rows}

    dim = len(qv)
    need = dim * 4
    ids, blobs, zeros = [], [], []
    for r in rows:
        b = r["embedding"]
        if not b or len(b) != need:
            zeros.append(r["id"])
            continue
        ids.append(r["id"])
        blobs.append(b)

    out = {i: 0.0 for i in zeros}
    if not ids:
        return out
    mat = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(ids), dim)
    q = np.asarray(qv, dtype=np.float32)
    denom = np.linalg.norm(mat, axis=1) * (np.linalg.norm(q) or 1.0)
    sims = mat.dot(q) / np.maximum(denom, 1e-9)
    out.update(zip(ids, sims.tolist()))
    return out


def _query_personas(conn, query):
    """问句里点名了哪些登记人物。

    P0（清库后 18 轮实测）：实体抽取会把「我」「你」「用户」登记成 personas，
    长度 1 的代词在几乎每个问句里都命中 → pids 非空 → 末尾人物硬过滤把
    「还没来得及建 involves 边」的新记忆全部丢弃。修复后：
    - 代词 / 单字称呼不算点名人物；
    - 其余单字名字要求以独立词出现，不再做无差别子串匹配。
    ``RECALL_COLDSTART_FIX=0`` 时完整保留修复前行为（A/B 对照用）。
    """
    fix = getattr(config, "RECALL_COLDSTART_FIX", True)
    # 非人实体：构建期把品种 / 节日 / 职业 / 群体也登记成了人物卡，它们一旦
    # 在查询里命中就会触发人物硬过滤，把整轮候选换成字面沾边的那几条。
    # 实测「谁养了只金毛叫布丁」→ pids=['金毛'] → 只剩「金毛撞你腿了」1 条，
    # 正解 0.643 被整条删掉；「我男朋友是谁」→ 被「男**朋友**」的子串劫持。
    # v19：判定从「硬编码黑名单」改为读 personas.kind（entity.kind_of 的产物），
    # 漏网的新名词（模型下轮造出来的"柯基""团建"）不用再逐个人工补词。
    generic = set(getattr(config, "GENERIC_PERSON_NAMES", ()) or ())
    ids = []
    for r in conn.execute("SELECT id,canonical_name,aliases_json,kind,bond FROM personas"):
        try:
            kind = r["kind"] or entity.kind_of(r["canonical_name"])
        except (IndexError, KeyError):
            kind = entity.kind_of(r["canonical_name"])
        if fix and kind != "person":
            continue
        # v20：只被"提及"的人（刘慈欣/周杰伦）不构成点名人物。问「我喜欢看什么
        # 电影」时「王家卫」会命中，曾经把整轮候选换成字面沾边的那几条——
        # 与「金毛」劫持是同一类故障，只是这次是人。他们的记忆仍可被检索到。
        try:
            if fix and (r["bond"] or "social") == "mentioned":
                continue
        except (IndexError, KeyError):
            pass
        names = [r["canonical_name"]] + json.loads(r["aliases_json"] or "[]")
        for n in names:
            if not n:
                continue
            n = str(n).strip()
            if fix and n in generic:
                continue
            if fix and n.lower() in _PRONOUN_NAMES:
                continue
            if fix and len(n) == 1:
                # 单字名做子串匹配等于必然命中，要求它被标点/空白隔开
                if not re.search(r"(?:^|[^\u4e00-\u9fff])%s(?:$|[^\u4e00-\u9fff])"
                                 % re.escape(n), query or ""):
                    continue
            if n in query:
                ids.append(r["id"])
                break
    return ids


def _relation_expand(conn, pids, min_strength=0.3):
    """查询人物 → 通过仍有效的关系扩展到相关人物 {pid: strength}。

    时间边界：until 早于今天的关系视为已结束，不再扩展。
    """
    if not pids:
        return {}
    day = datetime.now().date().isoformat()
    me = set(pids)
    out = {}
    for r in conn.execute("SELECT a,b,strength,until FROM relations"):
        until = (r["until"] or "")[:10]
        if until and until < day:
            continue
        st = float(r["strength"] or 0.0)
        if st < min_strength:
            continue
        if r["a"] in me and r["b"] not in me:
            out[r["b"]] = max(out.get(r["b"], 0.0), st)
        if r["b"] in me and r["a"] not in me:
            out[r["a"]] = max(out.get(r["a"], 0.0), st)
    return out


def _concept_related(conn, cid, ent_ids):
    for r in conn.execute(
        "SELECT event_id FROM concept_links WHERE concept_id=? AND type='derived_from'",
        (cid,),
    ):
        if r["event_id"] in ent_ids:
            return True
    return False


def _event_text(conn, eid):
    parts = [r["text"] for r in conn.execute(
        "SELECT f.text FROM bindings b JOIN fragments f ON b.fragment_id=f.id WHERE b.event_id=?",
        (eid,),
    )]
    return " ".join(parts)


def recall(conn, query, top_k=None, hops=None, info=None, tag=None):
    top_k = top_k or config.RECALL_TOP_K
    hops = config.SPREAD_HOPS if hops is None else hops

    # 无有效字符（纯符号/emoji/空串）：召不中任何东西，别拿概念当填充物
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", query or ""):
        return []

    # 嵌入服务不可用时降级：向量通道归零，关键词/实体/时间/图扩散仍可工作
    try:
        qv = embed.embed(query)
    except Exception as e:
        qv = None
        if info is not None:
            info["embed_error"] = str(e)[:200]
    kws = _expand(_keywords(query))
    # 焦点词：人名/疑问词之外还剩什么，用来挡住"只因为提到阿哲"就召回的无关记忆
    try:
        all_names = [r["canonical_name"] for r in conn.execute(
            "SELECT canonical_name FROM personas WHERE canonical_name<>''")]
    except Exception:
        all_names = []
    focus = _focus_terms(kws, all_names)
    rng = timeparse.parse_range(query)  # (start, end) 或 None
    out = []
    con_items = []          # 命中的概念（是否进依据池由 SCHEMA_AS_EVIDENCE 决定）
    schema_boost = {}       # 图式偏置：{event_id: 加分}，必须早于融合排序
    schema_hit = set()

    pids = _query_personas(conn, query)
    ent_ids = set()
    for pid in pids:
        for e in conn.execute(
            "SELECT source FROM edges WHERE target=? AND type='involves'", (pid,)
        ):
            ent_ids.add(e["source"])

    # 实体链接：查询里的类别词（"猫"）关联到登记过的名字（"团子"），
    # 把该实体的记忆拉进候选并补它的字面词；查询原文不改写。
    try:
        for name in alias.targets(conn, query):
            kws |= _keywords(name) | {name}
            focus.add(name)
            row = conn.execute("SELECT id FROM personas WHERE canonical_name=?",
                               (name,)).fetchone()
            if row:
                for e in conn.execute(
                        "SELECT source FROM edges WHERE target=? AND type='involves'",
                        (row["id"],)):
                    ent_ids.add(e["source"])
    except Exception:
        pass

    # P0-7：顺着有效关系扩到相关人物（朋友/家人），强度决定加成
    rel_ent = {}
    for pid, st in _relation_expand(conn, pids).items():
        for e in conn.execute(
            "SELECT source FROM edges WHERE target=? AND type='involves'", (pid,)
        ):
            rel_ent[e["source"]] = max(rel_ent.get(e["source"], 0.0), st)

    # 工作记忆：最近写入/召回过的记忆（短期注意），给小幅加成
    try:
        from . import working_memory as _wm
        wm_ids = set(_wm.recent_ids(conn, hours=config.WM_HOURS, limit=config.WM_CAPACITY))
    except Exception:
        wm_ids = set()

    # 记忆冲突：默认只看最新说法；问「以前/原来」时才把旧说法带出来
    include_old = bool(re.search(
        r"以前|之前|原来|曾经|过去|几次|改过|改期|变更|第几次|最早|最早的说法", query or ""))
    sql = "SELECT * FROM events WHERE archived=0"
    if not include_old and not config.COEXIST_DEFAULT:
        sql += " AND superseded_by IS NULL"
    params = []
    if tag:                            # 按主题标签过滤（旅游/工作/家庭…）
        sql += " AND id IN (SELECT event_id FROM event_tags WHERE tag=?)"
        params.append(tag)
    rows = conn.execute(sql, params).fetchall()
    if rows:
        # 焦点词清洗：只保留「确实在至少一条记忆里出现过」的焦点词。
        # 2-gram 碎片（如「儿工」）或同义扩展出来的词若全库无命中，说明
        # 它们只是分词噪声，不该让「没有命中焦点词」的惩罚乘到每条候选上。
        focus_effective = focus
        if getattr(config, "RECALL_COLDSTART_FIX", True) and focus:
            ev_texts = [((r["gist"] or "") + " " + (r["place"] or ""))
                        for r in rows]
            focus_effective = {f for f in focus
                               if any(f in t for t in ev_texts)}
        vec_scores = _bulk_cos(qv, rows)
        # 关键词只看 gist/地点：原来逐条查 fragments 是 N+1，2000 条时占了一半耗时
        # search_text 记录再巩固时新增的说法，让关键词通道也能命中改写过的问法
        kw_scores = {r["id"]: _kw_score(
            kws, " ".join(x for x in (
                r["gist"], r["place"],
                r["search_text"] if "search_text" in r.keys() else "")
                if x))
            for r in rows}

        vmax = max(vec_scores.values()) if vec_scores else 0.0
        verified_thr = max(0.55, vmax * 0.90)
        inferred_thr = max(0.40, vmax * 0.65)

        seed = {}
        for eid, s in sorted(vec_scores.items(), key=lambda kv: kv[1], reverse=True)[
            : config.RECALL_SEED
        ]:
            seed[eid] = max(seed.get(eid, 0.0), s)
        for eid in ent_ids:
            seed[eid] = max(seed.get(eid, 0.0), 0.6)
        # 关系扩展来的记忆也作为扩散种子（强度打折）
        for eid, st in rel_ent.items():
            seed[eid] = max(seed.get(eid, 0.0), 0.5 * st)
        g = build_graph(conn)
        spread_scores = spread(g, seed, hops=hops, decay=config.SPREAD_DECAY)

        # 目标驱动注意力：与进行中目标重合的记忆小幅加分
        goal_boost = {}
        try:
            from . import goals as _goals
            goal_boost = _goals.match_bonus(conn, query)
        except Exception:
            goal_boost = {}

        # 情绪一致性：当前心境对同号情绪记忆小幅加权（只影响排序，不改事实）
        emo_bias = 0.0
        if config.EMOTION_BIAS:
            try:
                er = conn.execute(
                    "SELECT valence FROM emotion_state WHERE id=1").fetchone()
                if er and abs(float(er["valence"] or 0)) >= config.EMOTION_MIN_INTENSITY:
                    emo_bias = float(er["valence"])
            except Exception:
                emo_bias = 0.0

        # --- 概念 / 图式预计算 ---
        # 关键：必须在融合排序**之前**算出偏置。旧版在 out 已按分数截断后
        # 才加分，簇内事件根本进不了 top_k，实测「带偏置 0 条」。
        # 概念只给自己簇内的事件加权，自己不占依据名额。
        if config.SCHEMA_BIAS or config.SCHEMA_AS_EVIDENCE:
            floor = (config.SCHEMA_MIN_SCORE if config.SCHEMA_BIAS
                     else config.CONCEPT_MIN_SCORE)
            cool_cut = (datetime.now().astimezone()
                        - timedelta(days=7)).isoformat(timespec="seconds")
            if not (rng or tag):
                try:
                    concepts = conn.execute(
                        "SELECT id,text,embedding,abstraction_level,created_at "
                        "FROM concepts WHERE embedding IS NOT NULL").fetchall()
                except Exception:
                    concepts = []
                for cr in concepts:
                    # 只做簇内加权时不再额外做人物过滤：最终候选本来就有实体
                    # 过滤，重复过滤会把绝大多数概念挡掉。冷静期同理，只在
                    # 概念真要进依据池时才生效。
                    if (pids and config.SCHEMA_AS_EVIDENCE
                            and not _concept_related(conn, cr["id"], ent_ids)):
                        continue
                    cs = config.CONCEPT_SCORE_SCALE * _cos(qv, db.unpack(cr["embedding"]))
                    if config.SCHEMA_AS_EVIDENCE and (cr["created_at"] or "") >= cool_cut:
                        cs *= 0.85
                    if cs < floor:
                        continue
                    con_items.append({
                        "id": cr["id"], "type": "concept", "gist": cr["text"],
                        "kind": "concept", "affect": {}, "salience": 0.8,
                        "fragments": [], "score": round(cs, 4), "vec": round(cs, 4),
                        "level": cr["abstraction_level"],
                    })
                con_items.sort(key=lambda x: x["score"], reverse=True)
                if config.SCHEMA_BIAS:
                    for cc in con_items[:3]:
                        for link in conn.execute(
                                "SELECT event_id FROM concept_links WHERE concept_id=? "
                                "AND type='derived_from'", (cc["id"],)):
                            e = link["event_id"]
                            schema_boost[e] = max(
                                schema_boost.get(e, 0.0),
                                cc["score"] * config.SCHEMA_BIAS_GAIN)

        merged = {}
        window_hit = set()      # 落在查询时间窗内的记忆：过阈值时豁免，见下方 keep_thr
        for r in rows:
            eid = r["id"]
            s = (0.40 * vec_scores.get(eid, 0.0)
                 + 0.30 * kw_scores.get(eid, 0.0)
                 + 0.20 * spread_scores.get(eid, 0.0))
            # 提取容易度：激活值低 = 想起来费劲，只调制语义基础分，不动规则层加成
            act = float(r["activation"] if r["activation"] is not None else 1.0)
            s *= (1.0 - config.ACTIVATION_WEIGHT) + config.ACTIVATION_WEIGHT * act
            if eid in goal_boost:
                s += goal_boost[eid]
            if eid in ent_ids:
                s += 0.05
            if eid in rel_ent:
                s += 0.18 * rel_ent[eid]  # 关系线索是次优证据：能带出记忆，但不该盖过直接命中
            if eid in wm_ids:
                s += config.WM_BONUS
            if rng:
                occ = (r["occurred_at"] or "")[:10]
                created = (r["created_at"] or "")[:10]
                in_window = ((occ and rng[0] <= occ <= rng[1])
                             or (not occ and rng[0] <= created <= rng[1]))
                if in_window:
                    # 时间窗内的记忆只做轻微提升，**绝不重映射分数**。
                    # 保底的真正目的是「别被 keep_thr 误杀」，所以豁免放到过滤阶段
                    # （见下面的 window_hit），让排序严格由语义决定。
                    # 旧写法 `s = max(s, 0.42) + 0.12` 会把窗内全部记忆压成同一个
                    # 分：实测「我最近在做什么」12 条候选里 8 条精确等于 0.5922
                    # （base 0.1489~0.5341 被抹平），排序退化成数据库顺序。
                    s = max(s * config.WINDOW_BOOST, config.WINDOW_FLOOR)
                    window_hit.add(eid)
                elif occ:
                    s *= 0.15  # 明确不在该时间段：基本排除
                else:
                    s *= 0.35  # 完全无日期线索：降权，但不至于查不到
            # 焦点词不命中 → 只是"提到了同一个人"；但点名人物时该人物的记忆不该重罚
            if focus_effective and not rng and not any(
                    f in ((r["gist"] or "") + " " + (r["place"] or ""))
                    for f in focus_effective):
                # 同义表述会造成焦点词假失配：「哪家公司上班」的焦点词是「公司」，
                # 而正解写作「在云栖科技做后端工程师」。旧值 0.55 把正解打到 0.195
                # 直接出局；0.72 保留「没提到就别排前面」的意图，但不至于判死。
                s *= 0.80 if (eid in ent_ids or eid in rel_ent) else config.FOCUS_MISS_PENALTY
            if emo_bias:
                try:
                    av = float(json.loads(r["affect_json"] or "{}").get("valence") or 0)
                except (ValueError, TypeError, AttributeError):
                    av = 0.0
                if av:
                    gain = (config.EMOTION_MATCH_GAIN if (av > 0) == (emo_bias > 0)
                            else -config.EMOTION_MISMATCH_GAIN)
                    s = max(0.0, s + gain * min(1.0, abs(emo_bias)) * min(1.0, abs(av)))
            if schema_boost.get(eid):
                s += schema_boost[eid]
                schema_hit.add(eid)
            merged[eid] = s
            # 评分轨迹：只在调用方显式传 info 时记录，用来解释「为什么这条排第一」。
            # 曾经出现过 4 条不同向量的候选取到完全相同的分数，没有这个根本查不出来。
            if info is not None:
                info.setdefault("score_trace", {})[eid] = {
                    "vec": round(vec_scores.get(eid, 0.0), 4),
                    "kw": round(kw_scores.get(eid, 0.0), 4),
                    "spread": round(spread_scores.get(eid, 0.0), 4),
                    "act": round(act, 4),
                    "ent": bool(eid in ent_ids),
                    "rel": round(rel_ent.get(eid, 0.0), 4),
                    "wm": bool(eid in wm_ids),
                    "goal": round(goal_boost.get(eid, 0.0), 4),
                    "rng": str(rng),
                    "emo": round(emo_bias, 4),
                    "schema": round(schema_boost.get(eid, 0.0), 4),
                    "base": round(0.40 * vec_scores.get(eid, 0.0)
                                  + 0.30 * kw_scores.get(eid, 0.0)
                                  + 0.20 * spread_scores.get(eid, 0.0), 4),
                    "score": round(s, 4),
                }

        # F3（2026-11-15 长测后）：最近几轮刚用过的记忆施加疲劳度降权，
        # 打断「采纳 → 更易召回 → 再采纳」的正反馈，避免回复塌缩成固定句式。
        # getattr 兜底：常驻 HTTP 服务可能仍持有旧的 config 模块。
        try:
            from . import recent as _recent
            recent_used = _recent.used_ids(
                conn, getattr(config, "RECENT_USED_TURNS", 6))
            if recent_used:
                penalty = getattr(config, "RECENT_USED_PENALTY", 0.25)
                for eid in merged:
                    if eid in recent_used:
                        merged[eid] *= (1.0 - penalty)
        except Exception:
            pass

        # 多取候选再在末尾截到 top_k：图式 / 情绪 / 目标等偏置本来就是为了
        # 把中排名的相关记忆拉上来，若这里直接截断，偏置只能在已入选条目里
        # 打转（实测泛化问句「带图式偏置 0 条」）。最终返回仍是 deduped[:top_k]。
        ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[
            : max(top_k * 3, 24)]
        # 人物硬过滤的前提是「确实存在与该人物相连的事件」。pids 命中但
        # ent_ids/rel_ent 都为空时（清库冷启动、新记忆尚未建边），这条过滤
        # 没有任何证据，只会把全部候选清空 —— 此时退化为不按人物过滤，
        # 交给向量 / 关键词 / 图扩散的融合分排序。
        if getattr(config, "RECALL_COLDSTART_FIX", True):
            person_filter = bool(pids and (ent_ids or rel_ent))
        else:
            person_filter = bool(pids)
        if ranked:
            # 相对阈值用「第 N 名」当基准，并且设上限。用 top1 当基准会被单条
            # 词面巧合命中的高分毒化：实测「我男朋友是谁」top1 是「和朋友约羽毛球」
            # （词面「朋友」），阈值被抬到 0.30 以上，正解「周野是你男友」0.348
            # 就差一点被砍；「谁养了金毛布丁」更惨，正解 0.643 直接归零。
            ref = ranked[min(config.RECALL_KEEP_RANK - 1, len(ranked) - 1)][1]
            keep_thr = min(max(config.RECALL_KEEP_MIN, ref * 0.65),
                           config.RECALL_KEEP_MAX)
            row_map = {r["id"]: r for r in rows}
            for eid, score in ranked:
                if score <= 0:
                    continue
                # 窗内候选豁免阈值：这是「昨天做了什么」能被召回的保底，
                # 与分数无关，所以不会像旧版那样把窗内候选的语义差异压平。
                if score < keep_thr and eid not in window_hit:
                    continue
                # 人物硬过滤保留：问「小明在做什么」时混进小红的记忆是硬错。
                # 识别错的问题在 _query_personas 里解决（排除非人实体），
                # 而不是在这里放宽——放宽会把「精确率」一起赔进去。
                if person_filter and eid not in ent_ids and eid not in rel_ent:
                    continue
                r = row_map[eid]
                v = vec_scores.get(eid, 0.0)
                if v >= verified_thr:
                    kind = "verified"
                elif v >= inferred_thr or eid in ent_ids:
                    kind = "inferred"
                else:
                    kind = "associative"
                out.append({
                    "id": eid,
                    "type": "event",
                    "gist": r["gist"],
                    # v13：主体要跟着候选一路走到生成层，否则 MCP 的
                    # 「关于我/对方」标注只能永远取默认值。
                    "subject": _row_subject(r),
                    "place": r["place"],
                    "time_text": r["time_text"],
                    "occurred_at": r["occurred_at"],
                    "created_at": r["created_at"],
                    "affect": json.loads(r["affect_json"] or "{}"),
                    "salience": r["salience"],
                    "fragments": [fr["text"] for fr in conn.execute(
                        "SELECT f.text FROM bindings b JOIN fragments f ON b.fragment_id=f.id "
                        "WHERE b.event_id=? ORDER BY b.seq", (eid,))],
                    "score": round(score, 4),
                    "vec": round(v, 4),
                    "kw": round(kw_scores.get(eid, 0.0), 4),
                    "kind": kind,
                    "working": bool(eid in wm_ids),
                    "schema": bool(eid in schema_hit),
                    "fuzzy": bool(float(r["activation"]
                                        if r["activation"] is not None else 1.0)
                                  < config.FUZZY_ACTIVATION),
                })
    # 纯检索不再写库：强化移到「采纳」之后（adoption.auto_record / Memory.feedback），
    # 避免负例反复检索出的噪声依据被「曝光即强化」推到 activation=1.0 并占据 drift 锚点。

    # 分流：具体问句只走图式偏置（概念不占依据名额）；概括性问句（「用户是个
    # 怎样的人」）没有单一事件能回答，最多补 2 条概念作为「理解」。
    # SCHEMA_AS_EVIDENCE=True 是恢复旧行为的全局对照开关。
    generic = bool(config.SCHEMA_GENERIC_AS_EVIDENCE
                   and _GENERIC_Q.search(query or ""))
    if config.SCHEMA_AS_EVIDENCE or generic:
        cut = config.SCHEMA_MIN_SCORE if generic else config.CONCEPT_MIN_SCORE
        con_items = [c for c in con_items if c["score"] >= cut][:2]
        # 概念分普遍低于具体事件，若只是追加，末尾 deduped[:top_k] 会把它们
        # 全部截掉（实测泛化问句「概念 0 条」）。让它占掉最弱的几个依据名额。
        all_items = out[:max(0, top_k - len(con_items))] + con_items
    else:
        all_items = out
    all_items.sort(key=lambda x: x["score"], reverse=True)
    seen, deduped = set(), []
    for it in all_items:
        if it["gist"] in seen:
            continue
        seen.add(it["gist"])
        deduped.append(it)

    # v19 聚合层： 「我一共养过几只宠物」这类问句的答案不在任何单条记忆里，
    # 而在集合性质里。top_k 截断必然给出残缺清单，所以这里追加一条**确定性
    # 统计**候选（全量扫描，不调 LLM），并放到最前面——它比任何单条都更该被看到。
    if getattr(config, "AGGREGATE_LAYER", True):
        try:
            from . import aggregate
            # 池子给聚合层做「相关性收窄」：不传的话它会统计全库，
            # 变成「我一共养过几只宠物 → 主题：工作 23、饮食 21」。
            pool = [e for e, _ in sorted(
                merged.items(), key=lambda kv: kv[1], reverse=True)[:max(top_k * 6, 60)]]
            roll = aggregate.build(conn, query, limit=top_k, pool_ids=pool)
        except Exception as e:
            roll = None
            if info is not None:
                info["aggregate_error"] = str(e)[:200]
        if roll:
            roll["score"] = round(max([d["score"] for d in deduped] or [0.0]) + 0.05, 4)
            deduped = [roll] + deduped
    return deduped[:top_k]