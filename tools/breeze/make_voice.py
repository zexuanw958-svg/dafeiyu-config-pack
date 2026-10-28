"""给大肥鱼做一个本地声音（Breeze TTS 2）。

两种做法：
  1 用一段话描述声音，出三个候选，挑一个（不用录音）
  2 拿一段录音克隆：自动在停顿处截一段 8–15 秒的整句，自动转写原话给你核对
两种做法都先试听一句（就是她以后在 QQ 里说话的样子），满意了才存。
存下的声音叫 dafeiyu（在 settings/pack.json 的 voice.breeze.voice 里改），导入工具语音选 4 时用它。
存的时候在 voice/breeze/ 留底：<名字>-参考.wav 和 <名字>-参考.txt（参考音和原话），克隆的还有 <名字>-原录音。
换了 Breeze 模型以后，存过的声音会失效，拿这两份重新存一次就行（下面的 save）。

用法：双击上两层的「做声音.bat」。要先用「装Breeze语音.bat」装好并启动 Breeze。
给装机的 AI 用的（不弹播放器、不问问题，结果打在屏幕上）：
  make_voice.py prep <录音> [--pick N]    截一段、转写，存成 voice/breeze/克隆-参考.wav，打出原话和其他可截的段
  make_voice.py preview <wav> <原话>      用它念一句，存成 voice/breeze/试听.wav（只试听，不存声音）
  make_voice.py save <wav> <原话> [--original <原录音>]   存成 dafeiyu，参考音和原话留底
  三个都能加 --api http://127.0.0.1:8137 指定 Breeze 的地址。
"""
import array
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from datetime import datetime

PACK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.dirname(PACK)  # 工作目录：Skill 把配置包放在 <工作目录>\dafeiyu-config-pack
OUT = os.path.join(PACK, "voice", "breeze")
DESIGN_TEXT = "哼，你终于想起来找我啦？我还以为你把我忘了呢。今天有没有给我带好吃的呀？"
TRY_TEXT = "在呢在呢，这么晚还不睡，是不是又在偷偷吃夜宵？"
DESIGN_CFG = 3  # 出候选用 3：4 会把描述里每个词都当真、换个种子差得更多，再低描述就管不住了（10-28 实测）
QQ_CFG, QQ_SEED = 1, 42  # AstrBot 调 Breeze 只给 voice，服务端按 cfg 1、种子 42 念：试听也照这个来
SR = 24000
MIN_S, BEST_S, MAX_S = 8, 11, 15  # 克隆参考音截多长：5–15 秒都行，8–15 秒的整句最稳
HOP = 0.02
BASE = "http://127.0.0.1:8137"


def say(msg=""):
    print(msg, flush=True)


def request(method, path, body=None, content_type="application/json", timeout=600):
    """调 Breeze。它一次只念一句，正忙（409，多半是她正在 QQ 里说话）就等 3 秒再试，最多等 2 分钟。"""
    for attempt in range(40):
        req = urllib.request.Request(BASE + path, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt < 39:
                if attempt == 0:
                    say("  Breeze 正忙（可能她正在 QQ 里说话），等一下…")
                time.sleep(3)
                continue
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e).encode()
    return 409, b"busy"


def multipart(fields, files):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        if v not in (None, ""):
            parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    for k, path in files.items():
        head = (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"ref{os.path.splitext(path)[1]}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        parts.append(head + open(path, "rb").read() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def write_wav(path, pcm, sr=SR):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


def speak(payload, out):
    """用描述或存好的声音念一段，存成 wav。返回是否成功。"""
    code, pcm = request("POST", "/v1/audio/speech", json.dumps({**payload, "response_format": "pcm"}).encode())
    if code != 200:
        say(f"  ！Breeze 报错（{code}）：{pcm[:200].decode('utf-8', 'replace')}")
        return False
    write_wav(out, pcm)
    return True


def forget(ref_text):
    """试听时 Breeze 会把参考音临时记在内存里（没存盘），用完删掉，免得越攒越多。"""
    code, out = request("GET", "/v1/voices", timeout=10)
    if code != 200:
        return
    for v in json.loads(out or b"[]"):
        if not v.get("saved") and str(v.get("id", "")).startswith("v_") and v.get("ref_text") == ref_text:
            request("DELETE", "/v1/voices/" + v["id"], timeout=10)


def preview(ref, text, out=None):
    """拿参考音直接念 TRY_TEXT，跟她以后在 QQ 里说话一个样（cfg 1、种子 42），但不存声音。返回 wav 路径。"""
    out = out or os.path.join(OUT, "试听.wav")
    body, ctype = multipart({"text": TRY_TEXT, "ref_text": text, "cfg_scale": QQ_CFG, "seed": QQ_SEED}, {"ref_audio": ref})
    code, pcm = request("POST", "/v1/audio/speech", body, ctype)
    forget(text)
    if code != 200:
        say(f"  ！试听失败（{code}）：{pcm[:200].decode('utf-8', 'replace')}")
        return ""
    write_wav(out, pcm)
    return out


def play(path):
    # 只有人坐在窗口前（双击 bat）才弹播放器；AI 用管道跑的时候只打路径，由它自己放给人听
    if sys.stdin.isatty():
        if os.name == "nt":
            os.startfile(path)
        elif shutil.which("afplay"):
            subprocess.run(["afplay", path])
    say(f"  （文件在 {path}）")


def save_voice(audio_path, text, name, description=""):
    body, ctype = multipart({"ref_text": text, "name": name, "description": description}, {"ref_audio": audio_path})
    code, out = request("POST", "/v1/voices", body, ctype)
    if code != 200:
        say(f"  ！存声音失败（{code}）：{out[:200].decode('utf-8', 'replace')}")
        return False
    return True


def rotate(path, stamp):
    """已有同名的留底文件就改名加上时间，不覆盖。"""
    if os.path.exists(path):
        root, ext = os.path.splitext(path)
        dst, n = f"{root}-{stamp}{ext}", 2
        while os.path.exists(dst):
            dst, n = f"{root}-{stamp}-{n}{ext}", n + 1
        os.replace(path, dst)


def keep_and_save(ref, text, name, description="", original=""):
    """存成声音，再把参考音、原话、原录音留一份在 voice/breeze/。"""
    if not save_voice(ref, text, name, description):
        return False
    code, out = request("GET", "/v1/voices", timeout=10)
    if code == 200 and not any(v.get("id") == name and v.get("saved") for v in json.loads(out or b"[]")):
        say(f"  ！Breeze 说存好了，但声音列表里没有 {name}")
        return False
    os.makedirs(OUT, exist_ok=True)
    stamp = datetime.now().strftime("%m%d-%H%M%S")
    data = open(ref, "rb").read()
    base = os.path.join(OUT, f"{name}-参考")
    rotate(base + ".wav", stamp)
    rotate(base + ".txt", stamp)
    for f in os.listdir(OUT):  # 上一次的原录音（或它的位置）也改名，免得跟这次的参考音对不上
        if re.fullmatch(re.escape(f"{name}-原录音") + r"(在哪\.txt|\.[^.]+)", f):
            if not (original and os.path.abspath(os.path.join(OUT, f)) == os.path.abspath(original)):
                rotate(os.path.join(OUT, f), stamp)
    open(base + ".wav", "wb").write(data)
    open(base + ".txt", "w", encoding="utf-8").write(text + "\n")
    kept = [base + ".wav", base + ".txt"]
    if original and os.path.isfile(original):
        dst = os.path.join(OUT, f"{name}-原录音{os.path.splitext(original)[1].lower()}")
        if os.path.getsize(original) > 50 * 1024 * 1024:  # 拖进来的是长视频之类：不拷，记下在哪
            dst = os.path.join(OUT, f"{name}-原录音在哪.txt")
            open(dst, "w", encoding="utf-8").write(os.path.abspath(original) + "\n")
        elif os.path.abspath(original) != os.path.abspath(dst):
            shutil.copyfile(original, dst)
        kept.append(dst)
    say(f"  留底：{'、'.join(os.path.basename(k) for k in kept)}（在 {OUT}；换了 Breeze 模型要重新存声音时用）")
    return True


# ───────── 找 ffmpeg 和本机语音识别（Skill 装在工作目录里，不进 PATH） ─────────
def answers():
    p = os.path.join(WORK, "answers.json")
    try:
        return json.load(open(p, encoding="utf-8-sig")) if os.path.isfile(p) else {}
    except (OSError, ValueError):
        return {}


def read_line(path):
    try:
        return open(path, encoding="utf-8-sig").read().strip().strip('"') if os.path.isfile(path) else ""
    except OSError:
        return ""


def find_ffmpeg():
    cands = [read_line(os.path.join(WORK, "ffmpeg-path.txt")),
             os.path.join(WORK, "ffmpeg", "bin", "ffmpeg.exe"),
             str(answers().get("ffmpeg_path") or ""),
             shutil.which("ffmpeg") or "",
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "ffmpeg.exe")]
    try:
        import imageio_ffmpeg  # 有的 Python 环境里自带一个
        cands.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    return next((c for c in cands if c and os.path.isfile(c)), "")


def find_asr():
    ff = find_ffmpeg()
    dirs = [read_line(os.path.join(WORK, "asr-path.txt")), os.path.join(WORK, "asr"), str(answers().get("asr_dir") or "")]
    if ff:
        dirs.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(ff))), "asr"))
    for d in dirs:
        if not d:
            continue
        exe, model, tokens = (os.path.join(d, n) for n in ("sherpa-onnx-offline.exe", "model.int8.onnx", "tokens.txt"))
        if all(os.path.isfile(p) for p in (exe, model, tokens)):
            return exe, model, tokens
    return None


def ascii_dir():
    """识别程序读不了带中文的路径：找一个全英文、能写的临时文件夹。"""
    for d in (tempfile.gettempdir(), WORK, os.environ.get("PUBLIC", ""), r"C:\Users\Public"):
        if d and d.isascii() and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return tempfile.gettempdir()


# ───────── 克隆：读录音、找停顿、截一段 ─────────
def decode(path, ff):
    """读成 24k 单声道 16 位。有 ffmpeg 什么格式都行（视频也行，只读前 5 分钟）；没有的话只认 16 位 wav。"""
    if ff:
        r = subprocess.run([ff, "-v", "error", "-i", path, "-t", "300", "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                           capture_output=True)
        if r.returncode == 0 and len(r.stdout) > SR:
            a = array.array("h")
            a.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
            if sys.byteorder == "big":
                a.byteswap()
            return a, SR
        say("  ！ffmpeg 读不了这个文件：" + r.stderr.decode("utf-8", "replace")[-200:])
        return None, 0
    if path.lower().endswith(".wav"):
        try:
            with wave.open(path) as w:
                ch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
                raw = w.readframes(min(w.getnframes(), sr * 300))
        except (wave.Error, EOFError, OSError):
            sw = 0
        if sw == 2:
            a = array.array("h")
            a.frombytes(raw)
            if sys.byteorder == "big":
                a.byteswap()
            if ch > 1:
                a = array.array("h", (sum(a[i:i + ch]) // ch for i in range(0, len(a) - ch + 1, ch)))
            return a, sr
    say("  ！这段录音要先转一下格式，可电脑上没找到 ffmpeg。")
    say("    让帮你装大肥鱼的 AI 跑一下 Skill 里的 install-ffmpeg.ps1（装看视频时也会装它），再来一次；")
    say("    或者自己先转成 wav（单声道、16 位）再拖进来。")
    return None, 0


def frame_db(a, sr):
    h = int(sr * HOP)
    out = []
    for i in range(len(a) // h):
        seg = a[i * h:(i + 1) * h]
        out.append(10 * math.log10(sum(x * x for x in seg) / h + 1e-9) - 90.3)  # 相对满幅的分贝
    return out


def windows(db):
    """按停顿把录音分成一句一句，挑连着的几句凑成 8–15 秒、中间没有长停顿、最接近 11 秒的几段。
    返回 [(起始帧, 结束帧)]，最好的在前。"""
    if not db:
        return []
    thr = max(db) - 35
    sp = [d > thr for d in db]
    if not any(sp):
        return []
    first, last = sp.index(True), len(sp) - 1 - sp[::-1].index(True)
    runs, i = [], first  # 一句一句的起止帧（中间隔着 0.25 秒以上的停顿）
    while i <= last:
        j = i
        while j <= last and (sp[j] or not all(not x for x in sp[j:j + int(0.25 / HOP)])):
            j += 1
        runs.append((i, j))
        while j <= last and not sp[j]:
            j += 1
        i = j
    pad = lambda p, q: (max(0, p - 5), min(len(db), q + 8))  # 前留 0.1 秒、后留 0.16 秒
    if (runs[-1][1] - runs[0][0]) * HOP <= MAX_S:
        return [pad(runs[0][0], runs[-1][1])]
    cands = []
    for x in range(len(runs)):
        for y in range(x, len(runs)):
            d = (runs[y][1] - runs[x][0]) * HOP
            if d > MAX_S:
                break
            if d < MIN_S:
                continue
            longest = max([(runs[k + 1][0] - runs[k][1]) * HOP for k in range(x, y)], default=0)
            speech = sum(sp[runs[x][0]:runs[y][1]]) / (runs[y][1] - runs[x][0])
            cands.append((abs(d - BEST_S) + (3 if longest > 1.0 else 0) + (1 - speech) * 5, runs[x][0], runs[y][1]))
    picked = []
    for _, p, q in sorted(cands):
        if all(min(q, q2) - max(p, p2) < 0.5 * (q - p) for p2, q2 in picked):
            picked.append((p, q))
        if len(picked) >= 5:
            break
    if not picked:  # 一口气说了很久、几乎没停：从最长那句的开头截，在 10–15 秒之间最安静的地方下刀
        p, q = max(runs, key=lambda r: r[1] - r[0])
        lo, hi = p + int(10 / HOP), min(q, p + int(MAX_S / HOP))
        picked = [(p, min(range(lo, hi), key=lambda k: db[k]))] if hi > lo else [(p, q)]
    return [pad(p, q) for p, q in picked]


def bounds(db, w):
    """去掉一段两头的静音（留 0.1 秒头、0.16 秒尾）。返回 (起始帧, 结束帧)。"""
    p, q = w
    thr = max(db) - 35
    inside = [k for k in range(p, q) if db[k] > thr]
    return (max(p, inside[0] - 5), min(q, inside[-1] + 8)) if inside else (p, q)


def cut(a, sr, db, w, out):
    """截出一段：去掉两头的静音，两头各淡入淡出 10 毫秒，音量拉到 -3 dB。返回 (起秒, 止秒)。"""
    p, q = bounds(db, w)
    h = int(sr * HOP)
    seg = a[p * h:q * h]
    peak = max(1, max(seg), -min(seg))
    g = 0.708 * 32767 / peak
    fade = int(sr * 0.01)
    n = len(seg)
    y = array.array("h", (int(max(-32768, min(32767, seg[k] * g * min(1, k / fade, (n - 1 - k) / fade)))) for k in range(n)))
    if sys.byteorder == "big":
        y.byteswap()
    write_wav(out, y.tobytes(), sr)
    return p * HOP, q * HOP


def transcribe(wav, asr):
    """用本机 SenseVoice 转写；它不出标点，按字和字之间的停顿补逗号，最后加句号。认不出返回空。"""
    exe, model, tokens = asr
    tmp = os.path.join(ascii_dir(), f"dfy-voice-{os.getpid()}.wav")
    try:
        shutil.copyfile(wav, tmp)
        r = subprocess.run([exe, "--num-threads=2", f"--tokens={tokens}", f"--sense-voice-model={model}",
                            "--sense-voice-use-itn=0", tmp], capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    for line in (r.stdout + b"\n" + r.stderr).decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("{") and '"text"' in line:
            try:
                j = json.loads(line)
            except ValueError:
                continue
            toks, ts = j.get("tokens") or [], j.get("timestamps") or []
            if not toks or len(toks) != len(ts):
                text = j.get("text", "")
            else:
                text = ""
                for k, t in enumerate(toks):
                    if k and ts[k] - ts[k - 1] > 0.45 and text and text[-1] not in "，。？！":
                        text = text.rstrip() + "，"
                    text += t.replace("▁", " ")
            text = re.sub(r"\s+", " ", text).strip()
            text = re.sub(r"\s+(?=[^\x00-\x7f])|(?<=[^\x00-\x7f])\s+", "", text)
            return text + "。" if text and text[-1] not in "，。？！" else text
    return ""


def prepare(path):
    """读录音、算停顿、给出可截的段。返回 (samples, sr, db, [段]) 或 None。"""
    ff = find_ffmpeg()
    a, sr = decode(path, ff)
    if a is None:
        return None
    db = frame_db(a, sr)
    ws = windows(db)
    if not ws:
        say("  ！这段录音听不到人声")
        return None
    dur = (ws[0][1] - ws[0][0]) * HOP
    if dur < 3:
        say(f"  ！有声音的部分只有 {dur:.1f} 秒，太短了。找一段 8 秒以上的再来。")
        return None
    return a, sr, db, ws


# ───────── 一问一答（双击 bat） ─────────
def design(name, persona):
    while True:
        say(f"\n声音描述：{persona}")
        say("出三个候选，每个要几秒到十几秒…")
        files = []
        for i in range(1, 4):
            out = os.path.join(OUT, f"候选{i}.wav")
            if speak({"input": DESIGN_TEXT, "instructions": persona, "cfg_scale": DESIGN_CFG, "seed": random.randint(1, 99999)}, out):
                files.append(out)
                say(f"  候选{i} 好了")
        if not files:
            return False
        for i, f in enumerate(files, 1):
            input(f"\n按回车听候选{i}…")
            play(f)
        while True:
            say("\n喜欢第几个？输入号码，先听她用这个声音在 QQ 里说话的样子；都不喜欢直接回车再出三个；想换个描述输入 d")
            pick = input("选：").strip().lower()
            if pick == "d":
                say("一句话写她是谁、什么情绪就行，比如「二十出头的女生，说话直爽利落，有点不耐烦」。写短一点，多出几个挑，比写长了好。")
                say("别写「生气时会……」这种某种情况才有的，也别写「离麦克风很近」：她会把每句话都念成那样。")
                persona = input("新的描述：").strip() or persona
                break
            if pick in ("1", "2", "3") and int(pick) <= len(files):
                ref = files[int(pick) - 1]
                say("试听中…")
                out = preview(ref, DESIGN_TEXT)
                if not out:
                    return False
                play(out)
                if input("就要这个？回车存下来；输入 n 回去重挑：").strip().lower() != "n":
                    return keep_and_save(ref, DESIGN_TEXT, name, persona)
                continue
            if not pick:
                break


def clone(name):
    path = input("\n把录音文件拖进这个窗口（或者粘贴路径），回车：").strip().strip('"').strip("'")
    if not os.path.isfile(path):
        say("  ！找不到这个文件")
        return False
    say("  读录音、找停顿…")
    got = prepare(path)
    if not got:
        return False
    a, sr, db, ws = got
    asr = find_asr()
    ref = os.path.join(OUT, "克隆-参考.wav")
    k = 0
    while True:
        t0, t1 = cut(a, sr, db, ws[k], ref)
        total = len(db) * HOP
        if t1 - t0 >= total - 0.5:
            say(f"  用整段（{t1 - t0:.1f} 秒）")
        else:
            say(f"  截了第 {t0:.1f}–{t1:.1f} 秒这段（{t1 - t0:.1f} 秒）" + ("，想换一段等会儿输入 n" if len(ws) > 1 else ""))
        if t1 - t0 < 5:
            say("  有点短，像的程度会差一点，最好 8 秒以上")
        play(ref)
        text = transcribe(ref, asr) if asr else ""
        if text:
            say(f"\n  听出来的原话：{text}")
            fix = input("  对的话直接回车；有错字就把整句改好（标点也带上）再回车：").strip()
            text = fix or text
        else:
            if not asr:
                say("  （没装本机语音识别，原话要自己打。装看视频时 Skill 会装它，装了以后这里会自动转写）")
            text = input("  这段录音里说的原话，一字不差打出来（带标点）：").strip()
        if not text:
            say("  ！原话不能空着，Breeze 要靠它对上每个字的发音")
            return False
        say("  试听中…")
        out = preview(ref, text)
        if not out:
            return False
        play(out)
        ans = input("\n满意就回车存下来；换一段录音输入 n；重打原话输入 t；不做了输入 q：").strip().lower()
        if ans == "q":
            return False
        if ans == "n":
            k = (k + 1) % len(ws)
            if len(ws) == 1:
                say("  这段录音只截得出这一段")
            continue
        if ans == "t":
            fix = input("  原话：").strip()
            if fix:
                out = preview(ref, fix)
                if out:
                    play(out)
                    if input("回车存下来；输入 q 不做了：").strip().lower() == "q":
                        return False
                    return keep_and_save(ref, fix, name, "克隆：" + os.path.basename(path), path)
            continue
        return keep_and_save(ref, text, name, "克隆：" + os.path.basename(path), path)


# ───────── 给 AI 用的三个命令 ─────────
def cmd_prep(args):
    pick = 1
    if "--pick" in args:
        i = args.index("--pick")
        pick = int(args[i + 1])
        del args[i:i + 2]
    if not args or not os.path.isfile(args[0]):
        say("用法：make_voice.py prep <录音> [--pick N]")
        return 1
    got = prepare(args[0])
    if not got:
        return 1
    a, sr, db, ws = got
    w = ws[min(max(pick, 1), len(ws)) - 1]
    ref = os.path.join(OUT, "克隆-参考.wav")
    t0, t1 = cut(a, sr, db, w, ref)
    say(f"参考音：{ref}（第 {t0:.1f}–{t1:.1f} 秒，{t1 - t0:.1f} 秒）")
    asr = find_asr()
    text = transcribe(ref, asr) if asr else ""
    say(f"原话：{text}" if text else ("原话：（没认出来，让他自己打）" if asr else "原话：（没装本机语音识别，让他自己打）"))
    if len(ws) > 1:
        say("能截的段（换段加 --pick 号码）：" + "；".join(
            f"{n} 第 {bounds(db, w)[0] * HOP:.1f}–{bounds(db, w)[1] * HOP:.1f} 秒" for n, w in enumerate(ws, 1)))
    return 0


def main():
    global BASE
    args = sys.argv[1:]
    api = ""
    if "--api" in args:
        i = args.index("--api")
        api = args[i + 1]
        del args[i:i + 2]
    if args and args[0].startswith("http"):
        api = args.pop(0)
    pack = json.load(open(os.path.join(PACK, "settings", "pack.json"), encoding="utf-8-sig"))
    b = pack["voice"]["breeze"]
    BASE = (api or b["api_base"]).rstrip("/")
    if BASE.endswith("/v1"):
        BASE = BASE[:-3]
    name = b["voice"]
    os.makedirs(OUT, exist_ok=True)

    if args and args[0] == "prep":
        return cmd_prep(args[1:])
    if args and args[0] in ("preview", "save"):
        if len(args) < 3 or not os.path.isfile(args[1]):
            say(f"用法：make_voice.py {args[0]} <wav> <原话>" + (" [--original <原录音>]" if args[0] == "save" else ""))
            return 1
        if args[0] == "preview":
            out = preview(args[1], args[2])
            say(f"试听：{out}" if out else "试听失败")
            return 0 if out else 1
        orig = args[args.index("--original") + 1] if "--original" in args else ""
        ok = keep_and_save(args[1], args[2], name, ("克隆：" + os.path.basename(orig)) if orig else "", orig)
        say(f"存好了，名字是 {name}" if ok else "没存成")
        return 0 if ok else 1

    say("给大肥鱼做一个本地声音（Breeze TTS 2）")
    code, out = request("GET", "/v1/voices", timeout=10)
    if code != 200:
        say(f"\n连不上 Breeze（{BASE}）。先双击桌面的「启动Breeze语音」，等黑窗口里不再刷字了再来。")
        say("还没装的话，先双击配置包里的「装Breeze语音.bat」。")
        return 1
    have = [v.get("id") for v in json.loads(out or b"[]") if v.get("saved")]
    if name in have:
        say(f"已经有一个叫 {name} 的声音了，再做一个会把它换掉（旧的参考音会留在 voice\\breeze 里）。")

    say("\n1 用一段话描述一个声音（不用录音，推荐先试这个）")
    say("2 用一段录音克隆（只有一个人说话、没有背景音乐；长一点没关系，会自动截一段）")
    way = input("选（直接回车是 1）：").strip() or "1"
    ok = clone(name) if way == "2" else design(name, b["persona"])
    if not ok:
        say("\n没存成，再双击一次「做声音.bat」重来。")
        return 1
    say(f"\n存好了，名字是 {name}。")
    say("第一次做的话，下一步双击「导入大肥鱼.bat」，语音那一步选 4。之前已经选过 4 的不用再导入，她下一条语音就是新声音。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
