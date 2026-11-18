"""巩固引擎：事件→概念，以及概念→更高层概念（再抽象 = 成长）。"""
import difflib
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import config, db, embed, llm


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


_VAGUE = ("人生", "生活", "成长", "历程", "起伏", "挑战", "自我发展", "关怀", "助力", "无常")


def _similar(a, b):
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


def _process_artifact(t):
    """检测模型把推理过程当结果吐出的结构性污染。

    v23：云端思考模型曾把「Draft 1/2/3 + 中英对照 + 字符自评」整段草稿链
    当概念输出（实测 367/467 字入库）。这些是结构特征，原有语义过滤全漏。
    """
    if re.search(r"\bDraft(ing)?\b", t, re.I):
        return True
    if re.search(r"\b\d+\s*(chars?|characters)\b", t, re.I):
        return True  # "15 chars. Good." 之类自评
    if re.search(r"[（(][A-Za-z][^）)]{12,}[）)]", t):
        return True  # 整句英文括注：中英对照草稿
    if "\n" in t or "**" in t or re.search(r"(^|\s)[\*\-]\s+\S", t):
        return True  # 换行/加粗/列表符：概念要求是一句话
    if re.match(r"^\s*\d+[\.、\)]\s", t):
        return True  # 编号列表开头
    return False


def _clean_concept(text):
    """从被草稿链污染的输出里提取最后一句像样的中文短句。

    提不出来就原样返回，交给 _valid_concept 拒收——清洗不负责凭空造概念。
    最后一句通常是模型草稿里的最终定稿，优先取它。
    """
    t = (text or "").strip()
    if len(t) <= 40 and not _process_artifact(t):
        return t
    clean = re.sub(r"[（(][^）)]*[）)]", "", t)
    clean = re.sub(r"\b\d+\s*(chars?|characters)\b[^\n]*", "", clean, flags=re.I)
    clean = clean.replace("**", "").replace("\n", " ")
    parts = re.split(r"\bDraft\s*\d+\s*[:：]|\d+\.\s+|[。！？!?]", clean, flags=re.I)
    candidates = []
    for seg in parts:
        seg = re.sub(r"^[\s\*\-\u2022]+", "", seg).strip(" \t.,;:：，；。")
        seg = re.sub(r"^\s*\d+[\.、\)]\s*", "", seg)
        if 4 <= len(seg) <= 40 and len(re.findall(r"[\u4e00-\u9fff]", seg)) >= 4:
            candidates.append(seg)
    return candidates[-1] if candidates else t


def _valid_concept(text):
    """过滤退化输出、笼统空话，以及草稿链等结构性污染。"""
    t = (text or "").strip()
    if len(t) < 4:
        return False
    if len(t) > 40:
        return False  # 概念要求 20/25 字以内；硬上限留余量，但不放过整段草稿
    han = len(re.findall(r"[\u4e00-\u9fff]", t))
    if han < 4:
        return False
    if _process_artifact(t):
        return False
    if re.fullmatch(r"[\d\.\-\s]+", t):
        return False
    if len(set(t)) <= 2:
        return False
    if re.search(r"(.)\1{5,}", t):
        return False  # 退化输出：长串重复字符（如 000000）
    hits = sum(1 for w in _VAGUE if w in t)
    if hits >= 2:
        return False
    if hits == 1 and len(t) > 30:
        return False
    if re.match(r"^(人类|人们|社会|时代|个体|每个人|所有人)", t):
        return False  # 宏大主语：不是从具体经历里长出来的
    return True


def _event_personas(conn, eid):
    """事件涉及的「具体人物」；对话者本人不算聚类约束，否则所有事件都成同类。"""
    return {
        r["target"]
        for r in conn.execute(
            "SELECT e.target FROM edges e JOIN personas p ON e.target=p.id "
            "WHERE e.source=? AND e.type='involves' AND p.canonical_name<>'用户'",
            (eid,),
        )
    }


_EVENT_PROMPT = (
    "本组经历的主语：{who}\n"
    "下面是若干条具体经历。请归纳出一句**具体、可核对**的认识（概念）。\n"
    "要求：\n"
    "1. 主语必须与经历中一致（「用户」「阿哲」「妈妈」等），不得替换成别人；\n"
    "2. 禁止「人生/生活/成长/历程/起伏/挑战/自我发展/关怀」这类空话；\n"
    "3. 必须保留经历里的具体名字与关键细节（人名、宠物名、物品名、时间/程度），不得用「它/这件事/某些」泛指；\n"
    "4. 只能使用经历里出现过的人物与细节，不得新增地点、身体部位、心理描写；\n"
    "5. 一句话，20 字以内，第三人称，不用「我」；\n"
    "6. 只输出这句话，不要解释。\n经历：\n{items}"
)

_RETRY_SUFFIX = "\n\n上一句太笼统，请写得更具体（点明是谁、遇到什么事、有什么反应）。"

_CONCEPT_PROMPT = (
    "下面是若干条相关的认识/概念。请提炼一条更概括、但仍然**具体**的认识。\n"
    "要求：\n"
    "1. 主语必须是具体人物（如「用户」「阿哲」「妈妈」）或具体场景，不要用「人类/个体/社会」；\n"
    "2. 禁止「生命/历程/矛盾/交织/压力」这类宏大词；\n"
    "3. 保留专名与关键细节（猫名/书名/地名/数字），不要为了概括把名字抽象掉；\n"
    "4. 一句话，25 字以内，第三人称；\n"
    "5. 只输出这句话。\n概念：\n{items}"
)


def _persona_name(conn, pids):
    """簇的主语：有具体人物就用它，否则就是对话者本人。"""
    # v13：对话者名从主体词典取，不再写死「用户」。
    fallback = config.COUNTERPART_NAME
    if not pids:
        return fallback
    pid = next(iter(pids))
    row = conn.execute("SELECT canonical_name FROM personas WHERE id=?", (pid,)).fetchone()
    return row["canonical_name"] if row else fallback


def _similar_concept(conn, text, threshold=0.88):
    """语义近似判定：用向量比（字面法判不了「借出的钱被拖」vs「借钱被拖」）。

    embedding 不可用时退回字面相似度，保证不至于因嵌入故障而重复建概念。
    """
    try:
        v = embed.embed(text)
    except Exception:
        v = None
    for r in conn.execute("SELECT id,text,embedding FROM concepts WHERE embedding IS NOT NULL"):
        if v is not None:
            if _cos(v, db.unpack(r["embedding"])) >= threshold:
                return r["id"]
        elif _similar(text, r["text"]) >= 0.75:
            return r["id"]
    return None


def _has_stranger(conn, text, corpus):
    """概念里出现的已知人名，必须在簇内经历原文出现过（防张冠李戴）。"""
    for r in conn.execute("SELECT canonical_name FROM personas"):
        n = r["canonical_name"] or ""
        if len(n) >= 2 and n in text and n not in corpus:
            return True
    return False


def _pack_embed(text):
    try:
        return db.pack(embed.embed(text))
    except Exception:
        return None


def run(conn, min_cluster=2, sim_threshold=0.55, max_cluster=6, limit=300, retry_days=7):
    """事件 → 概念（层级 1）。向量相似 + 共享实体双条件聚类。

    惊讶加权：高 surprise 的事件先成为簇种子，且概念置信度按簇内平均惊讶抬高
    ——「意料之外的事更容易变成认识」。

    每轮只处理前 `limit` 条未巩固事件：聚类是 O(N×簇数)，上万条时全量跑会卡住，
    分批交给自动进化推进（10 分钟一轮）。
    """
    rows = conn.execute(
        "SELECT id,gist,embedding,COALESCE(surprise,0.5) AS surprise,created_at FROM events "
        "WHERE archived=0 AND id NOT IN (SELECT event_id FROM concept_links) "
        "AND (last_try_at IS NULL OR last_try_at < ?) "
        "ORDER BY COALESCE(last_try_at, '') ASC, created_at ASC LIMIT ?",
        ((datetime.now(timezone.utc).astimezone()
          - timedelta(days=retry_days)).isoformat(timespec="seconds"), limit)
    ).fetchall()
    # 选批次按 FIFO（保证不会卡在高惊讶的孤立事件上永远轮不到后面）；
    # 批内聚类仍按惊讶从高到低，让"意外的事"先成为簇种子。
    rows = sorted(rows, key=lambda r: r["surprise"], reverse=True)
    if len(rows) < min_cluster:
        return []

    clusters = []
    for r in rows:
        emb = db.unpack(r["embedding"])
        pers = _event_personas(conn, r["id"])
        placed = False
        for c in clusters:
            common = pers & c["personas"]
            # 「有主事件」与「无主事件」不混：否则概念会把别人做的事安到某个人头上
            if bool(pers) != bool(c["personas"]):
                continue
            if pers and not common:
                continue
            members = c["members"]
            if len(members) >= max_cluster:
                continue  # 簇太大容易炖成空话，另起一簇
            avg = sum(_cos(emb, db.unpack(m["embedding"])) for m in members) / len(members)
            # 没有共同人物时，只靠向量相似，要求更严格
            need = sim_threshold if common else sim_threshold + 0.05
            if avg >= need:
                members.append(r)
                c["personas"] |= pers
                placed = True
                break
        if not placed:
            clusters.append({"members": [r], "personas": set(pers)})

    created = []
    now = _now()
    for c in clusters:
        if len(c["members"]) < min_cluster:
            continue
        if len(c["personas"]) > 1:
            continue  # 多人物混簇必然张冠李戴，宁可不生成
        gists = [m["gist"] for m in c["members"]]
        conn.commit()  # 释放写锁，LLM 调用期间不阻塞其它写入
        corpus = "\n".join(gists)
        who = _persona_name(conn, c["personas"])
        prompt = (_EVENT_PROMPT.replace("{who}", who)
                  .replace("{items}", "\n".join("- " + g for g in gists)))
        text = ""
        for attempt in range(2):
            try:
                text = llm.generate(prompt + (_RETRY_SUFFIX if attempt else ""),
                                    max_tokens=120, timeout=45, think=False).strip()
            except Exception:
                break
            text = _clean_concept(text)  # v23：草稿链污染时提取最后一句定稿
            if _valid_concept(text) and not _has_stranger(conn, text, corpus):
                break
        if not text or not _valid_concept(text) or _has_stranger(conn, text, corpus):
            continue
        cid = _uid("con")
        # 与已有概念近义 → 挂到旧概念上，不再新建（同一主题只留一条认识）
        dup = _similar_concept(conn, text)
        if dup:
            for m in c["members"]:
                conn.execute(
                    "INSERT INTO concept_links(concept_id,event_id,type) VALUES(?,?,?)",
                    (dup, m["id"], "derived_from"))
                conn.execute(
                    "UPDATE events SET consolidation_stage='consolidated' WHERE id=?", (m["id"],))
            conn.commit()
            created.append({"id": dup, "text": text, "n": len(c["members"]), "level": 1,
                            "merged": True})
            continue
        # 置信度随簇内平均惊讶升高：意外得来的经验更"确信"
        avg_sur = sum(float(m["surprise"] or 0.5) for m in c["members"]) / len(c["members"])
        conf = round(min(0.95, 0.55 + 0.4 * avg_sur), 3)
        conn.execute(
            "INSERT INTO concepts(id,text,abstraction_level,confidence,created_at,embedding) "
            "VALUES(?,?,?,?,?,?)",
            (cid, text, 1, conf, now, _pack_embed(text)),
        )
        for m in c["members"]:
            conn.execute(
                "INSERT INTO concept_links(concept_id,event_id,type) VALUES(?,?,?)",
                (cid, m["id"], "derived_from"),
            )
            conn.execute(
                "UPDATE events SET consolidation_stage='consolidated' WHERE id=?", (m["id"],)
            )
        created.append({"id": cid, "text": text, "n": len(c["members"]), "level": 1})

    # 记录本批尝试时间：聚不成的孤儿事件 7 天后才重试，避免永远霸占批次
    now_ts = _now()
    conn.executemany("UPDATE events SET last_try_at=? WHERE id=?",
                     [(now_ts, r["id"]) for r in rows])
    conn.commit()
    return created


def run_concepts(conn, min_cluster=3, sim_threshold=0.78):
    """同层概念 → 更高层概念（再抽象）。门槛提高：少于 3 条容易硬凑，相似度低容易乱炖。"""
    rows = conn.execute(
        "SELECT id,text,embedding,abstraction_level FROM concepts WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return []

    used = {
        r["event_id"]
        for r in conn.execute(
            "SELECT event_id FROM concept_links WHERE type='abstracted_from'"
        )
    }
    by_level = defaultdict(list)
    for r in rows:
        if r["id"] in used:
            continue
        by_level[r["abstraction_level"] or 1].append(r)

    created = []
    now = _now()
    for level, items in by_level.items():
        if len(items) < min_cluster:
            continue
        clusters = []
        for r in items:
            embv = db.unpack(r["embedding"])
            placed = False
            for c in clusters:
                avg = sum(
                    _cos(embv, db.unpack(m["embedding"])) for m in c["members"]
                ) / len(c["members"])
                if avg >= sim_threshold:
                    c["members"].append(r)
                    placed = True
                    break
            if not placed:
                clusters.append({"members": [r]})

        for c in clusters:
            if len(c["members"]) < min_cluster:
                continue
            texts = [m["text"] for m in c["members"]]
            conn.commit()  # 同样：LLM 调用前释放写锁
            prompt = _CONCEPT_PROMPT.replace("{items}", "\n".join("- " + t for t in texts))
            try:
                text = llm.generate(prompt, max_tokens=120, timeout=45, think=False).strip()
            except Exception:
                continue
            text = _clean_concept(text)
            if not text or not _valid_concept(text):
                continue
            if any(_similar(text, t) > 0.8 for t in texts):
                continue  # 只是复述某条下级概念，没有抽象增量
            nid = _uid("con")
            conn.execute(
                "INSERT INTO concepts(id,text,abstraction_level,confidence,created_at,embedding) "
                "VALUES(?,?,?,?,?,?)",
                (nid, text, level + 1, 0.65, now, _pack_embed(text)),
            )
            for m in c["members"]:
                conn.execute(
                    "INSERT INTO concept_links(concept_id,event_id,type) VALUES(?,?,?)",
                    (nid, m["id"], "abstracted_from"),
                )
            created.append({"id": nid, "text": text, "n": len(c["members"]), "level": level + 1})

    conn.commit()
    return created