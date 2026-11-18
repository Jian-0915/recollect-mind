"""系统通知：Windows 原生可点击 Toast，失败降级为气球提示。

设计的两条硬约束（来自需求）：
- 通知必须能点击回到聊天页。Windows 气球的 BalloonTip 没有可用的点击回调，
  所以主路径走 WinRT Toast（win11toast），它带 activated 回调。
- 通知发送不能卡住桌面端。win11toast.toast() 内部会等到「点击 / 消失 /
  失败」三者之一才返回，必须在守护线程里调用，主线程只负责投递。

降级：没有 win11toast（非 Windows / 未安装）或发送抛异常时，退回旧的气球
提示。此时没有点击回跳，但提醒本身不丢，不影响对话主流程。
"""
from __future__ import annotations

import threading

# 顶层 import：让 COM/WinRT 的初始化发生在主线程（模块被 app.py 导入时），
# 回调线程里只负责投递与触发回调，避免每次发通知都重新初始化 COM。
try:
    from win11toast import toast as _toast
except Exception:          # pragma: no cover - 非 Windows 或没装依赖
    _toast = None

_TITLE = "阿澈"


def _balloon(title, message):
    """旧路径：Windows 气球提示，5 秒后自动消失，没有点击回调。"""
    try:
        import subprocess
        title_esc = str(title).replace("'", "''")
        msg_esc = str(message).replace("'", "''")[:200]
        ps = (f"Add-Type -AssemblyName System.Windows.Forms;"
              f"$n=New-Object System.Windows.Forms.NotifyIcon;"
              f"$n.Icon=[System.Drawing.SystemIcons]::Information;"
              f"$n.BalloonTipTitle='{title_esc}';"
              f"$n.BalloonTipText='{msg_esc}';"
              f"$n.Visible=$true;$n.ShowBalloonTip(5000);"
              f"Start-Sleep -Milliseconds 5200;$n.Dispose()")
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def send(title, message, on_click=None):
    """投递一条系统通知；点击时回调 on_click（在守护线程执行）。

    返回 True 表示走了可点击 Toast，False 表示走了降级气球。
    无论哪条路径都不抛异常——通知是提醒，不该让对话失败。
    """
    title = str(title or _TITLE)
    message = str(message or "")

    if _toast is None:
        return _balloon(title, message)

    def _fire():
        def clicked(_args):
            if callable(on_click):
                try:
                    on_click()
                except Exception:
                    pass

        def _noop(_reason):
            pass

        try:
            # toast() 会阻塞到点击/消失，duration=short 约几秒，避免长挂。
            _toast(title, message[:180], on_click=clicked,
                   on_dismissed=_noop, on_failed=_noop, duration="short")
        except Exception:
            _balloon(title, message)

    try:
        threading.Thread(target=_fire, daemon=True, name="nm-toast").start()
        return True
    except Exception:
        return _balloon(title, message)
