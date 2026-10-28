# -*- coding: utf-8 -*-
"""大肥鱼工作台的本机小服务。只用 Python 标准库（AstrBot 自带的 Python 里有 psutil 就顺手用它看进程和端口）。

谁来跑它：桌面「大肥鱼工作台」→ tools\\workbench.cmd → workbench\\open.ps1 → AstrBot 自带的 pythonw.exe 跑本文件 --open。
    已经有一个在跑（logs\\workbench\\server.json 里的端口能应答）就只开窗口，不再起第二个。
它做什么：只听 127.0.0.1，发同目录的 index.html 和 assets\ 里的图、字体（GET /assets/<文件名>，只认 .webp、.woff2），再给页面提供
    GET  /api/status          各项状态（页面每 10 秒问一次）
    GET  /api/logs?src=brain  最近日志（已打码）
    GET  /api/job/<id>        长动作的进度
    POST /api/action/<名字>    修复、重启、补装、求助信息、备份……
安全：每次启动生成随机 token，/api/* 都要带 X-DFY-Token；校验 Host 头（防 DNS rebinding），POST 再校验 Origin。
    private\\ 里的 key、口令、密码只在这里读，任何响应、日志都不带；QQ 号一律打码。
    3 分钟没人来问状态、也没有在跑的动作，就自己退出。
不做的事：不改网络和代理设置；不碰工作目录以外的程序（用户自己的桌面 QQ 不关不动）。
    升级只管安装 Skill 和工作台自己（换文件，旧版先备份到 <工作目录>\\旧版\\）；AstrBot、SnowLuma、配置包里的插件只提醒，交给 Agent 升。
云端模式：她装在云服务器上（private\\cloud.txt 写着服务器、这台电脑上没有 AstrBot）时，页面一样，状态、日志、动作都转给同目录的 cloud.py，
    它用系统自带的 ssh 去服务器上问（服务器那头跑 cloud_remote.py）。这时用的 Python 是 finish.ps1 放进 <工作目录>\\python\\ 的便携版。

用法：pythonw.exe server.py [--dir <工作目录>] [--open | --no-open] [--port 26185]
"""
import argparse
import hmac
import http.server
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil  # AstrBot 自带；没有也能跑，只是慢一点
except Exception:  # noqa: BLE001
    psutil = None

if sys.stdout is None:  # pythonw 没有控制台
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

VERSION = "1.3.0"
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # 便携版 Python（python312._pth）不把脚本所在的文件夹放进 sys.path
    sys.path.insert(0, str(HERE))
import cloud  # noqa: E402  云端模式
ASSETS = HERE / "assets"  # 页面用的图和字体；只发这个文件夹里、下面这几种扩展名的文件
ASSET_TYPES = {".webp": "image/webp", ".woff2": "font/woff2"}
SCRIPTS = HERE.parent  # Skill 里是 scripts\，装好后是 <工作目录>\tools\（finish.ps1 把要用的 .ps1 一起拷过去）
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
DEFAULT_PORT = 26185
PET_PORT = 26199  # 大肥鱼桥（桌宠走它跟她说话，setup-pet.ps1 装的）；6199 是 QQ 连大脑用的，别撞
GH_PROXIES = ["https://gh.dpik.top/", "https://edgeone.gh-proxy.com/", "https://hk.gh-proxy.org/", "https://gh-proxy.org/", ""]
SKILL_REPO = "zexuanw958-svg/dafeiyu-config-pack"  # 安装 Skill 发在这个仓库的 Releases，附件 dafeiyu-install.zip + skill-version.json
MEME_NAMES = {"blue-fat-fish-meme-pack": "蓝色大肥鱼表情包", "deepseek-fanart-meme-pack": "DeepSeek 二创表情包"}

DIR = Path(".")
STATE = Path(".")
TOKEN = ""
PORT = 0
ALLOWED_HOSTS = set()
OPTS = argparse.Namespace(no_clipboard=False, idle=180)
LAST_SEEN = time.time()
LOCK = threading.RLock()
CACHE = {}       # 内存缓存：key -> (时间, 值)
SERVER = None
CLOUD = False    # 云端模式：main() 里认
cloud.S = sys.modules[__name__]


# ---------------------------------------------------------------- 小工具

def now():
    return time.time()


def log(msg):
    """服务自己的日志：只记事件，不记秘密（写之前再打一遍码）。"""
    try:
        f = STATE / "server.log"
        if f.exists() and f.stat().st_size > 1_000_000:
            f.write_text("", encoding="utf-8")
        with f.open("a", encoding="utf-8") as fp:
            fp.write(f"{datetime.now():%m-%d %H:%M:%S} {redact(str(msg))}\n")
    except Exception:  # noqa: BLE001
        pass


def read_text(p, default=""):
    try:
        return Path(p).read_text(encoding="utf-8-sig").strip()
    except Exception:  # noqa: BLE001
        return default


def read_kv(p):
    d = {}
    for line in read_text(p).splitlines():
        line = line.replace("：", ":")
        if ":" in line:
            k, v = line.split(":", 1)
            if v.strip():
                d[k.strip().lower()] = v.strip()
    return d


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def write_json(p, obj):
    p = Path(p)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def cached(key, ttl, fn):
    with LOCK:
        hit = CACHE.get(key)
        if hit and now() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with LOCK:
        CACHE[key] = (now(), val)
    return val


def drop_cache(*keys):
    with LOCK:
        for k in keys:
            CACHE.pop(k, None)


def decode(b):
    if b is None:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def run(cmd, timeout=30, env=None):
    """跑一个命令拿输出（不弹窗口）。返回 (退出码, 文本)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW, env=env)
        return p.returncode, decode(p.stdout) + decode(p.stderr)
    except subprocess.TimeoutExpired:
        return -1, "超时了"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}"


def powershell(script_path, *args, timeout=120):
    return run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path), *[str(a) for a in args]], timeout=timeout)


def mask_qq(s):
    s = str(s or "").strip()
    if len(s) >= 5 and s.isdigit():
        return s[:2] + "****" + s[-2:]
    return "****" if s else ""


def ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4])


def dur_text(sec):
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} 天 {h} 小时"
    if h:
        return f"{h} 小时 {m} 分"
    if m:
        return f"{m} 分钟"
    return "不到 1 分钟"


# ---------------------------------------------------------------- 打码

_SECRET_VALUES = []
_QQ_VALUES = []


def load_secret_values():
    """private\\ 里所有像秘密的值：后台密码、key、口令……用来在日志里原样替换成 ****。账号名、网址、协议名不算。"""
    vals, qq = set(), set()
    pdir = DIR / "private"
    try:
        files = list(pdir.iterdir()) if pdir.is_dir() else []
    except Exception:  # noqa: BLE001
        files = []
    for f in files:
        if not f.is_file() or f.stat().st_size > 20000:
            continue
        if f.name.lower() == "protocol.txt":
            continue
        if f.name.lower() == "group.txt" or f.name.lower().endswith("-qq.txt"):
            qq.update(re.findall(r"\d{5,}", read_text(f)))  # QQ 号、群号只留头尾（方便分清大号小号），5、6 位的老号也打
            continue
        raw = read_text(f)
        kv = read_kv(f)
        if kv and len(kv) >= 1 and all(":" in ln.replace("：", ":") for ln in raw.splitlines() if ln.strip()):
            for k, v in kv.items():
                if "url" in k or k in ("username", "user", "账号", "用户名") or (f.name.lower() == "cloud.txt" and k != "host" and k != "ip"):
                    continue
                if len(v) >= 4:
                    vals.add(v)
        elif len(raw) >= 4:
            vals.add(raw)
    global _SECRET_VALUES, _QQ_VALUES
    _SECRET_VALUES = sorted(vals, key=len, reverse=True)
    _QQ_VALUES = sorted(qq, key=len, reverse=True)


_RX = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-****"),
    (re.compile(r"eyJ[\w\-]{10,}\.[\w\-]{10,}\.[\w\-]{5,}"), "****"),
    (re.compile(r"(?i)(bearer\s+)[\w.\-~+/=]+"), r"\1****"),
    (re.compile(r"(?i)((?:access_?token|api_?key|apikey|secret|password|passwd|pwd|token|密码)[\"']?\s*[:=：]\s*[\"']?)[^\s\"',;&}]+"), r"\1****"),
    (re.compile(r"u_[A-Za-z0-9_\-]{16,}"), "u_****"),
    (re.compile(r"(?i)([A-Z]:\\Users\\)[^\\/:*?\"<>|\r\n]+"), r"\1<用户名>"),
    (re.compile(r"(?<![\d.])\d{7,}(?!\d)"), lambda m: _mask_long_digits(m.group(0))),  # 7 位以上的数字（QQ 号、群号）
]
_DATE8 = re.compile(r"20\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])")


def _mask_long_digits(d):
    if len(d) == 8 and _DATE8.fullmatch(d):  # 20261028 这种日期（备份文件名里有）不打
        return d
    return d[:2] + "****" + d[-2:]
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def redact(s):
    s = str(s)
    for v in _SECRET_VALUES:
        if v in s:
            s = s.replace(v, "****")
    for v in _QQ_VALUES:
        if v in s:
            s = re.sub(r"(?<!\d)" + v + r"(?!\d)", mask_qq(v), s)
    for rx, rep in _RX:
        s = rx.sub(rep, s)
    return s


# ---------------------------------------------------------------- 端口和进程

def net_snapshot():
    """返回 (在听的端口 -> pid, [(本地端口, 对方端口, pid) 已连接])。"""
    def get():
        listen, est = {}, []
        if psutil:
            try:
                for c in psutil.net_connections("tcp"):
                    if c.status == psutil.CONN_LISTEN and c.laddr:
                        listen.setdefault(c.laddr.port, c.pid)
                    elif c.status == psutil.CONN_ESTABLISHED and c.laddr and c.raddr:
                        est.append((c.laddr.port, c.raddr.port, c.pid))
                return listen, est
            except Exception:  # noqa: BLE001
                pass
        if IS_WIN:
            _, out = run(["netstat", "-ano", "-p", "TCP"], timeout=15)
            for line in out.splitlines():
                p = line.split()
                if len(p) >= 5 and p[0] == "TCP":
                    try:
                        lport = int(p[1].rsplit(":", 1)[1]); rport = int(p[2].rsplit(":", 1)[1]); pid = int(p[4])
                    except (ValueError, IndexError):
                        continue
                    if p[3] == "LISTENING":
                        listen.setdefault(lport, pid)
                    elif p[3] == "ESTABLISHED":
                        est.append((lport, rport, pid))
        return listen, est
    return cached("net", 2, get)


def listening(port):
    listen, _ = net_snapshot()
    if port in listen:
        return True
    if not IS_WIN and not psutil:  # 开发机上没有端口表，退回直接连一下
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            return False
    return False


def qq_online():
    _, est = net_snapshot()
    return any(r == 6199 for _, r, _ in est)


def process_list():
    """[{pid, ppid, name, exe, create}]，看不了的进程 exe 是空的。"""
    def get():
        out = []
        if psutil:
            for p in psutil.process_iter(["pid", "ppid", "name", "exe", "create_time"]):
                i = p.info
                out.append({"pid": i["pid"], "ppid": i.get("ppid"), "name": i.get("name") or "", "exe": i.get("exe") or "", "create": i.get("create_time") or 0})
            return out
        if IS_WIN:
            code, txt = run(["powershell.exe", "-NoProfile", "-Command",
                             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,@{n='C';e={if($_.CreationDate){[int64](($_.CreationDate.ToUniversalTime())-[datetime]'1970-01-01').TotalSeconds}}} | ConvertTo-Json -Compress"], timeout=30)
            try:
                for i in json.loads(txt):
                    out.append({"pid": i["ProcessId"], "ppid": i["ParentProcessId"], "name": i["Name"] or "", "exe": i["ExecutablePath"] or "", "create": i.get("C") or 0})
            except Exception:  # noqa: BLE001
                pass
        return out
    return cached("procs", 3, get)


def cmdline(pid):
    if psutil:
        try:
            return " ".join(psutil.Process(pid).cmdline())
        except Exception:  # noqa: BLE001
            return ""
    if IS_WIN:
        _, txt = run(["powershell.exe", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"], timeout=15)
        return txt.strip()
    return ""


def my_chain():
    pids = {os.getpid()}
    if psutil:
        try:
            for p in psutil.Process().parents():
                pids.add(p.pid)
        except Exception:  # noqa: BLE001
            pass
    else:
        pids.add(os.getppid())
    return pids


def norm(p):
    return os.path.normcase(os.path.abspath(str(p)))


def under(path, root):
    if not path:
        return False
    a, b = norm(path), norm(root)
    return a.startswith(b.rstrip("\\/") + os.sep)


def procs_under(root):
    return [p for p in process_list() if under(p["exe"], root)]


def kill_pids(pids):
    keep = my_chain()
    n = 0
    for pid in pids:
        if pid in keep:
            continue
        try:
            if psutil:
                psutil.Process(pid).kill()
            elif IS_WIN:
                run(["taskkill", "/F", "/PID", str(pid)], timeout=15)
            n += 1
        except Exception:  # noqa: BLE001
            pass
    drop_cache("procs", "net")
    return n


def kill_under(root):
    """只结束 root（工作目录里的某个子文件夹）里的程序。root 必须在工作目录里面，工作目录本身不行。"""
    if not under(root, DIR):
        raise RuntimeError("只许停工作目录里的程序")
    return kill_pids([p["pid"] for p in procs_under(root)])


def astrbot_pids():
    """工作目录里的 AstrBot：命令行是 `astrbot.exe run` 的那几个（uv 的启动器 + 两层 python），加上占着后台端口的那个。导入工具、本服务都不算。"""
    pids = set()
    listen, _ = net_snapshot()
    lp = listen.get(backend_port())
    for p in process_list():
        if not under(p["exe"], DIR):
            continue
        if p["pid"] == lp or (p["name"].lower() in ("astrbot.exe", "python.exe") and re.search(r"astrbot(\.exe)?\"?\s+run\b", cmdline(p["pid"]))):
            pids.add(p["pid"])
    return pids - my_chain()


def launch_cmd(cmdfile, visible=False, cwd=None):
    """在新的控制台窗口里跑一个 .cmd（默认最小化，不抢焦点），跟本服务脱钩，本服务退出了它照样跑。"""
    cmdfile = Path(cmdfile)
    if not IS_WIN:
        raise RuntimeError("只在 Windows 上能用")
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 1 if visible else 7  # SW_SHOWNORMAL / SW_SHOWMINNOACTIVE
    base = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    line = f'cmd.exe /d /c ""{cmdfile}""'
    for flags in (base | 0x01000000, base):  # 0x01000000 = CREATE_BREAKAWAY_FROM_JOB，不允许就不加
        try:
            subprocess.Popen(line, cwd=str(cwd or cmdfile.parent), creationflags=flags, startupinfo=si, close_fds=True)
            return
        except OSError:
            continue
    raise RuntimeError("没打开")


def launch_exe(exe, args=(), cwd=None, env=None, hidden=False):
    """hidden：给它一个看不见的控制台（CREATE_NO_WINDOW），它再起的子程序也跟着看不见。
    不加的话是 DETACHED_PROCESS，没有控制台；命令行程序再起子程序时，Windows 会给子程序新开一个终端窗口
    （10-29 测试机：一键修复起 ollama.exe serve，闪出一个黑框挡住工作台）。"""
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | (NO_WINDOW if hidden else 0x00000008)) if IS_WIN else 0
    for f in ((flags | 0x01000000) if IS_WIN else 0, flags):
        try:
            subprocess.Popen([str(exe), *args], cwd=str(cwd or Path(exe).parent), creationflags=f, close_fds=True, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue
    raise RuntimeError("没打开")


def known_folder(csidl, fallback):
    if IS_WIN:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(520)
            if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
                return Path(buf.value)
        except Exception:  # noqa: BLE001
            pass
    return Path(fallback)


def startup_dir():
    return known_folder(0x0007, os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"))


def boot_time():
    if psutil:
        try:
            return psutil.boot_time()
        except Exception:  # noqa: BLE001
            pass
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
            return now() - ctypes.windll.kernel32.GetTickCount64() / 1000
        except Exception:  # noqa: BLE001
            pass
    return 0


def wait_until(fn, seconds, job=None, p0=None, p1=None, step=None):
    t0 = now()
    while now() - t0 < seconds:
        drop_cache("net", "procs")
        if fn():
            return True
        if job is not None and p0 is not None:
            job.set(step, p0 + (p1 - p0) * min(1, (now() - t0) / seconds))
        time.sleep(2)
    drop_cache("net", "procs")
    return bool(fn())


# ---------------------------------------------------------------- 工作目录里的信息

def answers():
    a = read_json(DIR / "answers.json", {}) or {}
    return {k: ("" if v is None else str(v)) for k, v in a.items()} if isinstance(a, dict) else {}


def protocol():
    p = read_text(DIR / "private" / "protocol.txt").lower()
    if p in ("napcat", "snowluma"):
        return p
    if (DIR / "start-snowluma.cmd").exists() and not (DIR / "start-napcat.cmd").exists():
        return "snowluma"
    return "napcat"


def backend_port():
    u = read_kv(DIR / "private" / "backend.txt").get("url", "")
    m = re.search(r":(\d+)", u.split("//", 1)[-1])
    return int(m.group(1)) if m else 6185


def launcher_instance():
    """启动器装的 AstrBot：找端口对得上的那个实例文件夹。"""
    roots = []
    j = read_json(Path(os.environ.get("APPDATA", "")) / "com.github.raven95676.astrbot-launcher" / ".data-dir.json", {}) or {}
    if j.get("data_dir"):
        roots.append(Path(j["data_dir"]))
    roots.append(Path(os.environ.get("USERPROFILE", "")) / ".astrbot_launcher")
    cands = []
    for r in roots:
        try:
            cands += [d for d in (r / "instances").iterdir() if (d / "core" / "data" / "cmd_config.json").exists()]
        except Exception:  # noqa: BLE001
            pass
    want = backend_port()
    for c in cands:
        cfg = read_json(c / "core" / "data" / "cmd_config.json", {}) or {}
        if (cfg.get("dashboard") or {}).get("port") == want:
            return c
    return cands[0] if cands else None


def data_dir():
    d = DIR / "astrbot" / "data"
    if d.is_dir():
        return d
    inst = launcher_instance()
    return inst / "core" / "data" if inst else None


def is_cli_install():
    return (DIR / "start-astrbot.cmd").exists()


def snowluma_log():
    try:
        logs = sorted((DIR / "SnowLuma" / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        return logs[0] if logs else None
    except Exception:  # noqa: BLE001
        return None


def napcat_shell():
    try:
        for d in (DIR / "NapCat").glob("NapCat.*.Shell"):
            if (d / "NapCatWinBootMain.exe").exists():
                return d
    except Exception:  # noqa: BLE001
        pass
    return None


def napcat_log():
    sh = napcat_shell()
    if not sh:
        return None
    try:
        logs = sorted(sh.glob("versions/*/resources/app/napcat/logs/*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            return logs[0]
    except Exception:  # noqa: BLE001
        pass
    f = sh / "napcat-run.log"
    return f if f.exists() else None


def tail_lines(path, max_bytes=400_000, n=400):
    try:
        with open(path, "rb") as fp:
            fp.seek(0, 2)
            size = fp.tell()
            fp.seek(max(0, size - max_bytes))
            data = fp.read()
        txt = decode(data)
        lines = [_ANSI.sub("", ln).rstrip("\r") for ln in txt.split("\n")]
        if size > max_bytes:
            lines = lines[1:]
        return [ln for ln in lines if ln.strip()][-n:]
    except Exception:  # noqa: BLE001
        return []


def snowluma_upgrade_suspect():
    """SnowLuma 最新日志里「挂不上 QQ」（retry exhausted / 版本不支持）出现在最后一次挂上之后，多半是 QQ 自动升级了。"""
    f = snowluma_log()
    if not f:
        return False
    good = bad = -1
    for i, ln in enumerate(tail_lines(f, 300_000, 3000)):
        if "pipe connected" in ln or "connected ws://" in ln:
            good = i
        if re.search(r"(?i)retry exhausted|(qq|version)[^\n]{0,40}(unsupported|not supported)", ln):
            bad = i
    return bad > good


def qq_version_desktop():
    exe = ""
    m = re.search(r'start\s+""\s+"([^"]+)"', read_text(DIR / "start-qq.cmd"))
    if m:
        exe = m.group(1)
    for base in ([Path(exe).parent] if exe else []) + [Path(os.environ.get("ProgramFiles", "")) / "Tencent" / "QQNT", Path(os.environ.get("ProgramFiles(x86)", "")) / "Tencent" / "QQNT"]:
        try:
            vs = [d.name for d in (base / "versions").iterdir() if re.match(r"^\d+\.\d+\.\d+-\d+$", d.name)]
            if vs:
                return max(vs, key=ver_tuple)
        except Exception:  # noqa: BLE001
            continue
    return ""


def astrbot_version():
    try:
        from importlib import metadata
        return metadata.version("astrbot")
    except Exception:  # noqa: BLE001
        pass
    for sp in [DIR / "uv" / "tools" / "astrbot" / "Lib" / "site-packages"]:
        try:
            for d in sp.glob("astrbot-*.dist-info"):
                return d.name[len("astrbot-"):-len(".dist-info")]
        except Exception:  # noqa: BLE001
            pass
    return ""


def snowluma_version():
    return str((read_json(DIR / "SnowLuma" / "release.json", {}) or {}).get("version") or "")


def breeze_version():
    try:
        for d in (DIR / "breeze-tts" / "bin").iterdir():
            m = re.search(r"v(\d+\.\d+\.\d+)", d.name)
            if m:
                return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return ""


def pack_version():
    return str((read_json(DIR / "dafeiyu-config-pack" / "settings" / "pack.json", {}) or {}).get("version") or "")


def ollama_exe():
    m = re.search(r'"([^"]*ollama app\.exe)"', read_text(DIR / "start-ollama.cmd"), re.I)
    for p in ([m.group(1)] if m else []) + [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama app.exe"), str(DIR / "Ollama" / "ollama app.exe")]:
        if p and Path(p).exists():
            return Path(p)
    return None


def launch_ollama():
    """只起服务（ollama.exe serve），不开托盘程序：托盘程序每次启动都弹它自己的聊天窗口（10-28 测试机：打完游戏恢复时弹出来）。
    OLLAMA_MODELS、OLLAMA_KEEP_ALIVE 从注册表现读：装 Ollama 时才设的，工作台自己的环境里可能还没有。"""
    app = ollama_exe()
    cli = app.parent / "ollama.exe" if app else None
    if not cli or not cli.exists():
        return launch_exe(app)
    env = dict(os.environ)
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                for name in ("OLLAMA_MODELS", "OLLAMA_KEEP_ALIVE"):
                    try:
                        env[name] = str(winreg.QueryValueEx(k, name)[0])
                    except OSError:
                        pass
        except OSError:
            pass
    launch_exe(cli, ("serve",), env=env, hidden=True)


def game_file():
    return DIR / "logs" / "game-mode.json"  # DIR 在 main() 里才定下来，不能在模块里先算好


def game_mode():
    """打游戏模式开着没。电脑重启过（开机自启又把语音、记忆开起来了）就不算。"""
    g = read_json(game_file(), {}) or {}
    if not g.get("on") or g.get("since", 0) < boot_time():
        return None
    return g


LM_PLUGIN = "astrbot_plugin_livingmemory"


def astr_call(tok, method, path, body=None, timeout=30):
    """调 AstrBot 后台接口（打游戏模式临时关语音开关、停记忆插件；群里回复时 @ 不 @；表情包发多少）。出错就抛。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{backend_port()}/api/v1{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8", "replace") or "{}")
    if isinstance(out, dict) and out.get("status") == "error":
        raise RuntimeError(str(out.get("message"))[:120])
    return out.get("data") if isinstance(out, dict) else out


def astr_login():
    kv = read_kv(DIR / "private" / "backend.txt")
    pw = kv.get("password", "")
    if not pw:
        return None
    import hashlib
    for p in (pw, hashlib.md5(pw.encode()).hexdigest()):  # 改过密码的后台存的是 md5
        try:
            d = astr_call(None, "POST", "/auth/login", {"username": kv.get("username", "astrbot"), "password": p}, 15)
            if isinstance(d, dict) and d.get("token"):
                return d["token"]
        except Exception:  # noqa: BLE001
            pass
    return None


def astr_tts(tok, on):
    """把 AstrBot 的语音总开关拨到 on，返回拨之前是什么。"""
    cfg = astr_call(tok, "GET", "/config-profiles/default")["config"]
    t = cfg.setdefault("provider_tts_settings", {})
    was = bool(t.get("enable"))
    if was != on:
        t["enable"] = on
        astr_call(tok, "PUT", "/config-profiles/default", cfg)
    return was


def astr_plugin_on(tok, name):
    for p in astr_call(tok, "GET", "/plugins") or []:
        if isinstance(p, dict) and p.get("name") == name:
            return bool(p.get("activated", True))
    return None


# 群里回复时：(@ 对方, 引用对方那句)。AstrBot 自带的两个开关，改了马上生效；@ 只在群里加，引用私聊也会带。
# 她回的是语音时两样都不加（AstrBot 只给纯文字、图片的回复加）
GROUP_REPLY = {"off": (False, False), "mention": (True, False), "quote": (False, True), "both": (True, True)}
GROUP_REPLY_MSG = {"off": "改好了：群里回话不 @ 人、也不引用。",
                   "mention": "改好了：她在群里回谁就先 @ 谁（私聊不 @）。发语音那几条不带 @。",
                   "quote": "改好了：她回话会引用对方那句，一眼看出在回哪条（私聊也会引用）。发语音那几条不带引用。",
                   "both": "改好了：她在群里回谁就 @ 谁，还引用对方那句。发语音那几条不带。"}


def group_reply_mode():
    d = data_dir()
    ps = ((read_json(d / "cmd_config.json", {}) or {}).get("platform_settings") or {}) if d else {}
    cur = (bool(ps.get("reply_with_mention")), bool(ps.get("reply_with_quote")))
    return next((k for k, v in GROUP_REPLY.items() if v == cur), "off")


def act_group_reply(job, mode):
    if mode not in GROUP_REPLY:
        return job.done(False, "没有这个选项")
    if not listening(backend_port()):
        return job.done(False, "大脑没开，改不了。先把大脑开起来。")
    tok = astr_login()
    if not tok:
        return job.done(False, "大脑后台没登上（多半是在网页上改过后台密码）。去后台「配置文件 → 平台配置」里改「回复时 @ 发送者」「回复时引用消息」。")
    job.set("跟大脑说一声……", 40)
    cfg = astr_call(tok, "GET", "/config-profiles/default")["config"]
    ps = cfg.setdefault("platform_settings", {})
    ps["reply_with_mention"], ps["reply_with_quote"] = GROUP_REPLY[mode]
    astr_call(tok, "PUT", "/config-profiles/default", cfg)
    drop_cache("status")
    return job.done(True, GROUP_REPLY_MSG[mode])


# 表情包发多少：她给回复挑中表情以后，按这个百分比真发图（meme_manager 的 generation.emotion.probability）。
# 她本来就不是每句都挑（10-28 群里大约四成回复挑了），所以「多」就是挑中就发
MEME_FREQ = {"few": 30, "mid": 60, "many": 100}
MEME_FREQ_MSG = {"few": "改好了：表情包少发点，她挑中表情也只有三成会发。", "mid": "改好了：她挑中表情，六成会发图。", "many": "改好了：她挑中表情就发图。"}


def meme_freq():
    d = data_dir()
    conf = read_json(d / "config" / "meme_manager_config.json", {}) if d else {}
    v = (((conf or {}).get("generation") or {}).get("emotion") or {}).get("probability")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return ""
    return min(MEME_FREQ, key=lambda k: abs(MEME_FREQ[k] - v))


def act_meme_freq(job, level):
    if level not in MEME_FREQ:
        return job.done(False, "没有这个选项")
    if not listening(backend_port()):
        return job.done(False, "大脑没开，改不了。先把大脑开起来。")
    tok = astr_login()
    if not tok:
        return job.done(False, "大脑后台没登上（多半是在网页上改过后台密码）。去后台「插件 → meme_manager → 配置」里改表情的发送概率。")
    if astr_plugin_on(tok, "meme_manager") is None:
        return job.done(False, "表情包插件还没装，先点上面的「补装」。")
    job.set("跟大脑说一声……", 40)
    conf = astr_call(tok, "GET", "/plugins/config?plugin_id=meme_manager") or {}
    conf = conf.get("config", conf)
    conf.setdefault("generation", {}).setdefault("emotion", {})["probability"] = MEME_FREQ[level]
    astr_call(tok, "PUT", "/plugins/config", {"plugin_id": "meme_manager", "config": conf}, 60)
    drop_cache("status")
    return job.done(True, MEME_FREQ_MSG[level])


def game_boot_hook(on):
    """打游戏模式开着时在启动文件夹放一个快捷方式：没点「打完了，恢复」就关机重启，开机自己把语音、记忆开回来，开完删掉自己。"""
    lnk = startup_dir() / "大肥鱼-打完游戏恢复.lnk"
    if not on:
        try:
            lnk.unlink()
        except OSError:
            pass
        return
    py = Path(sys.executable)
    exe = py.with_name("pythonw.exe") if py.with_name("pythonw.exe").exists() else py
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:DFY_LNK);$s.TargetPath=$env:DFY_EXE;$s.Arguments=$env:DFY_ARGS;"
          "$s.WorkingDirectory=$env:TEMP;$s.WindowStyle=7;$s.Description='开机把大肥鱼的语音和记忆开回来，开完自己删掉';$s.Save()")
    env = dict(os.environ, DFY_LNK=str(lnk), DFY_EXE=str(exe), DFY_ARGS=f'-X utf8 "{HERE / "server.py"}" --dir "{DIR}" --game-off')
    run(["powershell.exe", "-NoProfile", "-Command", ps], timeout=30, env=env)


def http_json(url, timeout=4, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def ollama_models():
    def get():
        try:
            return [m.get("name", "") for m in http_json("http://127.0.0.1:11434/api/tags", 3).get("models", [])]
        except Exception:  # noqa: BLE001
            return None
    return cached("ollama", 15, get)


def ollama_version():
    try:
        return str(http_json("http://127.0.0.1:11434/api/version", 2).get("version") or "")
    except Exception:  # noqa: BLE001
        return ""


def memory_wiring():
    """记忆实际接成什么样（只读配置文件和数据库，不登后台）：插件在不在、开没开，指向哪个记忆模型，本机还是在线，在线的有没有 key。
    以前只看 answers.json 的 memory，选了 2（硅基流动）就一直亮绿灯；10-28 群友那台插件配的是硅基流动、key 空着，照样是绿的。"""
    def get():
        w = {"installed": False, "on": None, "provider": False, "local": False, "ollama": False, "sf": False, "model": "", "key": False}
        dd = data_dir()
        if not dd:
            return w
        w["installed"] = (dd / "plugins" / LM_PLUGIN).is_dir()
        if not w["installed"]:
            return w
        db = dd / "data_v4.db"
        if db.exists():  # 后台里被关掉的插件记在 preferences 表的 inactivated_plugins
            try:
                c = sqlite_ro(db)
                try:
                    row = c.execute("select value from preferences where key = 'inactivated_plugins'").fetchone()
                    v = json.loads(row[0]) if row and isinstance(row[0], str) else (row[0] if row else {})
                    vals = v.get("val") if isinstance(v, dict) else v
                    w["on"] = not any(LM_PLUGIN in str(x) for x in (vals or []))
                finally:
                    c.close()
            except (sqlite3.Error, ValueError, TypeError):
                pass
        lc = read_json(dd / "config" / f"{LM_PLUGIN}_config.json", {}) or {}
        pid = str((lc.get("provider_settings") or {}).get("embedding_provider_id") or "")
        cfg = read_json(dd / "cmd_config.json", {}) or {}
        p = next((x for x in cfg.get("provider") or [] if isinstance(x, dict) and pid and str(x.get("id")) == pid), None)
        if p:
            b = str(p.get("embedding_api_base") or p.get("api_base") or "")
            w.update(provider=True, local=bool(re.search(r"//(127\.0\.0\.1|localhost)[:/]", b)), ollama=":11434" in b, sf="siliconflow" in b,
                     model=str(p.get("embedding_model") or p.get("model") or ""), key=bool(str(p.get("embedding_api_key") or p.get("api_key") or "").strip()))
        return w
    return cached("memwire", 20, get)


def asr_state(a):
    """看视频的「听声音」现在是什么：local（本机识别齐了）、plus（还装了增强版）、online（硅基流动在线识别）、half（本机模型只下了半截）、none。
    本机识别要程序、模型、词表三样都在，模型要是完整的：以前的安装脚本断了会在 asr 里留半截模型，光看 exe 在不在会当成能听。"""
    asr = Path(a.get("asr_dir") or read_text(DIR / "asr-path.txt") or str(DIR / "asr"))
    files = [asr / "sherpa-onnx-offline.exe", asr / "model.int8.onnx", asr / "tokens.txt"]
    if all(f.exists() for f in files):
        try:
            full = files[1].stat().st_size >= 200_000_000
        except OSError:
            full = False
        if full:
            return "plus" if (asr / "funasr-nano" / "llm.int8.onnx").exists() else "local"
    dd = data_dir()
    vc = read_json(dd / "config" / "astrbot_plugin_dafeiyu_video_config.json", {}) if dd else {}
    if str((vc or {}).get("asr_key") or "").strip():
        return "online"
    return "half" if (asr / "model.int8.onnx").exists() else "none"


def launcher_autostart():
    """AstrBot 启动器「高级 → 通用 → 开机自启动」开没开：开了会在 HKCU\\...\\Run 写一项 AstrBot Launcher；
    在任务管理器「启动」页里关掉的记在 StartupApproved\\Run（第一个字节是单数＝关了）。不是 Windows 返回 None。"""
    if not IS_WIN:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            names = []
            i = 0
            while True:
                try:
                    n, v, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                names.append((n, str(v)))
                i += 1
    except OSError:
        return False
    name = next((n for n, v in names if re.search(r"(?i)astrbot[ _-]?launcher", f"{n} {v}")), None)
    if not name:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run") as k:
            b, _ = winreg.QueryValueEx(k, name)
            if isinstance(b, (bytes, bytearray)) and b and b[0] & 1:
                return False
    except OSError:
        pass
    return True


def whitelist_on():
    dd = data_dir()
    cfg = read_json(dd / "cmd_config.json", {}) if dd else None
    if not isinstance(cfg, dict):
        return None
    return bool((cfg.get("platform_settings") or {}).get("enable_id_white_list"))


# ---------------------------------------------------------------- 今天的真数据（AstrBot 的数据库，只读）

def sqlite_ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def today_stats():
    def get():
        out = {}
        dd = data_dir()
        if not dd:
            return out
        mid = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        mid_utc = mid.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db = dd / "data_v4.db"
        if db.exists():
            try:
                c = sqlite_ro(db)
                try:
                    out["msgs"] = int(c.execute("select coalesce(sum(count),0) from platform_stats where timestamp >= ?", (mid.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0])
                except sqlite3.Error:
                    pass
                try:
                    calls, tokens = c.execute("select count(*), coalesce(sum(token_input_other + token_input_cached + token_output),0) from provider_stats where agent_type='internal' and status != 'error' and created_at >= ?", (mid_utc,)).fetchone()
                    out["replies"], out["tokens"] = int(calls), int(tokens)
                except sqlite3.Error:
                    pass
                c.close()
            except sqlite3.Error:
                pass
        lm = dd / "plugin_data" / "astrbot_plugin_livingmemory" / "livingmemory.db"
        if lm.exists():
            try:
                c = sqlite_ro(lm)
                out["memories"] = int(c.execute("select count(*) from documents").fetchone()[0])
                c.close()
            except sqlite3.Error:
                pass
        return out
    return cached("stats", 30, get)


def meme_info():
    def get():
        dd = data_dir()
        info = {"installed": [], "images": 0, "expected": []}
        pack = read_json(DIR / "dafeiyu-config-pack" / "settings" / "pack.json", {}) or {}
        info["expected"] = [m["id"] for m in pack.get("meme_packs", []) if m.get("use", True) and m.get("id")]
        if not dd:
            return info
        md = dd / "plugin_data" / "meme_manager"
        reg = read_json(md / "registry.json", {}) or {}
        for p in reg.get("installed_packs", []):
            if p.get("enabled", True) and p.get("id"):
                n = 0
                try:
                    n = sum(1 for f in (md / "packs" / p["id"]).rglob("*") if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"))
                except Exception:  # noqa: BLE001
                    pass
                info["installed"].append({"id": p["id"], "name": p.get("name") or MEME_NAMES.get(p["id"], p["id"]), "n": n})
                info["images"] += n
        return info
    return cached("memes", 20, get)


RESULT_RX = re.compile(r"^RESULT\s+(\w+)=(\w+)(?:\s+reason=(\S+))?(?:\s+next=(.*))?$")
REASON_TEXT = {
    "network": "网络下不动（几个 GitHub 加速地址都没连上）", "ollama": "Ollama 没开，或者记忆模型 bge-m3 没下好", "login": "后台登不上（后台密码改过？）",
    "breeze-off-kept": "Breeze 没开，语音先没动", "test": "试了一下没通过", "not-chosen": "没选这项", "no-bot": "小号还没接上", "no-model": "DeepSeek 还没接好",
    "no-key": "硅基流动的密钥是空的，记忆先关着", "not-in-answers": "回答文件里没写要不要这项", "no-asr": "识别模型没装上，视频里的话先听不到", "no-plugin": "插件还没装上",
}


def import_log_state(block):
    """后台补装的日志：在跑（没有 RESULT、最近还在写）/ 跑完了（最后的 RESULT）/ 没跑过。"""
    f = DIR / "logs" / f"import-{block}.log"
    if not f.exists():
        return None
    lines = tail_lines(f, 200_000, 400)
    res = None
    for ln in lines:
        m = RESULT_RX.match(ln.strip())
        if m and m.group(1) == block:
            res = {"status": m.group(2), "reason": m.group(3) or "", "next": (m.group(4) or "").strip()}
    age = now() - f.stat().st_mtime
    st = {"mtime": f.stat().st_mtime, "result": res, "running": res is None and age < 180}
    pr = progress_from_lines(lines)
    if st["running"] and pr.get("total"):
        pr["step"] += eta_text(block + ":" + pr.get("pack", ""), pr["done"], pr["total"])
    st.update({"step": pr["step"], "pct": pr["pct"]})
    return st


ETA = {}  # 块 -> [(时间, 已下 MB)]，用最近两分钟的速度估「还要几分钟」


def eta_text(block, done, total):
    if not total or done is None:
        return ""
    with LOCK:
        pts = [x for x in ETA.get(block, []) if now() - x[0] < 120 and x[1] <= done]
        if not pts or pts[-1][1] != done:
            pts.append((now(), done))
        ETA[block] = pts[-60:]
    t0, d0 = pts[0]
    if now() - t0 < 15 or done <= d0:
        return ""
    left = (total - done) / ((done - d0) / (now() - t0))
    if left < 60:
        return "，快好了"
    return f"，还要大约 {int(left // 60) + 1} 分钟"


def progress_from_lines(lines):
    step, pct = "", None
    for ln in reversed(lines):
        s = ln.strip()
        if not s or s.startswith("━") or s.startswith("=") or s.startswith("RESULT"):
            continue
        m = re.search(r"([\w\-]+)：已下 (\d+)(?:/(\d+))? MB", s)
        if m:
            name = MEME_NAMES.get(m.group(1), m.group(1))
            if m.group(3) and int(m.group(3)) > 0:
                pct = min(99, int(int(m.group(2)) * 100 / int(m.group(3))))
                step = f"正在下「{name}」：{m.group(2)}/{m.group(3)} MB"
                return {"step": step, "pct": pct, "done": int(m.group(2)), "total": int(m.group(3)), "pack": m.group(1)}
            else:
                step = f"正在下「{name}」：已下 {m.group(2)} MB"
        else:
            step = redact(s.lstrip("▶ ！").strip())[:80]
        break
    return {"step": step, "pct": pct}


def result_text(res):
    if not res:
        return ""
    head = {"ok": "装好了", "partial": "装上了一部分", "fail": "没装上", "skip": "跳过了"}.get(res["status"], res["status"])
    why = res.get("reason", "")
    key = why.split(":", 1)[0]
    if key in REASON_TEXT:
        head += "：" + REASON_TEXT[key]
    elif why.startswith("error"):
        head += "：中途出了错（点「复制求助信息」交给 Agent 看）"
    return head


# ---------------------------------------------------------------- DeepSeek 余额（服务端用他的 key 查，只把金额给页面）

def deepseek_key():
    f = DIR / "private" / "deepseek-key.txt"
    raw = read_text(f)
    if ":" in raw.replace("：", ":"):
        kv = read_kv(f)
        for k, v in kv.items():
            if "key" in k:
                return v
    return raw.split()[0] if raw else ""


def update_disk(**kv):
    """cache.json 读改写（余额、更新两个后台线程都会写，加锁免得互相覆盖）。"""
    with LOCK:
        disk = read_json(STATE / "cache.json", {}) or {}
        disk.update(kv)
        try:
            write_json(STATE / "cache.json", disk)
        except Exception:  # noqa: BLE001
            pass


def check_balance(force=False):
    if CLOUD:
        return cloud.refresh(force_balance=force)
    disk = read_json(STATE / "cache.json", {}) or {}
    bal = disk.get("balance") or {}
    fresh = 600 if bal.get("state") == "ok" else 120
    if not force and bal and now() - bal.get("at", 0) < fresh:
        return bal
    key = deepseek_key()
    if not key:
        bal = {"state": "nokey", "at": now()}
    else:
        try:
            req = urllib.request.Request("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            infos = j.get("balance_infos") or []
            info = next((i for i in infos if i.get("currency") == "CNY"), infos[0] if infos else {})
            bal = {"state": "ok", "amount": str(info.get("total_balance", "")), "currency": info.get("currency", "CNY"),
                   "available": bool(j.get("is_available", True)), "at": now()}
        except urllib.error.HTTPError as e:
            bal = {"state": "auth" if e.code in (401, 403) else "http", "code": e.code, "at": now()}
        except Exception as e:  # noqa: BLE001
            bal = {"state": "net", "why": type(e).__name__, "at": now()}
        finally:
            key = ""
    upd = {"balance": bal}
    if bal.get("state") == "ok":
        day = datetime.now().strftime("%Y-%m-%d")
        if ((read_json(STATE / "cache.json", {}) or {}).get("balance_day") or {}).get("date") != day:
            upd["balance_day"] = {"date": day, "amount": bal["amount"], "at": now()}
    update_disk(**upd)
    log(f"余额检查：{bal.get('state')}")
    return bal


# ---------------------------------------------------------------- 更新提醒（缓存 6 小时，只提醒，不升级）

def fetch_text(url, timeout=10, limit=600_000):
    req = urllib.request.Request(url, headers={"User-Agent": "dafeiyu-workbench/" + VERSION, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit).decode("utf-8", "replace")


def latest_github(repo):
    try:
        j = json.loads(fetch_text(f"https://api.github.com/repos/{repo}/releases/latest", timeout=8))
        if j.get("tag_name"):
            return j["tag_name"].lstrip("v")
    except Exception:  # noqa: BLE001
        pass
    for p in GH_PROXIES[:-1]:  # API 不通就走加速站看 releases/latest 跳到哪一版
        try:
            m = re.search(rf"/{re.escape(repo)}/releases/tag/v?(\d+\.\d+\.\d+[\w.\-]*)", fetch_text(p + f"https://github.com/{repo}/releases/latest"))
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            continue
    return ""


def latest_pypi(pkg):
    for idx in ("https://mirrors.aliyun.com/pypi/simple/", "https://pypi.tuna.tsinghua.edu.cn/simple/"):
        try:
            vs = re.findall(rf"{pkg}-(\d+(?:\.\d+)+)(?:-py3-none-any\.whl|\.tar\.gz)", fetch_text(idx + pkg + "/", timeout=10, limit=3_000_000))
            if vs:
                return max(vs, key=ver_tuple)
        except Exception:  # noqa: BLE001
            continue
    return ""


def latest_pack_version():
    for p in GH_PROXIES[::-1][:1] + GH_PROXIES[:-1]:
        try:
            j = json.loads(fetch_text(p + "https://raw.githubusercontent.com/zexuanw958-svg/dafeiyu-config-pack/main/settings/pack.json", timeout=8))
            return str(j.get("version") or "")
        except Exception:  # noqa: BLE001
            continue
    return ""


def skill_version():
    """装着的安装 Skill 是哪一版：finish.ps1 拷来的 tools\\skill-version.json；测试版 1（10-28）还没有这个文件，算 0.3.0。"""
    v = str((read_json(SCRIPTS / "skill-version.json", {}) or {}).get("version") or "")
    return v or ("0.3.0" if (SCRIPTS / "workbench").is_dir() else "")


def latest_skill():
    """GitHub 上最新一版的 skill-version.json（版本、改了什么、要不要重新导入）。releases/latest 这个固定地址加速站也认。"""
    for p in GH_PROXIES[::-1][:1] + GH_PROXIES[:-1]:
        try:
            j = json.loads(fetch_text(p + f"https://github.com/{SKILL_REPO}/releases/latest/download/skill-version.json", timeout=10, limit=64_000))
            if re.match(r"^\d+\.\d+\.\d+$", str(j.get("version") or "")):
                j["_via"] = p
                return j
        except Exception:  # noqa: BLE001
            continue
    return {}


def check_updates(force=False):
    disk = read_json(STATE / "cache.json", {}) or {}
    up = disk.get("updates") or {}
    ttl = 6 * 3600 if up.get("ok") else 1800
    if not force and up and now() - up.get("at", 0) < ttl and up.get("dir") == str(DIR) and up.get("wb") == VERSION:  # 工作台换了版本就重查（新版可能多查几项）
        return up
    res = {"at": now(), "dir": str(DIR), "wb": VERSION, "items": {}}
    ok = True
    av = cloud.astrbot_version() if CLOUD else astrbot_version()
    if av:
        lv = latest_pypi("astrbot")
        ok &= bool(lv)
        res["items"]["astrbot"] = {"local": av, "latest": lv}
    if (DIR / "SnowLuma").is_dir():
        sv = snowluma_version()
        lv = latest_github("SnowLuma/SnowLuma")
        ok &= bool(lv)
        res["items"]["snowluma"] = {"local": sv, "latest": lv}
    kv = cloud.skill_version_effective() if CLOUD else skill_version()
    if kv:
        ls = latest_skill()
        ok &= bool(ls)
        if CLOUD and ls.get("version") and ver_tuple(str(ls["version"])) < ver_tuple(skill_version()):
            kv = skill_version()  # 这台电脑上的比 GitHub 上的还新（没发布的版本）：不提示「更新」，免得换回旧的
        res["items"]["skill"] = {"local": kv, "latest": str(ls.get("version") or ""), "notes": [str(x) for x in (ls.get("notes") or [])][:6],
                                 "reimport": [str(x) for x in (ls.get("reimport") or [])]}
    pv = "" if CLOUD else pack_version()
    if pv:
        res["items"]["pack"] = {"local": pv, "latest": latest_pack_version()}
    res["ok"] = ok
    update_disk(updates=res)
    log(f"更新检查：{'都查到了' if ok else '有查不到的'}")
    return res


def background(fn, *a):
    threading.Thread(target=lambda: _safe(fn, *a), daemon=True).start()


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:  # noqa: BLE001
        log("后台出错：" + traceback.format_exc(limit=3))


# ---------------------------------------------------------------- 状态

def item(id_, name, icon, what, state, text, fix=None, links=None, btns=None, pill=None, extra=None):
    d = {"id": id_, "name": name, "icon": icon, "what": what, "state": state, "text": text, "fix": fix, "links": links or [], "btns": btns or []}
    if pill:
        d["pill"] = pill
    if extra:
        d.update(extra)
    return d


def collect_versions(proto, voice, mem):
    versions = [["工作台", VERSION]]
    av = astrbot_version()
    if av:
        versions.append(["AstrBot", av])
    if proto == "snowluma":
        sv = snowluma_version()
        if sv:
            versions.append(["SnowLuma", sv])
        qv = qq_version_desktop()
        if qv:
            versions.append(["桌面 QQ", qv])
    else:
        sh = napcat_shell()
        if sh:
            try:
                qv = max((d.name for d in (sh / "versions").iterdir() if d.is_dir()), key=ver_tuple)
                versions.append(["NapCat 自带 QQ", qv])
            except Exception:  # noqa: BLE001
                pass
    if voice == "4" and breeze_version():
        versions.append(["Breeze", breeze_version()])
    if mem == "1":
        ov = cached("ollama_ver", 300, ollama_version)
        if ov:
            versions.append(["Ollama", ov])
    if pack_version():
        versions.append(["配置包", pack_version()])
    return versions


def deepseek_item(has_key, bal, day, wl):
    """DeepSeek 那一项：has_key＝密钥文件在不在，bal、day＝cache.json 里的余额和今天头一次查到的余额，wl＝白名单开没开（云端模式也用这个）。"""
    dlinks = [{"label": "看每天花了多少", "href": "https://platform.deepseek.com/usage"}, {"label": "充值", "href": "https://platform.deepseek.com/top_up"}]
    dwhat = "她想怎么回话靠它，按用量扣钱。"
    if not has_key:
        ds = item("deepseek", "DeepSeek", "i-bolt", dwhat, "bad", "没找到 DeepSeek 的密钥文件（private 里的 deepseek-key.txt），她回不了话。交给 Agent 重新接一下。", links=dlinks[:1])
    elif bal.get("state") == "ok":
        amt = bal.get("amount", "")
        try:
            v = float(amt)
        except ValueError:
            v = None
        used = ""
        try:
            if day.get("date") == datetime.now().strftime("%Y-%m-%d") and v is not None:
                d = float(day["amount"]) - v
                if d >= 0.005:
                    used = f"从今天 {datetime.fromtimestamp(day.get('at', now())):%H:%M} 到现在用了 ¥{d:.2f}。"
        except (ValueError, KeyError, TypeError):
            pass
        if v is not None and (v <= 0 or not bal.get("available", True)):
            ds = item("deepseek", "DeepSeek", "i-bolt", dwhat, "bad", f"余额 ¥{amt}，用完了，她回不了话。充一点马上就好。", links=dlinks[::-1], extra={"balance": amt})
        elif v is not None and v < 2:
            ds = item("deepseek", "DeepSeek", "i-bolt", dwhat, "warn", f"余额只剩 ¥{amt}，记得充一点。{used}", links=dlinks[::-1], extra={"balance": amt})
        else:
            ds = item("deepseek", "DeepSeek", "i-bolt", dwhat, "ok", f"余额 ¥{amt}。{used}", links=dlinks, extra={"balance": amt})
    elif bal.get("state") == "auth":
        ds = item("deepseek", "DeepSeek", "i-bolt", dwhat, "bad", "密钥（扣钱的钥匙）不对或者被删了，她回不了话。交给 Agent 换一把新的。", links=[{"label": "管理密钥", "href": "https://platform.deepseek.com/api_keys"}])
    elif bal.get("state") in ("net", "http"):
        ds = item("deepseek", "DeepSeek", "i-bolt", dwhat, "unk", "这会儿查不到余额（连不上 DeepSeek），不一定影响聊天，过会儿再看。", links=dlinks, pill="查不到")
    else:
        ds = item("deepseek", "DeepSeek", "i-bolt", dwhat, "unk", "正在查余额……", links=dlinks, pill="在查")
    if wl is False and ds["state"] in ("ok", "warn", "unk"):
        # 白名单关着是他自己选的，不改颜色，只把代价写明白
        ds["text"] = ds["text"].strip() + "白名单没开：谁 @ 她都能用，花的是你的余额。"
    return ds


def collect_status():
    if CLOUD:
        return cloud.collect_status()
    a = answers()
    proto = protocol()
    port = backend_port()
    base = f"http://127.0.0.1:{port}"
    cli = is_cli_install()
    procs = process_list()
    items = []
    jobs = running_jobs()

    def job_running(group):
        return any(j["group"] == group and j["state"] == "running" for j in jobs)

    # 1 大脑
    up = listening(port)
    lp = net_snapshot()[0].get(port)
    started = next((p["create"] for p in procs if p["pid"] == lp), 0) if up else 0
    astr_procs = [p for p in procs if under(p["exe"], DIR / "uv") and p["name"].lower() == "astrbot.exe"]
    if up:
        brain = item("brain", "大脑 · AstrBot", "i-brain", "管聊天的总程序。", "ok", f"开着，已连续跑了 {dur_text(now() - started)}。" if started else "开着。",
                     links=[{"label": "打开后台", "href": base + "/#/"}], extra={"since": started})
    elif job_running("brain") or (astr_procs and now() - max(p["create"] for p in astr_procs) < 180):
        brain = item("brain", "大脑 · AstrBot", "i-brain", "管聊天的总程序。", "busy", "正在起来，大约半分钟到一分钟。")
    elif astr_procs:
        brain = item("brain", "大脑 · AstrBot", "i-brain", "管聊天的总程序。", "bad", "程序开着，但后台一直没反应（卡住了）。重启一下就好。",
                     fix={"action": "restart_astrbot", "label": "重启大脑"})
    elif cli:
        brain = item("brain", "大脑 · AstrBot", "i-brain", "管聊天的总程序。", "bad", "没开，她现在回不了话。修复会把它打开，大约半分钟。",
                     fix={"action": "start_astrbot", "label": "一键修复"})
    else:
        brain = item("brain", "大脑 · AstrBot", "i-brain", "管聊天的总程序。", "bad", "没开，她现在回不了话。打开 AstrBot 启动器，点「启动」。")
    items.append(brain)

    # 2 登 QQ 的程序
    bot = mask_qq(read_text(DIR / "private" / "bot-qq.txt"))
    online = qq_online()
    if proto == "snowluma":
        pname, pwhat, proot = "SnowLuma", "帮她登上小号，挂在这台电脑的桌面 QQ 上。", DIR / "SnowLuma"
        plink = {"label": "SnowLuma 后台", "href": "http://127.0.0.1:5099"}
    else:
        pname, pwhat, proot = "NapCat", "帮她登上小号，用它自带的一份 QQ。", DIR / "NapCat"
        plink = {"label": "NapCat 后台", "href": "http://127.0.0.1:6099/webui"}
    prun = bool(procs_under(proot))
    btns = [{"label": "重新登", "action": "relogin"}, {"label": "重启它", "action": "restart_qq"}]
    suspect = proto == "snowluma" and cached("sl_suspect", 30, snowluma_upgrade_suspect)
    if online:
        qq = item("qq", f"登 QQ 的程序 · {pname}", "i-chat", pwhat, "ok", f"小号 {bot} 在线。" if bot else "小号在线。", links=[plink], btns=btns)
    elif job_running("qq"):
        qq = item("qq", f"登 QQ 的程序 · {pname}", "i-chat", pwhat, "busy", "正在重新登，稍等。", links=[plink])
    elif brain["state"] != "ok":
        qq = item("qq", f"登 QQ 的程序 · {pname}", "i-chat", pwhat, "bad", "大脑没开，小号也连不上她。先把大脑开起来。", links=[plink], btns=btns[:1])
    elif not prun:
        qq = item("qq", f"登 QQ 的程序 · {pname}", "i-chat", pwhat, "bad", f"{pname} 没开着，小号连不上她。点「重新登」会把它打开。",
                  fix={"action": "relogin", "label": "重新登"}, links=[plink], btns=btns[1:])
    else:
        t = "小号没登上。点「重新登」，照弹出来的窗口做。"
        if suspect:
            t = "小号没登上。看日志像是桌面 QQ 自动升级了，SnowLuma 还没跟上，去「更新」看看怎么办。"
        qq = item("qq", f"登 QQ 的程序 · {pname}", "i-chat", pwhat, "bad", t, fix={"action": "relogin", "label": "重新登"}, links=[plink], btns=btns[1:])
    items.append(qq)

    # 3 DeepSeek
    bal = (read_json(STATE / "cache.json", {}) or {}).get("balance") or {}
    day = (read_json(STATE / "cache.json", {}) or {}).get("balance_day") or {}
    items.append(deepseek_item((DIR / "private" / "deepseek-key.txt").exists(), bal, day, whitelist_on()))

    # 4 记忆：看实际接成什么样（插件开没开、指向哪个模型、在线的有没有 key、本机的模型在不在），answers.json 只当「他选过要不要」
    st = today_stats()
    mem = a.get("memory", "")
    mlinks = [{"label": "看她记住了什么", "href": base + "/#/plugin-view/astrbot_plugin_livingmemory/dashboard"}]
    mcount = f"记住了 {st['memories']} 件事。" if "memories" in st else ""
    mwhat = "让她记得你说过的事。"
    gm = game_mode()
    w = memory_wiring()
    mname = "记忆 · 本机 Ollama" if w["ollama"] else "记忆 · 硅基流动" if w["sf"] else "记忆"
    want = mem in ("1", "2")
    if gm and gm.get("mem") and w["on"] is not True:
        memi = item("memory", mname, "i-book", mwhat, "off", "打游戏模式：先关着。她照样聊天，只是不记新的事（以前记住的还在）。", links=mlinks, pill="打游戏中")
    elif not w["installed"] or w["on"] is False:
        if want:
            memi = item("memory", mname, "i-book", mwhat, "bad", "选了要记忆，但记忆插件" + ("没装上" if not w["installed"] else "关着（上次多半没接好，被关掉了）") + "，她记不住事。交给 Agent 补一下。",
                        extra={"ask": "memfix"})
        else:
            memi = item("memory", "记忆", "i-book", mwhat, "off", "没开。想要的话去「交给 Agent」。", extra={"ask": "memory"})
    elif not w["provider"]:
        memi = item("memory", mname, "i-book", mwhat, "bad", "记忆插件开着，但没接上记忆模型，她记不住事。交给 Agent 接一下。", links=mlinks, extra={"ask": "memfix"})
    elif w["ollama"]:
        mm = (w["model"] or "bge-m3").split(":")[0]
        if gm and not listening(11434):
            memi = item("memory", mname, "i-book", mwhat, "off", "打游戏模式：先关着。她照样聊天，只是不记新的事（以前记住的还在）。", links=mlinks, pill="打游戏中")
        elif listening(11434):
            models = ollama_models()
            if models is None or any(m.split(":")[0] == mm for m in models):
                memi = item("memory", mname, "i-book", mwhat, "ok", f"开着。{mcount}", links=mlinks)
            else:
                memi = item("memory", mname, "i-book", mwhat, "bad", f"Ollama 开着，但记忆模型 {mm} 不在，她记不住新的事。交给 Agent 重新下一下（约 1.2 GB，走国内 ModelScope）。",
                            links=mlinks, extra={"ask": "bge"})
        elif job_running("ollama"):
            memi = item("memory", mname, "i-book", mwhat, "busy", "正在打开 Ollama……", links=mlinks)
        else:
            fx = {"action": "start_ollama", "label": "一键修复"} if ollama_exe() else None
            memi = item("memory", mname, "i-book", mwhat, "bad", "Ollama 没开，她记不住新的事（以前记住的还在）。" + ("修复会把它打开。" if fx else "Ollama 没找到，交给 Agent 装一下。"),
                        fix=fx, links=mlinks, extra=None if fx else {"ask": "bge"})
    elif not w["local"] and not w["key"]:
        memi = item("memory", mname, "i-book", mwhat, "bad", "记忆接的是在线服务，但没填密钥，她记不住事。交给 Agent：粘一把硅基流动的密钥，或者改成本机记忆，或者干脆先关掉。",
                    links=mlinks, extra={"ask": "memfix"})
    else:
        memi = item("memory", mname, "i-book", mwhat, "ok", f"在线服务，不用开。{mcount}", links=mlinks)
    items.append(memi)

    # 5 语音
    voice = a.get("voice", "")
    vlinks = [{"label": "换模型、语音", "href": base + "/#/providers"}]
    vwhat = "让她能发语音。"
    if voice == "4":
        if gm and not listening(8137):
            vi = item("voice", "语音 · 本机 Breeze", "i-mic", "让她能发语音，用你的显卡算，不花钱。", "off", "打游戏模式：先关着，显卡腾给游戏，她现在只打字。",
                      links=vlinks, pill="打游戏中")
        elif listening(8137):
            vi = item("voice", "语音 · 本机 Breeze", "i-mic", "让她能发语音，用你的显卡算，不花钱。", "ok", "开着。", links=vlinks)
        elif job_running("breeze"):
            vi = item("voice", "语音 · 本机 Breeze", "i-mic", "让她能发语音，用你的显卡算，不花钱。", "busy", "正在打开 Breeze……", links=vlinks)
        else:
            fx = {"action": "start_breeze", "label": "一键修复"} if (DIR / "breeze-tts" / "start-breeze.cmd").exists() else None
            vi = item("voice", "语音 · 本机 Breeze", "i-mic", "让她能发语音，用你的显卡算，不花钱。", "bad",
                      "没在运行，她现在只能打字。" + ("修复会把它重新开起来，大约 20 秒。" if fx else "找不到 Breeze 的启动文件，交给 Agent 看看。"), fix=fx, links=vlinks)
    elif voice == "3":
        vi = item("voice", "语音 · 豆包", "i-mic", vwhat, "ok", "在线服务，不用开。按说话时长扣钱，大约一分钟一块。", links=vlinks)
    elif voice == "1":
        vi = item("voice", "语音 · Edge 免费女声", "i-mic", vwhat, "ok", "在线服务，不用开，不花钱。", links=vlinks)
    elif voice == "2":
        vi = item("voice", "语音 · 小米 MiMo", "i-mic", vwhat, "ok", "在线服务，不用开。", links=vlinks)
    else:
        vi = item("voice", "语音", "i-mic", vwhat, "off", "没开，她只打字。想要的话去「交给 Agent」。", extra={"ask": "voice"})
    items.append(vi)

    # 6 看图看视频
    video = a.get("video", "") == "1"
    vwhat2 = "看懂你发的图和视频。"
    if video:
        ff = a.get("ffmpeg_path") or read_text(DIR / "ffmpeg-path.txt") or str(DIR / "ffmpeg" / "bin" / "ffmpeg.exe")
        hear = asr_state(a)
        lk = [{"label": "截几张、看多长", "href": base + "/#/extension?open_config=astrbot_plugin_dafeiyu_video"}]
        if not Path(ff).exists():
            vis = item("vision", "看图看视频", "i-image", vwhat2, "bad", "找不到 ffmpeg，她看不了视频（看图不受影响）。交给 Agent 重新装一下。", links=lk, extra={"ask": "video"})
        elif hear in ("local", "plus"):
            vis = item("vision", "看图看视频", "i-image", vwhat2, "ok", "能看图、看视频，也听得懂视频里说的话" + ("（装了方言、歌词增强版）。" if hear == "plus" else "。"), links=lk)
        elif hear == "online":
            vis = item("vision", "看图看视频", "i-image", vwhat2, "ok", "能看图、看视频；视频里说的话用硅基流动在线识别（声音会传给硅基流动）。", links=lk)
        else:
            vis = item("vision", "看图看视频", "i-image", vwhat2, "warn",
                       "能看图、看视频画面，但视频里说的话听不到：" + ("识别模型只下了一半。" if hear == "half" else "识别模型没装上。") + "交给 Agent 补装（先走国内 ModelScope）。",
                       links=lk, extra={"ask": "hear"})
    else:
        vis = item("vision", "看图看视频", "i-image", vwhat2, "ok", "能看图。看视频没装，想要的话去「交给 Agent」。", pill="能看图", extra={"ask": "video"})
    items.append(vis)

    # 7 开机自启
    sdir = startup_dir()
    try:
        lnks = [f.name for f in sdir.iterdir()]
    except Exception:  # noqa: BLE001
        lnks = []
    need, miss = [], []
    if cli:
        need.append(("大肥鱼-AstrBot.lnk", "大脑"))
    need.append(("大肥鱼-SnowLuma.lnk" if proto == "snowluma" else "大肥鱼-NapCat.lnk", pname))
    if (mem == "1" or w["ollama"]) and ollama_exe():
        need.append(("Ollama.lnk", "Ollama（记忆）"))
    if pet_on() and (DIR / "start-petbridge.cmd").exists():
        need.append(("大肥鱼-桌宠桥.lnk", "大肥鱼桥（桌宠）"))
    for f, label in need:
        if f not in lnks:
            miss.append(label)
    if voice == "4" and not any("breeze" in n.lower() for n in lnks):
        miss.append("Breeze（语音）")
    # 启动器装的大脑：开机自启在启动器里开（我们不替他改启动器的设置）。没开就提醒，开了也说一句实例要跟着起来还得开「退出时保留实例运行状态」
    la = None if cli else launcher_autostart()
    lnote = ""
    if la is False:
        lnote = "大脑（AstrBot 启动器）开机不会自己起来：打开启动器，点最左边「高级」，在「通用」里打开「开机自启动」「自启动后最小化到托盘」「退出时保留实例运行状态」。启动器一退出大脑也会停，托盘图标别点退出。"
    elif la:
        lnote = "大脑跟着 AstrBot 启动器起来（启动器「高级 → 通用」里「退出时保留实例运行状态」要开着，实例才会跟着开；托盘图标别点退出）。"
    both = "大肥鱼-SnowLuma.lnk" in lnks and "大肥鱼-NapCat.lnk" in lnks
    bt = boot_time()
    boot = ""
    if bt and started and 0 <= started - bt < 900:
        boot = f"这次开机 {datetime.fromtimestamp(bt):%H:%M}，她 {datetime.fromtimestamp(started):%H:%M} 自己起来了。"
    if both:
        auto = item("autostart", "开机自启", "i-power", "开机后她自己上线。", "warn", "SnowLuma 和 NapCat 都设了开机自启，会抢同一个小号、互相挤下线。修复会只留现在用的那个。",
                    fix={"action": "autostart_fix", "label": "一键修复"})
    elif miss:
        auto = item("autostart", "开机自启", "i-power", "开机后她自己上线。", "warn", f"开机后{'、'.join(miss)}不会自己起来。修复会把开机自启补上。" + (lnote if la is False else ""),
                    fix={"action": "autostart_fix", "label": "补上"})
    elif la is False and not boot:
        auto = item("autostart", "开机自启", "i-power", "开机后她自己上线。", "warn", lnote + "开好以后重启电脑看一眼，她自己上线就对了。")
    else:
        auto = item("autostart", "开机自启", "i-power", "开机后她自己上线。", "ok", boot or ("都设好了。" + lnote))
        if proto == "snowluma":
            auto["text"] += "桌面 QQ 要在它自己的设置里开「开机自动启动」、登录时勾「自动登录」。" if not boot else ""
    items.append(auto)

    # 8 表情包
    mi = meme_info()
    ml = import_log_state("memes")
    elinks = [{"label": "看看、上传新的", "href": base + "/#/plugin-view/meme_manager/app"}]
    have = {p["id"] for p in mi["installed"]}
    lack = [x for x in mi["expected"] if x not in have]
    ewhat = "聊着聊着甩一张。"
    if ml and ml["running"]:
        memes = item("memes", "表情包", "i-smile", ewhat, "busy", (ml.get("step") or "正在补装表情包……") + "。她照常聊天。", links=elinks, extra={"pct": ml.get("pct")})
    elif not mi["installed"]:
        t = "还没装上，她先不配图。补装要从 GitHub 下几百 MB，放后台下，不耽误聊天。"
        if ml and ml.get("result"):
            t = f"还没装上（上次补装：{result_text(ml['result'])}）。可以再试一次，放后台下，不耽误聊天。"
        memes = item("memes", "表情包", "i-smile", ewhat, "warn", t, fix={"action": "import", "body": {"block": "memes"}, "label": "补装表情包"}, links=elinks)
    elif lack:
        names = "、".join(f"「{MEME_NAMES.get(x, x)}」" for x in lack)
        memes = item("memes", "表情包", "i-smile", ewhat, "warn", f"装了 {len(mi['installed'])} 套（{mi['images']} 张），还差{names}。可以补装，放后台下。",
                     fix={"action": "import", "body": {"block": "memes"}, "label": "补装表情包"}, links=elinks)
    else:
        memes = item("memes", "表情包", "i-smile", ewhat, "ok", f"装了 {len(mi['installed'])} 套，共 {mi['images']} 张。", links=elinks)
    items.append(memes)

    # 9 桌宠（配过才有这一项：private\pet-key.txt 在）
    if pet_on():
        pwhat = "电脑桌宠里跟你说话的也是她，记忆跟 QQ 私聊共用一份。"
        h = pet_health() if listening(PET_PORT) else None
        rb = [{"action": "restart_petbridge", "label": "重开大肥鱼桥"}]
        if job_running("pet"):
            pet = item("pet", "桌宠", "i-persona", pwhat, "busy", "正在重开大肥鱼桥……")
        elif h and h.get("astrbot") == "ok":
            pet = item("pet", "桌宠", "i-persona", pwhat, "ok", "连着她。桌宠右键「对话」就是跟她说话（DeepSeek Harness 要开着，桌宠才在）。", btns=rb)
        elif h and not listening(port):
            pet = item("pet", "桌宠", "i-persona", pwhat, "off", "大脑没开，桌宠这会儿找不到她。大脑开好就自己连上。")
        elif h:
            pet = item("pet", "桌宠", "i-persona", pwhat, "warn", "大肥鱼桥开着，但大脑不让它进（桌宠的 key 可能被删了）。交给 Agent 重新配一下桌宠。", btns=rb, extra={"ask": "pet"})
        elif (DIR / "start-petbridge.cmd").exists():
            pet = item("pet", "桌宠", "i-persona", pwhat, "warn", "大肥鱼桥没在跑：桌宠这会儿是 DeepSeek Harness 自己的模型在回话，不知道 QQ 里的事。修复会把它重新开起来，几秒钟。",
                       fix={"action": "restart_petbridge", "label": "重开大肥鱼桥"})
        else:
            pet = item("pet", "桌宠", "i-persona", pwhat, "warn", "找不到大肥鱼桥的启动文件。交给 Agent 重新配一下桌宠。", extra={"ask": "pet"})
        items.append(pet)

    # 今天的数字
    stats = []
    if "msgs" in st:
        stats.append({"id": "msgs", "n": st["msgs"], "unit": "条", "label": "今天收到的消息"})
    if "replies" in st:
        stats.append({"id": "replies", "n": st["replies"], "unit": "次", "label": "她回了"})
    if "tokens" in st:
        t = st["tokens"]
        stats.append({"id": "tokens", "n": (f"{t / 10000:.1f}" if t >= 10000 else str(t)), "unit": "万" if t >= 10000 else "", "label": "吃掉的 token", "note": "她的饭"})
    if "memories" in st and mem in ("1", "2"):
        stats.append({"id": "memories", "n": st["memories"], "unit": "件", "label": "记住的事"})
    elif mi["images"]:
        stats.append({"id": "memes", "n": mi["images"], "unit": "张", "label": "表情包"})

    # 补装（功能页）
    feats = []
    for blk, label, chosen in (("voice", "语音", voice in ("1", "2", "3", "4")), ("memory", "记忆", mem in ("1", "2")), ("memes", "表情包", True), ("video", "看视频", video)):
        s = import_log_state(blk)
        feats.append({"block": blk, "label": label, "chosen": chosen, "running": bool(s and s["running"]), "step": (s or {}).get("step", ""), "pct": (s or {}).get("pct"),
                      "result": result_text((s or {}).get("result")), "result_status": ((s or {}).get("result") or {}).get("status", ""),
                      "at": datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M") if s else ""})

    # 更新
    disk = read_json(STATE / "cache.json", {}) or {}
    up = disk.get("updates") or {}
    updates = []
    names = {"skill": ("安装 Skill 和工作台", "i-toggle", "帮你装、帮你修的那套说明书，还有这个窗口。"), "astrbot": ("AstrBot", "i-brain", "她的大脑。"), "snowluma": ("SnowLuma", "i-chat", "帮她登 QQ 的程序。"), "pack": ("大肥鱼配置包", "i-spark", "她的人设、插件和表情包清单。")}
    for k, v in (up.get("items") or {}).items():
        if k == "snowluma" and proto != "snowluma":
            continue
        n, ic, w = names[k]
        lv, cv = v.get("latest", ""), v.get("local", "")
        state = "unknown" if not lv else ("new" if ver_tuple(lv) > ver_tuple(cv) else "latest")
        updates.append({"id": k, "name": n, "icon": ic, "what": w, "local": cv, "latest": lv, "state": state, "notes": v.get("notes") or [], "reimport": v.get("reimport") or []})

    # 装机清单（T3）
    secrets_files = []
    desc = {"backend.txt": "AstrBot 后台的账号和密码", "deepseek-key.txt": "DeepSeek 密钥（扣钱的钥匙）", "napcat-ws-token.txt": "QQ 程序连大脑用的口令",
            "snowluma-webui.txt": "SnowLuma 后台密码", "napcat-webui-token.txt": "NapCat 后台口令", "owner-qq.txt": "你的大号（她只认这个号是主人）",
            "bot-qq.txt": "她的小号", "volc.txt": "豆包语音的两项凭证", "group.txt": "她进的群", "siliconflow-key.txt": "硅基流动密钥", "sf-key.txt": "硅基流动密钥", "mimo-key.txt": "小米 MiMo 密钥",
            "pet-key.txt": "桌宠用的大脑接口密钥（只能用来跟她聊天）", "pet-bridge-token.txt": "桌宠调大肥鱼桥的口令"}
    try:
        for f in sorted((DIR / "private").iterdir()):
            if f.is_file() and f.name != "protocol.txt":
                secrets_files.append({"name": f.name, "what": desc.get(f.name, "")})
    except Exception:  # noqa: BLE001
        pass
    cost = None
    ib = read_json(DIR / "logs" / "install-balance.json", {}) or {}
    if ib.get("start") and bal.get("state") == "ok":
        try:
            cost = {"start": ib["start"], "now": bal["amount"], "spent": f"{float(ib['start']) - float(bal['amount']):.2f}"}
        except (ValueError, TypeError):
            cost = None

    versions = cached("versions", 120, lambda: collect_versions(proto, voice, mem))
    return {
        "ok": True, "time": now(), "version": VERSION, "dir": str(DIR), "proto": proto, "proto_name": pname, "cli": cli,
        "backend": base, "bot": bot, "items": items, "stats": stats, "features": feats, "updates": updates,
        "updates_at": up.get("at", 0), "qq_upgrade": bool(suspect), "jobs": jobs,
        "install": {"secrets": secrets_files, "cost": cost, "group": (DIR / "private" / "group.txt").exists()},
        "versions": versions, "answers": {"memory": mem, "voice": voice, "video": "1" if video else ""},
        "game": {"on": bool(gm), "since": (gm or {}).get("since", 0), "can": voice == "4" or mem == "1",
                 "left": not gm and bool((read_json(game_file(), {}) or {}).get("on"))},  # 开着打游戏模式重启了、开机没恢复成
        "paths": {"backend": str(DIR / "private" / "backend.txt"), "private": str(DIR / "private")},
        "group_reply": group_reply_mode(),
        "meme_freq": meme_freq(),
    }


# ---------------------------------------------------------------- 日志（给页面看，打码）

AST_RX = re.compile(r"^\[(\d\d:\d\d:\d\d)(?:\.\d+)?\]\s+\[([^\]]+)\]\s+\[(\w+)\]\s+(?:\[[^\]]*\]:\s*)?(.*)$")
SL_RX = re.compile(r"^(\d\d:\d\d:\d\d)\s+(\w+)\s+(.*)$")
NC_RX = re.compile(r"^(?:\d\d-\d\d\s+)?(\d\d:\d\d:\d\d)\s+\[(\w+)\]\s+(.*)$")
NOISE = re.compile(r"\[WebUI\] (GET|POST|PUT|DELETE) |receive health armed|system face catalog")
BENIGN = re.compile(r"swigfaiss_avx")  # faiss 先试 AVX2 版、没有就退回普通版，不是错


def level_of(s):
    s = s.upper()
    if s.startswith(("ERR", "CRIT", "FATAL")):
        return "err"
    if s.startswith("WARN"):
        return "warn"
    if s in ("OK", "SUCCESS", "SUCC"):
        return "ok"
    if s.startswith(("DEBUG", "DEBG", "TRACE", "VERB")):
        return "debug"
    return "info"


def read_logs(src, n=200):
    if CLOUD:
        return cloud.read_logs(src, n)
    if src == "brain":
        f = DIR / "astrbot" / "run.log"
        if not f.exists():
            return {"missing": "没找到大脑的日志文件（启动器装的 AstrBot 日志在后台里看）。", "lines": []}
        rxs = [AST_RX]
    else:
        f = snowluma_log() if protocol() == "snowluma" else napcat_log()
        if not f:
            return {"missing": "没找到登 QQ 那个程序的日志。", "lines": []}
        rxs = [SL_RX, NC_RX]
    return parse_log_lines(tail_lines(f, 600_000, 1500), rxs, n, f.name)


def parse_log_lines(lines, rxs, n, fname):
    out = []
    for ln in lines:
        if NOISE.search(ln):
            continue
        rec = None
        for rx in rxs:
            m = rx.match(ln)
            if m:
                if rx is AST_RX:
                    t, who, lv, msg = m.groups()
                    msg = (f"[{who}] " if who not in ("Core",) else "") + msg
                else:
                    t, lv, msg = m.groups()
                rec = {"t": t, "lv": level_of(lv), "m": msg.strip()}
                break
        if rec is None:
            if out and out[-1]["lv"] == "err":
                out[-1]["m"] += "\n" + ln.strip()
                continue
            rec = {"t": "", "lv": "err" if ("Traceback" in ln or "Error" in ln) and not BENIGN.search(ln) else "info", "m": ln.strip()}
        if rec["lv"] == "debug":
            continue
        out.append(rec)
    out = out[-n:]
    for r in out:
        r["m"] = redact(r["m"])[:1200]
    out.reverse()
    errs = sum(1 for r in out if r["lv"] == "err")
    warns = sum(1 for r in out if r["lv"] == "warn")
    return {"lines": out, "errors": errs, "warns": warns, "file": fname}


# ---------------------------------------------------------------- 长动作

class Job:
    def __init__(self, kind, group, title):
        self.id = secrets.token_hex(6)
        self.kind, self.group, self.title = kind, group, title
        self.state, self.step, self.pct, self.msg = "running", "准备中…", 3, ""
        self.lines, self.data = [], {}
        self.t0, self.t1 = now(), None

    def set(self, step=None, pct=None):
        if step:
            self.step = step
        if pct is not None:
            self.pct = int(max(self.pct, min(99, pct)))

    def line(self, s):
        s = redact(s.strip())
        if s:
            self.lines = (self.lines + [s])[-30:]

    def done(self, ok, msg, **data):
        self.state = "ok" if ok else "fail"
        self.msg = msg
        self.pct = 100 if ok else self.pct
        self.data.update(data)
        self.t1 = now()
        drop_cache("net", "procs", "stats", "memes", "ollama")
        log(f"动作 {self.kind}：{'成功' if ok else '没成'} {msg}")

    def view(self):
        return {"id": self.id, "kind": self.kind, "group": self.group, "title": self.title, "state": self.state, "step": self.step,
                "pct": self.pct, "msg": self.msg, "lines": self.lines[-12:], "data": self.data, "secs": int((self.t1 or now()) - self.t0)}


JOBS = {}


def running_jobs():
    with LOCK:
        return [j.view() for j in JOBS.values() if j.state == "running" or (j.t1 and now() - j.t1 < 30)]


def start_job(kind, group, title, fn, *args):
    with LOCK:
        for j in JOBS.values():
            if j.group == group and j.state == "running":
                return j  # 同一件事已经在做了，接着看它
        j = Job(kind, group, title)
        JOBS[j.id] = j
        for k in [k for k, v in JOBS.items() if v.t1 and now() - v.t1 > 900]:
            JOBS.pop(k, None)

    def body():
        try:
            fn(j, *args)
        except Exception as e:  # noqa: BLE001
            log("动作出错：" + traceback.format_exc(limit=4))
            j.done(False, f"出错了（{type(e).__name__}）。点「复制求助信息」交给 Agent 看看。")
        if j.state == "running":
            j.done(False, "没做完。")
    threading.Thread(target=body, daemon=True).start()
    log(f"开始动作 {kind}")
    return j


def act_start_astrbot(job, restart=False):
    port = backend_port()
    if not is_cli_install():
        return job.done(False, "这台电脑的 AstrBot 是启动器装的：打开 AstrBot 启动器，点「启动」或「重启」。")
    if restart:
        job.set("让她先下线……", 8)
        pids = astrbot_pids()
        kill_pids(pids)
        if not wait_until(lambda: not listening(port), 25, job, 8, 20, "等大脑停下……"):
            return job.done(False, "大脑停不下来。把任务栏里「Dafeiyu-AstrBot」那个黑窗口关掉，再点一次「一键修复」。")
        time.sleep(2)
    elif listening(port):
        return job.done(True, "大脑本来就开着。")
    job.set("打开大脑（AstrBot）……", 22)
    launch_cmd(DIR / "start-astrbot.cmd")
    if not wait_until(lambda: listening(port), 150, job, 22, 82, "大脑在起来，大约半分钟到一分钟……"):
        return job.done(False, "等了两分半还没起来。点「看最近日志」看看卡在哪，或者点「复制求助信息」交给 Agent。")
    job.set("等小号连上……", 86)
    wait_until(qq_online, 40, job, 86, 98, "等小号连上……")
    if qq_online():
        return job.done(True, "大脑重启好了，小号也连上了。" if restart else "大脑开好了，小号也连上了。")
    return job.done(True, ("大脑重启好了" if restart else "大脑开好了") + "，小号还没连上：等一小会儿，或者点「重新登」。")


def act_relogin(job):
    if qq_online():
        return job.done(True, "小号本来就在线，不用重登。要是她在线却不回话，试试「重启登 QQ 的程序」。")
    cmd = DIR / "tools" / "relogin.cmd"
    if not cmd.exists():
        return job.done(False, "没找到掉线自救工具（tools 里的 relogin.cmd）。交给 Agent 重跑一下收尾那步。")
    job.set("弹出了一个黑色小窗口，照里面说的做……", 10)
    launch_cmd(cmd, visible=True)
    snow = protocol() == "snowluma"
    hint = "在桌面 QQ 的窗口里登小号：有头像点「登录」，是二维码就用小号的手机 QQ 扫" if snow else "能快速登录就不用管；弹出二维码就用小号的手机 QQ 扫一扫"
    t0 = now()
    while now() - t0 < 620:
        drop_cache("net")
        if qq_online():
            return job.done(True, "登上了，她回来了。那个黑窗口可以关了。")
        job.set(f"{hint}（最多等 10 分钟，已等 {int((now() - t0) // 60)} 分钟）", 10 + 80 * (now() - t0) / 620)
        time.sleep(3)
    return job.done(False, "10 分钟还没登上。看看那个黑窗口里写了什么；还不行就点「复制求助信息」。")


def act_restart_qq(job):
    snow = protocol() == "snowluma"
    root = DIR / ("SnowLuma" if snow else "NapCat")
    cmd = DIR / ("start-snowluma.cmd" if snow else "start-napcat.cmd")
    if not cmd.exists():
        return job.done(False, f"没找到 {cmd.name}，交给 Agent 看看。")
    name = "SnowLuma" if snow else "NapCat"
    job.set(f"停掉 {name}（只停工作目录里的，不碰你的桌面 QQ）……", 10)
    kill_under(root)
    wait_until(lambda: not procs_under(root), 15, job, 10, 25, f"等 {name} 停下……")
    time.sleep(2)
    job.set(f"重新打开 {name}……", 30)
    launch_cmd(cmd)
    if wait_until(qq_online, 75, job, 30, 96, "等小号连回来……"):
        return job.done(True, f"{name} 重启好了，小号连回来了。")
    if snow:
        return job.done(False, "SnowLuma 重新开了，小号还没连上。点「重新登」，在桌面 QQ 窗口里登一下。")
    return job.done(False, "NapCat 重新开了，小号还没登上。点「重新登」，会弹出二维码让你扫。")


def act_start_breeze(job):
    if listening(8137):
        return job.done(True, "Breeze 本来就开着。")
    cmd = DIR / "breeze-tts" / "start-breeze.cmd"
    if not cmd.exists():
        return job.done(False, "没找到 Breeze 的启动文件，交给 Agent 看看。")
    job.set("打开 Breeze……", 15)
    launch_cmd(cmd)
    if wait_until(lambda: listening(8137), 90, job, 15, 98, "Breeze 在加载声音模型，大约 20 秒……"):
        return job.done(True, "Breeze 开好了，她又能发语音了。")
    return job.done(False, "一分半还没开起来。看看任务栏里「Breeze TTS」那个窗口写了什么，或者点「复制求助信息」。")


def act_start_ollama(job):
    if listening(11434):
        return job.done(True, "Ollama 本来就开着。")
    exe = ollama_exe()
    if not exe:
        return job.done(False, "没找到 Ollama，从开始菜单打开它；找不到就交给 Agent。")
    job.set("打开 Ollama……", 15)
    launch_ollama()
    if not wait_until(lambda: listening(11434), 60, job, 15, 90, "等 Ollama 起来……"):
        return job.done(False, "一分钟还没开起来。从开始菜单打开 Ollama 试试。")
    # 端口刚通时模型列表常常还是空的（10-28 测试机误报过「bge-m3 不在」），等它一会儿
    if not wait_until(lambda: drop_cache("ollama") or any(m.startswith("bge-m3") for m in (ollama_models() or [])), 20):
        return job.done(False, "Ollama 开了，但记忆模型 bge-m3 不在。交给 Agent 重新下一下。")
    return job.done(True, "Ollama 开好了，她又能记事了。")


def ollama_pids():
    names = ("ollama app.exe", "ollama.exe", "ollama_llama_server.exe")
    if psutil:  # 只取进程名，不取路径：取一遍全部进程的路径，在登着两个账户的测试机上要二十几秒
        return [p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() in names]
    return [p["pid"] for p in process_list() if p["name"].lower() in names]


def port_tree(port, root):
    """占着 port 的程序和它的子进程（只认 root 里面的，不是就返回 []）；查不了返回 None，退回 kill_under。"""
    pid = net_snapshot()[0].get(port)
    if not pid or not psutil:
        return None
    try:
        p = psutil.Process(pid)
        if not under(p.exe(), root):
            return []
        return [pid] + [c.pid for c in p.children(recursive=True)]
    except Exception:  # noqa: BLE001
        return None


def act_game_on(job):
    """打游戏模式：关掉本机跑的语音（Breeze 占显存）和记忆（Ollama 占约 1.2 GB 内存），她照样打字聊天。
    先在后台关语音开关、停记忆插件，再关程序：不然每条消息都要先等连不上的记忆、语音重试七八秒才回。"""
    a = answers()
    voice, mem = a.get("voice") == "4", a.get("memory") == "1"
    if not (voice or mem):
        return job.done(False, "你的语音和记忆用的都是在线服务，不占这台电脑，打游戏不用管她。")
    g = game_mode() or {"on": True, "since": now(), "tts": False, "mem": False}  # 上次开到一半出错了就接着关，记下的「原来开着」不丢
    (DIR / "logs").mkdir(exist_ok=True)
    write_json(game_file(), g)
    game_boot_hook(True)
    notes = []
    if listening(backend_port()):
        job.set("跟大脑说一声：先不发语音、先不记事……", 10)
        tok = astr_login()
        if not tok:
            notes.append("大脑后台没登上（多半是在网页上改过后台密码），她每条消息会慢七八秒")
        else:
            try:
                g["tts"] = g.get("tts") or (voice and astr_tts(tok, False))
            except Exception as e:  # noqa: BLE001
                notes.append(f"语音开关没关上（{e}）")
            try:
                if mem and astr_plugin_on(tok, LM_PLUGIN):
                    astr_call(tok, "PATCH", "/plugins/enabled", {"plugin_id": LM_PLUGIN, "enabled": False}, 60)
                    g["mem"] = True
            except Exception as e:  # noqa: BLE001
                notes.append(f"记忆插件没停下（{e}）")
            write_json(game_file(), g)
    if voice and listening(8137):
        job.set("关掉 Breeze 语音，把显卡腾出来……", 40)
        pids = port_tree(8137, DIR / "breeze-tts")
        if pids is None:
            kill_under(DIR / "breeze-tts")
        else:
            kill_pids(pids)
        wait_until(lambda: not listening(8137), 20, job, 40, 60, "等 Breeze 关掉……")
        if listening(8137):
            notes.append("Breeze 没关掉，把任务栏里「Breeze TTS」那个窗口关了")
    if mem:
        job.set("关掉 Ollama 记忆，把内存腾出来……", 65)
        kill_pids(ollama_pids())
        wait_until(lambda: not listening(11434), 20, job, 65, 90, "等 Ollama 关掉……")
        if listening(11434):
            notes.append("Ollama 没关掉，在任务管理器里结束「ollama」")
    off = "、".join(x for x, y in (("语音", voice), ("记忆", mem)) if y)
    msg = f"打游戏模式开了：本机的{off}都关了。她照样回消息，只是只打字、这段时间说的事不记（以前记住的都还在）。打完点「打完了，恢复」；忘了点也没事，重启电脑会自己恢复。"
    return job.done(True, msg + ("另外：" + "；".join(notes) + "。" if notes else ""))


def act_game_off(job, boot=False):
    """打完了，恢复：先开程序（记忆插件开回来时要连得上 Ollama），再把后台的开关拨回去。
    boot=True 是开机自启那个快捷方式调的：开机自启本来就会开 Ollama 和 Breeze，先等它们自己起来，等不到再开。"""
    g = read_json(game_file(), {}) or {}
    if not g.get("on"):
        game_boot_hook(False)
        return job.done(True, "打游戏模式本来就关着。")
    a = answers()
    voice, mem = a.get("voice") == "4", a.get("memory") == "1"
    fails = []
    if mem:
        if boot and not listening(11434):
            wait_until(lambda: listening(11434), 120, job, 5, 15, "等 Ollama 自己起来……")
        if not listening(11434) and ollama_exe():
            job.set("打开 Ollama……", 15)
            launch_ollama()
            wait_until(lambda: listening(11434), 60, job, 15, 30, "等 Ollama 起来……")
        if listening(11434):
            job.set("把记忆模型装进内存……", 32)
            try:  # 先算一次，她下一条消息不用等加载
                req = urllib.request.Request("http://127.0.0.1:11434/api/embed", data=json.dumps({"model": "bge-m3", "input": "你好"}).encode(), method="POST")
                req.add_header("Content-Type", "application/json")
                urllib.request.urlopen(req, timeout=90).read()
            except Exception:  # noqa: BLE001
                pass
        else:
            fails.append("记忆（Ollama 没开起来，点记忆那一行的「打开 Ollama」）")
    if voice:
        cmd = DIR / "breeze-tts" / "start-breeze.cmd"
        if boot and not listening(8137):
            wait_until(lambda: listening(8137), 150, job, 35, 45, "等 Breeze 自己起来……")
        if not listening(8137) and cmd.exists():
            job.set("打开 Breeze 语音……", 45)
            launch_cmd(cmd)
            wait_until(lambda: listening(8137), 90, job, 45, 75, "Breeze 在加载声音模型，大约 20 秒……")
        if not listening(8137):
            fails.append("语音（Breeze 没开起来，点语音那一行的「打开 Breeze」）")
    if g.get("tts") or g.get("mem"):
        if boot:
            wait_until(lambda: listening(backend_port()), 300, job, 75, 80, "等大脑开起来……")
        job.set("跟大脑说一声：语音、记忆都回来了……", 80)
        tok = astr_login() if listening(backend_port()) else None
        if not tok:
            return job.done(False, "程序都开回来了，但大脑后台没登上，语音开关和记忆插件还关着。先把大脑打开，再点一次「打完了，恢复」。")
        try:
            if g.get("mem"):
                astr_call(tok, "PATCH", "/plugins/enabled", {"plugin_id": LM_PLUGIN, "enabled": True}, 180)
                if astr_plugin_on(tok, LM_PLUGIN) is False:
                    raise RuntimeError("记忆插件没开回来")
            if g.get("tts"):
                astr_tts(tok, True)
        except Exception as e:  # noqa: BLE001
            return job.done(False, f"后台开关没拨回去（{e}）。再点一次「打完了，恢复」；还不行就点「复制求助信息」。")
    try:
        game_file().unlink()
    except OSError:
        pass
    game_boot_hook(False)
    if fails:
        return job.done(False, "后台恢复好了，但" + "；".join(fails) + "。")
    return job.done(True, "恢复好了：她又能发语音、记事了。")


def act_import(job, block):
    a = answers()
    if block == "memory" and a.get("memory") not in ("1", "2"):
        return job.done(False, "你装的时候没选记忆。想要的话去「交给 Agent」。")
    if block == "voice" and a.get("voice") not in ("1", "2", "3", "4"):
        return job.done(False, "你装的时候没选语音。想要的话去「交给 Agent」。")
    if block == "video" and a.get("video") != "1":
        return job.done(False, "你装的时候没选看视频。想要的话去「交给 Agent」。")
    label = {"memes": "表情包", "memory": "记忆", "voice": "语音", "video": "看视频"}[block]
    logf = DIR / "logs" / f"import-{block}.log"
    st = import_log_state(block)
    if not (st and st["running"]):
        ps = SCRIPTS / "import-pack.ps1"
        if not ps.exists():
            return job.done(False, "没找到导入工具（tools 里的 import-pack.ps1）。交给 Agent 重跑一下收尾那步。")
        if not listening(backend_port()):
            return job.done(False, "大脑没开，补装不了。先把大脑开起来。")
        job.set(f"开始补装{label}……", 5)
        code, out = powershell(ps, "-Dir", DIR, "-Only", block, "-Background", timeout=90)
        for ln in out.splitlines()[-6:]:
            job.line(ln)
        if code != 0:
            last = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "")
            return job.done(False, f"没开始：{redact(last)[:160]}")
        t0 = now()
        while not logf.exists() and now() - t0 < 40:
            time.sleep(1)
    limit = 50 * 60 if block == "memes" else 15 * 60
    t0 = now()
    while now() - t0 < limit:
        st = import_log_state(block)
        if st and st.get("result"):
            r = st["result"]
            txt = result_text(r)
            return job.done(r["status"] in ("ok", "skip"), f"{label}{txt}。" + ("" if r["status"] == "ok" else "她照常聊天，过一阵可以再补装一次。"), result=r)
        if st:
            job.set(st.get("step") or f"补装{label}……", st.get("pct") if st.get("pct") is not None else 8 + 60 * (now() - t0) / limit)
            if not st["running"] and now() - st["mtime"] > 600:
                return job.done(False, "补装好像停住了（日志 10 分钟没动）。点「复制求助信息」交给 Agent 看看。")
        time.sleep(2)
    return job.done(False, "等太久了，补装还没完。它还在后台跑，过会儿再打开工作台看。")


def act_diag(job):
    ps = SCRIPTS / "diag.ps1"
    if not ps.exists():
        return job.done(False, "没找到 diag.ps1。")
    job.set("正在收集版本、端口和最近的日志，顺手打码……", 20)
    args = ["-Dir", DIR, "-Json"] + (["-NoClipboard"] if OPTS.no_clipboard else [])
    code, out = powershell(ps, *args, timeout=150)
    line = next((ln for ln in out.splitlines() if ln.startswith("DIAG_JSON ")), "")
    if not line:
        return job.done(False, "求助信息没生成出来：" + redact((out.strip().splitlines() or [""])[-1])[:160])
    d = json.loads(line[len("DIAG_JSON "):])
    preview = ""
    try:
        preview = redact("\n".join(Path(d["file"]).read_text(encoding="utf-8-sig").splitlines()[:140]))
    except Exception:  # noqa: BLE001
        pass
    LAST_FILES.add(norm(d.get("file", "")))
    job.done(True, "存到桌面了，那句话也复制好了。" if d.get("copied") else "存到桌面了。", file=d.get("file", ""), name=Path(d.get("file", "")).name,
             sentence=d.get("sentence", ""), code=d.get("code", ""), preview=preview, copied=bool(d.get("copied")))


def act_backup(job, memes=False):
    ps = SCRIPTS / "backup.ps1"
    if not ps.exists():
        return job.done(False, "没找到备份工具（tools 里的 backup.ps1）。交给 Agent 重跑一下收尾那步。")
    job.set("清点要备份的文件……", 5)
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps), "-Dir", str(DIR)] + (["-WithMemes"] if memes else [])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    total, out_file, last = 0, "", ""
    for raw in p.stdout:
        s = re.sub(r"^\[\d\d:\d\d:\d\d\]\s*", "", decode(raw).strip())
        if not s:
            continue
        last = s
        job.line(s)
        m = re.search(r"要备份 (\d+) 个文件", s)
        if m:
            total = int(m.group(1))
            job.set(s, 10)
        m = re.search(r"已打包 (\d+)/(\d+)", s)
        if m:
            job.set(s, 10 + 85 * int(m.group(1)) / max(1, int(m.group(2))))
        m = re.search(r"备份好了：(.+?\.zip)（", s)
        if m:
            out_file = m.group(1)
    p.wait()
    if p.returncode != 0 or not out_file:
        return job.done(False, "没备份成：" + redact(last)[:200])
    LAST_FILES.add(norm(out_file))
    size = ""
    try:
        size = f"{Path(out_file).stat().st_size / 1048576:.0f} MB"
    except Exception:  # noqa: BLE001
        pass
    job.done(True, f"备好了：桌面「{Path(out_file).name}」（{size}）。里面有你的密钥，别发给别人。", file=out_file, name=Path(out_file).name, size=size, files=total)


def pet_on():
    return (DIR / "private" / "pet-key.txt").exists()


def pet_health():
    """大肥鱼桥的 /health?deep=1：{"ok", "version", "astrbot": "ok"|"fail", "reason"}。连不上返回 None。不带口令也能看，不含秘密。"""
    def get():
        try:
            return http_json(f"http://127.0.0.1:{PET_PORT}/health?deep=1", timeout=6)
        except Exception:  # noqa: BLE001
            return None
    return cached("pet", 8, get)


def act_restart_petbridge(job):
    cmdf = DIR / "start-petbridge.cmd"
    if not cmdf.exists():
        return job.done(False, "没找到大肥鱼桥的启动文件（start-petbridge.cmd）。交给 Agent 重新配一下桌宠。")
    lp = net_snapshot()[0].get(PET_PORT)
    if lp and "dafeiyu_pet_bridge" in cmdline(lp):
        job.set("停掉旧的大肥鱼桥……", 15)
        kill_pids([lp])
        wait_until(lambda: drop_cache("net") or not listening(PET_PORT), 10)
    elif lp:
        return job.done(False, f"端口 {PET_PORT} 被别的程序占着，大肥鱼桥开不起来。点「复制求助信息」交给 Agent 看看。")
    job.set("打开大肥鱼桥……", 40)
    # 直接起 Python（不出窗口）；启动文件里认不出 Python 的路径就照原样跑启动文件（最小化窗口）
    m = re.search(r'"([^"]+?python[w]?\.exe)"\s+-X utf8\s+"([^"]+dafeiyu_pet_bridge\.py)"\s+--dir\s+"([^"]+)"', read_text(cmdf), re.I)
    if m and Path(m.group(1)).exists() and Path(m.group(2)).exists():
        launch_exe(m.group(1), ("-X", "utf8", m.group(2), "--dir", m.group(3)), cwd=DIR)
    else:
        launch_cmd(cmdf)
    drop_cache("pet")
    if not wait_until(lambda: drop_cache("net", "pet") or bool(pet_health()), 20, job, 40, 95, "等它起来……"):
        return job.done(False, "20 秒还没起来。看看工作目录 logs 里的 pet-bridge.log 最后几行，或者点「复制求助信息」。")
    h = pet_health() or {}
    if h.get("astrbot") != "ok":
        return job.done(False, "大肥鱼桥开起来了，但连不上她的大脑" + ("（大脑没开）" if not listening(backend_port()) else "（桌宠的 key 可能被删了，交给 Agent 重新配一下桌宠）") + "。")
    return job.done(True, "大肥鱼桥开好了，桌宠又能跟她说话了。")


AUTOSTART_PS = r"""
$ErrorActionPreference = 'Continue'
$Dir = $env:DFY_DIR; $proto = $env:DFY_PROTO; $mem = $env:DFY_MEM; $voice = $env:DFY_VOICE
$startup = [Environment]::GetFolderPath('Startup'); $ws = New-Object -ComObject WScript.Shell
function L($p, $t, $w, $d, $a = '') { $l = $ws.CreateShortcut($p); $l.TargetPath = $t; $l.Arguments = $a; $l.WorkingDirectory = $w; $l.WindowStyle = 7; $l.Description = $d; $l.Save(); "ADDED $(Split-Path $p -Leaf)" }
if ((Test-Path "$Dir\start-astrbot.cmd") -and -not (Test-Path "$startup\大肥鱼-AstrBot.lnk")) { L "$startup\大肥鱼-AstrBot.lnk" "$Dir\start-astrbot.cmd" $Dir '大肥鱼的大脑，别关' }
if ($proto -eq 'snowluma') {
  if (Test-Path "$startup\大肥鱼-NapCat.lnk") { Remove-Item "$startup\大肥鱼-NapCat.lnk"; 'REMOVED 大肥鱼-NapCat.lnk' }
  if ((Test-Path "$Dir\start-snowluma.cmd") -and -not (Test-Path "$startup\大肥鱼-SnowLuma.lnk")) { L "$startup\大肥鱼-SnowLuma.lnk" "$Dir\start-snowluma.cmd" $Dir '帮大肥鱼登 QQ（挂在桌面 QQ 上），别关' }
} else {
  if (Test-Path "$startup\大肥鱼-SnowLuma.lnk") { Remove-Item "$startup\大肥鱼-SnowLuma.lnk"; 'REMOVED 大肥鱼-SnowLuma.lnk' }
  if ((Test-Path "$Dir\start-napcat.cmd") -and -not (Test-Path "$startup\大肥鱼-NapCat.lnk")) { L "$startup\大肥鱼-NapCat.lnk" "$Dir\start-napcat.cmd" $Dir '帮大肥鱼登 QQ，别关' }
}
if ($mem -eq '1' -and -not (Test-Path "$startup\Ollama.lnk")) {
  $o = @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe", "$Dir\Ollama\ollama.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($o) { L "$startup\Ollama.lnk" "$env:SystemRoot\System32\conhost.exe" (Split-Path $o) '大肥鱼的记忆' "--headless `"$o`" serve" }
}
if ($voice -eq '4' -and -not (Get-ChildItem $startup -Filter '*Breeze*' -ErrorAction SilentlyContinue) -and (Test-Path "$Dir\breeze-tts\start-breeze.cmd")) {
  L "$startup\启动Breeze语音.lnk" "$Dir\breeze-tts\start-breeze.cmd" "$Dir\breeze-tts" '大肥鱼的语音'
}
if ((Test-Path "$Dir\private\pet-key.txt") -and (Test-Path "$Dir\start-petbridge.cmd") -and -not (Test-Path "$startup\大肥鱼-桌宠桥.lnk")) {
  L "$startup\大肥鱼-桌宠桥.lnk" "$env:SystemRoot\System32\conhost.exe" $Dir '桌宠跟大肥鱼说话走这里，别删' "--headless cmd.exe /c `"$Dir\start-petbridge.cmd`""
}
'DONE'
"""


def act_autostart_fix(job):
    import base64
    a = answers()
    env = dict(os.environ, DFY_DIR=str(DIR), DFY_PROTO=protocol(), DFY_MEM=a.get("memory", ""), DFY_VOICE=a.get("voice", ""))
    enc = base64.b64encode(AUTOSTART_PS.encode("utf-16-le")).decode()
    job.set("补开机自启……", 30)
    code, out = run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc], timeout=60, env=env)
    added = [ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith(("ADDED ", "REMOVED "))]
    if "DONE" not in out:
        return job.done(False, "没补上：" + redact(out.strip()[-160:]))
    return job.done(True, ("补好了：" + "、".join(added) + "。下次开机她会自己起来。") if added else "本来就都设好了。")


# ---------------------------------------------------------------- 更新安装 Skill 和工作台（只换文件，旧版先备份）

SKILL_BLOCKS = {"core": "人设和基础设置", "plugins": "插件", "video": "看视频", "memory": "记忆", "voice": "语音", "memes": "表情包"}


def skill_dirs():
    """装着 dafeiyu-install 的技能文件夹：finish.ps1 记下的那个，加上 DeepSeek Harness、WorkBuddy 的默认位置（两个都装了就都换）。"""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    cands = [Path(read_text(SCRIPTS / "skill-home.txt"))] if read_text(SCRIPTS / "skill-home.txt") else []
    cands += [home / ".agents" / "skills" / "dafeiyu-install", home / ".dsh" / "skills" / "dafeiyu-install", home / ".workbuddy" / "skills" / "dafeiyu-install"]
    out, seen = [], set()
    for c in cands:
        if (c / "SKILL.md").is_file() and norm(c) not in seen:
            seen.add(norm(c))
            out.append(c)
    return out


def download(urls, dest, job, p0, p1, min_bytes):
    """挨个地址试着下，下够 min_bytes 才算数。返回用上的地址，都不行返回空。"""
    tmp = dest.with_name(dest.name + ".part")
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "dafeiyu-workbench/" + VERSION})
            with urllib.request.urlopen(req, timeout=20) as r, open(tmp, "wb") as f:
                total, got = int(r.headers.get("Content-Length") or 0), 0
                while True:
                    b = r.read(65536)
                    if not b:
                        break
                    f.write(b)
                    got += len(b)
                    if total:
                        job.set(f"下载新版……{got * 100 // total}%", p0 + (p1 - p0) * got / total)
            if tmp.stat().st_size >= min_bytes:
                os.replace(tmp, dest)
                return u
        except Exception:  # noqa: BLE001
            continue
    try:
        tmp.unlink()
    except OSError:
        pass
    return ""


def act_skill_update(job):
    if SCRIPTS.name.lower() != "tools":
        return job.done(False, "这个工作台是从 Skill 文件夹直接开的，没法自己更新。")
    if any((import_log_state(b) or {}).get("running") for b in SKILL_BLOCKS) or \
            (CLOUD and any((cloud.imp_state(cloud.SNAP["data"] or {}, b) or {}).get("running") for b in SKILL_BLOCKS)):
        return job.done(False, "正在补装东西，等它装完再更新。")
    job.set("查 GitHub 上的新版……", 5)
    cur, ls = (cloud.skill_version_effective() if CLOUD else skill_version()), latest_skill()
    if not ls:
        return job.done(False, "查不到新版（GitHub 和加速站都没连上），过会儿再点。")
    if ver_tuple(ls["version"]) <= ver_tuple(cur):
        return job.done(True, f"已经是最新（{cur}）。")
    if CLOUD and ver_tuple(ls["version"]) < ver_tuple(skill_version()):
        return job.done(True, f"这台电脑上的（{skill_version()}）比 GitHub 上最新的（{ls['version']}）还新，没换。")
    dl = DIR / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    zp = dl / f"dafeiyu-install-{ls['version']}.zip"
    url = f"https://github.com/{SKILL_REPO}/releases/latest/download/dafeiyu-install.zip"
    via = ls.get("_via", "")
    if not download([b + url for b in [via] + [p for p in GH_PROXIES if p != via]], zp, job, 8, 40, 100_000):
        return job.done(False, "新版没下下来（网络不通），过会儿再点。")

    job.set("检查下好的文件……", 42)
    ext = Path(tempfile.mkdtemp(prefix="skill-", dir=str(dl)))
    try:
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            if any(n.startswith(("/", "\\")) or ":" in n or ".." in Path(n).parts for n in names):
                raise ValueError("里面的路径不对")
            for need in ("dafeiyu-install/SKILL.md", "dafeiyu-install/version.json", "dafeiyu-install/scripts/finish.ps1"):
                if need not in names:
                    raise ValueError("缺 " + need)
            z.extractall(ext)
    except (zipfile.BadZipFile, ValueError, OSError) as e:
        shutil.rmtree(ext, ignore_errors=True)
        return job.done(False, f"下好的新版不完整（{e}），过会儿再点。")
    src = ext / "dafeiyu-install"
    nv = read_json(src / "version.json", {}) or {}
    new = str(nv.get("version") or ls["version"])

    # 旧的先整份拷进 <工作目录>\旧版\（不留在技能文件夹里：Agent 会把两份同名 Skill 都读进去）
    bk = DIR / "旧版" / f"{datetime.now():%m%d-%H%M}-Skill{cur}"
    job.set("备份旧版……", 48)
    shutil.copytree(SCRIPTS, bk / "tools", dirs_exist_ok=True)
    replaced = []
    for i, d in enumerate(skill_dirs()):
        job.set(f"换上新版 Skill（{d.parent.parent.name}）……", 52 + 3 * i)
        keep = bk / f"skill-{d.parent.parent.name.lstrip('.')}"
        shutil.copytree(d, keep, dirs_exist_ok=True)
        try:
            shutil.rmtree(d)
            shutil.copytree(src, d)
        except OSError as e:
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(keep, d, dirs_exist_ok=True)
            shutil.rmtree(ext, ignore_errors=True)
            return job.done(False, f"换 Skill 没成（{type(e).__name__}，可能有文件正被打开），旧版已经放回去了。关掉 DeepSeek Harness 再点一次。")
        replaced.append(d)

    pack, newpack = DIR / "dafeiyu-config-pack", src / "pack" / "dafeiyu-config-pack"
    kit_err = ""
    if CLOUD:  # 配置包和导入工具在服务器上用：整包传上去换掉 kit/dafeiyu-install（旧的服务器上挪进 old/）
        kit_err = cloud.push_kit(job, zp, 60, 74)
    elif (newpack / "tools" / "import_dafeiyu.py").is_file():
        job.set("换上新版配置包……", 65)
        try:
            if pack.is_dir():
                shutil.move(str(pack), str(bk / "dafeiyu-config-pack"))
            shutil.copytree(newpack, pack)
        except OSError:
            shutil.copytree(newpack, pack, dirs_exist_ok=True)  # 挪不走（有文件开着）就盖在上面
        old_voice = bk / "dafeiyu-config-pack" / "voice" / "breeze"  # 做声音时留底的参考音、原话、原录音，不在发布包里
        if old_voice.is_dir():
            shutil.copytree(old_voice, pack / "voice" / "breeze", dirs_exist_ok=True)

    job.set("换上新版工作台……", 75)
    code, out = powershell((replaced[0] if replaced else src) / "scripts" / "finish.ps1", "-Dir", DIR, timeout=180)
    for ln in out.splitlines()[-8:]:
        job.line(ln)
    shutil.rmtree(ext, ignore_errors=True)
    try:
        zp.unlink()
    except OSError:
        pass
    if skill_version() != new:
        return job.done(False, "新版 Skill 换上了，工作台没换成：" + redact(out.strip()[-160:]) + " 交给 Agent 重跑一次收尾那步。")

    msg = f"更新好了：{cur} → {new}。"
    if kit_err:
        msg += f"这台电脑上的换好了，服务器上那份没换成（{kit_err}），过会儿再点一次「一键更新」。"
    if not replaced:
        msg += "没找到装着的 Skill（可能挪过），只换了工作台和配置包；Agent 那边要用新版，照资料文档重新装一次 Skill。"
    re_ = "、".join(SKILL_BLOCKS.get(b, b) for b in nv.get("reimport") or [])
    if re_:
        msg += f"这一版还要重新导入{re_}：在 DeepSeek Harness 里说「用大肥鱼的安装 Skill，按新版说明重新导入{re_}{'（她装在云服务器上）' if CLOUD else ''}」。"
    msg += f"旧版备份在工作目录的「旧版」文件夹里。工作台马上自己重开一下。"
    update_disk(updates={})  # 新的工作台起来重新查，别拿旧结果说「有新版」
    drop_cache("versions")
    job.done(True, msg, restart=True)
    background(restart_self)


def restart_self():
    """换了新版工作台：等页面拿到结果，起一个新的接着用同一个端口和 token（窗口不用关），自己退出。"""
    time.sleep(3)
    if CLOUD:
        cloud.stop()  # 隧道让新的那个自己开
    env = dict(os.environ, DFY_WB_TOKEN=TOKEN)
    subprocess.Popen([sys.executable, "-X", "utf8", str(HERE / "server.py"), "--dir", str(DIR), "--no-open", "--takeover", "--port", str(PORT)],
                     cwd=tempfile.gettempdir(), env=env, creationflags=NO_WINDOW | 0x00000200, close_fds=True)
    log("换了新版，重开")
    SERVER.shutdown()


LAST_FILES = set()


def open_folder(which, path=""):
    targets = {"dir": DIR, "private": DIR / "private", "logs": DIR / "logs"}
    if which == "file":
        if norm(path) not in LAST_FILES or not Path(path).exists():
            raise ValueError("只能打开刚生成的文件")
        subprocess.Popen(["explorer.exe", f"/select,{path}"], creationflags=NO_WINDOW)
        return
    if which == "backend":
        p = DIR / "private" / ("cloud-backend.txt" if CLOUD else "backend.txt")
        if p.exists():
            subprocess.Popen(["explorer.exe", f"/select,{p}"], creationflags=NO_WINDOW)
            return
        which = "private"
    t = targets.get(which)
    if not t or not Path(t).exists():
        raise ValueError("没有这个文件夹")
    if IS_WIN:
        os.startfile(str(t))  # noqa: S606


ACTIONS = {
    "start_astrbot": ("brain", "打开大脑", lambda j, b: act_start_astrbot(j)),
    "restart_astrbot": ("brain", "重启大脑", lambda j, b: act_start_astrbot(j, restart=True)),
    "relogin": ("qq", "重新登 QQ", lambda j, b: act_relogin(j)),
    "restart_qq": ("qq", "重启登 QQ 的程序", lambda j, b: act_restart_qq(j)),
    "start_breeze": ("breeze", "打开 Breeze 语音", lambda j, b: act_start_breeze(j)),
    "start_ollama": ("ollama", "打开 Ollama", lambda j, b: act_start_ollama(j)),
    "game_on": ("game", "打游戏模式", lambda j, b: act_game_on(j)),
    "game_off": ("game", "打完了，恢复", lambda j, b: act_game_off(j)),
    "diag": ("diag", "复制求助信息", lambda j, b: act_diag(j)),
    "backup": ("backup", "备份到桌面", lambda j, b: act_backup(j, bool(b.get("memes")))),
    "autostart_fix": ("autostart", "补开机自启", lambda j, b: act_autostart_fix(j)),
    "restart_petbridge": ("pet", "重开大肥鱼桥", lambda j, b: act_restart_petbridge(j)),
    "group_reply": ("grouprep", "群里回复时", lambda j, b: act_group_reply(j, str(b.get("mode", "")))),
    "meme_freq": ("memefreq", "表情包发多少", lambda j, b: act_meme_freq(j, str(b.get("level", "")))),
    "skill_update": ("skillup", "更新安装 Skill", lambda j, b: act_skill_update(j)),
}


# ---------------------------------------------------------------- 网页服务

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "dafeiyu-workbench"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 不往 stderr 写（pythonw 没有），也不记请求
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None, cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self' data:; font-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def guard(self, need_token):
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            self.send(403, {"ok": False, "error": "host"})
            return False
        if need_token and not hmac.compare_digest(self.headers.get("X-DFY-Token", ""), TOKEN):
            self.send(401, {"ok": False, "error": "token"})
            return False
        return True

    def send_asset(self, name):
        """发 assets\\ 里的一个文件：只认文件名本身（不许带 / \\ .. 盘符这些往别处走的写法），只认白名单扩展名。图和字体不含秘密，可以缓存。"""
        ctype = ASSET_TYPES.get(Path(name).suffix.lower())
        if not ctype or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", name) or ".." in name:
            return self.send(404, {"ok": False})
        try:
            base = ASSETS.resolve()
            f = (base / name).resolve()
            if f.parent != base or not f.is_file():
                return self.send(404, {"ok": False})
            st = f.stat()
            etag = f'"{st.st_size:x}-{st.st_mtime_ns:x}"'
            cache = "public, max-age=86400"
            if self.headers.get("If-None-Match") == etag:
                return self.send(304, b"", ctype, {"ETag": etag}, cache)
            return self.send(200, f.read_bytes(), ctype, {"ETag": etag}, cache)
        except OSError:
            return self.send(404, {"ok": False})

    def do_GET(self):
        global LAST_SEEN
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            if not self.guard(False):
                return
            try:
                html = (HERE / "index.html").read_bytes()
            except OSError:
                return self.send(500, "index.html 不见了", "text/plain; charset=utf-8")
            html = html.replace(b"__WB_V__", VERSION.encode("ascii"))  # 图和字体网址带上版本号，更新后不用旧缓存
            return self.send(200, html, "text/html; charset=utf-8")
        if path.startswith("/assets/"):
            if not self.guard(False):  # 跟发 index.html 一样只查 Host（token 在网址 # 后面，图和字体的请求带不上）
                return
            return self.send_asset(urllib.parse.unquote(path[len("/assets/"):]))
        if path == "/favicon.ico":
            return self.send(204, b"", "image/x-icon")
        if not path.startswith("/api/"):
            return self.send(404, {"ok": False})
        if not self.guard(True):
            return
        q = urllib.parse.parse_qs(url.query)
        try:
            if path == "/api/ping":
                return self.send(200, {"ok": True, "version": VERSION, "pid": os.getpid()})  # pid：页面靠它看出工作台重开过
            if path == "/api/status":
                LAST_SEEN = now()
                return self.send(200, cached("status", 2, collect_status))
            if path == "/api/logs":
                src = (q.get("src") or ["brain"])[0]
                return self.send(200, read_logs("brain" if src == "brain" else "qq"))
            if path.startswith("/api/job/"):
                LAST_SEEN = now()
                j = JOBS.get(path.rsplit("/", 1)[-1])
                return self.send(200, j.view() if j else {"state": "gone"})
        except ConnectionError:  # 窗口关了、请求半路断了，不算出错
            return
        except Exception:  # noqa: BLE001
            log("接口出错：" + traceback.format_exc(limit=4))
            return self.send(500, {"ok": False, "error": "server"})
        return self.send(404, {"ok": False})

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        if not self.guard(True):
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in ALLOWED_HOSTS}:
            return self.send(403, {"ok": False, "error": "origin"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > 8192:
            return self.send(413, {"ok": False})
        try:
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(body, dict):
                body = {}
        except ValueError:
            body = {}
        name = url.path[len("/api/action/"):] if url.path.startswith("/api/action/") else ""
        try:
            acts = cloud.ACTIONS if CLOUD else ACTIONS
            if name == "import":
                blk = str(body.get("block", ""))
                if blk not in ("memes", "memory", "voice", "video"):
                    return self.send(400, {"ok": False, "error": "block"})
                j = start_job("import", "import-" + blk, "补装" + {"memes": "表情包", "memory": "记忆", "voice": "语音", "video": "看视频"}[blk], cloud.act_import if CLOUD else act_import, blk)
                return self.send(200, {"ok": True, "job": j.view()})
            if name in acts:
                group, title, fn = acts[name]
                j = start_job(name, group, title, fn, body)
                drop_cache("status")
                return self.send(200, {"ok": True, "job": j.view()})
            if name == "open_folder":
                open_folder(str(body.get("which", "")), str(body.get("path", "")))
                return self.send(200, {"ok": True})
            if name == "refresh":
                drop_cache("status", "net", "procs", "stats", "memes", "ollama")
                background(check_balance, True)  # 云端模式：连同状态一起去服务器上再问一遍
                if body.get("updates"):
                    background(check_updates, True)
                return self.send(200, {"ok": True})
        except ValueError as e:
            return self.send(400, {"ok": False, "error": str(e)})
        except ConnectionError:
            return
        except Exception:  # noqa: BLE001
            log("动作接口出错：" + traceback.format_exc(limit=4))
            return self.send(500, {"ok": False, "error": "server"})
        return self.send(404, {"ok": False})

    def do_HEAD(self):
        self.send(405, b"", "text/plain")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # Windows 上 SO_REUSEADDR 会让两个程序抢同一个端口

    def server_bind(self):
        if IS_WIN and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        import socketserver
        socketserver.TCPServer.server_bind(self)  # 跳过 HTTPServer 的 getfqdn（有的电脑上要卡好几秒）
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    def handle_error(self, request, client_address):  # 连接半路断了不往外打 traceback
        if not isinstance(sys.exc_info()[1], ConnectionError):
            log("连接出错：" + traceback.format_exc(limit=3))


def find_edge():
    cands = []
    if IS_WIN:
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                        cands.append(winreg.QueryValue(k, None))
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            if os.environ.get(env):
                cands.append(os.path.join(os.environ[env], "Microsoft", "Edge", "Application", "msedge.exe"))
    for c in cands:
        if c and Path(c.strip('"')).exists():
            return c.strip('"')
    return ""


def open_window(port, token):
    url = f"http://127.0.0.1:{port}/#t={token}"
    edge = find_edge()
    if edge:
        try:
            subprocess.Popen([edge, f"--app={url}", "--window-size=1100,720"], close_fds=True)
            return
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)


def alive(info):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/ping", headers={"X-DFY-Token": info["token"], "Host": f"127.0.0.1:{info['port']}"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:  # noqa: BLE001
        return False


def watchdog():
    while True:
        time.sleep(15)
        busy = any(j["state"] == "running" for j in running_jobs()) or (CLOUD and cloud.in_use())
        if not busy and now() - LAST_SEEN > OPTS.idle:
            log("3 分钟没人看，退出")
            threading.Thread(target=SERVER.shutdown, daemon=True).start()
            return


def main():
    global DIR, STATE, TOKEN, PORT, ALLOWED_HOSTS, OPTS, SERVER, LAST_SEEN, CLOUD
    ap = argparse.ArgumentParser(description="大肥鱼工作台的本机小服务")
    ap.add_argument("--dir", default="")
    ap.add_argument("--open", action="store_true", help="开 Edge 小窗口（已经有服务在跑就只开窗口）")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--idle", type=int, default=180, help="多少秒没人看就退出")
    ap.add_argument("--no-clipboard", action="store_true", help="测试用：复制求助信息时不动剪贴板")
    ap.add_argument("--takeover", action="store_true", help="工作台自己更新后重开用：等旧的退出、端口空出来，接着用同一个端口和 token")
    ap.add_argument("--game-off", action="store_true", help="开机自启用：打游戏模式没关就重启了，把语音、记忆开回来就退")
    OPTS = ap.parse_args()
    if OPTS.dir:
        DIR = Path(OPTS.dir).resolve()
    elif HERE.parent.name.lower() == "tools":
        DIR = HERE.parent.parent
    else:
        ap.error("从 Skill 文件夹直接跑要给 --dir <工作目录>")
    STATE = DIR / "logs" / "workbench"
    STATE.mkdir(parents=True, exist_ok=True)
    CLOUD = cloud.detect(DIR)
    if CLOUD and OPTS.port == DEFAULT_PORT:
        OPTS.port = DEFAULT_PORT + 1  # 同一台电脑上本机那套的工作台也开着时，别抢同一个端口
    load_secret_values()
    if OPTS.game_off:
        j = Job("game_off", "game", "打完了，恢复")
        try:
            act_game_off(j, boot=True)
        except Exception:  # noqa: BLE001
            log("开机恢复出错：" + traceback.format_exc(limit=4))
        return

    info = read_json(STATE / "server.json", {}) or {}
    if not OPTS.takeover and info.get("port") and info.get("token") and alive(info):
        if not OPTS.no_open:
            open_window(info["port"], info["token"])
        return

    reuse = os.environ.pop("DFY_WB_TOKEN", "") if OPTS.takeover else ""
    for _ in range(30 if OPTS.takeover else 0):
        try:
            SERVER = Server(("127.0.0.1", OPTS.port), Handler)
            break
        except OSError:
            time.sleep(0.5)
    for p in [] if SERVER else ([OPTS.port] if OPTS.port else []) + [0]:  # 接管成了就不用再找端口
        try:
            SERVER = Server(("127.0.0.1", p), Handler)
            break
        except OSError:
            if p == OPTS.port:  # 固定端口被占：多半是刚双击过一次、前一个还没写好 server.json，等它几秒
                for _ in range(8):
                    time.sleep(0.5)
                    info = read_json(STATE / "server.json", {}) or {}
                    if info.get("port") and info.get("token") and alive(info):
                        if not OPTS.no_open:
                            open_window(info["port"], info["token"])
                        return
            continue
    if SERVER is None:
        log("端口都占满了，起不来")
        return
    PORT = SERVER.server_address[1]
    TOKEN = reuse if reuse and PORT == OPTS.port else secrets.token_urlsafe(24)
    ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    write_json(STATE / "server.json", {"port": PORT, "token": TOKEN, "pid": os.getpid(), "started": now(), "version": VERSION})
    log(f"起来了，端口 {PORT}，工作目录 {DIR}" + ("（云端模式）" if CLOUD else ""))
    LAST_SEEN = now()
    threading.Thread(target=watchdog, daemon=True).start()
    if CLOUD:
        cloud.start()  # 后台问服务器（顺手查余额）、开隧道
    else:
        background(check_balance)
    background(check_updates)
    if OPTS.open and not OPTS.no_open:
        open_window(PORT, TOKEN)
    try:
        SERVER.serve_forever(poll_interval=1)
    finally:
        if CLOUD:
            cloud.stop()
        cur = read_json(STATE / "server.json", {}) or {}
        if cur.get("pid") == os.getpid():
            try:
                (STATE / "server.json").unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
