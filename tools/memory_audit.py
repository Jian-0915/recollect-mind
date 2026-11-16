"""Read-only audit of memory-store data quality.

Checks: duplicate / orphan / first-person personas, dangling edges, duplicate
events and supersede-chain health. Never writes to the database.

Usage:
    python tools/memory_audit.py --db data/neuromem.db --out data/memory_audit.json
"""
import argparse
import json
import sqlite3
from collections import defaultdict

FIRST_PERSON = ("我", "我们", "自己", "本人", "俺")


def _dicts(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def audit(conn):
    conn.row_factory = sqlite3.Row
    table_names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    tables = {}
    for t in table_names:
        try:
            tables[t] = conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        except Exception:
            tables[t] = -1

    known_ids = set()
    for t in table_names:
        if t == "edges":
            continue
        try:
            for (v,) in conn.execute('SELECT id FROM "%s"' % t):
                if v is not None:
                    known_ids.add(str(v))
        except Exception:
            pass

    personas = _dicts(conn, "SELECT id,canonical_name,aliases_json FROM personas")
    name2id = {}
    for p in personas:
        name2id.setdefault(p["canonical_name"], p["id"])

    edge_n = defaultdict(int)
    for r in conn.execute("SELECT target,COUNT(*) n FROM edges WHERE type='involves' GROUP BY target"):
        edge_n[r["target"]] = r["n"]

    # Only treat as duplicate when the shorter name is a *kinship suffix* of the
    # longer one (妈 ⊂ 我妈). A bare pronoun prefix (我 ⊂ 我妈 / 我 ⊂ 我爸) is
    # NOT the same entity -- "我" is the user's first person, not a person named 我.
    pronoun_prefixes = ("我", "你", "他", "她")
    contains = []
    names = list(name2id)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a and b and a != b and (a in b or b in a):
                short, long = (a, b) if len(a) <= len(b) else (b, a)
                if short in pronoun_prefixes:
                    continue
                contains.append({
                    "short": short, "long": long,
                    "short_edges": edge_n.get(name2id[short], 0),
                    "long_edges": edge_n.get(name2id.get(long, ""), 0),
                })

    orphans = [{"name": p["canonical_name"], "id": p["id"]}
               for p in personas if edge_n.get(p["id"], 0) == 0]
    first_person = [{"name": p["canonical_name"], "id": p["id"], "edges": edge_n.get(p["id"], 0)}
                    for p in personas if p["canonical_name"] in FIRST_PERSON]

    dangling = []
    edge_types = defaultdict(int)
    for r in conn.execute("SELECT source,target,type FROM edges"):
        edge_types[r["type"]] += 1
        for side in ("source", "target"):
            val = str(r[side])
            if val and val not in known_ids:
                dangling.append({"side": side, "id": val, "type": r["type"]})

    gmap = defaultdict(list)
    for r in conn.execute("SELECT id,gist,created_at FROM events"):
        key = (r["gist"] or "").strip()
        if key:
            gmap[key].append({"id": r["id"], "created_at": r["created_at"]})
    dup_gist = [{"gist": k, "count": len(v), "ids": [x["id"] for x in v]}
                for k, v in gmap.items() if len(v) > 1]

    event_ids = set(x[0] for x in conn.execute("SELECT id FROM events"))
    sup = _dicts(conn, "SELECT id,gist,superseded_by FROM events WHERE superseded_by IS NOT NULL")
    bad_sup = [{"id": r["id"], "target": r["superseded_by"]} for r in sup
               if r["superseded_by"] and r["superseded_by"] not in event_ids]

    return {
        "tables": tables,
        "personas": {"total": len(personas), "contains": contains,
                     "orphans": orphans, "first_person": first_person},
        "edges": {"total": sum(edge_types.values()), "by_type": dict(edge_types),
                  "dangling": dangling[:50], "dangling_total": len(dangling)},
        "events": {"duplicate_gist_groups": len(dup_gist), "duplicate_gists": dup_gist[:30]},
        "supersede": {"marked": len(sup), "dangling_targets": bad_sup},
    }


def render(rep):
    lines = ["=== Memory-store data-quality audit (read-only) ==="]
    lines.append("tables: " + ", ".join("%s=%s" % kv for kv in rep["tables"].items() if kv[1]))
    p = rep["personas"]
    lines.append("\npersonas: %d" % p["total"])
    lines.append("  name-containment (suspected duplicates): %d" % len(p["contains"]))
    for c in p["contains"]:
        lines.append("    %s(%d edges) subset-of %s(%d edges)" % (
            c["short"], c["short_edges"], c["long"], c["long_edges"]))
    lines.append("  zero-edge orphans: " + (", ".join(x["name"] for x in p["orphans"]) or "none"))
    lines.append("  first-person personas: " + (
        ", ".join("%s(%d)" % (x["name"], x["edges"]) for x in p["first_person"]) or "none"))
    e = rep["edges"]
    lines.append("\nedges: %d %s" % (e["total"], e["by_type"]))
    lines.append("  dangling: %d" % e["dangling_total"])
    for d in e["dangling"][:8]:
        lines.append("    %s=%s (%s)" % (d["side"], d["id"][:16], d["type"]))
    lines.append("\nduplicate-gist groups: %d" % rep["events"]["duplicate_gist_groups"])
    lines.append("supersede marks: %d, dangling targets: %d" % (
        rep["supersede"]["marked"], len(rep["supersede"]["dangling_targets"])))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/neuromem.db")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    rep = audit(conn)
    conn.close()
    print(render(rep))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("\nJSON ->", args.out)


if __name__ == "__main__":
    main()
