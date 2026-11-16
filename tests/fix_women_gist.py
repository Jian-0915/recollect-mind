"""修正正式库中被误替换的 gist（"我们"→"用户们"）。"""
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
c = sqlite3.connect(r"D:\neuro-memory\data\neuromem.db")
cur = c.execute("UPDATE events SET gist=? WHERE gist LIKE '%用户们%'",
                ("用户和小明是好朋友，我们认识很久了",))
print("fixed rows:", cur.rowcount)
for r in c.execute("SELECT id,gist FROM events WHERE gist LIKE '%好朋友%'"):
    print(r[0], "|", r[1])
c.commit()
c.close()