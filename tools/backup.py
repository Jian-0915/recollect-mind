"""数据库备份：SQLite 在线备份 + 压缩，保留最近 N 份。

用法：
    python tools/backup.py              # 备份到 data/backups/
    python tools/backup.py --keep 10    # 保留最近 10 份

备份文件：data/backups/neuromem-YYYYmmdd-HHMMSS.db.zip
"""
import argparse
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "neuromem.db"
OUT = BASE / "data" / "backups"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=7, help="保留最近几份")
    a = ap.parse_args()

    if not DB.exists():
        print("数据库不存在：%s" % DB)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tmp = OUT / ("neuromem-%s.db" % stamp)
    dst = OUT / ("neuromem-%s.db.zip" % stamp)

    # 在线备份：WAL 下也能拿到一致快照
    src = sqlite3.connect(str(DB))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    try:
        out = sqlite3.connect(str(tmp))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()

    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, tmp.name)
    tmp.unlink()

    old = sorted(OUT.glob("neuromem-*.db.zip"))
    for f in old[:-a.keep] if a.keep > 0 else []:
        f.unlink()
        print("清理旧备份：%s" % f.name)

    print("备份完成：%s（%.1f KB，保留 %d 份）"
          % (dst, dst.stat().st_size / 1024, min(len(old), a.keep)))
    return 0


if __name__ == "__main__":
    sys.exit(main())