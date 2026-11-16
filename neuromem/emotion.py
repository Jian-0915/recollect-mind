"""情感状态机：持续的情绪状态（效价/唤醒），随交互平滑更新、随时间衰减。

2026-11-14：decay 改为按**真实经过的时间**指数回落，而不是每轮代谢
固定 ×0.8。旧版下情绪半衰期取决于服务跑了多少轮（10 分钟/轮时约 30 分钟，
服务停摆则永不回落），新版与进程是否在线无关。
"""
import math
from datetime import datetime, timezone

from . import config


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def _label(v, a):
    if abs(v) < 0.2 and a < 0.3:
        return "平静"
    if v >= 0.2:
        return "兴奋" if a >= 0.5 else "愉快"
    if v <= -0.2:
        return "愤怒/焦虑" if a >= 0.5 else "低落"
    return "中性"


class EmotionState:
    def __init__(self, conn):
        self.conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS emotion_state("
            "id INTEGER PRIMARY KEY CHECK(id=1), valence REAL DEFAULT 0, "
            "arousal REAL DEFAULT 0, label TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO emotion_state(id,valence,arousal,label) VALUES(1,0,0,'平静')"
        )
        conn.commit()

    def get(self):
        r = self.conn.execute("SELECT * FROM emotion_state WHERE id=1").fetchone()
        return {"valence": r["valence"], "arousal": r["arousal"], "label": r["label"]}

    def update(self, valence, arousal, weight=0.3):
        """平滑吸收一次情绪（当前 70% + 新 30%）。"""
        cur = self.get()
        v = (1 - weight) * cur["valence"] + weight * float(valence)
        a = (1 - weight) * cur["arousal"] + weight * float(arousal)
        v = max(-1.0, min(1.0, v))
        a = max(0.0, min(1.0, a))
        self.conn.execute(
            "UPDATE emotion_state SET valence=?,arousal=?,label=?,updated_at=? WHERE id=1",
            (round(v, 3), round(a, 3), _label(v, a), _now()),
        )
        self.conn.commit()
        return self.get()

    def decay(self, rate=None, now=None, half_life_hours=None):
        """情绪向中性回落。

        ``rate`` 是旧版「每轮 ×(1-rate)」语义，仅为兼容保留；默认 None 时
        按 ``updated_at`` 到现在的真实小时数做指数衰减。
        """
        cur = self.get()
        now = now or datetime.now(timezone.utc).astimezone()
        if rate is not None:
            k = max(0.0, 1.0 - float(rate))
        else:
            row = self.conn.execute(
                "SELECT updated_at FROM emotion_state WHERE id=1").fetchone()
            last = _parse(row["updated_at"]) if row else None
            if last is None:
                k = 1.0                      # 首次：只补时间戳，这一轮不衰减
            else:
                hours = max(0.0, (now - last).total_seconds()) / 3600.0
                hl = float(half_life_hours or config.DECAY_EMOTION_HALFLIFE_HOURS)
                k = math.exp(-hours * math.log(2) / max(0.01, hl))
        v = float(cur["valence"] or 0.0) * k
        a = float(cur["arousal"] or 0.0) * k
        self.conn.execute(
            "UPDATE emotion_state SET valence=?,arousal=?,label=?,updated_at=? WHERE id=1",
            (round(v, 3), round(a, 3), _label(v, a), now.isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return self.get()
