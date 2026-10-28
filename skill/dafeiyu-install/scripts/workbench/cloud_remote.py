# -*- coding: utf-8 -*-
"""云端大肥鱼工作台的服务器端小帮手：跑在云服务器上，Python 3.6 起都能跑（Alibaba Cloud Linux 3 自带的就是 3.6）。

谁来跑它：观众电脑上的工作台（workbench\\cloud.py）每次用 ssh 连上服务器，把本文件从标准输入喂给
    sudo -n python3 - <子命令> [参数] --dir /opt/dafeiyu
跑完就没了，服务器上不留这个文件。只读写 --dir 里的东西，只动 napcat、astrbot 两个容器（加上 tools 容器跑导入工具）；
不改网络、防火墙、ssh 和 Docker 的设置（autostart 只做 systemctl enable docker）。
输出：除了 backup（zip 写到标准输出），最后一行都是 `DFY_JSON {...}`。private 里的 key、口令、密码不出现在输出里，日志里也换成 ****。

子命令：
  status [--balance] [--meta]   服务器、两个容器、QQ 在不在、她今天的数字、各项设置。
                                --balance 顺手用服务器上的 DeepSeek key 查余额（只回金额），--meta 查是哪家云、哪个地域
  logs brain|qq [行数]           最近的日志（打过码）
  qr                            NapCat 现在的二维码（base64）和登录走到哪了
  start brain|qq|docker / restart brain|qq
  setting group_reply off|mention|quote|both / setting meme_freq few|mid|many
  import memes|memory|voice|video   放后台补装（docker compose run tools 跑导入工具），日志在 logs/import-<块>.log
  quicklogin [--recreate]       让 NapCat 记住小号（.env 写 NAPCAT_ACCOUNT，compose.yml 加 ACCOUNT）；--recreate 且 NapCat 在跑就重建它
  autostart                     systemctl enable docker
  backup [--memes]              private、compose.yml、.env、data、napcat/config 打成 zip 写到标准输出，进度写到标准错误
  kit <zip>                     换上新版安装 Skill（服务器上的 kit/dafeiyu-install，旧的挪进 old/）
"""
import base64
import calendar
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

try:
    import urllib.request as urlreq
    import urllib.error as urlerr
except Exception:  # noqa: BLE001
    urlreq = urlerr = None

HELPER_VERSION = 1
LM_PLUGIN = "astrbot_plugin_livingmemory"
MEME_NAMES = {"blue-fat-fish-meme-pack": "蓝色大肥鱼表情包", "deepseek-fanart-meme-pack": "DeepSeek 二创表情包"}
GROUP_REPLY = {"off": (False, False), "mention": (True, False), "quote": (False, True), "both": (True, True)}
MEME_FREQ = {"few": 30, "mid": 60, "many": 100}
BLOCKS = ("memes", "memory", "voice", "video", "core", "plugins")
RESULT_RX = re.compile(r"^RESULT\s+(\w+)=(\w+)(?:\s+reason=(\S+))?(?:\s+next=(.*))?$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def parse_args(argv):
    pos, flags, vals = [], set(), {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dir" and i + 1 < len(argv):
            vals["dir"] = argv[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            flags.add(a[2:])
        else:
            pos.append(a)
        i += 1
    return pos, flags, vals


POS, FLAGS, VALS = parse_args(sys.argv[1:])
D = Path(VALS.get("dir") or "/opt/dafeiyu")
DATA = D / "data"
PRIV = D / "private"
KIT = D / "kit" / "dafeiyu-install"
PACK = KIT / "pack" / "dafeiyu-config-pack"


# ---------------------------------------------------------------- 小工具

def out(obj):
    sys.stdout.flush()
    sys.stdout.buffer.write(("DFY_JSON " + json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def err(msg):
    sys.stderr.buffer.write((msg + "\n").encode("utf-8"))
    sys.stderr.buffer.flush()


def read_text(p, default=""):
    try:
        return Path(p).read_text(encoding="utf-8-sig").strip()
    except Exception:  # noqa: BLE001
        return default


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
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


def sh(cmd, timeout=40, cwd=None, binary=False):
    """跑一个命令，返回 (退出码, 输出)。binary 时输出是 bytes，标准错误另外丢掉。"""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if binary else subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=timeout, cwd=str(cwd) if cwd else None)
    except subprocess.TimeoutExpired:
        return -1, b"" if binary else "超时了"
    except OSError as e:
        return -2, b"" if binary else type(e).__name__
    return p.returncode, p.stdout if binary else p.stdout.decode("utf-8", "replace")


def compose():
    code, _ = sh(["docker", "compose", "version"], 15)
    return ["docker", "compose"] if code == 0 else ["docker-compose"]


def rfc_epoch(s):
    """Docker 的 2026-10-28T08:40:33.123456789Z → 秒；0001-01-01 那种（没起过）→ 0。"""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", str(s or ""))
    if not m or m.group(1) == "0001":
        return 0
    return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))


def mask_qq(s):
    s = str(s or "")
    return s[:2] + "****" + s[-2:] if len(s) >= 5 else ("****" if s else "")


# ---------------------------------------------------------------- 打码（private 里的值、.env 里的值、QQ 号）

SECRETS, QQS = [], []


def load_secrets():
    vals, qq = set(), set()
    try:
        files = list(PRIV.iterdir()) if PRIV.is_dir() else []
    except OSError:
        files = []
    for f in files:
        try:
            if not f.is_file() or f.stat().st_size > 20000:
                continue
        except OSError:
            continue
        n = f.name.lower()
        raw = read_text(f)
        if n == "group.txt" or n.endswith("-qq.txt"):
            qq.update(re.findall(r"\d{5,}", raw))
            continue
        if n.endswith(".json") or n == "protocol.txt":  # answers.json 只有选项和路径
            continue
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        if lines and all(":" in ln.replace("：", ":") for ln in lines):
            for k, v in read_kv(f).items():
                if "url" in k or k in ("username", "user", "账号", "用户名"):
                    continue
                if len(v) >= 4:
                    vals.add(v)
        elif len(raw) >= 4:
            vals.add(raw)
    for line in read_text(D / ".env").splitlines():
        if "=" in line:
            v = line.split("=", 1)[1].strip().strip("\"'")
            if len(v) >= 8:
                vals.add(v)
            elif line.strip().upper().startswith("NAPCAT_ACCOUNT=") and v:
                qq.add(v)
    for f in (D / "napcat" / "config").glob("onebot11_*.json"):
        m = re.search(r"_(\d{5,})\.json$", f.name)
        if m:
            qq.add(m.group(1))
    SECRETS[:] = sorted(vals, key=len, reverse=True)
    QQS[:] = sorted(qq, key=len, reverse=True)


RX = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-****"),
    (re.compile(r"(?i)(txz\.qq\.com/p\?k=)\S+"), r"\1****"),
    (re.compile(r"(?i)((?:access_?token|api_?key|apikey|secret|password|passwd|token|密码)[\"']?\s*[:=：]\s*[\"']?)[^\s\"',;&}]+"), r"\1****"),
    (re.compile(r"(?i)([?&]token=)[^\s&\"']+"), r"\1****"),
    (re.compile(r"(?<![\d.])\d{7,}(?!\d)"), lambda m: m.group(0)[:2] + "****" + m.group(0)[-2:]),
]


def redact(s):
    s = str(s)
    for v in SECRETS:
        if v in s:
            s = s.replace(v, "****")
    for v in QQS:
        if v in s:
            s = re.sub(r"(?<!\d)" + re.escape(v) + r"(?!\d)", mask_qq(v), s)
    for rx, rep in RX:
        s = rx.sub(rep, s)
    return s


# ---------------------------------------------------------------- 服务器和容器

def meminfo():
    m = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            m[k] = int(v.split()[0]) // 1024
    except Exception:  # noqa: BLE001
        pass
    return m


def os_name():
    for line in read_text("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def host_facts():
    m = meminfo()
    h = {"os": os_name(), "cpus": os.cpu_count() or 0, "mem_total": m.get("MemTotal", 0), "mem_avail": m.get("MemAvailable", m.get("MemFree", 0)),
         "swap_total": m.get("SwapTotal", 0), "swap_free": m.get("SwapFree", 0)}
    try:
        st = os.statvfs(str(D if D.exists() else "/"))
        h["disk_total"] = round(st.f_blocks * st.f_frsize / 1073741824, 1)
        h["disk_free"] = round(st.f_bavail * st.f_frsize / 1073741824, 1)
    except OSError:
        pass
    try:
        h["uptime"] = int(float(read_text("/proc/uptime").split()[0]))
    except (ValueError, IndexError):
        h["uptime"] = 0
    h["load"] = read_text("/proc/loadavg").split()[:3]
    return h


def containers():
    code, txt = sh(["docker", "inspect", "astrbot", "napcat"], 20)
    out_ = {}
    try:
        arr = json.loads(txt[txt.find("["):]) if "[" in txt else []
    except ValueError:
        arr = []
    for c in arr:
        name = str(c.get("Name", "")).lstrip("/")
        st = c.get("State") or {}
        env = (c.get("Config") or {}).get("Env") or []
        img = str((c.get("Config") or {}).get("Image") or "")
        ports = ((c.get("NetworkSettings") or {}).get("Ports") or {})
        hp = 0
        for k, v in ports.items():
            if k.startswith(("6185/", "6099/")) and v:
                hp = int(v[0].get("HostPort") or 0)
        mv = re.search(r":v?(\d+(?:\.\d+)+)", img)
        out_[name] = {
            "exists": True, "running": bool(st.get("Running")), "status": st.get("Status", ""), "started": rfc_epoch(st.get("StartedAt")),
            "finished": rfc_epoch(st.get("FinishedAt")), "exit": st.get("ExitCode", 0), "oom": bool(st.get("OOMKilled")), "pid": st.get("Pid", 0),
            "restarts": c.get("RestartCount", 0), "policy": ((c.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name", ""),
            "image": img.rsplit("/", 1)[-1], "version": mv.group(1) if mv else "",
            "account": any(str(e).startswith("ACCOUNT=") and str(e)[8:].strip() for e in env), "port": hp,
        }
    for n in ("astrbot", "napcat"):
        out_.setdefault(n, {"exists": False, "running": False, "status": "missing"})
    if code not in (0, 1) and not arr:
        out_["_err"] = redact(txt.strip()[-200:])
    return out_


def qq_conns(pid):
    """大脑的网络里，6199（QQ 程序连大脑用的）上已经建立的连接有几条。NapCat 连上来就是 1。"""
    n = 0
    if not pid:
        return 0
    for f in ("tcp", "tcp6"):
        try:
            for line in open("/proc/%d/net/%s" % (pid, f)):
                p = line.split()
                if len(p) > 3 and p[1].endswith(":1837") and p[3] == "01":
                    n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def http_code(port):
    if not urlreq or not port:
        return 0
    try:
        with urlreq.urlopen("http://127.0.0.1:%d/" % port, timeout=4) as r:
            return r.status
    except urlerr.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


def bot_uin():
    best, t = "", 0
    for f in (D / "napcat" / "config").glob("onebot11_*.json"):
        m = re.search(r"_(\d{5,})\.json$", f.name)
        if m and f.stat().st_mtime >= t:
            best, t = m.group(1), f.stat().st_mtime
    return best


NC_LINE = re.compile(r"^(\d\d)-(\d\d)\s+(\d\d):(\d\d):(\d\d)\s+\[(\w+)\]\s+(.*)$")
RISK = re.compile(r"安全风险|存在风险|风险提示|冻结|异常登录|登录异常|被迫下线|下线通知|KickedOffLine|被踢|已下线|账号.{0,6}(?:异常|限制|封)")


def nc_time(mo, d, hh, mm, ss):
    now = datetime.now()
    y = now.year - (1 if int(mo) > now.month + 1 else 0)
    try:
        return time.mktime(datetime(y, int(mo), int(d), int(hh), int(mm), int(ss)).timetuple())
    except ValueError:
        return 0


def napcat_state(c):
    """读 NapCat 这次启动以来的日志：最后一次出二维码、被扫了、登上了、最后一次收到/发出消息、有没有风控字样。"""
    st = {"qr_at": 0, "scan_at": 0, "login_at": 0, "recv_at": 0, "send_at": 0, "risk": "", "risk_at": 0, "expired": 0}
    if not c.get("exists"):
        return st
    cmd = ["docker", "logs", "--tail", "4000"]
    if c.get("started"):
        cmd += ["--since", datetime.utcfromtimestamp(c["started"] - 5).strftime("%Y-%m-%dT%H:%M:%SZ")]
    code, txt = sh(cmd + ["napcat"], 25)
    for raw in txt.splitlines():
        ln = ANSI.sub("", raw).strip()
        m = NC_LINE.match(ln)
        if not m:
            if "WebSocket反向服务" in ln and "已启动" in ln:
                st["login_at"] = max(st["login_at"], st["scan_at"], st["qr_at"]) or time.time()
            continue
        t = nc_time(*m.groups()[:5])
        msg = m.group(7)
        if "请扫描下面的二维码" in msg or "二维码已保存到" in msg:
            st["qr_at"] = t
        elif "二维码已被扫描" in msg:
            st["scan_at"] = t
        elif "ErrCode: 3" in msg.replace("  ", " "):
            st["expired"] += 1
        if " 接收 <- " in msg:
            st["recv_at"] = t
            st["login_at"] = max(st["login_at"], t)
        elif " 发送 -> " in msg:
            st["send_at"] = t
            st["login_at"] = max(st["login_at"], t)
        elif "本账号数据/缓存目录" in msg or re.match(r"^[^\[\s][^|]{0,40} \| ", msg):
            st["login_at"] = max(st["login_at"], t)
        if RISK.search(msg):
            st["risk"], st["risk_at"] = redact(msg)[:160], t
    st["waiting"] = bool(c.get("running") and st["qr_at"] and st["qr_at"] >= st["login_at"])
    st["scanned"] = bool(st["waiting"] and st["scan_at"] >= st["qr_at"])
    return st


# ---------------------------------------------------------------- 她的数据（data 目录，只读）

def sqlite_ro(p):
    return sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=3)


def cmd_config():
    return read_json(DATA / "cmd_config.json", {}) or {}


def memory_wiring(cfg):
    w = {"installed": (DATA / "plugins" / LM_PLUGIN).is_dir(), "on": None, "provider": False, "local": False, "ollama": False, "sf": False, "model": "", "key": False}
    if not w["installed"]:
        return w
    db = DATA / "data_v4.db"
    if db.exists():
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
    lc = read_json(DATA / "config" / ("%s_config.json" % LM_PLUGIN), {}) or {}
    pid = str((lc.get("provider_settings") or {}).get("embedding_provider_id") or "")
    p = next((x for x in cfg.get("provider") or [] if isinstance(x, dict) and pid and str(x.get("id")) == pid), None)
    if p:
        b = str(p.get("embedding_api_base") or p.get("api_base") or "")
        w.update(provider=True, local=bool(re.search(r"//(127\.0\.0\.1|localhost|ollama)[:/]", b)), ollama=":11434" in b, sf="siliconflow" in b,
                 model=str(p.get("embedding_model") or p.get("model") or ""), key=bool(str(p.get("embedding_api_key") or p.get("api_key") or "").strip()))
    return w


def meme_info():
    info = {"installed": [], "images": 0, "expected": []}
    pack = read_json(PACK / "settings" / "pack.json", {}) or {}
    info["expected"] = [m["id"] for m in pack.get("meme_packs", []) if m.get("use", True) and m.get("id")]
    md = DATA / "plugin_data" / "meme_manager"
    reg = read_json(md / "registry.json", {}) or {}
    for p in reg.get("installed_packs", []):
        if p.get("enabled", True) and p.get("id"):
            n = 0
            try:
                n = sum(1 for f in (md / "packs" / p["id"]).rglob("*") if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"))
            except OSError:
                pass
            info["installed"].append({"id": p["id"], "name": p.get("name") or MEME_NAMES.get(p["id"], p["id"]), "n": n})
            info["images"] += n
    return info


def today_stats():
    out_ = {}
    mid = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    mid_utc = datetime.utcfromtimestamp(time.mktime(mid.timetuple())).strftime("%Y-%m-%d %H:%M:%S")
    db = DATA / "data_v4.db"
    if db.exists():
        try:
            c = sqlite_ro(db)
            try:
                out_["msgs"] = int(c.execute("select coalesce(sum(count),0) from platform_stats where timestamp >= ?", (mid.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0])
            except sqlite3.Error:
                pass
            try:
                calls, tokens = c.execute("select count(*), coalesce(sum(token_input_other + token_input_cached + token_output),0) from provider_stats "
                                          "where agent_type='internal' and status != 'error' and created_at >= ?", (mid_utc,)).fetchone()
                out_["replies"], out_["tokens"] = int(calls), int(tokens)
            except sqlite3.Error:
                pass
            c.close()
        except sqlite3.Error:
            pass
    lm = DATA / "plugin_data" / LM_PLUGIN / "livingmemory.db"
    if lm.exists():
        try:
            c = sqlite_ro(lm)
            try:
                out_["memories"] = int(c.execute("select count(*) from documents").fetchone()[0])
            finally:
                c.close()
        except sqlite3.Error:
            # 系统自带的 sqlite 太老（Alibaba Cloud Linux 3 是 3.26），读不了记忆插件的表结构：借大脑容器里的 Python 数一下
            code, txt = sh(["docker", "exec", "astrbot", "python", "-c",
                            "import sqlite3;c=sqlite3.connect('file:/AstrBot/data/plugin_data/%s/livingmemory.db?mode=ro',uri=True);"
                            "print(c.execute('select count(*) from documents').fetchone()[0])" % LM_PLUGIN], 20)
            if code == 0 and txt.strip().isdigit():
                out_["memories"] = int(txt.strip())
    return out_


def settings(cfg):
    ps = cfg.get("platform_settings") or {}
    cur = (bool(ps.get("reply_with_mention")), bool(ps.get("reply_with_quote")))
    gr = next((k for k, v in GROUP_REPLY.items() if v == cur), "off")
    mc = read_json(DATA / "config" / "meme_manager_config.json", {}) or {}
    v = ((mc.get("generation") or {}).get("emotion") or {}).get("probability")
    mf = "" if isinstance(v, bool) or not isinstance(v, (int, float)) else min(MEME_FREQ, key=lambda k: abs(MEME_FREQ[k] - v))
    wl = bool(ps.get("enable_id_white_list")) if cfg else None
    vc = read_json(DATA / "config" / "astrbot_plugin_dafeiyu_video_config.json", {}) or {}
    video = {"plugin": (DATA / "plugins" / "astrbot_plugin_dafeiyu_video").is_dir(), "asr": "online" if str(vc.get("asr_key") or "").strip() else "none"}
    return gr, mf, wl, video


def answers():
    a = read_json(PRIV / "answers.json", {}) or {}
    return {k: ("" if a.get(k) is None else str(a.get(k))) for k in ("memory", "voice", "video")} if isinstance(a, dict) else {}


def import_states():
    res = {}
    for blk in BLOCKS:
        f = D / "logs" / ("import-%s.log" % blk)
        if not f.exists():
            continue
        try:
            raw = f.read_bytes()[-200000:].decode("utf-8", "replace")
        except OSError:
            continue
        lines = [ANSI.sub("", ln) for ln in raw.splitlines()]
        r = None
        for ln in lines:
            m = RESULT_RX.match(ln.strip())
            if m and m.group(1) == blk:
                r = {"status": m.group(2), "reason": m.group(3) or "", "next": (m.group(4) or "").strip()}
        alive = False
        pidf = D / "logs" / ("import-%s.pid" % blk)
        try:
            pid = int(read_text(pidf) or 0)
            alive = bool(pid) and os.path.exists("/proc/%d" % pid)
        except ValueError:
            pass
        mt = f.stat().st_mtime
        res[blk] = {"mtime": mt, "result": r, "running": r is None and (alive or time.time() - mt < 180), "tail": [redact(x)[:300] for x in lines[-40:]]}
    return res


def kit_version():
    return str((read_json(KIT / "version.json", {}) or {}).get("version") or "")


# ---------------------------------------------------------------- DeepSeek 余额、云厂商

def deepseek_key():
    f = PRIV / "deepseek-key.txt"
    raw = read_text(f)
    if ":" in raw.replace("：", ":"):
        for k, v in read_kv(f).items():
            if "key" in k:
                return v
    return raw.split()[0] if raw else ""


def balance():
    key = deepseek_key()
    if not key:
        return {"state": "nokey"}
    try:
        req = urlreq.Request("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
        with urlreq.urlopen(req, timeout=10) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        infos = j.get("balance_infos") or []
        info = next((i for i in infos if i.get("currency") == "CNY"), infos[0] if infos else {})
        return {"state": "ok", "amount": str(info.get("total_balance", "")), "currency": info.get("currency", "CNY"), "available": bool(j.get("is_available", True))}
    except urlerr.HTTPError as e:
        return {"state": "auth" if e.code in (401, 403) else "http", "code": e.code}
    except Exception as e:  # noqa: BLE001
        return {"state": "net", "why": type(e).__name__}


def meta():
    for prov, url in (("aliyun", "http://100.100.100.200/latest/meta-data/region-id"), ("tencent", "http://metadata.tencentyun.com/latest/meta-data/placement/region")):
        try:
            with urlreq.urlopen(url, timeout=1.5) as r:
                reg = r.read(64).decode("ascii", "replace").strip()
            if re.match(r"^[a-z0-9\-]{3,40}$", reg):
                return {"provider": prov, "region": reg}
        except Exception:  # noqa: BLE001
            continue
    return {"provider": "", "region": ""}


# ---------------------------------------------------------------- 子命令

def cmd_status():
    c = containers()
    a, n = c.get("astrbot", {}), c.get("napcat", {})
    cfg = cmd_config()
    gr, mf, wl, video = settings(cfg)
    env = read_text(D / ".env")
    comp = read_text(D / "compose.yml")
    code_en, en = sh(["systemctl", "is-enabled", "docker"], 10)
    code_ac, ac = sh(["systemctl", "is-active", "docker"], 10)
    uin = bot_uin()
    res = {
        "ok": True, "v": HELPER_VERSION, "now": time.time(), "dir": str(D), "exists": (D / "compose.yml").exists(),
        "host": host_facts(), "docker": {"enabled": en.strip(), "active": ac.strip()}, "c": c,
        "http": http_code(a.get("port") or 6185) if a.get("running") else 0,
        "conns": qq_conns(a.get("pid")) if a.get("running") else 0,
        "bot": mask_qq(uin), "nc": napcat_state(n),
        "answers": answers(), "mem": memory_wiring(cfg), "memes": meme_info(), "stats": today_stats(),
        "group_reply": gr, "meme_freq": mf, "whitelist": wl, "video": video,
        "kit": kit_version(), "pack": str((read_json(PACK / "settings" / "pack.json", {}) or {}).get("version") or ""),
        "imports": import_states(), "private": sorted(f.name for f in PRIV.iterdir() if f.is_file()) if PRIV.is_dir() else [],
        "quick": {"uin": bool(uin), "env": bool(re.search(r"(?m)^NAPCAT_ACCOUNT=\S+", env)), "compose": bool(re.search(r"(?m)^\s*-\s*ACCOUNT=", comp))},
    }
    if "balance" in FLAGS:
        res["balance"] = balance()
    if "meta" in FLAGS:
        res["meta"] = meta()
    out(res)


NOISE = re.compile(r"^\s*$|^[0-9a-f]{32,}$|^[\s█▀▄]+$|^\[[IWE]\] <|^如果控制台二维码无法扫码")


def cmd_logs():
    src = POS[1] if len(POS) > 1 else "brain"
    try:
        n = max(20, min(1500, int(POS[2]))) if len(POS) > 2 else 300
    except ValueError:
        n = 300
    name = "napcat" if src == "qq" else "astrbot"
    code, txt = sh(["docker", "logs", "--tail", str(max(n * 3, 1500 if name == "napcat" else 0)), name], 30)  # NapCat 的日志一大半是 QQ 自己的十六进制、二维码字符画，先多拿点再滤
    if code != 0 and ("No such container" in txt or "not found" in txt.lower()):
        return out({"ok": False, "missing": "服务器上没有 %s 这个容器。" % name})
    lines = [ANSI.sub("", ln).rstrip() for ln in txt.splitlines()]
    lines = [redact(ln)[:1500] for ln in lines if not NOISE.search(ln)][-n:]
    out({"ok": True, "lines": lines, "file": "docker logs " + name})


def cmd_qr():
    c = containers()
    n, a = c.get("napcat", {}), c.get("astrbot", {})
    res = {"ok": True, "running": bool(n.get("running")), "exists": bool(n.get("exists")), "online": qq_conns(a.get("pid")) > 0 if a.get("running") else False,
           "nc": napcat_state(n), "png": "", "png_at": 0, "bot": mask_qq(bot_uin())}
    if n.get("running") and res["nc"].get("waiting"):
        code, t = sh(["docker", "exec", "napcat", "stat", "-c", "%Y", "/app/napcat/cache/qrcode.png"], 15)
        try:
            res["png_at"] = int(t.strip().splitlines()[-1])
        except (ValueError, IndexError):
            res["png_at"] = 0
        if res["png_at"]:
            code, b = sh(["docker", "exec", "napcat", "cat", "/app/napcat/cache/qrcode.png"], 15, binary=True)
            if code == 0 and b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) < 400000:
                res["png"] = base64.b64encode(b).decode("ascii")
    out(res)


def cmd_start(restart):
    what = POS[1] if len(POS) > 1 else ""
    if what == "docker" and not restart:
        code, txt = sh(["systemctl", "start", "docker"], 90)
        return out({"ok": code == 0, "out": redact(txt.strip()[-300:])})
    name = {"brain": "astrbot", "qq": "napcat"}.get(what)
    if not name:
        return out({"ok": False, "out": "不认识 %s" % what})
    c = containers().get(name, {})
    if restart and c.get("running"):
        code, txt = sh(["docker", "restart", "-t", "15", name], 90)
    elif name == "napcat" or not c.get("exists"):
        code, txt = sh(compose() + ["up", "-d", name], 300, cwd=D)  # 改过 compose.yml（比如加了快速登录）也一起生效
    else:
        code, txt = sh(["docker", "start", name], 60)
    out({"ok": code == 0, "out": redact(txt.strip()[-300:])})


def astr_api(method, path, body=None, tok=None, timeout=30):
    port = containers().get("astrbot", {}).get("port") or 6185
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urlreq.Request("http://127.0.0.1:%d/api/v1%s" % (port, path), data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urlreq.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace") or "{}")
    if isinstance(j, dict) and j.get("status") == "error":
        raise RuntimeError(str(j.get("message"))[:120])
    return j.get("data") if isinstance(j, dict) else j


def astr_login():
    kv = read_kv(PRIV / "backend.txt")
    pw = kv.get("password", "")
    if not pw:
        return None
    for p in (pw, hashlib.md5(pw.encode()).hexdigest()):
        try:
            d = astr_api("POST", "/auth/login", {"username": kv.get("username", "astrbot"), "password": p}, None, 15)
            if isinstance(d, dict) and d.get("token"):
                return d["token"]
        except Exception:  # noqa: BLE001
            continue
    return None


def cmd_setting():
    kind = POS[1] if len(POS) > 1 else ""
    val = POS[2] if len(POS) > 2 else ""
    if (kind == "group_reply" and val not in GROUP_REPLY) or (kind == "meme_freq" and val not in MEME_FREQ) or kind not in ("group_reply", "meme_freq"):
        return out({"ok": False, "why": "bad"})
    tok = astr_login()
    if not tok:
        return out({"ok": False, "why": "login"})
    try:
        if kind == "group_reply":
            cfg = astr_api("GET", "/config-profiles/default", None, tok)["config"]
            ps = cfg.setdefault("platform_settings", {})
            ps["reply_with_mention"], ps["reply_with_quote"] = GROUP_REPLY[val]
            astr_api("PUT", "/config-profiles/default", cfg, tok)
        else:
            if not any(isinstance(p, dict) and p.get("name") == "meme_manager" for p in astr_api("GET", "/plugins", None, tok) or []):
                return out({"ok": False, "why": "noplugin"})
            conf = astr_api("GET", "/plugins/config?plugin_id=meme_manager", None, tok) or {}
            conf = conf.get("config", conf)
            conf.setdefault("generation", {}).setdefault("emotion", {})["probability"] = MEME_FREQ[val]
            astr_api("PUT", "/plugins/config", {"plugin_id": "meme_manager", "config": conf}, tok, 60)
    except Exception as e:  # noqa: BLE001
        return out({"ok": False, "why": "api", "msg": redact(str(e))[:160]})
    out({"ok": True})


def cmd_import():
    blk = POS[1] if len(POS) > 1 else ""
    if blk not in ("memes", "memory", "voice", "video"):
        return out({"ok": False, "why": "bad"})
    st = import_states().get(blk)
    if st and st["running"]:
        return out({"ok": True, "already": True})
    tool = PACK / "tools" / "import_dafeiyu.py"
    if not tool.is_file():
        return out({"ok": False, "why": "nokit"})
    if not (PRIV / "answers.json").is_file():
        return out({"ok": False, "why": "noanswers"})
    if not containers().get("astrbot", {}).get("running"):
        return out({"ok": False, "why": "brain"})
    logs = D / "logs"
    logs.mkdir(exist_ok=True)
    logf = logs / ("import-%s.log" % blk)
    with open(str(logf), "wb") as fp:
        fp.write(("▶ %s 工作台开始补装 %s\n" % (datetime.now().strftime("%m-%d %H:%M:%S"), blk)).encode("utf-8"))
    rel = str(tool.relative_to(D / "kit").as_posix())
    cmd = compose() + ["run", "--rm", "-T", "-e", "PYTHONUNBUFFERED=1", "tools", "kit/" + rel, "--answers", "/dafeiyu/private/answers.json", "--only", blk]
    fp = open(str(logf), "ab")
    p = subprocess.Popen(cmd, cwd=str(D), stdout=fp, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    (logs / ("import-%s.pid" % blk)).write_text(str(p.pid), encoding="utf-8")
    out({"ok": True, "pid": p.pid})


def set_line(text, key, value):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(key + "="):
            lines[i] = "%s=%s" % (key, value)
            break
    else:
        lines.append("%s=%s" % (key, value))
    return "\n".join(lines) + "\n"


def add_account_env(comp):
    """compose.yml 的 napcat 那段 environment 里加一行 ACCOUNT（有了就不动）。返回新文本，没找到地方返回 None。"""
    lines = comp.splitlines()
    in_nc, done = False, False
    for ln in lines:  # 先整段看一遍：已经有 ACCOUNT 就不动
        if re.match(r"^  \S", ln):
            in_nc = ln.strip().startswith("napcat:")
        if in_nc and re.match(r"^\s+-\s*ACCOUNT=", ln):
            return comp
    in_nc = False
    for i, ln in enumerate(lines):
        if re.match(r"^  \S", ln):
            in_nc = ln.strip().startswith("napcat:")
        if in_nc and re.match(r"^\s+-\s*WEBUI_TOKEN=", ln):
            ind = re.match(r"^(\s+)", ln).group(1)
            lines.insert(i + 1, ind + "- ACCOUNT=${NAPCAT_ACCOUNT:-}   # 快速登录：重启后不用再扫码（工作台加的）")
            done = True
            break
    return "\n".join(lines) + "\n" if done else None


def cmd_quicklogin():
    uin = bot_uin()
    if not uin:
        return out({"ok": False, "why": "nouin"})
    stamp = datetime.now().strftime("%m%d-%H%M%S")
    old = D / "old"
    old.mkdir(exist_ok=True)
    comp_p, env_p = D / "compose.yml", D / ".env"
    comp, env = read_text(comp_p), read_text(env_p)
    new_comp = add_account_env(comp)
    if new_comp is None:
        return out({"ok": False, "why": "compose"})
    new_env = set_line(env, "NAPCAT_ACCOUNT", uin)
    changed = []
    if new_comp.strip() != comp.strip():
        shutil.copy2(str(comp_p), str(old / ("compose-%s.yml" % stamp)))
        comp_p.write_text(new_comp, encoding="utf-8")
        changed.append("compose.yml")
    if new_env.strip() != env.strip():
        shutil.copy2(str(env_p), str(old / ("env-%s" % stamp)))
        os.chmod(str(old / ("env-%s" % stamp)), 0o600)
        env_p.write_text(new_env, encoding="utf-8")
        os.chmod(str(env_p), 0o600)
        changed.append(".env")
    code, txt = sh(compose() + ["config", "-q"], 30, cwd=D)
    if code != 0:
        if "compose.yml" in changed:
            comp_p.write_text(comp + "\n", encoding="utf-8")
        if ".env" in changed:
            env_p.write_text(env + "\n", encoding="utf-8")
        return out({"ok": False, "why": "config", "msg": redact(txt.strip()[-200:])})
    recreated = False
    c = containers().get("napcat", {})
    if "recreate" in FLAGS and c.get("running") and not c.get("account"):
        code, txt = sh(compose() + ["up", "-d", "napcat"], 300, cwd=D)
        if code != 0:
            return out({"ok": False, "why": "up", "msg": redact(txt.strip()[-200:]), "changed": changed})
        recreated = True
    out({"ok": True, "changed": changed, "recreated": recreated, "bot": mask_qq(uin), "running": bool(c.get("running"))})


def cmd_autostart():
    code, txt = sh(["systemctl", "enable", "docker"], 60)
    _, en = sh(["systemctl", "is-enabled", "docker"], 10)
    # 容器没设自动重启的补上（教程的 compose.yml 本来就写了 restart: always，这里只管被改掉的）
    for name in ("astrbot", "napcat"):
        _, pol = sh(["docker", "inspect", "-f", "{{.HostConfig.RestartPolicy.Name}}", name], 15)
        if pol.strip() in ("no", ""):
            sh(["docker", "update", "--restart", "always", name], 30)
    out({"ok": en.strip() == "enabled", "out": redact(txt.strip()[-200:])})


def cmd_backup():
    memes = "memes" in FLAGS
    skip = [DATA / "temp", DATA / "site-packages"] + ([] if memes else [DATA / "plugin_data" / "meme_manager"])
    files = []
    for p in (PRIV, D / "napcat" / "config", DATA):
        if not p.is_dir():
            continue
        for root, dirs, names in os.walk(str(p)):
            r = Path(root)
            if any(r == s or s in r.parents for s in skip):
                dirs[:] = []
                continue
            for nm in names:
                f = r / nm
                if f.is_file() and not f.is_symlink():
                    files.append(f)
    for f in (D / "compose.yml", D / ".env"):
        if f.is_file():
            files.append(f)
    total = sum(f.stat().st_size for f in files)
    err("要备份 %d 个文件，%d MB%s" % (len(files), total // 1048576, "（含表情包）" if memes else "（表情包没带，重装时会重新下）"))
    readme = ("大肥鱼的备份（%s，从云服务器的 %s 打的包）\r\n\r\n里面有：\r\n"
              "  private\\       DeepSeek 的 key、AstrBot 后台密码、QQ 号等（别发给任何人）\r\n"
              "  data\\          她的人格、记忆、聊天记录、插件设置%s\r\n"
              "  napcat\\config\\ 登 QQ 那个程序的设置（不含 QQ 登录数据，换了服务器要重新扫码）\r\n"
              "  compose.yml、.env  服务器上两个容器怎么起\r\n\r\n"
              "怎么还原：跟 AI 帮手说「用桌面上的这个备份把大肥鱼搬到新服务器（或者搬回这台电脑）」，\r\n"
              "它会照安装 Skill 的说明先装好、再把这些放回去。\r\n") % (datetime.now().strftime("%Y-%m-%d %H:%M"), D,
                                                           "" if memes else "\r\n  （表情包没放进来，重装时会重新下）")
    zf = zipfile.ZipFile(sys.stdout.buffer, "w", zipfile.ZIP_DEFLATED)
    zf.writestr("还原说明.txt", ("﻿" + readme).encode("utf-8"))
    zf.writestr("备份信息.json", json.dumps({"cloud": True, "dir": str(D), "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "memes": memes}, ensure_ascii=False))
    t0, miss = time.time(), 0
    for i, f in enumerate(files, 1):
        try:
            zf.write(str(f), str(f.relative_to(D).as_posix()))
        except OSError:
            miss += 1
        if time.time() - t0 >= 3:
            err("已打包 %d/%d 个文件" % (i, len(files)))
            t0 = time.time()
    zf.close()
    sys.stdout.buffer.flush()
    err("DFY_DONE %d %d" % (len(files), miss))


def cmd_kit():
    zp = Path(POS[1]) if len(POS) > 1 else None
    if not zp or not zp.is_file() or D not in zp.resolve().parents:
        return out({"ok": False, "why": "nozip"})
    stamp = datetime.now().strftime("%m%d-%H%M%S")
    tmp = D / ("kit.new-" + stamp)
    try:
        with zipfile.ZipFile(str(zp)) as z:
            names = z.namelist()
            if any(n.startswith(("/", "\\")) or ":" in n or ".." in n.replace("\\", "/").split("/") for n in names):
                raise ValueError("里面的路径不对")
            for need in ("dafeiyu-install/SKILL.md", "dafeiyu-install/version.json", "dafeiyu-install/pack/dafeiyu-config-pack/tools/import_dafeiyu.py"):
                if need not in names:
                    raise ValueError("缺 " + need)
            z.extractall(str(tmp))
    except (zipfile.BadZipFile, ValueError, OSError) as e:
        shutil.rmtree(str(tmp), ignore_errors=True)
        return out({"ok": False, "why": "zip", "msg": str(e)[:120]})
    old = D / "old"
    old.mkdir(exist_ok=True)
    (D / "kit").mkdir(exist_ok=True)
    was = kit_version()
    if KIT.exists():
        shutil.move(str(KIT), str(old / ("kit-%s-%s" % (was or "old", stamp))))
    shutil.move(str(tmp / "dafeiyu-install"), str(KIT))
    shutil.rmtree(str(tmp), ignore_errors=True)
    try:
        zp.unlink()
    except OSError:
        pass
    out({"ok": True, "was": was, "now": kit_version()})


def main():
    cmd = POS[0] if POS else "status"
    load_secrets()
    if cmd == "status":
        return cmd_status()
    if cmd == "logs":
        return cmd_logs()
    if cmd == "qr":
        return cmd_qr()
    if cmd in ("start", "restart"):
        return cmd_start(cmd == "restart")
    if cmd == "setting":
        return cmd_setting()
    if cmd == "import":
        return cmd_import()
    if cmd == "quicklogin":
        return cmd_quicklogin()
    if cmd == "autostart":
        return cmd_autostart()
    if cmd == "backup":
        return cmd_backup()
    if cmd == "kit":
        return cmd_kit()
    out({"ok": False, "why": "unknown"})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "why": "crash", "msg": redact("%s: %s" % (type(e).__name__, e))[:300]})
