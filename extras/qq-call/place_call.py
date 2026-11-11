"""QQ 主动拨号 POC（UI 自动化）。

把指定联系人的聊天窗口临时提到最前，按窗口相对坐标点击电话图标，
然后恢复原来的前台窗口。默认只 dry-run；加 --call 才会真的拨号。

必须在 QQ 的「音视频」设置里确认麦克风已选虚拟麦克风，否则对方听不到 TTS。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import time
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process

user32 = ctypes.windll.user32


def proc_name(pid: int) -> str:
    try:
        handle = win32api.OpenProcess(0x1000, False, pid)
        try:
            return Path(win32process.GetModuleFileNameEx(handle, 0)).name
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return "?"


def qq_windows() -> list[tuple[int, str, tuple[int, int, int, int]]]:
    rows: list[tuple[int, str, tuple[int, int, int, int]]] = []

    def callback(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if proc_name(pid).lower() == "qq.exe":
            rows.append((hwnd, title, win32gui.GetWindowRect(hwnd)))
        return True

    win32gui.EnumWindows(callback, 0)
    return rows


def find_chat(title: str) -> int | None:
    for hwnd, text, _ in qq_windows():
        if text == title:
            return hwnd
    return None


def call_window_exists(title: str) -> bool:
    return any(text == title and win32gui.IsWindowVisible(hwnd) for hwnd, text, _ in qq_windows())


def click(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--call", action="store_true", help="真的点击拨号；不加只 dry-run")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    conf = config.get("outgoing_call") or {}
    title = (conf.get("contact_title") or "").strip()
    if not title:
        raise SystemExit("outgoing_call.contact_title 为空：请填联系人备注/聊天窗口标题")

    hwnd = find_chat(title)
    if not hwnd:
        raise SystemExit(f"没有找到标题为「{title}」的 QQ 聊天窗口；先手动打开这个聊天")

    rect = win32gui.GetWindowRect(hwnd)
    x = rect[2] - int(conf.get("button_offset_from_right") or 285)
    y = rect[1] + int(conf.get("button_offset_y") or 84)
    call_title = conf.get("call_window_title") or "语音通话"
    print(f"chat hwnd={hwnd} rect={rect} call_button=({x},{y}) dry_run={not args.call}")
    if not args.call:
        return

    prev = win32gui.GetForegroundWindow()
    was_minimized = bool(win32gui.IsIconic(hwnd))
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.4)
        click(x, y)
        deadline = time.time() + float(conf.get("timeout_seconds") or 10)
        while time.time() < deadline:
            if call_window_exists(call_title):
                print("CALL_WINDOW_OPEN")
                return
            time.sleep(0.2)
        print("NO_CALL_WINDOW（可能按钮坐标变了或 QQ 弹了菜单）")
    finally:
        try:
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        except Exception:
            pass
        if was_minimized:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        if prev:
            try:
                win32gui.SetForegroundWindow(prev)
            except Exception:
                pass


if __name__ == "__main__":
    main()
