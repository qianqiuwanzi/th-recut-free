# -*- coding: utf-8 -*-
"""
check_audio_caption_sync.py — 导出前「音轨/字幕一致性」闸门（根因固化 · 2026-10-10）

问题背景
--------
口播成片的字幕在本技能里是烧录进画面的 MG 文本覆盖层（不是独立字幕轨）。
它们的时间码必须来自**最终播出的那条音轨**的转录。若有人把播出音轨
从「源视频内嵌音」换成了「另一条独立人声」（或反之）却没重新按新音轨
生成字幕，就会出现「字幕快/慢于语音、结尾语音没播完」的不同步缺陷。

本闸门在导出前强制校验三条不变量，任一不满足即退出码 1 阻断导出：

  ① 音轨身份一致：实际播出的音轨资产 == 字幕所依据的音轨资产。
     - 若播出音是 V1 内嵌音（videoItems.audioStreamId != "off"）且无独立人声项
       → 字幕按剪辑后 V1 定时，天然一致，自动通过。
     - 若存在独立人声项 → 必须传 --caption-source-audio <字幕依据的资产id>；
       若它与播出人声资产不一致 → 阻断（字幕没随音轨重做）。
  ② 无截断：每个独立人声项的时长 >= 其人声资产时长 - 容差，否则结尾被截。
  ③ （可选）转录内容校验：传 --audio-transcription 后，逐条字幕文本须能在
       最终音轨转录中找到、且时间（经分段映射）落在容差内。

用法
----
  python check_audio_caption_sync.py --project 2984e5f1
  python check_audio_caption_sync.py --project df5a5467 \
        --caption-source-audio 41c3f157-xxxx --audio-transcription voice.json
  python check_audio_caption_sync.py --timeline "..../Timeline_xxx.json"

退出码：0 = 通过；1 = 阻断（打印原因）。
"""
import json, os, sys, glob, argparse

def default_projects_root():
    """ChatCut 工程根目录：env CHATCUT_PROJECTS > %APPDATA%\\ChatCut\\projects（跨账号可移植）。"""
    env = os.environ.get("CHATCUT_PROJECTS")
    if env:
        return env
    appdata = os.environ.get("APPDATA") or r"C:\Users\Administrator\AppData\Roaming"
    return os.path.join(appdata, "ChatCut", "projects")


def find_timeline(pid=None, timeline=None, projects_root=None):
    if timeline:
        return timeline
    root = projects_root or default_projects_root()
    pat = os.path.join(root, f"{pid}*", "project.chatcutproject", "timelines", "Timeline_*.json")
    hits = glob.glob(pat)
    if not hits:
        # try any project dir
        pat2 = os.path.join(root, "*", "project.chatcutproject", "timelines", "Timeline_*.json")
        hits = glob.glob(pat2)
    return hits[0] if hits else None

def load_timeline(path):
    d = json.load(open(path, encoding="utf-8"))
    return d.get("timelines", [d])[0] if "timelines" in d else d

def project_assets_dir(timeline_path):
    # .../projects/<pid>/project.chatcutproject/timelines/Timeline_x.json
    # -> .../projects/<pid>/project.chatcutproject/assets
    return os.path.join(os.path.dirname(os.path.dirname(timeline_path)), "assets")

def read_asset_duration(assets_dir, asset_id):
    """Scan asset jsons for assetId, return duration seconds (microsec field / 1e6)."""
    for root, _, files in os.walk(assets_dir):
        for f in files:
            if not f.endswith(".json"):
                continue
            p = os.path.join(root, f)
            try:
                j = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            if isinstance(j, dict) and j.get("id") == asset_id:
                dur = j.get("duration")
                if isinstance(dur, (int, float)):
                    return dur / 1e6
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--timeline")
    ap.add_argument("--projects", help="ChatCut 工程根目录（覆盖 env CHATCUT_PROJECTS / %%APPDATA%% 默认）")
    ap.add_argument("--caption-source-audio", help="字幕所依据的音轨资产 id（独立人声播出时必填）")
    ap.add_argument("--audio-transcription", help="最终播出音轨的转录 json（可选，做内容+时间校验）")
    ap.add_argument("--tolerance", type=float, default=1.5)
    ap.add_argument("--max-mismatch-ratio", type=float, default=0.2)
    a = ap.parse_args()

    tl_path = find_timeline(a.project, a.timeline, a.projects)
    if not tl_path:
        print("ERR: 找不到时间线"); sys.exit(1)
    tl = load_timeline(tl_path)
    fps = tl.get("fps") or tl.get("frameRate") or 30
    tr = {t.get("id"): (t.get("name") or t.get("type")) for t in tl.get("tracks", [])}

    # ---- caption (MG text overlay) items ----
    caps = []
    for m in tl.get("motionGraphicItems", []):
        po = m.get("propertyOverrides") or {}
        txt = po.get("text") or po.get("content") or ""
        if not txt:
            continue
        caps.append((m.get("startFrame", 0) / fps, (m.get("startFrame", 0) + m.get("durationFrames", 0)) / fps, txt))
    if not caps:
        print("NOTE: 未检测到 MG 文本字幕项（可能用系统字幕轨，本闸门跳过字幕校验）")
        caps = []
    caps.sort()
    print(f"[info] 字幕 MG 项: {len(caps)} 条, span {caps[0][0]:.2f}-{caps[-1][1]:.2f}s" if caps else "[info] 无字幕项")

    # ---- base video track + embedded audio on? ----
    vids = tl.get("videoItems", [])
    embedded_on = any(str(v.get("audioStreamId")).lower() not in ("off", "none", "", "0") for v in vids)
    base_video_asset = vids[0].get("assetId") if vids else None
    # which video items are "on"
    on_vids = [v for v in vids if str(v.get("audioStreamId")).lower() not in ("off", "none", "", "0")]

    # ---- standalone audio items (non-bgm) ----
    audio_items = tl.get("audioItems", [])
    voice_items = []
    for ai in audio_items:
        tname = (tr.get(ai.get("trackId")) or "").lower()
        if "bgm" in tname or "music" in tname or "背景" in tname:
            continue
        voice_items.append(ai)
    audible_asset = voice_items[0].get("assetId") if voice_items else None

    print(f"[info] 内嵌源音开启={embedded_on}  独立人声项={len(voice_items)}  播出人声资产={audible_asset}")

    errors = []

    # ---- Invariant ① : audio identity ----
    if voice_items and not embedded_on:
        # standalone voice is the audible track
        if not a.caption_source_audio:
            errors.append("播出音轨为独立人声，但未传 --caption-source-audio：无法确认字幕依据的音轨，禁止导出。")
        elif a.caption_source_audio != audible_asset:
            errors.append(
                f"字幕依据音轨({a.caption_source_audio}) ≠ 播出音轨({audible_asset})："
                f"字幕未随音轨重做，会出现字幕/语音不同步。请按播出音轨重新生成字幕，"
                f"或恢复字幕依据的音轨。")
    elif embedded_on and not voice_items:
        # captions timed to edited V1 by construction -> OK
        pass
    elif embedded_on and voice_items:
        errors.append("同时存在内嵌源音与独立人声项，音频来源歧义，请二选一后再导出。")
    else:
        # neither -> silent video (ok if no captions, else suspicious)
        if caps:
            errors.append("未检测到任何播出音轨，但存在字幕项：观众将看到无语音的字幕。")

    # ---- Invariant ② : no truncation of standalone voice ----
    assets_dir = project_assets_dir(tl_path)
    for ai in voice_items:
        adur = ai.get("durationFrames", 0) / fps
        asset_dur = read_asset_duration(assets_dir, ai.get("assetId"))
        if asset_dur is not None and adur < asset_dur - a.tolerance:
            errors.append(
                f"人声项时长 {adur:.2f}s < 资产时长 {asset_dur:.2f}s（差 {asset_dur-adur:.2f}s）："
                f"结尾语音会被截断。请拉长人声项或缩短时间线。")

    # ---- Invariant ③ : optional transcription content+timing check ----
    if a.audio_transcription and caps:
        td = json.load(open(a.audio_transcription, encoding="utf-8"))
        utts = [(u["start"]/1e6, u["end"]/1e6, u["text"]) for u in td.get("utterances", [])]
        def han(s): return "".join(c for c in s if '一' <= c <= '鿿')
        def ov(aa, bb):
            sa = set(han(aa)); return sum(1 for c in han(bb) if c in sa)
        # segment map (for embedded audio, caption time -> source time)
        segs = sorted([(v.get("startFrame",0)/fps, (v.get("startFrame",0)+v.get("durationFrames",0))/fps, v.get("sourceIn",0)/1e6) for v in on_vids or vids])
        def src_time(T):
            for s0,s1,si in segs:
                if s0-1e-6 <= T < s1-1e-6:
                    return si + (T - s0)
            return segs[-1][2] + (T - segs[-1][0]) if segs else T
        mismatch = 0
        for cs, ce, ct in caps:
            Tmid = (cs + ce) / 2
            best = max(utts, key=lambda u: ov(ct, u[2])) if utts else None
            if best is None:
                mismatch += 1; continue
            S = src_time(Tmid) if embedded_on else Tmid
            if not (best[0]-a.tolerance <= S <= best[1]+a.tolerance):
                mismatch += 1
        ratio = mismatch / len(caps)
        print(f"[info] 转录校验: {len(caps)-mismatch}/{len(caps)} 条时间落在容差内")
        if ratio > a.max_mismatch_ratio:
            errors.append(f"转录校验失配率 {ratio:.0%} > {a.max_mismatch_ratio:.0%}：字幕与最终音轨不同步。")

    if errors:
        print("\n".join("BLOCK: " + e for e in errors))
        sys.exit(1)
    print("PASS: 音轨/字幕一致性校验通过。")
    sys.exit(0)

if __name__ == "__main__":
    main()
