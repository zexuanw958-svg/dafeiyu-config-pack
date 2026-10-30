# -*- coding: utf-8 -*-
"""工作台的云端模式：她装在云服务器上（<工作目录>\\private\\cloud.txt 写着服务器地址，这台电脑上没有 AstrBot）。

工作台还是在这台电脑上跑、长得一样，server.py 认出云端模式后，状态、日志、动作都转到这里：
    每次用 Windows 自带的 ssh 连上服务器，把同目录的 cloud_remote.py 从标准输入喂给服务器上的 python3（不是 root 就 sudo -n），
    服务器上不留文件。后台线程大约 8 秒问一次（页面开着时），/api/status 直接给最近一次的结果，页面不用等 ssh。
另开一条 ssh 隧道：本机 16185 → 服务器 6185（AstrBot 后台）、16099 → 6099（NapCat 后台），工作台退出时关掉自己开的那条。
cloud.txt 认这几行（只有 host 是必须的）：
    host: 1.2.3.4        user: root（默认）      port: 22（默认）     key: cloud_key（默认，private 里的私钥）
    dir: /opt/dafeiyu（默认）  expire: 2027-10-28（到期日，工作台里也能填）  provider: aliyun|tencent（查不到时才用）
秘密：服务器上的 key、口令只在服务器上读，cloud_remote.py 回来的东西已经打过码；这边再按本机 private 里的值打一遍。
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import webbrowser
import zipfile
from datetime import date, datetime
from pathlib import Path

S = None  # server.py 导入本文件后填上（cloud.S = server 模块）
HERE = Path(__file__).resolve().parent
REMOTE = HERE / "cloud_remote.py"
TUN_BRAIN, TUN_NAPCAT = 16185, 16099
PROVIDER = {"aliyun": "阿里云", "tencent": "腾讯云"}
CONSOLE = {"aliyun": "https://swasnext.console.aliyun.com/servers", "tencent": "https://console.cloud.tencent.com/lighthouse/instance/index"}
REGION = {"beijing": "北京", "shanghai": "上海", "hangzhou": "杭州", "shenzhen": "深圳", "guangzhou": "广州", "chengdu": "成都", "qingdao": "青岛",
          "zhangjiakou": "张家口", "huhehaote": "呼和浩特", "wulanchabu": "乌兰察布", "heyuan": "河源", "nanjing": "南京", "fuzhou": "福州",
          "wuhan": "武汉", "chongqing": "重庆", "hongkong": "香港", "singapore": "新加坡", "tokyo": "东京", "seoul": "首尔"}

STATE_TX = {"ok": "正常", "bad": "出问题", "warn": "要注意", "off": "没开", "unk": "查不到", "busy": "正在弄"}
SNAP = {"data": None, "at": 0, "err": None, "err_at": 0, "busy": False, "pc_at": 0}
SNAP_LOCK = threading.Lock()
TUN = {"p": None, "next": 0, "log": None}
TUN_LOCK = threading.Lock()
STOP = threading.Event()


class CloudError(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code, self.text = code, text


def detect(d):
    """工作目录是云端那种：cloud.txt 写了服务器，这台电脑上又没装 AstrBot。"""
    kv = S.read_kv(Path(d) / "private" / "cloud.txt")
    return bool(kv.get("host") or kv.get("ip")) and not (Path(d) / "start-astrbot.cmd").exists() and not (Path(d) / "astrbot" / "data").is_dir()


def on():
    return bool(S and S.CLOUD)


def conf():
    kv = S.read_kv(S.DIR / "private" / "cloud.txt")
    key = kv.get("key") or "cloud_key"
    rdir = kv.get("dir") or "/opt/dafeiyu"
    host = kv.get("host") or kv.get("ip") or ""
    user = kv.get("user") or "root"
    return {"host": host if re.fullmatch(r"[A-Za-z0-9.:\-\[\]]{1,255}", host) else "",
            "user": user if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]{0,31}", user) else "root",
            "port": kv.get("port") if str(kv.get("port", "")).isdigit() else "22",
            "key": Path(key) if os.path.isabs(key) else S.DIR / "private" / key,
            "dir": rdir if re.fullmatch(r"/[A-Za-z0-9._/\-]{1,200}", rdir) else "/opt/dafeiyu",
            "expire": kv.get("expire") or kv.get("到期") or "", "provider": (kv.get("provider") or "").lower()}


# ---------------------------------------------------------------- ssh

def ssh_exe():
    if S.IS_WIN:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        for c in (os.path.join(root, "System32", "OpenSSH", "ssh.exe"), os.path.join(root, "Sysnative", "OpenSSH", "ssh.exe")):
            if os.path.exists(c):
                return c
    return shutil.which("ssh") or "ssh"


def ssh_base(c=None):
    c = c or conf()
    kh = str(S.DIR / "private" / "cloud_known_hosts").replace("\\", "/")
    return [ssh_exe(), "-i", str(c["key"]), "-p", c["port"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-o", "StrictHostKeyChecking=accept-new",
            "-o", f'UserKnownHostsFile="{kh}"', "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR", f"{c['user']}@{c['host']}"]


def remote_cmd(args, c=None):
    c = c or conf()
    sudo = "" if c["user"] == "root" else "sudo -n "
    return ("P=$(command -v python3 || echo /usr/libexec/platform-python); " + sudo + "env PYTHONIOENCODING=utf-8 $P - "
            + " ".join(shlex.quote(str(a)) for a in args) + " --dir " + shlex.quote(c["dir"]))


ERRS = [
    ("hostkey", r"REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed",
     "服务器的指纹变了（多半是重装过系统）。为了安全先不连：确认是你自己重装的，就删掉 private 里的 cloud_known_hosts 再试；不确定就交给 Agent。"),
    ("keyperm", r"UNPROTECTED PRIVATE KEY|bad permissions", "连服务器的钥匙（private 里的 cloud_key）权限太松，ssh 不肯用。交给 Agent 收紧一下。"),
    ("nokey", r"no such identity|Load key .*No such file|identity file .* not accessible", "找不到连服务器的钥匙（private 里的 cloud_key）。交给 Agent 看看。"),
    ("auth", r"Permission denied", "服务器不认这把钥匙（公钥可能被删了，或者用户名不对）。交给 Agent 重新装一下钥匙。"),
    ("sudo", r"sudo: .*password|a terminal is required|sudo: .*not allowed", "登上服务器了，但这个用户不能免密用 sudo。交给 Agent 看看。"),
    ("nopy", r"python3?: (command )?not found|No such file or directory.*python", "服务器上没找到 python3。交给 Agent 装一下。"),
    ("dns", r"Could not resolve hostname", "认不出服务器地址（private 里的 cloud.txt 写错了？）。"),
    ("refused", r"Connection refused", "服务器拒绝连接：22 端口没开，或者 ssh 服务停了。去云控制台看看服务器是不是在重启。"),
    ("timeout", r"timed out|Connection timed out|Operation timed out|超时", "连不上服务器（超时了）。服务器可能关机、到期停机了，或者这台电脑的网络不通。"),
    ("net", r"Network is unreachable|No route to host|unreachable", "这台电脑的网络连不到服务器。"),
    ("reset", r"Connection reset|kex_exchange_identification|Connection closed", "连上服务器又被断开了，过会儿再试。"),
]


def classify(code, txt):
    for k, rx, msg in ERRS:
        if re.search(rx, txt or "", re.I):
            return CloudError(k, msg)
    tail = S.redact((txt or "").strip().splitlines()[-1] if (txt or "").strip() else f"退出码 {code}")[:120]
    return CloudError("other", f"连服务器出错了（{tail}）。")


def call(args, timeout=45):
    """在服务器上跑 cloud_remote.py <args>，返回它最后打的那行 JSON。连不上抛 CloudError。"""
    c = conf()
    if not c["host"]:
        raise CloudError("noconf", "private 里的 cloud.txt 没写服务器地址（host: …）。交给 Agent 补一下。")
    try:
        p = subprocess.run(ssh_base(c) + [remote_cmd(args, c)], input=REMOTE.read_bytes(), capture_output=True, timeout=timeout, creationflags=S.NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise CloudError("timeout", "服务器半天没回话（超时了），过会儿再试。") from None
    except OSError as e:
        raise CloudError("nossh", f"这台电脑上的 ssh 用不了（{type(e).__name__}）。Windows 10 1809 以后自带，没有的话在「设置 → 系统 → 可选功能」里加「OpenSSH 客户端」。") from None
    out = p.stdout.decode("utf-8", "replace")
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("DFY_JSON ")), "")
    if line:
        try:
            return json.loads(line[len("DFY_JSON "):])
        except ValueError:
            pass
    raise classify(p.returncode, p.stderr.decode("utf-8", "replace") + "\n" + out[-400:])


# ---------------------------------------------------------------- 后台问状态

def refresh(force_balance=False):
    with SNAP_LOCK:
        if SNAP["busy"]:
            return False
        SNAP["busy"] = True
    try:
        disk = S.read_json(S.STATE / "cache.json", {}) or {}
        bal = disk.get("balance") or {}
        args = ["status"]
        if force_balance or not bal or time.time() - bal.get("at", 0) > (600 if bal.get("state") == "ok" else 120):
            args.append("--balance")
        meta = disk.get("cloud_meta") or {}
        if meta.get("host") != conf()["host"]:
            args.append("--meta")
        try:
            d = call(args, 50)
        except CloudError as e:
            SNAP.update(err=e, err_at=time.time())
            S.log(f"连服务器没成：{e.code}")
            return False
        if not d.get("ok"):
            SNAP.update(err=CloudError("helper", "服务器上的小帮手出错了（" + S.redact(str(d.get("msg") or d.get("why") or ""))[:100] + "）。"), err_at=time.time())
            return False
        if "balance" in d:
            b = dict(d["balance"], at=time.time())
            upd = {"balance": b}
            if b.get("state") == "ok":
                day = datetime.now().strftime("%Y-%m-%d")
                if (disk.get("balance_day") or {}).get("date") != day:
                    upd["balance_day"] = {"date": day, "amount": b.get("amount", ""), "at": time.time()}
            S.update_disk(**upd)
        if "meta" in d:
            S.update_disk(cloud_meta=dict(d["meta"], host=conf()["host"]))
        SNAP.update(data=d, at=time.time(), err=None, pc_at=time.time())
        S.drop_cache("status")
        return True
    finally:
        SNAP["busy"] = False


def loop():
    while not STOP.is_set():
        busy = any(j["state"] == "running" for j in S.running_jobs())
        if busy or time.time() - S.LAST_SEEN < 90 or not SNAP["data"]:
            S._safe(refresh)
            S._safe(keep_tunnel)
        STOP.wait(8)


def wait_snap(cond, seconds, job=None, p0=None, p1=None, step=None, every=3):
    """一直问到 cond(状态) 成立；只认这一刻以后从服务器拿到的状态。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        refresh()
        d = SNAP["data"]
        if d and SNAP["at"] > t0 and cond(d):
            return True
        if job is not None and p0 is not None:
            job.set(step, p0 + (p1 - p0) * min(1, (time.time() - t0) / seconds))
        time.sleep(every)
    refresh()
    return bool(SNAP["data"] and SNAP["at"] > t0 and cond(SNAP["data"]))


def start():
    threading.Thread(target=lambda: S._safe(loop), daemon=True).start()


def stop():
    STOP.set()
    stop_tunnel()


# ---------------------------------------------------------------- 隧道（后台网页从这台电脑打开）

def remote_ports():
    c = ((SNAP["data"] or {}).get("c") or {})
    return (c.get("astrbot") or {}).get("port") or 6185, (c.get("napcat") or {}).get("port") or 6099


def keep_tunnel():
    """两个本机端口有人在听（自己开的、或者以前那两个 .cmd 开的）就不管；缺哪个补哪个。连不上服务器时一分钟才再试。"""
    with TUN_LOCK:
        need = [p for p in (TUN_BRAIN, TUN_NAPCAT) if not S.listening(p)]
        if not need:
            return True
        p = TUN["p"]
        if p and p.poll() is None:
            return False  # 刚开，还没听上
        if time.time() < TUN["next"] or SNAP["err"]:
            return False
        rb, rn = remote_ports()
        fw = []
        for lp in need:
            fw += ["-L", f"127.0.0.1:{lp}:127.0.0.1:{rb if lp == TUN_BRAIN else rn}"]
        cmd = ssh_base()[:-1] + ["-N", "-o", "ExitOnForwardFailure=yes"] + fw + [ssh_base()[-1]]
        try:
            lf = open(S.STATE / "tunnel.log", "ab")
            TUN["p"] = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, creationflags=S.NO_WINDOW)
            TUN["log"] = lf
            S.log("开隧道：" + "、".join(str(x) for x in need))
        except OSError:
            pass
        TUN["next"] = time.time() + 60
    for _ in range(16):
        time.sleep(0.5)
        S.drop_cache("net")
        if all(S.listening(x) for x in need):
            return True
    return False


def stop_tunnel():
    with TUN_LOCK:
        p = TUN["p"]
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
        TUN["p"] = None


def in_use():
    """后台网页还开着（隧道上有连接），工作台就先别自己退出。"""
    try:
        _, est = S.net_snapshot()
        return any(lp in (TUN_BRAIN, TUN_NAPCAT) for lp, _, _ in est)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 状态

def where(short=False):
    meta = (S.read_json(S.STATE / "cache.json", {}) or {}).get("cloud_meta") or {}
    prov = meta.get("provider") or conf()["provider"]
    reg = str(meta.get("region") or "")
    city = next((v for k, v in REGION.items() if k in reg), "")
    name = PROVIDER.get(prov, "")
    return (name + city) or ("云服务器" if not short else "")


def console_url():
    meta = (S.read_json(S.STATE / "cache.json", {}) or {}).get("cloud_meta") or {}
    return CONSOLE.get(meta.get("provider") or conf()["provider"], "")


def mb(n):
    return f"{n / 1024:.1f} GB" if n >= 1024 else f"{int(n)} MB"


def ago(sec):
    return "刚刚" if sec < 60 else S.dur_text(sec) + "前"


def expire_date():
    s = conf()["expire"]
    m = re.search(r"(20\d\d)\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def imp_state(d, blk):
    s = (d.get("imports") or {}).get(blk)
    if not s:
        return None
    st = {"mtime": s.get("mtime", 0), "result": s.get("result"), "running": bool(s.get("running"))}
    pr = S.progress_from_lines(s.get("tail") or [])
    if st["running"] and pr.get("total"):
        pr["step"] += S.eta_text("cloud-" + blk + ":" + pr.get("pack", ""), pr["done"], pr["total"])
    st.update({"step": pr["step"], "pct": pr["pct"]})
    return st


def connecting_status():
    items = [S.item("server", "云服务器", "i-server", "她住的那台服务器。", "busy", "正在连服务器……第一次要几秒钟。")]
    for i, n, ic in (("brain", "大脑 · AstrBot", "i-brain"), ("qq", "登 QQ 的程序 · NapCat", "i-chat"), ("deepseek", "DeepSeek", "i-bolt")):
        items.append(S.item(i, n, ic, "", "unk", "连上服务器就知道了。", pill="在查"))
    return base_status(items, {}, connecting=True)


def base_status(items, d, connecting=False, err=None):
    c = conf()
    a = d.get("answers") or {}
    up = (S.read_json(S.STATE / "cache.json", {}) or {}).get("updates") or {}
    updates = []
    names = {"skill": ("安装 Skill 和工作台", "i-toggle", "帮你装、帮你修的那套说明书，还有这个窗口；服务器上那份跟着一起换。"), "astrbot": ("AstrBot", "i-brain", "她的大脑（服务器上的 Docker 镜像）。")}
    for k, v in (up.get("items") or {}).items():
        if k not in names:
            continue
        n, ic, w = names[k]
        lv, cv = v.get("latest", ""), v.get("local", "")
        state = "unknown" if not lv else ("new" if S.ver_tuple(lv) > S.ver_tuple(cv) else "latest")
        updates.append({"id": k, "name": n, "icon": ic, "what": w, "local": cv, "latest": lv, "state": state, "notes": v.get("notes") or [], "reimport": v.get("reimport") or []})
    cc = d.get("c") or {}
    versions = [["工作台", S.VERSION]]
    for label, key in (("AstrBot", "astrbot"), ("NapCat", "napcat")):
        if (cc.get(key) or {}).get("version"):
            versions.append([label, cc[key]["version"]])
    if d.get("kit"):
        versions.append(["服务器上的安装 Skill", d["kit"]])
    if d.get("pack"):
        versions.append(["配置包", d["pack"]])
    if (d.get("host") or {}).get("os"):
        versions.append(["服务器系统", re.sub(r"\s*\(.*?\)", "", d["host"]["os"])[:40]])
    desc = {"cloud.txt": "服务器地址和登录用户名", "cloud_key": "连服务器的钥匙（私钥，别发给任何人）", "cloud_key.pub": "钥匙的公开那一半",
            "cloud-backend.txt": "AstrBot 后台的账号和密码", "napcat-webui.txt": "NapCat 后台口令", "cloud_known_hosts": "服务器的指纹（认准是这台）",
            "deepseek-key.txt": "DeepSeek 密钥（扣钱的钥匙）", "owner-qq.txt": "你的大号", "siliconflow-key.txt": "硅基流动密钥", "volc.txt": "豆包语音的两项凭证", "breeze-key.txt": "Breeze 在线语音密钥"}
    secrets_files = []
    try:
        for f in sorted((S.DIR / "private").iterdir()):
            if f.is_file():
                secrets_files.append({"name": f.name, "what": desc.get(f.name, "")})
    except OSError:
        pass
    for n in d.get("private") or []:
        secrets_files.append({"name": "服务器上 " + n, "what": desc.get(n, "")})
    w = where(True)
    base = f"http://127.0.0.1:{TUN_BRAIN}"
    return {
        "ok": True, "time": time.time(), "version": S.VERSION, "dir": str(S.DIR), "proto": "napcat", "proto_name": "NapCat", "cli": True,
        "backend": base, "bot": d.get("bot", ""), "items": items, "stats": stats_of(d), "features": feats_of(d), "updates": updates,
        "updates_at": up.get("at", 0), "qq_upgrade": False, "jobs": S.running_jobs(),
        "install": {"secrets": secrets_files, "cost": None, "group": "group.txt" in (d.get("private") or [])},
        "versions": versions, "answers": {"memory": a.get("memory", ""), "voice": a.get("voice", ""), "video": "1" if a.get("video") == "1" else ""},
        "game": {"on": False, "since": 0, "can": False, "left": False},
        "paths": {"backend": str(S.DIR / "private" / "cloud-backend.txt"), "private": str(S.DIR / "private")},
        "group_reply": d.get("group_reply", "off"), "meme_freq": d.get("meme_freq", ""), "voice_freq": d.get("voice_freq", ""),
        "cloud": {"where": w, "dir": c["dir"], "console": console_url(), "expire": c["expire"], "connecting": connecting,
                  "err": err.code if err else "", "quick": bool(((cc.get("napcat") or {}).get("account"))), "at": SNAP["pc_at"]},
    }


def stats_of(d):
    st = d.get("stats") or {}
    a = d.get("answers") or {}
    out = []
    if "msgs" in st:
        out.append({"id": "msgs", "n": st["msgs"], "unit": "条", "label": "今天收到的消息"})
    if "replies" in st:
        out.append({"id": "replies", "n": st["replies"], "unit": "次", "label": "她回了"})
    if "tokens" in st:
        t = st["tokens"]
        out.append({"id": "tokens", "n": (f"{t / 10000:.1f}" if t >= 10000 else str(t)), "unit": "万" if t >= 10000 else "", "label": "吃掉的 token", "note": "她的饭"})
    if "memories" in st and a.get("memory") in ("1", "2"):
        out.append({"id": "memories", "n": st["memories"], "unit": "件", "label": "记住的事"})
    elif (d.get("memes") or {}).get("images"):
        out.append({"id": "memes", "n": d["memes"]["images"], "unit": "张", "label": "表情包"})
    return out


def feats_of(d):
    a = d.get("answers") or {}
    feats = []
    for blk, label, chosen in (("voice", "语音", a.get("voice") in ("1", "2", "3", "5")), ("memory", "记忆", a.get("memory") in ("1", "2")),
                               ("memes", "表情包", True), ("video", "看视频", a.get("video") == "1")):
        s = imp_state(d, blk)
        feats.append({"block": blk, "label": label, "chosen": chosen, "running": bool(s and s["running"]), "step": (s or {}).get("step", ""), "pct": (s or {}).get("pct"),
                      "result": S.result_text((s or {}).get("result")), "result_status": ((s or {}).get("result") or {}).get("status", ""),
                      "at": datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M") if s else ""})
    return feats


def collect_status():
    d, err = SNAP["data"], SNAP["err"]
    if not d and not err:
        return connecting_status()
    jobs = S.running_jobs()

    def job_running(group):
        return any(j["group"] == group and j["state"] == "running" for j in jobs)

    items = []
    stale = bool(err) and (not d or time.time() - SNAP["at"] > 45)
    # 1 服务器
    name = "云服务器" + (" · " + where(True) if where(True) else "")
    swhat = "她住的那台服务器，24 小时开着。"
    clinks = [{"label": "云控制台", "href": console_url()}] if console_url() else []
    if stale:
        last = f"（上次连上是 {datetime.fromtimestamp(SNAP['at']):%m-%d %H:%M}）" if SNAP["at"] else ""
        items.append(S.item("server", name, "i-server", swhat, "bad", err.text + last, links=clinks, extra={"code": err.code, "ask": "server"}))
        for i, n, ic in (("brain", "大脑 · AstrBot", "i-brain"), ("qq", "登 QQ 的程序 · NapCat", "i-chat"), ("deepseek", "DeepSeek", "i-bolt")):
            items.append(S.item(i, n, ic, "", "unk", "连不上服务器，这会儿查不到。", pill="查不到"))
        return base_status(items, d or {}, err=err)
    h, dk, cc, nc = d.get("host") or {}, d.get("docker") or {}, d.get("c") or {}, d.get("nc") or {}
    a, n = cc.get("astrbot") or {}, cc.get("napcat") or {}
    snow = d.get("now") or time.time()
    if dk.get("active") != "active":
        srv = S.item("server", name, "i-server", swhat, "bad", "服务器开着，但 Docker 没在跑，大脑和 QQ 都停了。修复会把它开起来（大约一分钟），开好以后 QQ 多半要重新扫码。",
                     fix={"action": "start_docker", "label": "一键修复"}, links=clinks)
    elif not d.get("exists"):
        srv = S.item("server", name, "i-server", swhat, "bad", f"服务器上找不到她的文件夹（{conf()['dir']}）。交给 Agent 看看。", links=clinks, extra={"ask": "server"})
    else:
        free, avail = h.get("disk_free", 99), h.get("mem_avail", 9999)
        oom = [x for x, k in (("大脑", "astrbot"), ("NapCat", "napcat")) if (cc.get(k) or {}).get("oom")]
        base_t = f"连着。开机 {S.dur_text(h.get('uptime', 0))}，内存还剩 {mb(avail)}、硬盘还剩 {free:.0f} GB。"
        if free < 1:
            srv = S.item("server", name, "i-server", swhat, "bad", f"硬盘快满了（只剩 {free} GB），她可能存不下东西、出错。交给 Agent 清一清。", links=clinks, extra={"ask": "disk"})
        elif free < 3:
            srv = S.item("server", name, "i-server", swhat, "warn", f"硬盘只剩 {free} GB 了，满了她会出错。交给 Agent 清一清。", links=clinks, extra={"ask": "disk"})
        elif avail < 150:
            srv = S.item("server", name, "i-server", swhat, "warn", f"内存快用完了（只剩 {int(avail)} MB），她可能会被系统关掉。少开几样功能，或者给服务器升配。", links=clinks)
        elif oom:
            srv = S.item("server", name, "i-server", swhat, "warn", base_t + f"{'、'.join(oom)}之前因为内存不够被系统关过。", links=clinks)
        else:
            srv = S.item("server", name, "i-server", swhat, "ok", base_t, links=clinks)
    items.append(srv)

    # 2 大脑
    base = f"http://127.0.0.1:{TUN_BRAIN}"
    bwhat = "管聊天的总程序，跑在服务器的 Docker 里。"
    since = snow - a.get("started", 0) if a.get("started") else 0
    if dk.get("active") != "active":
        brain = S.item("brain", "大脑 · AstrBot", "i-brain", bwhat, "bad", "Docker 没在跑，大脑跟着停了。先修上面「云服务器」那一项。")
    elif not a.get("exists"):
        brain = S.item("brain", "大脑 · AstrBot", "i-brain", bwhat, "bad", "服务器上没有大脑的容器（astrbot）。交给 Agent 看看。", extra={"ask": "server"})
    elif a.get("running") and d.get("http") in (200, 301, 302, 304):
        brain = S.item("brain", "大脑 · AstrBot", "i-brain", bwhat, "ok", f"开着，已连续跑了 {S.dur_text(since)}。", links=[{"label": "打开后台", "href": base + "/#/"}],
                       extra={"since": time.time() - since})
    elif job_running("brain") or (a.get("running") and since < 120):
        brain = S.item("brain", "大脑 · AstrBot", "i-brain", bwhat, "busy", "正在起来，大约半分钟到一分钟。")
    elif a.get("running"):
        brain = S.item("brain", "大脑 · AstrBot", "i-brain", bwhat, "bad", "容器开着，但后台一直没反应（卡住了）。重启一下就好。", fix={"action": "restart_astrbot", "label": "重启大脑"})
    else:
        brain = S.item("brain", "大脑 · AstrBot", "i-brain", bwhat, "bad", "没开，她现在回不了话。修复会把它打开，大约半分钟。" + ("（上次是内存不够被系统关掉的。）" if a.get("oom") else ""),
                       fix={"action": "start_astrbot", "label": "一键修复"})
    items.append(brain)

    # 3 QQ
    online = (d.get("conns") or 0) > 0
    bot = d.get("bot", "")
    qwhat = "帮她登上小号，跑在服务器的 Docker 里。"
    btns = [{"label": "扫码登录", "action": "relogin"}, {"label": "重启它", "action": "restart_qq"}, {"label": "NapCat 后台", "action": "open_napcat"}]
    qn = "登 QQ 的程序 · NapCat"
    if online:
        recv = nc.get("recv_at") or 0
        t = (f"小号 {bot} 在线。" if bot else "小号在线。") + (f"最后收到消息是 {ago(snow - recv)}。" if recv else "这次登上以后还没收到过消息。")
        qq = S.item("qq", qn, "i-chat", qwhat, "ok", t, btns=btns)
    elif job_running("qq"):
        qq = S.item("qq", qn, "i-chat", qwhat, "busy", "正在登。看不到二维码的话，再点一次「扫码登录」。", btns=btns[:1])
    elif brain["state"] != "ok":
        qq = S.item("qq", qn, "i-chat", qwhat, "bad", "大脑没开，小号也连不上她。先把大脑开起来。", btns=btns[:1])
    elif not n.get("exists"):
        qq = S.item("qq", qn, "i-chat", qwhat, "bad", "服务器上没有 NapCat 的容器。交给 Agent 看看。", extra={"ask": "server"})
    elif not n.get("running"):
        qq = S.item("qq", qn, "i-chat", qwhat, "bad", "NapCat 停着，小号没登上。点「扫码登录」会把它打开，二维码就出在这个窗口里。",
                    fix={"action": "relogin", "label": "扫码登录"}, btns=btns[1:2])
    elif nc.get("risk") and nc.get("risk_at", 0) >= nc.get("login_at", 0):
        qq = S.item("qq", qn, "i-chat", qwhat, "bad", "QQ 提示这个号有风险，没登上。在小号的手机 QQ 上按提示处理好，隔一会儿再点「扫码登录」。",
                    fix={"action": "relogin", "label": "扫码登录"}, btns=btns[1:])
    elif nc.get("waiting"):
        t = "二维码被扫了，等手机上点「登录」。" if nc.get("scanned") else "NapCat 在等扫码。点「扫码登录」，二维码会出在这个窗口里。"
        qq = S.item("qq", qn, "i-chat", qwhat, "bad", t, fix={"action": "relogin", "label": "扫码登录"}, btns=btns[1:])
    elif snow - n.get("started", 0) < 90:
        qq = S.item("qq", qn, "i-chat", qwhat, "busy", "NapCat 正在起来……")
    elif nc.get("login_at"):
        qq = S.item("qq", qn, "i-chat", qwhat, "bad", "小号登上过，但现在没连上她的大脑。点「重启它」试试；还不行就重新扫码。",
                    fix={"action": "restart_qq", "label": "重启 NapCat"}, btns=[btns[0], btns[2]])
    else:
        qq = S.item("qq", qn, "i-chat", qwhat, "bad", "小号没登上。点「扫码登录」，二维码会出在这个窗口里。", fix={"action": "relogin", "label": "扫码登录"}, btns=btns[1:])
    items.append(qq)

    # 4 DeepSeek（余额在服务器上用那边的 key 查）
    disk = S.read_json(S.STATE / "cache.json", {}) or {}
    items.append(S.deepseek_item("deepseek-key.txt" in (d.get("private") or []), disk.get("balance") or {}, disk.get("balance_day") or {}, d.get("whitelist")))

    # 5 记忆
    ans = d.get("answers") or {}
    st = d.get("stats") or {}
    w = d.get("mem") or {}
    mem = ans.get("memory", "")
    mlinks = [{"label": "看她记住了什么", "href": base + "/#/plugin-view/astrbot_plugin_livingmemory/dashboard"}]
    mcount = f"记住了 {st['memories']} 件事。" if "memories" in st else ""
    mname = "记忆 · 服务器上的 Ollama" if w.get("ollama") else "记忆 · 硅基流动" if w.get("sf") else "记忆"
    mwhat = "让她记得你说过的事。"
    if not w.get("installed") or w.get("on") is False:
        if mem in ("1", "2"):
            memi = S.item("memory", mname, "i-book", mwhat, "bad", "选了要记忆，但记忆插件" + ("没装上" if not w.get("installed") else "关着") + "，她记不住事。交给 Agent 补一下。", extra={"ask": "memfix"})
        else:
            memi = S.item("memory", "记忆", "i-book", mwhat, "off", "没开。想要的话去「交给 Agent」。", extra={"ask": "memory"})
    elif not w.get("provider"):
        memi = S.item("memory", mname, "i-book", mwhat, "bad", "记忆插件开着，但没接上记忆模型，她记不住事。交给 Agent 接一下。", links=mlinks, extra={"ask": "memfix"})
    elif not w.get("local") and not w.get("key"):
        memi = S.item("memory", mname, "i-book", mwhat, "bad", "记忆接的是在线服务，但没填密钥，她记不住事。交给 Agent 粘一把硅基流动的密钥，或者先关掉记忆。", links=mlinks, extra={"ask": "memfix"})
    else:
        memi = S.item("memory", mname, "i-book", mwhat, "ok", ("跑在服务器上。" if w.get("local") else "在线服务，不用开。") + mcount, links=mlinks)
    items.append(memi)

    # 6 语音
    voice = ans.get("voice", "")
    vlinks = [{"label": "换模型、语音", "href": base + "/#/providers"}]
    if voice == "3":
        vi = S.item("voice", "语音 · 豆包", "i-mic", "让她能发语音。", "ok", "在线服务，不用开。按说话时长扣钱，大约一分钟一块。", links=vlinks)
    elif voice == "5":
        vi = S.item("voice", "语音 · Breeze 在线版", "i-mic", "让她能发语音，不占显存。", "ok", "已选择在线服务，不用在本机开语音程序；按字扣积分，余额和密钥请在 Breeze 网页核对。超过 120 字保留文字。", links=vlinks)
    elif voice == "1":
        vi = S.item("voice", "语音 · Edge 免费女声", "i-mic", "让她能发语音。", "ok", "在线服务，不用开，不花钱。", links=vlinks)
    elif voice == "2":
        vi = S.item("voice", "语音 · 小米 MiMo", "i-mic", "让她能发语音。", "ok", "在线服务，不用开。", links=vlinks)
    else:
        vi = S.item("voice", "语音", "i-mic", "让她能发语音。", "off", "没开，她只打字。想要的话去「交给 Agent」（服务器没有显卡，只能用在线语音）。", extra={"ask": "voice"})
    items.append(vi)

    # 7 看图看视频
    vd = d.get("video") or {}
    vwhat = "看懂你发的图和视频。"
    lk = [{"label": "截几张、看多长", "href": base + "/#/extension?open_config=astrbot_plugin_dafeiyu_video"}]
    if ans.get("video") == "1":
        if not vd.get("plugin"):
            vis = S.item("vision", "看图看视频", "i-image", vwhat, "warn", "能看图。选了看视频，但看视频的插件没装上，交给 Agent 补一下。", extra={"ask": "video"})
        elif vd.get("asr") == "online":
            vis = S.item("vision", "看图看视频", "i-image", vwhat, "ok", "能看图、看视频；视频里说的话用硅基流动在线识别。", links=lk)
        else:
            vis = S.item("vision", "看图看视频", "i-image", vwhat, "ok", "能看图、看视频画面。", links=lk)
    else:
        vis = S.item("vision", "看图看视频", "i-image", vwhat, "ok", "能看图。看视频没装，想要的话去「交给 Agent」。", pill="能看图", extra={"ask": "video"})
    items.append(vis)

    # 8 服务器重启后
    awhat = "服务器重启（比如云厂商维护）以后她自己上线。"
    pol_ok = all((cc.get(k) or {}).get("policy") in ("always", "unless-stopped") for k in ("astrbot", "napcat"))
    quick = bool(n.get("account"))
    uptime = h.get("uptime", 0)
    boot = ""
    if uptime and a.get("started") and 0 <= a["started"] - (snow - uptime) < 900:
        boot = f"这次开机 {datetime.fromtimestamp(time.time() - uptime):%m-%d %H:%M}，她 {datetime.fromtimestamp(time.time() - since):%H:%M} 自己起来了。"
    if dk.get("enabled") != "enabled" or not pol_ok:
        auto = S.item("autostart", "服务器重启后", "i-power", awhat, "warn", "服务器重启后她不会自己起来（Docker 没设开机自启，或者容器没设自动重启）。修复会补上。",
                      fix={"action": "autostart_fix", "label": "补上"})
    elif not quick and (d.get("quick") or {}).get("uin"):
        t = "服务器重启后大脑会自己起来，但 QQ 要重新扫码：NapCat 还没记住小号。"
        t += "点「补上」让它记住（NapCat 会重开一下，她掉线十几秒再自己连回来）。" if n.get("running") else "点「补上」先记下来，下次 NapCat 开起来就生效。"
        auto = S.item("autostart", "服务器重启后", "i-power", awhat, "warn", t, fix={"action": "autostart_fix", "label": "补上"})
    elif not quick:
        auto = S.item("autostart", "服务器重启后", "i-power", awhat, "warn", "服务器重启后大脑会自己起来，但 QQ 要重新扫码。先扫码登上一次，这里再点「补上」，以后就不用扫了。")
    else:
        auto = S.item("autostart", "服务器重启后", "i-power", awhat, "ok", boot or "两个容器会自己起来，QQ 用快速登录，一般不用扫码。")
    items.append(auto)

    # 9 表情包
    mi = d.get("memes") or {"installed": [], "images": 0, "expected": []}
    ml = imp_state(d, "memes")
    elinks = [{"label": "看看、上传新的", "href": base + "/#/plugin-view/meme_manager/app"}]
    have = {p["id"] for p in mi.get("installed") or []}
    lack = [x for x in mi.get("expected") or [] if x not in have]
    ewhat = "聊着聊着甩一张。"
    fx = {"action": "import", "body": {"block": "memes"}, "label": "补装表情包"}
    if ml and ml["running"]:
        memes = S.item("memes", "表情包", "i-smile", ewhat, "busy", (ml.get("step") or "正在补装表情包……") + "。在服务器上下，她照常聊天。", links=elinks, extra={"pct": ml.get("pct")})
    elif not mi.get("installed"):
        t = "还没装上，她先不配图。补装在服务器上下几百 MB，不耽误聊天。"
        if ml and ml.get("result"):
            t = f"还没装上（上次补装：{S.result_text(ml['result'])}）。可以再试一次，不耽误聊天。"
        memes = S.item("memes", "表情包", "i-smile", ewhat, "warn", t, fix=fx, links=elinks)
    elif lack:
        names = "、".join(f"「{S.MEME_NAMES.get(x, x)}」" for x in lack)
        memes = S.item("memes", "表情包", "i-smile", ewhat, "warn", f"装了 {len(mi['installed'])} 套（{mi['images']} 张），还差{names}。可以补装，在服务器上下，不耽误聊天。", fix=fx, links=elinks)
    else:
        memes = S.item("memes", "表情包", "i-smile", ewhat, "ok", f"装了 {len(mi['installed'])} 套，共 {mi['images']} 张。", links=elinks)
    items.append(memes)

    # 10 到期续费
    rwhat = "服务器按月或按年交钱，到期没续会被停机。"
    exp = expire_date()
    rlinks = [{"label": "去控制台续费", "href": console_url()}] if console_url() else []
    rbtn = [{"label": "改到期日" if exp else "记一下到期日", "action": "set_expire"}]
    if not exp:
        renew = S.item("renew", "到期续费", "i-cal", rwhat, "unk", "还没记服务器什么时候到期。点「记一下到期日」（云控制台的服务器列表里能看到），快到期时这里会提醒你。",
                       links=rlinks, btns=rbtn, pill="没记")
    else:
        left = (exp - date.today()).days
        if left < 0:
            renew = S.item("renew", "到期续费", "i-cal", rwhat, "bad", f"已经过了到期日（{exp:%Y-%m-%d}）。没续费的话服务器会被停机，她也就下线了。去控制台看看。", links=rlinks, btns=rbtn)
        elif left <= 15:
            renew = S.item("renew", "到期续费", "i-cal", rwhat, "warn", f"还有 {left} 天到期（{exp:%Y-%m-%d}），记得去控制台续费。续费价常比首年贵，续之前看一眼；也可以先点「备份到桌面」留一份。",
                           links=rlinks, btns=rbtn)
        else:
            renew = S.item("renew", "到期续费", "i-cal", rwhat, "ok", f"{exp:%Y-%m-%d} 到期，还有 {left} 天。", links=rlinks, btns=rbtn)
    items.append(renew)
    return base_status(items, d, err=err)


# ---------------------------------------------------------------- 日志

def read_logs(src, n=200):
    try:
        r = call(["logs", "qq" if src == "qq" else "brain", str(max(n, 300))], 60)
    except CloudError as e:
        return {"missing": e.text, "lines": []}
    if not r.get("ok"):
        return {"missing": r.get("missing") or "服务器上读不到日志。", "lines": []}
    return S.parse_log_lines(r.get("lines") or [], [S.NC_RX] if src == "qq" else [S.AST_RX], n, r.get("file", ""))


# ---------------------------------------------------------------- 动作

def need_ok(job):
    if not SNAP["data"] or SNAP["err"]:
        refresh()
    if SNAP["err"] and not SNAP["data"]:
        job.done(False, SNAP["err"].text)
        return None
    return SNAP["data"]


def online(d):
    return (d.get("conns") or 0) > 0


def brain_ok(d):
    a = (d.get("c") or {}).get("astrbot") or {}
    return bool(a.get("running") and d.get("http") in (200, 301, 302, 304))


def act_brain(job, restart=False):
    d = need_ok(job)
    if d is None:
        return
    if not restart and brain_ok(d):
        return job.done(True, "大脑本来就开着。")
    job.set("让服务器上的大脑重启……" if restart else "打开大脑（AstrBot）……", 10)
    try:
        r = call(["restart" if restart else "start", "brain"], 320)
    except CloudError as e:
        return job.done(False, e.text)
    if not r.get("ok"):
        return job.done(False, "没开起来：" + S.redact(r.get("out", ""))[-160:] + " 点「复制求助信息」交给 Agent。")
    time.sleep(3)
    if not wait_snap(brain_ok, 180, job, 20, 85, "大脑在起来，大约半分钟到一分钟……"):
        return job.done(False, "等了三分钟还没起来。点「看最近日志」看看卡在哪，或者点「复制求助信息」交给 Agent。")
    n = (SNAP["data"].get("c") or {}).get("napcat") or {}
    if n.get("running"):
        job.set("等小号连上……", 88)
        if wait_snap(online, 45, job, 88, 98, "等小号连上……"):
            return job.done(True, "大脑重启好了，小号也连上了。" if restart else "大脑开好了，小号也连上了。")
        return job.done(True, ("大脑重启好了" if restart else "大脑开好了") + "，小号还没连上：等一小会儿，或者点「扫码登录」。")
    return job.done(True, ("大脑重启好了" if restart else "大脑开好了") + "。NapCat 停着，小号没登上：点「扫码登录」。")


def act_qr(job):
    d = need_ok(job)
    if d is None:
        return
    if online(d):
        return job.done(True, "小号本来就在线，不用扫码。要是她在线却不回话，试试「重启 NapCat」。")
    if not brain_ok(d):
        return job.done(False, "大脑没开，扫码登上了也连不上她。先把大脑开起来。")
    n = (d.get("c") or {}).get("napcat") or {}
    if not n.get("running"):
        job.set("打开服务器上的 NapCat……", 6)
        try:
            r = call(["start", "qq"], 320)
        except CloudError as e:
            return job.done(False, e.text)
        if not r.get("ok"):
            return job.done(False, "NapCat 没开起来：" + S.redact(r.get("out", ""))[-160:])
    t0, last_png = time.time(), ""
    while time.time() - t0 < 600:
        try:
            q = call(["qr"], 40)
        except CloudError as e:
            job.set("连服务器断了一下，接着试……（" + e.text[:30] + "）", None)
            time.sleep(4)
            continue
        nc = q.get("nc") or {}
        if q.get("online"):
            job.data.pop("qr", None)
            refresh()
            quick = bool(((SNAP["data"] or {}).get("c") or {}).get("napcat", {}).get("account"))
            return job.done(True, "登上了，她回来了。" + ("" if quick else "想让服务器重启后不用再扫码，点「服务器重启后」那一项的「补上」。"))
        if nc.get("risk") and nc.get("risk_at", 0) >= nc.get("qr_at", 0) - 5 and nc.get("risk_at", 0) >= t0 - 60:
            job.data.pop("qr", None)
            return job.done(False, "QQ 提示这个号有风险，没登上。在小号的手机 QQ 上按提示处理好，隔一会儿再点「扫码登录」。")
        if not q.get("running"):
            job.data.pop("qr", None)
            return job.done(False, "NapCat 停了。点「看最近日志」看看；还不行就点「复制求助信息」交给 Agent。")
        el = int(time.time() - t0)
        if q.get("png"):
            if q["png"] != last_png:
                job.data["qr"] = "data:image/png;base64," + q["png"]
                job.data["qr_at"] = q.get("png_at", 0)
                last_png = q["png"]
            step = "扫上了，在手机上点「登录」……" if nc.get("scanned") else "等你用手机 QQ 扫码……"
        elif nc.get("waiting"):
            step = "二维码在路上……"
        elif nc.get("login_at", 0) > nc.get("qr_at", 0):
            job.data.pop("qr", None)
            step = "登上了，正在连她的大脑……"
        else:
            step = "NapCat 在起来，二维码马上出来……"
        job.set(f"{step}（已等 {el // 60} 分 {el % 60} 秒）", 8 + 88 * el / 600)
        time.sleep(3)
    job.data.pop("qr", None)
    return job.done(False, "10 分钟还没登上。再点一次「扫码登录」；手机 QQ 提示有风险的，先按提示处理。")


def act_restart_qq(job):
    d = need_ok(job)
    if d is None:
        return
    n = (d.get("c") or {}).get("napcat") or {}
    quick = bool(n.get("account"))
    job.set("让服务器上的 NapCat 重启……", 10)
    try:
        r = call(["restart" if n.get("running") else "start", "qq"], 320)
    except CloudError as e:
        return job.done(False, e.text)
    if not r.get("ok"):
        return job.done(False, "NapCat 没重启成：" + S.redact(r.get("out", ""))[-160:])
    time.sleep(3)
    if wait_snap(online, 80, job, 20, 96, "等小号连回来……"):
        return job.done(True, "NapCat 重启好了，小号连回来了。")
    nc = (SNAP["data"] or {}).get("nc") or {}
    if nc.get("waiting") or not quick:
        return job.done(False, "NapCat 重开了，要重新扫码：点「扫码登录」。")
    return job.done(False, "NapCat 重开了，小号还没连上。等一会儿，或者点「扫码登录」。")


def act_start_docker(job):
    job.set("打开服务器上的 Docker……", 10)
    try:
        r = call(["start", "docker"], 150)
    except CloudError as e:
        return job.done(False, e.text)
    if not r.get("ok"):
        return job.done(False, "Docker 没开起来：" + S.redact(r.get("out", ""))[-160:] + " 交给 Agent 看看。")
    if wait_snap(brain_ok, 180, job, 25, 95, "等大脑起来……"):
        return job.done(True, "Docker 开好了，大脑也起来了。QQ 要是没登上，点「扫码登录」。")
    return job.done(False, "Docker 开了，大脑还没起来。点「看最近日志」看看。")


def act_import(job, block):
    d = need_ok(job)
    if d is None:
        return
    a = d.get("answers") or {}
    if block == "memory" and a.get("memory") not in ("1", "2"):
        return job.done(False, "你装的时候没选记忆。想要的话去「交给 Agent」。")
    if block == "voice" and a.get("voice") not in ("1", "2", "3", "5"):
        return job.done(False, "你装的时候没选语音。想要的话去「交给 Agent」。")
    if block == "video" and a.get("video") != "1":
        return job.done(False, "你装的时候没选看视频。想要的话去「交给 Agent」。")
    label = {"memes": "表情包", "memory": "记忆", "voice": "语音", "video": "看视频"}[block]
    st = imp_state(d, block)
    if not (st and st["running"]):
        if not brain_ok(d):
            return job.done(False, "大脑没开，补装不了。先把大脑开起来。")
        job.set(f"在服务器上开始补装{label}……", 5)
        try:
            r = call(["import", block], 60)
        except CloudError as e:
            return job.done(False, e.text)
        if not r.get("ok"):
            why = {"nokit": "服务器上没有导入工具（kit 文件夹不全），交给 Agent 重新传一下 Skill。", "noanswers": "服务器上没有回答文件（private/answers.json），交给 Agent 看看。",
                   "brain": "大脑没开，补装不了。"}.get(r.get("why"), "没开始。")
            return job.done(False, why)
        time.sleep(4)
    limit = 50 * 60 if block == "memes" else 15 * 60
    t0 = time.time()
    while time.time() - t0 < limit:
        refresh()
        st = imp_state(SNAP["data"] or {}, block)
        if st and st.get("result") and st["mtime"] >= t0 - 30:
            r = st["result"]
            return job.done(r["status"] in ("ok", "skip"), f"{label}{S.result_text(r)}。" + ("" if r["status"] == "ok" else "她照常聊天，过一阵可以再补装一次。"), result=r)
        if st:
            job.set(st.get("step") or f"补装{label}……", st.get("pct") if st.get("pct") is not None else 8 + 60 * (time.time() - t0) / limit)
            if not st["running"] and not st.get("result") and time.time() - st["mtime"] > 600:
                return job.done(False, "补装好像停住了（日志 10 分钟没动）。点「复制求助信息」交给 Agent 看看。")
        time.sleep(4)
    return job.done(False, "等太久了，补装还没完。它还在服务器上跑，过会儿再打开工作台看。")


def act_setting(job, kind, val):
    table, msgs = {"group_reply": (S.GROUP_REPLY, S.GROUP_REPLY_MSG), "meme_freq": (S.MEME_FREQ, S.MEME_FREQ_MSG),
                   "voice_freq": (S.VOICE_FREQ, S.VOICE_FREQ_MSG)}[kind]
    if val not in table:
        return job.done(False, "没有这个选项")
    d = need_ok(job)
    if d is None:
        return
    if not brain_ok(d):
        return job.done(False, "大脑没开，改不了。先把大脑开起来。")
    job.set("跟大脑说一声……", 40)
    try:
        r = call(["setting", kind, val], 90)
    except CloudError as e:
        return job.done(False, e.text)
    if not r.get("ok"):
        why = r.get("why")
        if why == "login":
            return job.done(False, "大脑后台没登上（多半是在网页上改过后台密码）。去后台里手动改。")
        if why == "noplugin":
            return job.done(False, "表情包插件还没装，先点上面的「补装」。")
        if why == "novoice":
            return job.done(False, "语音还没接上，先在上面把语音补装好。")
        return job.done(False, "没改成：" + S.redact(r.get("msg", ""))[:120])
    refresh()
    return job.done(True, msgs[val])


def act_quick(job):
    d = need_ok(job)
    if d is None:
        return
    dk, cc = d.get("docker") or {}, d.get("c") or {}
    if dk.get("enabled") != "enabled" or any((cc.get(k) or {}).get("policy") not in ("always", "unless-stopped") for k in ("astrbot", "napcat") if (cc.get(k) or {}).get("exists")):
        job.set("让 Docker 和两个容器开机自己起来……", 15)
        try:
            call(["autostart"], 60)
        except CloudError as e:
            return job.done(False, e.text)
    n = (d.get("c") or {}).get("napcat") or {}
    if n.get("account"):
        refresh()
        return job.done(True, "补好了：服务器重启后两个容器自己起来，QQ 用快速登录。")
    if not (d.get("quick") or {}).get("uin"):
        refresh()
        return job.done(False, "Docker 开机自启补上了。QQ 的快速登录要先扫码登上一次，再回来点「补上」。")
    job.set("让 NapCat 记住小号……", 30)
    try:
        r = call(["quicklogin"] + (["--recreate"] if n.get("running") else []), 320)
    except CloudError as e:
        return job.done(False, e.text)
    if not r.get("ok"):
        why = {"compose": "服务器上的 compose.yml 跟教程里的不一样，没敢改。交给 Agent 看看。", "config": "改完检查没通过，已经改回去了。交给 Agent 看看。",
               "nouin": "还没扫码登上过，先扫一次。"}.get(r.get("why"), "没改成：" + S.redact(r.get("msg", ""))[:120])
        return job.done(False, why)
    if not r.get("recreated"):
        refresh()
        return job.done(True, "记下了：下次 NapCat 开起来就用快速登录，服务器重启后一般不用再扫码。")
    time.sleep(5)
    if wait_snap(online, 100, job, 40, 96, "NapCat 重开了，等它用快速登录连回来……"):
        return job.done(True, "补好了：NapCat 记住了小号，服务器重启后一般不用再扫码。")
    return job.done(False, "NapCat 重开了，但快速登录没登上：点「扫码登录」扫一下就好（以后重启就不用扫了）。")


def act_set_expire(job, s):
    m = re.fullmatch(r"(20\d\d)-(\d{1,2})-(\d{1,2})", str(s or "").strip())
    try:
        dt = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        dt = None
    if not dt:
        return job.done(False, "日期不对，照 2027-10-28 这样填。")
    f = S.DIR / "private" / "cloud.txt"
    lines = [ln for ln in S.read_text(f).splitlines() if not re.match(r"^\s*(expire|到期)\s*[:：]", ln, re.I)]
    lines.append(f"expire: {dt:%Y-%m-%d}")
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    S.drop_cache("status")
    left = (dt - date.today()).days
    return job.done(True, f"记下了：{dt:%Y-%m-%d} 到期" + (f"，还有 {left} 天。快到期时这里会提醒你。" if left >= 0 else "（已经过了，去控制台看看）。"))


def act_open_napcat(job):
    f = S.DIR / "private" / "napcat-webui.txt"
    raw = S.read_text(f)
    tok = next((v for k, v in S.read_kv(f).items() if "token" in k), "") if ":" in raw.replace("：", ":") else (raw.split()[0] if raw else "")
    if not tok:
        return job.done(False, "没找到 NapCat 后台的口令（private 里的 napcat-webui.txt）。交给 Agent 补一下。")
    job.set("打开隧道……", 30)
    if not keep_tunnel() and not S.listening(TUN_NAPCAT):
        return job.done(False, "隧道没开起来（连不上服务器？），打不开。")
    url = f"http://127.0.0.1:{TUN_NAPCAT}/webui?token={tok}"
    try:
        if S.IS_WIN:
            os.startfile(url)  # noqa: S606
        else:
            webbrowser.open(url)
    finally:
        tok = url = ""
    return job.done(True, "在浏览器里打开了。网址里带着口令，录屏、截图时别露出来。")


# ---------------------------------------------------------------- 求助信息、备份

def desktop():
    return S.known_folder(0x0010, os.path.join(os.path.expanduser("~"), "Desktop"))


def diag_problem(st):
    """挑最要紧的一件：返回 (卡在, 原因, 代码)。"""
    by = {i["id"]: i for i in st["items"]}
    err = (st.get("cloud") or {}).get("err")
    sv = by.get("server", {})
    if sv.get("state") == "bad":
        code = {"auth": "SERVER-AUTH", "nokey": "SERVER-AUTH", "keyperm": "SERVER-AUTH", "hostkey": "SERVER-HOSTKEY", "sudo": "SERVER-SUDO"}.get(err, "SERVER-DOWN")
        if "Docker" in sv.get("text", ""):
            code = "DOCKER-OFF"
        elif "硬盘" in sv.get("text", ""):
            code = "DISK-LOW"
        return "连服务器", sv.get("text", ""), code
    b = by.get("brain", {})
    if b.get("state") == "bad":
        return "大脑（AstrBot）" + ("卡住了" if "卡住" in b.get("text", "") else "没开"), b.get("text", ""), "BRAIN-STUCK" if "卡住" in b.get("text", "") else "BRAIN-OFF"
    q = by.get("qq", {})
    if q.get("state") == "bad":
        t = q.get("text", "")
        code = "NAPCAT-OFF" if "停着" in t else "QQ-RISK" if "风险" in t else "QQ-QR" if "扫码" in t and "等" in t else "QQ-OFF"
        return "小号没登上 QQ", t, code
    ds = by.get("deepseek", {})
    if ds.get("state") in ("bad", "warn"):
        t = ds.get("text", "")
        code = "DS-NOKEY" if "没找到" in t else "DS-BADKEY" if "密钥" in t else "DS-NOMONEY" if "用完" in t else "DS-LOW"
        return "DeepSeek", t, code
    for iid, where_, code in (("memory", "记不住事", "MEM-NOPROVIDER"), ("autostart", "服务器重启后不会自己上线", "QUICKLOGIN-MISS"),
                              ("memes", "表情包没装全", "MEME-MISS"), ("renew", "服务器快到期了", "RENEW-SOON"), ("server", "服务器有点挤", "RAM-LOW")):
        it = by.get(iid, {})
        if it.get("state") in ("bad", "warn"):
            if iid == "renew" and it.get("state") == "bad":
                code = "RENEW-PAST"
            if iid == "memory" and "密钥" in it.get("text", ""):
                code = "MEM-NOKEY"
            if iid == "autostart" and "Docker" in it.get("text", ""):
                code = "AUTO-MISS"
            if iid == "server" and "硬盘" in it.get("text", ""):
                code = "DISK-LOW"
            return where_, it.get("text", ""), code
    return None


def act_diag(job):
    job.set("连服务器收集版本、容器、最近的日志，顺手打码……", 15)
    refresh(force_balance=True)
    st = collect_status()
    d = SNAP["data"] or {}
    p = diag_problem(st)
    if p:
        sentence = f"卡在：{p[0]}；原因：{p[1]}；代码 {p[2]}"
        code = p[2]
    else:
        sentence, code = "卡在：没查出毛病；原因：几项检查都正常，可能是 QQ 那边的问题；代码 OK", "OK"
    sentence = S.redact(sentence.replace("\n", " "))
    L = []

    def sec(t):
        L.append("")
        L.append(f"== {t} ==")
    L.append(f"大肥鱼求助信息（云端） {datetime.now():%Y-%m-%d %H:%M}")
    L.append(sentence)
    sec("工作台")
    L.append(f"工作台 {S.VERSION}；这台电脑上的安装 Skill {S.skill_version() or '没找到'}；服务器上的 {d.get('kit') or '没找到'}；配置包 {d.get('pack') or '没找到'}")
    L.append(f"工作目录：{S.DIR}")
    L.append(f"服务器：{where()}，目录 {conf()['dir']}，用户 {conf()['user']}；到期日 {conf()['expire'] or '没记'}")
    if SNAP["err"]:
        L.append(f"连服务器：没连上（{SNAP['err'].code}）{SNAP['err'].text}")
    h, dk, cc = d.get("host") or {}, d.get("docker") or {}, d.get("c") or {}
    if h:
        sec("服务器")
        L.append(f"系统 {h.get('os')}；{h.get('cpus')} 核；内存 {h.get('mem_total')} MB（可用 {h.get('mem_avail')} MB）；swap {h.get('swap_free')}/{h.get('swap_total')} MB 空着；"
                 f"硬盘剩 {h.get('disk_free')}/{h.get('disk_total')} GB；开机 {S.dur_text(h.get('uptime', 0))}；负载 {' '.join(h.get('load') or [])}")
        L.append(f"Docker：开机自启 {dk.get('enabled')}，现在 {dk.get('active')}")
        for k in ("astrbot", "napcat"):
            c = cc.get(k) or {}
            L.append(f"{k}：{c.get('status')}（退出码 {c.get('exit')}，被内存杀过 {c.get('oom')}），镜像 {c.get('image')}，重启过 {c.get('restarts')} 次，"
                     f"自动重启 {c.get('policy')}，快速登录 {c.get('account')}，" + (f"{datetime.fromtimestamp(c['started']):%m-%d %H:%M} 起" if c.get("started") else "没起过"))
        nc = d.get("nc") or {}
        fmt = lambda t: f"{datetime.fromtimestamp(t):%m-%d %H:%M:%S}" if t else "没有"  # noqa: E731
        L.append(f"后台网页 {d.get('http')}；6199 上的连接 {d.get('conns')} 个；小号 {d.get('bot') or '没登过'}")
        L.append(f"NapCat 这次启动以来：最后出二维码 {fmt(nc.get('qr_at'))}，扫码 {fmt(nc.get('scan_at'))}，登上 {fmt(nc.get('login_at'))}，"
                 f"最后收到 {fmt(nc.get('recv_at'))}，最后发出 {fmt(nc.get('send_at'))}，二维码过期 {nc.get('expired', 0)} 次，风控字样：{nc.get('risk') or '没有'}")
        a = d.get("answers") or {}
        sec("设置")
        L.append(f"装的时候选的：记忆 {a.get('memory') or '-'}，语音 {a.get('voice') or '-'}，看视频 {a.get('video') or '-'}")
        L.append(f"记忆接线：{json.dumps(d.get('mem') or {}, ensure_ascii=False)}")
        mi = d.get("memes") or {}
        L.append(f"表情包：{'、'.join(p['id'] + '(' + str(p['n']) + ')' for p in mi.get('installed') or []) or '没有'}；应该有 {'、'.join(mi.get('expected') or [])}")
        L.append(f"白名单 {d.get('whitelist')}；群里回复 {d.get('group_reply')}；表情包多少 {d.get('meme_freq')}；服务器 private 里有：{'、'.join(d.get('private') or [])}")
    sec("几项检查")
    for it in st["items"]:
        L.append(f"[{STATE_TX.get(it['state'], it['state'])}] {it['name']}：{it['text']}")
    imps = d.get("imports") or {}
    if imps:
        sec("补装结果")
        for blk, s in imps.items():
            L.append(f"{blk}：{S.result_text(s.get('result')) or ('还在跑' if s.get('running') else '没跑完就停了')}（日志 {datetime.fromtimestamp(s.get('mtime', 0)):%m-%d %H:%M} 更新）")
            L.extend("    " + x for x in (s.get("tail") or [])[-15:])
    for src, title in (("brain", "大脑 AstrBot"), ("qq", "NapCat")):
        job.set(f"读{title}的日志……", 40 if src == "brain" else 65)
        try:
            r = call(["logs", src, "200"], 60)
            lines = r.get("lines") or []
            sec(f"{title} 日志（最后 {len(lines)} 行）")
            L.extend(lines if r.get("ok") else [r.get("missing", "读不到")])
        except CloudError as e:
            sec(f"{title} 日志")
            L.append("读不到：" + e.text)
    text = S.redact("\n".join(L)) + "\n"
    desk = desktop()
    now = datetime.now()
    f = desk / f"大肥鱼求助信息-{now:%m%d-%H%M}.txt"
    if f.exists():
        f = desk / f"大肥鱼求助信息-{now:%m%d-%H%M%S}.txt"
    try:
        desk.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8-sig")
    except OSError as e:
        return job.done(False, f"存不到桌面（{type(e).__name__}）。")
    copied = False
    if not S.OPTS.no_clipboard and S.IS_WIN:
        code, _ = S.run(["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard -Value $env:DFY_CLIP"], timeout=20, env=dict(os.environ, DFY_CLIP=sentence))
        copied = code == 0
    S.LAST_FILES.add(S.norm(str(f)))
    job.done(True, "存到桌面了，那句话也复制好了。" if copied else "存到桌面了。", file=str(f), name=f.name, sentence=sentence, code=code,
             preview="\n".join(text.splitlines()[:140]), copied=copied)


def act_backup(job, memes=False):
    d = need_ok(job)
    if d is None:
        return
    desk = desktop()
    out = desk / f"云端大肥鱼备份-{datetime.now():%Y%m%d-%H%M}.zip"
    if out.exists():
        return job.done(False, f"桌面上已经有「{out.name}」了，等一分钟再备份。")
    try:
        desk.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(desk)).free
    except OSError:
        free = 0
    if free and free < 600 * 1048576:
        return job.done(False, f"桌面那个盘只剩 {free / 1073741824:.1f} GB，不够放备份。")
    job.set("在服务器上清点要备份的文件……", 5)
    c = conf()
    tmp = out.with_name(out.name + ".part")
    cmd = ssh_base(c) + [remote_cmd(["backup"] + (["--memes"] if memes else []), c)]
    total, done_n, miss, last = 0, -1, 0, ""
    try:
        with open(tmp, "wb") as fp:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=fp, stderr=subprocess.PIPE, creationflags=S.NO_WINDOW)
            p.stdin.write(REMOTE.read_bytes())
            p.stdin.close()
            for raw in p.stderr:
                s = raw.decode("utf-8", "replace").strip()
                if not s:
                    continue
                last = s
                m = re.search(r"要备份 (\d+) 个文件", s)
                if m:
                    total = int(m.group(1))
                    job.line(s)
                    job.set(s, 10)
                m = re.search(r"已打包 (\d+)/(\d+)", s)
                if m:
                    job.set(f"从服务器拉回来：{s}", 10 + 85 * int(m.group(1)) / max(1, int(m.group(2))))
                m = re.match(r"DFY_DONE (\d+) (\d+)", s)
                if m:
                    done_n, miss = int(m.group(1)), int(m.group(2))
            p.wait(timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        last = type(e).__name__
    ok = done_n >= 0
    if ok:
        try:
            with zipfile.ZipFile(tmp) as z:
                ok = "还原说明.txt" in z.namelist()
        except (zipfile.BadZipFile, OSError):
            ok = False
    if not ok:
        try:
            tmp.unlink()
        except OSError:
            pass
        err = classify(-1, last) if last and not last.startswith(("要备份", "已打包")) else None
        return job.done(False, "没备份成：" + (err.text if err else S.redact(last)[:160] or "连接断了"))
    os.replace(tmp, out)
    S.LAST_FILES.add(S.norm(str(out)))
    size = f"{out.stat().st_size / 1048576:.0f} MB"
    note = f"（有 {miss} 个文件没读到）" if miss else ""
    job.done(True, f"备好了：桌面「{out.name}」（{size}）{note}。里面有你的密钥，别发给别人。", file=str(out), name=out.name, size=size, files=total)


# ---------------------------------------------------------------- 更新：把新版 Skill 也换到服务器上

def kit_version():
    return str((SNAP["data"] or {}).get("kit") or "")


def astrbot_version():
    for _ in range(15):
        if SNAP["data"] or SNAP["err"]:
            break
        time.sleep(2)
    return str((((SNAP["data"] or {}).get("c") or {}).get("astrbot") or {}).get("version") or "")


def skill_version_effective():
    """这台电脑上的 Skill 和服务器上那份，取旧的那个：服务器上那份旧了，「一键更新」也会出来，顺手把它换上。"""
    pc, kit = S.skill_version(), kit_version()
    if pc and kit:
        return min(pc, kit, key=S.ver_tuple)
    return pc or kit


def push_kit(job, zp, p0, p1):
    """把下好的 dafeiyu-install.zip 传到服务器上、换掉 kit/dafeiyu-install。返回空串＝成了，不然是原因。"""
    c = conf()
    rdir = c["dir"]
    job.set("把新版传到服务器上……", p0)
    sudo = "" if c["user"] == "root" else "sudo -n "
    put = (f"{sudo}mkdir -p {shlex.quote(rdir)}/downloads && {sudo}sh -c " +
           shlex.quote(f"cat > {rdir}/downloads/dafeiyu-install.zip.part && mv -f {rdir}/downloads/dafeiyu-install.zip.part {rdir}/downloads/dafeiyu-install.zip"))
    try:
        with open(zp, "rb") as fp:
            p = subprocess.run(ssh_base(c) + [put], stdin=fp, capture_output=True, timeout=600, creationflags=S.NO_WINDOW)
        if p.returncode != 0:
            return classify(p.returncode, p.stderr.decode("utf-8", "replace")).text
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"传不上去（{type(e).__name__}）"
    job.set("在服务器上换上新版……", (p0 + p1) / 2)
    try:
        r = call(["kit", f"{rdir}/downloads/dafeiyu-install.zip"], 180)
    except CloudError as e:
        return e.text
    if not r.get("ok"):
        return "服务器上没换成（" + str(r.get("msg") or r.get("why")) + "）"
    refresh()
    job.set("服务器上那份换好了……", p1)
    return ""


ACTIONS = {
    "start_astrbot": ("brain", "打开大脑", lambda j, b: act_brain(j)),
    "restart_astrbot": ("brain", "重启大脑", lambda j, b: act_brain(j, restart=True)),
    "relogin": ("qq", "扫码登录", lambda j, b: act_qr(j)),
    "restart_qq": ("qq", "重启 NapCat", lambda j, b: act_restart_qq(j)),
    "start_docker": ("brain", "打开 Docker", lambda j, b: act_start_docker(j)),
    "diag": ("diag", "复制求助信息", lambda j, b: act_diag(j)),
    "backup": ("backup", "备份到桌面", lambda j, b: act_backup(j, bool(b.get("memes")))),
    "autostart_fix": ("autostart", "服务器重启后", lambda j, b: act_quick(j)),
    "group_reply": ("grouprep", "群里回复时", lambda j, b: act_setting(j, "group_reply", str(b.get("mode", "")))),
    "meme_freq": ("memefreq", "表情包发多少", lambda j, b: act_setting(j, "meme_freq", str(b.get("level", "")))),
    "voice_freq": ("voicefreq", "语音发多少", lambda j, b: act_setting(j, "voice_freq", str(b.get("level", "")))),
    "set_expire": ("expire", "记到期日", lambda j, b: act_set_expire(j, b.get("date"))),
    "open_napcat": ("napweb", "打开 NapCat 后台", lambda j, b: act_open_napcat(j)),
    "skill_update": ("skillup", "更新安装 Skill", lambda j, b: S.act_skill_update(j)),
}
