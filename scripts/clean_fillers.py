# -*- coding: utf-8 -*-
"""字幕语气词 / 口头禅清洗（单一事实来源，talking-head-recut v4.9f）。

被两处复用：
  - split_sentences.py  : MG 字幕源（转写 -> 句单元）清洗前置到转写源；
  - pipeline.py phase_captions_clean : 原生字幕卡（edit_captions set_card_text）兜底清洗。
无论成片走 MG 还是原生字幕，语气词/口头禅都被去，杜绝"成片回退原生=零清洗"的盲区。

规则（与 SKILL.md §3.7 一致）：
  - 单字语气词（句首/句中/句尾）：嗯啊呃呢吧嘛呀哦哈啦咧喽咯哟喔诶呸哼嘞喏咦嗳呦喂…
  - 复音指示填充词（仅句首去，防"进这个行业"误伤）：这个 / 那个 / 那个啥 / 那个就是
  - 复音连接/口头禅（全局去）：然后 / 其实 / 就是说 / 对吧 / 是吧 / 怎么说 / 的话 / 就是呢
"""
import re

# 单字语气词（句首/句中/句尾）
MOOD_SINGLE = set("呃啊呀哎嗯呢吧嘛哦哈啦咧喽咯哟喔诶呸哼嘞喏咦嗳呦喂")
# 复音填充词：A) 指示类 —— 仅句首去，避免"进这个行业"->"进行业"误伤
#                     另含句首话语标记"那 / 那么"（口语填充，口播教程域误伤概率极低）
LEADING_FILLERS = ["这个", "那个", "那个啥", "那个就是", "那", "那么"]
# B) 连接/口头禅类 —— 全局去（去掉后语义基本不变，字幕更干净）
GLOBAL_FILLERS = ["然后", "其实", "就是说", "对吧", "是吧", "怎么说", "的话", "就是呢"]

PUNCT_STR = " ，。！？、；：,!?;:\t"


def _strip_leading(s):
    """循环去掉句首：语气字 / 指示类填充词 / 首尾标点与空格。"""
    changed = True
    while changed:
        changed = False
        s2 = s.lstrip(PUNCT_STR)
        if s2 != s:
            s = s2
            changed = True
            if not s:
                break
            continue
        if s and s[0] in MOOD_SINGLE:
            s = s[1:]
            changed = True
            continue
        for w in LEADING_FILLERS:
            if s.startswith(w):
                s = s[len(w):]
                changed = True
                break
    return s


def _strip_mid_mood(s):
    """句中/句尾单字语气词：后跟标点或结尾则删。"""
    cls = "[，。！？、；：,!?;:]"
    for m in MOOD_SINGLE:
        s = re.sub(re.escape(m) + "(?=" + cls + "|$)", "", s)
    return s


def _strip_global(s):
    for w in GLOBAL_FILLERS:
        s = s.replace(w, "")
    return s


def clean_one(text):
    """返回清洗后文本；若清洗后为空/纯标点则返回 None（调用方跳过该卡/单元）。"""
    if not text:
        return None
    s = text.strip()
    # 1) 句首语气词 + 句首指示填充词（循环）
    s = _strip_leading(s)
    if not s:
        return None
    # 2) 连接/口头禅类全局去除
    s = _strip_global(s)
    # 3) 句中/句尾单字语气词
    s = _strip_mid_mood(s)
    # 4) 清理标点周边空格、重复空格（保留句内/句尾标点，维持停顿）
    s = re.sub(r"\s*([，。！？、；：,.!?;:])\s*", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = s.strip()
    # 5) 空或纯标点 -> 跳过
    if not s or all(ch in PUNCT_STR for ch in s):
        return None
    return s


if __name__ == "__main__":
    import json, os, sys
    if len(sys.argv) > 1:
        t = sys.argv[1]
        print("IN :", t)
        print("OUT:", clean_one(t))
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v4_readcaps.json")
        if not os.path.exists(p):
            print("no v4_readcaps.json"); sys.exit(0)
        u = json.load(open(p, encoding="utf-8"))
        cards = u.get("cards", [])
        n_change = 0
        for c in cards:
            t = c.get("text", "")
            o = clean_one(t)
            if o != t:
                n_change += 1
        print("%d/%d 张卡文本有变化" % (n_change, len(cards)))
