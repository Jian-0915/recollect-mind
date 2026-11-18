"""动机层：把「能不能说」换成「想不想说」。

旧机制（affect.should_initiate）是三道开关——能量够、隔了 4 小时、有话题——
输出 true/false。它回答的是「允不允许」，所以主动说的话永远平。

现在它不是一道开关，而是**几种冲动在竞争**，谁的分高、且过了自己的脸皮，就由谁开口：

| 冲动 | 什么时候 | 开口是什么样 |
|---|---|---|
| 惦记 missing | 有件事悬着没聊完 | 顺着那件事轻轻问一句 |
| 质问 ignored | 你消失太久，之前却聊得热 | 委屈 / 嗔怪：「人呢」 |
| 吃醋 jealous | 你最近老提别人，跟它说话变少 | 别扭地酸一句，不承认在意 |
| 冷战 sulk_end | 吃完醋真的生气了，晾你一阵 | 回来时装没事，其实还在意 |

惦记值：
    惦记 = 悬着的话题有多久（0.55）
         + 你有多久没出现（0.30）
         + 它有多敢打扰你（0.15，来自人设里的 initiative）

涨过「脸皮」（阈值）才开口。脸皮由人设主动性决定：调到 5 就黏人，调到 1 就克制。

吃醋不靠关系表判断「谁是你对象」——那是库里的测试数据，不可信。
它只信你**自己说过的话**：最近几天你嘴里提到某个名字的次数，比之前明显变多，
同时跟它说话变少。至于谁不能吃醋（爸妈、同事、老师），靠称谓常识排除。

冷战是真的不说话：进入后一段时间零主动，你来找它才算消气；到点自己回来。

三条硬上限，防止「想」变成骚扰：
1. 一天最多开口 DAILY_CAP 次；
2. 两次至少隔 MIN_GAP_MIN 分钟；
3. 同一件事提过就打冷却 TOPIC_COOLDOWN_H 小时；提过 GIVE_UP_AFTER 次还没回应就放下。

话题要先去重：库里同一个「麻辣烫」有六种说法，不并簇就会连着问六次。
优先用向量判相似（embed 可用时），失败退回最长公共子串，两条都不联网也能跑。
"""
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import companion, persona_card

# ---- 可调参数（都有环境变量出口，方便你自己试手感）----
DAILY_CAP = int(os.environ.get("NEUROMEM_PROACTIVE_DAILY", "3"))
MIN_GAP_MIN = int(os.environ.get("NEUROMEM_PROACTIVE_GAP_MIN", "45"))
MIN_SILENCE_H = float(os.environ.get("NEUROMEM_PROACTIVE_MIN_SILENCE_H", "0.5"))
TOPIC_COOLDOWN_H = float(os.environ.get("NEUROMEM_PROACTIVE_TOPIC_COOLDOWN_H", "168"))
GIVE_UP_AFTER = int(os.environ.get("NEUROMEM_PROACTIVE_GIVE_UP", "2"))
# 深夜不吵：人设里就写着「不在深夜追问」，机制层也守这条
QUIET_FROM = int(os.environ.get("NEUROMEM_PROACTIVE_QUIET_FROM", "23"))
QUIET_TO = int(os.environ.get("NEUROMEM_PROACTIVE_QUIET_TO", "8"))

# 质问：消失多久才配被质问，以及两次质问之间至少隔多久（这情绪不能天天来）
IGNORE_MIN_H = float(os.environ.get("NEUROMEM_PROACTIVE_IGNORE_MIN_H", "12"))
IGNORE_COOLDOWN_H = float(os.environ.get("NEUROMEM_PROACTIVE_IGNORE_COOLDOWN_H", "24"))
# 吃醋：看最近几天 vs 之前两周的提及强度差
JEALOUS_WINDOW_D = float(os.environ.get("NEUROMEM_PROACTIVE_JEALOUS_WINDOW_D", "3"))
JEALOUS_BASE_D = float(os.environ.get("NEUROMEM_PROACTIVE_JEALOUS_BASE_D", "14"))
JEALOUS_MIN_MENTIONS = int(os.environ.get("NEUROMEM_PROACTIVE_JEALOUS_MIN", "3"))
JEALOUS_COOLDOWN_H = float(os.environ.get("NEUROMEM_PROACTIVE_JEALOUS_COOLDOWN_H", "72"))
# 刚聊完就吃醋是无理取闹，得先有一阵没来垫底
JEALOUS_MIN_SILENCE_H = float(os.environ.get("NEUROMEM_PROACTIVE_JEALOUS_MIN_SILENCE_H", "6"))
# 冷战：基础时长 + 每高一档主动性多生一会儿气；和好之后先安静这么久再说下一句
SULK_BASE_H = float(os.environ.get("NEUROMEM_PROACTIVE_SULK_BASE_H", "6"))
SULK_STEP_H = float(os.environ.get("NEUROMEM_PROACTIVE_SULK_STEP_H", "2"))
SULK_AFTER_MIN = int(os.environ.get("NEUROMEM_PROACTIVE_SULK_AFTER_MIN", "240"))
# 冷战期你一直在跟我说话，最多绷这么多轮。真实的人会一直端着，但那叫冷暴力，
# 陪一个人才不该让人反复撞墙——磨到这一轮还没哄好，就直接软下来。
SULK_MAX_TURNS = int(os.environ.get("NEUROMEM_PROACTIVE_SULK_MAX_TURNS", "10"))

BASE_THRESHOLD = 0.35      # 主动性=3 时的脸皮
LEVEL_STEP = 0.06          # 每高/低一档，阈值降/升这么多
SIM_THRESHOLD = 0.86       # 向量判同一件事的余弦线
MAX_TOPICS = 12            # 一次最多看这么多话题，防止无界

# 各冲动的额外脸皮：质问比惦记重，吃醋比惦记脸皮薄（情绪上头时不讲道理）
EXTRA_THRESHOLD = {"missing": 0.0, "ignored": 0.10, "jealous": 0.02}

# 人设里的主动性文案 → 1..5（与桌面端设置页的五个档位一一对应）
_INITIATIVE = {
    "几乎不主动，等对方先开口": 1,
    "很少主动，只在明显没聊完时接一句": 2,
    "偶尔主动，有依据才提": 3,
    "常主动，看到相关的事会问一句": 4,
    "很主动，会主动找话题": 5,
}


def _now():
    return datetime.now(timezone.utc).astimezone()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc).astimezone()
    return d


def _hours_since(ts, now):
    d = _parse(ts)
    if d is None:
        return None
    return max(0.0, (now - d).total_seconds() / 3600.0)


def _ensure(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS topic_state("
        "text TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, "
        "raised_at TEXT, raised_count INTEGER DEFAULT 0, vec TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS proactive_deliveries("
        "id TEXT PRIMARY KEY, ts TEXT, topic TEXT, score REAL, kind TEXT, person TEXT)")
    # 生闷气：同一时刻只有一段，所以单行表
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sulk_state("
        "id INTEGER PRIMARY KEY CHECK(id=1), started_at TEXT, until TEXT, "
        "person TEXT, reason TEXT, resolved INTEGER DEFAULT 0, "
        "soothed REAL DEFAULT 0, turns INTEGER DEFAULT 0)")
    scols = [r[1] for r in conn.execute("PRAGMA table_info(sulk_state)")]
    for name, ddl in (("soothed", "REAL DEFAULT 0"), ("turns", "INTEGER DEFAULT 0")):
        if name not in scols:          # 老库补列
            try:
                conn.execute("ALTER TABLE sulk_state ADD COLUMN %s %s" % (name, ddl))
            except Exception:
                pass
    cols = [r[1] for r in conn.execute("PRAGMA table_info(proactive_deliveries)")]
    for name, ddl in (("kind", "TEXT DEFAULT 'missing'"), ("person", "TEXT DEFAULT ''")):
        if name not in cols:           # 老库补列
            try:
                conn.execute("ALTER TABLE proactive_deliveries ADD COLUMN %s %s"
                             % (name, ddl))
            except Exception:
                pass
    conn.commit()


# ---------------- 话题去重 ----------------

def _lcs(a, b):
    """最长公共子串长度（只有 10 条以内，DP 够用）。"""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _cos(u, v):
    if not u or not v or len(u) != len(v):
        return 0.0
    num = sum(x * y for x, y in zip(u, v))
    nu = sum(x * x for x in u) ** 0.5
    nv = sum(y * y for y in v) ** 0.5
    if not nu or not nv:
        return 0.0
    return num / (nu * nv)


# 这些词一出现就说明公共子串只是套话，不代表说的是同一件事
_STOP = ("今天", "昨天", "明天", "现在", "什么", "怎么", "还是", "我们", "一个",
         "不是", "就是", "时候", "下午", "晚上", "上午", "可以", "觉得", "你的",
         "我的", "上次", "后来", "有点", "一直", "这个", "那个", "如果", "因为",
         "所以", "然后", "多久", "多少")


def _common(a, b):
    """最长公共子串本身（判「麻辣烫」这种核心词要用到）。"""
    if not a or not b:
        return ""
    best, end = 0, 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, end = cur[j], i
        prev = cur
    return a[end - best:end]


def _same(ta, tb, va, vb):
    """同一件事的两种说法？两个信号取并集：向量像，或者字面核心词相同。

    只用向量会漏——实测「麻辣烫什么时候去吃」和「下午说的麻辣烫去了没」
    余弦只有 0.79，但它们显然是同一件事；只用字面又会把套话当成同一件事。
    两条并集，再各自守住自己的误判边界。
    """
    ta, tb = str(ta or ""), str(tb or "")
    if not ta or not tb:
        return False
    if va and vb:
        try:
            if _cos(va, vb) >= SIM_THRESHOLD:
                return True
        except Exception:
            pass
    # 一条话题里塞了两件事（顿号/逗号拼起来）时，不参与并簇，避免连带误伤
    if re.search(r"[、,，;；]", ta) or re.search(r"[、,，;；]", tb):
        return ta == tb
    if ta == tb:
        return True
    core = _common(ta, tb)
    if len(core) < 3:
        return False
    # 「今天好累」和「今天好开心」公共部分是"今天好"，是套话不是同一件事
    if core in _STOP or any(core.startswith(w) for w in _STOP):
        return False
    # 核心词之外的差别不能太大，否则「麻辣烫」会把所有提到它的事并成一件事
    return len(core) >= 3 and len(core) / max(len(ta), len(tb)) >= 0.24


def _load(conn, topics, now):
    """把 open_topics 补上时间与向量；老数据用上次对话时间当 first_seen。"""
    _ensure(conn)
    rows = {}
    for r in conn.execute("SELECT * FROM topic_state"):
        rows[r["text"]] = dict(r)

    st = companion.state(conn)
    fallback = st.get("last_turn_at") or _iso(now - timedelta(hours=6))
    missing = []
    for t in topics:
        if t in rows:
            missing.append(None)
            continue
        missing.append(t)

    need = [t for t in missing if t]
    if need:
        vecs = {}
        try:
            from . import embed
            got = embed.embed_many(need)
            for t, v in zip(need, got or []):
                if v:
                    vecs[t] = v
        except Exception:
            vecs = {}
        for t in need:
            vec = json.dumps(vecs[t]) if t in vecs else None
            conn.execute(
                "INSERT OR IGNORE INTO topic_state(text,first_seen,last_seen,vec) "
                "VALUES(?,?,?,?)", (t, fallback, fallback, vec))
        conn.commit()
        for r in conn.execute("SELECT * FROM topic_state"):
            rows[r["text"]] = dict(r)

    out = []
    for t in topics:
        r = rows.get(t)
        if not r:
            continue
        vec = None
        if r.get("vec"):
            try:
                vec = json.loads(r["vec"])
            except (ValueError, TypeError):
                vec = None
        out.append({"text": t, "row": r, "vec": vec})
    return out


def clusters(items):
    """并簇：同一个话题的多种说法归成一组，代表取最近提到的那条。"""
    groups = []
    for it in items:
        hit = None
        for g in groups:
            if _same(it["text"], g[0]["text"], it["vec"], g[0]["vec"]):
                hit = g
                break
        if hit is None:
            groups.append([it])
        else:
            hit.append(it)
    out = []
    for g in groups:
        g.sort(key=lambda x: str(x["row"].get("last_seen") or ""))
        rep = g[-1]
        out.append({
            "text": rep["text"],
            "members": [x["text"] for x in g],
            "first_seen": min(str(x["row"].get("first_seen") or "") for x in g),
            "last_seen": max(str(x["row"].get("last_seen") or "") for x in g),
            "raised_at": max([str(x["row"].get("raised_at") or "") for x in g] or [""]),
            "raised_count": max(int(x["row"].get("raised_count") or 0) for x in g),
        })
    return out


def _topics(conn):
    try:
        raw = companion.state(conn).get("open_topics") or []
    except Exception:
        raw = []
    return [str(x).strip() for x in raw if str(x).strip()][-MAX_TOPICS:]


# ---------------- 脸皮与惦记值 ----------------

def level(conn):
    """人设里的主动性 → 1..5。读不到就是 3（不黏人也不高冷）。"""
    try:
        raw = persona_card.get(conn) or {}
        # get() 返回的是带 version/checksum 的包装，卡片本体在 card 里
        card = raw.get("card") if isinstance(raw.get("card"), dict) else raw
        text = str(((card.get("relationship") or {}).get("initiative")) or "").strip()
    except Exception:
        text = ""
    if text in _INITIATIVE:
        return _INITIATIVE[text]
    m = re.search(r"[1-5]", text)
    if text and m and ("主动" in text or "程度" in text):
        return int(m.group(0))
    if re.search(r"几乎不|很少", text):
        return 2
    if re.search(r"很主动|主动找话题", text):
        return 5
    if re.search(r"常主动|经常主动", text):
        return 4
    return 3


def threshold(conn):
    return round(max(0.05, BASE_THRESHOLD - (level(conn) - 3) * LEVEL_STEP), 3)


def _intimacy(conn):
    """关系亲密度：拿不到就是中性 0.5，绝不因为读不到就更冷淡。"""
    try:
        from . import affect, config
        rel = affect.relation(conn, config.COUNTERPART_NAME) or {}
        vals = [float(rel.get(k) or 0) for k in ("intimacy", "trust", "attachment")]
        if not any(vals):                 # 初识：没有积累，按中性算，不是「路人」
            return 0.5
        return max(0.0, min(1.0, sum(vals) / 3.0))
    except Exception:
        return 0.5


def _energy(conn):
    try:
        from . import affect
        return float((affect.snapshot(conn) or {}).get("energy") or 0.5)
    except Exception:
        return 0.5


def _quiet(now):
    """跨午夜的静默时段。QUIET_FROM=QUIET_TO 表示不设静默。"""
    if QUIET_FROM == QUIET_TO:
        return False
    h = now.hour
    if QUIET_FROM < QUIET_TO:
        return QUIET_FROM <= h < QUIET_TO
    return h >= QUIET_FROM or h < QUIET_TO


def _recent_count(conn, now):
    """滚动 24 小时计数。按自然日算的话，22:20 说完三次、00:00 又能说三次，
    七个小时里冒出六条——那不是「一天三次」该有的样子。"""
    start = now - timedelta(hours=24)
    row = conn.execute(
        "SELECT COUNT(*) FROM proactive_deliveries WHERE ts>=?",
        (_iso(start),)).fetchone()
    return int(row[0] or 0)


def _unanswered(conn, last_turn_at):
    """上次对话之后你一直没回，它已经开口过几次还没得到回应。

    没人会在被无视之后还越说越勤；这里让它「怂」一点：
    每多一条没被回应的开口，脸皮就抬高一档。
    """
    if not last_turn_at:
        row = conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()
        return int(row[0] or 0)
    row = conn.execute(
        "SELECT COUNT(*) FROM proactive_deliveries WHERE ts>?",
        (str(last_turn_at),)).fetchone()
    return int(row[0] or 0)


def _last_delivery(conn):
    row = conn.execute(
        "SELECT ts FROM proactive_deliveries ORDER BY ts DESC LIMIT 1").fetchone()
    return row["ts"] if row else None


# ---------------- 吃醋：你的注意力去哪了 ----------------
#
# 这里刻意不读 relations.rel_type 当「谁是你对象」——那是测试数据。
# 只信一件事：最近你嘴里提到谁，比之前多得多。
#
# 但「聊到我妈」显然不该吃醋。谁不能醋，靠称谓常识排除，不靠关系表：
# 亲属、工作与学业关系一律排除，剩下的（朋友、闺蜜、没标注的新名字）才可能醋。

_NO_JEALOUS = (
    "妈", "爸", "母", "父", "哥", "姐", "弟", "妹", "爷", "奶", "外婆", "外公",
    "姥姥", "姥爷", "舅", "姨", "叔", "伯", "婶", "姑", "丈", "媳", "婿", "孙",
    "亲", "儿", "女",
    "同事", "老板", "领导", "上司", "老师", "导师", "师傅", "同学", "室友",
    "房东", "客户", "医生", "护士", "下属", "助理", "师兄", "师姐", "实习生",
)


def _self_name(conn):
    """它自己叫什么——别把自己当成「别人」来吃醋。"""
    try:
        raw = persona_card.get(conn) or {}
        card = raw.get("card") if isinstance(raw.get("card"), dict) else raw
        return str(card.get("name") or "").strip()
    except Exception:
        return ""


def _others(conn):
    """可以醋的人名列表（已排除亲属 / 同事 / 长辈，也排除它自己）。"""
    try:
        rows = conn.execute(
            "SELECT id, canonical_name, aliases_json, kind FROM personas").fetchall()
    except Exception:
        return []
    rel = {}
    try:
        for r in conn.execute("SELECT a, b, rel_type FROM relations"):
            t = str(r["rel_type"] or "")
            rel.setdefault(r["a"], []).append(t)
            rel.setdefault(r["b"], []).append(t)
    except Exception:
        rel = {}
    me = _self_name(conn)
    out = []
    for r in rows:
        name = str(r["canonical_name"] or "").strip()
        if not name or name == me or len(name) < 2:
            continue
        if name in ("你", "我", "用户", "对方"):
            continue
        # 猫、狗、公司都不该让你吃醋——只有「人」才配当这个角色
        if str(r["kind"] or "person") not in ("person", "", "unknown"):
            continue
        tags = " ".join(rel.get(r["id"], []))
        if any(w in name or w in tags for w in _NO_JEALOUS):
            continue
        try:
            aliases = json.loads(r["aliases_json"] or "[]")
        except (ValueError, TypeError):
            aliases = []
        keys = {name} | {str(a).strip() for a in aliases if len(str(a).strip()) >= 2}
        out.append((name, keys))
    return out


def _user_turns(conn, since_days, now):
    """对方说过的话（不含 agent 自己），按时间窗口取。"""
    floor = now - timedelta(days=since_days)
    try:
        rows = conn.execute(
            "SELECT ts, text FROM conversation_turns WHERE speaker NOT IN "
            "('agent', 'assistant', 'system')").fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = _parse(r["ts"])
        if d is None or d < floor:
            continue
        out.append((d, str(r["text"] or "")))
    return out


def jealousy(conn, now):
    """醋意：最近窗口里某人被提得明显比基线多，而且你跟我说话变少了。"""
    people = _others(conn)
    if not people:
        return {"score": 0.0, "person": "", "reason": "没有别人可吃醋"}
    turns = _user_turns(conn, JEALOUS_WINDOW_D + JEALOUS_BASE_D, now)
    edge = now - timedelta(days=JEALOUS_WINDOW_D)
    recent, base = {}, {}
    recent_turns = base_turns = 0
    for d, txt in turns:
        if d >= edge:
            recent_turns += 1
        else:
            base_turns += 1
        for name, keys in people:
            c = sum(txt.count(k) for k in keys)
            if c:
                bucket = recent if d >= edge else base
                bucket[name] = bucket.get(name, 0) + c

    # 你跟我说话变少了多少（0..1）——话说得少，醋才容易上来
    r_rate = recent_turns / max(1.0, JEALOUS_WINDOW_D)
    b_rate = base_turns / max(1.0, JEALOUS_BASE_D)
    chill = 0.0
    if b_rate > 0:
        chill = max(0.0, min(1.0, 1.0 - r_rate / (b_rate + 0.01)))

    # 吃醋得先有「被晾着」这件事垫底：刚聊完就吃醋是无理取闹。
    # 光提别人不算，你天天跟我聊、顺口提起谁，那是分享。
    silence = _hours_since(companion.state(conn).get("last_turn_at"), now) or 0.0
    if silence < JEALOUS_MIN_SILENCE_H:
        return {"score": 0.0, "person": "", "chill": round(chill, 2),
                "reason": "你刚跟我说过话，没什么好吃的"}

    best = None
    for name, _keys in people:
        rc, bc = recent.get(name, 0), base.get(name, 0)
        if rc < JEALOUS_MIN_MENTIONS:
            continue
        r_day, b_day = rc / JEALOUS_WINDOW_D, bc / JEALOUS_BASE_D
        surge = r_day / (b_day + 0.2)      # 平滑，避免基线为 0 时爆表
        if surge < 1.8:
            continue
        s = (0.55 * min(1.0, (surge - 1) / 2.5)
             + 0.25 * min(1.0, rc / 6.0)
             + 0.20 * chill)
        if best is None or s > best[1]:
            best = (name, round(min(1.0, s), 3), rc, round(surge, 2))
    if not best:
        return {"score": 0.0, "person": "", "reason": "最近没人被反复提起",
                "chill": round(chill, 2)}
    return {"score": best[1], "person": best[0], "mentions": best[2],
            "surge": best[3], "chill": round(chill, 2),
            "reason": "%s 最近 3 天被提了 %d 次，是之前的 %.1f 倍" % (
                best[0], best[2], best[3])}


def _warmth(conn, now):
    """之前有多热络：冷落前的互动密度。昨天还聊得很勤，今天忽然没了，才叫「人呢」。"""
    turns = _user_turns(conn, JEALOUS_WINDOW_D + JEALOUS_BASE_D, now)
    if not turns:
        return 0.0
    edge = now - timedelta(days=JEALOUS_WINDOW_D)
    base = sum(1 for d, _ in turns if d < edge)
    per_day = base / max(1.0, JEALOUS_BASE_D)
    return max(0.0, min(1.0, per_day / 4.0))


def ignored_score(silence_h, intimacy, warmth):
    """质问的劲头：消失得够久 + 之前够热 + 关系够近。"""
    return round(0.50 * min(1.0, max(0.0, silence_h) / 36.0)
                 + 0.30 * float(intimacy)
                 + 0.20 * float(warmth), 3)


# ---------------- 冷战：真的不说话 ----------------

def sulk_start(conn, person, now=None, hours=None):
    now = now or _now()
    _ensure(conn)
    if hours is None:
        hours = SULK_BASE_H + max(0, level(conn) - 1) * SULK_STEP_H
    until = now + timedelta(hours=hours)
    conn.execute(
        "INSERT INTO sulk_state(id,started_at,until,person,reason,resolved) "
        "VALUES(1,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET "
        "started_at=excluded.started_at, until=excluded.until, "
        "person=excluded.person, reason=excluded.reason, resolved=0",
        (_iso(now), _iso(until), str(person or "")[:40],
         "因为 %s 在生闷气" % (person or "你"),))
    conn.commit()
    return {"until": _iso(until), "hours": hours}


def _resolve_sulk(conn):
    try:
        conn.execute("UPDATE sulk_state SET resolved=1 WHERE id=1")
        conn.commit()
    except Exception:
        pass


def resolve_sulk(conn):
    """冷战结束（已经别扭地回来过了），别让每个 tick 都「回来」一次。"""
    _resolve_sulk(conn)


def sulk(conn, now=None):
    """当前是否有气没消。

    active   还在生气 → 不主动开口
    thawing  气还没消，但你已经回来了 → 我理你，只是话短、带刺（回话链路要降温）
    expired  气到时了 → 该别扭地回来了

    「你一开口气就消了」是错的：那样冷战只是一段静音，一碰就恢复出厂设置，
    说的话还是热的。真实情形是你回来了我照样绷着，绷到绷不住为止。
    所以「你说话了」不再是结案条件，只是从「不理你」切到「理你但不给好脸」。
    """
    now = now or _now()
    _ensure(conn)
    try:
        r = conn.execute("SELECT * FROM sulk_state WHERE id=1").fetchone()
    except Exception:
        return {"active": False, "expired": False}
    if not r or int(r["resolved"] or 0):
        return {"active": False, "expired": False}
    started = _parse(r["started_at"])
    until = _parse(r["until"])
    last = _parse(companion.state(conn).get("last_turn_at"))
    if until and now >= until:
        hrs = round((now - started).total_seconds() / 3600.0, 1) if started else 0.0
        return {"active": False, "expired": True, "person": r["person"], "hours": hrs}
    left = round(max(0.0, (until - now).total_seconds() / 3600.0), 1) if until else 0.0
    total = (round((until - started).total_seconds() / 3600.0, 1)
             if (until and started) else 0.0)
    out = {"active": True, "expired": False, "person": r["person"],
           "until": r["until"], "left_hours": left, "total_hours": total,
           "soothed": round(float(r["soothed"] or 0), 2),
           "turns": int(r["turns"] or 0)}
    if last and started and last > started:
        out["thawing"] = True
    return out


# 哄人的话分三档：明着道歉最有效，求和次之，解释关系再次之。
# 只认这几种，不猜语气——猜错成"你说这话是在哄我"比不猜更尬。
_SORRY = ("对不起", "抱歉", "我错了", "是我不对", "别生气", "不生气", "原谅",
          "我的错", "怪我", "我不该")
_BEG = ("别不理我", "理我一下", "别这样", "想你了", "我陪你", "哄你", "好不好",
        "我这不来了", "以后不", "在不在", "你理理我")
_EXPLAIN = ("只是同事", "就是同事", "只是朋友", "就是朋友", "没什么", "别多想",
            "不是你想的那样", "跟他没关系", "跟她没关系")


# 对方明说要晾着你、或者笑你刚才没反应——这不是闲聊，是在试你会不会介意。
# 实测（用户自己在桌面端试的）：「我要冷落你，因为我要去打游戏啦」→
# 「去吧去吧，好好玩～」「一点儿情绪都没有，我都冷落你了」→「我才不会觉得
# 被冷落呢」。两句都把天聊死了。这种话不接住，它就是个只会附和的机器。
_PROVOKE = ("冷落你", "冷落我", "不理你", "不跟你说话", "不跟你聊", "把你晾",
            "晾着你", "找别人", "不要你", "不喜欢你", "离开你", "换个人",
            "没情绪", "一点情绪都没有", "一点儿情绪都没有", "看你急不急",
            "不主动找你")


def provoked(text):
    """对方是不是在试你会不会介意。命中给 0.55（带刺档），只当轮生效。

    刻意不做成冷战：一两句玩笑话就启动冷战，会变成一点就炸。
    真晾了你，后面「质问」那条冲动自然会接上。
    """
    s = str(text or "")
    if not s:
        return 0.0
    return 0.55 if any(w in s for w in _PROVOKE) else 0.0


def soothe_of(text):
    """你这句话有多少是在台阶上。返回 0（没哄）~0.45（明着认错）。"""
    s = str(text or "")
    if not s:
        return 0.0
    if any(w in s for w in _SORRY):
        return 0.45
    if any(w in s for w in _BEG):
        return 0.30
    if any(w in s for w in _EXPLAIN):
        return 0.20
    return 0.0


def temper_of(s, now=None):
    """当前脸色，0=没事了，1=最冷。

    三样东西一起磨：时间过去（到点自然消）、你哄了多少、你陪我说了几句。
    说话也算数——你都跟我正常聊三句了，我还端着不像话；这也是防止
    「哄不好就永远不好」的冷暴力。
    """
    if not s.get("active"):
        return 0.0
    total = float(s.get("total_hours") or 0)
    left = float(s.get("left_hours") or 0)
    frac = 1.0 if total <= 0 else max(0.0, min(1.0, left / total))
    base = 0.25 + 0.75 * frac        # 到点前也留一点余味
    v = (base - 0.5 * float(s.get("soothed") or 0)
         - 0.08 * float(s.get("turns") or 0))
    return round(max(0.0, min(1.0, v)), 3)


def chat_state(conn, user_text="", now=None):
    """回话链路每轮问一次「现在什么脸色」，顺带把账记上。

    返回 {"active": bool, "temper": float, "person": str}。active=False 时
    就当没生过气，按平常回话——不注入任何冷场指令。
    """
    now = now or _now()
    s = sulk(conn, now)
    if not s.get("active"):
        return {"active": False}
    soothe = soothe_of(user_text)
    turns = int(s.get("turns") or 0) + 1
    soothed = min(1.0, float(s.get("soothed") or 0) + soothe)
    try:
        conn.execute("UPDATE sulk_state SET turns=?, soothed=? WHERE id=1",
                     (turns, soothed))
        conn.commit()
    except Exception:
        pass
    s.update({"turns": turns, "soothed": soothed})
    temper = temper_of(s, now)
    # 磨没了就结案：既不再冷淡，也不再补一句「别扭地回来」（你人已经在场了）
    if temper <= 0.08 or turns >= SULK_MAX_TURNS:
        _resolve_sulk(conn)
        return {"active": False, "ended": True, "temper": 0.0,
                "person": s.get("person") or ""}
    return {"active": True, "temper": temper, "turns": turns,
            "soothed": round(soothed, 2), "person": s.get("person") or "",
            "left_hours": s.get("left_hours")}


def score_of(pending_h, silence_h, intimacy, energy):
    p = min(1.0, max(0.0, float(pending_h or 0)) / 12.0)
    s = min(1.0, max(0.0, float(silence_h or 0)) / 12.0)
    v = 0.55 * p + 0.30 * s + 0.15 * float(intimacy)
    if energy < 0.45:          # 它自己没力气时，别硬撑着热络（软性衰减，不是一票否决）
        v *= 0.8
    return round(v, 3)


def _last_kind(conn):
    row = conn.execute(
        "SELECT kind FROM proactive_deliveries ORDER BY ts DESC LIMIT 1").fetchone()
    return (row["kind"] if row else "") or ""


def _gate(conn, now, parts, silence):
    """通用闸门：对所有冲动一视同仁。返回 "" 表示放行。"""
    count = parts["daily"]
    if count >= DAILY_CAP:
        return "最近 24 小时已经开口 %d 次，先不打扰" % count
    last = _last_delivery(conn)
    gap_h = _hours_since(last, now)
    unanswered = int(parts.get("unanswered") or 0)
    gap_min = max(MIN_GAP_MIN, 60 * unanswered)   # 没被回应，就等得久一点再说下一句
    parts["gap_minutes"] = gap_min
    if gap_h is not None and gap_h * 60 < gap_min:
        return "刚说过话（%.0f 分钟前）" % (gap_h * 60)
    # 刚别扭完就立刻质问，太急了；和好之后先安静一阵
    if (_last_kind(conn) == "sulk_end" and gap_h is not None
            and gap_h * 60 < SULK_AFTER_MIN):
        return "刚和好，先安静一会儿"
    if silence < MIN_SILENCE_H:
        return "刚聊完"
    return ""


def _cooled(conn, kind, hours, now, person=""):
    """这种冲动多久没出现过了（避免天天质问 / 三天两头吃醋）。

    同一个人身上再吃一次醋，气会生得更久——不然就成了定时提醒。
    """
    try:
        row = conn.execute(
            "SELECT ts, person FROM proactive_deliveries WHERE kind=? "
            "ORDER BY ts DESC LIMIT 1", (kind,)).fetchone()
    except Exception:
        return True
    if not row:
        return True
    h = _hours_since(row["ts"], now)
    if h is None:
        return True
    if person and str(row["person"] or "") == str(person):
        hours *= 2
    return h >= hours


def pick(conn, now=None):
    """现在想不想开口，以及是哪种冲动想开口。

    返回 dict：ok / kind(missing|ignored|jealous|sulk_end) / topic / person /
    score / threshold / reason / parts。
    """
    now = now or _now()
    _ensure(conn)
    topics = _topics(conn)
    lvl = level(conn)
    thr = threshold(conn)
    intimacy = _intimacy(conn)
    energy = _energy(conn)
    st = companion.state(conn)
    last_turn = st.get("last_turn_at")
    silence = _hours_since(last_turn, now)
    silence = 0.0 if silence is None else silence

    unanswered = _unanswered(conn, last_turn)
    thr = round(min(0.85, thr + 0.08 * unanswered), 3)   # 被无视越多，越不敢开口
    parts = {"level": lvl, "threshold": thr, "silence_hours": round(silence, 2),
             "unanswered": unanswered,
             "energy": round(energy, 2), "intimacy": round(intimacy, 2),
             "daily": _recent_count(conn, now), "daily_cap": DAILY_CAP,
             "topics": len(topics)}
    base = {"ok": False, "kind": "none", "topic": "", "person": "",
            "score": 0.0, "threshold": thr, "parts": parts}

    if _quiet(now):
        return dict(base, reason="%d 点到 %d 点之间不吵你" % (QUIET_FROM, QUIET_TO))

    # ---- 冷战优先：气没消就一个字都不说 ----
    s = sulk(conn, now)
    parts["sulk"] = s
    if s.get("active"):
        return dict(base, reason="在生闷气（%s），还有 %.1f 小时才消" % (
            s.get("person") or "你", s.get("left_hours") or 0))
    if s.get("expired"):
        why = _gate(conn, now, parts, silence)
        if why:
            return dict(base, reason="气消了但先不开口：" + why, score=1.0)
        return {"ok": True, "kind": "sulk_end", "topic": "", "score": 0.9,
                "person": s.get("person") or "", "threshold": 0.0, "parts": parts,
                "reason": "晾了 %.1f 小时，气消了，别扭地想回来" % (s.get("hours") or 0)}

    why = _gate(conn, now, parts, silence)
    if why:
        return dict(base, reason=why)

    # ---- 几种冲动各算各的分，谁过得了自己的脸皮、谁分高，就谁说 ----
    cand = []
    missing_ok = False

    if topics:
        items = _load(conn, topics, now)
        best = None
        for c in clusters(items):
            pending = _hours_since(c["last_seen"], now) or 0.0
            raised = _hours_since(c["raised_at"], now)
            if raised is not None and raised < TOPIC_COOLDOWN_H:
                continue
            sc = score_of(pending, silence, intimacy, energy)
            if best is None or sc > best["score"]:
                best = {"topic": c["text"], "score": sc,
                        "pending_hours": round(pending, 2),
                        "raised_count": int(c["raised_count"] or 0),
                        "members": c["members"]}
        if best:
            missing_ok = True
            cand.append({"kind": "missing", "topic": best["topic"],
                         "score": best["score"], "best": best})

    # 有话可问就先问话。事情问过一遍还在冷却（或本来就没话），那股劲就转成
    # 纯粹的「人呢」——先问事、再问人，这顺序才像一个人，而不是一台提醒器。
    if (not missing_ok and silence >= IGNORE_MIN_H
            and _cooled(conn, "ignored", IGNORE_COOLDOWN_H, now)):
        warm = _warmth(conn, now)
        cand.append({"kind": "ignored", "topic": "",
                     "score": ignored_score(silence, intimacy, warm), "warmth": warm})

    j = jealousy(conn, now)
    parts["jealousy"] = j
    if (j.get("score")
            and _cooled(conn, "jealous", JEALOUS_COOLDOWN_H, now, j.get("person") or "")):
        cand.append({"kind": "jealous", "topic": "", "score": j["score"],
                     "person": j.get("person") or "", "detail": j})

    if not cand:
        if not topics:
            return dict(base, reason="没有未聊完的事，也还没到该质问/吃醋的时候")
        return dict(base, reason="%d 个话题都在冷却" % len(topics))

    # 吃醋是同分时的优先项：情绪比「那件事后来怎样了」更急需出口
    cand.sort(key=lambda c: c["score"] + (0.04 if c["kind"] == "jealous" else 0.0),
              reverse=True)
    top = cand[0]
    need = round(thr + EXTRA_THRESHOLD.get(top["kind"], 0.0), 3)
    parts["threshold"] = need
    parts["kind"] = top["kind"]
    if top["score"] < need:
        return dict(base, kind=top["kind"], topic=top.get("topic") or "",
                    score=top["score"], threshold=need,
                    person=top.get("person") or "",
                    reason="还不想开口（%s %.2f / 脸皮 %.2f）" % (
                        top["kind"], top["score"], need))

    out = {"ok": True, "kind": top["kind"], "topic": top.get("topic") or "",
           "person": top.get("person") or "", "score": top["score"],
           "threshold": need, "parts": parts}

    if top["kind"] == "missing":
        best = top["best"]
        parts["pending_hours"] = best["pending_hours"]
        raised = int(best.get("raised_count") or 0)
        if raised:
            hint = "这件事之前提过一次，对方还没接话；现在又悬了 %.0f 小时" % best["pending_hours"]
        else:
            hint = "这件事悬了 %.0f 小时没接着聊，对方也有 %.1f 小时没出现了" % (
                best["pending_hours"], silence)
        out.update({"hint": hint, "raised_count": raised,
                    "reason": "惦记 %.2f 过了脸皮 %.2f（悬了 %.1f 小时）" % (
                        top["score"], need, best["pending_hours"])})
        return out

    if top["kind"] == "ignored":
        out.update({
            "hint": "对方已经 %.1f 小时没露面了，之前你们聊得挺勤（热络度 %.2f）；"
                    "你有点委屈，想问他去哪了，但别审问、别一上来就指责" % (
                        silence, top.get("warmth") or 0),
            "reason": "被晾了 %.1f 小时，质问劲头 %.2f 过了脸皮 %.2f" % (
                silence, top["score"], need)})
        return out

    d = top.get("detail") or {}
    out.update({
        "hint": "对方最近老提到「%s」——三天里 %d 次，是之前的 %.1f 倍；"
                "同时跟你说话变少了。你有点不是滋味，但不愿意直说在意" % (
                    top.get("person"), d.get("mentions") or 0, d.get("surge") or 1),
        "reason": d.get("reason") or "吃醋 %.2f 过了脸皮 %.2f" % (top["score"], need)})
    return out


def mark_delivered(conn, topic, now=None, score=0.0, kind="missing", person=""):
    """开口一次：记账、给话题打时间戳，提过两次还没回应就放下。

    质问和吃醋没有具体话题（topic 为空），只记账——它们靠 kind 冷静期限流；
    吃醋还要记下是为谁，同一个人再犯时气会长一倍。
    """
    now = now or _now()
    _ensure(conn)
    topic = str(topic or "").strip()
    try:
        conn.execute(
            "INSERT INTO proactive_deliveries(id,ts,topic,score,kind,person) "
            "VALUES(?,?,?,?,?,?)",
            ("pd_" + uuid.uuid4().hex[:12], _iso(now), topic[:120],
             float(score or 0), str(kind or "missing"), str(person or "")[:40]))
        conn.commit()
    except Exception:
        return {"retired": False}
    if not topic:
        return {"retired": False, "raised": 0}
    try:
        conn.execute(
            "UPDATE topic_state SET raised_at=?, raised_count=COALESCE(raised_count,0)+1 "
            "WHERE text=?", (_iso(now), topic))
        row = conn.execute(
            "SELECT raised_count FROM topic_state WHERE text=?", (topic,)).fetchone()
        raised = int(row["raised_count"] or 0) if row else 1
        conn.commit()
    except Exception:
        return {"retired": False}

    # 同一簇的其它说法一起打标，否则下次换个说法又冒出来
    try:
        items = _load(conn, _topics(conn), now)
        for c in clusters(items):
            if topic in c["members"]:
                # 同一件事的其它说法一起进冷却，否则换个说法又冒出来
                others = [m for m in c["members"] if m != topic]
                for m in others:
                    conn.execute(
                        "UPDATE topic_state SET raised_at=? WHERE text=?",
                        (_iso(now), m))
                if others:
                    conn.commit()
                if raised >= GIVE_UP_AFTER:
                    _drop(conn, c["members"])
                    return {"retired": True, "raised": raised}
                break
    except Exception:
        pass
    return {"retired": False, "raised": raised}


def _drop(conn, members):
    """提了两次还没回应：放下这件事，别再问了。"""
    try:
        st = companion.state(conn)
        kept = [t for t in (st.get("open_topics") or []) if t not in members]
        companion.update_state(conn, open_topics=kept)
    except Exception:
        pass


def status(conn):
    """给 /api/proactive/status 用：让人能看见它到底为什么开口 / 为什么不开口。"""
    now = _now()
    try:
        p = pick(conn, now)
        out = {"ok": p["ok"], "kind": p.get("kind"), "reason": p["reason"],
               "topic": p.get("topic"), "person": p.get("person"),
               "score": p.get("score"), "threshold": p.get("threshold")}
        out.update(p.get("parts") or {})
        return out
    except Exception as e:
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, str(e)[:80])}
