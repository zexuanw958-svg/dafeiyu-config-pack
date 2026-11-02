# -*- coding: utf-8 -*-
"""掉线提醒：小号被 QQ 踢下线，电脑右下角弹个通知，点一下就重新登录。

登录 Windows 时从启动文件夹起来（pythonw，没窗口），一直在后台看：
  本机那套：大脑开着、小号却没连上它，或者 NapCat 日志里最后一件事是被踢下线；
  云端那套：经 ssh 问服务器同样的事（private\\cloud.txt，跟云端工作台同一把钥匙）。
不吵人的规矩：
  - 连续掉线 3 分钟才提醒（重启大脑、网络闪一下都不算）；日志明确写了被踢的，下一轮确认后就提醒。
  - 一次掉线最多提醒 3 回：马上、半小时后、两小时后。她回来就清零。
  - 大脑没开不提醒（多半是他自己关的）；云端连不上服务器也不提醒（多半是这台电脑没网）。
  - 正在重新登录（开机登录、双击重登）时不提醒。
  - 电脑刚睡醒重新算 3 分钟。
  - private\\qq-watch-off.txt 存在就不提醒（工作台的开关）。

只用标准库；本机那套借 backend_state.py 判断在线（同一个目录）。

用法：
  pythonw qq_watch.py --dir <工作目录>      一直跑
  python  qq_watch.py --dir <工作目录> --once    只看一次，打印结果
  python  qq_watch.py --dir <工作目录> --test    弹一条试试
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import struct
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0

INTERVAL = {"local": 60, "cloud": 180}
GRACE = 180                     # 连续掉线多少秒才提醒
REMIND = (0, 30 * 60, 2 * 3600)  # 第几回提醒，距离第一回多久
KICK_RX = re.compile(r"KickedOffLine|登录已失效|登录态已失效|被迫下线|下线通知")
# 日志里这些说明她之后还活着：又登了一次。收到消息单独算（聊天内容也在日志里，不能拿它去匹配被踢）
ALIVE_RX = re.compile(r"本账号数据/缓存目录|WebSocket反向服务.*已启动|二维码已被扫描")
FULL_EVERY = 15 * 60            # 在线时隔多久才完整查一次（完整查要起 PowerShell）


# ---------------------------------------------------------------- 工作目录

def read_text(p):
    try:
        return Path(p).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def read_kv(p):
    return {m[1].lower(): m[2].strip() for m in re.finditer(r"(?m)^\s*([^\s:：]+)\s*[:：]\s*(.*)$", read_text(p))}


def is_cloud(d):
    kv = read_kv(Path(d) / "private" / "cloud.txt")
    return bool(kv.get("host") or kv.get("ip")) and not (Path(d) / "start-astrbot.cmd").exists() and not (Path(d) / "astrbot" / "data").is_dir()


def relogin_running(d):
    """relogin-qq.ps1 开着时拿着一个命名锁（开机等大脑、双击重登都算），看锁在不在就行。"""
    if not IS_WIN:
        return False
    import ctypes
    norm = os.path.abspath(str(d)).rstrip("\\").lower()
    tag = hashlib.sha256(norm.encode("utf-8")).hexdigest().upper()[:24]
    k32 = ctypes.windll.kernel32
    h = k32.OpenMutexW(0x00100000, False, "Local\\DafeiyuQQ-" + tag)  # SYNCHRONIZE
    if h:
        k32.CloseHandle(h)
        return True
    return False


# ---------------------------------------------------------------- 本机：在不在线

def napcat_log(d):
    logs = []
    for sh in (Path(d) / "NapCat").glob("NapCat.*.Shell"):
        logs += list(sh.glob("versions/*/resources/app/napcat/logs/*.log"))
    try:
        return max(logs, key=lambda p: p.stat().st_mtime) if logs else None
    except OSError:
        return None


def kicked_in(lines):
    """最后一件事是被踢：有踢下线的字样，之后再没收到过消息、也没重新登录。
    发出去不算活着（被踢后她还会试着发，发不出去）；聊天内容里提到「下线通知」也不算被踢。"""
    kicked = False
    for ln in lines:
        if " 接收 <- " in ln:
            kicked = False
        elif " 发送 -> " in ln:
            continue
        elif KICK_RX.search(ln):
            kicked = True
        elif ALIVE_RX.search(ln):
            kicked = False
    return kicked


def tail(path, size=200_000):
    try:
        with open(path, "rb") as fp:
            fp.seek(0, 2)
            n = fp.tell()
            fp.seek(max(0, n - size))
            data = fp.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()


def protocol(d):
    p = read_text(Path(d) / "private" / "protocol.txt").strip().lower()
    if p:
        return p
    return "snowluma" if (Path(d) / "start-snowluma.cmd").exists() and not (Path(d) / "start-napcat.cmd").exists() else "napcat"


def tcp_rows():
    """这台电脑的 TCP 连接表（IPv4），[(状态, 本地端口, 对方端口, 进程号)]。直接问系统，不起别的程序。"""
    import ctypes
    iph = ctypes.windll.iphlpapi
    size = ctypes.c_ulong(0)
    iph.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 5, 0)  # AF_INET, TCP_TABLE_OWNER_PID_ALL
    buf = ctypes.create_string_buffer(size.value + 8192)
    size = ctypes.c_ulong(len(buf))
    if iph.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 5, 0):
        return []
    rows = []
    for i in range(struct.unpack_from("I", buf, 0)[0]):
        st, _la, lp, _ra, rp, pid = struct.unpack_from("6I", buf, 4 + i * 24)
        rows.append((st, socket.ntohs(lp & 0xffff), socket.ntohs(rp & 0xffff), pid))
    return rows


def ws_pids(rows, port):
    """连着 ws 端口的两头：(大脑那头的进程, 登 QQ 那头的进程)。"""
    est = [r for r in rows if r[0] == 5]
    return {r[3] for r in est if r[1] == port}, {r[3] for r in est if r[2] == port}


FAST = {}


def fast_online(now):
    """她好好连着时不用每分钟起一次 PowerShell（打游戏时别抢资源）：
    上次完整确认在线的那两个程序之间的连接还在，就还算在线。隔 15 分钟或者连接一变，才完整查。"""
    if not IS_WIN or not FAST or now - FAST["at"] > FULL_EVERY:
        return False
    try:
        srv, cli = ws_pids(tcp_rows(), FAST["port"])
    except (OSError, ValueError, struct.error, AttributeError):
        return False
    return bool(srv & FAST["srv"] and cli & FAST["cli"])


def remember_online(port, now):
    FAST.clear()
    if IS_WIN:
        try:
            srv, cli = ws_pids(tcp_rows(), port)
        except (OSError, ValueError, struct.error, AttributeError):
            return
        if srv and cli:
            FAST.update(port=port, srv=srv, cli=cli, at=now)


def local_status(d):
    now = time.time()
    online = fast_online(now)
    if not online:
        import backend_state
        st = backend_state.resolve(d, repair=False)
        if st.get("ambiguous"):
            return "unknown", "好几个实例认不准"
        if st.get("state") != "up":
            FAST.clear()
            return "brain-off", "大脑没开"
        online = bool(st.get("online"))
        if online:
            remember_online(st.get("ws_port") or 6199, now)
        else:
            FAST.clear()
    if relogin_running(d):
        return "busy", "正在重新登录"
    # 被踢后 NapCat 常常还连着大脑，光看连接看不出来，要看它自己的日志。换成 SnowLuma 后旧日志不算数
    if protocol(d) == "napcat":
        log = napcat_log(d)
        if log and kicked_in(tail(log)):
            return "kicked", "NapCat 日志里最后一件事是被踢下线"
    return ("online", "") if online else ("offline", "小号没连上大脑")


# ---------------------------------------------------------------- 云端：在不在线

def ssh_exe():
    if IS_WIN:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        for c in (os.path.join(root, "System32", "OpenSSH", "ssh.exe"), os.path.join(root, "Sysnative", "OpenSSH", "ssh.exe")):
            if os.path.exists(c):
                return c
    return "ssh"


def cloud_status(d):
    """问服务器一次。连不上一律 unknown：多半是这台电脑没网，不拿这个吓他。"""
    d = Path(d)
    kv = read_kv(d / "private" / "cloud.txt")
    host, user = kv.get("host") or kv.get("ip") or "", kv.get("user") or "root"
    if not re.fullmatch(r"[A-Za-z0-9.:\-\[\]]{1,255}", host) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]{0,31}", user):
        return "unknown", "cloud.txt 没写对"
    key = kv.get("key") or "cloud_key"
    key = key if os.path.isabs(key) else str(d / "private" / key)
    rdir = kv.get("dir") or "/opt/dafeiyu"
    port = kv.get("port") if str(kv.get("port", "")).isdigit() else "22"
    remote = HERE / "workbench" / "cloud_remote.py"
    kh = str(d / "private" / "cloud_known_hosts").replace("\\", "/")
    sudo = "" if user == "root" else "sudo -n "
    cmd = [ssh_exe(), "-i", key, "-p", port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-o", "StrictHostKeyChecking=accept-new",
           "-o", f'UserKnownHostsFile="{kh}"', "-o", "LogLevel=ERROR", f"{user}@{host}",
           "P=$(command -v python3 || echo /usr/libexec/platform-python); " + sudo + "env PYTHONIOENCODING=utf-8 $P - qqstate --dir " + shlex.quote(rdir)]
    try:
        p = subprocess.run(cmd, input=remote.read_bytes(), capture_output=True, timeout=45, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return "unknown", "连服务器出错"
    line = next((ln for ln in reversed(p.stdout.decode("utf-8", "replace").splitlines()) if ln.startswith("DFY_JSON ")), "")
    try:
        r = json.loads(line[len("DFY_JSON "):]) if line else {}
    except ValueError:
        r = {}
    if not r.get("ok"):
        return "unknown", "服务器没回话"
    return cloud_verdict(r)


def cloud_verdict(r):
    if not r.get("brain"):
        return "brain-off", "服务器上的大脑没开"
    if r.get("kicked"):
        return "kicked", "NapCat 日志里最后一件事是被踢下线"
    return ("online", "") if r.get("conns") else ("offline", "小号没连上大脑")


# ---------------------------------------------------------------- 弹通知

def toast(d, kind):
    """调 qq-toast.ps1（要 Windows PowerShell 5.1 才有通知的接口）。返回 (成没成, 说明)。"""
    if not IS_WIN:
        print(f"[toast] {kind}")
        return True, "不是 Windows，只打印"
    root = os.environ.get("SystemRoot", r"C:\Windows")
    ps = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        p = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(HERE / "qq-toast.ps1"), "-Dir", str(d), "-Kind", kind],
                           capture_output=True, timeout=40, creationflags=NO_WINDOW, cwd=tempfile.gettempdir())
    except (OSError, subprocess.SubprocessError) as e:
        return False, type(e).__name__
    out = p.stdout.decode("utf-8", "replace").strip().splitlines()
    return p.returncode == 0, (out[-1] if out else f"退出码 {p.returncode}")


# ---------------------------------------------------------------- 什么时候提醒

class Watch:
    """只管时间，不碰外面，方便测。tick 返回要弹的种类（kicked / offline），不弹返回 None。"""

    def __init__(self, interval):
        self.interval = interval
        self.last_tick = 0
        self.since = 0      # 从什么时候开始算掉线
        self.first = 0      # 这次掉线第一回提醒的时间
        self.sent = 0       # 这次掉线提醒了几回
        self.seen_kick = 0  # 连续几轮看到被踢

    def reset(self):
        self.since = self.first = self.sent = self.seen_kick = 0

    def tick(self, status, now):
        slept = self.last_tick and now - self.last_tick > max(self.interval * 3, 300)
        self.last_tick = now
        if status == "online":
            self.reset()
            return None
        if status not in ("offline", "kicked"):
            # 大脑没开、查不到、正在登：这段不算掉线。已经提醒过的次数留着，登不上时不重新吵满 3 回
            self.since = self.seen_kick = 0
            return None
        if slept:
            self.since = now
            self.seen_kick = 0
        self.since = self.since or now
        self.seen_kick = self.seen_kick + 1 if status == "kicked" else 0
        if self.sent >= len(REMIND):
            return None
        due = self.seen_kick >= 2 or now - self.since >= GRACE  # 被踢确认两轮，别的等满 3 分钟
        if not due:
            return None
        if self.sent and now - self.first < REMIND[self.sent]:
            return None
        self.first = self.first or now
        self.sent += 1
        return status


# ---------------------------------------------------------------- 一直跑

def write_state(d, **kw):
    p = Path(d) / "logs" / "qq-watch.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({**kw, "pid": os.getpid(), "at": time.time()}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def log(d, msg):
    p = Path(d) / "logs" / "qq-watch.log"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > 200_000:
            p.write_text("", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as fp:
            fp.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def take_lock(d):
    """同一个工作目录只跑一份。锁跟着进程走，崩了自动释放。"""
    p = Path(d) / "logs" / "qq-watch.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    fp = open(p, "a+b")
    try:
        if IS_WIN:
            import msvcrt
            fp.seek(0, 2)
            if not fp.tell():
                fp.write(b"0")
                fp.flush()
            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return None
    return fp


def status_of(d):
    return cloud_status(d) if is_cloud(d) else local_status(d)


def run(d):
    lock = take_lock(d)
    if not lock:
        return 0
    try:
        os.chdir(tempfile.gettempdir())  # 当前目录不留在工作目录里：卸载、挪文件夹时别占着
    except OSError:
        pass
    mode = "cloud" if is_cloud(d) else "local"
    w = Watch(INTERVAL[mode])
    log(d, f"起来了（{mode}）")
    off = Path(d) / "private" / "qq-watch-off.txt"
    while True:
        try:
            if off.exists():
                w.reset()
                write_state(d, state="off", note="开关关着")
            else:
                st, why = status_of(d)
                kind = w.tick(st, time.time())
                note = why
                if kind:
                    ok, res = toast(d, kind)
                    log(d, f"提醒第 {w.sent} 回（{kind}，{why}）：{res}")
                    note = why + "；" + ("已弹通知" if ok else "通知没弹出来：" + res)
                write_state(d, state=st, note=note, since=w.since, sent=w.sent, mode=mode)
        except Exception as e:  # noqa: BLE001  后台程序不能因为一次出错就没了
            log(d, f"这一轮出错：{type(e).__name__} {e}")
        time.sleep(w.interval)


def main():
    ap = argparse.ArgumentParser(description="大肥鱼掉线提醒")
    ap.add_argument("--dir", default="")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    d = a.dir or (str(HERE.parent) if HERE.name.lower() == "tools" else "")
    if not d:
        ap.error("要给 --dir <工作目录>")
    d = os.path.abspath(d)
    if a.test:
        ok, res = toast(d, "test")
        print(res)
        return 0 if ok else 1
    if a.once:
        st, why = status_of(d)
        print(json.dumps({"state": st, "why": why, "mode": "cloud" if is_cloud(d) else "local"}, ensure_ascii=False))
        return 0
    return run(d)


if __name__ == "__main__":
    sys.exit(main())
