"""本机大脑的共同口径：按实例进程认端口，不把 cmd_config 的默认端口当运行端口。

PowerShell、工作台、桌宠桥共用。只查当前 Windows 用户；不读写启动器数据库，
不启动实例。backend.txt 只改 url，原字节先备份；多实例认不准时不猜。
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid

try:
    import psutil
except ImportError:
    psutil = None

_LOCK = threading.RLock()


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def js(path):
    try:
        value = json.loads(read(path))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def kv(path):
    return {m[1].lower(): m[2].strip() for m in
            re.finditer(r"(?m)^\s*([^\s:：]+)\s*[:：]\s*(.*)$", read(path))}


def norm(path):
    return os.path.normcase(os.path.abspath(str(path))).replace("\\", "/").rstrip("/").lower()


def within(path, root):
    return bool(path) and norm(path).startswith(norm(root) + "/")


def snapshot():
    """procs 只含当前用户；端口表可含全机，但必须再按 pid 归属匹配。"""
    out = {"procs": [], "listen": {}, "est": []}
    if psutil:
        me = psutil.Process().username().lower()
        for p in psutil.process_iter():
            try:
                if p.username().lower() != me:
                    continue
                out["procs"].append(dict(pid=p.pid, name=p.name(), exe=p.exe(),
                                         cmd=" ".join(p.cmdline()), create=p.create_time()))
            except (psutil.Error, OSError):
                continue
        try:
            for c in psutil.net_connections("tcp"):
                if c.status == "LISTEN" and c.laddr:
                    out["listen"][c.laddr.port] = c.pid
                elif c.status == "ESTABLISHED" and c.laddr and c.raddr:
                    out["est"].append((c.laddr.port, c.raddr.port, c.pid))
            return out
        except (psutil.Error, OSError):
            pass
    if os.name == "nt":
        # 无 psutil 时仍按会话筛进程，不把 15000 等其他用户的实例认进来。
        code = r"""
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$s=(Get-Process -Id $PID).SessionId
$p=@(Get-CimInstance Win32_Process | Where-Object {$_.SessionId -eq $s} | ForEach-Object {
 @{pid=$_.ProcessId;name=$_.Name;exe=$_.ExecutablePath;cmd=$_.CommandLine;create=$(if($_.CreationDate){[int64](($_.CreationDate.ToUniversalTime())-[datetime]'1970-01-01').TotalSeconds}else{0})}
})
$t=@(Get-NetTCPConnection -ErrorAction SilentlyContinue | Select-Object LocalPort,RemotePort,OwningProcess,State)
@{procs=$p;tcp=$t}|ConvertTo-Json -Depth 4 -Compress
"""
        try:
            p = subprocess.run(["powershell.exe", "-NoProfile", "-Command", code],
                               capture_output=True, timeout=30, creationflags=0x08000000)
            j = json.loads(p.stdout.decode("utf-8-sig"))
            out["procs"] = j.get("procs", [])
            for c in j.get("tcp", []):
                if c["State"] in (2, "Listen"):
                    out["listen"][c["LocalPort"]] = c["OwningProcess"]
                elif c["State"] in (5, "Established"):
                    out["est"].append((c["LocalPort"], c["RemotePort"], c["OwningProcess"]))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return out


def roots():
    override = js(Path(os.environ.get("APPDATA", "")) /
                  "com.github.raven95676.astrbot-launcher" / ".data-dir.json").get("data_dir")
    return [Path(override)] if override else [Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".astrbot_launcher"]


def ws_port(config, token=""):
    platforms = [p for p in config.get("platform", []) if isinstance(p, dict)
                 and p.get("type") == "aiocqhttp" and p.get("enable", True)]
    matches = [p for p in platforms if token and p.get("ws_reverse_token") == token]
    if not matches:
        matches = [p for p in platforms if p.get("id") == "大肥鱼"] or platforms
    ports = set()
    for p in matches:
        try:
            port = int(p.get("ws_reverse_port", 6199))
            if 0 < port < 65536:
                ports.add(port)
        except (ValueError, TypeError):
            pass
    return ports.pop() if len(ports) == 1 else 6199


def dashboard_page(port):
    """只探实例 pid 已在听的端口，不带密码。排除插件开的其他 HTTP 服务。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/", timeout=0.7) as r:
            return "astrbot" in r.read(65536).decode("utf-8", "ignore").lower()
    except (OSError, ValueError):
        return False


def repair_url(path, url):
    """跨进程互斥；逐字节保留凭证、BOM 和换行。返回是否实际改过。"""
    lock = path.with_name("backend-url.lock")
    with _LOCK:
        try:
            guard = lock.open("a+b")
            if os.name == "nt":
                import msvcrt
                guard.seek(0, 2)
                if not guard.tell():
                    guard.write(b"0"); guard.flush()
                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if "guard" in locals():
                guard.close()
            return False
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig")
            old = kv(path).get("url", "")
            if old == url or not old:
                return False
            changed = re.sub(r"(?im)^(\s*url\s*[:：]\s*)[^\r\n]*", lambda m: m[1] + url, text, count=1)
            if changed == text:
                return False
            backup = path.with_name(f"backend.txt.before-port-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.bak")
            with backup.open("xb") as f:
                f.write(raw)
            tmp = path.with_name("backend.txt." + uuid.uuid4().hex + ".tmp")
            try:
                tmp.write_bytes((b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + changed.encode("utf-8"))
                if path.read_bytes() != raw:  # 用户正好在记事本里改过时，不覆盖他的改动。
                    return False
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
            return True
        except (OSError, UnicodeError):
            return False
        finally:
            guard.close()  # OS 锁在退出/进程崩溃时自动释放，磁盘上的空锁文件不影响重跑。


def resolve(directory, *, repair=True, snap=None, launcher_roots=None, check_page=dashboard_page):
    d = Path(directory)
    backend = d / "private" / "backend.txt"
    url = kv(backend).get("url", "http://127.0.0.1:6185").split()[0].rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(url if "://" in url else "http://" + url)
        port = parsed.port or 6185
    except ValueError:
        port = 6185
        parsed = urllib.parse.urlsplit("http://127.0.0.1:6185")
    s = snapshot() if snap is None else snap
    procs, listening = s["procs"], s["listen"]
    r = dict(url=url, port=port, ws_port=6199, kind="cli" if (d / "start-astrbot.cmd").exists() else "launcher",
             instance="", data_dir="", state="off", launcher_running=any(
                 re.search(r"astrbot.?launcher", p.get("name", ""), re.I) for p in procs),
             pid=0, started=0, online=False, repaired=False, ambiguous=False)
    token = read(d / "private" / "napcat-ws-token.txt").strip()
    owned = []
    if r["kind"] == "cli":
        r["data_dir"] = str(d / "astrbot" / "data")
        config = js(Path(r["data_dir"]) / "cmd_config.json")
        # 她自己是 astrbot.exe 那条。用她的 Python 跑我们的脚本（工作台、提醒、搬家、这个探针）也在 uv 下面、路径也带 astrbot，不算她开着
        owned = [p for p in procs if within(p.get("exe", ""), d / "uv")
                 and "astrbot" in p.get("cmd", "").lower()
                 and not re.search(r'\.pyw?(?:["\s]|$)', p.get("cmd", ""), re.I)]
        # 命令行版照旧认 backend.txt；不会自动换成别的安装的端口。
        if port in listening:
            r["state"] = "up"
            r["pid"] = listening[port]
    else:
        cands = []
        for root in roots() if launcher_roots is None else launcher_roots:
            for inst in (Path(root) / "instances").glob("*"):
                cfg = js(inst / "core" / "data" / "cmd_config.json")
                if cfg:
                    cands.append((inst, cfg))
        pinned = read(d / "private" / "backend-instance.txt").strip()
        matching = [(i, c) for i, c in cands if pinned and norm(i) == norm(pinned)]
        if not matching and not pinned and token:
            matching = [(i, c) for i, c in cands if any(
                isinstance(p, dict) and p.get("type") == "aiocqhttp" and p.get("ws_reverse_token") == token
                for p in c.get("platform", []))]
        if not matching and not pinned:
            for inst, cfg in cands:
                ip = [p for p in procs if norm(inst / "core" / "main.py") in (p.get("cmd") or "").replace("\\", "/").lower()]
                if listening.get(port) in {p["pid"] for p in ip}:
                    matching.append((inst, cfg))
        if not matching and not pinned and len(cands) == 1:
            matching = cands
        if len(matching) != 1:
            r["ambiguous"] = bool(cands)
            return r
        inst, config = matching[0]
        r["instance"], r["data_dir"] = str(inst), str(inst / "core" / "data")
        if repair and not pinned:
            try:
                with (d / "private" / "backend-instance.txt").open("x", encoding="utf-8") as f:
                    f.write(str(inst))
            except OSError:
                pass
        main = norm(inst / "core" / "main.py")
        owned = [p for p in procs if main in (p.get("cmd") or "").replace("\\", "/").lower()]
        pids = {p["pid"] for p in owned}
        ws = ws_port(config, token)
        ports = [p for p, pid in listening.items() if pid in pids and p != ws]
        actual = port if port in ports else next((p for p in ports if check_page(p)), None)
        if actual:
            r.update(port=actual, pid=listening[actual], state="up")
            # 仅本机后台允许自动校正，远端地址不探也不替换。
            if actual != port and parsed.hostname in ("127.0.0.1", "localhost", "::1"):
                host = "[::1]" if parsed.hostname == "::1" else parsed.hostname
                r["url"] = urllib.parse.urlunsplit((parsed.scheme or "http", f"{host}:{actual}", parsed.path, parsed.query, parsed.fragment))
                if repair:
                    r["repaired"] = repair_url(backend, r["url"])
    r["ws_port"] = ws_port(config, token)
    r["started"] = next((p.get("create", 0) for p in procs if p["pid"] == r["pid"]), 0)
    if r["state"] != "up" and owned:
        newest = max(p.get("create", 0) for p in owned)
        r["state"] = "starting" if time.time() - newest < 300 else "stuck"
    proto = read(d / "private" / "protocol.txt").strip().lower()
    root = d / ("SnowLuma" if proto == "snowluma" else "NapCat")
    clients = {p["pid"] for p in procs if within(p.get("exe", ""), root)}
    # ws 服务器的连接、其他实例和其他用户的连接，都不能点亮这个小号。
    r["online"] = (r["state"] == "up" and listening.get(r["ws_port"]) in ({r["pid"]} | {p["pid"] for p in owned})
                   and any(remote == r["ws_port"] and pid in clients for _, remote, pid in s["est"]))
    return r


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--no-repair", action="store_true")
    a = ap.parse_args()
    print(json.dumps(resolve(a.dir, repair=not a.no_repair), ensure_ascii=False))
