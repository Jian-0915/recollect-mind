"""真实 HTTP 验收：打 127.0.0.1:8000 的 /api/live（会真的写主库）。

与 tools/verify_live.py 的区别：那个直接调模块（进程内），这个走完整
HTTP + 鉴权 + 服务进程里的常驻单例，验证"web 上点发送"这条真实路径。

用法：python tools/verify_live_http.py "要说的话"
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from neuromem import account  # noqa: E402

BASE = "http://127.0.0.1:8000"


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "记一下：今天上午把网页对话接通了"
    s = requests.Session()
    s.cookies.set(account.COOKIE, account.make_token("jian"))

    st0 = s.get(BASE + "/api/live/status", timeout=15).json()
    print("BEFORE:", json.dumps({k: st0.get(k) for k in
                                 ("enabled", "loaded", "turns_stored", "events",
                                  "last_turn_at", "open_topics")},
                                ensure_ascii=False))

    r = s.post(BASE + "/api/live", json={"query": text}, timeout=240).json()
    print("TURN ok=%s elapsed=%sms" % (r.get("ok"), r.get("elapsed_ms")))
    print("  reply:", r.get("reply"))
    print("  remembered:", r.get("remembered"))
    print("  hint:", r.get("hint"))
    if not r.get("ok"):
        print("  error:", r.get("error"))
        return 1

    st1 = s.get(BASE + "/api/live/status", timeout=15).json()
    print("AFTER :", json.dumps({k: st1.get(k) for k in
                                 ("enabled", "loaded", "turns_stored", "events",
                                  "last_turn_at", "open_topics")},
                                ensure_ascii=False))
    d_turns = (st1.get("turns_stored") or 0) - (st0.get("turns_stored") or 0)
    d_events = (st1.get("events") or 0) - (st0.get("events") or 0)
    print("DELTA : turns %+d / events %+d / 时钟推进 %s"
          % (d_turns, d_events,
             "是" if st1.get("last_turn_at") != st0.get("last_turn_at") else "否"))
    ok = d_turns >= 2 and st1.get("last_turn_at") != st0.get("last_turn_at")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
