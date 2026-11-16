"""跨进程配置同步：一个进程改了设置，服务进程必须能立即看到。"""
import json
import os
import subprocess
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import settings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
orig = settings.current()["llm"]["extract_model"]
print("orig:", orig)

try:
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from neuromem import settings;"
        "settings.save({'llm': {'extract_model': 'sync-test-model'}});"
        "print('child saved')" % ROOT
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    print("child:", out.stdout.strip(), out.stderr.strip()[:200])

    with urllib.request.urlopen("http://127.0.0.1:8000/api/settings", timeout=30) as f:
        seen = json.load(f)["settings"]["llm"]["extract_model"]
    print("service sees:", seen)
    assert seen == "sync-test-model", "跨进程配置未同步（服务仍是旧缓存）"
finally:
    settings.save({"llm": {"extract_model": orig}})
    with urllib.request.urlopen("http://127.0.0.1:8000/api/settings", timeout=30) as f:
        back = json.load(f)["settings"]["llm"]["extract_model"]
    print("restored, service sees:", back)
    assert back == orig

print("PASS（跨进程配置热生效）")