"""大肥鱼表情包记账：没真发出去的表情包，不留在聊天记录里。

10-28 一位用户的截图：她一张图没发，却说「你又不是没看过我那张」「就我摊着肚皮那张」，追问才认「我瞎说的」。
原因（AstrBot 4.29.3 + meme_manager 5.2.9，测试机复现过）：
- 她想配图时在回复末尾写 &&分类&&。AstrBot 先把这份原话存进这一轮的聊天记录，然后才轮到 meme_manager
  把标记从要发的字里删掉、按概率（配置包默认 60%）决定发不发图。分类里没图、发送出错，也会没发。
- 所以聊天记录里每个标记都在，没发出去的也在。复现时 24 轮里 18 轮带标记、只发出 8 张图；
  她看着自己的记录以为发过的比实际多，被问「哪张」就说起「躺着的那个」，对得上的是一条带了标记却没发出去的回复。

这里分三步，只改这一轮存进聊天记录的那条回复，不碰发出去的消息：
1. on_agent_done（meme_manager 已认完标记、掷完骰子）：先把标记全删掉，原话记在本轮事件上；
2. on_decorating_result（排在 meme_manager 加图之后）：跟字一起发的图，把对应分类的标记放回去；
   要单独补发的图（配置包默认就是这种）先不放，记下来；
3. after_message_sent（排在 meme_manager 补发之后）：看补发真成功了几张，再把那几个分类的标记放回去。
   补发成没成功，是给 meme_manager 的补发函数套一层记数拿到的；它哪天改了函数名，套不上，就按「会发出去」算。
没走到发送这一步（出错、被打断）的，标记一直是删掉的状态。
流式输出时 meme_manager 在它自己的钩子里直接发图，这里看不到，按它掷骰子的结果估：中了就留第一个分类。

对方点名要表情包（「给我发个表情包」）时不掷骰子，她选了图就一定发（10-29 实测：她回「拿去」，图被概率刷掉了）。
"""

import re

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, ResultContentType, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star

from .settle import settle, tags

KEY = "_dafeiyu_memelog"
SENT_OK = "_dafeiyu_memelog_sent_ok"
SEND_FN = "_send_meme_image"  # meme_manager 5.2.9 补发表情包用的函数
ASK = re.compile(r"表情包|斗图|(发|来|整)(个|张|点)?表情")
ASK_NOT = re.compile(r"(别|不要|少|不用)(再)?(发|来)?(个|张|点)?表情")


def _last_assistant(run_context):
    for msg in reversed(getattr(run_context, "messages", None) or []):
        if getattr(msg, "role", None) == "assistant":
            return msg
    return None


def _text_slots(msg):
    """这条消息里能改的文字：[(取, 存)]。content 可能是字符串、TextPart 列表或 dict 列表。"""
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return [(lambda: msg.content, lambda v: setattr(msg, "content", v))]
    slots = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                slots.append((lambda p=part: p["text"], lambda v, p=part: p.__setitem__("text", v)))
            elif isinstance(getattr(part, "text", None), str):
                slots.append((lambda p=part: p.text, lambda v, p=part: setattr(p, "text", v)))
    return slots


def _apply(rec, keep, quiet=False):
    for text, put in rec["slots"]:
        put(settle(text, keep))
    dropped = [c for c in rec["chosen"] if c not in keep]
    if dropped and not quiet:
        logger.info(f"[dafeiyu_memelog] 这轮想配的表情包 {dropped} 没发出去，聊天记录里不留")


class DafeiyuMemeLog(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    def _count_meme_sends(self) -> bool:
        """给 meme_manager 的补发函数套一层：每成功发出一张，在这条消息的事件上记一笔。套上了返回 True。"""
        meta = self.context.get_registered_star("meme_manager")
        inst = getattr(meta, "star_cls", None) if meta else None
        fn = getattr(inst, SEND_FN, None) if inst else None
        if fn is None:
            return False
        if getattr(fn, "_dafeiyu_memelog", False):
            return True

        async def counted(event, image, *args, **kwargs):
            result = await fn(event, image, *args, **kwargs)
            try:
                event.set_extra(SENT_OK, (event.get_extra(SENT_OK) or 0) + 1)
            except Exception:  # noqa: BLE001
                pass
            return result

        counted._dafeiyu_memelog = True
        setattr(inst, SEND_FN, counted)
        return True

    @filter.on_llm_response(priority=99998)  # meme_manager 认标记、掷骰子在 99999，这里接在它后面
    async def asked_for_meme(self, event: AstrMessageEvent, resp):
        text = event.message_str or ""
        if not event.get_extra("found_emotions") or not ASK.search(text) or ASK_NOT.search(text):
            return
        if not event.get_extra("meme_manager_legacy_probability_hit"):
            event.set_extra("meme_manager_legacy_probability_hit", True)
            logger.info("[dafeiyu_memelog] 对方点名要表情包，这张不掷骰子，直接发")

    @filter.on_agent_done(priority=-100)
    async def hold_tags(self, event: AstrMessageEvent, run_context, llm_response):
        event.set_extra(KEY, None)
        msg = _last_assistant(run_context)
        if msg is None:
            return
        slots, found = [], []
        for get, put in _text_slots(msg):
            text = get()
            ts = tags(text)
            if ts:
                slots.append((text, put))
                found += [t for t in ts if t not in found]
        if not slots:
            return
        for text, put in slots:
            put(settle(text, []))
        chosen = event.get_extra("found_emotions")
        chosen = [c for c in chosen if c in found] if isinstance(chosen, (list, tuple)) else []
        event.set_extra(KEY, {
            "slots": slots,
            "chosen": chosen,
            "hit": bool(event.get_extra("meme_manager_legacy_probability_hit")),
            "inline": 0,
        })

    @filter.on_decorating_result(priority=99990)
    async def settle_inline(self, event: AstrMessageEvent):
        rec = event.get_extra(KEY)
        if not rec:
            return
        result = event.get_result()
        if result is None:
            return
        typ = result.result_content_type
        if typ == ResultContentType.STREAMING_RESULT:
            return  # 等流式结束那一次
        if typ == ResultContentType.STREAMING_FINISH:
            event.set_extra(KEY, None)
            _apply(rec, rec["chosen"][:1] if rec["hit"] else [])
            return
        inline = sum(isinstance(c, Image) for c in (result.chain or []))
        pending = len(event.get_extra("meme_manager_pending_images") or [])
        if pending and self._count_meme_sends():
            rec["inline"] = inline
            event.set_extra(SENT_OK, 0)
            _apply(rec, rec["chosen"][:inline], quiet=True)  # 补发的那几张，等发完再看
            return
        event.set_extra(KEY, None)
        _apply(rec, rec["chosen"][:inline + pending])

    @filter.after_message_sent(priority=-100)
    async def settle_pending(self, event: AstrMessageEvent):
        rec = event.get_extra(KEY)
        if not rec:
            return
        event.set_extra(KEY, None)
        ok = event.get_extra(SENT_OK) or 0
        _apply(rec, rec["chosen"][:rec["inline"] + ok])
