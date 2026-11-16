import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
c = sqlite3.connect(r"D:\neuro-memory\data\neuromem.db")
c.row_factory = sqlite3.Row

print("== personas ==")
for r in c.execute("SELECT id,canonical_name,aliases_json FROM personas"):
    print(dict(r))

print("\n== events 含 小明/小红 ==")
for r in c.execute("SELECT id,gist,occurred_at FROM events WHERE gist LIKE '%小明%' OR gist LIKE '%小红%' ORDER BY created_at"):
    print(r["id"], "|", r["gist"], "|", r["occurred_at"])

print("\n== relations 行数 ==", c.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
for r in c.execute("SELECT a,b,rel_type,strength,evidence FROM relations"):
    print(dict(r))

print("\n== edges 按类型 ==")
for r in c.execute("SELECT type,COUNT(*) n FROM edges GROUP BY type"):
    print(dict(r))

print("\n== 事件-人物绑定（edges.involves）==")
for r in c.execute("SELECT p.canonical_name, e.id, e.gist FROM edges ed "
                   "JOIN personas p ON ed.target=p.id "
                   "LEFT JOIN events e ON ed.source=e.id "
                   "WHERE ed.type='involves' ORDER BY e.created_at"):
    print(r["canonical_name"], "<-", r["id"], "|", r["gist"])