"""惊讶加权巩固：概念置信度反映簇内平均惊讶，高惊讶事件先成为种子。"""
from neuromem import consolidator, db, embed, persona


def _seed(conn, items):
    p = persona.ensure(conn, "小明")
    for eid, gist, sur in items:
        conn.execute(
            "INSERT INTO events(id,created_at,gist,embedding,salience,activation,archived,surprise) "
            "VALUES(?,?,?,?,0.5,1.0,0,?)",
            (eid, "2026-11-12T00:00:00+08:00", gist, db.pack([1.0, 0.0, 0.0, 0.0]), sur))
        conn.execute("INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,1.0)",
                     (eid, p["id"], "involves"))
    conn.commit()
    return p


def test_confidence_follows_cluster_surprise(memdb, conn, monkeypatch):
    _seed(conn, [
        ("e1", "小明被狗追了，很害怕", 0.9),
        ("e2", "小明又被狗追了，也很害怕", 0.5),
        ("e3", "小明看到狗就躲开", 0.1),
    ])
    monkeypatch.setattr(embed, "embed", lambda t, *a, **k: [1.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(consolidator.llm, "generate",
                        lambda *a, **k: "小明对狗始终保持警惕")

    out = consolidator.run(conn)
    assert len(out) == 1 and out[0]["n"] == 3
    conf = conn.execute("SELECT confidence FROM concepts WHERE id=?",
                        (out[0]["id"],)).fetchone()[0]
    expect = 0.55 + 0.4 * (0.9 + 0.5 + 0.1) / 3
    assert abs(conf - round(expect, 3)) < 1e-6


def test_high_surprise_event_seeds_the_cluster(memdb, conn, monkeypatch):
    _seed(conn, [
        ("low", "小明的一次普通经历", 0.05),
        ("high", "小明遇到一件怪事", 0.95),
        ("mid", "小明又一次经历", 0.4),
    ])
    monkeypatch.setattr(embed, "embed", lambda t, *a, **k: [1.0, 0.0, 0.0, 0.0])

    prompts = []
    monkeypatch.setattr(consolidator.llm, "generate",
                        lambda prompt, *a, **k: prompts.append(prompt) or "小明总能遇到怪事")
    consolidator.run(conn)

    assert prompts, "应触发一次概念归纳"
    body = prompts[0].split("经历：", 1)[1].strip().splitlines()
    first = body[0].lstrip("- ").strip()
    assert "怪事" in first, "簇种子应是惊讶度最高的事件，实际首行：%s" % first