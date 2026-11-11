"""可选的 QQ 来电自动接听。

默认关闭；只有 auto_answer.enabled=true 且 allowed_callers 非空时才会动作。
仅接受来电窗口文本中包含白名单名字的通话，避免接陌生人的电话。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from pathlib import Path

import uiautomation as auto
import win32api
import win32gui
import win32process

user32 = ctypes.windll.user32


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def proc_name(pid: int) -> str:
    try:
        handle = win32api.OpenProcess(0x1000, False, pid)
        try:
            return os.path.basename(win32process.GetModuleFileNameEx(handle, 0))
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return "?"


def call_windows() -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []

    def callback(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if proc_name(pid).lower() == "qq.exe" and win32gui.IsWindowVisible(hwnd) and "语音通话" in title:
            rows.append((hwnd, title))
        return True

    win32gui.EnumWindows(callback, 0)
    return rows


def window_text(root) -> str:
    parts: list[str] = []
    try:
        for control, _ in auto.WalkControl(root, maxDepth=5):
            name = (control.Name or "").strip()
            if name:
                parts.append(name)
    except Exception:
        pass
    return "\n".join(parts)


def click(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    config = load_config(Path(args.config))
    conf = config.get("auto_answer") or {}
    allowed = [str(x).strip() for x in (conf.get("allowed_callers") or []) if str(x).strip()]
    if not conf.get("enabled"):
        print("auto_answer 未启用，退出。")
        return
    if not allowed:
        print("auto_answer 需要 allowed_callers，不能空着自动接听任何来电。退出。")
        return

    button_name = conf.get("button_name") or "接听"
    poll = float(conf.get("poll_seconds") or 0.3)
    handled: set[int] = set()
    print("auto-answer 已启用；白名单：", allowed, flush=True)
    while True:
        for hwnd, title in call_windows():
            if hwnd in handled:
                continue
            try:
                root = auto.ControlFromHandle(hwnd)
                text = window_text(root)
            except Exception as exc:
                print("读取通话窗口失败：", type(exc).__name__, str(exc)[:120], flush=True)
                continue
            if not any(name in text for name in allowed):
                print(f"跳过非白名单来电 hwnd={hwnd}", flush=True)
                handled.add(hwnd)
                continue
            button = auto.ButtonControl(Name=button_name, searchFromControl=root, searchDepth=8)
            if not button.Exists(1):
                print(f"没有找到「{button_name}」按钮，可能已接通", flush=True)
                handled.add(hwnd)
                continue
            handled.add(hwnd)
            rect = button.BoundingRectangle
            click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            print(f"已接听 hwnd={hwnd}", flush=True)
        time.sleep(poll)


if __name__ == "__main__":
    main()
