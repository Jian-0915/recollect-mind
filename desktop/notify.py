"""系统通知：Windows 原生可点击 Toast，失败降级为气球提示。

设计的两条硬约束（来自需求）：
- 通知必须能点击回到聊天页。Windows 气球的 BalloonTip 没有可用的点击回调，
  所以主路径走 WinRT Toast（win11toast），它带 activated 回调。
- 通知发送不能卡住桌面端。pywebview 6.x 的 js_api 回调跑在 **UI 线程**，
  而 win11toast 的 import 会加载整套 WinRT/COM 库；若在 UI 线程同步 import，
  窗口消息队列会被阻塞，表现为「程序未响应」。因此这里把 import、COM 初始化、
  toast() 的等待全部放进守护线程，UI 线程的 send() 只做参数整理和起线程，
  立即返回。

降级：没有 win11toast（非 Windows / 未安装）或发送抛异常时，退回旧的气球
提示。此时没有点击回跳，但提醒本身不丢，不影响对话主流程。
"""
from __future__ import annotations

import sys
import threading

_TITLE = "念兹"

# win11toast 一旦报错就永久走降级：反复在 UI 线程触发重 import 会拖垮窗口。
_broken = False
_lock = threading.Lock()


def _load_toast():
    """惰性加载 win11toast，便于测试注入，也把重 import 挡在守护线程外。"""
    from win11toast import toast
    return toast


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

    返回 True 表示已投递到 Toast 守护线程，False 表示走了降级气球。
    无论哪条路径都不抛异常——通知是提醒，不该让对话失败，更不该卡住 UI。
    """
    global _broken
    title = str(title or _TITLE)
    message = str(message or "")

    with _lock:
        broken = _broken
    if broken:
        return _balloon(title, message)

    def _fire():
        global _broken
        try:
            # 关键：重 import 只在这条守护线程里发生，绝不碰 pywebview UI 线程。
            toast = _load_toast()

            def clicked(_args):
                if callable(on_click):
                    try:
                        on_click()
                    except Exception:
                        pass

            def _noop(_reason):
                pass

            # toast() 会阻塞到点击/消失，duration=short 约几秒，避免长挂。
            toast(title, message[:180], on_click=clicked,
                  on_dismissed=_noop, on_failed=_noop, duration="short")
        except Exception:
            import traceback
            print("[notify] toast failed:\n" + traceback.format_exc(), file=sys.stderr)
            with _lock:
                _broken = True
            _balloon(title, message)

    try:
        threading.Thread(target=_fire, daemon=True, name="nm-toast").start()
        return True
    except Exception:
        return _balloon(title, message)
