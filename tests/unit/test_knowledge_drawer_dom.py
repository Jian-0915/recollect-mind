"""外部知识抽屉（「她知道了什么」）的可点击性回归测试。

踩过的坑：抽屉曾被嵌进 .toast-wrap 里，而 .toast-wrap 是 pointer-events:none，
导致抽屉内的关闭按钮/搜索框/自主查询开关全部收不到点击，用户关不掉面板、
回不到对话页。这里用源码静态断言把抽屉钉在 .toast-wrap 之外，防止再犯。

不改 DOM、不起浏览器、不碰数据库：纯读文件断言，快且稳。
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "desktop" / "web" / "index.html"


def _html():
    return INDEX.read_text(encoding="utf-8")


def test_drawer_is_outside_toast_wrap():
    """抽屉不能落在 .toast-wrap 内——后者 pointer-events:none 会吃掉所有点击。"""
    html = _html()
    start = html.find('<div class="toast-wrap">')
    assert start != -1, "找不到 .toast-wrap 容器"
    end = html.find("</div>", html.find('id="toast"', start))
    toast_wrap_block = html[start:end]
    assert "knowledgeDrawer" not in toast_wrap_block, (
        "知识抽屉又被嵌进 .toast-wrap 了，关闭按钮会点不动"
    )


def test_drawer_exists_once_and_has_close_target():
    """抽屉只出现一次，且关闭按钮带 data-close（关闭监听靠它触发）。"""
    html = _html()
    assert html.count('id="knowledgeDrawer"') == 1
    # 关闭按钮或抽屉本身至少要有一个 data-close，点击才能关
    drawer_start = html.find('id="knowledgeDrawer"')
    drawer_block = html[drawer_start:drawer_start + 4000]
    assert "data-close" in drawer_block
