"""大肥鱼看视频：DeepSeek 只能看图、看不了视频，AstrBot 收到视频也只往对话里塞一行文件名。
这里在发给模型之前（on_llm_request）把视频均匀截几张画面，作为图片交给模型；再把声音转成文字：
有本机语音识别（install-asr.ps1 装的 sherpa-onnx + SenseVoice，加 -Plus 多装方言、歌词更准的 Fun-ASR-Nano）就本机认，免费；没有再用硅基流动的 key；都没有只看画面。

- 截的画面默认只给这一轮用（mark_as_temp），不进聊天记录，后面每轮不会重复按图收钱；文字说明会留在记录里。
  代价是追问细节她答不上（10-27 实测：追问「第一个画面有人吗」，她编了个「没人」），所以说明里让她记不清就直说。
  想让她记住画面就打开 keep_frames，之后每轮都带着这几张图（DeepSeek 前缀缓存命中按低价算，但还是多花钱）。
- 私聊都看；群里默认被 @ 或叫醒才看（群聊插件 group_chat_plus 也是走 event.request_llm，这个钩子同样生效）。
- 出任何错都只记日志，不影响她正常回话。
"""

import os
import shutil
import time
import uuid

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Reply, Video
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import ImageURLPart, TextPart
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from . import video as V


def _first_video(event: AstrMessageEvent):
    for comp in event.message_obj.message:
        if isinstance(comp, Video):
            return comp
    for comp in event.message_obj.message:
        if isinstance(comp, Reply) and comp.chain:
            for c in comp.chain:
                if isinstance(c, Video):
                    return c
    return None


def _is_astrbot_marker(part) -> bool:
    text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
    return isinstance(text, str) and text.startswith("[Video Attachment")


class DafeiyuVideo(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.root = os.path.join(get_astrbot_temp_path(), "dafeiyu_video")
        self.warned_no_ffmpeg = False

    def _purge_old(self):
        """上一轮的截图交给模型后就没用了，超过一小时的目录清掉。"""
        if not os.path.isdir(self.root):
            return
        for name in os.listdir(self.root):
            d = os.path.join(self.root, name)
            try:
                if os.path.isdir(d) and time.time() - os.path.getmtime(d) > 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    @filter.on_llm_request()
    async def look(self, event: AstrMessageEvent, req):
        video = _first_video(event)
        if not video:
            return
        if not event.is_private_chat() and self.conf.get("group_need_at", True) and not event.is_at_or_wake_command:
            return
        try:
            await self._look(event, req, video)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dafeiyu_video] 这段视频没看成：{type(e).__name__}: {e}")

    async def _look(self, event, req, video):
        ffmpeg = V.find_ffmpeg((self.conf.get("ffmpeg_path") or "").strip())
        if not ffmpeg:
            if not self.warned_no_ffmpeg:
                logger.warning("[dafeiyu_video] 没找到 ffmpeg，看不了视频。装好 ffmpeg 后在插件设置里填 ffmpeg.exe 的位置")
                self.warned_no_ffmpeg = True
            req.extra_user_content_parts.append(TextPart(
                text="[对方发来一段视频，但你这边看不了视频内容（电脑上没装 ffmpeg）。老实说没看到，别猜内容。]"))
            return

        t0 = time.time()
        path = await video.convert_to_file_path()
        max_seconds = int(self.conf.get("max_seconds", 180) or 180)
        max_frames = max(1, int(self.conf.get("max_frames", 6) or 6))
        duration, has_audio = await V.probe(ffmpeg, path)
        times = V.pick_times(duration, max_seconds, max_frames)

        self._purge_old()
        work = os.path.join(self.root, uuid.uuid4().hex[:12])
        frames = await V.grab_frames(ffmpeg, path, times, work)
        if not frames:
            logger.warning("[dafeiyu_video] ffmpeg 没截出画面（视频格式不认识？）")
            return

        heard, how = "", ""
        asr = V.find_asr((self.conf.get("asr_dir") or "").strip(), ffmpeg)
        key = (self.conf.get("asr_key") or "").strip()
        if has_audio and (asr or key):
            try:
                if asr:
                    how = "本机识别"
                    chunks = await V.grab_chunks(ffmpeg, path, max_seconds, work)
                    if chunks:
                        heard, which = await V.transcribe_local(asr, chunks)
                        how = f"本机识别（{which}）"
                else:
                    how = "硅基流动"
                    audio = await V.grab_audio(ffmpeg, path, max_seconds, work)
                    if audio:
                        heard = await V.transcribe(audio, key, self.conf.get("asr_model") or "FunAudioLLM/SenseVoiceSmall")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[dafeiyu_video] 声音转文字没成功（{how}），只看画面：{e}")

        keep = bool(self.conf.get("keep_frames", False))
        length = V.mmss(duration) if duration else "未知"
        cut = f"，你只看了前 {max_seconds // 60} 分钟" if duration > max_seconds else ""
        stamps = "、".join(V.mmss(t) for t, _ in frames)
        if heard:
            sound = f"视频里说的话（自动转写，可能有错字）：「{heard[:1500]}」。"
        elif has_audio and how:
            sound = "视频有声音，但没听出有人说话（可能只有音乐或杂音）。"
        elif has_audio:
            sound = "视频有声音，但你这次只看到了画面，没听到声音。"
        else:
            sound = ""
        text = (f"[对方发来一段视频，时长 {length}{cut}。下面 {len(frames)} 张图是从视频里按时间顺序截的画面"
                f"（分别在 {stamps}）。{sound}回的时候就当你刚看完这段视频，别提截图、抽帧、转写这些词。"
                f"{'' if keep else '这些画面只给你看这一次，之后被问到你这次没说到的细节，就说当时没看清，别编。'}]")

        req.extra_user_content_parts = [p for p in req.extra_user_content_parts if not _is_astrbot_marker(p)]
        req.extra_user_content_parts.append(TextPart(text=text))
        for _, f in frames:
            part = ImageURLPart(image_url=ImageURLPart.ImageURL(url=f))
            req.extra_user_content_parts.append(part if keep else part.mark_as_temp())
        logger.info(f"[dafeiyu_video] 看了一段 {length} 的视频：{len(frames)} 张画面，"
                    f"{how + '转写 ' + str(len(heard)) + ' 字' if heard else (how + '没认出字' if how else '没转写')}，用时 {time.time() - t0:.1f} 秒")
