"""Optional Windows per-user login startup; never enabled implicitly."""
import subprocess
import sys
from pathlib import Path

KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
NAME = 'ProxyTrafficMonitor'


def command():
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('开机自启仅支持便携 EXE；请先构建或下载便携版。')
    return subprocess.list2cmdline([str(Path(sys.executable).resolve()), '--recovered'])


def enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value, _ = winreg.QueryValueEx(key, NAME)
        return value == command()
    except FileNotFoundError:
        return False


def set_enabled(enable):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, NAME)
            except FileNotFoundError:
                pass
