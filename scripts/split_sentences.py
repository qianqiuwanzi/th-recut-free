#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""字幕智能断句（一行一句）—— 从词级转写切出「一句一卡」的字幕单元。

规则（与 SKILL.md §3.7 一致）：
  1) 首选在句末标点（。！？；）后切分；其次在停顿标点（，、）后切分；
  2) 每句长度目标 ≤ max_chars（默认 14），硬上限 hard_max（默认 20）；
  3) 过短句（< min_chars 且与相邻句合计仍 ≤ max_chars）与相邻句合并；
  4) 超长句（> hard_max）在词边界回退切分；
  5) 输出时间统一为**时间线帧**（按 V1 片段表把源微秒映射过来）。

用法：
  python split_sentences.py --trans <transcription_*.json> --segs <v1segs.json> \
      --out <sentences.json> [--max-chars 14] [--hard-max 20] [--min-chars 4] [--fps 30]
输出：[{"text": "...", "startFrame": int, "endFrame": int, "srcStartUs": int, "srcEndUs": int}, ...]
"""
import json
import argparse
import sys

try:
    import jieba
    _JIEBA = True
except ImportError:
    _JIEBA = False


def rejoin_jieba(toks):
    """把 token 流拼成全文后用 jieba 重分词（词边界绝不切断），词时间取覆盖 token 的首尾。
    ChatCut 的 tokens 常为逐字切分，直接按 token 累积会把「标题」「文件夹」拆到两句。"""
    if not _JIEBA or not toks:
        return toks
    char2tok = []
    for i, (t, s, e) in enumerate(toks):
        for _ in t:
            char2tok.append(i)
    full = "".join(t[0] for t in toks)
    if not full:
        return toks
    out = []
    pos = 0
    for w in jieba.cut(full):
        if pos >= len(char2tok):
            break
        i0 = char2tok[pos]
        i1 = char2tok[min(pos + len(w) - 1, len(char2tok) - 1)]
        out.append((w, toks[i0][1], toks[i1][2]))
        pos += len(w)
    return out

PUNCT_END = "。！？；!?;"
PUNCT_PAUSE = "，、,:："
# 语气词（单字，token 级剔除；不影响实词）。扩展集与 clean_fillers.MOOD_SINGLE 对齐
MOOD = set("呃啊呀哎嗯呢吧嘛哦哈啦咧喽咯哟喔诶呸哼嘞喏咦嗳呦喂")

# 句单元级语气词/填充词清洗（前置到转写源）：复用 clean_fillers.clean_one，
# 覆盖复音填充词（然后/其实/就是说/这个/那个…）与单字语气词（含 token 级漏网的"呃"）。
try:
    from clean_fillers import clean_one as _clean_one_unit
except Exception:  # 独立运行或技能目录未加入 path 时降级
    _clean_one_unit = None
# 关键词高亮候选：拉丁字母/数字/百分号连续段
import re as _re
LATIN_NUM = _re.compile(r"[A-Za-z0-9][A-Za-z0-9%~.·]*")


def tokens_of(u):
    """返回 [(text, start_us, end_us)]，跳过 [x seconds silent] 标记"""
    out = []
    for t in u.get("tokens", []):
        txt = t.get("text", "")
        if not txt or txt.startswith("["):
            continue
        out.append((txt, t.get("start", 0), t.get("end", 0)))
    if not out:
        txt = (u.get("text") or "").strip()
        if txt:
            out = [(txt, u.get("start", 0), u.get("end", 0))]
    return out


def apply_dict(text, fixdict):
    """ASR 纠错：整词/片段替换（如 Cortex->Codex、剪影->剪映、SQL->skill）"""
    if not fixdict:
        return text
    for k, v in fixdict.items():
        if k in text:
            text = text.replace(k, v)
    return text


def build_units(utterances, max_chars, hard_max, min_chars, fixdict=None):
    """把 utterances 切成 [{'text','s_us','e_us','toks'}]；切分点一律落在词边界（不切断词）"""
    units = []
    for u in utterances:
        toks = tokens_of(u)
        if not toks:
            continue
        if fixdict:
            toks = [(apply_dict(t, fixdict), s, e) for t, s, e in toks]
        # 语气词剔除：token 去标点后恰为单个语气字 → 丢弃（时间归相邻句）
        cleaned = []
        for t, a, b in toks:
            core = t.strip()
            core = core.rstrip("".join(PUNCT_END + PUNCT_PAUSE)).lstrip("".join(PUNCT_END + PUNCT_PAUSE))
            if len(core) == 1 and core in MOOD:
                continue
            cleaned.append((t, a, b))
        toks = cleaned
        if not toks:
            continue
        toks = rejoin_jieba(toks)  # jieba 词边界重切（防「标题」「文件夹」被拆到两句）
        buf = []
        for item in toks:
            buf.append(item)
            joined = "".join(x[0] for x in buf).strip()
            tail = item[0][-1:] if item[0] else ""
            force_end = tail in PUNCT_END
            soft_end = tail in PUNCT_PAUSE
            if force_end or (soft_end and len(joined) >= max_chars):
                units.append({"text": joined, "s_us": buf[0][1], "e_us": buf[-1][2], "toks": list(buf)})
                buf = []
            elif len(joined) >= hard_max:
                # 到硬上限：先回头找 [max_chars, hard_max] 内最近的标点作为切点（不切断词）
                cut = None
                for i in range(len(buf) - 1, -1, -1):
                    tk = buf[i][0]
                    if tk and tk[-1] in (PUNCT_END + PUNCT_PAUSE):
                        if len("".join(x[0] for x in buf[:i + 1])) >= max_chars:
                            cut = i
                            break
                if cut is not None:
                    head, tail = buf[:cut + 1], buf[cut + 1:]
                else:
                    head, tail = buf, []
                seg = "".join(x[0] for x in head).strip()
                if seg:
                    units.append({"text": seg, "s_us": head[0][1], "e_us": head[-1][2], "toks": list(head)})
                buf = list(tail)
        if buf:
            joined = "".join(x[0] for x in buf).strip()
            if joined:
                units.append({"text": joined, "s_us": buf[0][1], "e_us": buf[-1][2], "toks": list(buf)})

    # 合并过短单元（同样按词边界）
    merged = []
    for un in units:
        if merged and len(un["text"]) < min_chars and len(merged[-1]["text"]) + len(un["text"]) <= max_chars:
            merged[-1]["text"] += un["text"]
            merged[-1]["e_us"] = un["e_us"]
            merged[-1]["toks"] = merged[-1]["toks"] + un["toks"]
        else:
            merged.append({"text": un["text"], "s_us": un["s_us"], "e_us": un["e_us"], "toks": list(un["toks"])})

    # 超长单元：按 token 边界重切（不跨词；单 token 过长时独占一段）
    final = []
    for un in merged:
        if len(un["text"]) <= hard_max:
            final.append(un)
            continue
        cur = []
        for tk in un["toks"]:
            cur.append(tk)
            if len("".join(x[0] for x in cur)) >= max_chars:
                seg = "".join(x[0] for x in cur).strip()
                if seg:
                    final.append({"text": seg, "s_us": cur[0][1], "e_us": cur[-1][2], "toks": list(cur)})
                cur = []
        if cur:
            seg = "".join(x[0] for x in cur).strip()
            if seg:
                final.append({"text": seg, "s_us": cur[0][1], "e_us": cur[-1][2], "toks": list(cur)})

    # 句单元级语气词/填充词清洗（前置到转写源）：去掉后不影响语义，字幕更干净。
    # 仅改 text；token 时间跨度(s_us/e_us)保留，字幕仍按原时段显示。
    if _clean_one_unit:
        cleaned = []
        for un in final:
            ct = _clean_one_unit(un["text"])
            if ct:
                un["text"] = ct
                cleaned.append(un)
        final = cleaned
    return final


def strip_edge_punct(t):
    """去掉句首/句尾的标点（字幕不显示结尾标点）"""
    extra_tail = "\"'）)]」》"
    extra_head = "\"'（([「《"
    t = t.strip()
    while t and t[-1] in (PUNCT_END + PUNCT_PAUSE + extra_tail):
        t = t[:-1].rstrip()
    while t and t[0] in (PUNCT_PAUSE + extra_head):
        t = t[1:].lstrip()
    return t


def pick_accent(text, highlights):
    """关键词高亮：显式词表（最长优先）→ 拉丁/数字连续段（最长优先）；无则空串"""
    cands = []
    for h in highlights or []:
        if h and h in text:
            cands.append(h)
    cands += [m.group(0) for m in LATIN_NUM.finditer(text)]
    if not cands:
        return ""
    cands.sort(key=len, reverse=True)
    return cands[0]


def to_frames(units, segs, fps):
    """源微秒 -> 时间线帧（按 V1 片段表）"""
    def t2f(us):
        s = us / 1e6
        for row in segs:
            sf, dur, s0 = row[2], row[3], row[4] / 1e6
            if s0 - 0.002 <= s <= s0 + dur / float(fps) + 0.002:
                return int(round(sf + (s - s0) * fps))
        return None

    out = []
    for un in units:
        a = t2f(un["s_us"])
        b = t2f(un["e_us"])
        if a is None:
            continue
        if b is None or b <= a:
            b = a + 6
        txt = strip_edge_punct(un["text"])
        if not txt:
            continue
        out.append({"text": txt, "accentWord": pick_accent(txt, un.get("_hl")),
                    "startFrame": a, "endFrame": b,
                    "srcStartUs": un["s_us"], "srcEndUs": un["e_us"]})
    # 去重叠：后一句起点不得早于前一句终点 - 2
    for i in range(1, len(out)):
        if out[i]["startFrame"] < out[i - 1]["endFrame"] - 2:
            out[i - 1]["endFrame"] = max(out[i - 1]["startFrame"] + 4, out[i]["startFrame"] - 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trans", required=True)
    ap.add_argument("--segs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-chars", type=int, default=14)
    ap.add_argument("--hard-max", type=int, default=20)
    ap.add_argument("--min-chars", type=int, default=4)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--highlights", default=None,
                    help="关键词高亮词表 JSON（字符串数组，最长优先），如 [\"Codex\",\"记忆库\"]")
    ap.add_argument("--dict", dest="fixdict", default=None,
                    help="ASR 纠错词典 JSON：{\"Cortex\":\"Codex\", \"剪影\":\"剪映\"}")
    a = ap.parse_args()

    d = json.load(open(a.trans, encoding="utf-8"))
    segs = json.load(open(a.segs, encoding="utf-8"))
    fixdict = json.load(open(a.fixdict, encoding="utf-8")) if a.fixdict else None
    hl = json.load(open(a.highlights, encoding="utf-8")) if a.highlights else []
    if _JIEBA:
        for w in list((fixdict or {}).values()) + list(hl or []):
            if w and len(w) >= 2:
                jieba.add_word(w)
    units = build_units(d["utterances"], a.max_chars, a.hard_max, a.min_chars, fixdict)
    for un in units:
        un["_hl"] = hl
    frames = to_frames(units, segs, a.fps)
    json.dump(frames, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    n_hl = len([x for x in frames if x.get("accentWord")])
    lens = [len(u["text"]) for u in frames]
    print("高亮句 %d / %d" % (n_hl, len(frames)))
    print("句数 %d | 平均 %.1f 字 | 最长 %d 字 | 超过硬上限 %d 句"
          % (len(frames), sum(lens) / max(1, len(lens)), max(lens) if lens else 0,
             len([x for x in lens if x > a.hard_max])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
