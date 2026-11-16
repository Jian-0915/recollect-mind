"""pytest 公共夹具：临时库 + 端点绑定临时库。

约定
----
- 每个用例一个独立 SQLite 文件（tmp_path 下，WAL 也在里面），用完自动清理
- `db.connect` 的默认路径被重定向到临时库，`api.conn` 同步指向它
- 因此**任何用例都不会碰** `data/neuromem.db`
- 需要真实服务 / 模型的脚本留在 `tests/*.py`，标记为 integration，不参与默认收集
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neuromem import api, db  # noqa: E402


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