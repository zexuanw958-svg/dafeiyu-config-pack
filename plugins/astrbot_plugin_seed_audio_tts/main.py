"""豆包音频生成模型 1.0（seed-audio-1.0）语音合成提供商。

接口：POST https://openspeech.bytedance.com/api/v3/tts/create
鉴权：豆包语音控制台旧版应用的 APP ID + Access Token（X-Api-App-Id / X-Api-Access-Key），
外加 X-Api-Resource-Id: seed-audio-1.0。火山方舟的 API Key 用不了。
计费：按生成音频的原始时长，1 元/分钟（重试的那次也算钱）。

整条语音（seed_audio_whole_reply = true，默认）：AstrBot 自带的语音会先把回复拆段、一段一段排队合成，
每段都要等十几秒，几段声音也不太一样。开了整条语音后由本插件掷骰子（seed_audio_reply_probability），
掷中时会发送整条语音并保留原文字作为兼容保底，没掷中照常发分段文字。此时要把 AstrBot 的语音总开关
（provider_tts_settings.enable）关掉，不然 AstrBot 还会另外按段转语音。
"""

import base64
import json
import os
import re
import time
import uuid

import random

import aiohttp

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.provider import register as _reg
from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.provider import TTSProvider
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

TYPE_NAME = "seed_audio_tts"
DEFAULT_PERSONA = "一个憨萌软糯的小女生，声音甜甜的、有点奶，十几岁的感觉，嘴馋爱撒娇，带一点小傲娇"
DEFAULT_TEMPLATE = "{persona}，在QQ上给好朋友发语音，语气轻快自然，只说引号里的这句话，一字不差：“{text}”"
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _norm(s: str) -> str:
    return _NON_WORD.sub("", s or "").lower()


def _read_env_file(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                out[k.strip()] = v.strip()
    return out


# 插件热重载时先摘掉旧的注册，避免「提供商类型已注册」报错。
_old = _reg.provider_cls_map.pop(TYPE_NAME, None)
if _old in _reg.provider_registry:
    _reg.provider_registry.remove(_old)


@_reg.register_provider_adapter(
    TYPE_NAME,
    "豆包 Seed Audio 1.0（火山引擎，用一句话描述人设和语气）",
    provider_type=ProviderType.TEXT_TO_SPEECH,
    default_config_tmpl={
        "id": "seed_audio_tts",
        "type": TYPE_NAME,
        "provider": "volcengine",
        "provider_type": "text_to_speech",
        "enable": False,
        "app_id": "",
        "access_key": "",
        "credentials_file": "",
        "api_base": "https://openspeech.bytedance.com/api/v3/tts/create",
        "resource_id": "seed-audio-1.0",
        "model": "seed-audio-1.0",
        "seed_audio_persona": DEFAULT_PERSONA,
        "seed_audio_prompt_template": DEFAULT_TEMPLATE,
        "seed_audio_format": "mp3",
        "seed_audio_sample_rate": 24000,
        "seed_audio_max_attempts": 2,
        "seed_audio_whole_reply": True,
        "seed_audio_reply_probability": 0.33,
        "timeout": 60,
    },
    provider_display_name="豆包 Seed Audio",
)
class ProviderSeedAudioTTS(TTSProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        c = provider_config
        self.app_id = str(c.get("app_id", "") or "")
        self.access_key = str(c.get("access_key", "") or "")
        cred = str(c.get("credentials_file", "") or "")
        if cred and not (self.app_id and self.access_key):
            env = _read_env_file(cred)
            self.app_id = self.app_id or env.get("VOLC_SPEECH_APP_ID", "")
            self.access_key = self.access_key or env.get("VOLC_SPEECH_ACCESS_TOKEN", "")
        self.api_base = c.get("api_base") or "https://openspeech.bytedance.com/api/v3/tts/create"
        self.resource_id = c.get("resource_id") or "seed-audio-1.0"
        self.model = c.get("model") or "seed-audio-1.0"
        self.persona = c.get("seed_audio_persona") or DEFAULT_PERSONA
        self.template = c.get("seed_audio_prompt_template") or DEFAULT_TEMPLATE
        self.fmt = c.get("seed_audio_format") or "mp3"
        self.sample_rate = int(c.get("seed_audio_sample_rate") or 24000)
        self.max_attempts = max(1, int(c.get("seed_audio_max_attempts") or 2))
        self.timeout = float(c.get("timeout") or 60)
        self.whole_reply = bool(c.get("seed_audio_whole_reply", False))
        prob = c.get("seed_audio_reply_probability", 0.33)
        self.reply_probability = float(0.33 if prob in (None, "") else prob)
        self.stats_path = os.path.join(get_astrbot_temp_path(), "seed_audio_calls.jsonl")

    def _prompt(self, text: str) -> str:
        line = text.replace("“", "「").replace("”", "」").replace('"', "")
        return self.template.format(persona=self.persona, text=line)

    async def _request(self, text: str) -> tuple[dict, float]:
        if not (self.app_id and self.access_key):
            raise Exception("Seed Audio 缺少 APP ID 或 Access Token")
        body = {
            "model": self.model,
            "text_prompt": self._prompt(text),
            "audio_config": {
                "format": self.fmt,
                "sample_rate": self.sample_rate,
                "enable_subtitle": True,
            },
        }
        headers = {
            "X-Api-App-Id": self.app_id,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }
        t0 = time.monotonic()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_base,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                raw = await resp.text()
                dt = time.monotonic() - t0
                try:
                    data = json.loads(raw)
                except Exception:
                    raise Exception(f"Seed Audio HTTP {resp.status}: {raw[:200]}")
                if resp.status != 200 or not data.get("audio"):
                    brief = {k: v for k, v in data.items() if k not in ("audio", "url")}
                    raise Exception(f"Seed Audio HTTP {resp.status}: {json.dumps(brief, ensure_ascii=False)[:300]}")
                return data, dt

    def _log(self, rec: dict) -> None:
        try:
            with open(self.stats_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def get_audio(self, text: str) -> str:
        target = _norm(text)
        if not target:
            raise Exception("这段没有可念的字")
        call_id = uuid.uuid4().hex[:8]
        last_err = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                data, dt = await self._request(text)
            except Exception as e:
                last_err = str(e)
                logger.warning(f"[SeedAudio] {call_id} 第 {attempt} 次请求失败：{last_err}")
                self._log({"ts": time.time(), "id": call_id, "attempt": attempt, "text": text, "ok": False, "error": last_err[:200]})
                continue
            sub = data.get("subtitle") or {}
            heard = sub.get("text") or "".join(s.get("text", "") for s in sub.get("sentences", []))
            dur = float(data.get("original_duration") or data.get("duration") or 0)
            match = _norm(heard) == target
            logger.info(
                f"[SeedAudio] {call_id} 第 {attempt} 次 {'台词一致' if match else '台词不一致'}，"
                f"耗时 {dt:.1f}s，音频 {dur:.2f}s，约 {dur / 60:.3f} 元，念的是：{heard}"
            )
            self._log({"ts": time.time(), "id": call_id, "attempt": attempt, "text": text, "heard": heard,
                       "match": match, "latency": round(dt, 2), "duration": dur, "ok": True})
            if not match:
                last_err = f"念成了「{heard}」"
                continue
            os.makedirs(get_astrbot_temp_path(), exist_ok=True)
            path = os.path.join(get_astrbot_temp_path(), f"seed_audio_{int(time.time() * 1000)}_{call_id}.{self.fmt}")
            with open(path, "wb") as f:
                f.write(base64.b64decode(data["audio"]))
            return path
        raise Exception(f"Seed Audio {self.max_attempts} 次都没成，改发文字（{last_err}）")

    async def test(self) -> None:
        """后台「测试」按钮：用一句中文试一次（AstrBot 默认念 "hi"，这个模型常念成「嗨」，会被判成台词不一致）。"""
        path = await self.get_audio("你好呀")
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise Exception("Seed Audio 测试没生成音频")


class SeedAudioTTSPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._warned_double = False

    def _whole_reply_provider(self):
        for p in self.context.get_all_tts_providers():
            if isinstance(p, ProviderSeedAudioTTS) and p.whole_reply:
                return p
        return None

    @filter.on_decorating_result(priority=-1000)
    async def whole_reply_voice(self, event: AstrMessageEvent):
        """掷中就把整条回复合成一条语音（在表情包等插件处理完文字之后、AstrBot 分段之前）。"""
        result = event.get_result()
        if result is None or not result.chain or not result.is_llm_result():
            return
        prov = self._whole_reply_provider()
        if prov is None:
            return
        try:
            tts_on = bool(self.context.get_config(umo=event.unified_msg_origin)["provider_tts_settings"]["enable"])
        except Exception:
            tts_on = False
        if tts_on and not self._warned_double:
            self._warned_double = True
            logger.warning("[SeedAudio] 整条语音已由本插件处理，请把 AstrBot 的语音总开关关掉，否则没掷中的回复还会被按段转语音")
        if not await SessionServiceManager.should_process_tts_request(event):
            return
        if random.random() > prov.reply_probability:
            return
        texts = [c.text.strip() for c in result.chain if isinstance(c, Plain) and c.text.strip()]
        if not texts:
            return
        full = ""
        for t in (x.strip() for x in "\n".join(texts).splitlines()):
            if not t:
                continue
            if full and full[-1] not in "，。！？、；：～…,.!?;:~":
                full += "，"
            full += t
        if len(_norm(full)) < 2:
            return
        t0 = time.monotonic()
        try:
            path = await prov.get_audio(full)
        except Exception as e:
            logger.warning(f"[SeedAudio] 整条语音没成，照常发文字：{e}")
            return
        logger.info(f"[SeedAudio] 整条语音 {time.monotonic() - t0:.1f}s：{full}")
        new_chain, placed = [], False
        for c in result.chain:
            if isinstance(c, Plain):
                if not placed:
                    new_chain.append(Record(file=path, url=path, text=full))
                    placed = True
                # Some QQ clients may render a Record segment as blank or
                # unsupported. Keep the original text so a successful TTS
                # conversion never makes the reply unreadable.
                new_chain.append(c)
                continue
            new_chain.append(c)
        result.chain = new_chain
