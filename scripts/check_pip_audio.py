#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_pip_audio.py — PIP 布局音频单声源闸门 (talking-head-recut §3.6.6.4)

根因 (2026-10-10 batch930 A/B/C):
  PIP 人像窗 (`pip` 轨) 与主轨 (`main`/V1) 都用 audioStreamId="default"，
  二者同源或同一说话人 -> 人声叠两遍 (同相加倍增益 / 错位回声相位叠加)。
  表现: 闷、回声感、"双音频叠加"。

本闸门在导出前校验两条不变量:
  ① PIP 轨 videoItems 必须 audioStreamId="off" (人像窗是纯视觉嵌入层，不发声)。
  ② 全时间轴最多一个 "视频发声层" (main V1) 处于 audible；若存在第二个
     带 audio=default 的视频轨且其与 main 在时间上重叠，则判定双音频风险并阻断。

用法:
  python check_pip_audio.py --project <pid> [--timeline <json_path>]
  --allow-pip-audio   显式允许 PIP 发声 (仅当 PIP 是独立、非重叠的配音/反应镜头时)

退出码: 0=通过, 1=阻断导出。
"""
import argparse, json, glob, os, sys

def default_projects_root():
    """ChatCut 工程根目录：env CHATCUT_PROJECTS > %APPDATA%\\ChatCut\\projects（跨账号可移植）。"""
    env = os.environ.get("CHATCUT_PROJECTS")
    if env:
        return env
    appdata = os.environ.get("APPDATA") or r"C:\Users\Administrator\AppData\Roaming"
    return os.path.join(appdata, "ChatCut", "projects")


def load_timeline(project, timeline_path=None, projects_root=None):
    if timeline_path:
        return json.load(open(timeline_path, encoding="utf-8"))
    root = projects_root or default_projects_root()
    # 精确全 id
    base = os.path.join(root, project, "project.chatcutproject")
    fs = [f for f in glob.glob(os.path.join(base, "timelines", "Timeline_*.json"))
          if not f.endswith(".bak930b")]
    if not fs:
        # 短 id 前缀匹配 ChatCut projects 目录
        if os.path.isdir(root):
            for d in os.listdir(root):
                if d.startswith(project) and os.path.isdir(os.path.join(root, d, "project.chatcutproject")):
                    fs = [f for f in glob.glob(os.path.join(root, d, "project.chatcutproject",
                                                            "timelines", "Timeline_*.json"))
                          if not f.endswith(".bak930b")]
                    if fs:
                        break
    if not fs:
        raise SystemExit(f"[ERR] cannot locate timeline for project {project}")
    return json.load(open(fs[0], encoding="utf-8"))

def tracks_of(tl):
    t = tl.get("timelines", [tl])[0]
    return t

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--projects", default=None,
                    help="ChatCut 工程根目录（覆盖 env CHATCUT_PROJECTS / %%APPDATA%% 默认）")
    ap.add_argument("--allow-pip-audio", action="store_true",
                    help="显式允许 PIP 发声 (独立非重叠配音/反应镜头)")
    args = ap.parse_args()

    tl = load_timeline(args.project, args.timeline, args.projects)
    t = tracks_of(tl)
    tracks = {tr.get("id"): (tr.get("name") or "") for tr in t.get("tracks", [])}
    fps = t.get("fps") or 30

    video_items = t.get("videoItems", [])
    pip_track_ids = {tid for tid, nm in tracks.items() if nm.lower() == "pip"}
    # main = unnamed video track (first video track without a name)
    main_track_ids = {tid for tid, nm in tracks.items()
                      if (tracks.get(tid, "") == "") and
                      any(v.get("trackId") == tid for v in video_items)}

    errors = []
    pip_on = []
    audible_video_tracks = set()
    for v in video_items:
        tid = v.get("trackId"); aud = v.get("audioStreamId")
        if tid in pip_track_ids:
            if aud != "off":
                pip_on.append((v.get("id"), aud))
        if aud not in (None, "off"):
            audible_video_tracks.add(tid)

    # ① PIP 必须静音
    if pip_on and not args.allow_pip_audio:
        errors.append(
            f"PIP 轨存在 {len(pip_on)} 个发声项 (audioStreamId!='off'): "
            + ", ".join(f"{i}={a}" for i, a in pip_on)
            + " — 人像窗是纯视觉嵌入层，必须静音以避免双音频叠加。")

    # ② 双视频发声层风险
    if len(audible_video_tracks) >= 2:
        names = [tracks.get(tid, tid[:8]) for tid in audible_video_tracks]
        # 检查是否有时间重叠
        def segs(tid):
            out = []
            for v in video_items:
                if v.get("trackId") == tid and v.get("audioStreamId") not in (None, "off"):
                    s = v.get("startFrame", 0) / fps
                    e = (v.get("startFrame", 0) + v.get("durationFrames", 0)) / fps
                    out.append((s, e))
            return out
        overlap = False
        alls = []
        for tid in audible_video_tracks:
            alls += segs(tid)
        alls.sort()
        for i in range(len(alls) - 1):
            if alls[i][1] > alls[i + 1][0]:
                overlap = True
                break
        if overlap:
            errors.append(
                f"检测到 {len(audible_video_tracks)} 个视频发声层且时间重叠 "
                f"({', '.join(names)}) — 双音频叠加风险，仅保留一个发声层。")

    if errors:
        print("[FAIL] PIP 音频单声源校验未通过:")
        for e in errors:
            print("  - " + e)
        print("[FAIL] 阻断导出。请静音 PIP 轨 (audioStreamId='off')，"
              "确保全时间轴仅一个视频发声层。")
        sys.exit(1)
    print("[PASS] PIP 音频单声源校验通过 (PIP 静音, 单一视频发声层)。")
    sys.exit(0)

if __name__ == "__main__":
    main()
