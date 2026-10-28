"""大肥鱼看群里的图：没 @ 她的图先看一眼，换成一句话，再交给群聊插件。

群聊插件（group_chat_plus）只看 @ 她那条消息里的图。先发图、过一会儿再 @ 她问「图里写了啥」，那张图早被跳过了
（它本想从 AstrBot 自带的群聊上下文拿图片描述，AstrBot 4.29 那边换了实现，拿不到，10-28 测试群实测）。
这里排在群聊插件前面：白名单群里没 @ 她的图，用后台的「看图模型」看一眼，把图换成「[图片：……]」一行字，
群聊插件记下的上下文里就有这张图是什么。@ 她的图不动，照旧由群聊插件和 AstrBot 自己看。
每张图调一次看图模型（DeepSeek 不到半分钱）；同一张图一小时内不重复看。出错只记日志，图原样留着。
"""

import asyncio
import time
import uuid
from collections import OrderedDict

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig

PROMPT = (
    "这是 QQ 群里有人发的一张图。用一两句中文说清楚图里是什么；图里有字就把字原样写出来（太长就写前面的）；"
    "是表情包就说是什么表情包、什么情绪。只写描述，不要开场白。"
)


class DafeiyuGroupImage(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.cache = OrderedDict()  # 图片地址 → (描述, 时间)

    def _provider(self, event):
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        pid = (cfg.get("provider_settings") or {}).get("default_image_caption_provider_id") or ""
        return self.context.get_provider_by_id(pid) if pid else None

    async def _caption(self, prov, url):
        hit = self.cache.get(url)
        if hit and time.time() - hit[1] < 3600:
            return hit[0]
        resp = await asyncio.wait_for(
            prov.text_chat(prompt=PROMPT, session_id=uuid.uuid4().hex, image_urls=[url], persist=False), timeout=40
        )
        text = " ".join((resp.completion_text or "").split())[:300]
        if text:
            self.cache[url] = (text, time.time())
            while len(self.cache) > 300:
                self.cache.popitem(last=False)
        return text

    # 群聊插件处理群消息的 handler 是 priority=-1，这里要排在它前面
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=100)
    async def on_group(self, event: AstrMessageEvent):
        if not self.conf.get("enable", True):
            return
        chain = event.message_obj.message
        imgs = [c for c in chain if isinstance(c, Image)]
        if not imgs:
            return
        me = str(event.get_self_id())
        if any(isinstance(c, At) and str(c.qq) == me for c in chain):
            return
        prov = self._provider(event)
        if not prov:
            logger.info("[群里的图] 后台没设看图模型，跳过")
            return
        changed = False
        for img in imgs[: int(self.conf.get("max_images", 3) or 3)]:
            url = img.url or img.file
            if not url:
                continue
            t0 = time.time()
            try:
                text = await self._caption(prov, url)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[群里的图] 没看成：{e}")
                continue
            if text:
                chain[chain.index(img)] = Plain(f"[图片：{text}]")
                changed = True
                logger.info(f"[群里的图] 看了 {time.time() - t0:.1f} 秒：{text[:60]}")
        if changed:
            s = "".join(c.text for c in chain if isinstance(c, Plain)).strip()
            event.message_obj.message_str = s
            event.message_str = s
