"""Breeze 在线语音：复用 AstrBot 的 ElevenLabs 接口，整条短回复只合成一次。

密钥从 private 文件读；长回复、限流和服务故障保留原文字，不自动重试计费请求。
"""
import asyncio
import random
from pathlib import Path
from urllib.parse import urlparse

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star
from astrbot.core.provider import register as reg
from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.sources.elevenlabs_tts_source import ProviderElevenLabsTTSAPI
from astrbot.core.star.session_llm_manager import SessionServiceManager

TYPE = "breeze_online_tts"
DEFAULTS = {
    "id": "大肥鱼语音-Breeze在线", "type": TYPE, "provider": "breeze",
    "provider_type": "text_to_speech", "enable": False,
    "credentials_file": "", "api_base": "https://api.breeze.blue/v1",
    "model": "breeze-tts-2", "elevenlabs-tts-voice-id": "voc_u3nxkd5578wc",
    "elevenlabs-tts-output-format": "mp3_24000_128", "timeout": 45,
    "breeze_online_whole_reply": True, "breeze_online_reply_probability": 0.33,
    "breeze_online_max_chars": 120, "proxy": "",
}

old = reg.provider_cls_map.pop(TYPE, None)
if old in reg.provider_registry:
    reg.provider_registry.remove(old)


@reg.register_provider_adapter(
    TYPE, "Breeze 在线语音（不用显卡，按字扣积分）",
    provider_type=ProviderType.TEXT_TO_SPEECH,
    default_config_tmpl=DEFAULTS, provider_display_name="Breeze 在线版",
)
class BreezeOnlineTTS(ProviderElevenLabsTTSAPI):
    def __init__(self, provider_config: dict, provider_settings: dict):
        c = {**DEFAULTS, **provider_config}
        url = urlparse(c["api_base"])
        if (url.scheme != "https" or url.hostname not in ("api.breeze.blue", "api.breezeblue.ai")
                or url.path.rstrip("/") != "/v1" or url.query or url.fragment or url.username or url.password
                or url.port not in (None, 443)):
            raise ValueError("Breeze 地址应为官方 HTTPS API 的 /v1 地址")
        try:
            key = Path(c["credentials_file"]).read_text(encoding="utf-8-sig").strip()
        except (OSError, ValueError):
            raise ValueError("Breeze 密钥文件打不开，请检查 private 中的文件") from None
        if not key or any(x.isspace() for x in key):
            raise ValueError("Breeze 密钥文件应只放一行密钥")
        self.whole_reply = bool(c["breeze_online_whole_reply"])
        self.reply_probability = min(1.0, max(0.0, float(c["breeze_online_reply_probability"])))
        self.max_chars = min(300, max(1, int(c["breeze_online_max_chars"])))
        self._lock = asyncio.Lock()
        # 只给现成接口的内存副本，不把密钥写回提供商配置。
        super().__init__({**c, "api_key": key}, provider_settings)

    async def get_audio(self, text: str) -> str:
        if not text.strip() or len(text) > self.max_chars:
            raise ValueError("Breeze 这条回复较长，保留文字")
        # 同一实例一次只发一个计费请求；忙时不排长队、不重试。
        if self._lock.locked():
            raise RuntimeError("Breeze 正忙，这条保留文字")
        async with self._lock:
            try:
                return await super().get_audio(text)
            except Exception:
                # 服务端错误正文和请求对象可能带凭证，不传播到日志。
                raise RuntimeError("Breeze 合成未成功，请检查密钥、余额、并发或网络；这条保留文字") from None


class BreezeOnlinePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.on_decorating_result(priority=-1100)
    async def whole_reply_voice(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain or not result.is_llm_result():
            return
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        tts = cfg.get("provider_tts_settings") or {}
        # 只认当前会话选中的提供商，切回别的声音后不会插一条旧声音。
        pid = tts.get("provider_id")
        prov = next((p for p in self.context.get_all_tts_providers()
                     if isinstance(p, BreezeOnlineTTS) and p.meta().id == pid), None)
        if not prov or not prov.whole_reply:
            return
        if tts.get("enable"):
            logger.warning("[Breeze在线] 请关闭 AstrBot 语音总开关，整条语音由插件处理")
            return
        if any(isinstance(c, Record) for c in result.chain):
            return
        if not await SessionServiceManager.should_process_tts_request(event):
            return
        full = "\n".join(c.text.strip() for c in result.chain if isinstance(c, Plain) and c.text.strip())
        if not full or len(full) > prov.max_chars or random.random() > prov.reply_probability:
            return
        try:
            path = await prov.get_audio(full)
            if not path or not Path(path).is_file() or Path(path).stat().st_size == 0:
                return
        except Exception:
            logger.warning("[Breeze在线] 这条合成未成功，照常发文字（不自动重试）")
            return
        chain, placed = [], False
        for comp in result.chain:
            if isinstance(comp, Plain):
                if not placed:
                    chain.append(Record(file=path, url=path, text=full))
                    placed = True
            else:
                chain.append(comp)
        result.chain = chain
