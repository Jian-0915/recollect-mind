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


def test_close_uses_closest_for_svg_child():
    """× 是 SVG，点到 <path> 时 target 是子元素，必须用 closest 才能命中按钮。

    旧写法 event.target.hasAttribute("data-close") 只在点到按钮空白处生效，
    点到图标线条就失灵——表现成"偶发性关不掉"。
    """
    app = (Path(__file__).resolve().parents[2] / "desktop" / "web"
           / "assets" / "app.js").read_text(encoding="utf-8")
    assert 'closest("[data-close]")' in app
    assert 'hasAttribute("data-close")' not in app


def test_drawer_itself_has_no_data_close():
    """抽屉根元素不能带 data-close，否则点面板内部任意处都会误关。"""
    html = _html()
    start = html.find('<aside class="drawer" id="knowledgeDrawer"')
    assert start != -1
    open_tag = html[start:html.find(">", start) + 1]
    assert "data-close" not in open_tag, (
        "drawer 根节点带 data-close 会让 closest 把面板内点击也当成关闭"
    )


def test_nested_layer_close_does_not_bubble_to_parent():
    """嵌套弹层：只让最近的那一层响应关闭，点内层不把外层抽屉一起关掉。"""
    app = (Path(__file__).resolve().parents[2] / "desktop" / "web"
           / "assets" / "app.js").read_text(encoding="utf-8")
    assert 'closest(".sheet, .drawer, .overlay")' in app
    assert "if (owner !== layer) return;" in app
