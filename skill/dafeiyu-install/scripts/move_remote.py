# -*- coding: utf-8 -*-
"""搬上云时在服务器上跑的那一半（move_to_cloud.py 经 ssh 从标准输入喂给服务器的 python3）。

服务器的系统 Python 可能只有 3.6（Alibaba Cloud Linux 3），这里只用标准库和老语法。
结果打一行 DFY_JSON {...}。不打印任何 key、密码、QQ 号。

用法（参数都是一段 base64 的 JSON，秘密不走这里）：
  python3 - fix <json>        改数据里绑定那台电脑的设置，只碰 cmd_config.json 和 config 文件夹
  python3 - napcat <json>     把 NapCat 连大脑用的 token 填好
  python3 - counts <json>     数人设、插件、记忆、表情包，给搬完对账用
  python3 - health <json>     登后台看插件和对话模型通不通

counts 和 fix 的函数也被 move_to_cloud.py 直接 import，在那台电脑上数同一份。
"""
import base64
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

try:
    import urllib.request as urlreq
    import urllib.error as urlerr
except ImportError:  # noqa
    urlreq = urlerr = None

LOCAL_URL = re.compile(r"(?i)\b(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]|host\.docker\.internal)\b")
WIN_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\]|file:/+[A-Za-z]:[\\/])")
SKIP_DIRS = ("__pycache__",)
LM = "astrbot_plugin_livingmemory"


def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8-sig") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return default


def write_json(p, obj):
    tmp = p + ".move.tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def out(obj):
    print("DFY_JSON " + json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


# ---------------------------------------------------------------- 改设置

def clear_paths(obj, where, hits, local_dir=""):
    """把设置里 Windows 的绝对路径清空（ffmpeg、识别模型、日志文件这些，服务器上都不存在）。人设那种长文字不碰。"""
    if isinstance(obj, dict):
        for k in list(obj):
            v = obj[k]
            if isinstance(v, str):
                if is_win_path(v, local_dir):
                    obj[k] = ""
                    hits.append(where + "." + str(k))
            else:
                clear_paths(v, where + "." + str(k), hits, local_dir)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                if is_win_path(v, local_dir):
                    obj[i] = ""
                    hits.append("%s[%d]" % (where, i))
            else:
                clear_paths(v, "%s[%d]" % (where, i), hits, local_dir)


def is_win_path(v, local_dir=""):
    if not v or len(v) > 400 or "\n" in v:
        return False
    if WIN_PATH.match(v):
        return True
    return bool(local_dir) and local_dir.lower() in v.lower().replace("/", "\\")


def base_of(p):
    return str(p.get("api_base") or p.get("base_url") or p.get("embedding_api_base") or p.get("host") or "")


def fix_cmd_config(c, token="", local_dir=""):
    """改一份 cmd_config（字典，就地改）。返回报告。不碰人设、模型、白名单、聊天设置。"""
    rep = {"disabled": [], "cleared": [], "proxy": False, "platform": 0}
    d = c.setdefault("dashboard", {})
    d["host"] = "0.0.0.0"
    d["port"] = 6185
    for p in c.get("platform") or []:
        if isinstance(p, dict) and p.get("type") == "aiocqhttp":
            p["ws_reverse_host"] = "0.0.0.0"
            p["ws_reverse_port"] = 6199
            if token:
                p["ws_reverse_token"] = token
            rep["platform"] += 1
    # 那台电脑上的代理到了服务器就是死地址
    if LOCAL_URL.search(str(c.get("http_proxy") or "")):
        c["http_proxy"] = ""
        rep["proxy"] = True
    # 连着那台电脑自己的服务（本机语音、Ollama）的提供商先关掉，文件留着，导入语音和记忆时再接新的
    local_src = set()
    for s in c.get("provider_sources") or []:
        if isinstance(s, dict) and LOCAL_URL.search(base_of(s)):
            local_src.add(s.get("id"))
    for p in c.get("provider") or []:
        if not isinstance(p, dict):
            continue
        if LOCAL_URL.search(str(p.get("proxy") or "")):
            p["proxy"] = ""
            rep["proxy"] = True
        if LOCAL_URL.search(base_of(p)) or p.get("provider_source_id") in local_src:
            if p.get("enable", True):
                p["enable"] = False
                rep["disabled"].append(str(p.get("id")))
    hits = []
    clear_paths(c, "cmd_config", hits, local_dir)
    rep["cleared"] = hits
    return rep


def cmd_fix(a):
    data = os.path.join(a["dir"], "data")
    cfg = os.path.join(data, "cmd_config.json")
    c = read_json(cfg)
    if not isinstance(c, dict):
        return out({"ok": False, "why": "no-config", "msg": "服务器上没有 data/cmd_config.json，数据没传全"})
    rep = fix_cmd_config(c, a.get("token", ""), a.get("local_dir", ""))
    write_json(cfg, c)
    cleared = rep["cleared"]
    # config 文件夹：插件设置和其他配置档，只清路径
    cd = os.path.join(data, "config")
    names = [os.path.join(cd, n) for n in sorted(os.listdir(cd))] if os.path.isdir(cd) else []
    names += [os.path.join(data, n) for n in ("mcp_server.json",)]
    for f in names:
        if not f.endswith(".json") or not os.path.isfile(f):
            continue
        o = read_json(f)
        if o is None:
            continue
        hits = []
        clear_paths(o, os.path.basename(f), hits, a.get("local_dir", ""))
        if hits:
            write_json(f, o)
            cleared += hits
    rep["cleared"] = cleared
    rep["ok"] = True
    rep["pw_ok"] = password_matches(c, a["dir"])
    out(rep)


def password_matches(c, d):
    """backend.txt 的密码和后台存的对不对得上（后台存的是 md5）。对不上时后面的导入都登不进去。"""
    stored = str((c.get("dashboard") or {}).get("password") or "")
    kv = read_kv(os.path.join(d, "private", "backend.txt"))
    pw = kv.get("password", "")
    if not stored or not pw:
        return None
    return hashlib.md5(pw.encode("utf-8")).hexdigest() == stored or pw == stored


def read_kv(p):
    try:
        with open(p, encoding="utf-8-sig") as fp:
            return {m.group(1).lower(): m.group(2).strip() for m in re.finditer(r"(?m)^\s*([^\s:：]+)\s*[:：]\s*(.*)$", fp.read())}
    except (OSError, UnicodeError):
        return {}


def cmd_napcat(a):
    """MODE=astrbot 第一次起来只写一份空 token 的 onebot11.json，填好后 NapCat 扫码时会照着它复制。"""
    d = a["dir"]
    try:
        with open(os.path.join(d, "private", "onebot-token.txt"), encoding="utf-8-sig") as fp:
            tok = fp.read().strip()
    except OSError:
        return out({"ok": False, "why": "no-token"})
    cd = os.path.join(d, "napcat", "config")
    t0 = time.time()
    while time.time() - t0 < float(a.get("wait", 90)):
        files = [os.path.join(cd, n) for n in os.listdir(cd) if re.match(r"onebot11.*\.json$", n)] if os.path.isdir(cd) else []
        if files:
            break
        time.sleep(3)
    else:
        return out({"ok": False, "why": "no-config", "msg": "NapCat 还没写出 onebot11.json"})
    n = 0
    for f in files:
        o = read_json(f)
        if not isinstance(o, dict):
            continue
        for w in ((o.get("network") or {}).get("websocketClients") or []):
            w["token"] = tok
            n += 1
        with open(f, "w", encoding="utf-8") as fp:  # 原地写，文件主人还是 NapCat 那个用户
            json.dump(o, fp, ensure_ascii=False, indent=2)
    out({"ok": n > 0, "patched": n})


# ---------------------------------------------------------------- 对账

def count_files(p):
    n = size = 0
    for r, ds, fs in os.walk(p):
        ds[:] = [x for x in ds if x not in SKIP_DIRS]
        for f in fs:
            try:
                size += os.path.getsize(os.path.join(r, f))
                n += 1
            except OSError:
                pass
    return n, size


def sql_count(db, table):
    """数一张表。系统 sqlite 太老读不了时（alinux 3 的 3.26 读不了记忆库）返回 None，由调用方借容器数。"""
    if not os.path.exists(db):
        return 0
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=3)
        try:
            return int(c.execute('select count(*) from "%s"' % table).fetchone()[0])
        finally:
            c.close()
    except sqlite3.Error:
        return None


def counts(data):
    """一份数据（data 文件夹）里有多少东西。两边各数一次，对得上才算搬全。"""
    pd = os.path.join(data, "plugins")
    plugins = sorted(n for n in os.listdir(pd) if os.path.isdir(os.path.join(pd, n)) and n not in SKIP_DIRS) if os.path.isdir(pd) else []
    db = os.path.join(data, "data_v4.db")
    memes, memes_size = count_files(os.path.join(data, "plugin_data", "meme_manager"))
    c = read_json(os.path.join(data, "cmd_config.json"), {}) or {}
    return {
        "plugins": plugins,
        "personas": sql_count(db, "personas"),
        "conversations": sql_count(db, "conversations"),
        "messages": sql_count(db, "platform_message_history"),
        "memories": sql_count(os.path.join(data, "plugin_data", LM, "livingmemory.db"), "documents"),
        "memes": memes,
        "memes_size": memes_size,
        "providers": len(c.get("provider") or []),
        "whitelist": len((c.get("platform_settings") or {}).get("id_whitelist") or []),
    }


def cmd_prune(a):
    """这台电脑上已经没有的 SQLite 临时文件（-wal、-shm、-journal），服务器上也删掉。
    留着的话，旧 -wal 会被 SQLite 回放到新传的库上，把库弄坏。只在容器没开的时候调。"""
    root = os.path.join(a["dir"], "data")
    keep = set(a.get("keep") or [])
    removed = []
    for r, _ds, fs in os.walk(root):
        for f in fs:
            if f.endswith(("-wal", "-shm", "-journal")):
                full = os.path.join(r, f)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                if rel not in keep:
                    os.remove(full)
                    removed.append(rel)
    out({"ok": True, "removed": removed})


def cmd_counts(a):
    r = counts(os.path.join(a["dir"], "data"))
    if r["memories"] is None:
        # 借大脑容器里的 Python 数（容器的 sqlite 够新）
        try:
            code = ("import sqlite3;c=sqlite3.connect('file:/AstrBot/data/plugin_data/%s/livingmemory.db?mode=ro',uri=True);"
                    "print(c.execute('select count(*) from documents').fetchone()[0])" % LM)
            o = subprocess.check_output(["docker", "exec", "astrbot", "python", "-c", code], stderr=subprocess.STDOUT, timeout=30)
            r["memories"] = int(o.decode().strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    r["ok"] = True
    out(r)


# ---------------------------------------------------------------- 起来以后看一眼

def api(base, method, path, body=None, token="", timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urlreq.Request(base + "/api/v1" + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    opener = urlreq.build_opener(urlreq.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urlerr.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            return {"status": "error", "http": e.code}
    except (OSError, ValueError) as e:
        return {"status": "error", "message": type(e).__name__}


def cmd_health(a):
    base = "http://127.0.0.1:6185"
    t0 = time.time()
    while time.time() - t0 < float(a.get("wait", 240)):
        try:
            with urlreq.build_opener(urlreq.ProxyHandler({})).open(base + "/", timeout=5) as p:
                if p.status < 500:
                    break
        except urlerr.HTTPError as e:
            if e.code < 500:
                break
        except (OSError, ValueError):
            pass
        time.sleep(5)
    else:
        return out({"ok": False, "why": "no-brain", "msg": "大脑 %d 秒还没起来" % int(a.get("wait", 240))})
    kv = read_kv(os.path.join(a["dir"], "private", "backend.txt"))
    tok = ""
    for p in (kv.get("password", ""), hashlib.md5(kv.get("password", "").encode("utf-8")).hexdigest()):
        r = api(base, "POST", "/auth/login", {"username": kv.get("username", ""), "password": p}, timeout=20)
        if isinstance(r.get("data"), dict) and r["data"].get("token"):
            tok = r["data"]["token"]
            break
    if not tok:
        return out({"ok": False, "why": "login", "msg": "后台登不进去：账号密码和 backend.txt 对不上"})
    res = {"ok": True, "login": True}
    r = api(base, "GET", "/plugins", token=tok)
    plist = r.get("data") if isinstance(r.get("data"), list) else []
    res["plugins"] = sorted(str(p.get("name")) for p in plist if isinstance(p, dict))
    res["plugins_off"] = sorted(str(p.get("name")) for p in plist if isinstance(p, dict) and p.get("activated") is False)
    r = api(base, "GET", "/config-profiles/default", token=tok)
    cfg = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
    res["persona"] = ""
    if isinstance(cfg, dict):
        res["persona"] = str(((cfg.get("agent_runner") or {}).get("config") or {}).get("persona", {}).get("persona_id") or (cfg.get("provider_settings") or {}).get("default_personality") or "")
        chat = ((cfg.get("agent_runner") or {}).get("config") or {}).get("model", {}).get("provider_id") or (cfg.get("provider_settings") or {}).get("default_provider_id")
        if chat:
            r = api(base, "POST", "/providers/test", {"provider_id": chat}, tok, timeout=90)
            st = (r.get("data") or {}).get("status") if isinstance(r.get("data"), dict) else r.get("status")
            res["chat_ok"] = str(st) not in ("error", "unavailable", "failed")
    out(res)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    a = json.loads(base64.b64decode(sys.argv[2]).decode("utf-8")) if len(sys.argv) > 2 else {}
    if cmd == "fix":
        return cmd_fix(a)
    if cmd == "napcat":
        return cmd_napcat(a)
    if cmd == "counts":
        return cmd_counts(a)
    if cmd == "prune":
        return cmd_prune(a)
    if cmd == "health":
        return cmd_health(a)
    out({"ok": False, "why": "unknown"})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "why": "crash", "msg": "%s: %s" % (type(e).__name__, str(e)[:200])})
