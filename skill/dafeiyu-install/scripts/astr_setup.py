"""AstrBot 后台设置（第 2、3、5 步）。用 AstrBot 自带的 Python 跑：
  <工作目录>\\uv\\tools\\astrbot\\Scripts\\python.exe -X utf8 astr_setup.py --dir C:\\dafeiyu <子命令>

子命令：
  dashboard-local [--port 6185]  后台只听本机（改 data\\cmd_config.json，不用登录）
  account                        用初始账号 astrbot 和 private\\astrbot-initial-password.txt 登进去，改成 backend.txt 里的账号密码（Docker 版第一次开会随机生成初始密码，云端用）
  deepseek                       核 key 余额，建 DeepSeek 提供商和 deepseek-flash 模型，设成默认和看图模型，关掉「操作电脑」
  bot [--ws-host 0.0.0.0]        建 aiocqhttp 平台「大肥鱼」（反向 WS 127.0.0.1:6199，token 用 private\\napcat-ws-token.txt；云端 Docker 里要 0.0.0.0，NapCat 容器才连得进来）
  whitelist                      白名单只放大号（private\\owner-qq.txt），有 group.txt 再加群；大号设成管理员。
                                 记忆已经是「私聊和桌宠共用一份」（global）时，群同时加进记忆的强制隔离和记忆白名单
  pet-key [remove]               建桌宠用的 API key「大肥鱼桌宠」（范围 chat + chat:admin）写进 private\\pet-key.txt，
                                 再生成桌宠调桥的口令 private\\pet-bridge-token.txt；已有且能用就不重建。remove：撤掉 key、删这两个文件
  memory-scope                   记忆改成：主人的 QQ 私聊和桌宠共用一份（global），每个群各记各的（强制隔离），
                                 没登记的群不记（记忆白名单）。改完重载记忆插件
  memory-migrate [apply]         把主人 QQ 私聊以前记住的事搬进共用的那份（只搬私聊，群的不动）。不写 apply 只预览；apply 前插件会自动备份数据库
  group-reply [mention|quote|both|off]  群里回复时 @ 对方 / 引用对方那句 / 两样都要 / 都不要；不写就只打印现在的
  status                         打印现在的设置摘要和五项功能现状（登 QQ 的程序、语音、记忆、角色、看视频，再加表情包；不含秘密）
  logs [N] [关键词]              看后台最近 N 条日志（QQ 号和秘密打码）
  meme-show                      列出装了哪几套表情包、默认规则用哪几套
  meme-install <作者/仓库>       从 GitHub 装一套表情包（ref 用 main；仓库写的版本标签常常不存在）
  meme-use <pack_id> [...]       默认规则只用这几套（换成观众自己的角色时用）
  greet [一句话]                 让她主动给大号发一句招呼（不写就用大肥鱼口气的三句里随机一句；他换了自己的角色，你按角色写一句传进来）
                                 平台不写就自动挑 QQ 那个；--dry 只说会发什么、不真发；--platform <不存在的名字> 只验证接口通不通
任何输出都不含 key、密码、token、QQ 号。
"""
import argparse, hashlib, json, os, random, re, sys, urllib.error, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True)
ap.add_argument("cmd")
ap.add_argument("rest", nargs="*")
ap.add_argument("--port", type=int, default=6185)
ap.add_argument("--ws-host", default="127.0.0.1")
ap.add_argument("--dry", action="store_true", help="greet 只说会发什么、从哪个平台发，不真发")
ap.add_argument("--platform", default="", help="不写就自动挑 QQ 平台：有「大肥鱼」用它，没有用第一个开着的 aiocqhttp 平台")
A = ap.parse_args()
DIR = A.dir.rstrip("\\/")
PRIV = os.path.join(DIR, "private")
ROOT_DATA = os.path.join(DIR, "astrbot", "data")
SECRETS = set()


def read(name, required=True):
    p = os.path.join(PRIV, name)
    if not os.path.exists(p):
        if required:
            sys.exit(f"缺少 private\\{name}")
        return None
    v = open(p, encoding="utf-8-sig").read().strip()
    if v:
        SECRETS.add(v)
    return v


def kv(name):
    d = {}
    p = os.path.join(PRIV, name)
    for line in open(p, encoding="utf-8-sig"):
        k, sep, v = line.replace("：", ":", 1).partition(":")
        if sep and v.strip():
            d[k.strip().lower()] = v.strip()
            if k.strip().lower() == "password":
                SECRETS.add(v.strip())
    return d


def mask(s):
    s = str(s)
    for x in sorted(SECRETS, key=len, reverse=True):
        if x and len(x) >= 5:
            s = s.replace(x, "***")
    return re.sub(r"(?<!\d)\d{6,11}(?!\d)", lambda m: m.group(0)[:2] + "****", s)


if A.cmd == "dashboard-local":
    f = os.path.join(ROOT_DATA, "cmd_config.json")
    c = json.load(open(f, encoding="utf-8-sig"))
    d = c.setdefault("dashboard", {})
    changed = (d.get("host"), d.get("port")) != ("127.0.0.1", A.port)
    d["host"], d["port"] = "127.0.0.1", A.port
    json.dump(c, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("后台改成只听本机 127.0.0.1:%d" % A.port if changed else "后台本来就只听本机")
    sys.exit(0)

B = kv("backend.txt")
URL = (B.get("url") or "http://127.0.0.1:6185").split()[0].rstrip("/")


def call(method, path, body=None, token=None, timeout=120):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(URL + "/api/v1" + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text, code = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        text, code = e.read().decode("utf-8", "replace"), e.code
    except urllib.error.URLError as e:
        sys.exit(f"连不上后台 {URL}：{e.reason}（AstrBot 没开？先跑 install-astrbot.ps1）")
    try:
        out = json.loads(text) if text else {}
    except ValueError:
        out = {"raw": text[:300]}
    if not isinstance(out, dict):
        out = {"data": out}
    out["_http"] = code
    return out


def ok(r):
    return r.get("_http", 200) < 400 and r.get("status", "ok") != "error"


def try_login(user, pw):
    for p in (pw, hashlib.md5(pw.encode()).hexdigest()):
        r = call("POST", "/auth/login", {"username": user, "password": p})
        if isinstance(r.get("data"), dict) and r["data"].get("token"):
            return r["data"]["token"]
    return None


def login():
    t = try_login(B["username"], B["password"])
    if t:
        return t
    sys.exit("后台登录失败：private\\backend.txt 里的账号密码和后台对不上（观众在网页上改过密码的话，让他把新密码写回这个文件）")


if A.cmd == "account":
    if try_login(B["username"], B["password"]):
        print("后台账号密码已经是 backend.txt 里那套了")
        sys.exit(0)
    tmp = read("astrbot-initial-password.txt")
    t = try_login("astrbot", tmp)
    if not t:
        sys.exit("初始账号 astrbot 登不上：初始密码抄错了，或者账号已经被改过（让观众把现在的账号密码写进 private\\backend.txt）")
    r = call("PATCH", "/auth/account", {"password": tmp, "new_username": B["username"], "new_password": B["password"],
                                        "confirm_password": B["password"]}, t)
    if not ok(r) or not try_login(B["username"], B["password"]):
        sys.exit("改账号密码没成功：" + mask(json.dumps(r, ensure_ascii=False))[:300])
    print(f"后台账号改成 {B['username']}，密码用 backend.txt 里那个（不显示）")
    sys.exit(0)

T = login()


def get_cfg():
    r = call("GET", "/config-profiles/default", None, T)
    return r["data"]["config"]


def put_cfg(cfg):
    r = call("PUT", "/config-profiles/default", cfg, T)
    if not ok(r):
        sys.exit("保存配置失败：" + mask(json.dumps(r, ensure_ascii=False))[:300])


# ---------- 桌宠和记忆范围用的几个小工具 ----------
PET_KEY_NAME = "大肥鱼桌宠"
PET_SESSION = "desk-pet"   # 桥固定用这个会话名；AstrBot 里是 webchat:FriendMessage:webchat!<主人号>!desk-pet
LM = "astrbot_plugin_livingmemory"


def qq_platform():
    """QQ 平台的名字：我们建的叫「大肥鱼」；他自己配的机器人平台名是他起的，用它的。"""
    bots = call("GET", "/bots", None, T).get("data") or []
    qq = [b.get("id") for b in (bots if isinstance(bots, list) else bots.get("bots", []) if isinstance(bots, dict) else []) if b.get("type") == "aiocqhttp" and b.get("id")]
    return "大肥鱼" if "大肥鱼" in qq or not qq else qq[0]


def file_groups():
    return [g for g in re.split(r"[\s,，、;；]+", read("group.txt", required=False) or "") if g.isdigit()]


def key_call(key, method, path, body=None, timeout=30):
    """用 API key（不是后台登录）调开放接口。返回 (HTTP 码, 文本)；连不上返回 (0, '')。"""
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(URL + "/api/v1" + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return 0, ""


def key_works(key, owner):
    code, text = key_call(key, "GET", "/chat/sessions?username=" + owner + "&page=1&page_size=1")
    return code == 200 and '"status": "error"' not in text and '"status":"error"' not in text


def chat_once(key, owner, session, message, timeout=120):
    """以主人的身份在 WebChat 的 session 里说一句，返回她（或插件命令）回的全部文字。"""
    data = json.dumps({"message": message, "username": owner, "session_id": session, "enable_streaming": False}, ensure_ascii=False).encode()
    req = urllib.request.Request(URL + "/api/v1/chat", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-Key", key)
    out = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            if ev.get("type") == "plain" and isinstance(ev.get("data"), str):
                out.append(ev["data"])
            elif ev.get("type") == "complete" and not out and isinstance(ev.get("data"), str):
                out.append(ev["data"])
            elif ev.get("type") == "error":
                raise RuntimeError(str(ev.get("data"))[:200])
            elif ev.get("type") == "end":
                break
    return "\n".join(x for x in out if x.strip())


def free_pet_sessions(owner, key):
    """桌宠的会话名在 AstrBot 里全局唯一，谁先用就归谁。被别的 username 先用了（10-29：Agent 自测时拿后台账号
    在 desk-pet 里说过话），桥再以主人身份说话就回「session_id belongs to another username」，桌宠只能回落到
    dsh-pet 自己的模型：口气对，记忆不通。占位的会话在这里删掉（会话和聊天记录一起删，长期记忆不动）。"""
    db = os.path.join(DIR, "astrbot", "data", "data_v4.db")
    if not os.path.exists(db):
        return
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rows = con.execute("SELECT session_id, creator FROM platform_sessions WHERE session_id IN (?, ?)",
                           (PET_SESSION, PET_SESSION + "-whisper")).fetchall()
        con.close()
    except sqlite3.Error as e:
        print(f"（没查成桌宠的会话归谁：{e}）")
        return
    for sid, creator in rows:
        creator = str(creator or "")
        if creator == owner:
            continue
        if creator == B["username"]:
            r = call("DELETE", f"/chat/sessions/{sid}", None, T)
        else:
            code, text = key_call(key, "DELETE", f"/chat/sessions/{sid}?username={urllib.request.quote(creator)}")
            # 出错时开放接口照样回 200，正文里是 "status": "error"（10-29 实测用 key 删主人名下的会话回 Permission denied）
            failed = code != 200 or '"error"' in text
            r = {"_http": code or 500, "status": "error" if failed else "ok", "message": text[:150]}
        print(f"桌宠的会话 {sid} 先被「{mask(creator)}」用掉了（自测时拿别的身份在里面说过话），"
              + ("删掉了，桌宠能接上她了" if ok(r) else f"没删成：{mask(str(r.get('message') or r))[:150]}；让他到后台「对话」里把 {sid} 删了，再跑一次 setup-pet"))


def pet_keys():
    r = call("GET", "/api-keys", None, T)
    if not ok(r):
        sys.exit("读不到后台的 API key 列表：" + mask(str(r.get("message") or r))[:200] + "（AstrBot 太旧？桌宠要 AstrBot 4.29 以上）")
    return [k for k in (r.get("data") or []) if isinstance(k, dict)]


def new_key(name, days=None):
    body = {"name": name, "scopes": ["chat", "chat:admin"]}
    if days:
        body["expires_in_days"] = days
    r = call("POST", "/api-keys", body, T)
    d = r.get("data") if isinstance(r.get("data"), dict) else {}
    if not ok(r) or not d.get("api_key"):
        sys.exit("建 key 没成功：" + mask(str(r.get("message") or r))[:200])
    SECRETS.add(d["api_key"])
    return d["api_key"], d.get("key_id")


def lm_config():
    r = call("GET", f"/plugins/config?plugin_id={LM}", None, T)
    d = r.get("data") if isinstance(r.get("data"), dict) else {}
    return d.get("config") if ok(r) and isinstance(d.get("config"), dict) else None


def split_ids(text):
    return [x.strip() for x in re.split(r"[\n,;，；]+", str(text or "")) if x.strip()]


def lm_scope(plat, owner, groups, force):
    """记忆范围三项：global + 群强制隔离 + 记忆白名单。force=False 时只在已经是 global 的情况下同步群（whitelist 用）。
    白名单里只放完整会话 ID：只写主人号的话，主人在没登记的群里说话也会被记，所以单独的号码（和 平台:号码）会被拿掉。"""
    lc = lm_config()
    if lc is None:
        return "记忆插件没装（或读不到它的设置），记忆范围没改" if force else ""  # whitelist 顺手同步时，没装记忆就不出声
    fs, ac = lc.setdefault("filtering_settings", {}), lc.setdefault("access_control", {})
    was = fs.get("memory_scope_mode") or "legacy"
    if not force and was != "global":
        return ""
    private = f"{plat}:FriendMessage:{owner}"
    pet = f"webchat:FriendMessage:webchat!{owner}!{PET_SESSION}"
    keep_iso = [x for x in split_ids(fs.get("isolated_sessions")) if ":GroupMessage:" not in x or not x.startswith(plat + ":")]
    iso = list(dict.fromkeys(keep_iso + groups))
    old_ids = split_ids(ac.get("allowed_ids"))
    dropped = [x for x in old_ids if re.fullmatch(r"(?:[^:]+:)?\d+", x)]
    keep_ids = [x for x in old_ids if x not in dropped and (":GroupMessage:" not in x or not x.startswith(plat + ":"))]
    ids = list(dict.fromkeys([private, pet] + keep_ids + groups))
    fs["memory_scope_mode"] = "global"
    fs["isolated_sessions"] = "\n".join(iso)
    ac["whitelist_enabled"] = True
    ac["allowed_ids"] = "\n".join(ids)
    r = call("PUT", "/plugins/config", {"plugin_id": LM, "config": lc}, T)
    if not ok(r):
        return "记忆范围没存上：" + mask(str(r.get("message") or r))[:200]
    call("POST", "/plugins/reload", {"plugin_id": LM}, T, 180)
    msg = f"记忆范围：主人的 QQ 私聊和桌宠共用一份，{len(groups)} 个群各记各的，没登记的群不记（记忆插件已重载）"
    if dropped:
        msg += f"；记忆白名单里拿掉了 {len(dropped)} 条单独的号码（只写号码会让没登记的群也被记）"
    if was != "global":
        msg += f"\n以前是「{was}」：私聊里以前记住的事还在旧位置，接着跑 memory-migrate 预览，再 memory-migrate apply 搬过来\nMIGRATE private-memories"
    return msg


if A.cmd == "deepseek":
    key = read("deepseek-key.txt")
    print(f"key：{len(key)} 位，{'是' if key.startswith('sk-') else '不是'} sk- 开头")
    try:
        req = urllib.request.Request("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + key})
        bal = json.loads(urllib.request.urlopen(req, timeout=30).read())
        total = sum(float(x.get("total_balance", 0)) for x in bal.get("balance_infos", []))
        print(f"DeepSeek 账户：可用={bal.get('is_available')}，余额>0={total > 0}")
        # 记下装机开始时的余额（只记第一次、只放金额），工作台拿它算「装机花了多少钱」
        try:
            f = os.path.join(DIR, "logs", "install-balance.json")
            if not os.path.exists(f):
                import datetime
                os.makedirs(os.path.dirname(f), exist_ok=True)
                json.dump({"start": f"{total:.2f}", "at": datetime.datetime.now().isoformat(timespec="seconds")}, open(f, "w", encoding="utf-8"))
        except OSError:
            pass
        if not bal.get("is_available"):
            sys.exit("余额不够，请观众去 DeepSeek 开放平台充值后再跑")
    except urllib.error.HTTPError as e:
        sys.exit("key 不对（HTTP %d），让观众重新建一把 key 粘进 private\\deepseek-key.txt" % e.code)
    except Exception as e:  # 网络问题不拦，后面的测试会再验
        print("余额没查成（%s），接着往下" % type(e).__name__)
    src = {"id": "deepseek", "provider": "deepseek", "type": "openai_chat_completion", "provider_type": "chat_completion",
           "enable": True, "key": [key], "api_base": "https://api.deepseek.com/v1", "timeout": 120, "proxy": "", "custom_headers": {}}
    have = call("GET", "/provider-sources", None, T).get("data") or []
    ids = [s.get("id") for s in (have if isinstance(have, list) else have.get("provider_sources", []) if isinstance(have, dict) else [])]
    r = call("PUT", "/provider-sources/deepseek", {"config": src}, T) if "deepseek" in ids else call("POST", "/provider-sources", {"config": src}, T)
    print("DeepSeek 提供商：", "好了" if ok(r) else "失败 " + mask(json.dumps(r, ensure_ascii=False))[:200])
    models = call("GET", "/provider-sources/deepseek/models", None, T).get("data")
    names = json.dumps(models, ensure_ascii=False)
    model = "deepseek-flash" if "deepseek-flash" in names or not models else None
    if not model:
        m = re.findall(r'"(deepseek-[a-z0-9.\-]*flash[a-z0-9.\-]*)"', names) or re.findall(r'"(deepseek-[a-z0-9.\-]+)"', names)
        model = m[0] if m else "deepseek-flash"
    pid = "deepseek/" + model
    r = call("GET", "/providers/by-id?provider_id=" + pid, None, T)
    if not (ok(r) and isinstance(r.get("data"), dict) and r["data"]):
        prov = {"id": pid, "enable": True, "provider_source_id": "deepseek", "model": model,
                "modalities": ["text", "image", "tool_use"], "custom_extra_body": {}, "max_context_tokens": 0}
        r = call("POST", "/provider-sources/deepseek/providers", {"config": prov}, T)
        print(f"模型 {model}：", "好了" if ok(r) else "失败 " + mask(json.dumps(r, ensure_ascii=False))[:200])
    r = call("POST", "/providers/test", {"provider_id": pid}, T, 120)
    print("模型测试：", "通过" if ok(r) else "没通过 " + mask(json.dumps(r, ensure_ascii=False))[:300])
    cfg = get_cfg()
    cfg.setdefault("agent_runner", {}).setdefault("config", {}).setdefault("model", {})["provider_id"] = pid
    ps = cfg.setdefault("provider_settings", {})
    ps["default_image_caption_provider_id"] = pid
    ps["computer_use_runtime"] = "none"
    put_cfg(cfg)
    print(f"默认模型、看图模型都设成 {pid}；「操作电脑」已关")
    print("下一步：接着跑 bot 子命令，建机器人平台「大肥鱼」")

elif A.cmd == "bot":
    ws = read("napcat-ws-token.txt")
    r = call("GET", "/bots", None, T)
    bots = r.get("data") or []
    lst = bots if isinstance(bots, list) else bots.get("bots", []) if isinstance(bots, dict) else []
    names = [b.get("id") for b in lst]
    plat = {"id": "大肥鱼", "type": "aiocqhttp", "enable": True, "ws_reverse_host": A.ws_host, "ws_reverse_port": 6199, "ws_reverse_token": ws}
    r = call("PUT", "/bots/by-id", {"bot_id": "大肥鱼", "config": plat}, T) if "大肥鱼" in names else call("POST", "/bots", {"config": plat}, T)
    print(f"机器人平台「大肥鱼」（等 NapCat 连 {A.ws_host}:6199）：", "好了" if ok(r) else "失败 " + mask(json.dumps(r, ensure_ascii=False))[:200])

elif A.cmd == "whitelist":
    owner = read("owner-qq.txt")
    group = read("group.txt", required=False)
    if not owner.isdigit():
        sys.exit("private\\owner-qq.txt 里应该只有一串数字（大号 QQ 号）")
    cfg = get_cfg()
    ps = cfg.setdefault("platform_settings", {})
    # group.txt 可以放好几个群号（换行、空格、逗号隔开都行）
    groups = [g for g in re.split(r"[\s,，、;；]+", group or "") if g.isdigit()]
    # 白名单的前缀是 QQ 平台的名字：我们建的叫「大肥鱼」；他自己配的机器人平台名是他起的，用它的，不然她不回他
    bots = call("GET", "/bots", None, T).get("data") or []
    qq = [b.get("id") for b in (bots if isinstance(bots, list) else bots.get("bots", []) if isinstance(bots, dict) else []) if b.get("type") == "aiocqhttp" and b.get("id")]
    plat = "大肥鱼" if "大肥鱼" in qq or not qq else qq[0]
    wl = [f"{plat}:FriendMessage:{owner}"] + [f"{plat}:GroupMessage:{g}" for g in groups]
    ps.update({"enable_id_white_list": True, "id_whitelist": wl, "wl_ignore_admin_on_group": False,
               "wl_ignore_admin_on_friend": False, "id_whitelist_log": True})
    admins = cfg.setdefault("admins_id", [])
    if owner not in admins:
        admins.append(owner)
    put_cfg(cfg)
    print(f"白名单 {len(wl)} 条（大号{f'、{len(groups)} 个群' if groups else ''}），大号已设成管理员")
    # 记忆已经是「私聊和桌宠共用一份」时，新群要同时进强制隔离（各记各的）和记忆白名单（不然这个群什么都不记）
    sync = lm_scope(plat, owner, [f"{plat}:GroupMessage:{g}" for g in groups], force=False)
    if sync:
        print(sync)

elif A.cmd == "group-reply":
    # AstrBot 自带的两个开关：@ 只在群里加（私聊不加），引用会带上她回的是哪一句
    modes = {"mention": (True, False), "quote": (False, True), "both": (True, True), "off": (False, False)}
    mode = (A.rest[0] if A.rest else "").lower()
    if mode and mode not in modes:
        sys.exit("group-reply 后面写 mention、quote、both 或 off")
    cfg = get_cfg()
    ps = cfg.setdefault("platform_settings", {})
    if mode:
        ps["reply_with_mention"], ps["reply_with_quote"] = modes[mode]
        put_cfg(cfg)
    print("群里回复时 @ 对方：", "开" if ps.get("reply_with_mention") else "关", "；引用对方那句：", "开" if ps.get("reply_with_quote") else "关")

elif A.cmd == "status":
    cfg = get_cfg()
    ps = cfg.get("platform_settings", {})
    bots = call("GET", "/bots", None, T).get("data") or []
    lst = bots if isinstance(bots, list) else bots.get("bots", []) if isinstance(bots, dict) else []
    print("默认模型：", cfg.get("agent_runner", {}).get("config", {}).get("model", {}).get("provider_id"))
    print("看图模型：", cfg.get("provider_settings", {}).get("default_image_caption_provider_id"))
    print("操作电脑：", cfg.get("provider_settings", {}).get("computer_use_runtime"))
    print("白名单：", ps.get("enable_id_white_list"), len(ps.get("id_whitelist") or []), "条；管理员", len(cfg.get("admins_id") or []), "个")
    print("平台：", [(b.get("id"), b.get("type"), b.get("enable")) for b in lst])
    # 五项功能现在什么样。补装、「就用它」时照这几行标「现在是」，不用再翻配置文件、数据库（10-28 桌面版 DSH 为这个自己写了 4 个脚本、多花 3 分钟）
    provs = {p.get("id"): p for p in cfg.get("provider") or [] if isinstance(p, dict)}
    def where(p):
        base = str(p.get("api_base") or p.get("embedding_api_base") or "")
        return "本机" if ("127.0.0.1" in base or "localhost" in base) else "在线"
    pf = os.path.join(PRIV, "protocol.txt")
    proto = open(pf, encoding="utf-8-sig").read().strip() if os.path.exists(pf) else ""
    if not proto:
        proto = "snowluma" if os.path.exists(os.path.join(DIR, "start-snowluma.cmd")) and not os.path.exists(os.path.join(DIR, "start-napcat.cmd")) else "napcat"
    print("1 登 QQ 的程序：", {"snowluma": "SnowLuma", "napcat": "NapCat"}.get(proto.lower(), proto))
    tts = cfg.get("provider_tts_settings") or {}
    tp = provs.get(tts.get("provider_id"))
    if tts.get("enable") and tp:
        print(f"2 语音：开着，「{tp.get('id')}」（{where(tp)}，{tp.get('type')}），{int(float(tts.get('trigger_probability', 1)) * 100)}% 的回复带语音")
    else:
        print("2 语音：没开")
    plugins = call("GET", "/plugins", None, T).get("data") or []
    plugins = {p.get("name"): p for p in plugins if isinstance(p, dict)}
    def on(p):
        return bool(p.get("activated", p.get("enabled", p.get("enable", True))))
    lm = plugins.get("astrbot_plugin_livingmemory")
    if not lm:
        print("3 记忆：没装记忆插件")
    else:
        # 插件开着不等于记得住：还要指向一个记忆模型、在线的有 key、本机的 Ollama 里真有这个模型，最后当场试一下。
        # 10-28 群友那台「插件配的是硅基流动、key 是空的」，以前这里照样说「开着」
        lc = (call("GET", "/plugins/config?plugin_id=astrbot_plugin_livingmemory", None, T).get("data") or {}).get("config") or {}
        ep = provs.get((lc.get("provider_settings") or {}).get("embedding_provider_id"))
        desc = f"「{ep.get('id')}」（{where(ep)}，{ep.get('embedding_model') or ep.get('model')}）" if ep else ""
        if not on(lm):
            print("3 记忆：插件关着（先不要记忆，或者上次没接好被关掉了）" + (f"，配的是{desc}" if desc else ""))
        elif not ep:
            print("3 记忆：插件开着，但没指向记忆模型，记不住事。补：answers.json 的 memory 填 1（本机）或 2（硅基流动），再 import-pack -Only memory")
        else:
            key = str(ep.get("embedding_api_key") or ep.get("api_key") or "").strip()
            if key:
                SECRETS.add(key)
            base = str(ep.get("embedding_api_base") or ep.get("api_base") or "")
            problem = ""
            if where(ep) == "在线" and not key:
                problem = "在线的记忆模型没填 key，记不住事。补：硅基流动 key 放 private\\sf-key.txt，answers.json 里 memory 填 2、siliconflow_key_file 指向它，再 import-pack -Only memory"
            elif ":11434" in base:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=4) as r:
                        names = [str(m.get("name", "")).split(":")[0] for m in json.loads(r.read().decode("utf-8")).get("models") or []]
                    want = str(ep.get("embedding_model") or "").split(":")[0]
                    if want and want not in names:
                        problem = f"本机 Ollama 里没有 {want}，记不住事。补：跑 install-ollama.ps1（从 ModelScope 下），再 import-pack -Only memory"
                except Exception:
                    problem = "本机 Ollama 没开，记不住事。开一下 Ollama（开始菜单里点 Ollama，或重启电脑让它自己起来）"
            if not problem:
                try:
                    t = call("POST", "/providers/test", {"provider_id": ep.get("id")}, T, timeout=30)
                except Exception as e:  # 试连超时不能让整个 status 断掉
                    t = {"status": "error", "message": f"试连超时或出错（{type(e).__name__}）"}
                td = t.get("data") if isinstance(t.get("data"), dict) else {}
                if ok(t) and str(td.get("status") or t.get("status") or "") not in ("error", "unavailable", "failed"):
                    print(f"3 记忆：开着，{desc}，刚试过能用")
                else:
                    print(f"3 记忆：开着，{desc}，但刚才试连没通：{mask(str(td.get('error') or td.get('message') or t.get('message') or ''))[:160]}")
            else:
                print(f"3 记忆：插件开着，{desc}，但{problem}")
        fs = lc.get("filtering_settings") or {}
        if (fs.get("memory_scope_mode") or "legacy") == "global":
            ng = len([x for x in split_ids(fs.get("isolated_sessions")) if ":GroupMessage:" in x])
            wl_on = (lc.get("access_control") or {}).get("whitelist_enabled")
            print(f"  记忆范围：私聊和桌宠共用一份，{ng} 个群各记各的" + ("，没登记的群不记" if wl_on else "，记忆白名单没开（没登记的群也会记进共用那份）"))
        else:
            print(f"  记忆范围：{fs.get('memory_scope_mode') or 'legacy'}（每个会话各记各的；要让桌宠和 QQ 私聊共用，跑 memory-scope）")
    pf2 = os.path.join(PRIV, "pet-key.txt")
    if os.path.exists(pf2):
        print("桌宠：配过（private 里有 pet-key.txt），桥在不在看工作台「桌宠」那一项")
    pid = cfg.get("agent_runner", {}).get("config", {}).get("persona", {}).get("persona_id") or (cfg.get("provider_settings") or {}).get("default_personality")
    # 第 1 步「先问一句」照这一行分：大肥鱼 = 我们装的；别的名字 = 他自己配的机器人；default = AstrBot 自带的默认人格
    kind = "大肥鱼" if pid == "大肥鱼" else "AstrBot 自带的默认人格" if pid in (None, "", "default") else "不是大肥鱼，是他自己配的人设"
    print("4 角色（默认人格）：", pid or "default", f"（{kind}）")
    vid = next((n for n in plugins if n and n.endswith("_video")), None)
    if not vid:
        print("5 看视频：没装")
    else:
        vc = (call("GET", f"/plugins/config?plugin_id={vid}", None, T).get("data") or {}).get("config") or {}
        # 本机识别要三个文件都在、模型是完整的（以前的安装脚本断了会在 asr 里留半截模型）；设置里没填时插件用工作目录的 asr
        asr = str(vc.get("asr_dir") or "") or os.path.join(DIR, "asr")
        mf = os.path.join(asr, "model.int8.onnx")
        full = all(os.path.isfile(os.path.join(asr, f)) for f in ("sherpa-onnx-offline.exe", "model.int8.onnx", "tokens.txt"))
        if full and os.path.getsize(mf) >= 200_000_000:
            ear = "本机增强版（多认方言、歌词）" if os.path.isdir(os.path.join(asr, "funasr-nano")) else "本机普通版"
        elif vc.get("asr_key"):
            ear = "硅基流动在线识别"
        elif full:
            ear = "本机模型只下了半截，听不到声音（重跑 install-asr.ps1，会接着下）"
        else:
            ear = "没装，只看画面（install-asr.ps1 装本机识别，或给硅基流动 key 走在线识别）"
        print(f"5 看视频：{'开着' if on(plugins[vid]) else '插件在、但关着'}，听声音={ear}")
    if "meme_manager" in plugins:
        r = call("GET", "/plugins/extensions/meme_manager/packs", None, T)
        packs = r.get("packs") or ((r.get("data") or {}).get("packs") if isinstance(r.get("data"), dict) else None) or []
        print("表情包：", "、".join(f"{p.get('name') or p.get('pack_id')}（{p.get('image_count', p.get('count', '?'))} 张）" for p in packs) or "插件在，一套都没有")
    else:
        print("表情包：没装表情包插件")

elif A.cmd == "logs":
    n = int(A.rest[0]) if A.rest else 40
    kw = A.rest[1] if len(A.rest) > 1 else ""
    r = call("GET", "/log-history", None, T)
    d = r.get("data")
    logs = d.get("logs") if isinstance(d, dict) else d
    out = [str(l.get("data") if isinstance(l, dict) else l) for l in (logs or [])]
    for s in [x for x in out if kw in x][-n:]:
        print(mask(s)[:400])
elif A.cmd.startswith("meme-"):
    EXT = "/plugins/extensions/meme_manager"

    def payload(r, key):
        return r[key] if key in r else ((r.get("data") or {}).get(key) if isinstance(r.get("data"), dict) else None)

    def rules():
        return [x for x in (payload(call("GET", f"{EXT}/settings/rules", None, T), "rules") or []) if isinstance(x, dict)]

    if A.cmd == "meme-install":
        if not A.rest or "/" not in A.rest[0]:
            sys.exit("用法：meme-install <作者/仓库>")
        src = {"type": "github", "repo": A.rest[0], "ref": "main", "subpath": "."}
        r = call("POST", f"{EXT}/community/install", {"source": src, "set_as_default": False}, T, 900)
        print("装上了" if ok(r) else "没装上：" + mask(str(r.get("message") or r))[:300])
    elif A.cmd == "meme-use":
        if not A.rest:
            sys.exit("用法：meme-use <pack_id> [<pack_id> ...]（pack_id 先用 meme-show 看）")
        rs = rules()
        cur = next((x for x in reversed(rs) if x.get("scope") == "default"), {})
        rule = {"id": cur.get("id") or "default", "scope": "default", "pack_id": A.rest[0], "pack_ids": A.rest}
        r = call("POST", f"{EXT}/settings/rules", {"rules": [x for x in rs if x.get("scope") != "default"] + [rule]}, T)
        print("默认规则改好了（已保存）" if ok(r) else "没改成：" + mask(str(r.get("message") or r))[:200])
    elif A.cmd != "meme-show":
        sys.exit("不认识的子命令：" + A.cmd)
    for p in payload(call("GET", f"{EXT}/packs", None, T), "packs") or []:
        print("表情包：", p.get("pack_id") or p.get("id"), p.get("name"), p.get("image_count", p.get("count", "")), "张")
    for x in rules():
        print("规则：", x.get("scope"), "用", x.get("pack_ids") or [x.get("pack_id")])
elif A.cmd == "pet-key":
    # 桌宠经「大肥鱼桥」以主人的身份跟她说话：key 要 chat + chat:admin（没有 chat:admin，开放接口不让用管理员的号说话）
    owner = read("owner-qq.txt")
    kf, tf = os.path.join(PRIV, "pet-key.txt"), os.path.join(PRIV, "pet-bridge-token.txt")
    mine = [k for k in pet_keys() if k.get("name") == PET_KEY_NAME]
    if A.rest and A.rest[0] == "remove":
        gone = sum(1 for k in mine if ok(call("DELETE", f"/api-keys/{k.get('key_id')}", None, T)))
        for f in (kf, tf):
            if os.path.exists(f):
                os.remove(f)
        print(f"桌宠的 key 撤掉了 {gone} 把，private 里的 pet-key.txt、pet-bridge-token.txt 删了")
        sys.exit(0)
    old = read("pet-key.txt", required=False)
    live = [k for k in mine if not k.get("is_revoked") and not k.get("is_expired")]
    same = old and any(old.startswith(str(k.get("key_prefix") or "~")) and "chat:admin" in (k.get("scopes") or []) for k in live)
    if same and key_works(old, owner):
        print("桌宠的 key 已经有了，能用，不重建")
    else:
        # 旧的同名 key（文件丢了、被撤了、范围不对）先删掉，免得后台攒一堆
        for k in mine:
            call("DELETE", f"/api-keys/{k.get('key_id')}", None, T)
        key, _ = new_key(PET_KEY_NAME)
        os.makedirs(PRIV, exist_ok=True)
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key)
        print("桌宠的 key 建好了（范围 chat + chat:admin），存在 private\\pet-key.txt" + ("，旧的那把删了" if mine else "")
              + ("；试了一下能用" if key_works(key, owner) else "；但试用没通过，看后台日志"))
    free_pet_sessions(owner, read("pet-key.txt"))
    if not os.path.exists(tf) or not read("pet-bridge-token.txt", required=False):
        import secrets as _secrets
        with open(tf, "w", encoding="utf-8") as f:
            f.write("dfy-" + _secrets.token_urlsafe(24))
        print("桌宠调桥的口令生成好了，存在 private\\pet-bridge-token.txt")

elif A.cmd == "memory-scope":
    owner = read("owner-qq.txt")
    plat = qq_platform()
    wl = (get_cfg().get("platform_settings") or {}).get("id_whitelist") or []
    groups = list(dict.fromkeys([str(x) for x in wl if str(x).startswith(plat + ":GroupMessage:")] + [f"{plat}:GroupMessage:{g}" for g in file_groups()]))
    print(lm_scope(plat, owner, groups, force=True))

elif A.cmd == "memory-migrate":
    # 走插件自己的命令 /lmem migrate-scope global [dry-run|apply] <来源>：来源只写主人的私聊，群的记忆不动。apply 前插件自动备份数据库
    owner = read("owner-qq.txt")
    plat = qq_platform()
    apply = bool(A.rest) and A.rest[0] == "apply"
    lc = lm_config()
    if lc is None:
        sys.exit("记忆插件没装（或读不到它的设置），不用搬")
    if ((lc.get("filtering_settings") or {}).get("memory_scope_mode") or "legacy") != "global":
        sys.exit("记忆还不是「私聊和桌宠共用一份」，先跑 memory-scope")
    key = read("pet-key.txt", required=False)
    tmp = None
    if not key or not key_works(key, owner):
        key, tmp = new_key("大肥鱼-搬记忆（用完就删）", days=1)
    try:
        src = f"{plat}:FriendMessage:{owner}"
        out = chat_once(key, owner, "dafeiyu-setup", f"/lmem migrate-scope global {'apply' if apply else 'dry-run'} {src}")
    except Exception as e:  # noqa: BLE001
        out = f"没跑成：{type(e).__name__} {e}"
    finally:
        if tmp:
            call("DELETE", f"/api-keys/{tmp}", None, T)
    print(mask(out) or "插件没回话（记忆插件太旧、没有 migrate-scope 命令？看后台日志）")
    if not apply and "migrate-scope" in out:
        print("\n上面是预览，没动数据。确认「来源」只有主人的私聊，再跑 memory-migrate apply")

elif A.cmd == "greet":
    # AstrBot 4.29 起有对外发消息的接口（/api/v1/im/messages），后台登录的令牌就能用
    owner = read("owner-qq.txt")
    lines = ["我来啦！以后我就住在这个号里了。有事叫我，没事也可以叫我……最好顺手带点小鱼干。",
             "到岗！我是大肥鱼，刚搬进来。先说好，摸鱼是我的本职工作。想聊什么直接说～",
             "喂喂，看得到吗？我是大肥鱼，正式上线啦！今天想聊点什么？"]
    text = " ".join(A.rest).strip() or random.choice(lines)
    plat = A.platform
    if not plat:
        # 装过上一期、来补装的，QQ 平台名是他自己起的，不一定叫「大肥鱼」
        r = call("GET", "/bots", None, T)
        bots = r.get("data") or []
        lst = bots if isinstance(bots, list) else bots.get("bots", []) if isinstance(bots, dict) else []
        qq = [b.get("id") for b in lst if b.get("type") == "aiocqhttp" and b.get("enable", True) and b.get("id")]
        if not qq:
            sys.exit("没发出去：AstrBot 里没有开着的 QQ 平台（aiocqhttp），先查第 5 步")
        plat = "大肥鱼" if "大肥鱼" in qq else qq[0]
    if A.dry:
        print(f"（没真发）会从 QQ 平台「{plat}」给大号发：「{text}」")
        sys.exit(0)
    r = call("POST", "/im/messages", {"umo": f"{plat}:FriendMessage:{owner}", "message": text}, T, 30)
    if ok(r):
        print("她给大号发了一句招呼：「" + text + "」。叫他看手机 QQ，回她一句")
    else:
        msg = mask(str(r.get("message") or r))[:200]
        hint = "（QQ 那边还没连上 AstrBot，先查第 5 步）" if "not found or not running" in msg else "（AstrBot 太旧没有这个接口，跳过这步，让他自己发一句就行）" if r.get("_http") == 404 else ""
        sys.exit("没发出去：" + msg + hint)
else:
    sys.exit("不认识的子命令：" + A.cmd)
