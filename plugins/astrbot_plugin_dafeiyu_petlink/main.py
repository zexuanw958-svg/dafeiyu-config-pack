"""大肥鱼桌宠互通：电脑桌宠（dsh-pet 经「大肥鱼桥」）和主人的 QQ 私聊是同一个她。

长期记忆靠 LivingMemory 的 global 作用域两边共用一份（安装 Skill 的 memory-scope 设好）。可 LivingMemory 攒满 10 轮
才总结一次，刚聊过的几句还没进长期记忆。这个插件补上这一段，另外管两件桌宠那边的小事：

1. 短期互通（on_llm_request）：
   - 桌宠会话（webchat:FriendMessage:webchat!<主人号>!desk-pet）里说话时，带上主人 QQ 私聊里上次之后新说的几句；
   - 主人 QQ 私聊里说话时，反过来带上桌宠那边新说的几句。
   只带「上次带过之后」新增的（每个方向各记一个位置），对面超过 recent_hours 没人说话就不带。
   补在这一轮用户消息后面（不进系统提示，不打乱 DeepSeek 的缓存），包在 <dafeiyu_petlink> 里；读对面记录时先把它洗掉，不会一层层套。
   群聊、别人的私聊一律不碰：私聊的事不往群里带。
2. 桌宠会话里的固定说明（系统提示，每轮一样）：她在桌宠里、只显示文字、别配表情包别说发语音；
   提醒到点发 QQ 私聊，要看、要取消提醒请主人去 QQ 私聊。碎碎念会话也带一份精简的（只显示文字、别配表情包）：
   10-28 测试机上碎碎念没带说明，她在回复里写了表情包标记。
3. 提醒改发（redirect_reminders）：AstrBot 的提醒工具把任务绑在「当前会话」上（core/tools/cron_tools.py），
   桌宠会话是网页会话，到点发过去主人看不到。这里给 cron 管理器的 add_active_job 套一层：会话是桌宠的，
   就改成主人的 QQ 私聊（发信人照旧是主人，所以 QQ 私聊里能查、能改、能取消）。插件卸载时还原。
4. 桌宠会话不发语音（pet_tts_off）：桌宠气泡只显示文字，语音白合成，豆包还按时长扣钱。
   用 AstrBot 自带的「会话语音开关」关掉桌宠和碎碎念两个会话（后台「会话管理」里能看到、能改回来）。
   配置包的 dafeiyu_clean 插件按这个开关告诉她「你现在不发语音」。
5. 桌宠一次回完（pet_one_bubble）：AstrBot 的分段回复会把她的话按句切开、每句之间等 1.5–3.5 秒，
   QQ 里像真人在打字；桌宠气泡要等整段回完才显示，切开只会让她晚好几秒出声。桌宠和碎碎念会话里不切。
碎碎念会话（desk-pet-whisper）不带对面的话，也不进长期记忆（记忆白名单里没有它）。
"""

import json
import os
import time

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, ResultContentType, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.session_llm_manager import SessionServiceManager

from .link import block, lines_from

NAME = "astrbot_plugin_dafeiyu_petlink"
PET_NOTE = (
    "\n\n【现在在哪】主人这会儿是在他电脑桌面上的桌宠窗口里跟你说话，不是在 QQ 上。桌宠气泡只显示文字："
    "别配表情包，也别说要发语音。在这里定的提醒，到点会发到主人的 QQ 私聊；要看或取消已经定好的提醒，请主人到 QQ 私聊里说。"
)
WHISPER_NOTE = "\n\n【现在在哪】这句碎碎念会显示在主人电脑桌面的桌宠气泡里，只显示文字：别配表情包，也别说要发语音。"


class DafeiyuPetlink(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.state_path = os.path.join(str(StarTools.get_data_dir(NAME)), "seen.json")
        self.state = self._load()
        self.tts_done = set()
        self._patch_cron()

    # ---------- 认会话 ----------
    def _owner_session(self) -> str:
        return str(self.conf.get("owner_session") or "").strip()

    def _owner(self) -> str:
        s = self._owner_session()
        return s.rsplit(":", 1)[-1] if s else ""

    def _sid(self) -> str:
        return str(self.conf.get("pet_session_id") or "desk-pet").strip() or "desk-pet"

    def _kind(self, umo: str) -> str:
        """pet / whisper / owner / ''。桌宠会话认后缀（WebChat 的平台名不写死）。"""
        owner = self._owner()
        if not owner or not umo:
            return ""
        if umo == self._owner_session():
            return "owner"
        if ":FriendMessage:" in umo:
            if umo.endswith(f"!{owner}!{self._sid()}"):
                return "pet"
            if umo.endswith(f"!{owner}!{self._sid()}-whisper"):
                return "whisper"
        return ""

    def _pet_umo(self) -> str:
        return self.state.get("pet_umo") or f"webchat:FriendMessage:webchat!{self._owner()}!{self._sid()}"

    # ---------- 记位置 ----------
    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    async def _fresh(self, other: str, viewer: str) -> list:
        """对面会话里，上次带给 viewer 之后新说的几句（最多 recent_count 条）。"""
        n_max = int(self.conf.get("recent_count", 8) or 0)
        if n_max <= 0:
            return []
        cm = self.context.conversation_manager
        cid = await cm.get_curr_conversation_id(other)
        if not cid:
            return []
        conv = await cm.get_conversation(other, cid)
        if not conv:
            return []
        try:
            hist = json.loads(conv.history or "[]")
        except ValueError:
            return []
        seen = self.state.setdefault("seen", {})
        key = f"{viewer} <- {other}"
        st = seen.get(key) or {}
        start = int(st.get("n", 0)) if st.get("cid") == cid and int(st.get("n", 0)) <= len(hist) else 0
        seen[key] = {"cid": cid, "n": len(hist)}
        self._save()
        upd = int(getattr(conv, "updated_at", 0) or 0)
        if upd > 10**12:
            upd //= 1000
        hours = int(self.conf.get("recent_hours", 12) or 12)
        if upd and time.time() - upd > hours * 3600:
            return []
        return lines_from(hist, start)[-n_max:]

    async def _tts_off(self, umo: str) -> None:
        if not self.conf.get("pet_tts_off", True) or umo in self.tts_done:
            return
        self.tts_done.add(umo)
        try:
            if await SessionServiceManager.is_tts_enabled_for_session(umo):
                await SessionServiceManager.set_tts_status_for_session(umo, False)
                logger.info("[大肥鱼桌宠互通] 桌宠会话的语音关掉了（桌宠只显示文字）")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[大肥鱼桌宠互通] 关桌宠会话语音没成功：{e}")

    async def initialize(self):
        # 第一次跟桌宠说话之前就把语音关上：dafeiyu_clean 按这个开关告诉她发不发语音，第一句也不会说错
        if self._owner():
            await self._tts_off(self._pet_umo())
            await self._tts_off(self._pet_umo() + "-whisper")

    # ---------- 1、2、4 ----------
    # priority 比 dafeiyu_clean 的 10 高：先把桌宠会话的语音关上，clean 读开关时才会告诉她「你现在不发语音」
    @filter.on_llm_request(priority=20)
    async def link(self, event: AstrMessageEvent, req):
        umo = event.unified_msg_origin
        kind = self._kind(umo)
        if not kind:
            return
        if kind in ("pet", "whisper"):
            await self._tts_off(umo)
        if kind == "whisper":
            req.system_prompt = (req.system_prompt or "") + WHISPER_NOTE
            return
        if kind == "pet":
            if self.state.get("pet_umo") != umo:
                self.state["pet_umo"] = umo
                self._save()
            req.system_prompt = (req.system_prompt or "") + PET_NOTE
            other, where = self._owner_session(), "在 QQ 私聊里"
        else:
            other, where = self._pet_umo(), "在电脑桌宠里"
        try:
            text = block(where, await self._fresh(other, umo))
        except Exception as e:  # noqa: BLE001  读对面记录出错不能挡住这一轮回话
            logger.warning(f"[大肥鱼桌宠互通] 读对面最近的话没成功：{e}")
            return
        if text:
            req.extra_user_content_parts.append(TextPart(text=text))

    # ---------- 5 桌宠一次回完 ----------
    # 排在所有插件后面（洗字、表情包、语音插件都处理完了）：把桌宠这条回复标成普通结果，
    # AstrBot 后面就不再按句切开、不在句子之间停顿（也不会再合成语音）。QQ 里照旧分段。
    @filter.on_decorating_result(priority=-100000)
    async def one_bubble(self, event: AstrMessageEvent):
        if not self.conf.get("pet_one_bubble", True):
            return
        if self._kind(event.unified_msg_origin) not in ("pet", "whisper"):
            return
        result = event.get_result()
        if result is not None and result.chain and result.is_model_result():
            result.set_result_content_type(ResultContentType.GENERAL_RESULT)

    # ---------- 3 提醒改发 ----------
    def _patch_cron(self) -> None:
        mgr = getattr(self.context, "cron_manager", None)
        if mgr is None or not hasattr(mgr, "add_active_job"):
            return
        orig = mgr.add_active_job
        while getattr(orig, "_dafeiyu_petlink_orig", None) is not None:  # 上一个实例没还原就被换掉了：剥到最里层
            orig = orig._dafeiyu_petlink_orig
        plugin = self

        async def add_active_job(*args, **kwargs):
            try:
                payload = kwargs.get("payload") if "payload" in kwargs else (args[2] if len(args) > 2 else None)
                target = plugin._owner_session()
                if (plugin.conf.get("redirect_reminders", True) and target and isinstance(payload, dict)
                        and plugin._kind(str(payload.get("session") or "")) in ("pet", "whisper")):
                    payload = dict(payload, session=target, from_pet=True)
                    if "payload" in kwargs:
                        kwargs["payload"] = payload
                    else:
                        args = args[:2] + (payload,) + args[3:]
                    logger.info("[大肥鱼桌宠互通] 桌宠里定的提醒，改成到点发到主人的 QQ 私聊")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[大肥鱼桌宠互通] 改提醒发到哪没成功，照原样定：{e}")
            return await orig(*args, **kwargs)

        add_active_job._dafeiyu_petlink_orig = orig
        mgr.add_active_job = add_active_job
        self._cron = (mgr, orig)

    async def terminate(self):
        mgr, orig = getattr(self, "_cron", (None, None))
        if mgr is not None and getattr(mgr.add_active_job, "_dafeiyu_petlink_orig", None) is not None:
            try:
                del mgr.add_active_job  # 去掉实例上的这一层，回到类上原来的方法
            except AttributeError:
                mgr.add_active_job = orig
