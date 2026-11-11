"""AstrBot 工具示例：主人明确要求时，调用 extras/qq-call 的拨号脚本。

只做“发起拨号”这一件事；通话里的语音由 call_agent.py / 你配置的 agent 负责。
"""
from __future__ import annotations

import ctypes
import os
import shlex
import subprocess
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

CREATE_NO_WINDOW = 0x08000000


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


class QQCallPlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config

    def _owner(self) -> str:
        return str(self.config.get("owner_qq", "") or "").strip()

    def _dir(self) -> Path:
        return Path(str(self.config.get("qq_call_dir", "") or "")).expanduser()

    def _python(self) -> str:
        return str(self.config.get("python_exe", "") or "python")

    def _config_file(self) -> Path:
        return self._dir() / str(self.config.get("config_file", "") or "config.json")

    def _start_agent(self) -> None:
        if not bool(self.config.get("start_agent", True)):
            return
        directory = self._dir()
        script = directory / str(self.config.get("agent_script", "") or "call_agent.py")
        if not script.is_file():
            logger.warning("qq_call: agent 脚本不存在：%s", script)
            return
        pid_file = directory / str(self.config.get("agent_pid_file", "") or "call_agent.pid")
        try:
            old = int(pid_file.read_text(encoding="ascii").strip() or "0")
        except Exception:
            old = 0
        if pid_alive(old):
            return
        args = [self._python(), str(script)]
        extra = str(self.config.get("agent_args", "") or "").strip()
        if extra:
            args.extend(shlex.split(extra))
        try:
            proc = subprocess.Popen(
                args,
                cwd=str(directory),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            pid_file.write_text(str(proc.pid), encoding="ascii")
            logger.info("qq_call: 已启动通话 agent pid=%s", proc.pid)
        except Exception as exc:
            logger.warning("qq_call: 启动通话 agent 失败：%s", exc)

    @filter.llm_tool(name="call_owner")
    async def call_owner(self, event: AstrMessageEvent) -> str:
        """当主人明确要求“给我打电话/打给我”时，发起 QQ 语音通话。

        只允许配置的 owner_qq 触发；不要主动拨打，不要给其他人拨打。
        """
        owner = self._owner()
        if not owner:
            return "没有配置 owner_qq，不能拨号。"
        if str(event.get_sender_id()) != owner:
            return "只有主人可以要求拨打电话。"
        directory = self._dir()
        if not directory.is_dir():
            return f"找不到 qq-call 目录：{directory}"
        place = directory / "place_call.py"
        if not place.is_file():
            return "找不到 place_call.py。"
        self._start_agent()
        cmd = [
            self._python(),
            str(place),
            "--config",
            str(self._config_file()),
            "--call",
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(directory),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=40,
            )
        except Exception as exc:
            return f"拨号失败：{type(exc).__name__}: {exc}"
        output = "\n".join(x for x in (result.stdout, result.stderr) if x).strip()
        logger.info("qq_call: place_call 返回：%s", output[-1000:])
        if "CALL_WINDOW_OPEN" in output:
            return "正在拨打，等电话响。"
        return "拨号没成功：" + (output[-500:] or "没有返回")
