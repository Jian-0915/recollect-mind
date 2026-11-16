"""MCP server 冒烟：模拟一次 stdio 会话（initialize / tools/list / 5 个只读工具调用）。

用法：python tools/mcp_smoke.py
"""
import json
import subprocess
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "mcp_server.py")


def main():
    p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", bufsize=1)

    def send(obj):
        p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        p.stdin.flush()
        line = p.stdout.readline()
        return json.loads(line) if line else {}

    init = send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                            "clientInfo": {"name": "smoke", "version": "0"}}})
    ok_init = init.get("result", {}).get("serverInfo", {}).get("name") == "neuromem"
    print(("PASS" if ok_init else "FAIL"), "initialize", init.get("result", {}).get("protocolVersion"))

    lst = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = [t["name"] for t in lst.get("result", {}).get("tools", [])]
    print(("PASS" if len(tools) >= 10 else "FAIL"), "tools/list", tools)

    def call(name, args):
        r = send({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                  "params": {"name": name, "arguments": args}})
        res = r.get("result") or {}
        text = (res.get("content") or [{}])[0].get("text", "")
        print(("PASS" if res and not res.get("isError") else "FAIL"), name,
              text.replace("\n", " | ")[:90])
        return text

    call("memory_retrieve", {"query": "妈妈", "top_k": 3})
    call("memory_person", {"name": "阿哲"})
    call("memory_recent", {"days": 2})
    call("memory_status", {})
    call("memory_state", {})

    p.stdin.close()
    p.wait(timeout=10)
    err = p.stderr.read()
    if err.strip():
        print("stderr:", err[:300])


if __name__ == "__main__":
    main()