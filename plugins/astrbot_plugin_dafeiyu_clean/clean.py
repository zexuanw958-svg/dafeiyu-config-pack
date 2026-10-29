"""把回复里漏出来的内部标签删掉。只依赖标准库，方便单独测。

会删的：
- AstrBot 塞进用户消息的 <system_reminder>…</system_reminder>（发消息人的 QQ 号、昵称、时间）；
  没闭合的 <system_reminder> 从它删到这一行末尾。没带标签的「User ID: 数字, Nickname: …」也删。
- DeepSeek 原生工具调用标记 DSML：<｜DSML｜function_calls> 整块、零散的 <｜DSML｜invoke …>、</｜DSML｜parameter> 等。
- 老格式 <｜tool▁calls▁begin｜>…<｜tool▁calls▁end｜>、<tool_call>…</tool_call>。
- 本插件自己补给她的「[你上一条回复是念成语音发出去的……]」，她照抄出来的话也删。

表情包暗号（10-29 群里实测：她写成「& &greeting& &」，meme_manager 只认没空格的「&&greeting&&」，
没认出来就留在字里，赶上这轮发语音，四条语音里两条在念 greeting、sleep）：
- clean() 先把写歪的改回「&&分类&&」，排在 meme_manager 前面，图照样能配上；
- strip_markers() 在发出去前把还剩在字里的暗号删掉（分类不存在之类），不让语音念出来。

单独一段的「……」（10-29 Z1）：分段回复按换行和「。？！~…」切（配置包默认），她单独一行「……」、
或者一开口就是「……」，会切成单独一段，赶上这轮发语音也被念出来。AstrBot 只给多于一个字的段配语音，
fold_lone_punct() 把单独一行的标点接到上一行末尾，开头那串（前面没东西可接）缩成一个字。
"""
import re

_BAR = r"[|｜]"
_PATTERNS = [
    re.compile(r"<\s*system_reminder\s*>.*?<\s*/\s*system_reminder\s*>", re.S | re.I),
    re.compile(r"<\s*system_reminder\s*>[^\n]*", re.I),
    re.compile(r"<\s*/\s*system_reminder\s*>", re.I),
    re.compile(rf"<\s*{_BAR}+\s*DSML\s*{_BAR}+\s*function_calls\s*>.*?(?:<\s*/\s*{_BAR}+\s*DSML\s*{_BAR}+\s*function_calls\s*>|$)", re.S | re.I),
    re.compile(rf"<\s*/?\s*{_BAR}+\s*DSML\s*{_BAR}+[^>]*>", re.I),
    re.compile(r"<｜tool▁calls▁begin｜>.*?(?:<｜tool▁calls▁end｜>|$)", re.S),
    re.compile(r"<｜[^｜>\n]*▁[^｜>\n]*｜>"),
    re.compile(r"<\s*tool_call\s*>.*?(?:<\s*/\s*tool_call\s*>|$)", re.S | re.I),
    re.compile(r"User ID:\s*\d+\s*,\s*Nickname:[^\n<]*"),
    re.compile(r"\[你上一条回复是念成语音[^\]\n]*\]?"),
]
_MEME_SPACED = re.compile(r"&[ \t]*&[ \t]*([^&\s]{1,100}?)[ \t]*&[ \t]*&")
# 她写「配表情包」的暗号：&&分类键&&。表情包插件会把认得的当场换成图，
# 认不得的会留在正文里被念成语音。
_MARKER = re.compile(r"&\s*&\s*[a-zA-Z][\w:\- ]{0,40}?\s*&\s*&")
_SPLIT = "。？！~…"
_GAP = re.compile(r"([。？！~…])[ \t\u3000]+(?=[。？！~…])")
_LONE = re.compile(r"[ \t\u3000]*(?:\r?\n[ \t\u3000]*)+([。？！~～…?!.，,、]+)[ \t\u3000]*(?=\r?\n|$)")
_HEAD = re.compile(r"^\s*([。？！~…]{2,})")
_ONLY = re.compile(r"^\s*([。？！~～…?!.，,、])[。？！~～…?!.，,、\s]+$")
_HINT = re.compile(r"system_reminder|DSML|tool▁|tool_call|User ID:|你上一条回复是念成语音", re.I)


def strip_markers(text: str) -> str:
    """删掉没换成图、剩在正文里的表情包暗号。只该在发出去之前（配图那步之后）调。"""
    if not text or "&" not in text:
        return text
    t = _MARKER.sub("", text)
    if t == text:
        return text
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def fold_lone_punct(text: str) -> str:
    """「好吧\n……\n行」→「好吧……\n行」，「……你又来了」→「…你又来了」，整条只有「……」→「…」。"""
    if not text or not any(c in text for c in _SPLIT + "?!."):
        return text
    t = _GAP.sub(r"\1", text)
    t = _LONE.sub(r"\1", t)
    m = _ONLY.match(t)
    if m:
        return _one(t.strip())
    m = _HEAD.match(t)
    if m:
        t = _one(m.group(1)) + t[m.end():]
    return t


def _one(run: str) -> str:
    return "…" if any(c in run for c in "….") else run[0]


def _fix_meme(text: str) -> str:
    return _MEME_SPACED.sub(lambda m: f"&&{m.group(1)}&&", text)


def dirty(text: str) -> bool:
    return bool(text) and (bool(_HINT.search(text)) or _fix_meme(text) != text)


def clean(text: str) -> str:
    if not dirty(text):
        return text
    text = _fix_meme(text)
    for p in _PATTERNS:
        text = p.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
