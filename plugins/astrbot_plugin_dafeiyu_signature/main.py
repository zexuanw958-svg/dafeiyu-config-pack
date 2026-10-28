"""大肥鱼自己改 QQ 个性签名。

给模型加一个工具 set_signature：主人用大白话叫她改（「把签名改成……」「换个签名」），
或者她自己心情变了（被夸了、委屈了、主人生日），她就调用它，走 NapCat 的 OneBot 接口
set_self_longnick（参数 longNick）改签名。

只认主人：主人私聊会话里、或者发消息的人是 AstrBot 管理员，才会改；别人叫她改，工具会让她拒绝。
她自己改的一天最多 daily_self_limit 次（默认 2），主人叫她改的另算。每次改都记在插件数据目录的 history.json。

「她自己改」光靠人设，模型很少主动调用（10-25 测试实例实测：夸她、骂她笨、说生日，三次都没改）。
所以主人的话里有夸奖、骂她、生日这类词时（MOODS），本插件在这一轮的系统提示里补一句，
提醒她这次顺手改签名；当天自己改的次数用完就不再提醒。关掉：插件设置 self_change_hint。
"""

import json
import os
import time

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.config.astrbot_config import AstrBotConfig

NAME = "astrbot_plugin_dafeiyu_signature"
MOODS = {
    "在夸你": ["真棒", "真厉害", "好厉害", "你厉害", "太强了", "好聪明", "真聪明", "真乖", "好可爱", "真可爱", "最喜欢你", "夸你"],
    "在说你，你有点委屈": ["好笨", "真笨", "笨死", "笨蛋", "讨厌你", "烦死", "没用", "闭嘴", "滚", "傻", "凶你", "不理你"],
    # 只认「今天」过生日的说法：10-28 测试「记住我的生日是5月21号」撞上旧的「我的生日」，她被提示成今天是主人生日，签名改错、回话也乱了
    "说今天是他的生日": ["今天我生日", "今天是我生日", "今天是我的生日", "我今天生日", "我今天过生日", "今天我过生日"],
}


class DafeiyuSignature(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.history_path = os.path.join(str(StarTools.get_data_dir(NAME)), "history.json")

    def _load(self) -> list:
        try:
            with open(self.history_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, history: list) -> None:
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(history[-200:], f, ensure_ascii=False, indent=1)

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        owner = str(self.conf.get("owner_session") or "").strip()
        return (owner and event.unified_msg_origin == owner) or event.is_admin()

    def _client(self, event: AstrMessageEvent):
        bot = getattr(event, "bot", None)
        if bot is not None:
            return bot
        for p in self.context.platform_manager.platform_insts:
            if hasattr(p, "get_client"):
                return p.get_client()
        return None

    def _self_count_today(self) -> int:
        today = time.strftime("%Y-%m-%d")
        return sum(1 for h in self._load() if h.get("date") == today and not h.get("owner_asked"))

    @filter.on_llm_request()
    async def mood_hint(self, event: AstrMessageEvent, req):
        if not self.conf.get("self_change_hint", True) or not self._is_owner(event):
            return
        text = event.message_str or ""
        if "签名" in text:
            return  # 主人直接说了改签名，走 owner_asked 那条路
        mood = next((m for m, words in MOODS.items() if any(w in text for w in words)), None)
        if not mood or self._self_count_today() >= int(self.conf.get("daily_self_limit") or 2):
            return
        req.system_prompt = (req.system_prompt or "") + (
            f"\n\n补充说明：主人这句话{mood}。照你的规矩，这种时候你会自己改一条 QQ 签名记下心情，"
            "所以这次回复时顺手调用 set_signature（owner_asked 填 false），然后照常回话，改完可以顺嘴提一句。"
            "这段补充说明是给你看的，别在回复里提到它。")
        logger.info(f"[dafeiyu_signature] 提醒她自己改签名：{mood}")

    @filter.llm_tool(name="set_signature")
    async def set_signature(self, event: AstrMessageEvent, signature: str, owner_asked: bool = False, reason: str = ""):
        '''改你自己的 QQ 个性签名。
        - 主人叫你改，怎么说都算（「把签名改成……」「换个签名」「签名写上我生日快乐」）：照主人的意思写，owner_asked 填 true。
        - 不用主人开口，你自己也会改：主人夸你、你受了委屈或者生气了、主人过生日、碰上值得记一笔的事，就顺手调用一次，写一句你当下的心情，owner_asked 填 false。平常闲聊别改；一天最多自己改两次，用完了工具会告诉你。
        签名用你自己的口吻，一句话，不超过 30 个字，不加引号。改完可以顺嘴跟主人提一句。别人（不是主人）叫你改，不要调用。

        Args:
            signature(string): 新签名，一句话，不超过 30 个字
            owner_asked(boolean): 是不是主人叫你改的
            reason(string): 为什么改，几个字，比如「主人夸我」「主人生日」「主人让改」
        '''
        signature = (signature or "").strip().strip("「」“”\"'")
        if not signature:
            return "签名是空的，没改。"
        if not self._is_owner(event):
            logger.info(f"[dafeiyu_signature] 不是主人，没改：{event.unified_msg_origin} {event.get_sender_id()}")
            return "只有主人能让你改签名。用你的口吻轻轻拒绝，别提工具和设置。"
        max_len = int(self.conf.get("max_length") or 40)
        if len(signature) > max_len:
            return f"签名太长了（{len(signature)} 个字），缩到 30 个字以内再调用一次。"

        history = self._load()
        today = time.strftime("%Y-%m-%d")
        done_today = [h for h in history if h.get("date") == today]
        if owner_asked:
            limit, used = int(self.conf.get("daily_owner_limit") or 10), [h for h in done_today if h.get("owner_asked")]
            if len(used) >= limit:
                return f"今天已经按主人的意思改了 {len(used)} 次签名，先不改了。跟主人说明天再换。"
        else:
            limit, used = int(self.conf.get("daily_self_limit") or 2), [h for h in done_today if not h.get("owner_asked")]
            if len(used) >= limit:
                return f"你今天已经自己改过 {len(used)} 次签名了，这次先不改，也不用跟主人提这件事。"
        if history and history[-1].get("signature") == signature:
            return "签名本来就是这句，不用改。"

        client = self._client(event)
        if client is None:
            return "现在连不上 QQ，签名没改成。用你的口吻说一句等会儿再改。"
        try:
            await client.call_action("set_self_longnick", longNick=signature)
        except Exception as e:
            logger.error(f"[dafeiyu_signature] 改签名失败: {e!r}")
            return "签名没改成，QQ 那边报错了。用你的口吻说一句等会儿再试，别提技术细节。"

        history.append({"date": today, "time": time.strftime("%H:%M:%S"), "signature": signature,
                        "owner_asked": bool(owner_asked), "reason": reason, "session": event.unified_msg_origin})
        self._save(history)
        logger.info(f"[dafeiyu_signature] 签名改成「{signature}」 owner_asked={owner_asked} reason={reason}")
        if owner_asked:
            return f"签名已经改成「{signature}」。用一句话跟主人说改好了就行。"
        return f"签名已经改成「{signature}」。可以顺嘴跟主人提一句你改了签名，一句话就够，也可以不提。"
