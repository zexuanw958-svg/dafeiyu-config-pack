# -*- coding: utf-8 -*-
"""一键搬上云：把这台电脑上养好的大肥鱼（记忆、人设、表情包、插件设置）搬到云服务器。

步骤记在 <云端工作目录>\\logs\\move-cloud.json，断了重跑 run 从断的地方接着来。
只用标准库和系统自带的 ssh。这台电脑上的数据一个字不改、不删；入口挪走但留清单，undo 能退回去。
秘密不进命令行、不打印；屏幕和日志里服务器地址、QQ 号都打码。

用法（Python 用 AstrBot 自带的就行，加 -X utf8）：
  move_to_cloud.py --dir <本机工作目录> --cloud-dir <云端工作目录> plan
      只读。看两边情况，列出会带什么、带不走什么、要花多久；最后一行 RESULT。
  move_to_cloud.py --dir ... --cloud-dir ... run [--voice 5|3|1|0] [--memory auto|online|off] [--memes carry|skip] [--replace-existing]
  move_to_cloud.py --dir ... --cloud-dir ... verify     两边对账
  move_to_cloud.py --dir ... --cloud-dir ... status     现在到哪一步
  move_to_cloud.py --dir ... --cloud-dir ... undo       退回这台电脑（只在刚搬完用：搬完以后云上新记住的事带不回来）

输出里给 Agent 读的行：
  STEP <步骤> ...       进度
  RESULT ok | need_human <说明> | fail <说明>      退出码 0 / 3 / 1
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import move_remote  # noqa: E402

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
ASTRBOT_PINNED = "4.29.3"
NAPCAT_IMAGE = "m.daocloud.io/docker.io/mlikiowa/napcat-docker:v4.18.41"
ASTRBOT_IMAGE = "m.daocloud.io/docker.io/soulter/astrbot:v%s"
BATCH_BYTES = 150 * 1024 * 1024    # 一批传多少。断了只重传这一批
EXCLUDE_DIRS = {"temp", "site-packages", "__pycache__", "logs", ".venv", "venv", "node_modules"}
EXCLUDE_TOP_MEMES = "plugin_data/meme_manager"
EXCLUDE_EXT = {".pyc"}
# 带上服务器的私密文件。cloud_key、protocol、桌宠的 key、SnowLuma 那些只对这台电脑有用
SECRETS = ["deepseek-key.txt", "owner-qq.txt", "group.txt", "bot-qq.txt", "siliconflow-key.txt", "sf-key.txt", "volc.txt", "breeze-key.txt"]
STEPS = ["prep", "compose", "kit", "memes", "park", "data", "fix", "up", "reimport", "workdir", "verify"]
STEP_NAME = {"prep": "服务器打底（swap、时区、Docker）", "compose": "放配置、拉镜像", "kit": "传 Skill", "memes": "传表情包（她这时还在线）",
             "park": "停这台电脑上的她", "data": "传记忆、人设、聊天记录、插件", "fix": "改掉绑定这台电脑的设置", "up": "在服务器上起大脑和 NapCat",
             "reimport": "接语音和记忆", "workdir": "放云端工作台", "verify": "对账"}


class Stop(Exception):
    """停下来。kind：need_human（要人做一件事，做完重跑）或 fail。"""

    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind, self.msg = kind, msg


# ---------------------------------------------------------------- 输出

class Ctx:
    pass


C = Ctx()


def redact(s):
    s = str(s)
    host = getattr(C, "host", "")
    if host:
        s = s.replace(host, "<服务器>")
    return re.sub(r"(?<!\d)\d{7,11}(?!\d)", "****", s)


def say(msg):
    line = redact(msg)
    print(line, flush=True)
    try:
        p = C.cloud / "logs" / "move-cloud.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fp:
            fp.write(time.strftime("%m-%d %H:%M:%S ") + line + "\n")
    except (OSError, AttributeError):
        pass


# ---------------------------------------------------------------- 状态

def state_path():
    return C.cloud / "logs" / "move-cloud.json"


def load_state():
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"done": [], "batches": {}}


def save_state(st):
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- 连服务器

def read_kv(p):
    return move_remote.read_kv(str(p))


def load_conf(a):
    C.dir = Path(a.dir).resolve()
    C.cloud = Path(a.cloud_dir).resolve()
    C.data_override = Path(a.data).resolve() if a.data else None
    kv = read_kv(C.cloud / "private" / "cloud.txt")
    C.host = kv.get("host") or kv.get("ip") or ""
    C.user = kv.get("user") or "root"
    C.port = kv.get("port") if str(kv.get("port", "")).isdigit() else "22"
    key = kv.get("key") or "cloud_key"
    C.key = key if os.path.isabs(key) else str(C.cloud / "private" / key)
    C.rdir = kv.get("dir") or "/opt/dafeiyu"
    if not re.fullmatch(r"[A-Za-z0-9.:\-\[\]]{1,255}", C.host):
        raise Stop("need_human", "云端工作目录的 private\\cloud.txt 还没写服务器地址（host: …）。照 云端.md 的 C1 先连上服务器")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]{0,31}", C.user) or not re.fullmatch(r"/[A-Za-z0-9._/\-]{1,200}", C.rdir):
        raise Stop("fail", "cloud.txt 里的 user 或 dir 写得不对")
    if not Path(C.key).exists():
        raise Stop("need_human", "还没有连服务器的钥匙（private\\cloud_key）。照 云端.md 的 C1 生成并装好")


def ssh_exe():
    if IS_WIN:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        for c in (os.path.join(root, "System32", "OpenSSH", "ssh.exe"), os.path.join(root, "Sysnative", "OpenSSH", "ssh.exe")):
            if os.path.exists(c):
                return c
    return "ssh"


def ssh_base():
    kh = str(C.cloud / "private" / "cloud_known_hosts").replace("\\", "/")
    return [ssh_exe(), "-i", C.key, "-p", C.port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
            "-o", f'UserKnownHostsFile="{kh}"', "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4", "-o", "LogLevel=ERROR",
            "-o", "IdentitiesOnly=yes", f"{C.user}@{C.host}"]


def sudo():
    return "" if C.user == "root" else "sudo -n "


def rsh(script, timeout=120, check=True):
    """在服务器上跑一段 sh。脚本走标准输入，不走命令行（Windows 的引号规则管不到它，也不进进程列表）。"""
    try:
        p = subprocess.run(ssh_base() + [sudo() + "sh -s"], input=script.replace("\r\n", "\n").encode("utf-8"),
                           capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise Stop("fail", f"服务器上这一步 {timeout} 秒没跑完。重跑 run 接着来") from None
    except OSError as e:
        raise Stop("fail", f"这台电脑上的 ssh 用不了（{type(e).__name__}）") from None
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    if p.returncode == 255:
        raise Stop("fail", "连不上服务器：" + redact(err.strip()[-200:]))
    if check and p.returncode != 0:
        raise Stop("fail", "服务器上这一步出错：" + redact((err or out).strip()[-300:]))
    return p.returncode, out


def rput(path, data):
    """往服务器写一个秘密文件（内容走标准输入，权限 600）。"""
    if not re.fullmatch(r"/[A-Za-z0-9._/\-]{1,250}", path):
        raise Stop("fail", "路径不对：" + path)
    cmd = sudo() + "sh -c 'umask 077; cat > " + path + "'"
    p = subprocess.run(ssh_base() + [cmd], input=data, capture_output=True, timeout=60, creationflags=NO_WINDOW)
    if p.returncode != 0:
        raise Stop("fail", "往服务器写文件没成功：" + redact(p.stderr.decode("utf-8", "replace").strip()[-200:]))


def rpy(cmd, timeout=300, **args):
    """跑 move_remote.py 的一个子命令，返回它的 JSON。"""
    args.setdefault("dir", C.rdir)
    arg = base64.b64encode(json.dumps(args, ensure_ascii=False).encode("utf-8")).decode()
    line = 'P=$(command -v python3 || echo /usr/libexec/platform-python); ' + sudo() + 'env PYTHONIOENCODING=utf-8 $P - ' + cmd + ' ' + arg
    try:
        p = subprocess.run(ssh_base() + [line], input=(HERE / "move_remote.py").read_bytes(), capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise Stop("fail", f"服务器 {timeout} 秒没回话。重跑 run 接着来") from None
    out = p.stdout.decode("utf-8", "replace")
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("DFY_JSON ")), "")
    if not line:
        raise Stop("fail", "服务器没回结果：" + redact((p.stderr.decode("utf-8", "replace") or out).strip()[-200:]))
    return json.loads(line[len("DFY_JSON "):])


# ---------------------------------------------------------------- 这台电脑上的她

def find_data():
    if C.data_override:
        return C.data_override
    d = C.dir / "astrbot" / "data"
    if d.is_dir():
        return d
    # 启动器装的：数据在启动器的实例里，由共同探针认（认不准不猜）
    import backend_state
    r = backend_state.resolve(str(C.dir), repair=False)
    if r.get("ambiguous"):
        raise Stop("need_human", "这台电脑上有好几个 AstrBot 实例，认不准是哪一个。先在启动器里只留她那一个开着，再跑一次")
    if r.get("data_dir") and Path(r["data_dir"]).is_dir():
        return Path(r["data_dir"])
    raise Stop("need_human", f"{C.dir} 里没找到她的数据（astrbot\\data），启动器也没认出来。先确认工作目录对不对；启动器装的先把实例开起来")


def backend_login_ok(data):
    """这台电脑 backend.txt 的账号密码还对不对。先比后台存的 md5，对不上且她开着就真登一次（网页上改过密码时存的可能不是 md5）。
    返回 True / False，查不了返回 None。"""
    c = move_remote.read_json(str(data / "cmd_config.json"), {}) or {}
    if move_remote.password_matches(c, str(C.dir)):
        return True
    kv = read_kv(C.dir / "private" / "backend.txt")
    if not kv.get("username") or not kv.get("password"):
        return False
    url = (kv.get("url") or "http://127.0.0.1:6185").split()[0].rstrip("/")
    if not re.match(r"https?://(127\.0\.0\.1|localhost)[:/]", url + "/"):
        return None
    for pw in (kv["password"], hashlib.md5(kv["password"].encode("utf-8")).hexdigest()):
        r = move_remote.api(url, "POST", "/auth/login", {"username": kv["username"], "password": pw}, timeout=8)
        if isinstance(r.get("data"), dict) and r["data"].get("token"):
            return True
        if r.get("message") in ("URLError", "ConnectionRefusedError", "TimeoutError", "OSError", "RemoteDisconnected"):
            return None  # 她没开，真登不了
    return False


def local_answers():
    for p in (C.dir / "private" / "answers.json", C.dir / "answers.json"):
        o = move_remote.read_json(str(p))
        if isinstance(o, dict):
            return o
    return {}


def local_astrbot_version(data):
    """她现在是哪一版 AstrBot。数据库只往前升，搬到更老的镜像可能打不开。"""
    for sp in [C.dir / "uv" / "tools" / "astrbot" / "Lib" / "site-packages"]:
        try:
            for d in sp.glob("astrbot-*.dist-info"):
                return d.name[len("astrbot-"):-len(".dist-info")]
        except OSError:
            pass
    try:
        m = re.search(r'(?m)^version\s*=\s*"([0-9.]+)"', (data.parent / "pyproject.toml").read_text(encoding="utf-8"))  # 启动器：core\pyproject.toml
        if m:
            return m.group(1)
    except OSError:
        pass
    return ""


def ver(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


def list_files(root, skip_top=None):
    """要传的文件（相对路径、字节数），排好序，同一份数据每次分批都一样。链接和特殊文件不带。"""
    root = Path(root)
    out, size = [], 0
    for r, ds, fs in os.walk(root, followlinks=False):
        rel = Path(r).relative_to(root).as_posix()
        rel = "" if rel == "." else rel + "/"
        ds[:] = sorted(x for x in ds if x not in EXCLUDE_DIRS and (rel + x) != skip_top and not os.path.islink(os.path.join(r, x)))
        for f in sorted(fs):
            full = os.path.join(r, f)
            if os.path.splitext(f)[1].lower() in EXCLUDE_EXT or f.endswith(".move.tmp") or os.path.islink(full) or not os.path.isfile(full):
                continue
            try:
                n = os.path.getsize(full)
            except OSError:
                continue
            out.append((rel + f, n))
            size += n
    return out, size


def human(n):
    return f"{n / 1024 / 1024 / 1024:.1f} GB" if n >= 1 << 30 else f"{n / 1024 / 1024:.0f} MB"


# ---------------------------------------------------------------- 传文件

def push_tar(root, files, dest):
    """把这一批文件打成 tar.gz 流，经 ssh 在服务器上解开。不落盘，这台电脑不用多占地方。"""
    cmd = sudo() + "sh -c 'mkdir -p " + dest + " && tar xzf - -C " + dest + " --no-same-owner --no-same-permissions'"
    p = subprocess.Popen(ssh_base() + [cmd], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    err = []
    try:
        gz = gzip.GzipFile(fileobj=p.stdin, mode="wb", compresslevel=1, mtime=0)
        with tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for rel, _n in files:
                full = os.path.join(str(root), rel.replace("/", os.sep))
                try:
                    ti = tar.gettarinfo(full, arcname=rel)
                except OSError:
                    continue  # 刚好被挪走了（临时文件）
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                ti.mode = 0o644
                try:
                    with open(full, "rb") as fp:
                        tar.addfile(ti, fp)
                except OSError as e:
                    err.append(f"{rel}：{type(e).__name__}")
        gz.close()
        p.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    stderr = p.stderr.read().decode("utf-8", "replace")
    code = p.wait()
    if code != 0:
        raise Stop("fail", "传文件断了：" + redact(stderr.strip()[-200:] or f"退出码 {code}") + "。重跑 run，从这一批接着传")
    if err:
        raise Stop("fail", "有文件读不了（被别的程序占着？）：" + "、".join(err[:5]))


def batch_fp(root, files):
    """这一批文件现在的样子（路径、大小、修改时间）。断点只认一模一样的批：文件动过、前面多了文件，都算没传过，不会漏。"""
    h = hashlib.sha1()
    for rel, n in files:
        try:
            mt = os.stat(os.path.join(str(root), rel.replace("/", os.sep))).st_mtime_ns
        except OSError:
            mt = 0
        h.update(f"{rel}|{n}|{mt}\n".encode("utf-8", "surrogatepass"))
    return h.hexdigest()[:16]


def push_tree(root, dest, tag, st, skip_top=None):
    """分批传一棵目录。传完的批记在状态里，断了不重传；文件动过的批重传。"""
    if not re.fullmatch(r"/[A-Za-z0-9._/\-]{1,250}", dest):
        raise Stop("fail", "路径不对：" + dest)
    files, total = list_files(root, skip_top)
    batches, cur, size = [], [], 0
    for f in files:
        if cur and size + f[1] > BATCH_BYTES:
            batches.append(cur)
            cur, size = [], 0
        cur.append(f)
        size += f[1]
    if cur:
        batches.append(cur)
    done = st["batches"].setdefault(tag, [])
    todo = []
    for i, b in enumerate(batches):
        fp = batch_fp(root, b)  # 传之前算：传的时候文件又动了，下次就认不上，会重传
        if fp not in done:
            todo.append((i, b, fp))
    t0, sent = time.time(), 0
    for k, (i, b, fp) in enumerate(todo):
        push_tar(root, b, dest)
        done.append(fp)
        save_state(st)
        sent += sum(x[1] for x in b)
        speed = sent / max(1, time.time() - t0)
        left = sum(x[1] for _i, bb, _f in todo[k + 1:] for x in bb)
        say(f"STEP {tag} 第 {i + 1}/{len(batches)} 批传完" + (f"，还剩 {human(left)}，约 {int(left / max(speed, 1) // 60) + 1} 分钟" if left else ""))
    return len(files), total


# ---------------------------------------------------------------- 看情况

PROBE = r"""
. /etc/os-release 2>/dev/null; echo "os=$ID $VERSION_ID"
echo "arch=$(uname -m)"
echo "mem=$(free -m | awk '/^Mem:/{print $2}')"
echo "avail=$(free -m | awk '/^Mem:/{print $7}')"
echo "swap=$(free -m | awk '/^Swap:/{print $2}')"
echo "disk=$(df -m / | awk 'NR==2{print $4}')"
echo "docker=$(docker compose version --short 2>/dev/null)"
echo "containers=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^(astrbot|napcat)$' | tr '\n' ' ')"
echo "ports=$(ss -ltn 2>/dev/null | grep -oE ':(6185|6099)\b' | sort -u | tr '\n' ' ')"
echo "dir=$([ -e %(rdir)s ] && echo yes || echo no)"
echo "ours=$([ -f %(rdir)s/.move-state.json ] && echo yes || echo no)"
echo "deepseek=$(curl -s -m 8 -o /dev/null -w '%%{http_code}' https://api.deepseek.com)"
echo "daocloud=$(curl -s -m 8 -o /dev/null -w '%%{http_code}' https://m.daocloud.io)"
"""


def probe():
    _c, out = rsh(PROBE % {"rdir": C.rdir}, 90)
    return {m[1]: m[2].strip() for m in re.finditer(r"(?m)^(\w+)=(.*)$", out)}


def decide(a, ans, sv):
    """语音、记忆、看视频到了云上怎么办。返回 (决定, 要告诉他的话)。"""
    priv = C.dir / "private"
    has = lambda n: (priv / n).is_file() and (priv / n).stat().st_size > 0  # noqa: E731
    was_voice = str(ans.get("voice", ""))
    voice, notes = str(a.voice or ""), []
    if not voice:
        if was_voice == "4" or was_voice == "":
            voice = "5" if has("breeze-key.txt") else "3" if has("volc.txt") else "1"
        else:
            voice = was_voice
    if voice == "5" and not has("breeze-key.txt"):
        voice = "3" if has("volc.txt") else "1"
    if voice == "3" and not has("volc.txt"):
        voice = "1"
    if was_voice == "4":
        notes.append("本机语音带不走（服务器没显卡），到了云上换成 " + {"5": "Breeze 在线版", "3": "豆包", "1": "Edge 免费女声", "0": "不发语音"}.get(voice, voice))
    was_mem = str(ans.get("memory", ""))
    sf = "siliconflow-key.txt" if has("siliconflow-key.txt") else "sf-key.txt" if has("sf-key.txt") else ""
    if a.memory == "off":
        mem = "0"
    elif a.memory == "online":
        mem = "2"
    else:
        mem = "2" if sf and was_mem in ("1", "2", "") else "0" if was_mem in ("0",) else "2" if sf else "0"
    if mem == "2" and not sf:
        mem = "0"
    if was_mem == "1":
        if mem == "2":
            notes.append("记忆换成硅基流动在线（同一个模型 bge-m3），以前记住的都带着")
        else:
            notes.append("这台电脑的 Ollama 带不走，也没有硅基流动的 key：记忆文件带过去但先关着，新的事不记。给一把 key 再跑 --only memory 就开了")
    video = "1" if str(ans.get("video", "")) == "1" and int(sv.get("mem") or 0) >= 3500 else "0"
    if str(ans.get("video", "")) == "1" and video == "0":
        notes.append("这台服务器内存不到 3.5G，看视频先关着，别的不受影响")
    return {"voice": voice, "memory": mem, "video": video, "sf": sf}, notes


def cmd_plan(a, quiet=False):
    """看两边情况。有挡路的就停在这里，什么都没动。"""
    data = find_data()
    files, size = list_files(data, EXCLUDE_TOP_MEMES)
    memes, memes_size = list_files(data / "plugin_data" / "meme_manager") if (data / "plugin_data" / "meme_manager").is_dir() else ([], 0)
    n = move_remote.counts(str(data))
    ans = local_answers()
    sv = probe()
    dec, notes = decide(a, ans, sv)
    v = local_astrbot_version(data)
    image = v or ASTRBOT_PINNED
    block = []
    mem = int(sv.get("mem") or 0)
    disk = int(sv.get("disk") or 0)
    os_ = sv.get("os", "")
    if not re.match(r"(ubuntu|debian|alinux)\b", os_):
        block.append(("need_human", f"服务器系统是 {os_ or '认不出来'}，没测过。在控制台重装成 Ubuntu 24.04（会清空服务器，先问他上面有没有别的东西）"))
    if sv.get("arch") not in ("x86_64", "amd64", None, ""):
        block.append(("need_human", f"服务器是 {sv.get('arch')} 架构，NapCat 的镜像没测过，换一台 x86 的"))
    if mem and mem < 1800:
        block.append(("need_human", "服务器内存不到 2G，跑不动 QQ 加大脑。升配或换一台"))
    need = (size + memes_size) / 1024 / 1024 + 8000
    if disk and disk < need:
        block.append(("need_human", f"服务器硬盘只剩 {disk // 1024} GB，镜像加她的数据要约 {int(need) // 1024} GB。清一清或扩容"))
    if str(sv.get("deepseek", "000")).startswith("0"):
        block.append(("need_human", "服务器连不上 DeepSeek。查一下它的 DNS 和安全组出方向"))
    if backend_login_ok(data) is False:
        block.append(("need_human", "这台电脑 private\\backend.txt 的账号密码跟后台对不上（多半在网页上改过密码）。让他把现在的账号密码写进那个文件，再跑一次"))
    st = load_state()
    started = "compose" in st.get("done", [])
    ours = sv.get("ours") == "yes"
    if not started and not ours and not a.replace_existing:
        if sv.get("dir") == "yes":
            block.append(("need_human", f"服务器上 {C.rdir} 已经有东西了。确认不要了就加 --replace-existing（整个挪到旁边改名，不删）"))
        if sv.get("containers"):
            block.append(("need_human", f"服务器上已经有 {sv['containers'].strip()} 容器。照 云端.md「服务器上已经有他自己的机器人」先问他动不动"))
        if sv.get("ports"):
            block.append(("need_human", f"服务器的 {sv['ports'].strip()} 端口被别的程序占着。先看是什么，再决定"))
    if not quiet:
        say("PLAN 这台电脑：")
        say(f"  数据 {human(size)} / {len(files)} 个文件；表情包 {human(memes_size)} / {len(memes)} 个文件")
        say(f"  插件 {len(n['plugins'])} 个；人设 {n['personas']} 个；记忆 {n['memories']} 条；对话 {n['conversations']} 个；消息 {n['messages']} 条")
        say(f"  AstrBot {v or '版本没认出来，按 ' + ASTRBOT_PINNED + ' 走'}；现在语音 {ans.get('voice', '?')}、记忆 {ans.get('memory', '?')}、看视频 {ans.get('video', '?')}")
        say("PLAN 服务器：")
        say(f"  {os_}，内存 {mem} MB（可用 {sv.get('avail')}），swap {sv.get('swap')} MB，硬盘剩 {disk // 1024} GB，Docker {sv.get('docker') or '还没装'}")
        say("PLAN 会带上：人设、记忆、聊天记录、白名单、插件和插件设置" + ("、表情包" if a.memes == "carry" else ""))
        say("PLAN 带不走：本机语音和 Ollama（服务器没显卡）、桌宠（在这台电脑上）、QQ 的登录状态（要在服务器上重新扫码）")
        for t in notes:
            say("PLAN " + t)
        speed = 5 * 1024 * 1024 / 8  # 家里上传按 5 Mbps 算最慢的情况
        say(f"PLAN 她下线的时间：传 {human(size)} 约 {int(size / speed // 60) + 1} 分钟，加起容器和扫码，一般半小时内。表情包在她下线前先传（约 {int(memes_size / speed // 60) + 1} 分钟，不影响聊天）")
    for kind, msg in block:
        say(("PLAN 挡路：") + msg)
    if block:
        raise Stop(block[0][0], block[0][1])
    return data, dec, sv, image


# ---------------------------------------------------------------- 各步

def step_prep(a, ctx, st):
    script = r"""set -e
M=$(free -m | awk '/^Mem:/{print $2}')
if [ "$M" -lt 3500 ] && [ -z "$(swapon --show --noheadings 2>/dev/null)" ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 加了 2G swap
fi
[ "$(timedatectl show -p Timezone --value 2>/dev/null)" = "Asia/Shanghai" ] || timedatectl set-timezone Asia/Shanghai || true
if ! docker compose version >/dev/null 2>&1; then
  . /etc/os-release
  case "$ID" in
    ubuntu|debian) apt-get update -qq && apt-get install -y -qq docker.io docker-compose-v2 ;;
    alinux) dnf config-manager --add-repo=https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo && dnf -y install dnf-plugin-releasever-adapter --repo alinux3-plus && dnf -y install docker-ce --nobest ;;
    *) echo "不认识的系统 $ID" >&2; exit 2 ;;
  esac
  systemctl enable --now docker
fi
docker compose version --short
"""
    _c, out = rsh(script, 900)
    say("  Docker " + out.strip().splitlines()[-1])


COMPOSE = """services:
  napcat:
    image: %(napcat)s
    container_name: napcat
    restart: always
    environment:
      - NAPCAT_UID=${NAPCAT_UID:-1000}
      - NAPCAT_GID=${NAPCAT_GID:-1000}
      - MODE=astrbot
      - WEBUI_TOKEN=${NAPCAT_WEBUI_TOKEN}
      - ACCOUNT=${NAPCAT_ACCOUNT:-}
    ports:
      - "127.0.0.1:6099:6099"
    volumes:
      - ./data:/AstrBot/data
      - ./napcat/config:/app/napcat/config
      - ./ntqq:/app/.config/QQ
    mac_address: ${NAPCAT_MAC}
%(napcat_mem)s    networks: [astrbot_network]
  astrbot:
    image: %(astrbot)s
    container_name: astrbot
    restart: always
    environment:
      - TZ=Asia/Shanghai
    ports:
      - "127.0.0.1:6185:6185"
    volumes:
      - ./data:/AstrBot/data
%(breeze)s%(astrbot_mem)s    networks: [astrbot_network]
  tools:
    image: %(astrbot)s
    profiles: ["tools"]
    working_dir: /dafeiyu
    entrypoint: ["python", "-X", "utf8"]
    volumes:
      - ./kit:/dafeiyu/kit:ro
      - ./private:/dafeiyu/private
    networks: [astrbot_network]
networks:
  astrbot_network:
    driver: bridge
"""


def backend_text():
    kv = read_kv(C.dir / "private" / "backend.txt")
    if not kv.get("username") or not kv.get("password"):
        raise Stop("need_human", "这台电脑的 private\\backend.txt 里没有后台账号密码。让他把后台现在的账号密码写进去（改过密码的话写新的），再跑一次")
    return f"url: http://astrbot:6185\nusername: {kv['username']}\npassword: {kv['password']}\n".encode("utf-8")


def step_compose(a, ctx, st):
    d = C.rdir
    dec, tag = ctx["dec"], ctx["image"]
    # 服务器上原来的东西整个挪到旁边，不删
    if a.replace_existing and not st.get("replaced"):
        _c, out = rsh(f"""cd /tmp
if [ -e {d} ] && [ ! -f {d}/.move-state.json ]; then
  [ -f {d}/compose.yml ] && (cd {d} && docker compose --profile tools down 2>/dev/null || true)
  mv {d} {d}.old-$(date +%m%d-%H%M) && echo moved
fi
for c in astrbot napcat; do docker rm -f $c >/dev/null 2>&1 || true; done
""", 180)
        if "moved" in out:
            say("  服务器上原来的目录挪到了旁边（名字带 .old-日期，没删）")
        st["replaced"] = True
        save_state(st)
    # 服务器上已经有一份搬过来的数据（退回过又搬、从头再传）：先停容器，旧 data 整个挪开。
    # 往正在用的库上解包会弄坏它，留着的旧 -wal 也会被 SQLite 回放到新传的库上
    _c, out = rsh(f"""cd {d} 2>/dev/null || exit 0
if [ -f data/data_v4.db ] || [ -f data/cmd_config.json ]; then
  docker compose --profile tools down >/dev/null 2>&1 || true
  for c in astrbot napcat; do docker rm -f $c >/dev/null 2>&1 || true; done
  mv data data.old-$(date +%m%d-%H%M%S) && echo moved
fi
""", 180, check=False)
    if "moved" in out:
        say("  服务器上上次的数据挪到了旁边（data.old-日期，没删）")
    rsh(f"""set -e
mkdir -p {d}/private {d}/data {d}/kit {d}/napcat/config {d}/ntqq
chmod 700 {d} {d}/private
echo '{{"by":"move_to_cloud"}}' > {d}/.move-state.json
""")
    priv = C.dir / "private"
    # 连 QQ 的 token 沿用这台电脑上的，两边设置一致；没有就新生成
    tok = ""
    try:
        tok = (priv / "napcat-ws-token.txt").read_text(encoding="utf-8-sig").strip()
    except OSError:
        pass
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", tok):
        tok = secrets.token_hex(16)
    rput(f"{d}/private/onebot-token.txt", tok.encode())
    rput(f"{d}/private/napcat-ws-token.txt", tok.encode())
    rput(f"{d}/private/backend.txt", backend_text())
    for n in SECRETS:
        f = priv / n
        if f.is_file() and f.stat().st_size:
            rput(f"{d}/private/{n}", f.read_bytes())
    rsh(f"""set -e
cd {d}
[ -s private/napcat-webui-token.txt ] || openssl rand -hex 16 > private/napcat-webui-token.txt
if [ ! -s .env ]; then
  MAC=$(printf '02:42:%02x:%02x:%02x:%02x' $(od -An -N4 -tu1 /dev/urandom))
  printf 'NAPCAT_UID=1000\\nNAPCAT_GID=1000\\nNAPCAT_WEBUI_TOKEN=%s\\nNAPCAT_MAC=%s\\n' "$(cat private/napcat-webui-token.txt)" "$MAC" > .env
fi
chmod 600 .env private/*
""")
    small = int(ctx["sv"].get("mem") or 0) < 3500
    y = COMPOSE % {
        "napcat": NAPCAT_IMAGE, "astrbot": ASTRBOT_IMAGE % tag,
        "napcat_mem": "    mem_limit: 1200m\n" if small else "", "astrbot_mem": "    mem_limit: 800m\n" if small else "",
        "breeze": "      - ./private/breeze-key.txt:/dafeiyu/private/breeze-key.txt:ro\n" if dec["voice"] == "5" else "",
    }
    rput(f"{d}/compose.yml", y.encode("utf-8"))
    say("  拉镜像（大约两三 GB，第一次要几分钟）")
    code, out = rsh(f"cd {d} && docker compose pull astrbot napcat 2>&1 | tail -3", 1500, check=False)
    code2, _o = rsh(f"docker image inspect {ASTRBOT_IMAGE % tag} >/dev/null 2>&1 && docker image inspect {NAPCAT_IMAGE} >/dev/null 2>&1", 30, check=False)
    if code2 != 0:
        if tag != ASTRBOT_PINNED and ver(tag) <= ver(ASTRBOT_PINNED):
            say(f"  AstrBot {tag} 的镜像拉不到，用测过的 {ASTRBOT_PINNED}（比这台电脑上的新，数据会自动升级）")
            ctx["image"] = st["image"] = ASTRBOT_PINNED
            save_state(st)
            return step_compose(a, ctx, st)
        raise Stop("fail", "镜像拉不下来：" + redact(out.strip()[-200:]) + "。照 云端.md C5.2 末尾换镜像地址。这台电脑上的她还开着，没受影响")


def kit_root(a):
    root = Path(a.kit).resolve() if a.kit else HERE.parent
    if not (root / "scripts" / "astr_setup.py").exists() or not (root / "pack" / "dafeiyu-config-pack" / "tools" / "import_dafeiyu.py").exists():
        raise Stop("fail", f"{root} 不是完整的安装 Skill（缺配置包 pack）。用完整下载的 Skill 跑")
    return root


def step_kit(a, ctx, st):
    root = kit_root(a)
    n, size = push_tree(root, f"{C.rdir}/kit/dafeiyu-install", "kit", st)
    say(f"  传了 {n} 个文件，{human(size)}")


def step_memes(a, ctx, st):
    src = ctx["data"] / "plugin_data" / "meme_manager"
    if a.memes != "carry" or not src.is_dir():
        return say("  这次不传表情包")
    n, size = push_tree(src, f"{C.rdir}/data/plugin_data/meme_manager", "memes", st)
    say(f"  传了 {n} 个文件，{human(size)}")


def run_local_ps(script, *args, timeout=300):
    root = os.environ.get("SystemRoot", r"C:\Windows")
    ps = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    # 搬家脚本可能就跑在她 uv 目录的 Python 里（Scripts\\python.exe 是个转发的，真身是它的子进程）：告诉停进程的脚本放过这两个
    env = dict(os.environ, DFY_KEEP_PIDS=f"{os.getpid()},{os.getppid()}")
    p = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / script), *args], capture_output=True, timeout=timeout, env=env)
    out = p.stdout.decode("utf-8", "replace")
    for ln in out.splitlines():
        if ln.strip():
            say("  " + ln.strip())
    return p.returncode


def step_park(a, ctx, st):
    if not IS_WIN:
        return say("  不是 Windows，跳过（测试）")
    code = run_local_ps("move-local.ps1", "-Action", "park", "-Dir", str(C.dir))
    if code == 5:
        raise Stop("need_human", "启动器装的大脑要他自己停（可能还开着、在启动或者卡住了）：打开 AstrBot 启动器，点实例的「停止」，再把「开机自启动」关掉。停好了重跑 run")
    if code != 0:
        raise Stop("fail", f"停这台电脑上的她没成（退出码 {code}）。看上面的提示，处理好重跑 run")


def step_data(a, ctx, st):
    n, size = push_tree(ctx["data"], f"{C.rdir}/data", "data", st, EXCLUDE_TOP_MEMES)
    keep = [rel for rel, _n in list_files(ctx["data"], EXCLUDE_TOP_MEMES)[0] if rel.endswith(("-wal", "-shm", "-journal"))]
    r = rpy("prune", keep=keep)
    if not r.get("ok"):
        raise Stop("fail", "清服务器上的旧临时文件没成：" + str(r.get("msg") or r.get("why")) + "。重跑 run")
    say(f"  传了 {n} 个文件，{human(size)}")


def step_fix(a, ctx, st):
    tok = ""
    code, out = rsh(f"cat {C.rdir}/private/onebot-token.txt", 30)
    tok = out.strip()
    r = rpy("fix", token=tok, local_dir=str(C.dir) if IS_WIN else "")
    if not r.get("ok"):
        raise Stop("fail", "改设置没成：" + str(r.get("msg") or r.get("why")))
    if r.get("disabled"):
        say("  这些连着这台电脑的服务，先关了：" + "、".join(r["disabled"]))
    if r.get("proxy"):
        say("  设置里有这台电脑的代理地址，清掉了")
    if r.get("cleared"):
        say(f"  清掉了 {len(r['cleared'])} 处 Windows 路径（ffmpeg、识别模型这些，服务器上用不着）：" + "、".join(r["cleared"][:6]) + ("……" if len(r["cleared"]) > 6 else ""))
    st["fix"] = {k: r.get(k) for k in ("disabled", "cleared", "proxy", "platform")}
    if r.get("pw_ok") is False:
        say("  注意：backend.txt 的密码和后台存的对不上，下一步可能登不进去")
    if not r.get("platform"):
        say("  注意：设置里没找到 QQ 平台（aiocqhttp），下一步要补建")


def step_up(a, ctx, st):
    d = C.rdir
    rput(f"{d}/private/backend.txt", backend_text())  # 登不进去时他改好这个文件重跑，这里跟着换
    rsh(f"cd {d} && docker compose up -d astrbot napcat 2>&1 | tail -3", 300)
    say("  等 NapCat 写出设置，把连大脑的 token 填上")
    r = rpy("napcat", wait=120)
    if not r.get("ok"):
        raise Stop("fail", "NapCat 的 token 没填上：" + str(r.get("msg") or r.get("why")) + "。看 docker logs napcat")
    rsh("docker restart napcat >/dev/null", 120)
    say("  等大脑起来（第一次要给插件装依赖，2G 的机器可能要几分钟）")
    r = rpy("health", timeout=720, wait=600)
    if not r.get("ok"):
        if r.get("why") == "login":
            raise Stop("need_human", "后台登不进去：这台电脑 private\\backend.txt 的账号密码跟后台对不上。让他把现在的账号密码写进那个文件，重跑 run")
        raise Stop("fail", str(r.get("msg") or r.get("why")) + "。在服务器上看 docker logs astrbot")
    if r.get("plugins_off"):
        say("  这些插件没开：" + "、".join(r["plugins_off"]))
    if r.get("chat_ok") is False:
        say("  对话模型没测通（看 DeepSeek 余额和 key）")
    say(f"  大脑起来了，后台能登，插件 {len(r.get('plugins') or [])} 个")


def step_reimport(a, ctx, st):
    d = C.rdir
    dec = ctx["dec"]
    ans = {"base_url": "http://astrbot:6185", "username_file": "/dafeiyu/private/backend.txt", "username_key": "username",
           "password_file": "/dafeiyu/private/backend.txt", "password_key": "password", "owner_qq_file": "/dafeiyu/private/owner-qq.txt",
           "memory": dec["memory"], "voice": dec["voice"], "video": dec["video"]}
    if dec["sf"]:
        ans["siliconflow_key_file"] = "/dafeiyu/private/" + dec["sf"]
    if dec["voice"] == "5":
        la = local_answers()
        ans["breeze_api_key_file"] = "/dafeiyu/private/breeze-key.txt"
        ans["breeze_voice_id"] = la.get("breeze_voice_id") or "voc_u3nxkd5578wc"
    if dec["voice"] == "3":
        ans["volc_file"] = "/dafeiyu/private/volc.txt"
        for k, v in local_answers().items():  # 豆包的音色这些照原来的
            if k.startswith(("volc_", "doubao_", "seed_")) and not k.endswith("_file"):
                ans[k] = v
    rput(f"{d}/private/answers.json", json.dumps(ans, ensure_ascii=False, indent=1).encode("utf-8"))
    imp = "kit/dafeiyu-install/pack/dafeiyu-config-pack/tools/import_dafeiyu.py"
    blocks = ["voice", "memory"] + (["video"] if dec["video"] == "1" else []) + (["memes"] if a.memes != "carry" else [])
    res = {}
    for b in blocks:
        _c, out = rsh(f"cd {d} && docker compose run --rm -T tools {imp} --answers /dafeiyu/private/answers.json --only {b} 2>&1 | tail -40", 900, check=False)
        m = re.findall(rf"(?m)^RESULT {b}=(\w+)(.*)$", out)
        res[b] = m[-1][0] if m else "?"
        say(f"  {b}：{res[b]}" + (redact(m[-1][1])[:160] if m and res[b] not in ('ok', 'skip') else ""))
        if not m:
            say("    " + redact(out.strip()[-300:]))
    st["reimport"] = res


def step_workdir(a, ctx, st):
    d = C.rdir
    priv = C.cloud / "private"
    priv.mkdir(parents=True, exist_ok=True)
    _c, b = rsh(f"cat {d}/private/backend.txt")
    (priv / "cloud-backend.txt").write_text(b, encoding="utf-8")
    _c, w = rsh(f"cat {d}/private/napcat-webui-token.txt")
    (priv / "napcat-webui.txt").write_text(w.strip(), encoding="utf-8")
    if (C.dir / "private" / "qq-watch-off.txt").exists():
        (priv / "qq-watch-off.txt").write_text("这台电脑上关过掉线提醒，搬过来照旧关着。\n", encoding="utf-8")
    (C.dir / "private" / "moved-to-cloud.txt").write_text(f"搬到云服务器了（{time.strftime('%Y-%m-%d %H:%M')}）。云端工作目录：{C.cloud}\n", encoding="utf-8")
    if not IS_WIN:
        return say("  不是 Windows，工作台跳过（测试）")
    wd = Path(os.environ.get("LOCALAPPDATA", "")) / "dafeiyu" / "workdir.txt"
    if wd.exists() and not st.get("workdir_was"):
        st["workdir_was"] = wd.read_text(encoding="utf-8-sig").strip()
    wd.parent.mkdir(parents=True, exist_ok=True)
    wd.write_text(str(C.cloud), encoding="utf-8-sig")  # 带 BOM：老脚本的 Get-Content 才认得中文路径
    code = run_local_ps("finish.ps1", "-Dir", str(C.cloud), timeout=600)
    if code != 0:
        raise Stop("fail", "云端工作台没放好（多半是便携版 Python 没下下来）。网络好了重跑 run")


def compare(skip_memes=False):
    """两边对账。返回 (对得上吗, 一行行说明)。"""
    data = C.data_override or find_data()
    a = move_remote.counts(str(data))
    b = rpy("counts")
    lines, ok = [], True
    miss = sorted(set(a["plugins"]) - set(b.get("plugins") or []))
    lines.append(f"插件：这边 {len(a['plugins'])} 个，服务器 {len(b.get('plugins') or [])} 个" + (f"，少了 {'、'.join(miss)}" if miss else ""))
    ok = ok and not miss
    for k, name in (("personas", "人设"), ("conversations", "对话"), ("messages", "消息"), ("memories", "记忆"), ("whitelist", "白名单")):
        x, y = a.get(k), b.get(k)
        if x is None:  # 这边都读不出来（表不存在、库坏了）就没法比，不算搬丢
            lines.append(f"{name}：这边读不出来，没法比")
            continue
        good = not x or (y is not None and y >= x)  # 她上线后只会多不会少；这边有东西、服务器读不出来算对不上
        lines.append(f"{name}：这边 {x}，服务器 {'读不出来' if y is None else y}" + ("" if good else "  ← 少了"))
        ok = ok and good
    if a["memes"] and not skip_memes:
        good = (b.get("memes") or 0) >= a["memes"]
        lines.append(f"表情包文件：这边 {a['memes']}，服务器 {b.get('memes')}" + ("" if good else "  ← 少了"))
        ok = ok and good
    return ok, lines


def step_verify(a, ctx, st):
    ok, lines = compare(skip_memes=a.memes != "carry")
    for ln in lines:
        say("  " + ln)
    if not ok:
        raise Stop("fail", "两边对不上。这台电脑的数据没动。先看上面哪一项少了，查清楚后跑 run --fresh 从头传（服务器上这份挪到旁边，不删）")
    r = rpy("health", wait=60)
    if r.get("ok"):
        say(f"  后台能登，插件 {len(r.get('plugins') or [])} 个，对话模型{'通' if r.get('chat_ok') else '没测通' if r.get('chat_ok') is False else '没测'}")


ACT = {"prep": step_prep, "compose": step_compose, "kit": step_kit, "memes": step_memes, "park": step_park, "data": step_data,
       "fix": step_fix, "up": step_up, "reimport": step_reimport, "workdir": step_workdir, "verify": step_verify}


def cmd_run(a):
    st = load_state()
    if a.fresh:
        # 从头再传：已经做过的都不算，服务器上那份在 compose 这步挪到旁边。可以趁机换语音、记忆的选择
        st["done"] = [s for s in st["done"] if s == "prep"]
        st["batches"] = {}
        for k in ("dec", "image", "memes"):
            st.pop(k, None)
        save_state(st)
    data, dec, sv, image = cmd_plan(a, quiet=True)
    # 中途换主意（比如 --voice）不改已经做完的步骤；决定记在状态里，断点续跑用同一份
    dec = st.setdefault("dec", dec)
    image = st.setdefault("image", image)
    a.memes = st.setdefault("memes", a.memes)
    ctx = {"data": data, "dec": dec, "sv": sv, "image": image}
    save_state(st)
    # 接着跑：上次已经停过这台电脑上的她，中间他可能又开起来了。再停一次，不然两边抢小号。
    # 云上还没起的话，数据和改设置也重做一遍：只有她这期间动过的文件才会真传
    if "park" in st["done"] and "verify" not in st["done"]:
        say("STEP park 接着跑，先确认这台电脑上的她是停着的")
        step_park(a, ctx, st)
        if "up" not in st["done"]:
            st["done"] = [s for s in st["done"] if s not in ("data", "fix")]
            save_state(st)
    for s in STEPS:
        if s in st["done"]:
            continue
        say(f"STEP {s} {STEP_NAME[s]}")
        ACT[s](a, ctx, st)
        st["done"].append(s)
        save_state(st)
    say("搬完了，还差最后一步要他自己来：打开桌面「云端大肥鱼工作台」，点「扫码登录」，用小号的手机 QQ 扫。")
    say("扫上以后在工作台「服务器重启后」那项点「补上」。这台电脑上的她已经停了，数据原样留着，不想要了以后再卸。")


def cmd_verify(a):
    ok, lines = compare(skip_memes=load_state().get("memes", a.memes) != "carry")
    for ln in lines:
        say(ln)
    r = rpy("health", wait=30)
    say("后台：" + ("能登" if r.get("ok") else str(r.get("msg") or r.get("why"))))
    if not ok:
        raise Stop("fail", "两边对不上")


def cmd_status(a):
    st = load_state()
    for s in STEPS:
        say(("[完成] " if s in st["done"] else "[没做] ") + STEP_NAME[s])
    if st.get("reimport"):
        say("语音、记忆：" + json.dumps(st["reimport"], ensure_ascii=False))


def cmd_undo(a):
    """退回这台电脑：服务器上的容器撤掉（数据留着），这台电脑的入口放回去、把她开起来。"""
    st = load_state()
    if (C.cloud / "private" / "cloud.txt").exists() and not a.server_gone:
        # 确认撤掉了才开这台电脑，不然两边抢小号。用 down 不用 stop：不然服务器一重启 NapCat 又起来
        gone_hint = "这台电脑先不开，不然两边抢小号。服务器已经不要了（退订、重装了）就加 --server-gone 再跑"
        try:
            _c, out = rsh(f"""[ -f {C.rdir}/compose.yml ] && cd {C.rdir} && docker compose --profile tools down >/dev/null 2>&1
docker info >/dev/null 2>&1 && echo "left=$(docker ps -a --format '{{{{.Names}}}}' | grep -E '^(astrbot|napcat)$' | tr '\\n' ' ')"
""", 180, check=False)
        except Stop as e:
            raise Stop("need_human", f"{e.msg}。{gone_hint}") from None
        m = re.search(r"(?m)^left=(.*)$", out)
        if not m:
            raise Stop("need_human", f"服务器上的 Docker 没回话，确认不了容器撤没撤。{gone_hint}")
        if m.group(1).strip():
            raise Stop("need_human", f"服务器上的 {m.group(1).strip()} 没撤掉。{gone_hint}")
        say("服务器上的容器撤了（data 都留着）")
    try:
        (C.dir / "private" / "moved-to-cloud.txt").unlink()
    except OSError:
        pass
    # 先记下来再开她：启动器装的要他自己点启动，下面会停在那里，状态不能还是「搬完了」
    st["done"] = [s for s in st["done"] if s in ("prep", "kit")]
    st["batches"] = {k: v for k, v in st["batches"].items() if k == "kit"}
    save_state(st)
    if IS_WIN:
        run_local_ps("move-local.ps1", "-Action", "cloud-off", "-Dir", str(C.cloud))
        code = run_local_ps("move-local.ps1", "-Action", "unpark", "-Dir", str(C.dir))
        wd = Path(os.environ.get("LOCALAPPDATA", "")) / "dafeiyu" / "workdir.txt"
        wd.parent.mkdir(parents=True, exist_ok=True)
        wd.write_text(str(C.dir), encoding="utf-8-sig")  # 带 BOM：老脚本的 Get-Content 才认得中文路径
        if code == 6:
            raise Stop("need_human", "有的入口放不回去（被占用或没权限）。关掉占用的程序，再跑一次 undo")
        if code != 0:
            raise Stop("need_human", "这台电脑的入口放回去了，但她没自己起来。看上面的提示（启动器装的要在启动器里点启动）")
    say("退回来了。服务器搬完以后新记住的事不会带回来。想再搬一次，重跑 run（服务器上这份会挪到旁边，不删）。")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="一键搬上云")
    ap.add_argument("--dir", required=True, help="这台电脑上的工作目录")
    ap.add_argument("--cloud-dir", required=True, help="云端工作目录（放 private\\cloud.txt 和钥匙的那个）")
    ap.add_argument("--data", default="", help="测试用：数据文件夹不在默认位置时指一下")
    ap.add_argument("--kit", default="", help="测试用：完整 Skill 不在默认位置时指一下")
    ap.add_argument("cmd", choices=["plan", "run", "verify", "status", "undo"])
    ap.add_argument("--voice", default="", choices=["", "5", "3", "1", "0"])
    ap.add_argument("--memory", default="auto", choices=["auto", "online", "off"])
    ap.add_argument("--memes", default="carry", choices=["carry", "skip"])
    ap.add_argument("--replace-existing", action="store_true", help="服务器上已有的 /opt/dafeiyu 挪到旁边（不删）")
    ap.add_argument("--fresh", action="store_true", help="run：从头再传一遍，服务器上搬过来的那份 data 挪到旁边（不删）")
    ap.add_argument("--server-gone", action="store_true", help="undo：服务器已经不要了，不去撤容器，直接开回这台电脑")
    a = ap.parse_args()
    try:
        load_conf(a)
        if a.cmd == "plan":
            cmd_plan(a)
        else:
            {"run": cmd_run, "verify": cmd_verify, "status": cmd_status, "undo": cmd_undo}[a.cmd](a)
    except Stop as e:
        say(f"RESULT {e.kind} {e.msg}")
        return 3 if e.kind == "need_human" else 1
    except KeyboardInterrupt:
        say("RESULT fail 被打断了。重跑 run 接着来")
        return 1
    say("RESULT ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
