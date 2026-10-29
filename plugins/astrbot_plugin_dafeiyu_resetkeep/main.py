"""大肥鱼清空不失忆：发 /reset（或 /new）时，先把还没存进长期记忆的那几轮存了，再让记忆插件清空。

10-29 群友：「每次改完人设发 /reset，她就有点失忆，但是指向性问她又会记起来」。
原因（LivingMemory 2.8.1）：
- 它攒满「总结触发轮次」（配置包默认 10 轮）才把这一段聊天总结进长期记忆；
- /reset、/new 的回复发出去以后，它在 after_message_sent 里把自己那份会话记录和总结进度一起清掉，
  没攒满的那几轮从来没进长期记忆，就再也找不回来了；
- 更早总结进去的还在，但它只按眼前这句话去检索，所以泛泛地问想不起来，指着问又想起来了。
这里挂在同一个钩子上、排在它前面（优先级 100，它是 0）：看到 /reset、/new 的标记，先调它自己的「立即总结」
（/lmem summarize 背后那个函数，记忆白名单、群聊隔离、记忆范围都照它的规矩走），总结完才轮到它清空。
没装或没开记忆插件、这个会话不在记忆白名单里、新攒下的不到两条消息的，什么都不做。
总结要叫一次对话模型，一般几秒；这时 /reset 的回复已经发出去了，不耽误聊天。超时或出错就照原来那样清空。

/reset、/new 的回复是 AstrBot 写死的英文（10-29 群友截图里「✅ The current conversation context has been cleared.」），
发出去之前换成中文；开着记忆的顺便提醒一句等十来秒再问。
"""

import asyncio
import re

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

LM = "astrbot_plugin_livingmemory"
RESET_FLAGS = ("_clean_group_context_session", "_clean_ltm_session")  # AstrBot 4.29 的 /reset、/new 设前一个；后一个是记忆插件也认的旧名字
TIMEOUT = 90
RESET_EN = "✅ The current conversation context has been cleared."
NEW_EN = re.compile(r"✅ Switched to new conversation: (\w+)\.")


def _text(result) -> str:
    try:
        return result.get_plain_text().replace("\n", " ")
    except Exception:  # noqa: BLE001
        return ""


class DafeiyuResetKeep(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    def _summarize_fn(self):
        """记忆插件的「立即总结」函数；没装、没开、换了写法拿不到，返回 None。"""
        meta = self.context.get_registered_star(LM)
        if not meta or not getattr(meta, "activated", True):
            return None, None
        lm = getattr(meta, "star_cls", None)
        fn = getattr(getattr(lm, "command_handler", None), "handle_summarize", None)
        return lm, fn

    @filter.on_decorating_result(priority=100)
    async def reply_in_chinese(self, event: AstrMessageEvent):
        if not any(event.get_extra(k, False) for k in RESET_FLAGS):
            return
        result = event.get_result()
        for comp in (result.chain if result else None) or []:
            if not isinstance(comp, Plain):
                continue
            t = comp.text.strip()
            if t == RESET_EN:
                comp.text = "✅ 聊天清空了"
            elif m := NEW_EN.fullmatch(t):
                comp.text = f"✅ 开了个新对话（{m.group(1)}）"
            else:
                continue
            if self._summarize_fn()[1] is not None:
                comp.text += "。长期记忆还在，刚才没存的几轮这就存进去，等十来秒再问她以前的事"

    @filter.after_message_sent(priority=100)
    async def keep_before_reset(self, event: AstrMessageEvent, *_args):
        if not any(event.get_extra(k, False) for k in RESET_FLAGS):
            return
        lm, fn = self._summarize_fn()
        if fn is None:
            return
        try:
            ready, _ = await lm._ensure_plugin_ready(wait=False)
        except Exception:  # noqa: BLE001  它没有这个函数了，就当准备好了，交给下面的 try
            ready = True
        if not ready:
            return

        async def run():
            return [_text(r) async for r in fn(event)]

        try:
            said = await asyncio.wait_for(run(), TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"[dafeiyu_resetkeep] 清空前总结超过 {TIMEOUT} 秒没做完，这几轮没存上")
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dafeiyu_resetkeep] 清空前总结出错，这几轮没存上：{type(e).__name__}: {e}")
            return
        last = said[-1] if said else ""
        if len(said) >= 2:
            logger.info(f"[dafeiyu_resetkeep] 清空前先存进长期记忆：{last[:120]}")
        else:
            logger.info(f"[dafeiyu_resetkeep] 清空前没有要存的：{last[:120]}")
