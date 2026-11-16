"""命令行入口。"""
import sys

from . import config, consolidator, db, evolve, forgetting
from .answer import answer as generate_answer
from .recall import recall as recall_mem
from .store import remember as remember_text


def _print_recall(items):
    if not items:
        print("（没有召回任何记忆）")
        return
    for i, it in enumerate(items, 1):
        if it.get("type") == "concept":
            print("[%d] 【概念 L%s】 %s  (score=%.3f)" % (i, it.get("level", 1), it["gist"], it["score"]))
            print()
            continue
        aff = it.get("affect", {}) or {}
        print(f"[{i}] {it['kind']:11s} score={it['score']:.3f} vec={it['vec']:.3f}")
        print(f"    {it['gist']}")
        meta = []
        if it.get("time_text"):
            meta.append("时间:" + it["time_text"])
        if it.get("place"):
            meta.append("地点:" + it["place"])
        if aff:
            meta.append("情感:%s(%+.1f)" % (aff.get("label", ""), aff.get("valence", 0)))
        if meta:
            print("    " + " | ".join(meta))
        if it.get("fragments"):
            print("    碎片: " + "、".join(it["fragments"]))
        print()


def cmd_remember(args):
    text = " ".join(args).strip()
    if not text:
        print("用法: neuromem remember <文本>")
        return
    conn = db.connect()
    ids = remember_text(conn, text)
    total = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    print("已记忆 %d 条事件（库中共 %d 条）" % (len(ids), total))
    for i in ids:
        print("  -", i)


def cmd_recall(args):
    q = " ".join(args).strip()
    if not q:
        print("用法: neuromem recall <查询>")
        return
    _print_recall(recall_mem(db.connect(), q))


def cmd_ask(args):
    q = " ".join(args).strip()
    if not q:
        print("用法: neuromem ask <问题>")
        return
    conn = db.connect()
    items = recall_mem(conn, q)
    print("【召回记忆】")
    if items:
        for i, it in enumerate(items, 1):
            tag = ("概念L%s" % it.get("level", 1)) if it.get("type") == "concept" else it["kind"]
            print("  %d. [%s] %.3f  %s" % (i, tag, it["score"], it["gist"]))
    else:
        print("  （无）")
    print("\n【回答】")
    print(generate_answer(q, items))


def cmd_chat(args):
    """交互式 agent 对话：检索 -> 回答 -> 写入记忆。"""
    conn = db.connect()
    print("NeuroMem 对话  (/exit 退出  /evolve 代谢  /memory 统计)")
    while True:
        try:
            user = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in ("/exit", "/quit"):
            break
        if user == "/evolve":
            r = evolve.tick(conn)
            print("[代谢] 新概念%d 升层%d 归档%d"
                  % (len(r["new_concepts"]), len(r["abstracted"]), r["decay"]["newly_archived"]))
            continue
        if user == "/memory":
            for t in ["events", "concepts", "personas"]:
                n = conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
                print("  %-9s: %d" % (t, n))
            continue

        items = recall_mem(conn, user)
        ans = generate_answer(user, items, user_name=config.COUNTERPART_NAME)
        print("Agent>", ans)
        if items:
            print("   (用到 %d 条记忆)" % len(items))
        if not user.rstrip().endswith(("?", "？")):
            try:
                remember_text(conn, user, speaker=config.COUNTERPART_NAME)
            except Exception as e:
                print("[记忆失败]", e)
    conn.close()


def cmd_evolve(args):
    conn = db.connect()
    r = evolve.tick(conn)
    print("== 自进化 · 一轮代谢 ==")
    print("新巩固概念: %d" % len(r["new_concepts"]))
    for c in r["new_concepts"]:
        print("  L%d [%d条经历] %s" % (c["level"], c["n"], c["text"]))
    print("概念再抽象: %d" % len(r["abstracted"]))
    for c in r["abstracted"]:
        print("  L%d [%d条概念] %s" % (c["level"], c["n"], c["text"]))
    print("遗忘: %d 条事件，新归档 %d 条" % (r["decay"]["total"], r["decay"]["newly_archived"]))
    s = r["snapshot"]
    print("成长快照: 事件%d(活跃%d/归档%d) 概念%d 最高层L%d"
          % (s["events"], s["active"], s["archived"], s["concepts"], s["max_level"]))


def cmd_growth(args):
    conn = db.connect()
    rows = conn.execute("SELECT * FROM growth_log ORDER BY id").fetchall()
    print("成长轨迹（%d 个快照）：" % len(rows))
    for r in rows:
        print("  %s | 事件%d(活%d/档%d) | 概念%d | 最高层L%d"
              % (r["ts"], r["events"], r["active_events"], r["archived"], r["concepts"], r["max_level"]))


def cmd_consolidate(args):
    conn = db.connect()
    created = consolidator.run(conn)
    created += consolidator.run_concepts(conn)
    if not created:
        print("没有可巩固的内容。")
        return
    for c in created:
        print("  L%d [%d] %s" % (c["level"], c["n"], c["text"]))


def cmd_decay(args):
    r = forgetting.decay(db.connect())
    print("衰减 %d 条事件，新归档 %d 条。" % (r["total"], r["newly_archived"]))


def cmd_concepts(args):
    conn = db.connect()
    rows = conn.execute(
        "SELECT c.id,c.text,c.abstraction_level,COUNT(cl.event_id) n "
        "FROM concepts c LEFT JOIN concept_links cl ON c.id=cl.concept_id "
        "GROUP BY c.id ORDER BY c.abstraction_level, c.created_at DESC"
    ).fetchall()
    print("共 %d 条概念：" % len(rows))
    for r in rows:
        print("  L%s  %s  [%d]" % (r["abstraction_level"], r["text"], r["n"]))


def cmd_list(args):
    conn = db.connect()
    rows = conn.execute(
        "SELECT id,gist,salience,activation,archived FROM events ORDER BY created_at DESC"
    ).fetchall()
    print("共 %d 条事件：" % len(rows))
    for r in rows:
        flag = " [已归档]" if r["archived"] else ""
        print("  %s  sal=%.2f act=%.2f%s  %s"
              % (r["id"], r["salience"] or 0, r["activation"] or 0, flag, r["gist"]))


def cmd_stats(args):
    conn = db.connect()
    for t in ["events", "fragments", "bindings", "personas", "edges", "concepts", "concept_links", "growth_log"]:
        n = conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
        print("  %-13s: %d" % (t, n))


def cmd_personas(args):
    conn = db.connect()
    for r in conn.execute("SELECT id,canonical_name,aliases_json FROM personas"):
        print("  %s  %s  aliases=%s" % (r["id"], r["canonical_name"], r["aliases_json"]))


def _take_db(args):
    """从子命令参数里摘出 --db，其余按位置参数返回。"""
    rest, dbpath = [], None
    i = 0
    while i < len(args):
        if args[i] == "--db" and i + 1 < len(args):
            dbpath = args[i + 1]
            i += 2
            continue
        rest.append(args[i])
        i += 1
    return rest, dbpath


def cmd_persona(args):
    """人设卡备用命令行入口。正式设置请走后台「人设」页（同一套 API / 同一张表）。"""
    import json
    from pathlib import Path

    from . import persona_card

    rest, dbpath = _take_db(args)
    usage = ("用法: neuromem persona <show|template|apply|preview|verify|history|rollback> "
             "[参数] [--db 库路径]\n"
             "  推荐后台：http://127.0.0.1:8000 → 左侧「人设」")
    if not rest:
        print(usage)
        return
    action, rest = rest[0], rest[1:]
    c = db.connect(dbpath)
    try:
        if action == "show":
            cur = persona_card.get(c) or {}
            cur["verify"] = persona_card.verify(c)[1]
            print(json.dumps(cur, ensure_ascii=False, indent=2))
        elif action == "template":
            ex = "--blank" not in rest
            print(json.dumps(persona_card.template(example=ex),
                             ensure_ascii=False, indent=2))
        elif action == "apply":
            if not rest:
                print("用法: neuromem persona apply <persona.json> [--db 库路径]")
                return
            card = json.loads(Path(rest[0]).read_text(encoding="utf-8"))
            r = persona_card.apply(c, card, actor="cli", reason="CLI 人工设置")
            print(json.dumps(r, ensure_ascii=False))
        elif action == "preview":
            if not rest:
                print("用法: neuromem persona preview <persona.json> [--db 库路径]")
                return
            card = json.loads(Path(rest[0]).read_text(encoding="utf-8"))
            r = persona_card.preview(c, card)
            print(json.dumps({"ok": r["ok"], "errors": r["errors"]},
                             ensure_ascii=False))
            print(r["render"] or "（校验未通过 / 名字为空时不注入）")
        elif action == "verify":
            print(persona_card.verify(c)[1])
        elif action == "render":
            print(persona_card.render(c) or "（未设置人设，生成层沿用默认人格）")
        elif action == "history":
            print(json.dumps(persona_card.history(c, limit=20),
                             ensure_ascii=False, indent=2))
        elif action == "rollback":
            if not rest or not rest[0].isdigit():
                print("用法: neuromem persona rollback <版本> [--db 库路径]")
                return
            print(json.dumps(persona_card.rollback(c, int(rest[0]), actor="cli"),
                             ensure_ascii=False))
        else:
            print(usage)
    finally:
        c.close()


def cmd_init(args):
    """初始化库与各子系统状态表（幂等，不清除已有记忆）。"""
    from . import emotion, selfnarrative

    conn = db.connect()
    st = emotion.EmotionState(conn)
    selfnarrative.get(conn)
    print("库文件: %s" % config.DB_PATH)
    print("状态表: emotion_state / self_narrative 就绪")
    print("当前情绪: %s" % st.get())
    for t in ["events", "personas", "concepts", "relations"]:
        n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        print("  %-10s: %d" % (t, n))
    conn.close()
    print("初始化完成。查看阶段: python -m neuromem stats / 前端 http://127.0.0.1:8000")


def cmd_serve(args):
    import uvicorn
    port = 8000
    if args and args[0].isdigit():
        port = int(args[0])
    print("NeuroMem Viewer: http://127.0.0.1:%d" % port)
    uvicorn.run("neuromem.api:app", host="127.0.0.1", port=port, log_level="info")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("用法: neuromem <init|chat|remember|recall|ask|evolve|growth|consolidate|decay|concepts|list|stats|personas|persona|serve> ...")
        return
    cmd, args = argv[0], argv[1:]
    table = {
        "chat": cmd_chat,
        "remember": cmd_remember,
        "recall": cmd_recall,
        "ask": cmd_ask,
        "evolve": cmd_evolve,
        "growth": cmd_growth,
        "consolidate": cmd_consolidate,
        "decay": cmd_decay,
        "concepts": cmd_concepts,
        "list": cmd_list,
        "stats": cmd_stats,
        "personas": cmd_personas,
        "persona": cmd_persona,
        "init": cmd_init,
        "serve": cmd_serve,
    }
    if cmd not in table:
        print("未知命令: %s" % cmd)
        return
    table[cmd](args)