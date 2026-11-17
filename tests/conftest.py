"""pytest 公共夹具：临时库 + 端点绑定临时库。

约定
----
- 每个用例一个独立 SQLite 文件（tmp_path 下，WAL 也在里面），用完自动清理
- `db.connect` 的默认路径被重定向到临时库，`api.conn` 同步指向它
- `config.DB_PATH` **全局**指向临时库（autouse），所以 `usage` 这种自己
  `sqlite3.connect(config.DB_PATH)` 的模块也不会漏到主库
- 因此**任何用例都不会碰** `data/neuromem.db`
- 需要真实服务 / 模型的脚本留在 `tests/*.py`，标记为 integration，不参与默认收集
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neuromem import api, config, db, usage  # noqa: E402


def _drop_usage_conn():
    """丢掉 usage 的模块级长连接，让它下次按新的 DB_PATH 重连。"""
    with usage._lock:
        if usage._conn is not None:
            try:
                usage._conn.close()
            except Exception:
                pass
        usage._conn = None


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """每个用例都把库指到 tmp_path —— 这条是**兜底**，不依赖用例主动申请夹具。

    只重定向 ``db.connect`` 不够：``usage`` 模块自己维护一条
    ``sqlite3.connect(config.DB_PATH)`` 的长连接，完全不经过 db.connect。
    2026-11-16 的实际后果是：跑一遍单测，`model='m'`、`latency_ms=0`、
    `tokens=10+8` 这些假调用（test 里的 FakeResp 载荷）被写进了主库
    usage_log，管理页「最近调用」凭空多出几行。
    """
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "neuromem.db")
    _drop_usage_conn()
    yield
    _drop_usage_conn()


@pytest.fixture()
def memdb(tmp_path, monkeypatch):
    """临时库路径；db.connect() 无参调用也会落到这里。"""
    path = str(tmp_path / "neuromem.db")
    db.connect(path).close()

    real_connect = db.connect

    def _connect(p=None, *args, **kwargs):
        return real_connect(path if p is None else p, *args, **kwargs)

    monkeypatch.setattr(db, "connect", _connect)
    monkeypatch.setattr(api, "conn", lambda: _connect())
    return path


@pytest.fixture()
def conn(memdb):
    """给用例自己断言用的连接（不经过 api）。"""
    c = db.connect()
    yield c
    c.close()