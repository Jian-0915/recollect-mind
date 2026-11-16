"""实验库引导：让 agent 完全不写主库。

``llm.generate`` 会读 settings（含加密的 API Key）并写 usage_log，两者默认
都走 ``config.DB_PATH``（主库）。为了真实测试不污染主库，本模块在 agent
启动时把主库的配置行**只读复制**到实验库，配合 ``NEUROMEM_DB`` 让运行时
整体指向实验库。复制的只是配置，不含任何记忆。
"""
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MAIN_DB = BASE_DIR / "data" / "neuromem.db"
LAB_DB = BASE_DIR / "data" / "agent_lab.db"


def point_runtime_to(db_path):
    """在 import neuromem 之后调用：把后续所有默认连接指向实验库。"""
    p = Path(db_path).resolve()
    os.environ["NEUROMEM_DB"] = str(p)
    from neuromem import config
    config.DB_PATH = p
    return p


def is_project_lab_db(db_path):
    """只有项目 data/ 下的实验库才做 L0 同步；单测的 tmp_path 临时库不碰主库。"""
    try:
        return str(Path(db_path).resolve()).lower().startswith(
            str((BASE_DIR / "data").resolve()).lower())
    except Exception:
        return False


def sync_l0_from_main(db_path, src_path=None):
    """把主库的 L0（宪法 + 人设卡）整份同步到实验库。

    后台管理改的是主库，而陪伴 agent 实验台默认用独立库；不同步就会出现
    「后台改了人设、agent 还是默认人格」。每次启动同步一次，只覆盖 L0 四张表，
    实验库里的记忆、会话、采纳、情感数据一律不动。主库全程只读。

    注意：主库是 L0 的唯一人工设定源，实验库里用 CLI 单独设的人设会在下次
    启动时被主库覆盖 —— 这正是「统一在后台管理」的语义。
    """
    from neuromem import db, identity, persona_card

    src_path = Path(src_path or MAIN_DB)
    dst = Path(db_path)
    if not src_path.exists() or dst.resolve() == src_path.resolve():
        return False, "主库不存在或目标即主库，跳过 L0 同步"

    c = db.connect(str(dst))
    try:
        identity._ensure(c)             # 幂等建表（含默认行），随后被主库内容覆盖
        persona_card._ensure(c)
        src = sqlite3.connect("file:%s?mode=ro" % src_path.as_posix(),
                             uri=True, timeout=15)
        try:
            names = {r[0] for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            copied = {}
            for t in ("identity", "identity_history",
                      "persona_card", "persona_card_history"):
                if t not in names:
                    continue
                cur = src.execute("SELECT * FROM %s" % t)
                cols = [d[0] for d in cur.description]
                rows = [tuple(r) for r in cur.fetchall()]
                c.execute("DELETE FROM %s" % t)
                if rows:
                    ph = ",".join("?" * len(cols))
                    c.executemany(
                        "INSERT INTO %s (%s) VALUES (%s)" % (t, ",".join(cols), ph),
                        rows)
                copied[t] = len(rows)
            c.commit()
        finally:
            src.close()
        return True, copied
    finally:
        c.close()


def ensure_lab_settings(db_path):
    """实验库缺配置时，从主库只读复制 settings 行；主库全程只读。

    返回 (copied, note)。新库为空时复制，之后不再覆盖用户在实验库里的改配置。
    """
    from neuromem import db

    src_path = Path(MAIN_DB)
    if not src_path.exists():
        return False, "主库不存在，使用默认配置"
    c = db.connect(str(db_path))
    try:
        # settings 表由 settings 模块惰性自建，不在 db.SCHEMA 里，必须先建表
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
        row = c.execute("SELECT 1 FROM settings WHERE key='models'").fetchone()
        if row:
            return False, "实验库已有配置"
        src = sqlite3.connect("file:%s?mode=ro" % src_path.as_posix(),
                               uri=True, timeout=15)
        try:
            rows = src.execute("SELECT key,value FROM settings").fetchall()
        finally:
            src.close()
        c.executemany("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                      [(r[0], r[1]) for r in rows])
        c.commit()
        return True, "已从主库只读复制 %d 行配置" % len(rows)
    finally:
        c.close()
