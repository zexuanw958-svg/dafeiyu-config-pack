"""看视频用到的 ffmpeg 和转写，不依赖 AstrBot，可以单独拿一段视频试：
python video.py <视频> [ffmpeg路径] [本机识别文件夹]
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_ffmpeg(configured: str = "") -> str:
    """按顺序找：设置里填的、PATH、winget 装的位置。都没有返回空串。"""
    cands = [configured] if configured else []
    which = shutil.which("ffmpeg")
    if which:
        cands.append(which)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(os.path.join(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


async def _run(args, timeout=120):
    def go():
        return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)

    return await asyncio.to_thread(go)


async def probe(ffmpeg: str, path: str) -> tuple[float, bool]:
    """返回 (时长秒数, 有没有声音)。ffmpeg -i 不带输出会报错退出，信息在 stderr 里。"""
    p = await _run([ffmpeg, "-hide_banner", "-i", path], timeout=30)
    err = p.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    return dur, bool(re.search(r"Stream #\S+.*Audio:", err))


def pick_times(duration: float, max_seconds: int, max_frames: int) -> list[float]:
    """在看的那段里均匀取点：大约每 5 秒一张，至少 3 张，最多 max_frames 张。"""
    span = min(duration, max_seconds) if duration > 0 else 0
    if span <= 0:
        return [0.0]
    n = max(3, min(max_frames, round(span / 5)))
    n = min(n, max_frames)
    return [round(span * (i + 0.5) / n, 2) for i in range(n)]


async def grab_frames(ffmpeg: str, path: str, times: list[float], out_dir: str) -> list[tuple[float, str]]:
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for i, t in enumerate(times):
        out = os.path.join(out_dir, f"frame_{i + 1}.jpg")
        # -ss 放在 -i 前面是快速跳转；长边缩到 768，DeepSeek 一张图最多按 1024 token 算，再大也不多看
        vf = "scale='if(gt(iw,ih),min(768,iw),-2)':'if(gt(iw,ih),-2,min(768,ih))'"
        await _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", path,
                    "-frames:v", "1", "-vf", vf, "-q:v", "4", "-y", out])
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            frames.append((t, out))
    return frames


async def grab_audio(ffmpeg: str, path: str, max_seconds: int, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "audio.mp3")
    await _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path, "-t", str(max_seconds),
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-y", out])
    return out if os.path.isfile(out) and os.path.getsize(out) > 0 else ""


ASR_EXE = "sherpa-onnx-offline" + (".exe" if os.name == "nt" else "")
ASR_FILES = (ASR_EXE, "model.int8.onnx", "tokens.txt")
# 增强版 Fun-ASR-Nano（install-asr.ps1 -Plus 装的），在 asr 文件夹下的 funasr-nano 里
PLUS_FILES = ("encoder_adaptor.int8.onnx", "llm.int8.onnx", "embedding.int8.onnx", os.path.join("Qwen3-0.6B", "tokenizer.json"))
CHUNK_SECONDS = 15


def find_asr(configured: str, ffmpeg: str) -> str:
    """本机语音识别（install-asr.ps1 装的）所在文件夹：设置里填的，或者 ffmpeg 旁边的 asr 文件夹。没有返回空串。"""
    cands = [configured] if configured else []
    if ffmpeg:  # C:\dafeiyu\ffmpeg\bin\ffmpeg.exe → C:\dafeiyu\asr
        cands.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(ffmpeg))), "asr"))
    for c in cands:
        if c and all(os.path.isfile(os.path.join(c, f)) for f in ASR_FILES):
            return c
    return ""


def has_plus(asr_dir: str) -> bool:
    return all(os.path.isfile(os.path.join(asr_dir, "funasr-nano", f)) for f in PLUS_FILES)


async def grab_chunks(ffmpeg: str, path: str, max_seconds: int, out_dir: str) -> list[str]:
    """转成 16k 单声道 wav，每 15 秒切一段，不按停顿断句：断句模型遇到带伴奏的歌、说唱一段都切不出来（10-27 实测）。
    15 秒是试出来的：10 秒长歌最好、25 秒电话杂音最好，平均差不多。开头垫半秒静音，第一个字不容易丢。"""
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("asr_") and f.endswith(".wav"):
            os.remove(os.path.join(out_dir, f))
    await _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path, "-t", str(max_seconds), "-vn",
                "-af", "adelay=500:all=1,apad=pad_dur=0.5", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-y", os.path.join(out_dir, "asr_%03d.wav")])
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir)
                  if f.startswith("asr_") and f.endswith(".wav") and os.path.getsize(os.path.join(out_dir, f)) > 1000)


def clean(text: str) -> str:
    """大模型做的识别（增强版）偶尔会接着往下编：吐出 <|endoftext|> 后面跟一段英文，或者只吐一个 system。"""
    text = re.sub(r"<\|[^|>]*\|>", "", text.split("<|endoftext|>")[0])
    text = re.sub(r"^\s*(system|assistant|user|human)\b\s*[:：]?\s*", "", text, flags=re.I).strip()
    return "" if text.lower() in ("system", "assistant", "user", "human") else text


async def _recognize(asr_dir: str, chunks: list[str], plus: bool) -> str:
    d, n = asr_dir, os.path.join(asr_dir, "funasr-nano")
    if plus:
        model = [f"--funasr-nano-encoder-adaptor={os.path.join(n, 'encoder_adaptor.int8.onnx')}",
                 f"--funasr-nano-llm={os.path.join(n, 'llm.int8.onnx')}",
                 f"--funasr-nano-embedding={os.path.join(n, 'embedding.int8.onnx')}",
                 f"--funasr-nano-tokenizer={os.path.join(n, 'Qwen3-0.6B')}"]
    else:
        # 语言固定 zh：自动判语言时短句容易判成粤语（「锻炼出来」认成「对炼出嚟」）；固定后英文、粤语照样认得对
        model = [f"--tokens={os.path.join(d, 'tokens.txt')}", f"--sense-voice-model={os.path.join(d, 'model.int8.onnx')}",
                 "--sense-voice-use-itn=1", "--sense-voice-language=zh"]
    threads = max(1, min(4, os.cpu_count() or 1))  # 8 线程不比 4 线程快（10-27 实测）
    # 增强版一分钟音频要认 40 秒左右，慢电脑再翻倍也够
    p = await _run([os.path.join(d, ASR_EXE), f"--num-threads={threads}", *model, *chunks],
                   timeout=60 + 3 * CHUNK_SECONDS * len(chunks))
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    text = ""
    for line in out.splitlines():  # 每段一行 JSON，顺序和传进去的文件一样
        if not line.startswith("{"):
            continue
        try:
            seg = clean(str(json.loads(line).get("text") or ""))
        except ValueError:
            continue
        if seg:
            text += ("" if not text or text[-1] in "，。！？、,.!?…" else "，") + seg
    if not text and p.returncode != 0:
        raise RuntimeError(f"识别程序退出码 {p.returncode}：{out.strip()[-200:]}")
    return text


async def transcribe_local(asr_dir: str, chunks: list[str]) -> tuple[str, str]:
    """返回 (文字, 用的哪个)。装了增强版先用增强版，没认出字或出错再用 SenseVoice。"""
    which = "SenseVoice"
    if has_plus(asr_dir):
        try:
            text = await _recognize(asr_dir, chunks, True)
            if text:
                return text, "增强版"
            which = "SenseVoice（增强版没认出字）"
        except Exception as e:
            which = f"SenseVoice（增强版出错：{str(e)[:80]}）"
    return await _recognize(asr_dir, chunks, False), which


async def transcribe(audio: str, key: str, model: str) -> str:
    import aiohttp

    form = aiohttp.FormData()
    form.add_field("model", model)
    with open(audio, "rb") as f:
        form.add_field("file", f.read(), filename="audio.mp3", content_type="audio/mpeg")
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.siliconflow.cn/v1/audio/transcriptions", data=form,
                          headers={"Authorization": f"Bearer {key}"},
                          timeout=aiohttp.ClientTimeout(total=90)) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status}: {str(data)[:200]}")
    # SenseVoice 会在开头带 <|zh|><|NEUTRAL|> 这类标签
    return re.sub(r"<\|[^|>]*\|>", "", str(data.get("text") or "")).strip()


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


if __name__ == "__main__":
    async def demo():
        ff = find_ffmpeg(sys.argv[2] if len(sys.argv) > 2 else "")
        print("ffmpeg:", ff or "没找到")
        dur, has_audio = await probe(ff, sys.argv[1])
        times = pick_times(dur, 180, 6)
        print(f"时长 {dur:.1f} 秒，有声音：{has_audio}，截图时间点：{times}")
        out = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "frames")
        print(await grab_frames(ff, sys.argv[1], times, out))
        asr = find_asr(sys.argv[3] if len(sys.argv) > 3 else "", ff)
        if has_audio and asr:
            print("本机识别：", await transcribe_local(asr, await grab_chunks(ff, sys.argv[1], 180, out)))
        elif has_audio:
            print("音频：", await grab_audio(ff, sys.argv[1], 180, out))

    asyncio.run(demo())
