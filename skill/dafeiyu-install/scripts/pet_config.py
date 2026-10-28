"""桌宠那边（DeepSeek Harness + dsh-pet）的设置：桌宠跟她说话时走本机的「大肥鱼桥」。只用标准库（有 PyYAML 就顺手校验一下）。

  python -X utf8 pet_config.py detect                     看装没装 dsh-pet、DSH 的 profile 在哪（JSON）
  python -X utf8 pet_config.py apply --token-file <private\\pet-bridge-token.txt> [--port 26199] [--name 大肥鱼] [--persona-file <文件>]
  python -X utf8 pet_config.py revert                     改回去（照第一次改之前记下的样子）
  python -X utf8 pet_config.py status                     现在接没接上（不含口令）
  都可以加 --dsh-home <目录>（不写就用环境变量 DSH_HOME，再没有就是 %USERPROFILE%\\.dsh）、
  --dsh-ports 1,2,3（DSH 网页服务可能在的端口；找得到就顺手让 dsh-pet 重载、核对模型清单）

apply 改三个文件，改之前都备份，改了什么记在 <DSH_HOME>\\dsh-pet\\dafeiyu-pet-original.json（revert 照它还原）：
1. profiles\\<装了 dsh-pet 的 profile>\\cordis.patch.yml：llm-pi-ai 下加一个服务商 dafeiyu（OpenAI 兼容，
   地址 http://127.0.0.1:<port>/v1，模型 dafeiyu）。他自己加过的别的自定义模型原样留着。DSH 设置页「自定义模型 API」存的也是这里。
2. .credentials.yaml：refs 里加 DAFEIYU_API_KEY（调桥的口令；设置页存 key 也存在这里，文件会被 DSH 自动重新读）。
3. dsh-pet\\main-config.jsonc：对话模型选 dafeiyu；人设换成精简的大肥鱼（桥连不上、回落到 DSH 自己的模型时她也还是大肥鱼，
   不会变回「蓝毛小女仆」）；每只宠物的名字改成「大肥鱼」。碎碎念模型不动（默认跟随 DSH 当前模型：不走桥、不进记忆）。
   这个文件会被重写成不带注释的 JSON，原文件备份成 main-config.jsonc.dafeiyu-bak。
看不懂的写法（缩进不是两格、写成一行的 {…}）一律不硬改：输出 RESULT dsh=manual，让 Agent 带他在 DSH 设置页里点。
输出不含口令。最后一行 RESULT dsh=ok|manual|fail reason=… 给 Agent 读。
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROVIDER = "dafeiyu"
MODEL = "dafeiyu"
LABEL = "大肥鱼（QQ 同款）"
REF = "DAFEIYU_API_KEY"
STATE = "dafeiyu-pet-original.json"
MARK = "# 大肥鱼安装 Skill 加的：桌宠跟她说话走本机的「大肥鱼桥」。不要了就让 Skill 卸掉，或者整段删掉"
HERE = os.path.dirname(os.path.abspath(__file__))


class Manual(Exception):
    """这份文件的写法看不懂，不硬改。"""


# ---------------------------------------------------------------- 小工具

def read_text(p):
    with open(p, encoding="utf-8-sig") as f:
        return f.read()


def read_json(p):
    try:
        return json.loads(read_text(p))
    except (OSError, ValueError):
        return None


def write_atomic(path, text, private=False):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".dafeiyu-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if private and os.name != "nt":
            os.chmod(tmp, 0o600)  # DSH 在 Mac、Linux 上不读别人也能读的凭证文件
        elif os.path.exists(path) and os.name != "nt":
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def backup_once(path):
    b = path + ".dafeiyu-bak"
    if os.path.exists(path) and not os.path.exists(b):
        shutil.copy2(path, b)
    return b


def dsh_home(a):
    return os.path.abspath(a.dsh_home or os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh"))


def profiles(home):
    root = os.path.join(home, "profiles")
    out = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        pkg = read_json(os.path.join(d, "package.json")) or {}
        deps = dict(pkg.get("dependencies") or {})
        bundles = ((pkg.get("dsh") or {}).get("profile") or {}).get("bundles") or []
        nm = os.path.join(d, "node_modules", "dsh-pet")
        out.append({"name": name, "dir": d, "has_pet": "dsh-pet" in deps or "dsh-pet" in bundles or os.path.isdir(nm),
                    "pet_pkg": nm if os.path.isdir(nm) else "",
                    "pet_version": (read_json(os.path.join(nm, "package.json")) or {}).get("version") or deps.get("dsh-pet", "")})
    return out


def targets(home):
    ps = profiles(home)
    have = [p for p in ps if p["has_pet"]]
    if have:
        return have
    return [p for p in ps if p["name"] == "desktop"]


# ---------------------------------------------------------------- JSONC

def strip_jsonc(s):
    """去掉 // 和 /* */ 注释、末尾多余的逗号（字符串里的不动）。"""
    out, i, n, in_str, esc = [], 0, len(s), False, False
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        elif c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def read_jsonc(p):
    return json.loads(strip_jsonc(read_text(p)))


# ---------------------------------------------------------------- cordis.patch.yml（行级改，不重排他的文件）

def ind(line):
    return len(line) - len(line.lstrip(" "))


def blank(line):
    s = line.strip()
    return not s or s.startswith("#")


def region(lines, i, depth):
    """第 i 行这个键下面的子块：从 i+1 到第一行缩进 <= depth 的非空、非注释行（不含）。"""
    j = i + 1
    while j < len(lines) and (blank(lines[j]) or ind(lines[j]) > depth):
        j += 1
    while j > i + 1 and not lines[j - 1].strip():  # 末尾空行不算子块
        j -= 1
    return i + 1, j


def provider_lines(port):
    return ["      dafeiyu:",
            f"        apiKeyEnv: {REF}",
            "        api: openai-completions",
            f"        baseURL: http://127.0.0.1:{port}/v1",
            f'        displayName: "{LABEL}"',
            "        models:",
            "          - id: dafeiyu",
            f'            name: "{LABEL}"']


def entries(lines):
    """顶层列表每一项的 [开始, 结束)。文件不是顶层列表（也不是 []）就看不懂。"""
    starts = []
    for i, l in enumerate(lines):
        if blank(l) or ind(l) > 0:
            continue
        if re.match(r"^-(\s|$)", l):
            starts.append(i)
        elif l.strip() != "[]":
            raise Manual("cordis.patch.yml 不是一张列表")
    return [(s, starts[k + 1] if k + 1 < len(starts) else len(lines)) for k, s in enumerate(starts)]


def pi_entry(lines):
    for s, e in entries(lines):
        for i in range(s, e):
            if re.match(r"""^(-\s|\s\s)id:\s*["']?llm-pi-ai["']?\s*(#.*)?$""", lines[i]):
                if not lines[s].startswith("- "):
                    raise Manual("llm-pi-ai 那一项的写法看不懂")
                return s, e
    return None


def find_key(lines, lo, hi, depth, key):
    rx = re.compile(rf"^ {{{depth}}}{key}:(.*)$") if depth else re.compile(rf"^(?:- )?{key}:(.*)$")
    for i in range(lo, hi):
        if depth == 2 and lines[i].startswith("- ") and re.match(rf"^- {key}:(.*)$", lines[i]):
            return i, lines[i].split(":", 1)[1]
        m = rx.match(lines[i])
        if m and ind(lines[i]) == depth:
            return i, m.group(1)
    return None, None


def patch_add(text, port):
    """返回 (新文本, 是不是我们新建的整项)。"""
    lines = text.splitlines()
    ours = provider_lines(port)
    hit = pi_entry(lines)
    if not hit:
        lines = [l for l in lines if l.strip() != "[]"]
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", MARK, "- id: llm-pi-ai", "  config:", "    providers:"] + ours
        return "\n".join(lines) + "\n", True
    s, e = hit
    ci, rest = find_key(lines, s, e, 2, "config")
    if ci is None:
        ins = s + 1
        lines[ins:ins] = ["  config:", "    providers:"] + ours
        return "\n".join(lines) + "\n", False
    rest = rest.split("#", 1)[0].strip()
    if rest == "{}":
        lines[ci] = "  config:"
        lines[ci + 1:ci + 1] = ["    providers:"] + ours
        return "\n".join(lines) + "\n", False
    if rest:
        raise Manual("llm-pi-ai 的 config 写成了一行")
    lo, hi = region(lines, ci, 2)
    pi, prest = find_key(lines, lo, hi, 4, "providers")
    if pi is None:
        for i in range(lo, hi):
            if not blank(lines[i]) and ind(lines[i]) != 4:
                raise Manual("llm-pi-ai 的 config 不是两格缩进")
        lines[ci + 1:ci + 1] = ["    providers:"] + ours
        return "\n".join(lines) + "\n", False
    prest = prest.split("#", 1)[0].strip()
    if prest == "{}":
        lines[pi] = "    providers:"
        lines[pi + 1:pi + 1] = ours
        return "\n".join(lines) + "\n", False
    if prest:
        raise Manual("providers 写成了一行")
    plo, phi = region(lines, pi, 4)
    for i in range(plo, phi):
        if not blank(lines[i]) and ind(lines[i]) < 6:
            raise Manual("providers 不是两格缩进")
    di, _ = find_key(lines, plo, phi, 6, PROVIDER)
    if di is not None:
        dlo, dhi = region(lines, di, 6)
        lines[di:dhi] = ours
    else:
        lines[pi + 1:pi + 1] = ours
    return "\n".join(lines) + "\n", False


def patch_remove(text, created):
    lines = text.splitlines()
    try:
        hit = pi_entry(lines)
    except Manual:
        return text
    if not hit:
        return text
    s, e = hit
    ci, _ = find_key(lines, s, e, 2, "config")
    if ci is None:
        return text
    lo, hi = region(lines, ci, 2)
    pi, _ = find_key(lines, lo, hi, 4, "providers")
    if pi is None:
        return text
    plo, phi = region(lines, pi, 4)
    di, _ = find_key(lines, plo, phi, 6, PROVIDER)
    if di is None:
        return text
    dlo, dhi = region(lines, di, 6)
    del lines[di:dhi]
    # 我们新建的那一项，删完只剩空壳：整项连同说明那行一起拿掉
    s, e = pi_entry(lines) or (s, s)
    body = [l for l in lines[s:e] if l.strip() and not l.strip().startswith("#")]
    if created and body == ["- id: llm-pi-ai", "  config:", "    providers:"]:
        cut = s - 1 if s > 0 and lines[s - 1] == MARK else s
        del lines[cut:e]
        while lines and not lines[-1].strip():
            lines.pop()
        if not [l for l in lines if not blank(l)]:
            lines.append("[]")
    return "\n".join(lines) + "\n"


def patch_has(text, port=None):
    try:
        lines = text.splitlines()
        hit = pi_entry(lines)
    except Manual:
        return False
    if not hit:
        return False
    blk = "\n".join(lines[hit[0]:hit[1]])
    return bool(re.search(r"^      dafeiyu:\s*$", blk, re.M)) and (port is None or f"127.0.0.1:{port}/v1" in blk)


def yaml_ok(text):
    """有 PyYAML 就核一下：能解析，dafeiyu 在 llm-pi-ai 的 providers 下。没有 PyYAML 返回 None。"""
    try:
        import yaml
    except ImportError:
        return None

    class L(yaml.SafeLoader):
        pass
    L.add_multi_constructor("", lambda loader, suffix, node: None)  # !!js 这类自定义标签不管
    try:
        doc = yaml.load(text, Loader=L)
    except yaml.YAMLError:
        return False
    if not isinstance(doc, list):
        return False
    for it in doc:
        if isinstance(it, dict) and it.get("id") == "llm-pi-ai":
            p = ((it.get("config") or {}).get("providers") or {}).get(PROVIDER) or {}
            return p.get("apiKeyEnv") == REF and p.get("api") == "openai-completions"
    return False


# ---------------------------------------------------------------- .credentials.yaml

FLOW_KEY = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\S")


def flow_items(inner):
    """refs: {A: x, B: "y"} 里花括号中间那段，按不在引号里的逗号切开；值原样留着（是他的 key，一个字不动）。看不懂返回 None。"""
    items, cur, q = [], "", ""
    for ch in inner:
        if q:
            cur += ch
            if ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
            cur += ch
        elif ch == ",":
            items.append(cur)
            cur = ""
        elif ch in "{}[]":
            return None  # 里面还套着 {…}、[…]：不是 DSH 写的那种，不硬改
        else:
            cur += ch
    if q:
        return None
    items.append(cur)
    items = [x.strip() for x in items if x.strip()]
    return items if all(FLOW_KEY.match(x) for x in items) else None


def flow_key(item):
    return FLOW_KEY.match(item).group(1)


def cred_set(text, value):
    lines = (text or "").splitlines()
    if not lines:
        return f'version: 1\nrefs:\n  {REF}: "{value}"\n'
    if not any(re.match(r"^version:\s*1\s*$", l) for l in lines):
        raise Manual(".credentials.yaml 不是 DSH 现在的格式（没有 version: 1）")
    ours = f'  {REF}: "{value}"'
    for i, l in enumerate(lines):
        m = re.match(r"^refs:(.*)$", l)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest == "{}" or re.fullmatch(r"\{\}\s*#.*", rest):
            lines[i] = "refs:"
            lines.insert(i + 1, ours)
            return "\n".join(lines) + "\n"
        if rest.startswith("{"):
            # DSH 欢迎页存 key 时写的是一行：refs: {DEEPSEEK_API_KEY: sk-…}。我们的那条加在花括号里，他的原样不动
            items = flow_items(rest[1:-1]) if rest.endswith("}") else None
            if items is None:
                raise Manual(".credentials.yaml 的 refs 写法看不懂（一行里套了别的东西）")
            items = [x for x in items if flow_key(x) != REF] + [f'{REF}: "{value}"']
            lines[i] = "refs: {" + ", ".join(items) + "}"
            return "\n".join(lines) + "\n"
        if rest and not rest.startswith("#"):
            raise Manual(".credentials.yaml 的 refs 写法看不懂")
        lo, hi = region(lines, i, 0)
        for j in range(lo, hi):
            if re.match(rf"^\s+{REF}:", lines[j]):
                lines[j] = ours
                return "\n".join(lines) + "\n"
        lines.insert(i + 1, ours)
        return "\n".join(lines) + "\n"
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines + ["refs:", ours]) + "\n"


def cred_remove(text):
    lines = [l for l in (text or "").splitlines() if not re.match(rf"^\s+{REF}:", l)]
    for i, l in enumerate(lines):
        m = re.match(r"^refs:\s*\{(.*)\}\s*$", l)
        if m:
            items = flow_items(m.group(1))
            if items is not None and any(flow_key(x) == REF for x in items):
                lines[i] = "refs: {" + ", ".join(x for x in items if flow_key(x) != REF) + "}"
            continue
        # refs 下只剩我们那一条的话，删完要写成 refs: {}（空着 DSH 会当成写错了，启动报错）
        if re.match(r"^refs:\s*(#.*)?$", l) and not any(not blank(x) for x in lines[slice(*region(lines, i, 0))]):
            lines[i] = "refs: {}"
    return "\n".join(lines) + "\n" if lines else ""


def cred_has(text):
    return bool(re.search(rf"(^\s+|^refs:\s*\{{.*?)\b{REF}:\s*[^\s,}}]", text or "", re.M))


# ---------------------------------------------------------------- dsh-pet 设置

def pet_paths(home):
    d = os.path.join(home, "dsh-pet")
    return d, os.path.join(d, "main-config.jsonc"), os.path.join(d, "main-config.json"), os.path.join(d, STATE)


def default_pets(home):
    for p in profiles(home):
        f = os.path.join(p["pet_pkg"], "assets", "config.jsonc") if p["pet_pkg"] else ""
        if f and os.path.exists(f):
            try:
                pets = read_jsonc(f).get("pets")
                if isinstance(pets, list) and pets:
                    return pets
            except (OSError, ValueError):
                pass
    # 找不到包里的默认：照 dsh-pet 0.3.8 的默认写一只（字段要写全，少了它会告警）
    return [{"name": "蓝毛小女仆", "id": "main", "size": 462, "balanceEnabled": True, "whisperEnabled": True,
             "workStatusEnabled": True, "fixedEnabled": False, "display": "both",
             "position": {"corner": "top-right", "marginX": 24, "marginY": 24}}]


def pet_apply(home, name, persona):
    d, jsonc, legacy, state_f = pet_paths(home)
    src = jsonc if os.path.exists(jsonc) else legacy if os.path.exists(legacy) else ""
    try:
        cfg = read_jsonc(src) if src else {}
    except ValueError:
        raise Manual("dsh-pet 的设置文件 main-config.jsonc 解析不了（可能被手改坏了）")
    if not isinstance(cfg, dict):
        raise Manual("dsh-pet 的设置文件不是一个对象")
    state = read_json(state_f) or {}
    first = not state.get("pet")
    if first:
        # 只记第一次改之前的样子：重跑 apply 不会把我们改过的当成原样
        state["pet"] = {"had_file": bool(src), "chatModel": cfg.get("chatModel"), "whisperPrompt": cfg.get("whisperPrompt"),
                        "had_pets": isinstance(cfg.get("pets"), list),
                        "pets": [{"id": p.get("id"), "name": p.get("name"), "whisperPrompt": p.get("whisperPrompt")}
                                 for p in (cfg.get("pets") or []) if isinstance(p, dict)]}
    pets = cfg.get("pets") if isinstance(cfg.get("pets"), list) and cfg.get("pets") else default_pets(home)
    for p in pets:
        if isinstance(p, dict):
            p["name"] = name
            p.pop("whisperPrompt", None)  # 每只宠物自己的人设会盖过顶层那份：去掉，统一用大肥鱼的
    cfg["pets"] = pets
    cfg["chatModel"] = {"provider": PROVIDER, "model": MODEL}
    cfg["whisperPrompt"] = persona
    if src and first:
        backup_once(src)  # 只备份他自己的原文件，重跑时不拿我们改过的版本当备份
    head = "// 大肥鱼安装 Skill 改过：对话模型走本机的「大肥鱼桥」（就是 QQ 里那个她），名字和人设换成她。\n// 改之前的原文件在 main-config.jsonc.dafeiyu-bak；让 Skill「把桌宠改回来」会还原这几项。\n"
    write_atomic(jsonc, head + json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    write_atomic(state_f, json.dumps(state, ensure_ascii=False, indent=1))
    return len(pets)


def pet_revert(home, persona):
    d, jsonc, legacy, state_f = pet_paths(home)
    state = read_json(state_f) or {}
    orig = state.get("pet")
    if not orig or not os.path.exists(jsonc):
        return "没改过"
    try:
        cfg = read_jsonc(jsonc)
    except ValueError:
        return "设置文件解析不了，没动"
    cm = cfg.get("chatModel") or {}
    if isinstance(cm, dict) and cm.get("provider") == PROVIDER:
        if orig.get("chatModel") is None:
            cfg.pop("chatModel", None)
        else:
            cfg["chatModel"] = orig["chatModel"]
    if cfg.get("whisperPrompt") == persona or orig.get("whisperPrompt") is not None:
        if orig.get("whisperPrompt") is None:
            cfg.pop("whisperPrompt", None)
        else:
            cfg["whisperPrompt"] = orig["whisperPrompt"]
    if not orig.get("had_pets"):
        cfg.pop("pets", None)
    else:
        by = {p.get("id"): p for p in orig.get("pets") or []}
        for p in cfg.get("pets") or []:
            o = by.get(p.get("id")) if isinstance(p, dict) else None
            if o:
                if o.get("name") is not None:
                    p["name"] = o["name"]
                if o.get("whisperPrompt"):
                    p["whisperPrompt"] = o["whisperPrompt"]
    if not cfg and not orig.get("had_file"):
        os.remove(jsonc)  # 本来就没有这个文件（dsh-pet 用的默认设置）：删掉，回到默认
    else:
        write_atomic(jsonc, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    state.pop("pet", None)
    write_atomic(state_f, json.dumps(state, ensure_ascii=False, indent=1))
    return "改回去了"


# ---------------------------------------------------------------- DSH 网页服务（找得到才用，找不到不影响）

def dsh_get(port, path, method="GET", timeout=4):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"{}" if method == "POST" else None)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def find_dsh(ports):
    for p in ports:
        try:
            meta = dsh_get(p, "/dsh-pet-7340/config/meta", timeout=2)
            if isinstance(meta, dict):
                return p
        except Exception:  # noqa: BLE001
            continue
    return None


def has_model(port):
    try:
        cat = dsh_get(port, "/dsh-pet-7340/models", timeout=8)
    except Exception:  # noqa: BLE001
        return None
    for p in cat.get("providers") or []:
        if p.get("id") == PROVIDER:
            return True
    return False


# ---------------------------------------------------------------- 子命令

def cmd_detect(a):
    home = dsh_home(a)
    d, jsonc, legacy, state_f = pet_paths(home)
    ps = profiles(home)
    print(json.dumps({"dsh_home": home, "dsh_home_exists": os.path.isdir(home), "pet_dir": os.path.isdir(d),
                      "pet_profiles": [p["name"] for p in ps if p["has_pet"]], "profiles": [p["name"] for p in ps],
                      "pet_version": next((p["pet_version"] for p in ps if p["pet_version"]), ""),
                      "user_config": jsonc if os.path.exists(jsonc) else (legacy if os.path.exists(legacy) else ""),
                      "configured": bool((read_json(state_f) or {}).get("pet"))}, ensure_ascii=False))


def cmd_status(a):
    home = dsh_home(a)
    out = {"profiles": {}, "credential": False, "chat_model": False}
    for p in targets(home):
        f = os.path.join(p["dir"], "cordis.patch.yml")
        out["profiles"][p["name"]] = patch_has(read_text(f)) if os.path.exists(f) else False
    cf = os.path.join(home, ".credentials.yaml")
    out["credential"] = cred_has(read_text(cf)) if os.path.exists(cf) else False
    d, jsonc, legacy, state_f = pet_paths(home)
    try:
        cm = read_jsonc(jsonc).get("chatModel") or {} if os.path.exists(jsonc) else {}
        out["chat_model"] = cm.get("provider") == PROVIDER
    except ValueError:
        out["chat_model"] = None
    out["ok"] = bool(out["profiles"]) and all(out["profiles"].values()) and out["credential"] and out["chat_model"] is True
    print(json.dumps(out, ensure_ascii=False))


def cmd_apply(a):
    home = dsh_home(a)
    if not os.path.isdir(home):
        print(f"没找到 DeepSeek Harness 的数据目录：{home}")
        print("RESULT dsh=fail reason=no-dsh next=他没装 DSH 桌面版（或者装在别的地方，用 --dsh-home 指过去）")
        return 1
    token = read_text(a.token_file).strip() if a.token_file and os.path.exists(a.token_file) else ""
    if not token:
        print("RESULT dsh=fail reason=no-token next=先跑 astr_setup.py pet-key（会生成 private\\pet-bridge-token.txt）")
        return 1
    persona = read_text(a.persona_file).strip() if a.persona_file else read_text(os.path.join(HERE, "pet-persona.txt")).strip()
    tg = targets(home)
    if not tg:
        print("RESULT dsh=manual reason=no-profile next=DSH 里没找到 profile：让他在 DSH 设置 → 模型 → 自定义模型 API 里手动加（见 references/桌宠.md）")
        return 2
    d, jsonc, legacy, state_f = pet_paths(home)
    state = read_json(state_f) or {}
    manual = []
    # 1 自定义模型
    for p in tg:
        f = os.path.join(p["dir"], "cordis.patch.yml")
        text = read_text(f) if os.path.exists(f) else "[]\n"
        try:
            new, created = patch_add(text, a.port)
        except Manual as e:
            manual.append(f"{p['name']}：{e}")
            continue
        if yaml_ok(new) is False:
            manual.append(f"{p['name']}：改完校验没过，没写")
            continue
        if new != text:
            backup_once(f)
            write_atomic(f, new)
        st = state.setdefault("patch", {}).setdefault(p["name"], {})
        st["created"] = st.get("created") or created
        print(f"DSH 的 {p['name']} 里登记好了自定义模型「{LABEL}」（地址 127.0.0.1:{a.port}）")
    # 2 口令
    cf = os.path.join(home, ".credentials.yaml")
    try:
        ctext = read_text(cf) if os.path.exists(cf) else ""
        new = cred_set(ctext, token)
        if new != ctext:
            if ctext:
                backup_once(cf)
            write_atomic(cf, new, private=True)
        state["credential"] = True
        print("调桥的口令存进了 DSH 的凭证文件（.credentials.yaml 的 DAFEIYU_API_KEY）")
    except Manual as e:
        manual.append(str(e))
    write_atomic(state_f, json.dumps(state, ensure_ascii=False, indent=1))
    # 3 dsh-pet
    try:
        n = pet_apply(home, a.name, persona)
        print(f"dsh-pet：对话模型选了「{LABEL}」，{n} 只宠物都叫「{a.name}」，人设换成精简的{a.name}")
    except Manual as e:
        manual.append(str(e))
    # 4 DSH 开着的话：让桌面宠物按新设置重建窗口，核一下模型清单里有没有她
    ports = [int(x) for x in re.split(r"[,\s]+", a.dsh_ports or "") if x.isdigit()]
    port = find_dsh(ports) if ports else None
    seen = None
    if port:
        for _ in range(10):
            seen = has_model(port)
            if seen:
                break
            time.sleep(1)
        try:
            dsh_get(port, "/dsh-pet-7340/reload", method="POST", timeout=10)
            print("让桌宠重载了一次（名字、人设马上换）")
        except Exception:  # noqa: BLE001
            print("桌宠重载没成功：让他右键桌宠点「重载配置」")
    if manual:
        for m in manual:
            print("没改成：" + m)
        print("RESULT dsh=manual reason=" + ("patch" if any("cordis" in m or "providers" in m or "config" in m or "llm-pi-ai" in m for m in manual) else "other")
              + " next=照 references/桌宠.md「手动填」那节带他在 DSH 设置页里点")
        return 2
    if port and seen is False:
        print("DSH 的模型清单里还没有她：DSH 可能要重开一次才认新加的模型")
        print("RESULT dsh=ok reason=restart-dsh next=让他从托盘退出 DSH 再打开（这会断掉你这次对话，先把要说的说完）")
        return 0
    print("RESULT dsh=ok" + ("" if port else " reason=dsh-not-checked"))
    return 0


def cmd_revert(a):
    home = dsh_home(a)
    d, jsonc, legacy, state_f = pet_paths(home)
    state = read_json(state_f) or {}
    for p in profiles(home):
        f = os.path.join(p["dir"], "cordis.patch.yml")
        if os.path.exists(f):
            text = read_text(f)
            new = patch_remove(text, ((state.get("patch") or {}).get(p["name"]) or {}).get("created", False))
            if new != text:
                write_atomic(f, new)
                print(f"DSH 的 {p['name']} 里去掉了自定义模型「{LABEL}」")
    cf = os.path.join(home, ".credentials.yaml")
    if os.path.exists(cf):
        text = read_text(cf)
        new = cred_remove(text)
        if new != text:
            write_atomic(cf, new, private=True)
            print("DSH 凭证文件里去掉了调桥的口令")
    if os.path.exists(cf + ".dafeiyu-bak"):
        os.remove(cf + ".dafeiyu-bak")  # 改之前的备份里也有他别的凭证，拆了就不留这份副本
    persona = read_text(a.persona_file).strip() if a.persona_file else read_text(os.path.join(HERE, "pet-persona.txt")).strip()
    print("dsh-pet：" + pet_revert(home, persona))
    state = read_json(state_f) or {}
    for k in ("patch", "credential"):
        state.pop(k, None)
    if os.path.exists(state_f):
        if state:
            write_atomic(state_f, json.dumps(state, ensure_ascii=False, indent=1))
        else:
            os.remove(state_f)
    ports = [int(x) for x in re.split(r"[,\s]+", a.dsh_ports or "") if x.isdigit()]
    port = find_dsh(ports) if ports else None
    if port:
        try:
            dsh_get(port, "/dsh-pet-7340/reload", method="POST", timeout=10)
        except Exception:  # noqa: BLE001
            pass
    print("RESULT dsh=reverted")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["detect", "apply", "revert", "status"])
    ap.add_argument("--dsh-home", default="")
    ap.add_argument("--dsh-ports", default="")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--port", type=int, default=26199)
    ap.add_argument("--name", default="大肥鱼")
    ap.add_argument("--persona-file", default="")
    a = ap.parse_args()
    sys.exit({"detect": cmd_detect, "apply": cmd_apply, "revert": cmd_revert, "status": cmd_status}[a.cmd](a) or 0)


if __name__ == "__main__":
    main()
