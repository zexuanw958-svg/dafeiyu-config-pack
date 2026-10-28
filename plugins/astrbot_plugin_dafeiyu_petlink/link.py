"""桌宠互通用到的纯文字处理：从一段聊天记录里挑出「上次之后新说的」几句，洗掉内部标签，拼成补给她的背景。
只依赖标准库，方便单独测。"""
import json
import re

TAG = "dafeiyu_petlink"
_STRIP = [
    re.compile(rf"<{TAG}>.*?</{TAG}>", re.S),                         # 本插件自己补的背景，不能一层层套进去
    re.compile(r"<\s*system_reminder\s*>.*?<\s*/\s*system_reminder\s*>", re.S | re.I),  # AstrBot 塞的号码、昵称、时间
    re.compile(r"<RAG-Faiss-Memory>.*?</RAG-Faiss-Memory>", re.S),   # LivingMemory 注入的长期记忆
    re.compile(r"<Quoted Message>.*?</Quoted Message>", re.S),
    re.compile(r"&&[^&\s]{1,40}&&"),                                   # 表情包标记
    re.compile(r"\[你上一条回复是念成语音[^\]\n]*\]?"),
    re.compile(r"User ID:\s*\d+\s*,\s*Nickname:[^\n<]*"),
]
MAX_CHARS = 100


def clean(text):
    text = str(text or "")
    for p in _STRIP:
        text = p.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS] + ("…" if len(text) > MAX_CHARS else "")


def text_of(content):
    if isinstance(content, str):
        return content
    out = []
    for part in content or []:
        if isinstance(part, dict):
            if part.get("type") == "text":
                out.append(str(part.get("text") or ""))
            elif part.get("type") in ("image_url", "image"):
                out.append("[图片]")
        elif isinstance(getattr(part, "text", None), str):
            out.append(part.text)
    return " ".join(out)


def lines_from(history, start=0):
    """history：OpenAI 格式的消息列表（或它的 JSON 字符串）。返回 [(谁, 话)]，只要主人说的和她说的文字。"""
    if isinstance(history, str):
        try:
            history = json.loads(history or "[]")
        except ValueError:
            history = []
    out = []
    for m in (history or [])[start:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        t = clean(text_of(m.get("content")))
        if not t or t == "Output your last task result below.":
            continue
        if m.get("role") == "assistant" and t.startswith("[CronJob]"):
            # AstrBot 存的是「[CronJob] 名字: 要做的事 triggered at …, I finished this job…」
            c = re.match(r"\[CronJob\]\s*([^:：]*)[:：]\s*(.*?)\s+triggered at", t)
            what = (c.group(2) or c.group(1)).strip() if c else ""
            t = f"（到点提醒了主人：{what}）" if what else "（到点提醒了主人一件事）"
        out.append(("主人" if m.get("role") == "user" else "你", t))
    return out


def block(where, lines):
    if not lines:
        return ""
    body = "\n".join(f"{who}：{t}" for who, t in lines)
    return (f"<{TAG}>\n（这段是插件补充的背景，不是主人这句话的一部分）主人刚才{where}跟你聊过这几句，旧的在前：\n"
            f"{body}\n接着自然聊就行，用不上就当没看见；别提「插件」「记录」这些词。\n</{TAG}>")
