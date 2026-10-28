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
每块结束打一行 RESULT <块>=ok|partial|fail|skip reason=… next=…，给 Agent 照着转述。

用法：双击上一层的「导入大肥鱼.bat」，按提示回答几个问题（全装）。
Agent 用：python import_dafeiyu.py --answers 回答.json [--only core,plugins]
  --only 只装点名的块，别的块一律不碰（不会因为没填就把已经开着的记忆、语音关掉）。
  回答文件里的 *_file 字段指向放密码或 key 的文本文件；meme_zip_dir 指向已经下好的 <表情包id>.zip 所在文件夹（有就从本地导入，不再从 GitHub 下）；
  meme_copy_from 指向本机另一套的 astrbot/data/plugin_data/meme_manager 文件夹（另装一套时，那套装过的表情包直接拿来，不重下）。
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
    return lines[0].strip()


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
            set_path(conf, k, fill(v, S.ctx))
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
    for p in pack["plugins"]:
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
    market_names = [p["name"] for p in pack["plugins"]]
    for name in market_names:
        S.results.append((f"插件 {name}", name in S.installed))

    install_extras(S, lambda meta, name: extra_wanted(S, meta, name))

    step("改插件设置")
    apply_plugin_configs(S)
    for tool, perm in pack["tool_permissions"].items():
        r = api.call("PATCH", f"/tools/{urllib.parse.quote(tool, safe='')}/permission", {"permission": perm})
        if not api.ok(r):
            warn(f"工具 {tool} 权限没改成：{api.msg(r)}")
    say("  发空间、改签名只听主人的：已设置")
    reload_changed(S, skip=("astrbot_plugin_livingmemory",))
    missing = [n for n in market_names if n not in S.installed]
    if missing:
        return "fail", "market:" + ",".join(missing), "过几分钟重跑：--only plugins"
    return "ok", "", ""


def block_video(S):
    step("看视频插件")
    S.refresh_installed()
    install_extras(S, lambda meta, name: meta.get("opt_in") == "video")
    name = next((p["name"] for p in S.pack["plugins"] if p["name"].endswith("_video")), "astrbot_plugin_dafeiyu_video")
    apply_plugin_configs(S, [name])
    reload_changed(S)
    if name in S.installed:
        return "ok", "", ""
    return "fail", "upload", "重跑：--only video"


def block_memes(S):
    api, pack = S.api, S.pack
    step("表情包（在后台下载，每 10 秒报一次进度；哪套下不动就先跳过，不影响聊天）")
    if "meme_manager" not in S.refresh_installed():
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
    if S.memory == "0":
        say("  先不装。以后想装，再运行一次本工具选 1 或 2（只装记忆：--only memory）。")
        return "skip", "not-chosen", ""
    if lm not in S.installed:
        S.results.append(("记忆", False))
        return "skip", "no-plugin", "先装插件：--only plugins"
    m = pack["memory"]
    conf, reason = None, ""
    if S.memory == "1":
        o = m["ollama"]
        if ollama_ready(o["ollama_model"]):
            conf = {"id": o["id"], "embedding_api_key": o["embedding_api_key"],
                    "embedding_api_base": o["embedding_api_base"], "embedding_model": o["embedding_model"]}
        else:
            warn("Ollama 没在运行或向量模型没下好，记忆先关着。装好以后再运行一次本工具（只装记忆：--only memory）。")
            reason = "ollama"
    else:
        s = m["siliconflow"]
        conf = {"id": s["id"], "embedding_api_key": S.sf_key, "embedding_api_base": s["embedding_api_base"],
                "embedding_model": s["embedding_model"]}
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
            r = api.call("GET", f"/plugins/config?plugin_id={lm}")
            lconf = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
            if isinstance(lconf, dict):
                set_path(lconf, "provider_settings.embedding_provider_id", conf["id"])
                mem_ok = api.ok(api.call("PUT", "/plugins/config", {"plugin_id": lm, "config": lconf})) and good
    api.call("PATCH", "/plugins/enabled", {"plugin_id": lm, "enabled": mem_ok})
    if mem_ok:
        api.call("POST", "/plugins/reload", {"plugin_id": lm}, timeout=180)
    say("  记忆已打开" if mem_ok else "  记忆插件先关着（没配好记忆模型时开着会报错）")
    S.results.append(("记忆", mem_ok))
    return ("ok", "", "") if mem_ok else ("fail", reason or "config", "处理好再跑：--only memory")


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
    global ANSWERS
    if "--answers" in sys.argv:
        ANSWERS = json.load(open(sys.argv[sys.argv.index("--answers") + 1], encoding="utf-8-sig"))
    only = None
    if "--only" in sys.argv:
        only = [b.strip() for b in sys.argv[sys.argv.index("--only") + 1].split(",") if b.strip()]
        bad = [b for b in only if b not in BLOCKS]
        if bad:
            sys.exit(f"--only 只认这几块：{'、'.join(BLOCKS)}（逗号隔开）。不认识：{'、'.join(bad)}")
    # 不点名就全装（看视频、唱歌照旧要回答文件里写 "1"）；点了名的只装那几块，别的块一律不碰
    wanted = only or ([b for b in BLOCKS if b != "video"] + (["video"] if str(ANSWERS.get("video", "0")) == "1" else []))
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

    owner = ask("owner_qq", "你自己的 QQ 号（大号，她会把它当主人）")
    if not owner.isdigit():
        sys.exit("QQ 号只能是数字")
    # 只问这次要装的块用得上的问题；没点名的块从回答文件里原样取，取不到就当没选，绝不去改它
    memory, sf_key = str(ANSWERS.get("memory", "")), ""
    if "memory" in wanted or ("video" in wanted and ANSWERS.get("siliconflow_key_file")):
        if "memory" in wanted:
            say("\n记忆二选一：1 本机 Ollama（免费，要先装 Ollama）  2 硅基流动（要一个硅基流动的 key）  0 先不装")
            memory = ask("memory", "选", "2", choices=["0", "1", "2"])
        if memory == "2" or ANSWERS.get("siliconflow_key_file"):
            sf_key = ask("siliconflow_key", "硅基流动 key（输入时不显示）", secret=True)
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

    fns = {"core": block_core, "plugins": block_plugins, "memes": block_memes, "memory": block_memory,
           "voice": block_voice, "video": block_video}
    try:
        # 固定顺序：先让她变成大肥鱼（几秒），再装要下载的东西；表情包放最后，它最大、最容易卡
        for name in ("core", "plugins", "video", "memory", "voice", "memes"):
            if name in wanted:
                run_block(S, name, fns[name])
    finally:
        say("\n===== 结果 =====")
        for name, ok in S.results:
            say(f"  {'✔' if ok else '✘'} {name}")
        if any(not ok for _, ok in S.results):
            say("\n打叉的按上面的提示处理后，再运行一次本工具就行（已经装好的会自动跳过；只补某一块用 --only，比如 --only memes）。")
        if "core" in wanted:
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
    每套都从第一个加速地址重新排（以前两套共用一个下标，第一套试完，第二套只剩直连一次机会）。"""
    cur = meme_accelerator(api)
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
