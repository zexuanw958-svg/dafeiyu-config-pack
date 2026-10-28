"""大肥鱼配置包导入工具。

把 settings/pack.json 里写的东西装进你的 AstrBot：人格、六个插件和它们的设置、
表情包、记忆、语音，还有 plugins/ 文件夹里的额外插件。可以反复运行，已经装过的会跳过或更新。

分成几块，各装各的，哪块没装上都不挡别的：
  core     变成大肥鱼：人格、设成默认、分段回复、认主人。只改本机设置，几秒钟，永远第一个跑
  plugins  插件市场的插件和配置包自带的插件
  video    看视频插件（回答文件里 video 写 "1" 才装）
  memory   记忆
  voice    语音
  memes    表情包（最大、最容易卡，放最后，后台下载、每 10 秒报进度）
  pet      电脑桌宠也是她（回答文件里 pet 写 "1" 才装）：桌宠互通插件 + 记忆范围（私聊和桌宠共用一份、群各记各的）
每块结束打一行 RESULT <块>=ok|partial|fail|skip reason=… next=…，给 Agent 照着转述。

用法：双击上一层的「导入大肥鱼.bat」，按提示回答几个问题（全装）。
Agent 用：python import_dafeiyu.py --answers 回答.json [--only core,plugins]
  --only 只装点名的块，别的块一律不碰（不会因为没填就把已经开着的记忆、语音关掉）。
  回答文件里的 *_file 字段指向放密码或 key 的文本文件；meme_zip_dir 指向已经下好的 <表情包id>.zip 所在文件夹（有就从本地导入，不再从 GitHub 下）；
  meme_copy_from 指向本机另一套的 astrbot/data/plugin_data/meme_manager 文件夹（另装一套时，那套装过的表情包直接拿来，不重下）。
  keep_persona 写 "1"：他已经有自己配的机器人，只拿几样（表情包、插件、语音、记忆、看视频）。core 一律不跑，
    人设和全局对话设置不动；他原来就装着的插件，设置也不动，只给这次新装的插件填配置包的设置。
改设置前先存快照：每次运行先把全局设置、人格、提供商、相关插件的设置和开关、表情包规则、工具权限存进
  回答文件旁边的 private/snapshots/（里面有 key，别外传）。第一次那份另存成 original.json（还没动之前的样子）。
  --snapshot            只存一份快照，不装
  --restore [快照文件]  退回到那份快照（不写文件就用 original.json）
"""
import base64
import getpass
import glob
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import tempfile
import uuid
import zipfile

PACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSWERS = {}
ANSWERS_PATH = ""
# 表情包插件下 GitHub 用的加速站，按顺序试；最后的空串是直连 GitHub。加速站常换常挂，挂了往这里补新的
GH_ACCELERATORS = ["https://gh.dpik.top/", "https://edgeone.gh-proxy.com/", "https://hk.gh-proxy.org/", "https://gh-proxy.org/", ""]  # 跟 Skill 的 common.ps1 同一份（10-28 实测）


def say(msg=""):
    print(msg, flush=True)


def step(msg):
    say(f"\n▶ {msg}")


def warn(msg):
    say(f"  ！{msg}")


def read_secret_file(path, key=None):
    lines = open(path, encoding="utf-8-sig").read().splitlines()
    if key:
        for line in lines:
            k, sep, v = line.replace("：", ":", 1).partition(":")  # 中文冒号也认
            if sep and k.strip().lower() == key:
                return v.strip()
    return lines[0].strip() if lines else ""  # 空文件（建好了还没粘）当成空值，别让工具崩掉


def ask(name, prompt, default=None, secret=False, choices=None):
    """问一个问题；--answers 模式下从回答文件取。"""
    if name in ANSWERS:
        return str(ANSWERS[name])
    if f"{name}_file" in ANSWERS:
        return read_secret_file(ANSWERS[f"{name}_file"], ANSWERS.get(f"{name}_key"))
    if ANSWERS:
        if default is not None:
            return default
        sys.exit(f"回答文件里缺 {name}")
    while True:
        hint = f"（直接回车用 {default}）" if default not in (None, "") else ""
        text = f"{prompt}{hint}："
        value = getpass.getpass(text) if secret else input(text)
        value = value.strip() or (default or "")
        if choices and value not in choices:
            say(f"  请输入 {' / '.join(choices)} 里的一个")
            continue
        if value:
            return value


class Api:
    def __init__(self, base):
        self.root = base.rstrip("/")
        self.token = None

    def call(self, method, path, body=None, timeout=120, raw_body=None, content_type="application/json"):
        url = self.root + "/api/v1" + path
        data = raw_body if raw_body is not None else (
            json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", content_type)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
                code = r.status
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            code = e.code
        except (TimeoutError, urllib.error.URLError, ConnectionError, http.client.HTTPException, OSError) as e:
            # 超时、连不上、后台中途断开：当成一次失败返回，别让整个工具崩掉（后台多半还在下大文件，过一会儿重跑就行）
            why = "后台在规定时间里没回话（多半还在下大文件）" if isinstance(e, TimeoutError) or "timed out" in str(e) else f"连不上后台（{type(e).__name__}）"
            return {"status": "error", "message": why, "_http": 0, "_neterr": True}
        try:
            out = json.loads(text) if text else {}
        except ValueError:
            out = {"status": "error", "message": text[:300]}
        if not isinstance(out, dict):
            out = {"status": "ok", "data": out}
        out["_http"] = code
        return out

    @staticmethod
    def ok(r):
        return r.get("_http", 200) < 400 and r.get("status", "ok") != "error"

    @staticmethod
    def msg(r):
        return str(r.get("message") or r.get("detail") or r.get("data") or r)[:300]

    def login(self, user, password):
        for pw in (password, hashlib.md5(password.encode()).hexdigest()):
            r = self.call("POST", "/auth/login", {"username": user, "password": pw})
            data = r.get("data")
            if isinstance(data, dict) and data.get("token"):
                self.token = data["token"]
                return True
        return False


def get_path(d, dotted):
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def set_path(d, dotted, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def fill(value, ctx):
    """把 {主人会话}、{对话模型} 这类占位换成真值。"""
    if isinstance(value, str):
        for k, v in ctx.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [fill(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, ctx) for k, v in value.items()}
    return value


def launcher_data_dirs():
    """启动器的数据目录：默认在用户目录下；在启动器「高级」里改过的，记在 APPDATA 下的设置文件里。"""
    dirs = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        try:
            d = json.load(open(os.path.join(appdata, "com.github.raven95676.astrbot-launcher", ".data-dir.json"),
                               encoding="utf-8-sig")).get("data_dir", "").strip()
            if d:
                dirs.append(d)
        except Exception:
            pass
    dirs.append(os.path.join(os.path.expanduser("~"), ".astrbot_launcher"))
    return dirs


def find_instances():
    """启动器各实例配置里写的后台端口（启动器常另给端口，这个只当候选）。"""
    found = []
    cfgs = []
    for data_dir in launcher_data_dirs():
        cfgs += glob.glob(os.path.join(data_dir, "instances", "*", "core", "data", "cmd_config.json"))
    for cfg in cfgs:
        try:
            port = json.load(open(cfg, encoding="utf-8-sig")).get("dashboard", {}).get("port")
        except Exception:
            continue
        if port:
            found.append((cfg.split(os.sep)[-4], int(port)))
    return found


def python_listening_ports():
    """本机 python 进程正在监听的端口（AstrBot 后台一定在里面）。拿不到就返回空。"""
    ports = set()
    try:
        if os.name == "nt":
            names = {}
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                 timeout=15, errors="replace").stdout
            for line in out.splitlines():
                cols = [c.strip('"') for c in line.split('","')]
                if len(cols) > 1:
                    names[cols[1]] = cols[0].lower()
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                                 timeout=15, errors="replace").stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                    host, _, port = parts[1].rpartition(":")
                    if host in ("127.0.0.1", "0.0.0.0") and names.get(parts[4], "").startswith("python"):
                        ports.add(int(port))
        else:
            out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True,
                                 timeout=15, errors="replace").stdout
            for line in out.splitlines():
                if line.lower().startswith("python") and "(LISTEN)" in line:
                    addr = line.split()[-2]
                    host, _, port = addr.rpartition(":")
                    if host in ("127.0.0.1", "*", "localhost") and port.isdigit():
                        ports.add(int(port))
    except Exception:
        pass
    return ports


def is_astrbot_dashboard(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            return "AstrBot" in r.read(65536).decode("utf-8", "replace")
    except Exception:
        return False


def pick_backend():
    if "base_url" in ANSWERS:
        return ANSWERS["base_url"]
    candidates = sorted({port for _, port in find_instances()} | python_listening_ports())
    alive = [port for port in candidates if is_astrbot_dashboard(port)]
    if not alive:
        # 兜底：扫一遍 AstrBot 和启动器常用的端口段
        alive = [port for port in [*range(6185, 6201), *range(18600, 18621)]
                 if port not in candidates and is_astrbot_dashboard(port)]
    if len(alive) > 1:
        say("  找到好几个正在运行的 AstrBot：" + "、".join(f"127.0.0.1:{p}" for p in alive))
    default = f"http://127.0.0.1:{alive[0]}" if alive else "http://127.0.0.1:6185"
    url = ask("base_url", "AstrBot 后台地址（浏览器里打开后台时地址栏那串）", default)
    if not url.startswith("http"):
        url = "http://" + url
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def upsert_provider(api, conf):
    pid = conf["id"]
    existing = provider_config(api, pid)
    if existing:
        merged = {**existing, **conf}
        r = api.call("PUT", f"/providers/{urllib.parse.quote(pid, safe='')}", {"config": merged})
    else:
        r = api.call("POST", "/providers", {"config": conf})
    if not api.ok(r):
        warn(f"保存「{pid}」失败：{api.msg(r)}")
        return False
    return True


def provider_config(api, pid):
    """读一个模型提供商的配置；没有就返回 None。"""
    r = api.call("GET", f"/providers/{urllib.parse.quote(pid, safe='')}")
    data = r.get("data") if api.ok(r) and isinstance(r.get("data"), dict) else {}
    data = data.get("provider", data)
    return data if isinstance(data, dict) and data.get("id") == pid else None


def test_provider(api, pid):
    r = api.call("POST", "/providers/test", {"provider_id": pid}, timeout=90)
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    status = str(data.get("status") or r.get("status") or "")
    if api.ok(r) and status not in ("error", "unavailable", "failed"):
        return True, ""
    return False, str(data.get("error") or data.get("message") or api.msg(r))[:200]


def extra_plugins():
    root = os.path.join(PACK, "plugins")
    zips = glob.glob(os.path.join(root, "*.zip"))
    dirs = [d for d in glob.glob(os.path.join(root, "*")) if os.path.isfile(os.path.join(d, "metadata.yaml"))]
    return sorted(zips + dirs)


def plugin_name(path):
    meta = os.path.join(path, "metadata.yaml")
    if os.path.isfile(meta):
        for line in open(meta, encoding="utf-8-sig"):
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def zip_folder(folder):
    import io
    import zipfile
    buf = io.BytesIO()
    top = os.path.basename(folder.rstrip("\\/"))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for f in filenames:
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(dirpath, f)
                zf.write(full, os.path.join(top, os.path.relpath(full, folder)))
    return buf.getvalue()


def multipart(field, filename, payload):
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/zip\r\n\r\n").encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + payload + tail, f"multipart/form-data; boundary={boundary}"


BLOCKS = {
    "core": "变成大肥鱼（人格、设成默认、分段回复、认主人）",
    "plugins": "插件（群聊、QQ 空间、记忆、表情包这些插件本身）",
    "memes": "表情包",
    "memory": "记忆",
    "voice": "语音",
    "video": "看视频",
    "pet": "桌宠也是她（互通插件、记忆范围）",
}


class Run:
    """一次导入里各块共用的东西。"""

    def __init__(self, api, pack, owner, memory, sf_key, voice, mimo_key, volc_app_id, volc_token):
        self.api, self.pack, self.owner = api, pack, owner
        self.memory, self.sf_key = memory, sf_key
        self.voice, self.mimo_key, self.volc_app_id, self.volc_token = voice, mimo_key, volc_app_id, volc_token
        self.results = []     # (名字, 成没成)，最后打成清单
        self.changed = []     # 改过设置、要重新加载的插件
        self.installed = set()
        self.platform_id = self.chat_id = self.session = ""
        self.ctx = {}
        # 只拿几样（他自己配的机器人）：人设、全局对话设置、他原来就有的插件的设置都不动
        self.keep = str(ANSWERS.get("keep_persona", "0")) == "1"
        self.wanted = []
        self.pre_installed = set()   # 这次开跑前就装着的插件

    def refresh_installed(self):
        r = self.api.call("GET", "/plugins")
        if self.api.ok(r):
            self.installed = {p.get("name") for p in (r.get("data") or []) if isinstance(p, dict)}
        return self.installed

    def config(self):
        r = self.api.call("GET", "/config-profiles/default")
        cfg = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
        if not isinstance(cfg, dict):
            raise RuntimeError(f"读不到后台配置：{self.api.msg(r)}")
        return cfg

    def save_config(self, cfg):
        return self.api.ok(self.api.call("PUT", "/config-profiles/default", cfg))


def block_core(S):
    api, pack = S.api, S.pack
    if S.keep:
        say("  他选了「只拿几样」：人设和对话设置一律不动，这块不跑")
        return "skip", "keep-persona", "想整个换成大肥鱼：先跟他确认，把回答文件里的 keep_persona 删掉再跑 --only core（改之前会自动存快照）"
    step("调整对话模型")
    prov = provider_config(api, S.chat_id)
    if prov and "deepseek" in (str(prov.get("model", "")) + str(prov.get("provider_source_id", ""))).lower():
        extra = dict(prov.get("custom_extra_body") or {})
        extra.update(pack["chat_model"]["custom_extra_body"])
        prov["custom_extra_body"] = extra
        # 看图：deepseek-flash 官方支持看图，模型能力里没勾「图片」的补上
        mods = list(prov.get("modalities") or ["text", "tool_use"])
        if "flash" in str(prov.get("model", "")).lower() and "image" not in mods:
            prov["modalities"] = mods + ["image"]
        ok = api.ok(api.call("PUT", f"/providers/{urllib.parse.quote(S.chat_id, safe='')}", {"config": prov}))
        say("  已关闭深度思考，回得更快" if ok else "  ！没改成，不影响使用")
    else:
        say("  不是 DeepSeek 模型，跳过")

    step("写入人格「大肥鱼」")
    pid = pack["persona"]["id"]
    text = open(os.path.join(PACK, pack["persona"]["file"]), encoding="utf-8-sig").read().replace("{OWNER_QQ}", S.owner)
    qid = urllib.parse.quote(pid, safe="")
    cur = api.call("GET", f"/personas/{qid}")
    if api.ok(cur) and isinstance(cur.get("data"), dict) and cur["data"].get("persona_id") == pid:
        body = {k: v for k, v in cur["data"].items() if k in ("begin_dialogs", "skills", "folder_id", "sort_order", "custom_error_message")}
        body.update({"system_prompt": text, "tools": None})
        r = api.call("PUT", f"/personas/{qid}", body)
    else:
        r = api.call("POST", "/personas", {"persona_id": pid, "system_prompt": text, "begin_dialogs": [], "tools": None})
    persona_ok = api.ok(r)
    S.results.append(("人格", persona_ok))
    say("  完成" if persona_ok else f"  ！失败：{api.msg(r)}")

    # 设成默认人格、分段回复、认主人这些只改本机设置，不下载任何东西，放在最前面：后面哪块没装上，她也已经是大肥鱼了
    step("设成默认人格，打开分段回复，认得主人")
    cfg = S.config()
    for k, val in pack["base"].items():
        set_path(cfg, k, fill(val, S.ctx))
    # 引用消息里的图要靠「默认图片转述模型」，没设就用对话模型
    if not get_path(cfg, "provider_settings.default_image_caption_provider_id"):
        set_path(cfg, "provider_settings.default_image_caption_provider_id", S.chat_id)
    ps = cfg.setdefault("platform_settings", {})
    if ps.get("enable_id_white_list"):
        wl = ps.setdefault("id_whitelist", [])
        if S.session not in wl and S.owner not in wl:
            wl.append(S.session)
    admins = cfg.setdefault("admins_id", [])
    if S.owner not in admins:
        admins.append(S.owner)
    base_ok = S.save_config(cfg)
    S.results.append(("设成默认人格和基础设置", base_ok))
    say("  完成" if base_ok else "  ！失败，后台没存上")
    if persona_ok and base_ok:
        say("\n  ★ 她已经是大肥鱼了：去 QQ 先发 /reset 清掉旧对话，再发「在吗」。")
        return "ok", "", ""
    return "fail", "persona" if not persona_ok else "config", "重跑：--only core"


def install_extras(S, pick):
    """装 plugins/ 里的额外插件。pick(meta, name) 返回真的才装。"""
    for z in extra_plugins():
        base = z[:-4] if z.endswith(".zip") else z
        meta_path = base + ".json"
        meta = json.load(open(meta_path, encoding="utf-8-sig")) if os.path.exists(meta_path) else {}
        name = meta.get("name") or plugin_name(z) or os.path.basename(base)
        if not pick(meta, name):
            continue
        if name in S.installed and not meta.get("always_reinstall"):
            say(f"  {name}：已装过")
        else:
            say(f"  {name}：从配置包安装…")
            payload = open(z, "rb").read() if z.endswith(".zip") else zip_folder(z)
            body, ctype = multipart("file", os.path.basename(base) + ".zip", payload)
            r = S.api.call("POST", "/plugins/install/upload", raw_body=body, content_type=ctype, timeout=600)
            if not S.api.ok(r):
                warn(f"装不上：{S.api.msg(r)}")
        if meta.get("config") and not any(p["name"] == name for p in S.pack["plugins"]):
            S.pack["plugins"].append({"name": name, "config": meta["config"], "what": meta.get("what", "")})
        S.refresh_installed()
        S.results.append((f"插件 {name}", name in S.installed))


def extra_wanted(S, meta, name):
    """plugins 块装哪些额外插件：跟语音绑定的只在选了那种语音时装；默认不装的（唱歌）回答文件里写了 "1" 才装；已经装过的照常更新。
    看视频插件归 video 块管，这里不碰。"""
    if meta.get("opt_in") == "video":
        return False
    if name in S.installed:
        return True
    if meta.get("only_with_voice") and meta["only_with_voice"] != S.voice:
        return False
    if meta.get("opt_in") and str(ANSWERS.get(meta["opt_in"], "0")) != "1":
        return False
    return True


def apply_plugin_configs(S, names=None):
    api = S.api
    for p in S.pack["plugins"]:
        if p["name"] not in S.installed or not p["config"] or (names is not None and p["name"] not in names):
            continue
        r = api.call("GET", f"/plugins/config?plugin_id={urllib.parse.quote(p['name'])}")
        conf = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
        if not isinstance(conf, dict):
            warn(f"{p['name']}：读不到设置")
            continue
        for k, v in p["config"].items():
            if get_path(conf, k) is None and "." in k and get_path(conf, k.rsplit(".", 1)[0]) is None:
                warn(f"{p['name']}：设置项 {k} 不存在，可能插件更新改名了，跳过")
                continue
            val = fill(v, S.ctx)
            # 占位（{硅基流动key}、{asr_dir} 这类）这次没值：别把他已经填好的设置冲成空的
            if isinstance(v, str) and v.startswith("{") and val == "" and get_path(conf, k):
                continue
            set_path(conf, k, val)
        r = api.call("PUT", "/plugins/config", {"plugin_id": p["name"], "config": conf})
        say(f"  {p['name']}：{'完成' if api.ok(r) else '失败 ' + api.msg(r)}")
        if api.ok(r):
            S.changed.append(p["name"])


def reload_changed(S, skip=()):
    names = [n for n in dict.fromkeys(S.changed) if n not in skip]
    if not names:
        return
    step("重新加载改过设置的插件")
    for name in names:
        S.api.call("POST", "/plugins/reload", {"plugin_id": name}, timeout=180)
    S.changed = [n for n in S.changed if n in skip]
    say("  完成")


def block_plugins(S):
    api, pack = S.api, S.pack
    step("从插件市场安装插件（插件市场是 AstrBot 自己的服务器，第一次要下载，慢的话等一两分钟）")
    S.refresh_installed()
    # 只拿几样：表情包、记忆的插件跟着那两块走，他没点那两样就不装
    skip = set()
    if S.keep:
        skip = {n for n, b in (("meme_manager", "memes"), ("astrbot_plugin_livingmemory", "memory")) if b not in S.wanted}
    for p in pack["plugins"]:
        if p["name"] in skip:
            continue
        if p["name"] in S.installed:
            say(f"  {p['name']}：已装过")
            continue
        say(f"  {p['name']}：安装中…（{p['what']}）")
        r = api.call("POST", "/plugins/install/github",
                     {"install_method": "market", "market_plugin_id": p["market_id"]}, timeout=600)
        if not api.ok(r):
            warn(f"装不上：{api.msg(r)}")
            warn("插件是从插件市场的服务器下的，可能一时没响应。等几分钟再运行一次本工具（只补插件：--only plugins）；"
                 "还装不上，就在后台「插件 → 插件市场」里搜这个插件手动装。")
    S.refresh_installed()
    market_names = [p["name"] for p in pack["plugins"] if p["name"] not in skip]
    for name in market_names:
        S.results.append((f"插件 {name}", name in S.installed))

    install_extras(S, lambda meta, name: extra_wanted(S, meta, name))

    step("改插件设置")
    if S.keep:
        # 他原来就装着的插件是他自己调过的：设置不动，只给这次新装的填配置包的设置
        kept = [p["name"] for p in pack["plugins"] if p["name"] in S.pre_installed and p["name"] not in skip]
        if kept:
            say(f"  你原来就装着的，设置不动：{'、'.join(kept)}")
        apply_plugin_configs(S, [p["name"] for p in pack["plugins"] if p["name"] not in S.pre_installed and p["name"] not in skip])
    else:
        apply_plugin_configs(S)
    owners = {}
    if S.keep:
        r = api.call("GET", "/tools")
        owners = {t.get("name"): t.get("origin_name") for t in (r.get("data") or []) if isinstance(t, dict)} if api.ok(r) and isinstance(r.get("data"), list) else {}
    for tool, perm in pack["tool_permissions"].items():
        if S.keep and owners.get(tool) in S.pre_installed:
            continue  # 他原来就有这个插件，权限是他自己定的
        r = api.call("PATCH", f"/tools/{urllib.parse.quote(tool, safe='')}/permission", {"permission": perm})
        if not api.ok(r):
            warn(f"工具 {tool} 权限没改成：{api.msg(r)}")
    say("  发空间、改签名只听主人的：已设置")
    if S.keep:
        cfg = S.config()
        if S.owner not in [str(x) for x in cfg.get("admins_id") or []]:
            warn("发空间、改签名只听管理员的，他的大号还不是这个机器人的管理员：要用就在后台「配置文件 → 平台配置 → 管理员 ID」里加上大号（全局设置我没替他改）")
        if not get_path(cfg, "provider_settings.default_image_caption_provider_id"):
            warn("后台没设「默认图片转述模型」：群里没 @ 她的图她先不看（全局设置我没替他改，想要就在后台「配置文件 → 模型」里选他的对话模型）")
    reload_changed(S, skip=("astrbot_plugin_livingmemory",))
    missing = [n for n in market_names if n not in S.installed]
    if missing:
        return "fail", "market:" + ",".join(missing), "过几分钟重跑：--only plugins"
    return "ok", "", ""


def block_video(S):
    step("看视频插件")
    S.refresh_installed()
    blind = False
    if S.keep:
        # 看视频是截几张画面交给对话模型看；只拿几样不跑 core，他的模型没开看图的话这里只提醒、不替他改
        prov = provider_config(S.api, S.chat_id) or {}
        mods = prov.get("modalities")
        blind = isinstance(mods, list) and bool(mods) and "image" not in mods
        if blind:
            warn(f"他的对话模型「{S.chat_id}」没开看图：视频画面她看不到，只能听声音。要看画面就在后台这个模型的「模型能力」里勾上图片（我没替他改）")
    install_extras(S, lambda meta, name: meta.get("opt_in") == "video")
    name = next((p["name"] for p in S.pack["plugins"] if p["name"].endswith("_video")), "astrbot_plugin_dafeiyu_video")
    # 本机识别没装上时插件改用硅基流动在线识别：这次没给 key，后台里已经有一把（上一期 bat 装的在线记忆）就接着用
    if not S.ctx.get("硅基流动key"):
        S.ctx["硅基流动key"] = existing_sf_key(S)[0]
    apply_plugin_configs(S, [name])
    reload_changed(S)
    if name not in S.installed:
        return "fail", "upload", "重跑：--only video"
    # 告诉他她听不听得到视频里说的话（跟插件找识别程序的办法一样：设置里填的，或者 ffmpeg 旁边的 asr 文件夹）
    r = S.api.call("GET", f"/plugins/config?plugin_id={name}")
    vc = ((r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None) or {}
    ff = str(vc.get("ffmpeg_path") or S.ctx.get("ffmpeg") or "")
    cands = [str(vc.get("asr_dir") or S.ctx.get("asr_dir") or "")] + ([os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(ff))), "asr")] if ff else [])
    # 模型要是完整的（约 226 MB）：以前的安装脚本断了会在 asr 里留半截
    local = any(c and all(os.path.isfile(os.path.join(c, f)) for f in ("sherpa-onnx-offline.exe", "model.int8.onnx", "tokens.txt"))
                and os.path.getsize(os.path.join(c, "model.int8.onnx")) >= 200_000_000 for c in cands)
    if local:
        say("  听声音：本机识别")
    elif str(vc.get("asr_key") or S.ctx.get("硅基流动key") or "").strip():
        say("  听声音：本机识别没装好，先用硅基流动在线识别（视频里的声音、QQ 语音会发给硅基流动转成字）")
    else:
        warn("本机识别模型没装好，也没有硅基流动 key：" + ("她现在视频画面看不到、声音也听不到，QQ 语音也听不懂" if blind else "她能看视频画面，但听不到视频里说的话、听不懂 QQ 语音"))
        return "partial", "no-asr", "装好识别模型（install-asr.ps1）再跑 --only video；或者给一把硅基流动 key（siliconflow_key_file）走在线识别"
    return "ok", "", ""


def block_memes(S):
    api, pack = S.api, S.pack
    step("表情包（在后台下载，每 10 秒报一次进度；哪套下不动就先跳过，不影响聊天）")
    if "meme_manager" not in S.refresh_installed() and not ensure_plugin(S, "meme_manager"):
        S.results.append(("表情包", False))
        return "skip", "no-plugin", "先装插件：--only plugins"
    ext = "/plugins/extensions/meme_manager"
    have = meme_pack_ids(api, ext)
    zip_dir = str(ANSWERS.get("meme_zip_dir", ""))
    copy_from = str(ANSWERS.get("meme_copy_from", ""))
    # 小的先装：网慢的人至少先有一套能用
    for mp in sorted(pack["meme_packs"], key=lambda m: m.get("size_mb", 0)):
        if mp["id"] in have:
            say(f"  {mp['id']}：已有")
            S.results.append((f"表情包 {mp['id']}", True))
            continue
        local = os.path.join(zip_dir, mp["id"] + ".zip") if zip_dir else ""
        other = os.path.join(copy_from, "packs", mp["id"]) if copy_from else ""
        if local and os.path.isfile(local):
            ok = meme_import_zip(api, ext, local, mp.get("default", False))
        elif other and os.path.isfile(os.path.join(other, "manifest.json")):
            ok = meme_import_dir(api, ext, other, mp.get("default", False))
            if not ok:
                ok = meme_install_with_progress(api, ext, mp)
        else:
            ok = meme_install_with_progress(api, ext, mp)
        say(f"  {mp['id']}：{'完成' if ok else '这会儿没装上，先跳过'}")
        S.results.append((f"表情包 {mp['id']}", ok))
    # 默认规则写成「几套一起用」：meme_manager 的选择规则支持 pack_ids 列表，主套排第一
    use = [mp["id"] for mp in sorted(pack["meme_packs"], key=lambda m: not m.get("default")) if mp.get("use", True)]
    if S.keep and "meme_manager" in S.pre_installed:
        # 他原来就有表情包插件：他自己那几套排前面照用，大肥鱼的加在后面
        mine = meme_default_ids(api, ext)
        if mine:
            say(f"  他原来的表情包（{'、'.join(mine)}）留着，大肥鱼的加在后面一起用")
            use = mine + [u for u in use if u not in mine]
    ok = bool(use) and meme_use_together(api, ext, use)
    if not ok:
        # 规则接口用不了（比如插件版本太老），或者主套没装上：有哪套就先用哪套
        have = meme_pack_ids(api, ext)
        first = next((mp["id"] for mp in sorted(pack["meme_packs"], key=lambda m: not m.get("default")) if mp["id"] in have), None)
        if first:
            ok = api.ok(api.call("POST", f"{ext}/packs/default", {"pack_id": first}))
            say(f"  先把 {first} 设成默认：{'完成' if ok else '失败'}")
    S.results.append(("表情包默认用的几套", ok))
    missing = [mp["id"] for mp in pack["meme_packs"] if mp["id"] not in meme_pack_ids(api, ext)]
    if missing:
        return ("fail" if len(missing) == len(pack["meme_packs"]) else "partial"), "network:" + ",".join(missing), \
            "过一阵补装：--only memes（她照常聊天，只是先不配图或少一套）"
    return "ok", "", ""


def block_memory(S):
    api, pack = S.api, S.pack
    step("记忆")
    lm = "astrbot_plugin_livingmemory"
    S.refresh_installed()
    if S.memory not in ("0", "1", "2"):
        say("  回答文件里没写 memory，记忆不动。")
        return "skip", "not-in-answers", "回答文件里写上 memory（1 本机、2 硅基流动、0 不要）再跑 --only memory"
    if S.memory == "0":
        # 明确不要：插件在就关掉（以前只打一句「先不装」，开着的半截记忆会一直留着）。以前记住的不删
        if lm in S.installed:
            api.call("PATCH", "/plugins/enabled", {"plugin_id": lm, "enabled": False})
            say("  先不要：记忆插件关掉了（以前记住的还在）。以后想要，再运行一次本工具选 1 或 2（只装记忆：--only memory）。")
        else:
            say("  先不装。以后想装，再运行一次本工具选 1 或 2（只装记忆：--only memory）。")
        return "skip", "not-chosen", ""
    if lm not in S.installed and not ensure_plugin(S, lm):
        S.results.append(("记忆", False))
        return "skip", "no-plugin", "先装插件：--only plugins"
    m = pack["memory"]
    conf, reason, nxt = None, "", "处理好再跑：--only memory"
    if S.memory == "1":
        o = m["ollama"]
        if ollama_ready(o["ollama_model"]):
            conf = {"id": o["id"], "embedding_api_key": o["embedding_api_key"],
                    "embedding_api_base": o["embedding_api_base"], "embedding_model": o["embedding_model"]}
        else:
            warn("Ollama 没在运行或向量模型没下好，记忆先关着。装好以后再运行一次本工具（只装记忆：--only memory）。")
            reason, nxt = "ollama", "先跑 install-ollama.ps1（它会把 bge-m3 从国内 ModelScope 下好），再跑 --only memory"
    else:
        s = m["siliconflow"]
        conf = {"id": s["id"], "embedding_api_base": s["embedding_api_base"], "embedding_model": s["embedding_model"]}
        old_key, old_pid = existing_sf_key(S)
        if S.sf_key:
            conf["embedding_api_key"] = S.sf_key
        elif old_key and old_pid != s["id"]:
            conf["embedding_api_key"] = old_key  # 他自己在后台建的硅基流动模型里有 key：借过来（同一个提供商就不重写，原样留着）
        if not S.sf_key and not old_key:
            # 不建一个空 key 的硅基流动记忆：建了也测不通，插件还会指着它，看着像装好了
            warn("硅基流动的 key 是空的（key 文件还没粘进去？），记忆先关着。")
            conf, reason = None, "no-key"
            nxt = "把硅基流动 key 粘进 siliconflow_key_file 指的文件再跑 --only memory；不要在线记忆就把 memory 改成 1（本机）或 0（关掉）再跑"
        elif not S.sf_key:
            say("  这次没给 key，接着用后台里已经填好的那把硅基流动 key")
    mem_ok = False
    if conf:
        conf.update({"type": "openai_embedding", "provider": "openai", "provider_type": "embedding", "enable": True,
                     "embedding_dimensions": m["embedding_dimensions"], "embedding_dimensions_mode": "auto",
                     "timeout": 20, "proxy": ""})
        if upsert_provider(api, conf):
            good, why = test_provider(api, conf["id"])
            if not good:
                warn(f"记忆模型测试没通过：{why}")
                reason = "test"
            # 测通了才把记忆插件指过去、打开；没测通就关着，不留「插件开着、模型不通」的半截
            r = api.call("GET", f"/plugins/config?plugin_id={lm}") if good else {}
            lconf = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
            if isinstance(lconf, dict):
                set_path(lconf, "provider_settings.embedding_provider_id", conf["id"])
                for k, v in m.get("plugin_config", {}).items():
                    set_path(lconf, k, v)
                was = None
                if S.keep and lm in S.pre_installed:
                    say("  记忆插件是他原来就装着的：记忆范围不动（要让桌宠也共用，跑 --only pet）")
                else:
                    was = memory_scope(S, lconf)
                mem_ok = api.ok(api.call("PUT", "/plugins/config", {"plugin_id": lm, "config": lconf}))
                if mem_ok and was is not None:
                    scope_note(S, was)
    api.call("PATCH", "/plugins/enabled", {"plugin_id": lm, "enabled": mem_ok})
    if mem_ok:
        api.call("POST", "/plugins/reload", {"plugin_id": lm}, timeout=180)
    say("  记忆已打开" if mem_ok else "  记忆插件先关着（没配好记忆模型时开着会报错）")
    S.results.append(("记忆", mem_ok))
    return ("ok", "", "") if mem_ok else ("fail", reason or "config", nxt)


def memory_scope(S, lconf):
    """记忆范围三项（10-28 定）：主人的 QQ 私聊和电脑桌宠共用一份（global），每个群各记各的（强制隔离），
    没登记的群不记（记忆白名单）。白名单只放完整会话 ID：只写主人号的话，主人在没登记的群里说话也会被记，
    所以单独的号码（和 平台:号码）会被拿掉。群取后台白名单里登记过的（让她进群那步 astr_setup.py whitelist 会同步）。
    只改 lconf，不保存。返回改之前的模式。"""
    cfg = S.config()
    pref = f"{S.platform_id}:GroupMessage:"
    groups = [str(x) for x in (get_path(cfg, "platform_settings.id_whitelist") or []) if str(x).startswith(pref)]

    def split(t):
        return [x.strip() for x in re.split(r"[\n,;，；]+", str(t or "")) if x.strip()]

    was = get_path(lconf, "filtering_settings.memory_scope_mode") or "legacy"
    iso = [x for x in split(get_path(lconf, "filtering_settings.isolated_sessions")) if not x.startswith(pref)]
    old = split(get_path(lconf, "access_control.allowed_ids"))
    keep = [x for x in old if not re.fullmatch(r"(?:[^:]+:)?\d+", x) and not x.startswith(pref)]
    ids = [S.session, f"webchat:FriendMessage:webchat!{S.owner}!desk-pet"] + keep + groups
    set_path(lconf, "filtering_settings.memory_scope_mode", "global")
    set_path(lconf, "filtering_settings.isolated_sessions", "\n".join(dict.fromkeys(iso + groups)))
    set_path(lconf, "access_control.whitelist_enabled", True)
    set_path(lconf, "access_control.allowed_ids", "\n".join(dict.fromkeys(ids)))
    S.scope_groups = len(groups)
    return was


def scope_note(S, was):
    say(f"  记忆范围：你的 QQ 私聊（和电脑桌宠）共用一份，{getattr(S, 'scope_groups', 0)} 个群各记各的，没登记的群不记")
    if was != "global" and "astrbot_plugin_livingmemory" in S.pre_installed:
        # 以前（legacy）私聊的记忆存在私聊自己的作用域里，改成 global 以后召回不到，要搬一次。群的不用搬（群照旧各记各的）
        say("  以前在私聊里记住的事还在旧位置：用 astr_setup.py memory-migrate 预览、memory-migrate apply 搬过来（只搬私聊）")
        say("MIGRATE private-memories")


def block_pet(S):
    """电脑桌宠（DeepSeek Harness 的 dsh-pet）也是她：装桌宠互通插件，把记忆范围改成私聊和桌宠共用一份。
    桥、DSH 那边的设置由安装 Skill 的 setup-pet.ps1 管，这里不碰。"""
    api = S.api
    lm = "astrbot_plugin_livingmemory"
    step("桌宠互通插件")
    S.refresh_installed()
    install_extras(S, lambda meta, name: meta.get("opt_in") == "pet")
    names = [p["name"] for p in S.pack["plugins"] if p["name"] == "astrbot_plugin_dafeiyu_petlink"]
    apply_plugin_configs(S, names)
    reload_changed(S, skip=(lm,))
    ok_plugin = "astrbot_plugin_dafeiyu_petlink" in S.installed
    step("记忆范围")
    r = api.call("GET", f"/plugins/config?plugin_id={lm}") if lm in S.installed else {}
    lconf = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
    scope = "none"
    if isinstance(lconf, dict):
        was = memory_scope(S, lconf)
        if api.ok(api.call("PUT", "/plugins/config", {"plugin_id": lm, "config": lconf})):
            api.call("POST", "/plugins/reload", {"plugin_id": lm}, timeout=180)
            scope_note(S, was)
            scope = "ok"
        else:
            warn("记忆范围没存上")
            scope = "fail"
    else:
        say("  没装记忆插件：桌宠照样是她，只是两边都不记长期的事（短期互通照常）")
    S.results.append(("桌宠互通插件", ok_plugin))
    if not ok_plugin:
        return "fail", "plugin", "重跑：--only pet"
    if scope == "fail":
        return "partial", "memory-scope", "重跑：--only pet，或 astr_setup.py memory-scope"
    return "ok", "" if scope == "ok" else "no-memory", ""


def existing_sf_key(S):
    """后台里已经有一把硅基流动的 key（上一期用 bat 装的记忆、或者他自己在后台填的）：返回 (key, 它在哪个提供商里)，没有返回 ("", "")。
    配置包自己那个提供商排在最前面。只在本进程里用，不打印。"""
    try:
        cfg = S.config()
    except Exception:  # noqa: BLE001
        return "", ""
    own = S.pack["memory"]["siliconflow"]["id"]
    srcs = {s.get("id"): s for s in cfg.get("provider_sources") or [] if isinstance(s, dict)}
    provs = sorted((p for p in cfg.get("provider") or [] if isinstance(p, dict)), key=lambda p: p.get("id") != own)
    for p in provs:
        src = srcs.get(p.get("provider_source_id")) or {}
        base = str(p.get("embedding_api_base") or p.get("api_base") or src.get("api_base") or "")
        if "siliconflow" not in base:
            continue
        for v in (p.get("embedding_api_key"), p.get("api_key"), p.get("key"), src.get("key")):
            v = v[0] if isinstance(v, list) and v else v
            if isinstance(v, str) and v.strip():
                return v.strip(), str(p.get("id") or "")
    return "", ""


def block_voice(S):
    api, v = S.api, S.pack["voice"]
    step("语音")
    S.refresh_installed()
    voice, tts_id, keep_tts, conf = S.voice, "", False, None
    if voice == "3" and v["doubao"]["plugin"] not in S.installed:
        # 豆包要配置包里的语音插件；单独跑语音这块时 plugins 块可能没装它
        install_extras(S, lambda meta, name: meta.get("only_with_voice") == "3" and not meta.get("opt_in"))
    if voice == "1":
        e = v["edge"]
        for pkg in e["pip"]:
            say(f"  安装 {pkg.split('==')[0]}…")
            r = api.call("POST", "/pip/install", {"package": pkg}, timeout=600)
            if not api.ok(r):
                warn(f"装不上：{api.msg(r)}")
        conf = {"id": e["id"], "type": "edge_tts", "provider": "microsoft", "provider_type": "text_to_speech",
                "enable": True, "voice": e["voice"], "rate": "+0%", "volume": "+0%", "pitch": "+0Hz",
                "timeout": 20, "proxy": ""}
        tts_id = e["id"] if upsert_provider(api, conf) else ""
    elif voice == "2":
        mm = v["mimo"]
        ref = os.path.join(PACK, mm["reference_audio"])
        if not os.path.exists(ref):
            found = [f for f in sorted(glob.glob(os.path.join(PACK, "voice", "*"))) if f.lower().endswith((".mp3", ".wav", ".m4a"))]
            ref = found[0] if found else ref
        if not os.path.exists(ref):
            ref = ask("reference_audio", "配置包里没有参考音，给一段你想克隆的声音文件路径（mp3/wav，5–10 秒）").strip('"')
        ext = os.path.splitext(ref)[1].lower().lstrip(".") or "mp3"
        mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}.get(ext, "audio/mpeg")
        data_uri = f"data:{mime};base64," + base64.b64encode(open(ref, "rb").read()).decode()
        conf = {"id": mm["id"], "type": "mimo_tts_api", "provider": "mimo", "provider_type": "text_to_speech",
                "enable": True, "api_key": S.mimo_key, "api_base": mm["api_base"], "model": mm["model"],
                "mimo-tts-voice": data_uri, "mimo-tts-format": "wav", "mimo-tts-style-prompt": "",
                "mimo-tts-dialect": "", "mimo-tts-seed-text": "", "timeout": 30, "proxy": ""}
        tts_id = mm["id"] if upsert_provider(api, conf) else ""
    elif voice == "3":
        d = v["doubao"]
        if S.keep:
            # 声音描述默认是给大肥鱼写的；他自己的角色要先按角色包.md 第 3 节改 settings/pack.json 的 voice.doubao.persona
            say(f"  声音描述用的是：「{d['persona'][:40]}…」。还是大肥鱼那句（憨萌软糯的小女生）的话，先按他的角色改 settings/pack.json 的 voice.doubao.persona 再跑")
        if d["plugin"] not in S.installed:
            warn(f"豆包语音要的插件 {d['plugin']} 没装上，先跳过语音。")
        else:
            conf = {"id": d["id"], "type": "seed_audio_tts", "provider": "volcengine", "provider_type": "text_to_speech",
                    "enable": True, "app_id": S.volc_app_id, "access_key": S.volc_token, "credentials_file": "",
                    "api_base": d["api_base"], "resource_id": d["resource_id"], "model": d["model"],
                    "seed_audio_persona": d["persona"], "seed_audio_prompt_template": d["prompt_template"],
                    "seed_audio_format": d["format"], "seed_audio_sample_rate": d["sample_rate"],
                    "seed_audio_max_attempts": d["max_attempts"],
                    "seed_audio_whole_reply": d.get("whole_reply", True),
                    "seed_audio_reply_probability": v["trigger_probability"], "timeout": d["timeout"]}
            say("  试念一句「你好呀」，豆包要十几秒…")
            tts_id = d["id"] if upsert_provider(api, conf) else ""
    elif voice == "4":
        bz = v["breeze"]
        if S.keep:
            say(f"  Breeze 用的是叫 {bz['voice']} 的声音：要是按他的角色捏的或者用角色录音克隆的才对（语音-Breeze.md 第 2 步），别直接用大肥鱼那段描述")
        try:
            with urllib.request.urlopen(bz["api_base"] + "/voices", timeout=10) as r:
                have = [x.get("id") for x in json.loads(r.read() or b"[]")]
        except Exception:
            have = None
        if have is None and provider_config(api, bz["id"]):
            # 以前配好过，只是现在 Breeze 没开（比如刚开机还没起来）：别把语音关掉，保持原样
            keep_tts = True
            warn("连不上本机的 Breeze，语音设置保持原样。双击桌面的「启动Breeze语音」，等它开好再运行一次本工具核一下。")
        elif have is None:
            warn("连不上本机的 Breeze。先双击桌面的「启动Breeze语音」，没装的话先双击「装Breeze语音.bat」。")
        elif bz["voice"] not in have:
            warn(f"Breeze 里还没有叫 {bz['voice']} 的声音，先双击「做声音.bat」做一个。")
        else:
            conf = {"id": bz["id"], "type": "openai_tts_api", "provider": "openai", "provider_type": "text_to_speech",
                    "enable": True, "api_key": "unused", "api_base": bz["api_base"], "model": "tts-1",
                    "openai-tts-voice": bz["voice"], "timeout": 180, "proxy": ""}
            say("  试念一句，Breeze 要几秒…")
            tts_id = bz["id"] if upsert_provider(api, conf) else ""
    if voice != "3":
        # 以前选过豆包又换了别的：豆包插件只要看到它的提供商开着就会掷骰子发整条语音，得关掉
        old = provider_config(api, v["doubao"]["id"])
        if old and old.get("enable"):
            upsert_provider(api, {**old, "enable": False})
            say("  之前开着的豆包语音已关掉")
    good = False
    if tts_id:
        good, why = test_provider(api, tts_id)
        if good:
            say("  语音已打开，大约三条回复里有一条会发语音")
            if voice == "3":
                say("  豆包是整条回复合成一条语音，要等 15–35 秒才到")
        else:
            # 测不通就先关着：开着的话她每发一次语音都会报错
            warn(f"语音测试没通过：{why}")
            if voice == "1" and re.search(r"WinError 2|找不到指定的文件|ffmpeg", why, re.I):
                warn("Edge 语音要用 ffmpeg 转格式，这台电脑上多半没装。用 AI 帮手装的：让它跑 Skill 里的 install-ffmpeg.ps1；"
                     "自己装的：在 PowerShell 里运行 winget install Gyan.FFmpeg，重启 AstrBot。装好再运行一次本工具（--only voice）。")
            conf["enable"] = False
            upsert_provider(api, conf)
            tts_id = ""
            say("  语音先关着，处理好再运行一次本工具（只弄语音：--only voice）")
    # AstrBot 自己的「语音输出」开关在全局设置里，只在这一块改
    cfg = S.config()
    tts = cfg.setdefault("provider_tts_settings", {})
    if tts_id and voice == "3" and v["doubao"].get("whole_reply", True):
        # 整条语音由豆包插件掷骰子、一次合成；AstrBot 自己的按段语音要关掉，不然会再按段转一遍
        tts.update({"enable": False, "provider_id": tts_id, "trigger_probability": v["trigger_probability"]})
    elif tts_id:
        tts.update({"enable": True, "provider_id": tts_id, "trigger_probability": v["trigger_probability"]})
    elif not keep_tts:
        tts["enable"] = False
    S.save_config(cfg)
    reload_changed(S)
    if keep_tts:
        S.results.append(("语音（Breeze 没开，没改）", True))
        return "ok", "breeze-off-kept", ""
    if voice == "0":
        say("  先不开")
        return "skip", "not-chosen", ""
    S.results.append(("语音", good))
    return ("ok", "", "") if good else ("fail", "test", "处理好再跑：--only voice")


def ensure_plugin(S, name):
    """表情包、记忆这两块单独跑时插件还没装（只拿几样的人可能没跑 plugins 块）：从插件市场装上这一个，按配置包设好。"""
    p = next((x for x in S.pack["plugins"] if x["name"] == name), None)
    if not p:
        return False
    say(f"  {name}：还没装，先从插件市场装上（{p['what']}）")
    r = S.api.call("POST", "/plugins/install/github", {"install_method": "market", "market_plugin_id": p["market_id"]}, timeout=600)
    if not S.api.ok(r):
        warn(f"装不上：{S.api.msg(r)}")
    if name not in S.refresh_installed():
        return False
    apply_plugin_configs(S, [name])
    reload_changed(S, skip=("astrbot_plugin_livingmemory",))
    return True


def meme_default_ids(api, ext):
    """表情包插件现在默认用哪几套。"""
    r = api.call("GET", f"{ext}/settings/rules")
    rules = [x for x in (meme_payload(r, "rules") or []) if isinstance(x, dict)]
    cur = next((x for x in reversed(rules) if x.get("scope") == "default"), {})
    return list(cur.get("pack_ids") or ([cur["pack_id"]] if cur.get("pack_id") else []))


# ---------- 改之前存一份快照，改坏了一条命令退回 ----------
SNAP_KEEP = 20  # 带时间的快照留最近这么多份；original.json（第一次动之前的样子）一直留着


def snapshot_dir():
    """快照放回答文件旁边的 private/snapshots（里面有 key，跟别的秘密放一起）；双击 bat 没有回答文件，就放配置包旁边。"""
    base = os.path.dirname(os.path.abspath(ANSWERS_PATH)) if ANSWERS_PATH else os.path.dirname(PACK)
    return os.path.join(base, "private", "snapshots")


def our_plugin_names(pack):
    """配置包会装、会改设置的插件：插件市场那几个，加 plugins/ 里自带的。"""
    names = {p["name"] for p in pack["plugins"]}
    for z in extra_plugins():
        base = z[:-4] if z.endswith(".zip") else z
        meta = json.load(open(base + ".json", encoding="utf-8-sig")) if os.path.exists(base + ".json") else {}
        names.add(meta.get("name") or plugin_name(z) or os.path.basename(base))
    return names


def plugin_conf(api, name):
    r = api.call("GET", f"/plugins/config?plugin_id={urllib.parse.quote(name)}")
    conf = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
    return conf if isinstance(conf, dict) else None


def take_snapshot(api, pack, label):
    """把这次可能改到的东西存一份：全局设置（默认人格、分段回复、记几轮对话、白名单……）、人格列表、模型提供商、
    配置包相关插件的设置和开关、表情包规则、工具权限。返回文件路径；后台读不到设置就返回空。"""
    r = api.call("GET", "/config-profiles/default")
    cfg = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
    r = api.call("GET", "/plugins")
    if not isinstance(cfg, dict) or not api.ok(r):
        return ""
    plugins = {p["name"]: bool(p.get("activated", True)) for p in (r.get("data") or []) if isinstance(p, dict) and p.get("name")}
    confs = {}
    for n in sorted(our_plugin_names(pack) & set(plugins)):
        c = plugin_conf(api, n)
        if c is not None:
            confs[n] = c
    r = api.call("GET", "/personas")
    personas = r.get("data") if api.ok(r) and isinstance(r.get("data"), list) else None
    rules = None
    if "meme_manager" in plugins:
        r = api.call("GET", "/plugins/extensions/meme_manager/settings/rules")
        rules = meme_payload(r, "rules") if api.ok(r) else None
    perms = {}
    r = api.call("GET", "/tools")
    for t in (r.get("data") if api.ok(r) and isinstance(r.get("data"), list) else []):
        if isinstance(t, dict) and t.get("name") in pack.get("tool_permissions", {}):
            perms[t["name"]] = t.get("permission") if t.get("permission_configured") else ""
    snap = {"version": 1, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "label": label, "base_url": api.root,
            "config": cfg, "personas": personas, "plugins": plugins, "plugin_configs": confs,
            "meme_rules": rules, "tool_permissions": perms}
    d = snapshot_dir()
    path = os.path.join(d, f"before-{label}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    orig = os.path.join(d, "original.json")
    made = []
    try:
        os.makedirs(d, exist_ok=True)
        made.append(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        if not os.path.exists(orig):
            made.append(orig)
            shutil.copyfile(path, orig)
    except OSError as e:
        # 盘满了、没权限：这次写了半截的快照不能留（退回时会读坏的），删掉当没存
        warn(f"快照写不进 {d}（{type(e).__name__}）")
        for f in made:
            try:
                os.remove(f)
            except OSError:
                pass
        return ""
    for old in sorted(glob.glob(os.path.join(d, "before-*.json")), key=os.path.getmtime)[:-SNAP_KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def restore_snapshot(api, pack, path):
    """退回到一份快照。他后来自己加的模型、插件、人格不碰；配置包后来建的提供商、后来装的配置包插件只关不删。"""
    snap = json.load(open(path, encoding="utf-8-sig"))
    step(f"退回到 {snap.get('time')} 存的那份设置（{os.path.basename(path)}）")
    bad = []
    q = lambda s: urllib.parse.quote(str(s), safe="")  # noqa: E731
    # 1 全局设置。后台登录那一节（dashboard）用现在的：他后来改过后台密码，别给退回去。模型、平台列表这个接口改不了，下面单独退
    r = api.call("GET", "/config-profiles/default")
    cur = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
    if not isinstance(cur, dict):
        say("RESULT restore=fail reason=config next=后台读不到设置：看 AstrBot 开没开、backend.txt 对不对")
        return False
    new = dict(snap["config"])
    if "dashboard" in cur:
        new["dashboard"] = cur["dashboard"]
    good = api.ok(api.call("PUT", "/config-profiles/default", new))
    say(f"  全局设置（默认人格、分段回复、记几轮对话、白名单这些）：{'退回了' if good else '没退成'}")
    if not good:
        bad.append("config")
    # 2 模型提供商：改过的写回原样（比如对话模型的深度思考、看图）；配置包后来建的（记忆、语音）关掉，不删
    ours = {pack["memory"]["ollama"]["id"], pack["memory"]["siliconflow"]["id"]} | \
        {v["id"] for v in pack["voice"].values() if isinstance(v, dict) and v.get("id")}
    old = {p.get("id"): p for p in snap["config"].get("provider") or [] if isinstance(p, dict)}
    for p in cur.get("provider") or []:
        pid = p.get("id") if isinstance(p, dict) else None
        if pid in old and p != old[pid]:
            good = api.ok(api.call("PUT", f"/providers/{q(pid)}", {"config": old[pid]}))
            say(f"  模型「{pid}」：{'退回了' if good else '没退成'}")
        elif pid and pid not in old and pid in ours and p.get("enable", True):
            good = api.ok(api.call("PUT", f"/providers/{q(pid)}", {"config": {**p, "enable": False}}))
            say(f"  「{pid}」是后来建的：{'关掉了（没删）' if good else '没关成'}")
        else:
            continue
        if not good:
            bad.append(pid)
    # 3 人格：内容被改过的写回去；后来新建的（比如大肥鱼）留在列表里，不是默认人格了就不碍事
    if isinstance(snap.get("personas"), list):
        r = api.call("GET", "/personas")
        now = {x.get("persona_id"): x for x in (r.get("data") or []) if isinstance(x, dict)} if api.ok(r) else {}
        fields = ("system_prompt", "begin_dialogs", "tools", "skills", "custom_error_message")
        for x in snap["personas"]:
            pid = x.get("persona_id") if isinstance(x, dict) else None
            if not pid:
                continue
            body = {k: x[k] for k in fields if k in x}
            if pid not in now:
                good = api.ok(api.call("POST", "/personas", {"persona_id": pid, **body}))
                say(f"  人格「{pid}」不见了，补回来：{'好了' if good else '没成'}")
            elif any(now[pid].get(k) != v for k, v in body.items()):
                good = api.ok(api.call("PUT", f"/personas/{q(pid)}", body))
                say(f"  人格「{pid}」的内容：{'退回了' if good else '没退成'}")
            else:
                continue
            if not good:
                bad.append("persona:" + pid)
        later = [p for p in now if p not in {x.get("persona_id") for x in snap["personas"] if isinstance(x, dict)}]
        if later:
            say(f"  后来新建的人格留着没删：{'、'.join(later)}（已经不是默认人格；想删在后台「人格设定」里删）")
    # 4 配置包的插件：设置写回去、开关恢复；快照里没有的（后来装的）关掉，不卸载
    r = api.call("GET", "/plugins")
    now = {p["name"]: bool(p.get("activated", True)) for p in (r.get("data") or []) if isinstance(p, dict) and p.get("name")} if api.ok(r) else {}
    was = snap.get("plugins") or {}
    changed = []
    for name, conf in (snap.get("plugin_configs") or {}).items():
        if name in now and plugin_conf(api, name) != conf:
            good = api.ok(api.call("PUT", "/plugins/config", {"plugin_id": name, "config": conf}))
            say(f"  插件 {name} 的设置：{'退回了' if good else '没退成'}")
            changed += [name] if good else []
            bad += [] if good else [name]
    for name in sorted(our_plugin_names(pack) & set(now)):
        want = bool(was.get(name, False))
        if now[name] != want:
            good = api.ok(api.call("PATCH", "/plugins/enabled", {"plugin_id": name, "enabled": want}))
            what = "打开" if want else ("关掉（后来装的，没卸载）" if name not in was else "关掉")
            say(f"  插件 {name}：{what}{'' if good else '，没成'}")
            bad += [] if good else [name]
    # 5 表情包规则
    if isinstance(snap.get("meme_rules"), list) and "meme_manager" in now:
        ext = "/plugins/extensions/meme_manager"
        if meme_payload(api.call("GET", f"{ext}/settings/rules"), "rules") != snap["meme_rules"]:
            good = api.ok(api.call("POST", f"{ext}/settings/rules", {"rules": snap["meme_rules"]}))
            say(f"  表情包规则：{'退回了' if good else '没退成'}")
            bad += [] if good else ["meme_rules"]
    # 6 工具权限（原来没设过的就是默认的「谁都能用」）
    r = api.call("GET", "/tools")
    tools = {t.get("name"): t for t in (r.get("data") or []) if isinstance(t, dict)} if api.ok(r) and isinstance(r.get("data"), list) else {}
    for tool, perm in (snap.get("tool_permissions") or {}).items():
        want = perm or "member"
        if tool in tools and tools[tool].get("permission") != want:
            good = api.ok(api.call("PATCH", f"/tools/{q(tool)}/permission", {"permission": want}))
            say(f"  工具 {tool} 的权限：{'退回了' if good else '没退成'}")
            bad += [] if good else [tool]
    for name in changed:
        if was.get(name, now.get(name)):
            api.call("POST", "/plugins/reload", {"plugin_id": name}, timeout=180)
    if bad:
        say(f"RESULT restore=partial reason={','.join(bad)} next=没退成的几样在后台手动改，或者过一会儿再跑一次 --restore")
        return False
    say("\n  退回好了。去 QQ 上先发 /reset 清掉旧对话，再聊一句，看是不是原来那样")
    say("RESULT restore=ok")
    return True


def run_block(S, name, fn):
    say(f"\n━━━━ {BLOCKS[name]} ━━━━")
    try:
        status, reason, nxt = fn(S)
    except Exception as e:  # 一块出错不挡别的块，也不让整个工具崩掉
        status, reason, nxt = "fail", f"error:{type(e).__name__}", f"重跑：--only {name}"
        warn(f"这一块出错了（{type(e).__name__}：{str(e)[:160]}），先跳过，别的照常装")
    # 这一行给 Agent 读：照它转述，不用自己去翻报错
    say(f"RESULT {name}={status}" + (f" reason={reason}" if reason else "") + (f" next={nxt}" if nxt else ""))
    return status


def main():
    global ANSWERS, ANSWERS_PATH
    if "--answers" in sys.argv:
        ANSWERS_PATH = sys.argv[sys.argv.index("--answers") + 1]
        ANSWERS = json.load(open(ANSWERS_PATH, encoding="utf-8-sig"))
    only = None
    if "--only" in sys.argv:
        only = [b.strip() for b in sys.argv[sys.argv.index("--only") + 1].split(",") if b.strip()]
        bad = [b for b in only if b not in BLOCKS]
        if bad:
            sys.exit(f"--only 只认这几块：{'、'.join(BLOCKS)}（逗号隔开）。不认识：{'、'.join(bad)}")
    # 不点名就全装（看视频、唱歌照旧要回答文件里写 "1"）；点了名的只装那几块，别的块一律不碰
    wanted = only or ([b for b in BLOCKS if b not in ("video", "pet")] + [b for b in ("video", "pet") if str(ANSWERS.get(b, "0")) == "1"])
    if os.name == "nt":
        # 下表情包、等插件装好可能要十几分钟：这段时间别让电脑自己睡着（进程退出就恢复）
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        except Exception:
            pass
    pack = json.load(open(os.path.join(PACK, "settings", "pack.json"), encoding="utf-8-sig"))
    say("大肥鱼配置包导入工具")
    say("开始前确认：AstrBot 已经在运行，QQ 小号已经接上（NapCat 或 SnowLuma），DeepSeek 已经能正常回话。")
    if only:
        say("这次只装：" + "、".join(BLOCKS[b] for b in only))

    # ---------- 连接后台 ----------
    api = Api(pick_backend())
    user = ask("username", "后台账号", "astrbot")
    password = ask("password", "后台密码（输入时不显示）", secret=True)
    if not api.login(user, password):
        say("RESULT core=fail reason=login next=核对后台地址和 private\\backend.txt 里的账号密码")
        sys.exit("登录失败：检查后台地址、账号、密码。")
    say("  已登录后台")

    if "--snapshot" in sys.argv:
        path = take_snapshot(api, pack, "manual")
        say(f"  存好了：{path}\nSNAPSHOT {path}" if path else "RESULT snapshot=fail reason=snapshot next=后台读不到设置（看 AstrBot 开没开），或工作目录那个盘满了、写不进去")
        return
    if "--restore" in sys.argv:
        i = sys.argv.index("--restore")
        f = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--") else os.path.join(snapshot_dir(), "original.json")
        if not os.path.isfile(f):
            say(f"RESULT restore=fail reason=no-snapshot next=没找到快照 {f}")
            return
        restore_snapshot(api, pack, f)
        return

    owner = ask("owner_qq", "你自己的 QQ 号（大号，她会把它当主人）")
    if not owner.isdigit():
        sys.exit("QQ 号只能是数字")
    # 只问这次要装的块用得上的问题；没点名的块从回答文件里原样取，取不到就当没选，绝不去改它
    memory, sf_key = str(ANSWERS.get("memory", "")), ""
    if "memory" in wanted or ("video" in wanted and ANSWERS.get("siliconflow_key_file")):
        if "memory" in wanted:
            say("\n记忆二选一：1 本机 Ollama（免费，要先装 Ollama）  2 硅基流动（要一个硅基流动的 key）  0 先不装")
            # 回答文件里没写 memory 就不动记忆（以前这里默认 2，会给没给 key 的人建一个空 key 的硅基流动记忆）
            memory = ask("memory", "选", "" if ANSWERS else "2", choices=["0", "1", "2"])
        if memory == "2" or ANSWERS.get("siliconflow_key_file") or ANSWERS.get("siliconflow_key"):
            try:
                # 回答文件模式下 key 文件空着或不存在都当成没有 key，由记忆块明确报出来，不让整个工具退出
                sf_key = ask("siliconflow_key", "硅基流动 key（输入时不显示）", "" if ANSWERS else None, secret=True)
            except OSError:
                warn("硅基流动 key 文件打不开（路径不对或还没建），当成没有 key")
                sf_key = ""
    voice, mimo_key, volc_app_id, volc_token = str(ANSWERS.get("voice", "")), "", "", ""
    if "voice" in wanted:
        say("\n语音选一个：3 豆包 Seed Audio（推荐，要火山引擎豆包语音的 APP ID 和 Access Token，按语音时长约 1 元/分钟）"
            "  4 本机 Breeze（免费，显卡在本机算，先用「装Breeze语音.bat」「做声音.bat」装好、做好声音）"
            "  1 微软 Edge 免费女声  2 小米 MiMo 克隆音（要 MiMo 的 key）  0 先不开")
        voice = ask("voice", "选", "3", choices=["0", "1", "2", "3", "4"])
        mimo_key = ask("mimo_key", "MiMo key（输入时不显示）", secret=True) if voice == "2" else ""
        if voice == "3":
            volc_app_id = ask("volc_app_id", "豆包语音 APP ID")
            volc_token = ask("volc_access_token", "豆包语音 Access Token（不是火山方舟的 API Key；输入时不显示）", secret=True)

    S = Run(api, pack, owner, memory, sf_key, voice, mimo_key, volc_app_id, volc_token)
    cfg = S.config()
    platforms = [p for p in cfg.get("platform", []) if p.get("type") == "aiocqhttp"]
    platforms.sort(key=lambda p: not p.get("enable", True))
    if not platforms:
        say("RESULT core=fail reason=no-bot next=先把 QQ 小号接上 AstrBot（第 4、5 步）")
        sys.exit("后台里还没有 QQ 机器人（OneBot v11），先接好 QQ 小号再来。")
    S.platform_id = platforms[0]["id"]
    S.chat_id = get_path(cfg, "agent_runner.config.model.provider_id") or ""
    if not S.chat_id:
        chats = [p for p in cfg.get("provider", []) if p.get("provider_type", "chat_completion") == "chat_completion" and p.get("enable", True)]
        S.chat_id = chats[0]["id"] if chats else ""
    if not S.chat_id:
        say("RESULT core=fail reason=no-model next=先接好 DeepSeek（第 3 步）")
        sys.exit("后台里还没有对话模型，先接好 DeepSeek 再来。")
    S.session = f"{S.platform_id}:FriendMessage:{owner}"
    S.ctx = {"主人会话": S.session, "对话模型": S.chat_id, "唱歌后端": (pack.get("sing") or {}).get("backend") or "doubao",
             # 看视频：ffmpeg、本机语音识别的位置留空插件会自己找（识别找 ffmpeg 旁边的 asr 文件夹）；
             # 没装本机识别、记忆又选了硅基流动，就顺手用那把 key 听视频里的声音
             "ffmpeg": str(ANSWERS.get("ffmpeg_path", "")), "asr_dir": str(ANSWERS.get("asr_dir", "")), "硅基流动key": sf_key}
    say(f"  机器人：{S.platform_id}　对话模型：{S.chat_id}")
    S.wanted = wanted
    S.pre_installed = set(S.refresh_installed())
    # 改之前先存快照：他自己配的机器人改坏了，一条命令退回
    snap = take_snapshot(api, pack, "+".join(b for b in ("core", "plugins", "video", "memory", "voice", "memes", "pet") if b in wanted))
    if snap:
        say(f"  改之前先存了一份设置快照：{snap}")
        say("  想退回：import_dafeiyu.py --answers <回答文件> --restore（退回到第一次动之前）；只退这一次就在 --restore 后面写上面这个文件")
        say(f"SNAPSHOT {snap}")
    else:
        # 没有快照就不改：改坏了退不回去
        say("RESULT snapshot=fail reason=snapshot next=快照没存成（后台读不到设置，或工作目录那个盘满了、写不进去），先处理好再跑；这次什么都没改")
        sys.exit(1)
    if S.keep:
        say("  只拿几样：人设和全局对话设置不动，他原来就装着的插件，设置也不动")

    fns = {"core": block_core, "plugins": block_plugins, "memes": block_memes, "memory": block_memory,
           "voice": block_voice, "video": block_video, "pet": block_pet}
    try:
        # 固定顺序：先让她变成大肥鱼（几秒），再装要下载的东西；表情包放最后，它最大、最容易卡
        # 桌宠排在记忆后面：它要改的记忆范围得等记忆插件接好
        for name in ("core", "plugins", "video", "memory", "pet", "voice", "memes"):
            if name in wanted:
                run_block(S, name, fns[name])
    finally:
        say("\n===== 结果 =====")
        for name, ok in S.results:
            say(f"  {'✔' if ok else '✘'} {name}")
        if any(not ok for _, ok in S.results):
            say("\n打叉的按上面的提示处理后，再运行一次本工具就行（已经装好的会自动跳过；只补某一块用 --only，比如 --only memes）。")
        if "core" in wanted and not S.keep:
            say("\n去 QQ 上用大号给她发一句「在吗」试试。之前聊过的话，先发 /reset 清掉旧对话，新人格才会生效。")


def meme_payload(r, key):
    """meme_manager 的接口有时直接返回字段，有时包在 data 里。"""
    if key in r:
        return r[key]
    data = r.get("data")
    return data.get(key) if isinstance(data, dict) else None


def meme_pack_ids(api, ext):
    packs = meme_payload(api.call("GET", f"{ext}/packs"), "packs") or []
    return {str(p.get("pack_id") or p.get("id")) for p in packs if isinstance(p, dict)}


def meme_accelerator(api, url=None):
    """不给 url：读表情包插件现在的 GitHub 加速地址（读不到返回 None）。给 url：改成它，成功返回 url，失败返回 None。"""
    r = api.call("GET", "/plugins/config?plugin_id=meme_manager")
    conf = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
    if not isinstance(conf, dict):
        return None
    if url is None:
        return str(get_path(conf, "community.github_accelerator_url") or "")
    set_path(conf, "community.github_accelerator_url", url)
    return url if api.ok(api.call("PUT", "/plugins/config", {"plugin_id": "meme_manager", "config": conf})) else None


def meme_import_zip(api, ext, path, set_default):
    """把已经下好的表情包 zip 从本地导进表情包插件（不连 GitHub）。
    后台对单次请求有大小上限（10-28 实测 462 MB 一次传会被断开），所以照插件网页的做法 8 MB 一片地传；插件太旧没有分片接口才一次传。"""
    size = os.path.getsize(path)
    say(f"  从本地导入 {os.path.basename(path)}（{size // 1048576} MB）…")
    piece = 8 * 1024 * 1024
    total = max(1, -(-size // piece))
    r = api.call("POST", f"{ext}/packs/import/session", {"filename": os.path.basename(path), "total_chunks": total})
    sid = str(meme_payload(r, "session_id") or "")
    if not sid:
        if r.get("_http") in (404, 405):
            return meme_import_zip_once(api, ext, path, set_default)
        warn(f"本地导入没成：{api.msg(r)}")
        return False
    with open(path, "rb") as f:
        for i in range(total):
            boundary = uuid.uuid4().hex
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"chunk\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n").encode() + f.read(piece) + f"\r\n--{boundary}--\r\n".encode()
            r = api.call("POST", f"{ext}/packs/import/session/chunk/{sid}/{i}/{total}", raw_body=body,
                         content_type=f"multipart/form-data; boundary={boundary}", timeout=600)
            if not api.ok(r):
                warn(f"本地导入没成（第 {i + 1}/{total} 片）：{api.msg(r)}")
                return False
            if (i + 1) % 15 == 0 and i + 1 < total:
                say(f"  已传 {(i + 1) * 8}/{size // 1048576} MB")
    token = str(meme_payload(r, "import_token") or sid)
    r = api.call("POST", f"{ext}/packs/import/apply",
                 {"import_token": token, "overwrite": "false", "set_as_default": "true" if set_default else "false"}, timeout=600)
    if not api.ok(r):
        warn(f"本地导入没成：{api.msg(r)}")
    return api.ok(r)


def meme_import_zip_once(api, ext, path, set_default):
    """老版本表情包插件没有分片接口：整个 zip 一次传。"""
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("overwrite", "false"), ("set_as_default", "true" if set_default else "false")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{os.path.basename(path)}\"\r\n"
                  "Content-Type: application/zip\r\n\r\n").encode())
    body = b"".join(parts) + open(path, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    r = api.call("POST", f"{ext}/packs/import", raw_body=body, content_type=f"multipart/form-data; boundary={boundary}", timeout=600)
    if not api.ok(r):
        warn(f"本地导入没成：{api.msg(r)}")
    return api.ok(r)


def meme_import_dir(api, ext, src, set_default):
    """本机另一套装好的表情包文件夹（根目录有 manifest.json）打成 zip，走插件自己的导入接口；不用停 AstrBot，也不动新这套的设置。"""
    say(f"  {os.path.basename(src)}：本机另一套里有，直接拿过来（不重下）")
    tmp_dir = tempfile.mkdtemp(prefix="dafeiyu-meme-")
    tmp = os.path.join(tmp_dir, os.path.basename(src) + ".zip")  # 插件拿文件名当表情包 id 的备选，别带随机后缀
    try:
        # 图片本身已经压过，存储模式打包最快
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for root, _, files in os.walk(src):
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, src))
        return meme_import_zip(api, ext, tmp, set_default)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def meme_install_with_progress(api, ext, mp, stall_seconds=120):
    """用表情包插件自己的后台安装任务下一套表情包：每 10 秒报一次进度；下载阶段两分钟没进展就取消、换下一个 GitHub 加速地址。
    每套都从第一个加速地址重新排（以前两套共用一个下标，第一套试完，第二套只剩直连一次机会）。
    装成了，插件就留在那条能用的线路上；都没装成，把插件的加速地址改回试之前那个（以前会停在最后试的「直连 GitHub」，
    他之后自己去资源广场装包更难下）。"""
    cur = meme_accelerator(api)
    ok = False
    try:
        ok = _meme_install_try(api, ext, mp, stall_seconds, cur)
    finally:
        if not ok and cur is not None and meme_accelerator(api) != cur:
            back = meme_accelerator(api, cur)
            say(f"  加速线路改回原来的（{(cur.replace('https://', '').rstrip('/') or '直连 GitHub')}）" if back is not None else "  ！加速线路没改回去，后台插件设置里看一下")
    return ok


def _meme_install_try(api, ext, mp, stall_seconds, cur):
    accels = ([cur] if cur and cur not in GH_ACCELERATORS else []) + GH_ACCELERATORS
    i, waited_busy = 0, False
    while i < len(accels):
        acc = accels[i]
        label = acc.replace("https://", "").rstrip("/") or "直连 GitHub"
        meme_accelerator(api, acc)
        api.call("POST", f"{ext}/community/index/fetch", {}, timeout=60)
        r = api.call("POST", f"{ext}/community/install/start", {"pack_id": mp["id"], "set_as_default": bool(mp.get("default"))}, timeout=60)
        if r.get("_http") == 404 and "job_id" not in str(r):
            # 老版本插件没有后台任务接口：退回一次性安装（最多等 15 分钟，超时也不会崩）
            r = api.call("POST", f"{ext}/community/install", {"pack_id": mp["id"], "set_as_default": bool(mp.get("default"))}, timeout=900)
            if api.ok(r):
                return True
            say(f"  {mp['id']}：走 {label} 没下下来（{api.msg(r)[:80]}）")
            i += 1
            continue
        job = meme_payload(r, "job_id")
        if r.get("_http") == 409 and not waited_busy:
            # 后台还有一套在装（多半是上次没装完的）：等它结束再开这一套，最多 20 分钟
            waited_busy = True
            say("  后台还有一套表情包在装（可能是上次没装完的），等它装完…")
            for _ in range(120):
                time.sleep(10)
                st = api.call("GET", f"{ext}/community/install/status", timeout=30)
                if str(meme_payload(st, "status") or "idle") not in ("running", "cancelling"):
                    break
            if mp["id"] in meme_pack_ids(api, ext):
                return True
            continue
        if not job:
            say(f"  {mp['id']}：没开始下（{api.msg(r)[:80]}），换个线路")
            i += 1
            continue
        say(f"  {mp['id']}：开始下载（走 {label}）")
        last, last_change, last_say = None, time.time(), 0
        while True:
            time.sleep(10)
            st = api.call("GET", f"{ext}/community/install/status?job_id={urllib.parse.quote(str(job))}", timeout=30)
            status = str(meme_payload(st, "status") or "")
            phase = str(meme_payload(st, "phase") or "")
            got = int(meme_payload(st, "downloaded_bytes") or 0)
            total = meme_payload(st, "total_bytes")
            sig = (phase, got, meme_payload(st, "progress"))
            if sig != last:
                last, last_change = sig, time.time()
            if status == "succeeded":
                return True
            if status in ("failed", "cancelled") or st.get("_http") == 404:
                say(f"  {mp['id']}：走 {label} 没下下来（{str(meme_payload(st, 'message') or api.msg(st))[:80]}）")
                break
            if time.time() - last_say >= 10:
                last_say = time.time()
                what = meme_payload(st, "message") or phase
                if phase in ("", "queued", "connecting", "downloading"):
                    size = f"{got / 1048576:.0f}/{int(total) / 1048576:.0f} MB" if total else f"{got / 1048576:.0f} MB"
                    say(f"  {mp['id']}：已下 {size}（{what}，走 {label}）")
                else:
                    say(f"  {mp['id']}：{what}")
            limit = stall_seconds if phase in ("", "queued", "connecting", "downloading") else 600
            if time.time() - last_change > limit:
                say(f"  {mp['id']}：{limit // 60} 分钟没动静，取消，换个线路")
                api.call("POST", f"{ext}/community/install/cancel", {"job_id": job})
                for _ in range(12):
                    time.sleep(5)
                    s2 = api.call("GET", f"{ext}/community/install/status?job_id={urllib.parse.quote(str(job))}", timeout=30)
                    if str(meme_payload(s2, "status") or "") not in ("running", "cancelling"):
                        break
                break
        i += 1
    return False


def meme_use_together(api, ext, wanted):
    """把 meme_manager 的 default 选择规则设成 wanted 这几套一起用（已经是了就不动）。"""
    have = meme_pack_ids(api, ext)
    ids = [p for p in wanted if p in have]
    missing = [p for p in wanted if p not in have]
    if missing:
        warn(f"这几套表情包没装上，先不算进去：{'、'.join(missing)}")
    if not ids:
        return False
    r = api.call("GET", f"{ext}/settings/rules")
    rules = [x for x in (meme_payload(r, "rules") or []) if isinstance(x, dict)]
    if not api.ok(r):
        warn(f"读不到表情包规则：{api.msg(r)}")
        return False
    cur = next((x for x in reversed(rules) if x.get("scope") == "default"), {})
    cur_ids = cur.get("pack_ids") or ([cur["pack_id"]] if cur.get("pack_id") else [])
    if cur_ids == ids:
        say(f"  默认已经是这 {len(ids)} 套：{'、'.join(ids)}")
        return True
    keep = [x for x in rules if x.get("scope") != "default"]
    rule = {"id": cur.get("id") or "default", "scope": "default", "pack_id": ids[0], "pack_ids": ids}
    r = api.call("POST", f"{ext}/settings/rules", {"rules": keep + [rule]})
    if not api.ok(r):
        warn(f"表情包规则没改成：{api.msg(r)}")
        return False
    say(f"  默认改成用这 {len(ids)} 套：{'、'.join(ids)}")
    return True


def ollama_ready(model):
    def tags():
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
                return [m.get("name", "") for m in json.load(r).get("models", [])]
        except Exception:
            return None
    names = tags()
    if names is None:
        warn("连不上本机 Ollama（127.0.0.1:11434）。先装 Ollama 并打开它。")
        return False
    if any(n.split(":")[0] == model for n in names):
        say(f"  Ollama 里已经有 {model}")
        return True
    if ANSWERS:
        # Agent 用回答文件跑时不在这里拉 1.2 GB 的模型（没有进度、可能挂很久）：Skill 的 install-ollama.ps1 会从国内源下好
        warn(f"Ollama 里还没有 {model}：先跑 Skill 的 install-ollama.ps1 把它下好，再跑 --only memory。")
        return False
    exe = shutil.which("ollama") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    if not os.path.exists(exe) and not shutil.which("ollama"):
        warn("找不到 ollama 命令。")
        return False
    say(f"  正在下载向量模型 {model}（约 1.2 GB）…")
    subprocess.call([exe, "pull", model])
    names = tags() or []
    return any(n.split(":")[0] == model for n in names)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        main()
    except KeyboardInterrupt:
        say("\n已取消")
