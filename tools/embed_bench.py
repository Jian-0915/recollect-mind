"""嵌入模型延迟/维度基准：bge-large vs qwen3-embedding。

用法：
    python tools/embed_bench.py --model bge-large:latest
    python tools/embed_bench.py --model qwen3-embedding:4b
    python tools/embed_bench.py --model qwen3-embedding:0.6b

只读，不改库、不改配置。第一次调用含模型加载，单独记为 cold。
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:11434"

SAMPLE = [
    ("短", "我的猫怕什么"),
    ("中", "团子特别怕吹风机，一开就钻到沙发底下"),
    ("长", "用户上个月去杭州出差，住在西湖边的民宿，白天开会晚上沿着湖走了一圈，"
           "回来之后把照片整理成了一个相册，还给妈妈挑了一条丝巾，"
           "说下次想带家里人一起去，顺便尝尝当地的片儿川和龙井虾仁。"),
]


def embed(model, texts, timeout=600):
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as f:
        d = json.loads(f.read().decode("utf-8"))
    return d.get("embeddings") or [], time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-large:latest")
    ap.add_argument("--n", type=int, default=5, help="每个样本重复次数")
    a = ap.parse_args()

    print("模型:", a.model)
    try:
        t0 = time.time()
        vecs, dt = embed(a.model, ["预热"])
        print("cold: %.2fs  dim=%d" % (dt, len(vecs[0]) if vecs else 0))
    except Exception as e:
        print("调用失败:", str(e)[:200])
        print("提示: 先 `ollama pull %s`，并确认 `ollama serve` 在跑" % a.model)
        return 1

    for label, text in SAMPLE:
        times = []
        for _ in range(a.n):
            _, dt = embed(a.model, [text])
            times.append(dt)
        print(" %s(%d字): avg %.0fms  p50 %.0fms  min %.0fms  char/s %.1f" % (
            label, len(text), statistics.mean(times) * 1000,
            statistics.median(times) * 1000, min(times) * 1000,
            len(text) / statistics.mean(times)))

    texts = [t for _, t in SAMPLE] * 4  # 12 条批量
    vecs, dt = embed(a.model, texts)
    print(" 批量12条: %.0fms  单条 %.0fms  dim=%d" % (
        dt * 1000, dt * 1000 / len(texts), len(vecs[0]) if vecs else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())