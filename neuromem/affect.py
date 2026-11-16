"""皮层情感层（v13）：多维情绪 → 编码、召回、表达与决策。

旧 ``emotion.py`` 只有 valence/arousal 两维、6h 半衰期，且**只**给召回打分。
只加标签不会有体感变化，所以这里把情绪接到三处：

1. 编码：``store.remember(affect_strength=...)`` 用 ``salience_boost()`` 提高强
   情绪记忆的显著性（τ 变长、更不容易被时间冲淡）。
2. 召回：``update(sync_emotion=True)`` 同步写回旧 emotion_state，让既有
   EMOTION_BIAS（心境一致性）继续生效，不重复造一套召回逻辑。
3. 表达与决策：``expression_hints()`` / ``should_initiate()`` 产出语气、追问、
   主动发起、话题回避建议，随 ContextPack 交给 agent 判断。

四类状态分开存：离散情绪向量（快）、慢变量心境、按人关系情感、能量节律，
外加可追溯的情绪事件日志。全部按**真实时间**衰减，与进程是否常驻无关。
"""
import json
import math
import uuid
from datetime import datetime, timezone

from . import config, emotion

# 八类离散情绪 → (效价, 唤醒)。权重用于把向量折回旧两维。
DIMS = {
    "joy":          (0.80, 0.60),
    "anger":        (-0.70, 0.85),
    "sadness":      (-0.75, 0.30),
    "fear":         (-0.70, 0.90),
    "surprise":     (0.00, 0.85),
    "disgust":      (-0.60, 0.45),
    "anticipation": (0.35, 0.65),
    "guilt":        (-0.55, 0.40),
}
CN_LABELS = {
    "joy": "喜", "anger": "怒", "sadness": "哀", "fear": "惧",
    "surprise": "惊", "disgust": "厌", "anticipation": "期待", "guilt": "愧疚",
}
LEXICON = {
    "开心": "joy", "高兴": "joy", "喜悦": "joy", "愉快": "joy", "兴奋": "joy",
    "愤怒": "anger", "生气": "anger", "恼火": "anger", "烦躁": "anger",
    "难过": "sadness", "低落": "sadness", "伤心": "sadness", "失落": "sadness",
    "害怕": "fear", "恐惧": "fear", "焦虑": "fear", "紧张": "fear",
    "惊讶": "surprise", "意外": "surprise", "震惊": "surprise",
    "厌恶": "disgust", "反感": "disgust", "讨厌": "disgust",
    "期待": "anticipation", "盼望": "anticipation", "好奇": "anticipation",
    "愧疚": "guilt", "内疚": "guilt", "抱歉": "guilt", "自责": "guilt",
}

_DDL = [
    """CREATE TABLE IF NOT EXISTS affect_state (
         id INTEGER PRIMARY KEY CHECK(id=1),
         vector_json TEXT NOT NULL, mood_json TEXT NOT NULL,
         energy REAL DEFAULT 0.5, valence REAL DEFAULT 0,
         arousal REAL DEFAULT 0, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS affect_relations (
         person TEXT PRIMARY KEY, trust REAL DEFAULT 0,
         intimacy REAL DEFAULT 0, attachment REAL DEFAULT 0,
         label TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS affect_events (
         id TEXT PRIMARY KEY, ts TEXT, person TEXT,
         vector_json TEXT, valence REAL, arousal REAL,
         trigger TEXT, note TEXT)""",
]


def _now():
    return datetime.now(timezone.utc).astimezone()


def _stamp(dt):
    return dt.isoformat(timespec="seconds")


def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
        return d if d.tzinfo else d.replace(tzinfo=_now().tzinfo)
    except (ValueError, TypeError):
        return None


def _jload(raw, default):
    try:
        v = json.loads(raw or "")
        return v if isinstance(v, dict) else default
    except Exception:
        return default


def _zero():
    return {d: 0.0 for d in DIMS}


def _clamp(x):
    return max(0.0, min(1.0, float(x)))


def _ensure(conn):
    for ddl in _DDL:
        conn.execute(ddl)
    row = conn.execute("SELECT 1 FROM affect_state WHERE id=1").fetchone()
    if row is None:
        z = json.dumps(_zero(), ensure_ascii=False)
        conn.execute(
            "INSERT INTO affect_state(id,vector_json,mood_json,energy,valence,"
            "arousal,updated_at) VALUES(1,?,?,0.5,0,0,?)",
            (z, z, _stamp(_now())))
        conn.commit()
    return conn


def _factor(last, half_life_hours, now):
    d = _parse(last)
    if d is None:
        return 1.0
    hours = max(0.0, (now - d).total_seconds()) / 3600.0
    return math.exp(-hours * math.log(2) / max(0.01, float(half_life_hours)))


def parse_emotions(raw):
    """把多种输入归一成 {dim: 0~1}。支持离散情绪名、中文词、旧两维。"""
    if not raw:
        return {}
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in DIMS:
            return {s: 0.6}
        for word, dim in LEXICON.items():
            if word in raw:
                return {dim: 0.6}
        return {}
    if not isinstance(raw, dict):
        return {}
    if isinstance(raw.get("emotions"), dict):
        return parse_emotions(raw["emotions"])
    out = {}
    for k, v in raw.items():
        k = str(k).strip().lower()
        if k in DIMS:
            try:
                out[k] = max(out.get(k, 0.0), _clamp(v))
            except (TypeError, ValueError):
                continue
        elif k in LEXICON:
            out[LEXICON[k]] = max(out.get(LEXICON[k], 0.0), _clamp(v))
        elif k in ("label", "emotion"):
            out.update(parse_emotions(str(v)))
    if not out and ("valence" in raw or "arousal" in raw):
        # 旧两维兼容：映射到最接近的离散情绪，避免情绪层形同虚设
        try:
            v, a = float(raw.get("valence") or 0), float(raw.get("arousal") or 0)
        except (TypeError, ValueError):
            v, a = 0.0, 0.0
        intensity = _clamp(min(1.0, abs(v) * 0.7 + a * 0.5))
        if intensity > config.AFFECT_MIN_INTENSITY:
            if v <= -0.2 and a >= 0.5:
                dim = "anger"
            elif v <= -0.2:
                dim = "sadness"
            elif v >= 0.2:
                dim = "joy"
            else:
                dim = "surprise" if a >= 0.5 else "anticipation"
            out[dim] = intensity
    return out


def _fold(vector):
    """离散向量 → (valence, arousal, energy)，供旧召回偏置与决策复用。"""
    total = sum(vector.values())
    if total <= 1e-6:
        return 0.0, 0.0, 0.5
    v = sum(vector[d] * DIMS[d][0] for d in DIMS) / total
    a = sum(vector[d] * DIMS[d][1] for d in DIMS) / total
    energy = _clamp(0.5 + 0.35 * a - 0.20 * max(vector["sadness"],
                                                  vector["guilt"])
                    + 0.10 * vector["joy"])
    return round(v, 3), round(a, 3), round(energy, 3)


def snapshot(conn, now=None):
    """当前情感投影（**只读**：按真实时间算衰减，但不落库）。"""
    _ensure(conn)
    now = now or _now()
    r = conn.execute("SELECT * FROM affect_state WHERE id=1").fetchone()
    if r is None:
        return {"vector": _zero(), "mood": _zero(), "energy": 0.5,
                "valence": 0.0, "arousal": 0.0, "top": "", "top_label": "",
                "intensity": 0.0, "relations": {}}
    kv = _factor(r["updated_at"], config.AFFECT_HALFLIFE_HOURS, now)
    km = _factor(r["updated_at"], config.AFFECT_MOOD_HALFLIFE_HOURS, now)
    vec = {d: round(_clamp(_jload(r["vector_json"], {}).get(d, 0.0) * kv), 4)
           for d in DIMS}
    mood = {d: round(_clamp(_jload(r["mood_json"], {}).get(d, 0.0) * km), 4)
            for d in DIMS}
    v, a, energy = _fold(vec)
    top = max(vec, key=vec.get) if vec else ""
    intensity = vec.get(top, 0.0)
    if intensity < config.AFFECT_MIN_INTENSITY:
        top = ""
    rels = relation_all(conn)
    return {
        "vector": vec, "mood": mood, "energy": energy,
        "valence": v, "arousal": a,
        "top": top, "top_label": CN_LABELS.get(top, ""),
        "intensity": round(intensity, 4), "relations": rels,
    }


def salience_boost(snap):
    """情绪 → 编码显著性加成系数（0~1），交给 store.remember。"""
    return _clamp((snap or {}).get("intensity") or 0.0)


def valence_arousal(raw):
    """把任意情绪输入折成旧两维，供会话轮次日志 / emotion_state 复用。"""
    vec = _zero()
    vec.update(parse_emotions(raw))
    v, a, _ = _fold(vec)
    return v, a


def update(conn, emotions=None, *, person=None, trigger=None, note=None,
           sync_emotion=True, now=None):
    """吸收一次情绪事件：快变量更新、慢心境平滑、按人关系情感、日志留痕。"""
    _ensure(conn)
    now = now or _now()
    cur = snapshot(conn, now)
    incoming = parse_emotions(emotions)
    vec = dict(cur["vector"])
    mood = dict(cur["mood"])
    if incoming:
        inertia = config.AFFECT_VECTOR_INERTIA
        alpha = config.AFFECT_MOOD_ALPHA
        for d, val in incoming.items():
            vec[d] = _clamp(inertia * vec.get(d, 0.0) + val)
            mood[d] = _clamp((1 - alpha) * mood.get(d, 0.0) + alpha * vec[d])
    v, a, energy = _fold(vec)
    conn.execute(
        "UPDATE affect_state SET vector_json=?,mood_json=?,energy=?,valence=?,"
        "arousal=?,updated_at=? WHERE id=1",
        (json.dumps(vec, ensure_ascii=False), json.dumps(mood, ensure_ascii=False),
         energy, v, a, _stamp(now)))
    if incoming:
        conn.execute(
            "INSERT INTO affect_events(id,ts,person,vector_json,valence,arousal,"
            "trigger,note) VALUES(?,?,?,?,?,?,?,?)",
            ("af_" + uuid.uuid4().hex[:12], _stamp(now), person or "",
             json.dumps(incoming, ensure_ascii=False), v, a,
             str(trigger or "")[:120], str(note or "")[:300]))
        if person:
            _bump_relation(conn, person, v, a, now)
    conn.commit()
    if sync_emotion and incoming:
        # 召回侧：把离散情绪折回旧两维，复用既有 EMOTION_BIAS 心境一致性。
        try:
            emotion.EmotionState(conn).update(
                v, a, weight=config.EMOTION_TURN_WEIGHT)
        except Exception:
            pass
    return snapshot(conn)


def _bump_relation(conn, person, valence, arousal, now):
    row = conn.execute(
        "SELECT trust,intimacy,attachment FROM affect_relations WHERE person=?",
        (person,)).fetchone()
    t, i, at = ((row["trust"], row["intimacy"], row["attachment"])
                if row else (0.0, 0.0, 0.0))
    # 正向互动小幅累积信任与亲密度，负向只轻微磨损信任（不因一次冲突清零）。
    t = _clamp(t + (0.02 if valence >= 0 else -0.01))
    i = _clamp(i + 0.01 + 0.01 * max(0.0, valence))
    at = _clamp(at + 0.01 * arousal)
    label = "亲近" if i >= 0.6 else ("熟悉" if i >= 0.25 else "初识")
    conn.execute(
        "INSERT OR REPLACE INTO affect_relations(person,trust,intimacy,"
        "attachment,label,updated_at) VALUES(?,?,?,?,?,?)",
        (person, round(t, 4), round(i, 4), round(at, 4), label, _stamp(now)))


def relation(conn, person):
    r = conn.execute(
        "SELECT trust,intimacy,attachment,label FROM affect_relations "
        "WHERE person=?", (person,)).fetchone()
    return dict(r) if r else {"trust": 0.0, "intimacy": 0.0,
                              "attachment": 0.0, "label": "初识"}


def relation_all(conn):
    _ensure(conn)
    return {r["person"]: {"trust": r["trust"], "intimacy": r["intimacy"],
                          "attachment": r["attachment"], "label": r["label"]}
            for r in conn.execute("SELECT * FROM affect_relations")}


def expression_hints(snap):
    """情绪 → 语气 / 追问 / 回避建议。只给提示，不替 agent 决定措辞。"""
    s = snap or {}
    top, intensity = s.get("top") or "", float(s.get("intensity") or 0)
    if intensity < config.AFFECT_MIN_INTENSITY:
        return []
    hints = []
    if top in ("sadness", "fear", "guilt"):
        hints.append("当下偏%s：语气放轻，先接住情绪再谈事，别急着给建议或讲道理。"
                     % CN_LABELS[top])
    elif top in ("anger", "disgust"):
        hints.append("当下偏%s：别辩解、别纠正，先承认感受，回答短一些。"
                     % CN_LABELS[top])
    elif top in ("joy", "anticipation"):
        hints.append("当下偏%s：可以轻快一点，但别因此夸大记忆里没有的事。"
                     % CN_LABELS[top])
    elif top == "surprise":
        hints.append("当下有意外感：先确认自己理解对了，再决定要不要展开。")
    if float(s.get("energy") or 0.5) < 0.35:
        hints.append("能量偏低：短句回应，不主动抛新话题，别硬撑热闹。")
    return hints


def should_initiate(snap, idle_hours=None, has_open_topic=False):
    """是否建议主动开口：能量 + 距离 + 未闭合话题三者共同决定。"""
    s = snap or {}
    energy = float(s.get("energy") or 0)
    if energy < config.AFFECT_INITIATE_ENERGY:
        return False, "能量不足"
    if idle_hours is None or idle_hours < config.AFFECT_INITIATE_GAP_HOURS:
        return False, "刚聊过"
    if not has_open_topic:
        return False, "没有未聊完的事"
    return True, "能量够、隔了一阵、有未闭合话题"
