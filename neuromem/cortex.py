"""Cortex 扩展总线（v13）：给大脑挂新能力，而不改主干。

``recall.py`` 已经 20K+ 字符、偏置层层叠加，本质就是缺一条扩展总线的代价：
每加一个机制都要往召回主干里塞判断。这里定一个极薄的模块接口，插件各写
各的表、各管各的生命周期，**不改 L0、不碰 recall 主干、不阻断主链路**。

生命周期：
    on_turn_start(brain, pack, affect_now) → contribute(brain, pack) -> note
    on_commit(brain, result, ...)          → tick(brain, now)

开关：config.CORTEX_ENABLED / config.CORTEX_MODULES（或环境变量
NEUROMEM_MODULES=proactive,reflection）。任何插件抛异常都只记一笔，不影响
Brain.turn / commit 返回。
"""
import uuid
from datetime import datetime, timezone

from . import affect, companion, config


def _now():
    return datetime.now(timezone.utc).astimezone()


def _stamp(dt=None):
    return (dt or _now()).isoformat(timespec="seconds")


class BrainModule:
    name = "base"
    description = ""

    def __init__(self, brain):
        self.brain = brain

    @property
    def conn(self):
        return self.brain.conn

    def on_turn_start(self, pack, *, affect_now=None):
        return None

    def contribute(self, pack):
        return None

    def on_commit(self, result, *, query="", reply="", used_ids=None):
        return None

    def tick(self, now):
        return None


REGISTRY = {}


def register(cls):
    """类装饰器：注册一个扩展模块。"""
    REGISTRY[cls.name] = cls
    return cls


@register
class ProactiveModule(BrainModule):
    """主动发起：能量够 + 隔了一阵 + 有未闭合话题，才提议开口。

    只产出「建议」，是否真的开口仍由 agent 决定；清库时这张表会被动态枚举清掉。
    """
    name = "proactive"
    description = "主动发起候选（不替 agent 决定说不说）"

    def _ensure(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS proactive_suggestions("
            "id TEXT PRIMARY KEY, ts TEXT, topic TEXT, gist TEXT, "
            "reason TEXT, consumed INTEGER DEFAULT 0)")
        self.conn.commit()

    def contribute(self, pack):
        self._ensure()
        init = pack.get("initiate") or {}
        if not init.get("suggested"):
            return None
        row = self.conn.execute(
            "SELECT topic,reason FROM proactive_suggestions "
            "WHERE consumed=0 ORDER BY ts DESC LIMIT 1").fetchone()
        if not row:
            return None
        return ("可以主动提起「%s」（%s）；不想打扰就直接忽略，不要为凑话头编造记忆。"
                % (str(row["topic"] or "")[:40], str(row["reason"] or "")[:40]))

    def on_commit(self, result, *, query="", reply="", used_ids=None):
        if not reply:
            return None
        self._ensure()
        self.conn.execute(
            "UPDATE proactive_suggestions SET consumed=1 "
            "WHERE consumed=0 AND topic!='' AND ? LIKE '%'||topic||'%'",
            (reply[:300],))
        self.conn.commit()
        return None

    def tick(self, now):
        self._ensure()
        st = companion.state(self.conn)
        snap = affect.snapshot(self.conn)
        last = st.get("last_turn_at")
        idle = None
        if last:
            try:
                d = datetime.fromisoformat(last)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=now.tzinfo)
                idle = max(0.0, (now - d).total_seconds() / 3600.0)
            except (ValueError, TypeError):
                idle = None
        ok, why = affect.should_initiate(
            snap, idle, has_open_topic=bool(st.get("open_topics")))
        if not ok:
            return {"created": 0, "reason": why}
        topic = str((st.get("open_topics") or [""])[-1])[:60]
        self.conn.execute(
            "INSERT INTO proactive_suggestions(id,ts,topic,gist,reason,consumed) "
            "VALUES(?,?,?,?,?,0)",
            ("pro_" + uuid.uuid4().hex[:10], _stamp(now), topic, "", why))
        self.conn.commit()
        return {"created": 1, "reason": why, "topic": topic}


@register
class ReflectionModule(BrainModule):
    """反思日记：记录「没依据可用 / 回复塌缩」，下轮给 agent 一句纠偏提示。"""
    name = "reflection"
    description = "离线反思：空依据与回复塌缩提示"

    def _ensure(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS reflection_log("
            "id TEXT PRIMARY KEY, ts TEXT, query TEXT, reply TEXT, "
            "used_count INTEGER, outcome TEXT, note TEXT)")
        self.conn.commit()

    def contribute(self, pack):
        self._ensure()
        rows = self.conn.execute(
            "SELECT note FROM reflection_log ORDER BY ts DESC LIMIT 2").fetchall()
        notes = [str(r["note"])[:80] for r in rows if r["note"]]
        return "；".join(notes[:2]) if notes else None

    def on_commit(self, result, *, query="", reply="", used_ids=None):
        self._ensure()
        n = len(used_ids or [])
        note = ""
        if n == 0:
            note = "上一轮没有可用依据，别为显得记得住而硬凑内容。"
        else:
            prev = self.conn.execute(
                "SELECT reply FROM reflection_log ORDER BY ts DESC LIMIT 1").fetchone()
            if prev and reply and prev["reply"] and \
                    len(set(reply) & set(prev["reply"])) >= max(6, len(reply) // 2):
                note = "最近两轮说法偏重复，换个角度说，别复读同一句。"
        if not note:
            return None
        self.conn.execute(
            "INSERT INTO reflection_log(id,ts,query,reply,used_count,outcome,note) "
            "VALUES(?,?,?,?,?,?,?)",
            ("rf_" + uuid.uuid4().hex[:10], _stamp(), (query or "")[:200],
             (reply or "")[:300], n, str(result.get("context_text_ok")), note))
        self.conn.commit()
        return note

    def tick(self, now):
        self._ensure()
        cnt = self.conn.execute(
            "SELECT COUNT(*) FROM reflection_log").fetchone()[0]
        return {"entries": cnt}


class Cortex:
    """按配置装配插件，四个生命周期钩子全部容错。"""

    def __init__(self, brain, modules=None):
        self.brain = brain
        names = list(modules if modules is not None else config.CORTEX_MODULES)
        self.active, self.missing = [], []
        if not config.CORTEX_ENABLED:
            self.missing = list(names)
            return
        for n in names:
            cls = REGISTRY.get(n)
            if cls is None:
                self.missing.append(n)
                continue
            try:
                self.active.append(cls(brain))
            except Exception:
                self.missing.append(n)

    def _safe(self, hook, *a, **kw):
        for m in self.active:
            try:
                getattr(m, hook)(*a, **kw)
            except Exception:
                pass      # 插件故障不能拖垮主链路

    def on_turn_start(self, pack, *, affect_now=None):
        self._safe("on_turn_start", pack, affect_now=affect_now)

    def contribute(self, pack):
        out = {}
        for m in self.active:
            try:
                note = m.contribute(pack)
            except Exception:
                note = None
            if note:
                out[m.name] = str(note)[:300]
        return out

    def on_commit(self, result, *, query="", reply="", used_ids=None):
        self._safe("on_commit", result, query=query, reply=reply,
                   used_ids=used_ids)

    def tick(self, now):
        out = {}
        for m in self.active:
            try:
                out[m.name] = m.tick(now)
            except Exception as e:
                out[m.name] = {"error": str(e)[:100]}
        return out


def available():
    return {n: cls.description for n, cls in REGISTRY.items()}
