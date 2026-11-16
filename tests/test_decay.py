"""遗忘曲线参数验证：不该几轮就归档，也不能永不归档。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import config, db, forgetting  # noqa: E402

DB = r"D:\neuro-memory\data\decay_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)
for eid, sal in (("e_hi", 0.8), ("e_low", 0.1)):
    conn.execute("INSERT INTO events(id,activation,salience,gist,archived) VALUES(?,?,?,?,0)",
                 (eid, 1.0, sal, "测试事件 %s" % eid))
conn.commit()

print("参数: factor=%s bonus=%s threshold=%s" %
      (config.DECAY_FACTOR, config.DECAY_SALIENCE_BONUS, config.DECAY_THRESHOLD))

for _ in range(10):
    forgetting.decay(conn)
rows = {r["id"]: (r["activation"], r["archived"]) for r in
        conn.execute("SELECT id,activation,archived FROM events")}
print("10 轮后（约 100 分钟）:", rows)
assert rows["e_hi"][1] == 0 and rows["e_low"][1] == 0, "10 轮就归档太激进"
assert rows["e_hi"][0] > 0.85, "10 轮后激活值应仍较高"

for _ in range(290):
    forgetting.decay(conn)
rows = {r["id"]: (r["activation"], r["archived"]) for r in
        conn.execute("SELECT id,activation,archived FROM events")}
print("300 轮后（约 50 小时）:", rows)
assert rows["e_hi"][1] == 1, "高显著事件最终也应归档（不免疫）"
assert rows["e_low"][1] == 1, "低显著事件应已归档"

conn.execute("UPDATE events SET archived=0, activation=1.0 WHERE id='e_hi'")
conn.commit()
for _ in range(10):
    forgetting.decay(conn)
assert conn.execute("SELECT archived FROM events WHERE id='e_hi'").fetchone()[0] == 0
print("恢复后 10 轮仍活跃: OK")

conn.close()
os.remove(DB)
print("PASS")