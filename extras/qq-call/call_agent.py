"""Windows QQ 原生通话语音循环（轮次式 MVP）。

流程：QQ 通话声音 -> WASAPI loopback -> VAD -> 本地 ASR -> OpenAI 兼容 LLM
-> 可配置 TTS -> 播放到虚拟声卡（QQ 麦克风）。

不包含来电自动接听；自动接听见 auto_answer.py（默认关闭）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import httpx
import numpy as np
import soundcard as sc
import soundfile as sf
import webrtcvad


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def clean_speech(text: str) -> str:
    s = text or ""
    s = re.sub(r"&&[^&]{1,40}&&", "", s)
    s = re.sub(r"[*_`#>]+", "", s)
    s = re.sub(r"[（(][^（）()]{1,24}[）)]", "", s)
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", s)
    s = s.replace("……", "，").replace("…", "，")
    s = re.sub(r"\s*\n+\s*", "。", s)
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "，", s)
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"[。]{2,}", "。", s)
    s = re.sub(r"^[，。、\s]+", "", s)
    s = re.sub(r"[，、]+([。！？])", r"\1", s)
    return s.strip() or (text or "").strip()


class TTSProvider:
    def __init__(self, config: dict):
        self.config = config

    def synth(self, text: str, out_mp3: Path) -> Path:
        raise NotImplementedError

    def _write(self, content: bytes, out_mp3: Path) -> Path:
        if not content:
            raise RuntimeError("TTS 返回了空音频")
        out_mp3.write_bytes(content)
        return out_mp3


class EdgeTTS(TTSProvider):
    def synth(self, text: str, out_mp3: Path) -> Path:
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("未安装 edge-tts，请先 pip install -r requirements.txt") from exc
        conf = self.config["tts"]["edge_tts"]

        async def run() -> None:
            communicate = edge_tts.Communicate(
                text,
                voice=conf.get("voice") or "zh-CN-XiaoxiaoNeural",
                rate=conf.get("rate") or "+0%",
                volume=conf.get("volume") or "+0%",
            )
            await communicate.save(str(out_mp3))

        asyncio.run(run())
        if not out_mp3.is_file() or out_mp3.stat().st_size == 0:
            raise RuntimeError("Edge TTS 没有生成音频")
        return out_mp3


class BreezeOnlineTTS(TTSProvider):
    def synth(self, text: str, out_mp3: Path) -> Path:
        conf = self.config["tts"]["breeze_online"]
        if not conf.get("api_key") or not conf.get("voice_id"):
            raise RuntimeError("breeze_online 需要 api_key 和 voice_id；本仓库不附带任何声音")
        base = (conf.get("base_url") or "https://api.breeze.blue/v1").rstrip("/")
        headers = {
            "xi-api-key": conf["api_key"],
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        payload = {"text": text, "model_id": conf.get("model") or "breeze-tts-2"}
        with httpx.Client(
            timeout=120,
            proxy=conf.get("proxy") or None,
            trust_env=False,
        ) as client:
            resp = client.post(
                f"{base}/text-to-speech/{conf['voice_id']}",
                params={"output_format": conf.get("output_format") or "mp3_24000_128"},
                headers=headers,
                json=payload,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Breeze 在线 TTS 失败：{resp.status_code} {resp.text[:200]}")
        return self._write(resp.content, out_mp3)


class BreezeLocalTTS(TTSProvider):
    """调用本地 Breeze 服务的通用 HTTP 适配器。

    约定：POST endpoint，JSON {"text": ..., "voice": ...}，响应体为音频字节。
    不同本地 Breeze 发行版的 API 不同，需要按实际接口调整 endpoint/字段。
    """

    def synth(self, text: str, out_mp3: Path) -> Path:
        conf = self.config["tts"]["breeze_local"]
        endpoint = conf.get("endpoint")
        if not endpoint:
            raise RuntimeError("breeze_local 需要 endpoint")
        with httpx.Client(timeout=float(conf.get("timeout") or 120), trust_env=False) as client:
            resp = client.post(endpoint, json={"text": text, "voice": conf.get("voice") or ""})
        if resp.status_code != 200:
            raise RuntimeError(f"本地 Breeze 失败：{resp.status_code} {resp.text[:200]}")
        return self._write(resp.content, out_mp3)


def make_tts(config: dict) -> TTSProvider:
    provider = (config.get("tts") or {}).get("provider") or "edge_tts"
    if provider == "breeze_online":
        return BreezeOnlineTTS(config)
    if provider == "breeze_local":
        return BreezeLocalTTS(config)
    if provider == "edge_tts":
        return EdgeTTS(config)
    raise ValueError(f"未知 TTS provider: {provider}")


def ffmpeg_bin(config: dict) -> str:
    value = (config.get("ffmpeg") or "").strip()
    if value and Path(value).is_file():
        return value
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("找不到 ffmpeg，请在 config.json 里填 ffmpeg 路径")
    return found


def to_wav(config: dict, src: Path, dst: Path, gain: float = 1.0) -> Path:
    subprocess.run(
        [ffmpeg_bin(config), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(src), "-af", f"volume={gain:.2f}", "-ar", "48000", "-ac", "1", str(dst)],
        check=True,
    )
    return dst


def play_wav(config: dict, wav: Path) -> None:
    data, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    name = (config.get("audio") or {}).get("virtual_speaker") or ""
    speaker = sc.get_speaker(name) if name else sc.default_speaker()
    speaker.play(data, samplerate=sr)


def ask_llm(config: dict, text: str) -> str:
    llm = config["llm"]
    base = (llm.get("base_url") or "").rstrip("/")
    key = llm.get("api_key") or ""
    if not base or not key:
        raise RuntimeError("llm.base_url / llm.api_key 未配置")
    body = {
        "model": llm.get("model") or "deepseek-chat",
        "messages": [
            {"role": "system", "content": llm.get("system_prompt") or "你是一个简洁、自然的语音助手。"},
            {"role": "user", "content": text},
        ],
        "max_tokens": int(llm.get("max_tokens") or 160),
    }
    with httpx.Client(timeout=60, trust_env=False) as client:
        resp = client.post(
            base + "/chat/completions",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
    return str(data["choices"][0]["message"].get("content") or "").strip()


def transcribe(config: dict, wav: Path) -> str:
    asr = config["asr"]
    sherpa = Path(asr.get("sherpa_exe") or "")
    model = Path(asr.get("model") or "")
    tokens = Path(asr.get("tokens") or "")
    if not sherpa.is_file() or not model.is_file() or not tokens.is_file():
        raise RuntimeError("asr.sherpa_exe / model / tokens 未配置或文件不存在")

    audio, _ = sf.read(str(wav), dtype="float32", always_2d=True)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    gain = min(20.0, max(1.0, 0.5 / max(peak, 1e-4)))
    wav16 = wav.with_name(wav.stem + "_16k.wav")
    to_wav(config, wav, wav16, gain)
    result = subprocess.run(
        [str(sherpa), f"--tokens={tokens}", f"--sense-voice-model={model}",
         f"--sense-voice-language={asr.get('language') or 'zh'}",
         "--sense-voice-use-itn=true",
         f"--num-threads={int(asr.get('threads') or 4)}", str(wav16)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        text = str(obj.get("text") or "").strip()
        if text:
            return text
    return ""


def record_utterance(config: dict) -> np.ndarray:
    audio_conf = config.get("audio") or {}
    sample_rate = int(audio_conf.get("sample_rate") or 48000)
    silence_frames = max(8, int((float(audio_conf.get("vad_silence_ms") or 700) / 1000) * sample_rate / 480))
    max_seconds = float(audio_conf.get("max_utterance_seconds") or 20)
    speaker_name = (audio_conf.get("loopback_speaker") or "").strip()
    speaker = sc.get_speaker(speaker_name) if speaker_name else sc.default_speaker()
    mic = sc.get_microphone(speaker.name, include_loopback=True)
    vad = webrtcvad.Vad(2)
    frame_samples = int(sample_rate * 0.03)
    pre = deque(maxlen=8)
    frames: list[np.ndarray] = []
    speaking = False
    silence = 0
    speech = 0
    started = 0.0
    print(f"[listen] loopback={speaker.name}", flush=True)
    with mic.recorder(samplerate=sample_rate) as recorder:
        while True:
            block = recorder.record(numframes=frame_samples)
            mono = block[:, 0]
            pcm = (np.clip(mono, -1, 1) * 32767).astype(np.int16).tobytes()
            is_speech = vad.is_speech(pcm, sample_rate)
            if not speaking:
                pre.append(block)
                if is_speech:
                    speaking = True
                    started = time.time()
                    frames = list(pre)
                    speech = 1
                    silence = 0
                    print("[vad] speech start", flush=True)
                continue
            frames.append(block)
            if is_speech:
                speech += 1
                silence = 0
            else:
                silence += 1
            if silence >= silence_frames and speech >= 8:
                print(f"[vad] speech end frames={speech}", flush=True)
                return np.concatenate(frames, axis=0)
            if time.time() - started > max_seconds:
                print("[vad] max utterance", flush=True)
                return np.concatenate(frames, axis=0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--test-llm", action="store_true")
    parser.add_argument("--test-tts", action="store_true")
    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.is_file():
        raise SystemExit(f"找不到 {config_path}，先复制 config.example.json")
    config = load_config(config_path)
    base = config_path.resolve().parent

    if args.test_llm:
        print("LLM:", ask_llm(config, "只回一个字：好。"))
        return
    if args.test_tts:
        tts = make_tts(config)
        mp3 = base / "test-tts.mp3"
        wav = base / "test-tts.wav"
        tts.synth("语音链路测试。", mp3)
        to_wav(config, mp3, wav)
        play_wav(config, wav)
        print("TTS OK", wav)
        return

    tts = make_tts(config)
    print("QQ call agent 已启动；Ctrl+C 结束。", flush=True)
    count = 0
    while True:
        audio = record_utterance(config)
        count += 1
        wav = base / f"utterance-{count}.wav"
        sf.write(str(wav), audio, int((config.get("audio") or {}).get("sample_rate") or 48000))
        t0 = time.time()
        text = transcribe(config, wav)
        print(f"[asr] {time.time()-t0:.2f}s text={text!r}", flush=True)
        if not text:
            continue
        reply = ask_llm(config, text)
        print(f"[llm] {reply!r}", flush=True)
        if not reply:
            continue
        clean = clean_speech(reply)
        if not clean:
            continue
        mp3 = base / f"reply-{count}.mp3"
        wav_reply = base / f"reply-{count}.wav"
        t0 = time.time()
        tts.synth(clean, mp3)
        to_wav(config, mp3, wav_reply)
        print(f"[tts] {time.time()-t0:.2f}s", flush=True)
        play_wav(config, wav_reply)


if __name__ == "__main__":
    main()
