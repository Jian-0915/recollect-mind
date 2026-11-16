"""存量库回填（v19 起）：给历史数据补上 kind / 别名归一 / subject / bond。

三件事都不改 gist（gist 是原文证据），只修结构化字段：

1. ``personas.kind`` —— 把「清明」「金毛」「我们一大家子」「医生」标出类型，
   让画像与召回的人物硬过滤只认真人。
2. **别名归一** —— 「我妈」「妈妈」「母亲」并成一张人物卡，画像/关系/involves
   边不再分裂成三份。
3. ``events.subject`` —— 用写入路径同一套 ``_infer_subject`` 重算，
   把「我外甥上幼儿园」这类第三方事实从 counterpart 里分出来。
4. ``personas.bond``（v20）—— 把「刘慈欣」这类只在作品语境里出现的人
   标成 mentioned，不让他们触发召回的人物硬过滤、也不进社交图。

用法：
    python tools/backfill_entities.py --dry      # 只打印，不写库
    python tools/backfill_entities.py --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import config, db, entity, persona, store  # noqa: E402


def _personas(conn):
    return conn.execute(
        "SELECT id,canonical_name,aliases_json,kind,bond FROM personas "
        "ORDER BY canonical_name").fetchall()


def backfill_kind(conn, dry=True):
    pet_names = store._pet_alias_names(conn)
    changed = []
    for r in _personas(conn):
        k = entity.kind_of(r["canonical_name"], pet_names=pet_names)
        if (r["kind"] or "") == k:
            continue
        changed.append((r["canonical_name"], r["kind"] or "", k))
        if not dry:
            conn.execute("UPDATE personas SET kind=? WHERE id=?", (k, r["id"]))
    if not dry:
        conn.commit()
    return changed


def backfill_aliases(conn, dry=True):
    """把同一角色的多张卡收敛成一张。先改名，撞车就合并。"""
    actions = []
    for r in _personas(conn):
        name = r["canonical_name"]
        canon = entity.canonicalize(name)
        if not canon or canon == name:
            continue
        exist = conn.execute(
            "SELECT id FROM personas WHERE canonical_name=? AND id<>?",
            (canon, r["id"])).fetchone()
        if exist:
            actions.append(("merge", name, canon))
            if not dry:
                persona.merge(conn, canon, name)
        else:
            aliases = set(json.loads(r["aliases_json"] or "[]"))
            aliases.add(name)
            aliases.add(canon)
            actions.append(("rename", name, canon))
            if not dry:
                conn.execute(
                    "UPDATE personas SET canonical_name=?, aliases_json=? WHERE id=?",
                    (canon, json.dumps(sorted(aliases), ensure_ascii=False), r["id"]))
    if not dry:
        conn.commit()
    return actions


def _who_names(conn, eid):
    """这条事件绑定的人物。名字先过一遍归一，dry-run 才能与 apply 结果一致。"""
    return [entity.canonicalize(r["canonical_name"]) for r in conn.execute(
        "SELECT p.canonical_name FROM edges e JOIN personas p ON p.id=e.target "
        "WHERE e.source=? AND e.type='involves'", (eid,))]


def backfill_subject(conn, dry=True):
    old_counterpart = config.COUNTERPART_NAME
    nonperson = store._nonperson_names(conn)
    changed = []
    rows = conn.execute(
        "SELECT id,gist,subject FROM events WHERE archived=0").fetchall()
    for r in rows:
        gist = r["gist"] or ""
        if not gist:
            continue
        # 历史 gist 里的「我」/「你」已经替换过，说话人按对话者传
        new = store._infer_subject(gist, _who_names(conn, r["id"]), old_counterpart,
                                   nonperson=nonperson)
        if new == (r["subject"] or ""):
            continue
        changed.append((r["id"], gist[:34], r["subject"] or "", new))
        if not dry:
            conn.execute("UPDATE events SET subject=? WHERE id=?", (new, r["id"]))
    if not dry:
        conn.commit()
    return changed


def backfill_bond(conn, dry=True):
    """重算 personas.bond（social / mentioned）。

    只对 kind='person' 的人算：非人实体本来就不该进社交图，bond 无意义。
    证据是「这个人的全部 involves 事件 + relations 里有没有边」。
    """
    pet_names = store._pet_alias_names(conn)
    changed = []
    for r in _personas(conn):
        kind = (r["kind"] or "") or entity.kind_of(
            r["canonical_name"], pet_names=pet_names)
        if kind != "person":
            continue
        if r["canonical_name"] in (config.COUNTERPART_NAME, "用户"):
            continue
        gists = [x["gist"] for x in conn.execute(
            "SELECT e.gist FROM edges ed JOIN events e ON e.id=ed.source "
            "WHERE ed.target=? AND ed.type='involves' AND e.archived=0", (r["id"],))]
        has_rel = conn.execute(
            "SELECT 1 FROM relations WHERE a=? OR b=? LIMIT 1",
            (r["id"], r["id"])).fetchone() is not None
        new = entity.bond_of(gists, has_relation=has_rel)
        try:
            old = r["bond"] or ""
        except (IndexError, KeyError):
            old = ""
        if old == new:
            continue
        changed.append((r["canonical_name"], old or "(空)", new, gists[:2]))
        if not dry:
            conn.execute("UPDATE personas SET bond=? WHERE id=?", (new, r["id"]))
    if not dry:
        conn.commit()
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    dry = not a.apply
    conn = db.connect(a.db) if a.db else db.connect()

    print("=== 1. 实体类型（personas.kind） ===")
    kinds = backfill_kind(conn, dry)
    for name, old, new in kinds:
        print("  %-12s %-8s -> %s" % (name, old or "(空)", new))
    print("  共 %d 条" % len(kinds))

    print("\n=== 2. 别名归一 ===")
    acts = backfill_aliases(conn, dry)
    for act, a1, a2 in acts:
        print("  %-6s %s -> %s" % (act, a1, a2))
    print("  共 %d 条" % len(acts))

    print("\n=== 3. 主体重算（events.subject） ===")
    subs = backfill_subject(conn, dry)
    tally = {}
    for _, _, old, new in subs:
        tally[(old, new)] = tally.get((old, new), 0) + 1
    for (old, new), n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  %-12s -> %-12s %d 条" % (old, new, n))
    print("  共 %d 条" % len(subs))
    for _, gist, old, new in subs[:12]:
        print("    %-12s -> %-12s %s" % (old, new, gist))

    # bond 依赖 kind，所以放在 kind 之后；别名合并会改 canonical_name，
    # 但不影响按 id 取事件，顺序不敏感。
    print("\n=== 4. 关系性质（personas.bond） ===")
    bonds = backfill_bond(conn, dry)
    for name, old, new, gists in bonds:
        print("  %-12s %-10s -> %-10s  %s" % (
            name, old, new, (gists[0][:40] if gists else "")))
    print("  共 %d 条" % len(bonds))

    if dry:
        print("\n[dry] 未写库。加 --apply 执行。")


if __name__ == "__main__":
    main()
