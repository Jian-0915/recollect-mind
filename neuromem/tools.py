"""外部工具层 —— 可插拔搜索 provider。

设计红线（ROADMAP 自进化章节）：
  - 念兹只认「搜索」这个动作，不认底下是 HTTP API 还是 MCP server。
  - 模型永远不直接碰工具协议：只输出意图 JSON，由本层执行。
  - 查询词必须脱敏：绝不把私人对话原文、人名、称呼发给外部服务。
  - 失败一律降级，不能把「没查到」伪装成「查到了」。

默认 provider 是 AnySearch 的 Streamable HTTP MCP 端点
（https://api.anysearch.com/mcp，MCP spec 2025-03-26）。实测该端点：
  - POST JSON-RPC 2.0，响应 application/json（也兼容 text/event-stream）
  - 无状态：不需要 initialize / session id，直接 tools/call 即可
  - 匿名可用（限流更严），有 key 走 Authorization: Bearer

零依赖：只用标准库 urllib，不给桌面端增加 pip 负担。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Protocol

from . import config


class SearchError(RuntimeError):
    """搜索失败（网络 / 服务端错误 / 返回不可解析）。"""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    fetched_at: str = ""
    request_id: str = ""

    def as_prompt(self, index: int = 1) -> str:
        """给模型看的证据块，带编号便于回答里标注出处。"""
        parts = [f"[{index}] {self.title}"]
        if self.url:
            parts.append(f"来源: {self.url}")
        if self.snippet:
            parts.append(self.snippet[:1200])
        return "\n".join(parts)


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...

    def extract(self, url: str) -> str: ...


# --------------------------------------------------------------------------
# AnySearch MCP (Streamable HTTP)
# --------------------------------------------------------------------------

_ANYSEARCH_URL = config._env("ANYSEARCH_URL", "https://api.anysearch.com/mcp")


def _api_key() -> str:
    """API key 只从环境变量读，不写进代码 / 数据库 / 仓库。

    匿名访问可用，所以这里返回空串是正常路径，不是错误。
    兼容 ANYSEARCH_API_KEY（服务官方命名）和 NEUROMEM_ANYSEARCH_KEY。
    """
    import os
    return (os.environ.get("ANYSEARCH_API_KEY")
            or os.environ.get("NEUROMEM_ANYSEARCH_KEY")
            or "").strip()


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class AnySearchProvider:
    name = "anysearch"

    def __init__(self, endpoint: str | None = None, api_key: str | None = None,
                 timeout: float = 30.0):
        self.endpoint = endpoint or _ANYSEARCH_URL
        self._key_override = api_key
        self.timeout = timeout

    def _rpc(self, method: str, params: dict) -> dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "nianzi-memory/1.0",
        }
        key = self._key_override if self._key_override is not None else _api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise SearchError(f"AnySearch HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise SearchError(f"AnySearch 连接失败: {type(exc).__name__}: {exc}") from exc

        data = _parse_rpc_body(body, ctype)
        if "error" in data:
            err = data["error"]
            raise SearchError(f"AnySearch RPC 错误: {err.get('message', err)}")
        return data.get("result", {})

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        query = (query or "").strip()
        if not query:
            raise SearchError("搜索词为空")
        result = self._rpc("tools/call", {
            "name": "search",
            "arguments": {"query": query, "max_results": max(1, min(10, max_results))},
        })
        text = _first_content_text(result)
        return parse_search_text(text)

    def extract(self, url: str) -> str:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise SearchError("extract 只接受 http(s) URL")
        result = self._rpc("tools/call", {"name": "extract", "arguments": {"url": url}})
        text = _first_content_text(result)
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return str(obj.get("content") or "")
        except (ValueError, TypeError):
            pass
        return text


def _parse_rpc_body(body: str, content_type: str) -> dict:
    """兼容 application/json 与 text/event-stream 两种 MCP 响应。"""
    if "event-stream" in content_type or body.lstrip().startswith("event:"):
        chunks = []
        for line in body.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw and raw != "[DONE]":
                    try:
                        chunks.append(json.loads(raw))
                    except ValueError:
                        continue
        for chunk in chunks:
            if "result" in chunk or "error" in chunk:
                return chunk
        raise SearchError("AnySearch SSE 响应里没有有效 JSON-RPC 结果")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise SearchError(f"AnySearch 返回不是合法 JSON: {body[:200]}") from exc
    if not isinstance(data, dict):
        raise SearchError("AnySearch 返回的 JSON 结构异常")
    return data


def _first_content_text(result: dict) -> str:
    content = result.get("content") or []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "")
    raise SearchError("AnySearch 返回里没有 text content")


# --------------------------------------------------------------------------
# 结果解析：AnySearch 的 search 返回是 Markdown 文本，转成结构化数据
# --------------------------------------------------------------------------

_TITLE_RE = re.compile(r"^###\s+\d+\.\s+(.+?)\s*$")
_URL_RE = re.compile(r"^-\s*\*\*URL\*\*:\s*(\S+)\s*$", re.IGNORECASE)
_REQ_RE = re.compile(r"^\s*request_id:\s*(\S+)\s*$", re.MULTILINE)


def parse_search_text(text: str) -> list[SearchResult]:
    """解析 AnySearch search 的 Markdown 输出。

    实测格式::

        ## Search Results (10 results, 1345ms)

        ### 1. 标题
        - **URL**: https://...
        - 摘要第一行
        - 摘要第二行

        ### 2. ...

        ```yaml
        request_id: xxx
        ```
    """
    if not text or not text.strip():
        raise SearchError("AnySearch 搜索结果为空")
    m = _REQ_RE.search(text)
    request_id = m.group(1) if m else ""
    fetched = _iso_now()

    results: list[SearchResult] = []
    current: SearchResult | None = None
    snippet_lines: list[str] = []

    def flush():
        nonlocal current, snippet_lines
        if current is not None:
            current.snippet = " ".join(s.strip() for s in snippet_lines if s.strip())
            results.append(current)
        current, snippet_lines = None, []

    for line in text.splitlines():
        t = _TITLE_RE.match(line)
        if t:
            flush()
            current = SearchResult(title=t.group(1).strip(), url="",
                                   fetched_at=fetched, request_id=request_id)
            continue
        u = _URL_RE.match(line)
        if u and current is not None:
            current.url = u.group(1).strip()
            continue
        if current is not None and line.strip().startswith("- "):
            snippet_lines.append(line.strip()[2:])

    flush()
    if not results:
        raise SearchError(f"AnySearch 搜索结果无法解析: {text[:200]}")
    return results


# --------------------------------------------------------------------------
# 查询脱敏 —— 私人记忆绝不外发
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_URL_RE_ANY = re.compile(r"https?://\S+")
_QUOTED_RE = re.compile(r"[「『\"“']([^」』\"”']{2,80})[」』\"”']")


def sanitize_query(text: str, private_terms: Iterable[str] = ()) -> str:
    """把一句对话改写成可安全外发的检索词。

    - 去掉邮箱 / 手机号 / URL
    - 去掉引号里的原话（那是私人对话，不是检索词）
    - 去掉传入的私人词（人名、称呼、宠物名等，由调用方从人设卡取）
    - 只保留空格/标点分隔后的实体片段，避免整句私聊原文外发

    注意：这是「最小必要」原则的兜底，调用方更该只把识别出的实体词传进来，
    而不是把整段对话丢进来再指望脱敏兜住。
    """
    s = text or ""
    s = _URL_RE_ANY.sub(" ", s)
    s = _EMAIL_RE.sub(" ", s)
    s = _PHONE_RE.sub(" ", s)
    s = _QUOTED_RE.sub(" ", s)
    for term in private_terms:
        term = (term or "").strip()
        if term and len(term) >= 1:
            s = s.replace(term, " ")
    s = re.sub(r"\s+", " ", s).strip(" ,，。.!！?？:：;；、")
    return s


# --------------------------------------------------------------------------
# Provider 工厂
# --------------------------------------------------------------------------

def get_provider(name: str | None = None) -> SearchProvider:
    """按配置返回 provider。目前只有 anysearch，留接口给将来接 HTTP API。"""
    choice = (name or config._env("SEARCH_PROVIDER", "anysearch")).strip().lower()
    if choice in ("", "anysearch", "mcp"):
        return AnySearchProvider()
    raise SearchError(f"未知的搜索 provider: {choice}")


def search(query: str, max_results: int = 5,
           provider: SearchProvider | None = None) -> list[SearchResult]:
    return (provider or get_provider()).search(query, max_results)
