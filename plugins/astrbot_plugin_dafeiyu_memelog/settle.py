"""表情包标记的记账规则。只依赖标准库，方便单独测。

模型想配表情包时，在回复末尾写 &&分类&&（meme_manager 的格式）。meme_manager 把它从要发出去的字里删掉，
再按概率（配置包默认 60%）决定这次真发不发图；分类里没图、发送失败也会没发。
但 AstrBot 存聊天记录用的是删标记之前那份原话，所以记录里每个 &&分类&& 都在，不管图发没发。
下一轮她看到自己「发过」很多表情包，就会说「你又不是没看过我那张」。

settle(text, keep) 把没真发出去的标记从记录里删掉，真发了的原样留着（她认得自己的格式，知道那条配过图）。
"""
import re

TAG = re.compile(r"&&([^&\s]{1,40})&&")


def tags(text: str) -> list[str]:
    """按出现顺序列出不重复的分类。"""
    out = []
    for key in TAG.findall(text or ""):
        if key not in out:
            out.append(key)
    return out


def settle(text: str, keep) -> str:
    """keep 里的分类各留第一个标记，其余标记删掉，顺手收拾删出来的空行。"""
    if not text or "&&" not in text:
        return text
    keep = list(keep or [])
    kept = set()

    def repl(m):
        key = m.group(1)
        if key in keep and key not in kept:
            kept.add(key)
            return m.group(0)
        return ""

    new = TAG.sub(repl, text)
    if new == text:
        return text
    new = re.sub(r"[ \t]+\n", "\n", new)
    new = re.sub(r"\n{3,}", "\n\n", new)
    return new.strip()
