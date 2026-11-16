"""命令行入口：``python -m agent --help``。

    python -m agent script --fresh --yes               # 跑默认剧本并验收
    python -m agent script --script smoke --compare      # A/B 对照
    python -m agent chat --show-context                 # 终端真聊
    python -m agent report --trace data/agent_lab.trace.jsonl

默认写独立实验库 data/agent_lab.db；``--fresh`` 拒绝删主库 neuromem.db。
"""
import argparse
import json
import sys
from pathlib import Path

from . import script as scripts
from .companion import LAB_DB, LAB_TRACE, CompanionAgent
from .report import compare, format_report, lab_report

BASE_DIR = Path(__file__).resolve().parent.parent
MAIN_DB = BASE_DIR / "data" / "neuromem.db"


def _add_common(p):
    p.add_argument("--db", default=str(LAB_DB), help="实验库路径（默认独立库）")
    p.add_argument("--trace", default=str(LAB_TRACE), help="trace JSONL 输出")
    p.add_argument("--model", default=None, help="覆盖生成模型名")
    p.add_argument("--local", action="store_true",
                    help="改用本地 Ollama（配合 --model，如 qwen3:8b）")
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--no-brain", action="store_true", help="对照组：不接记忆")


def _fresh(paths, yes):
    """删实验库三件套；硬保护：绝不接受主库路径，且必须显式 --yes。"""
    for p in paths:
        if Path(p).resolve() == MAIN_DB.resolve():
            raise SystemExit("拒绝清空主库 %s（请用独立实验库）" % MAIN_DB)
    if not yes:
        raise SystemExit("--fresh 需要同时给 --yes；将被重建的文件：\n  " +
                         "\n  ".join(str(p) for p in paths))
    for p in paths:
        for suffix in ("", "-wal", "-shm"):
            q = Path(str(p) + suffix)
            if q.exists():
                q.unlink()
                print("已删除 %s" % q)


def _make_agent(args, db_path):
    return CompanionAgent(
        db_path=db_path, session_id=args.session, model=args.model,
        provider="ollama" if args.local else None,
        temperature=args.temperature, use_brain=not args.no_brain,
        trace_path=args.trace)


def cmd_script(args):
    turns = scripts.get(args.script)
    dbp = Path(args.db)
    control = dbp.with_name(dbp.stem + "_control" + dbp.suffix)
    if args.fresh:
        _fresh([dbp, control] if args.compare else [dbp], args.yes)

    def run(path, use_brain, trace_path):
        a = CompanionAgent(
            db_path=path, session_id=args.session, model=args.model,
            provider="ollama" if args.local else None,
            temperature=args.temperature, use_brain=use_brain,
            trace_path=trace_path)
        try:
            print("\n>>> %s：%s，共 %d 轮" %
                  ("接记忆" if use_brain else "对照组·无记忆", path, len(turns)))
            a.run_script(turns)
            a.save_trace()
            rep = a.report()
            print("\n" + format_report(rep))
            return rep
        finally:
            a.close()

    rep_a = run(dbp, not args.no_brain, args.trace)
    rep_b = None
    if args.compare:
        rep_b = run(control, False,
                    str(Path(args.trace).with_suffix("")) + ".control.jsonl")
        print("\n" + compare(rep_a, rep_b))

    if args.json_out:
        out = {"a": rep_a, "b": rep_b} if rep_b else rep_a
        Path(args.json_out).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n报告已写入 %s" % args.json_out)
    return 0


def cmd_chat(args):
    a = _make_agent(args, args.db)
    print("陪伴 Agent 已启动（库：%s，模式：%s）。/quit 退出，/report 看报告，"
          "/context 看本轮大脑上下文，/surface 主动话头。" %
          (args.db, "接记忆" if not args.no_brain else "无记忆对照"))
    try:
        while True:
            try:
                text = input("\n你：").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text in ("/quit", "/exit", "q"):
                break
            if text == "/report":
                print(format_report(a.report()))
                continue
            if text == "/surface":
                print(json.dumps(a.surface(), ensure_ascii=False, default=str)[:1000])
                continue
            if text == "/tick":
                print(json.dumps(a.tick(), ensure_ascii=False, default=str)[:1000])
                continue
            row = a.step(text)
            print("伴：%s" % row["reply"])
            if args.show_context:
                print("     [候选 %d / 采用 %s / 写入 %d / 情绪 %s / %s]" % (
                    row["candidate_count"], row["used_ids"] or "无",
                    len(row["remembered_ids"]), row["mood_top"] or "-",
                    row["outcome"]))
        a.save_trace()
        print("\n" + format_report(a.report()))
    finally:
        a.close()
    return 0


def cmd_report(args):
    trace = []
    tp = Path(args.trace)
    if tp.exists():
        for line in tp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    trace.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    print(format_report(lab_report(Path(args.db), trace,
                                   use_brain=not args.no_brain)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m agent",
                                 description="陪伴 Agent 实验台（v13）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("script", cmd_script), ("chat", cmd_chat),
                    ("report", cmd_report)):
        p = sub.add_parser(name)
        _add_common(p)
        p.add_argument("--session", default="lab")
        if name == "script":
            p.add_argument("--script", default="default",
                            choices=sorted(scripts.SCRIPTS))
            p.add_argument("--fresh", action="store_true", help="重建实验库")
            p.add_argument("--yes", action="store_true", help="确认删除实验库")
            p.add_argument("--compare", action="store_true",
                           help="额外跑无记忆对照组并输出 A/B")
            p.add_argument("--json-out", default=None, help="报告 JSON 输出路径")
        if name == "chat":
            p.add_argument("--show-context", action="store_true")
        p.set_defaults(func=fn)
    args = ap.parse_args(argv)
    # 解析完 --db 再把运行时（usage_log / settings / db.connect 默认值）
    # 指到实验库，避免真实测试写主库。
    from .bootstrap import point_runtime_to
    point_runtime_to(args.db)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
