"""大肥鱼桥：桌宠（DeepSeek Harness 的 dsh-pet 插件）跟她说话时，由 QQ 里同一个大肥鱼来回答。

DSH 里登记一个「自定义模型 API」（OpenAI Chat Completions 兼容，provider dafeiyu、模型 dafeiyu，地址指向这座桥），
dsh-pet 的对话模型选它。桥只取用户最后一句话，转给 AstrBot 的开放接口 POST /api/v1/chat（WebChat 会话），
把她的回复传回桌宠气泡。人设、长期记忆（LivingMemory）、工具（记住、提醒、改签名……）全走 AstrBot 那一套；
桌宠自己发来的人设、历史、表情包清单一律不用。

- 以主人的身份说话：username = 主人 QQ 号，session_id 固定 desk-pet。key 要有 chat + chat:admin 两个范围
  （没有 chat:admin，开放接口会拒绝「username 是管理员」的请求）。
- 碎碎念（dsh-pet 的固定开头「随便说一句日常碎碎念」）走单独的 desk-pet-whisper 会话：不挤进主人和她的对话；
  记忆白名单里不列它，就不进长期记忆。配置包默认碎碎念不走桥（用 DSH 自己的模型），这里只是兜底。
- 跟 AstrBot 要「不流式」的结果：表情包标记、漏出来的内部标签由 AstrBot 和配置包插件先洗掉（跟 QQ 里一样），
  分段回复的每一段在气泡里各占一行。图片、语音这里不转（桌宠气泡只显示文字）。
- 连不上、超时、AstrBot 报错都回 OpenAI 格式的错误：dsh-pet 会自动回落到 DSH 当前对话的模型再试一次。

安全：只听 127.0.0.1；Host 头只认 127.0.0.1 / localhost（挡 DNS 重绑定）；POST 只收 application/json（挡网页表单）；
不回任何跨域头；/v1/* 要带口令（Authorization: Bearer 或 X-API-Key，口令在 private\\pet-bridge-token.txt）。
key、口令、主人号只从文件读，不打印、不写日志；日志只记时间、地址、结果、字数。

只用 Python 标准库。用法（Windows 上用 AstrBot 自带的 Python）：
  python -X utf8 dafeiyu_pet_bridge.py --dir C:\\dafeiyu
  python -X utf8 dafeiyu_pet_bridge.py --dir C:\\dafeiyu --check     # 只查 AstrBot 和 key，不起服务；通了退出码 0
"""
import argparse
import hmac
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.1"
MODEL_ID = "dafeiyu"
DEFAULT_PORT = 26199          # 6199 是 QQ 连 AstrBot 的反向 WS，别撞；工作台是 26185
WHISPER_PREFIX = "随便说一句日常碎碎念"  # dsh-pet 碎碎念的固定开头（src/host/whisper.ts）
MEME_MARK = "\n\n[配图]"       # dsh-pet 对话时追加在用户原话后的表情包清单（src/host/chat.ts）
MEME_TAG = re.compile(r"&&[^&\s]{1,40}&&")  # meme_manager 的标记，流式时可能漏出来
MAX_BODY = 4 * 1024 * 1024
LOG_MAX = 512 * 1024


def read_file(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read().strip()


def read_kv(path):
    d = {}
    try:
        for line in open(path, encoding="utf-8-sig"):
            k, sep, v = line.replace("：", ":", 1).partition(":")
            if sep and v.strip():
                d[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return d


class Log:
    """写 <工作目录>\\logs\\pet-bridge.log（pythonw 没有控制台）。超过 512 KB 挪成 .1 再写新的。"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def __call__(self, msg):
        line = time.strftime("%m-%d %H:%M:%S ") + msg + "\n"
        if not self.path:
            sys.stderr.write(line)
            return
        with self.lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                if os.path.exists(self.path) and os.path.getsize(self.path) > LOG_MAX:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return ""


def last_user_text(body):
    for m in reversed(body.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            t = text_of(m.get("content"))
            if MEME_MARK in t:
                t = t.split(MEME_MARK, 1)[0]
            return t.strip()
    return ""


def tidy(text):
    text = MEME_TAG.sub("", text or "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class Cfg:
    """key 和口令跟着文件走：重建了 key（setup-pet.ps1 重跑）下一次请求就用新的，不用重开桥。
    文件一时不见了（正在重建）就先用上一次读到的，不会变成「不要口令」。"""

    def __init__(self):
        self._files, self._cache = {}, {}

    def watch(self, name, path):
        self._cache[name] = (os.stat(path).st_mtime_ns, read_file(path))  # 开桥时文件必须在，不在就报缺文件
        self._files[name] = path

    def __getattr__(self, name):
        files = self.__dict__.get("_files") or {}
        if name not in files:
            raise AttributeError(name)
        old = self._cache[name]
        try:
            mt = os.stat(files[name]).st_mtime_ns
            if mt != old[0]:
                val = read_file(files[name])
                if val:
                    self._cache[name] = old = (mt, val)
        except OSError:
            pass
        return old[1]


def astrbot_stream(cfg, text, session):
    """调 AstrBot 开放接口，一段一段吐出她的回复文字（不流式时一段就是分段回复的一条）。"""
    body = {"message": text, "username": cfg.owner, "session_id": session}
    if not cfg.stream:
        body["enable_streaming"] = False
    req = urllib.request.Request(cfg.astrbot + "/api/v1/chat", data=json.dumps(body, ensure_ascii=False).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-Key", cfg.key)
    got = False
    with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("{"):
                # 出错时 AstrBot 回的是一整个 JSON（状态码照样 200），不是一行行 data:。
                # 10-29 实测：会话名 desk-pet 先被别的 username 占了，回「session_id belongs to another username」，
                # 以前这里当成「她没说话」，日志看不出原因
                try:
                    err = json.loads(line)
                except ValueError:
                    err = None
                if isinstance(err, dict) and err.get("status") == "error":
                    raise RuntimeError(str(err.get("message") or "AstrBot 报错")[:200])
                continue
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            kind, data = ev.get("type"), ev.get("data")
            if ev.get("chain_type") in ("tool_call", "tool_call_result", "reasoning", "agent_stats"):
                continue
            if kind == "plain" and isinstance(data, str):
                if not ev.get("streaming") and got and not cfg.stream:
                    yield "\n"  # 不流式：分段回复的每一段各占一行
                got = True
                yield data
            elif kind == "complete" and not got and isinstance(data, str):
                got = True
                yield data
            elif kind == "error":
                raise RuntimeError(str(data or "AstrBot 报错")[:200])
            elif kind == "end":
                return
            # image / record / file：桌宠气泡只显示文字，不转


def check_astrbot(cfg, timeout=5):
    """不聊天，只看 AstrBot 开没开、key 还能不能用。返回 (ok, 原因)。"""
    q = urllib.parse.urlencode({"username": cfg.owner, "page": 1, "page_size": 1})
    req = urllib.request.Request(cfg.astrbot + "/api/v1/chat/sessions?" + q)
    req.add_header("X-API-Key", cfg.key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8", "replace") or "{}")
        if isinstance(out, dict) and out.get("status") == "error":
            return False, "key"
        return True, ""
    except urllib.error.HTTPError as e:
        return False, "key" if e.code in (401, 403) else f"http-{e.code}"
    except (urllib.error.URLError, OSError, ValueError):
        return False, "astrbot-down"


class Handler(BaseHTTPRequestHandler):
    cfg = None
    log = None
    server_version = "dafeiyu-pet-bridge"
    sys_version = ""

    def log_message(self, fmt, *args):  # 不记每个请求的原始行（里面可能有地址参数）
        pass

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code, msg):
        # OpenAI 格式的错误：dsh-pet 收到失败会自动回落到 DSH 当前对话的模型重试一次
        self._send(code, {"error": {"message": msg, "type": "dafeiyu_bridge_error"}})

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return name in ("127.0.0.1", "localhost", "[::1]")

    def _authed(self):
        tok = self.cfg.token
        if not tok:
            return True
        h = self.headers.get("Authorization", "")
        given = h[7:].strip() if h.startswith("Bearer ") else self.headers.get("X-API-Key", "").strip()
        return bool(given) and hmac.compare_digest(given, tok)

    def _path(self):
        return urllib.parse.urlsplit(self.path).path.rstrip("/")

    def do_OPTIONS(self):  # 不回跨域头：网页里的脚本调不进来
        self._error(405, "不支持")

    def do_GET(self):
        if not self._host_ok():
            return self._error(403, "只认本机地址")
        path = self._path()
        if path in ("", "/health"):
            info = {"ok": True, "name": "dafeiyu-pet-bridge", "version": VERSION, "port": self.cfg.port, "pid": os.getpid()}
            if "deep=1" in (urllib.parse.urlsplit(self.path).query or ""):
                good, why = check_astrbot(self.cfg)
                info.update({"astrbot": "ok" if good else "fail", "reason": why})  # reason：key（密钥不对）、astrbot-down（大脑没开）、http-<码>
            return self._send(200, info)
        if path.endswith("/models"):
            if not self._authed():
                return self._error(401, "口令不对")
            return self._send(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "dafeiyu"}]})
        self._error(404, "没有这个地址")

    def do_POST(self):
        if not self._host_ok():
            return self._error(403, "只认本机地址")
        if not self._path().endswith("/chat/completions"):
            return self._error(404, "没有这个地址")
        if not self._authed():
            return self._error(401, "口令不对")
        if "application/json" not in (self.headers.get("Content-Type") or "").lower():
            return self._error(415, "只收 JSON")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._error(400, "请求太大或是空的")
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._error(400, "请求不是 JSON")
        if body.get("tools"):
            # DSH 的主对话（干活那个）选了「大肥鱼」这个模型：它每次都带工具清单，桌宠从来不带。
            # 不转给她：转过去写代码的话会进她跟主人的对话和记忆，DSH 那边也等不到工具调用、活干不成
            self.log("拒了一个带工具的请求（DSH 主对话选了大肥鱼？）")
            return self._error(400, "大肥鱼只在桌宠里说话：DeepSeek Harness 的主对话请换回 DeepSeek 的模型")
        text = last_user_text(body)
        if not text:
            return self._error(400, "没找到用户说的话")
        whisper = text.startswith(WHISPER_PREFIX)
        session = self.cfg.session + ("-whisper" if whisper else "")
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        model = body.get("model") or MODEL_ID
        t0 = time.time()
        kind = "碎碎念" if whisper else "对话"

        if not body.get("stream"):
            try:
                reply = tidy("".join(astrbot_stream(self.cfg, text, session)))
            except Exception as e:  # noqa: BLE001  连不上、超时、AstrBot 报错
                self.log(f"{kind} 失败（{type(e).__name__}：{str(e)[:120]}），交给 dsh-pet 回落")
                return self._error(502, f"大肥鱼没回话：{type(e).__name__}")
            if not reply:
                self.log(f"{kind} 她这次没说话（{time.time() - t0:.1f}s）")
                return self._error(502, "大肥鱼这次没说话")
            self.log(f"{kind} 回了 {len(reply)} 字（{time.time() - t0:.1f}s）")
            return self._send(200, {"id": cid, "object": "chat.completion", "created": created, "model": model,
                                    "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

        # 流式：先拿到第一段再回 200；连不上就回错误，让桌宠回落
        gen = astrbot_stream(self.cfg, text, session)
        pieces = []
        try:
            for piece in gen:
                if piece.strip():
                    pieces.append(piece)
                    break
        except Exception as e:  # noqa: BLE001
            self.log(f"{kind} 失败（{type(e).__name__}：{str(e)[:120]}），交给 dsh-pet 回落")
            return self._error(502, f"大肥鱼没回话：{type(e).__name__}")
        if not pieces:
            self.log(f"{kind} 她这次没说话（{time.time() - t0:.1f}s）")
            return self._error(502, "大肥鱼这次没说话")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(delta, finish=None):
            obj = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()

        total = 0
        try:
            first = tidy(pieces[0])
            send({"role": "assistant", "content": first})
            total += len(first)
            pending = ""
            for piece in gen:
                pending += piece
                if "&&" in pending and not MEME_TAG.search(pending) and len(pending) < 80:
                    continue  # 表情包标记可能被拆在两段里：攒齐了再发
                out = MEME_TAG.sub("", pending)
                pending = ""
                if out:
                    send({"content": out})
                    total += len(out)
            if pending:
                send({"content": MEME_TAG.sub("", pending)})
            send({}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception as e:  # noqa: BLE001  桌宠那边断开，或 AstrBot 流到一半断了
            self.log(f"{kind} 流到一半断了（{type(e).__name__}）")
            return
        self.log(f"{kind} 回了 {total} 字（{time.time() - t0:.1f}s）")


def already_running(port):
    """这个端口上已经是一座大肥鱼桥：返回 True（不重复开）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("name") == "dafeiyu-pet-bridge"
    except Exception:  # noqa: BLE001
        return False


def load_cfg(a):
    cfg = Cfg()
    d = a.dir.rstrip("\\/") if a.dir else ""
    priv = os.path.join(d, "private") if d else ""
    cfg.watch("key", a.key_file or os.path.join(priv, "pet-key.txt"))
    cfg.owner = read_file(a.owner_file or os.path.join(priv, "owner-qq.txt"))
    tf = a.token_file or (os.path.join(priv, "pet-bridge-token.txt") if d else "")
    if tf and os.path.exists(tf):
        cfg.watch("token", tf)
    else:
        cfg.token = ""
    url = a.astrbot or (read_kv(os.path.join(priv, "backend.txt")).get("url") if d else "") or "http://127.0.0.1:6185"
    cfg.astrbot = url.split()[0].rstrip("/")
    if not cfg.astrbot.startswith("http"):
        cfg.astrbot = "http://" + cfg.astrbot
    cfg.port, cfg.session, cfg.timeout, cfg.stream = a.port, a.session, a.timeout, a.stream
    if not cfg.owner.isdigit():
        sys.exit("主人号文件里应该只有一串数字")
    return cfg


def main():
    ap = argparse.ArgumentParser(description="大肥鱼桥：桌宠 ↔ AstrBot 开放接口")
    ap.add_argument("--dir", default="", help="大肥鱼工作目录（读 private\\pet-key.txt、owner-qq.txt、pet-bridge-token.txt、backend.txt）")
    ap.add_argument("--astrbot", default="", help="AstrBot 后台地址，不写就用 backend.txt 的 url")
    ap.add_argument("--key-file", default="")
    ap.add_argument("--owner-file", default="")
    ap.add_argument("--token-file", default="", help="桌宠调用这座桥要带的口令；用 --dir 时默认 private\\pet-bridge-token.txt")
    ap.add_argument("--session", default="desk-pet", help="AstrBot 里桌宠用的会话名，固定不变")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--stream", action="store_true", help="跟 AstrBot 要流式结果（字一个个出来，但不经过分段和表情包处理）")
    ap.add_argument("--log", default="", help="日志文件；用 --dir 时默认 logs\\pet-bridge.log，写 - 打到屏幕")
    ap.add_argument("--check", action="store_true", help="只查 AstrBot 和 key 能不能用，不起服务")
    a = ap.parse_args()
    if not a.dir and not (a.key_file and a.owner_file):
        ap.error("要么写 --dir，要么同时写 --key-file 和 --owner-file")
    try:
        cfg = load_cfg(a)
    except OSError as e:
        sys.exit(f"缺文件：{os.path.basename(getattr(e, 'filename', '') or '')}（先跑 setup-pet.ps1）")
    if a.check:
        good, why = check_astrbot(cfg)
        print("AstrBot 和桌宠 key 都能用" if good else {"key": "桌宠 key 不对或被撤销了（重跑 setup-pet.ps1）",
                                                        "astrbot-down": "连不上 AstrBot（大脑没开？）"}.get(why, "AstrBot 回了错误：" + why))
        sys.exit(0 if good else 1)
    logf = "" if a.log == "-" else (a.log or (os.path.join(a.dir.rstrip("\\/"), "logs", "pet-bridge.log") if a.dir else ""))
    log = Log(logf)
    if already_running(cfg.port):
        print(f"大肥鱼桥已经在跑了（127.0.0.1:{cfg.port}）")
        return
    Handler.cfg, Handler.log = cfg, log
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", cfg.port), Handler)
    except OSError as e:
        log(f"端口 {cfg.port} 被别的程序占着，没开起来（{e.__class__.__name__}）")
        sys.exit(f"端口 {cfg.port} 被别的程序占着")
    srv.daemon_threads = True
    log(f"大肥鱼桥 {VERSION} 开了：127.0.0.1:{cfg.port}/v1（模型 {MODEL_ID}）→ AstrBot，会话 {cfg.session}，{'带口令' if cfg.token else '没设口令'}")
    print(f"大肥鱼桥已开：http://127.0.0.1:{cfg.port}/v1 （模型名 {MODEL_ID}）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    socket.setdefaulttimeout(None)
    main()
