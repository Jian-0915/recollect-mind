"""品牌标识与无控制台启动的契约测试。

覆盖两件事：
- 窗口标题/通知兜底统一用品牌名「念兹」，不跟人设名漂移；
- pythonw 下 sys.stdout/stderr 为 None 时，日志兜底到文件且不抛异常。

日志兜底用子进程验证（必须先改 sys.stdout 再 import），不污染测试进程。
"""
import subprocess
import sys

import desktop.app as da


def test_brand_is_consistent():
    """品牌名只有一份，窗口标题与通知兜底都指向它。"""
    assert da.BRAND == "念兹"
    assert da.PRODUCT_TITLE == da.BRAND
    assert da.TITLE == da.BRAND


def test_no_console_redirects_stderr_to_log(tmp_path):
    """模拟 pythonw：stdout/stderr 为 None 时 import，print 不炸且落盘。"""
    log = tmp_path / "desktop.log"
    code = (
        "import sys; sys.stdout = None; sys.stderr = None\n"
        "import desktop.app\n"
        "print('BOOT-OK', file=sys.stderr)\n"
        "sys.stderr.flush()\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(da.ROOT),
        env={"NEUROMEM_DESKTOP_LOG": str(log), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert log.exists(), "日志文件未创建"
    assert "BOOT-OK" in log.read_text(encoding="utf-8")
