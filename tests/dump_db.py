"""导出 SQLite 数据库的全部表内容为可读文本（用于查看原始存储）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import config, db

TABLES = ["events", "fragments", "bindings", "personas", "edges", "concepts", "concept_links"]


def fmt(v, width=100):
    if isinstance(v, (bytes, bytearray)):
        return "<blob %dB>" % len(v)
    s = "" if v is None else str(v)
    s = s.replace("\n", "\\n")
    if len(s) > width:
        s = s[:width] + "…"
    return s


def main():
    conn = db.connect()
    print("DATABASE FILE:", config.DB_PATH)
    out_path = Path(config.DATA_DIR) / "dump.txt"
    lines = []
    lines.append("DATABASE FILE: %s" % config.DB_PATH)
    for t in TABLES:
        rows = conn.execute("SELECT * FROM %s" % t).fetchall()
        header = "\n===== %s (%d rows) =====" % (t, len(rows))
        print(header)
        lines.append(header)
        if rows:
            cols = list(rows[0].keys())
            head = " | ".join(cols)
            print(head)
            lines.append(head)
            for r in rows:
                line = " | ".join(fmt(r[c]) for c in cols)
                print(line)
                lines.append(line)
        else:
            print("(empty)")
            lines.append("(empty)")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n[已导出到] %s" % out_path)


if __name__ == "__main__":
    main()