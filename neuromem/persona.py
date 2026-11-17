"""人物档案：画像生成、别名合并、人-人关系抽取与查看。"""
import json
import re
import uuid
from datetime import datetime, timezone

from . import config, db, entity, llm

_PROFILE_PROMPT = """根据下面关于"{name}"的经历，写一段简明的人物画像（100字内）。
包含：性格 / 喜好 / 重要关系 / 近况。用第三人称，不要编造经历里没有的内容。
**必须用简体中文输出**：即使思考过程用了英文，最终这段画像也只能是中文。
经历：
{events}
只输出画像文字，不要解释。"""

_REL_WORDS = [
    "同事", "朋友", "同学", "家人", "爸爸", "妈妈", "儿子", "女儿", "夫妻",
    "男朋友", "女朋友", "老板", "领导", "下属", "亲戚", "哥哥", "姐姐",
    "弟弟", "妹妹", "老师", "学生", "室友", "搭档", "合作伙伴",
    # 2026-11-13 扩充：常见亲属与社交称谓（此前这些句子连 hint 都命中不了）
    "发小", "嫂子", "亲兄弟", "兄弟", "姐妹", "闺蜜",
    "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷",
    "堂哥", "堂姐", "表弟", "表妹", "表哥", "表姐",
    "姐夫", "妹夫", "儿媳", "女婿", "婆婆", "丈母娘", "邻居", "房东",
]
_REL_WORD_SET = set(_REL_WORDS)
_REL_ALT = "|".join(sorted(_REL_WORDS, key=len, reverse=True))
_PRONOMS_US = {"我", "自己", "本人", "俺", "用户"}
# 亲属/社交称谓本身就能指人（"表哥""妈妈"），可以当关系两端；
# 而"同事/朋友/同学"这类是关系类型，不能当人名
_KINSHIP = {
    "爸爸", "妈妈", "儿子", "女儿", "哥哥", "姐姐", "弟弟", "妹妹",
    "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷",
    "堂哥", "堂姐", "表弟", "表妹", "表哥", "表姐",
    "姐夫", "妹夫", "儿媳", "女婿", "婆婆", "丈母娘", "嫂子", "发小",
}
_STOP_NAMES = {"这", "那", "他", "她", "它", "其", "此", "你", "您", "每", "某"}
# 泛称不是具名人物，不能建成实体（C4："和朋友去看电影" 不该生成"朋友"这个人）
_GENERIC = {"朋友", "同事", "同学", "老师", "学生", "领导", "老板", "室友",
            "家人", "亲戚", "邻居", "下属", "搭档", "合作伙伴", "男朋友", "女朋友"}
_PRONOUNS = {"我", "自己", "本人", "俺"}
# 关系词同义组：模型可能把"好朋友"归纳成"朋友"，只要原文有同组词就放行
_REL_SYNONYMS = [
    {"同事", "工作伙伴", "搭档", "合作伙伴"},
    {"朋友", "好朋友", "好友", "哥们", "闺蜜"},
    {"父子", "爸爸", "父亲", "儿子"},
    {"母子", "妈妈", "母亲", "儿子"},
    {"夫妻", "老公", "老婆", "丈夫", "妻子"},
    {"领导", "老板", "上司", "主管"},
    {"同学", "同班同学", "校友"},
    {"师生", "老师", "学生"},
    {"兄弟姐妹", "哥哥", "弟弟", "姐姐", "妹妹"},
    {"亲戚", "叔叔", "阿姨", "舅舅", "姑姑"},
]

_REL_PROMPT = """从句子中抽取人物之间的关系。严格只输出 JSON 数组，没有则输出 []。
规则：
1. a 和 b 必须是句子中明确出现的两个不同人物，不得引入句子外的人，也不得把"同事/朋友"等关系词当人名；
2. type 必须来自句中的关系表述（如 同事/朋友/父子/领导），不得推断；
3. 只是同处一句不代表有关系，必须句中有明确的关系表述；
4. 不确定就输出 []。
每个元素格式：{"a":"人物A","b":"人物B","type":"关系词"}
句子：{text}"""


def _type_supported(rel_type, text):
    if rel_type in text:
        return True
    for group in _REL_SYNONYMS:
        if rel_type in group and any(w in text for w in group):
            return True
    return False


def _name_in_text(name, text, speaker):
    """人物必须出现在原文里；"我"这类代词按 speaker 计。"""
    if not name:
        return False
    if name in text:
        return True
    if speaker and name == speaker and any(p in text for p in _PRONOUNS):
        return True
    return False


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _uid(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def list_personas(conn):
    return [
        {"id": r["id"], "name": r["canonical_name"],
         "aliases": json.loads(r["aliases_json"] or "[]"), "profile": r["profile"] or "",
         "profile_at": r["profile_at"] or "",
         "kind": _row_kind(r), "bond": _row_bond(r)}
        for r in conn.execute("SELECT * FROM personas ORDER BY canonical_name")
    ]


def _row_bond(r):
    """personas.bond；旧行没有值时为 social（保守：宁可留着当社交对象）。"""
    try:
        b = r["bond"]
    except (IndexError, KeyError):
        b = None
    return b or "social"


def _row_kind(r):
    """personas.kind；旧库/旧行没有列或为空时按人名兜底判定。

    v19 之前建的人物卡一律是 person（那时没有类型概念），所以这里不能
    简单回落到 'person'——「金毛」「清明」这些历史污染行同样没有 kind。
    """
    try:
        k = r["kind"]
    except (IndexError, KeyError):
        k = None
    if k:
        return k
    return entity.kind_of(r["canonical_name"])


def find(conn, name):
    if not name:
        return None
    for r in conn.execute("SELECT * FROM personas"):
        aliases = json.loads(r["aliases_json"] or "[]")
        if name == r["canonical_name"] or name in aliases:
            return r
    return None


def ensure(conn, name):
    """人物不存在则创建，返回该人物行。"""
    p = find(conn, name)
    if p:
        return p
    pid = _uid("p")
    conn.execute(
        "INSERT INTO personas(id,canonical_name,aliases_json,profile) VALUES(?,?,?,?)",
        (pid, name, json.dumps([name], ensure_ascii=False), ""),
    )
    conn.commit()
    return conn.execute("SELECT * FROM personas WHERE id=?", (pid,)).fetchone()


def event_gists(conn, pid, limit=20):
    rows = conn.execute(
        "SELECT e.gist FROM edges ed JOIN events e ON ed.source=e.id "
        "WHERE ed.target=? AND ed.type='involves' AND e.archived=0 "
        "ORDER BY COALESCE(e.occurred_at, e.created_at) DESC LIMIT ?",
        (pid, limit),
    ).fetchall()
    return [r["gist"] for r in rows]


def _valid_profile(text):
    """挡住模型的退化输出（"0000000000"、"。。。"、空串）。"""
    t = (text or "").strip()
    if len(t) < 12:
        return False
    if re.fullmatch(r"[\d\s\.\-]+", t):
        return False
    if len(set(t)) <= 2:
        return False
    return len(re.findall(r"[\u4e00-\u9fff]", t)) >= 8


def build_profile(conn, name):
    p = find(conn, name)
    if not p:
        return None
    gists = event_gists(conn, p["id"])
    if not gists:
        return None
    prompt = (
        _PROFILE_PROMPT.replace("{name}", p["canonical_name"])
        .replace("{events}", "\n".join("- " + g for g in gists))
    )
    try:
        text = llm.generate(prompt, model=config.ANSWER_MODEL, max_tokens=400, timeout=90).strip()
    except Exception:
        return None
    if not _valid_profile(text):
        return None
    n = conn.execute(
        "SELECT COUNT(*) FROM edges ed JOIN events e ON e.id=ed.source "
        "WHERE ed.target=? AND ed.type='involves' AND e.archived=0", (p["id"],)
    ).fetchone()[0]
    conn.execute(
        "UPDATE personas SET profile=?, profile_at=?, profile_events=? WHERE id=?",
        (text, _now(), n, p["id"]),
    )
    conn.commit()
    return text


# === 画像自动生成（2026-11-13：此前只有手动按钮会生成，画像一直空着） ===
PROFILE_MIN_EVENTS = 3      # 至少 3 条经历才画像，太少的画像没信息量
PROFILE_REFRESH = 5         # 上次画像后新增 ≥5 条才刷新
PROFILE_PER_ROUND = 2       # 每轮代谢最多生成 2 个（控制 LLM 成本）


def pending_profiles(conn, limit=PROFILE_PER_ROUND):
    """待画像的人物。v19 起只给 kind='person' 生成，v20 起跳过 bond='mentioned'。

    以前「清明」「金毛」「我们一大家子」也会进画像队列，产出「清明最近很忙」
    这种把节日当人的文本，再被召回喂给生成层。刘慈欣 / 周杰伦虽然是人，
    但只在「看他的小说」「歌单里占了大半」里出现，给他写画像是把作品当社交。
    """
    rows = conn.execute(
        "SELECT p.id, p.canonical_name, p.profile, p.profile_events, p.kind, p.bond, "
        "COUNT(e.id) n "
        "FROM personas p "
        "JOIN edges ed ON ed.target=p.id AND ed.type='involves' "
        "JOIN events e ON e.id=ed.source AND e.archived=0 "
        "GROUP BY p.id HAVING n >= ? ORDER BY n DESC",
        (PROFILE_MIN_EVENTS,),
    ).fetchall()
    out = []
    for r in rows:
        if _row_kind(r) != "person":
            continue
        try:
            if (r["bond"] or "social") == "mentioned":
                continue
        except (IndexError, KeyError):
            pass
        have = (r["profile"] or "").strip()
        base = int(r["profile_events"] or 0)
        if not have or r["n"] - base >= PROFILE_REFRESH:
            out.append(r)
    return out[:limit]


def build_profiles(conn, limit=PROFILE_PER_ROUND):
    """代谢中自动补齐 / 刷新画像，返回本轮更新的人物。"""
    out = []
    for r in pending_profiles(conn, limit):
        text = build_profile(conn, r["canonical_name"])
        if not text:
            continue  # LLM 失败留到下一轮，不标记完成
        out.append({"name": r["canonical_name"], "events": r["n"]})
    return out


def merge(conn, keep_name, drop_name):
    keep = find(conn, keep_name)
    drop = find(conn, drop_name)
    if not keep or not drop or keep["id"] == drop["id"]:
        return False
    aliases = set(json.loads(keep["aliases_json"] or "[]"))
    aliases |= set(json.loads(drop["aliases_json"] or "[]"))
    aliases.add(drop["canonical_name"])
    aliases.discard(keep["canonical_name"])
    conn.execute(
        "UPDATE personas SET aliases_json=? WHERE id=?",
        (json.dumps(sorted(aliases), ensure_ascii=False), keep["id"]),
    )
    # P0-8：画像也要合并，否则 drop 的画像随删除丢失
    kp = (keep["profile"] or "").strip()
    dp = (drop["profile"] or "").strip()
    if dp and dp not in kp:
        merged_profile = (kp + "\n" + dp) if kp else dp
        conn.execute("UPDATE personas SET profile=? WHERE id=?",
                     (merged_profile[:400], keep["id"]))
    conn.execute(
        "UPDATE edges SET target=? WHERE target=? AND type='involves'",
        (keep["id"], drop["id"]),
    )
    conn.execute("UPDATE relations SET a=? WHERE a=?", (keep["id"], drop["id"]))
    conn.execute("UPDATE relations SET b=? WHERE b=?", (keep["id"], drop["id"]))
    conn.execute("DELETE FROM personas WHERE id=?", (drop["id"],))
    conn.commit()
    return True


_REL_NORMALIZE = {
    "最好的朋友": "好朋友", "好友": "好朋友", "老朋友": "好朋友",
    "大学同学": "同学", "同班同学": "同学", "老同学": "同学",
    "大学室友": "室友", "舍友": "室友", "室友": "室友",
    "丈夫": "夫妻", "妻子": "夫妻", "老公": "夫妻", "老婆": "夫妻",
}


def normalize_type(t):
    """关系词规范化：避免「最好的朋友 / 好朋友」被当成两种关系重复入库。"""
    t = (t or "").strip()
    return _REL_NORMALIZE.get(t, t)


def has_relation_hint(text):
    return any(w in text for w in _REL_WORDS)


# === 关系抽取：分句 + 规则兜底 ===
_SPLIT_RE = re.compile(r"[，,。；;！!？?\n]+")
# "小明是用户大学认识的好朋友"（中间可有修饰，但不能跨句）
_RULE_SELF = re.compile(
    r"(?P<a>[\u4e00-\u9fa5A-Za-z·]{1,6}?)(?:是|和|跟|与)(?:用户|我|自己)"
    r"(?:的)?[^，。；！？\s]{0,10}?(?P<t>%s)" % _REL_ALT)
# "用户和小林是同学"
_RULE_PAIR = re.compile(
    r"(?P<a>[\u4e00-\u9fa5A-Za-z·]{1,6}?)(?:和|跟|与)"
    r"(?P<b>[\u4e00-\u9fa5A-Za-z·]{1,6}?)(?:是|成为了|互为)(?P<t>%s)" % _REL_ALT)
# "嫂子是表哥的大学同学"（"大学"这类修饰词要允许，但只认词表里的关系词）
_RULE_POSS = re.compile(
    r"(?P<a>[\u4e00-\u9fa5A-Za-z·]{1,6}?)是"
    r"(?P<b>[\u4e00-\u9fa5A-Za-z·]{1,6}?)的"
    r"[\u4e00-\u9fa5]{0,4}?(?P<t>%s)" % _REL_ALT)


def sentences(text):
    """按中英文标点切句：关系常只出现在其中一句，整句喂模型会被后面的描述带偏。"""
    return [p.strip() for p in _SPLIT_RE.split(text or "") if p.strip()]


def _llm_items(sentence):
    """单句抽关系，返回 [{"a","b","t"}]（未校验）。"""
    try:
        d = llm.generate_json(
            _REL_PROMPT.replace("{text}", sentence),
            model=config.ANSWER_MODEL, max_tokens=200, timeout=60,
        )
    except Exception:
        return []
    if isinstance(d, dict):
        if "relations" in d:
            d = d.get("relations") or []
        elif "a" in d and "b" in d:
            d = [d]
        else:
            d = []
    if not isinstance(d, list):
        return []
    out = []
    for item in d:
        if not isinstance(item, dict):
            continue
        a = (item.get("a") or "").strip()
        b = (item.get("b") or "").strip()
        t = (item.get("type") or "").strip()
        if a and b and t:
            out.append({"a": a, "b": b, "t": t})
    return out


def _rule_items(conn, text, me):
    """本地规则兜底：模型全判 [] 时，用明确句式补齐（不猜、只认原文有的）。"""

    def ok_name(name):
        if not name:
            return False
        if name == me or name in _PRONOMS_US:
            return True  # 说话人本身是合法一端
        if name in _STOP_NAMES or len(name) < 2 or len(name) > 4:
            return False
        if name.startswith("用户") or "的" in name:
            return False  # 挡住"用户大学认识"这类粘连片段
        if name in _REL_WORD_SET and name not in _KINSHIP and not find(conn, name):
            return False  # 关系类型词不能当人名（除非库里确有同名人物）
        return True

    out = []
    for s in sentences(text):
        for m in _RULE_SELF.finditer(s):
            a = m.group("a")
            if a in _PRONOMS_US or not ok_name(a):
                continue
            out.append({"a": a, "b": me, "t": m.group("t")})
        for m in _RULE_PAIR.finditer(s):
            a, b = m.group("a"), m.group("b")
            if a in _PRONOMS_US:
                a = me
            if b in _PRONOMS_US:
                b = me
            if not ok_name(a) or not ok_name(b):
                continue
            out.append({"a": a, "b": b, "t": m.group("t")})
        for m in _RULE_POSS.finditer(s):
            a, b = m.group("a"), m.group("b")
            if "的" in a:
                a = a.split("的")[-1]  # "娶的嫂子" → "嫂子"
            if b in _PRONOMS_US:
                b = me
            if a in _PRONOMS_US:
                continue
            if not ok_name(a) or not ok_name(b):
                continue
            out.append({"a": a, "b": b, "t": m.group("t")})
    return out


def extract_relations(conn, text, speaker=None):
    """从句子抽人-人关系，存 relations 表。仅当含关系词时调用。

    2026-11-13 修正：整句喂模型时长句常被判 []（实测「…，性格开朗，喜欢打篮球」→ []，
    去掉后半句就能抽出来）。改为按标点分句逐句抽；模型全空时再用本地规则兜底，
    只认「X 是 用户的 关系词」「X 和 Y 是 关系词」这类明确句式。
    """
    if not has_relation_hint(text):
        return []
    me = speaker or "用户"

    items = []
    for s in sentences(text):
        if has_relation_hint(s):
            items.extend(_llm_items(s))
    if not items:
        items = _rule_items(conn, text, me)
    if not items:
        return []

    # 分句可能重复抽到同一条，先按 (a,b,type) 去重
    seen, uniq = set(), []
    for it in items:
        t = normalize_type(it["t"])
        k = (it["a"], it["b"], t)
        if k in seen:
            continue
        seen.add(k)
        uniq.append({"a": it["a"], "b": it["b"], "t": t})

    pronouns = _PRONOUNS
    out = []
    for item in uniq:
        a, b, t = item["a"], item["b"], item["t"]
        if a in pronouns or a in _PRONOMS_US:
            a = me
        if b in pronouns or b in _PRONOMS_US:
            b = me
        # 硬校验：两个人必须都在原文出现、关系词必须有原文支撑，否则视为模型编造
        if not _name_in_text(a, text, speaker) or not _name_in_text(b, text, speaker):
            continue
        if a == b:
            continue
        # 关系类型词（同事/朋友/同学）不能当人名；亲属称谓（表哥/嫂子）可以
        if ((a in _REL_WORD_SET and a not in _KINSHIP and not find(conn, a))
                or (b in _REL_WORD_SET and b not in _KINSHIP and not find(conn, b))):
            continue
        # 泛称（朋友/同事/室友）不建具名实体，除非库里本来就有同名人物
        if (a in _GENERIC and not find(conn, a)) or (b in _GENERIC and not find(conn, b)):
            continue
        if not _type_supported(t, text):
            continue
        pa, pb = ensure(conn, a), ensure(conn, b)
        if not pa or not pb:
            continue
        dup = conn.execute(
            "SELECT id FROM relations WHERE rel_type=? AND ((a=? AND b=?) OR (a=? AND b=?))",
            (t, pa["id"], pb["id"], pb["id"], pa["id"]),
        ).fetchone()
        if dup:
            continue
        rid = _uid("rel")
        conn.execute(
            "INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) VALUES(?,?,?,?,?,?,?,?)",
            (rid, pa["id"], pb["id"], t, 0.5, "", "", text),
        )
        out.append({"id": rid, "a": pa["canonical_name"], "b": pb["canonical_name"], "type": t})
    conn.commit()
    return out


def _name_map(conn):
    return {r["id"]: r["canonical_name"]
            for r in conn.execute("SELECT id,canonical_name FROM personas")}


def list_relations(conn):
    """关系列表；人物已被删除/合并时**不许把内部 id 当名字下发**。

    v22 之前这里是 ``name_map.get(r["a"], r["a"])``：人物行一旦没了（历史合并、
    手工删库、旧版 merge 不迁 relations），接口就直接把 ``p_8922ac406acb``
    这种内部主键渲染到「关系」页上。现在名字缺失时返回空串并带 ``broken``
    标记，前端显示「人物已删除」，id 只在 ``*_id`` 里留给排障和修复用。
    """
    names = _name_map(conn)
    out = []
    for r in conn.execute("SELECT * FROM relations ORDER BY rowid DESC"):
        an, bn = names.get(r["a"]), names.get(r["b"])
        out.append({
            "id": r["id"],
            "a_id": r["a"], "b_id": r["b"],
            "a": an or "", "b": bn or "",
            "a_missing": an is None, "b_missing": bn is None,
            "broken": an is None or bn is None,
            "type": r["rel_type"], "strength": r["strength"],
            "since": r["since"] or "", "until": r["until"] or "",
            "evidence": r["evidence"] or "",
        })
    return out


def heal_relations(conn, dry_run=True):
    """修复一端已被删除的关系。

    历史数据里存在「关系指向已不存在的人物」——旧版合并只搬了 edges，没搬
    relations。这些行在界面上表现为一串 id（见 ``list_relations`` 注释）。
    大部分能按关系词复原：``A —表哥→ p_6a32…`` 里的 ``表哥`` 就是被删掉的
    那个人物名，库里通常还留着一张同名人物卡。

    只做**可证明**的修复：候选名 = 关系词，且库里确有同名人物、且修完不会
    变成自环。修不了的原样保留（交给人工判断，不猜、不删）。
    返回 ``[(rid, rel_type, from_id, to_id, to_name), …]``。
    """
    names = _name_map(conn)
    actions = []
    for r in conn.execute("SELECT * FROM relations ORDER BY rowid DESC"):
        a, b, t = r["a"], r["b"], (r["rel_type"] or "").strip()
        target = None
        if a not in names and b in names:
            target = (a, "a", b)
        elif b not in names and a in names:
            target = (b, "b", a)
        if not target or not t:
            continue
        old_id, col, other_id = target
        cand = find(conn, t)
        if not cand or cand["id"] == other_id:
            continue          # 修不了，或会变成「自己和自己」的关系
        actions.append((r["id"], t, old_id, cand["id"], cand["canonical_name"]))
        if not dry_run:
            conn.execute("UPDATE relations SET %s=? WHERE id=?" % col,
                         (cand["id"], r["id"]))
    if not dry_run and actions:
        conn.commit()
    return actions


def active_relations(conn, min_strength=0.3, on_date=None):
    """当前仍有效的关系：未结束（until 为空或 >= on_date）且强度达阈值。

    P0-7：强度与时间边界不再只是展示字段——召回与回答都会用。
    """
    day = (on_date or datetime.now().date().isoformat())[:10]
    names = _name_map(conn)
    out = []
    for r in conn.execute("SELECT * FROM relations ORDER BY strength DESC"):
        until = (r["until"] or "")[:10]
        if until and until < day:
            continue
        st = float(r["strength"] or 0.0)
        if st < min_strength:
            continue
        a, b = names.get(r["a"]), names.get(r["b"])
        if not a or not b:
            # 一端已被删除：这条关系既没法描述、也没法判断指向谁。
            # 以前会退回显示原 id，结果把 p_xxxx 拼进召回上下文/回答里。
            continue
        out.append({
            "id": r["id"], "a_id": r["a"], "b_id": r["b"],
            "a": a, "b": b,
            "type": r["rel_type"], "strength": round(st, 3),
            "since": (r["since"] or "")[:10], "until": until,
            "evidence": r["evidence"] or "",
        })
    return out