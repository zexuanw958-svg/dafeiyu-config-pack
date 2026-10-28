"""把回复里漏出来的内部标签删掉。只依赖标准库，方便单独测。

会删的：
- AstrBot 塞进用户消息的 <system_reminder>…</system_reminder>（发消息人的 QQ 号、昵称、时间）；
  没闭合的 <system_reminder> 从它删到这一行末尾。没带标签的「User ID: 数字, Nickname: …」也删。
- DeepSeek 原生工具调用标记 DSML：<｜DSML｜function_calls> 整块、零散的 <｜DSML｜invoke …>、</｜DSML｜parameter> 等。
- 老格式 <｜tool▁calls▁begin｜>…<｜tool▁calls▁end｜>、<tool_call>…</tool_call>。
- 本插件自己补给她的「[你上一条回复是念成语音发出去的……]」，她照抄出来的话也删。
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
_HINT = re.compile(r"system_reminder|DSML|tool▁|tool_call|User ID:|你上一条回复是念成语音", re.I)


def dirty(text: str) -> bool:
    return bool(text) and bool(_HINT.search(text))


def clean(text: str) -> str:
    if not dirty(text):
        return text
    for p in _PATTERNS:
        text = p.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
