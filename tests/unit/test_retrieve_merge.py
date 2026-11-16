"""retrieval 接口与概念合并。"""
from datetime import datetime

from neuromem import api, db, persona, recall


def _mk(conn, eid, gist, vec):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO events(id,created_at,gist,embedding,salience,activation,archived,surprise) "
        "VALUES(?,?,?,?,0.5,1.0,0,0.5)", (eid, now, gist, db.pack(vec)))


def test_retrieve_returns_scores_without_llm(memdb, conn, monkeypatch):
    _mk(conn, "e1", "阿哲请我吃饭说他升职了", [1.0, 0.0, 0.0, 0.0])
    _mk(conn, "e2", "妈妈打电话说橘子熟了", [0.0, 1.0, 0.0, 0.0])
    p = persona.ensure(conn, "阿哲")
    conn.execute("INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,1.0)",
                 ("e1", p["id"], "involves"))
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
                 "VALUES('r1',?,?, '朋友',0.9,'','','x')", (p["id"], "p_x"))
    conn.commit()
    monkeypatch.setattr(recall.embed, "embed", lambda q, *a, **k: [1.0, 0.0, 0.0, 0.0])

    r = api.retrieve(api.RetrieveIn(query="阿哲最近怎么样", top_k=5))
    assert r["ok"] and r["events"], r
    top = r["events"][0]
    assert top["id"] == "e1"
    assert top["scores"]["vec"] is not None
    assert r["timings"]["recall_ms"] >= 0
    assert "summary" not in r, "retrieve 不应生成回答"


def test_merge_concepts_moves_links(memdb, conn):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    vec = db.pack([0.0, 0.0, 0.0, 1.0])
    for cid, text in (("c1", "妈妈关心用户饮食"), ("c2", "妈妈总问用户吃没吃好")):
        conn.execute("INSERT INTO concepts(id,text,abstraction_level,confidence,created_at,embedding) "
                     "VALUES(?,?,1,0.6,?,?)", (cid, text, now, vec))
    _mk(conn, "e1", "妈妈问我吃饭没", [0.0, 0.0, 0.0, 1.0])
    _mk(conn, "e2", "妈妈又问我吃没吃好", [0.0, 0.0, 0.0, 1.0])
    conn.execute("INSERT INTO concept_links(concept_id,event_id,type) VALUES('c1','e1','derived_from')")
    conn.execute("INSERT INTO concept_links(concept_id,event_id,type) VALUES('c2','e2','derived_from')")
    conn.commit()

    r = api.merge_concepts(api.ConceptMergeIn(keep="c1", drop="c2"))
    assert r["ok"] and r["events"] == 2
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM concept_links WHERE concept_id='c1'").fetchone()[0] == 2

    bad = api.merge_concepts(api.ConceptMergeIn(keep="c1", drop="c1"))
    assert bad["ok"] is False