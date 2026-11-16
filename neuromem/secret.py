"""API Key 静态加密：Windows DPAPI（绑定当前用户）。

- `protect()`：明文 → `dpapi:<base64>`；非 Windows 或调用失败时原样返回
- `unprotect()`：反向；解不开（换用户/换机器）返回空串，由调用方提示重填
- 旧库里的明文 Key 仍能正常读取，下次保存时自动加密
"""
import base64
import ctypes
import sys
from ctypes import wintypes

PREFIX = "dpapi:"

if sys.platform == "win32":
    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _call(data, unprotect):
    if sys.platform != "win32" or not data:
        return None
    inb, _keep = _blob(data)
    out = _BLOB()
    if unprotect:
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out))
    else:
        ok = _crypt32.CryptProtectData(
            ctypes.byref(inb), "neuromem", None, None, None, 0, ctypes.byref(out))
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(out.pbData)


def protect(text):
    text = (text or "").strip()
    if not text or text.startswith(PREFIX):
        return text
    try:
        raw = _call(text.encode("utf-8"), unprotect=False)
    except Exception:
        return text
    if raw is None:
        return text
    return PREFIX + base64.b64encode(raw).decode("ascii")


def unprotect(stored):
    stored = (stored or "").strip()
    if not stored.startswith(PREFIX):
        return stored  # 旧明文，原样返回
    try:
        raw = _call(base64.b64decode(stored[len(PREFIX):]), unprotect=True)
    except Exception:
        return ""
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except Exception:
        return ""


def is_encrypted(text):
    return (text or "").startswith(PREFIX)