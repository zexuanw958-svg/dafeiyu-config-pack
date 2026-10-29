"""大肥鱼回复防串台：模型偶尔会把内部标签当成回复吐出来（10-25 正式实例：「才没有 我在研究今天午饭吃什么
这算正事 <system_reminder>User ID: …, Nickname: …</｜DSML｜parameter> </｜DSML｜invoke> …」）。

三处一起清（规则见 clean.py）：
1. on_llm_request：发给模型的历史里，之前漏进去的助手回复先洗干净，免得她照着学；
2. on_agent_done：这一轮她的回复存进聊天记录前洗干净；
3. on_decorating_result：发出去之前最后洗一遍（排在表情包、语音、分段前面）。

她知道自己会发语音（10-28 加，放这里是因为这个插件人人都装，看视频插件是选装的）：
4. on_llm_request：系统提示末尾加一段：语音开着（AstrBot 的语音开关，或者豆包整条语音）是「你会发语音」，
   没开是「你现在不发语音」。每轮按开关现读，人格文件里不写死，换成自己的角色、后台改了开关都跟着变；
5. after_message_sent：这一轮真念成语音发出去了，记下念的是什么；下一轮在用户消息后面补一行
   「[你上一条回复是念成语音发出去的……]」，跟着存进聊天记录。
   只认 AstrBot 分段回复发出去的语音（配置包默认开分段）；关了分段，语音会在发送时被单独拆走，这里看不到。
"""

import time
import uuid

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.star.session_llm_manager import SessionServiceManager

from .clean import clean, dirty, fold_lone_punct, strip_markers
from .voice import voice_note, voice_prompt

VOICE_NOTE_TTL = 24 * 3600


def _rate(value, default=0.0) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default


def _clean_content(content):
    """content 可能是字符串、dict 列表（OpenAI 格式）或 TextPart 列表。返回 (新 content, 改没改)。"""
    if isinstance(content, str):
        new = clean(content)
        return new, new != content
    changed = False
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and dirty(part["text"]):
                part["text"] = clean(part["text"])
                changed = True
            elif isinstance(getattr(part, "text", None), str) and dirty(part.text):
                part.text = clean(part.text)
                changed = True
    return content, changed


class DafeiyuClean(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.voiced = {}  # 会话 → 上一轮念成语音的那几句，下一轮告诉她

    async def _voice_rate(self, event: AstrMessageEvent) -> float:
        """这个会话里回复变成语音的概率；没开语音是 0。"""
        if not await SessionServiceManager.should_process_tts_request(event):
            return 0.0
        umo = event.unified_msg_origin
        tts = self.context.get_config(umo=umo).get("provider_tts_settings") or {}
        rate = 0.0
        if tts.get("enable") and await self.context.get_using_tts_provider_async(umo):
            rate = _rate(tts.get("trigger_probability", 1), 1.0)
        # 豆包整条语音（配置包的 seed_audio_tts 插件）自己掷骰子，AstrBot 的语音开关是关着的
        for prov in self.context.get_all_tts_providers():
            if getattr(prov, "whole_reply", False):
                rate = max(rate, _rate(getattr(prov, "reply_probability", 0)))
        return rate

    @filter.on_llm_request(priority=10)
    async def tell_voice(self, event: AstrMessageEvent, req):
        cid = getattr(getattr(req, "conversation", None), "cid", None)
        event.set_extra("_dafeiyu_turn", (uuid.uuid4().hex, cid))
        try:
            rate = await self._voice_rate(event)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dafeiyu_clean] 没读出语音开没开，这次不跟她提语音：{e}")
            rate = None
        if rate is not None:
            req.system_prompt = (req.system_prompt or "") + voice_prompt(rate)
        last = self.voiced.pop(event.unified_msg_origin, None)
        if (last and time.time() - last["t"] < VOICE_NOTE_TTL
                and (not last["cid"] or not cid or last["cid"] == cid)):
            req.extra_user_content_parts.append(TextPart(text=voice_note(last["said"])))
            logger.info("[dafeiyu_clean] 告诉她上一条是念成语音发出去的")

    @filter.after_message_sent()
    async def remember_voice(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain or not result.is_llm_result():
            return
        said = [c.text.strip() for c in result.chain
                if isinstance(c, Record) and isinstance(getattr(c, "text", None), str) and c.text.strip()]
        if not said:
            return
        turn, cid = event.get_extra("_dafeiyu_turn") or (None, None)
        umo = event.unified_msg_origin
        last = self.voiced.get(umo)
        if last and turn and last["turn"] == turn:
            last["said"] += said  # 同一轮里先后发了几次（中间调了工具），接着记
        else:
            self.voiced[umo] = {"turn": turn, "cid": cid, "t": time.time(), "said": said}
        if len(self.voiced) > 500:
            for k in sorted(self.voiced, key=lambda k: self.voiced[k]["t"])[:100]:
                self.voiced.pop(k, None)

    @filter.on_llm_request(priority=100000)
    async def clean_history(self, event: AstrMessageEvent, req):
        n = 0
        for msg in getattr(req, "contexts", None) or []:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                msg["content"], changed = _clean_content(msg.get("content"))
                n += changed
        if n:
            logger.info(f"[dafeiyu_clean] 历史里 {n} 条回复带内部标签，发给模型前洗掉了")

    @filter.on_llm_response(priority=100000)
    async def clean_response(self, event: AstrMessageEvent, resp):
        text = getattr(resp, "completion_text", None)
        if isinstance(text, str) and dirty(text):
            try:
                resp.completion_text = clean(text)
            except Exception:
                pass

    @filter.on_agent_done(priority=100000)
    async def clean_saved(self, event: AstrMessageEvent, run_context, llm_response):
        messages = getattr(run_context, "messages", None) or []
        for msg in reversed(messages):
            if getattr(msg, "role", None) == "assistant":
                content, changed = _clean_content(msg.content)
                if changed:
                    msg.content = content
                    logger.info("[dafeiyu_clean] 这条回复带内部标签，存聊天记录前洗掉了")
                break

    @filter.on_decorating_result(priority=100000)
    async def clean_outgoing(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain:
            return
        new_chain, hit, tagged = [], False, False
        for comp in result.chain:
            if isinstance(comp, Plain):
                text = clean(comp.text) if dirty(comp.text) else comp.text
                if text != comp.text:
                    hit = True
                text2 = strip_markers(text)
                if text2 != text:
                    tagged = True
                    hit = True
                comp.text = text2
                if not comp.text:
                    continue
            new_chain.append(comp)
        if hit:
            result.chain = new_chain
        try:
            voiced = await self._voice_rate(event) > 0
        except Exception:  # noqa: BLE001
            voiced = False
        if voiced:
            for comp in result.chain:
                if isinstance(comp, Plain) and (t := fold_lone_punct(comp.text)) != comp.text:
                    comp.text = t
                    logger.info("[dafeiyu_clean] 单独一段的标点（「……」之类）接到上一句或缩成一个字，免得被念成语音")
        if hit:
            logger.warning(
                "[dafeiyu_clean] 回复里漏了内部标签（system_reminder / DSML）"
                + ("或表情包暗号" if tagged else "")
                + "，发出去前删掉了"
            )
