# -*- coding: utf-8 -*-
"""
check_card_layout.py —— 主卡布局闸门（根因固化 · 2026-10-09 增补）

问题根因：batch930 A 有 6 张「主卡(card 轨)」被放在画布正中央
(L=550 T=290 W=523 H=300, 卡中心≈(811,440)=画布中心)，而口播人物是
正中说话头，于是主卡盖住人脸。A 之前的「PIP 修复」只动了 fs 段，这 6
张主卡一直没动 —— 用户反馈「很多主卡还在正中央遮脸」。

本闸门校验：每张 card 轨主卡都不能压在「实测人脸框」上。
  - 人脸框优先用 --faceband（detect_faces 产出，画布坐标
    {"x0","y0","x1","y1"}）；
  - 否则用 --mp4 从成片抽帧做 Haar 检脸（最准，适合导出后验收）；
  - 都没有才退化为「上中危险带」启发式，并打印 WARNING（可能误报，
    竖屏人脸偏下时尤其会误报，务必补 --faceband/--mp4）。

判定：主卡包围盒与(人脸框+边距)相交 → 遮脸。
退出码：发现任意主卡遮脸 → 1（阻断 local_export）；全部通过 → 0。

用法：
  python check_card_layout.py --project <pid> [--faceband fb.json | --mp4 x.mp4]
"""
import json
import os
import sys
import glob
import subprocess

# 退化用：上中危险带（占画布比例）
FX0R, FX1R, FY0R, FY1R = 0.40, 0.62, 0.05, 0.50
PAD = 40  # 人脸框外扩边距(画布像素)，避免贴边误判


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
    base = projects_root or default_projects_root()
    if pid:
        cand = glob.glob(os.path.join(base, pid + "*", "project.chatcutproject",
                                       "timelines", "Timeline_*.json"))
        cand = [c for c in cand if not c.endswith(".bak930")]
        if cand:
            return sorted(cand, key=os.path.getmtime)[-1]
    projects = glob.glob(os.path.join(base, "*", "project.chatcutproject",
                                      "timelines", "Timeline_*.json"))
    projects = [p for p in projects if not p.endswith(".bak930")]
    if not projects:
        return None
    return sorted(projects, key=os.path.getmtime)[-1]


def detect_face_band_from_mp4(mp4, W, H):
    import cv2
    qc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_qc_tmp")
    os.makedirs(qc, exist_ok=True)
    casc = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    fc = cv2.CascadeClassifier(casc)
    # 抽 6 帧均匀采样
    boxes = []
    for i in range(6):
        fr = os.path.join(qc, f"_fb{i}.png")
        subprocess.run(["ffmpeg", "-y", "-ss", str(i * 1.0 + 0.5),
                        "-i", mp4, "-frames:v", "1", fr],
                       capture_output=True)
        img = cv2.imread(fr)
        if img is None:
            continue
        ih, iw = img.shape[:2]
        d = fc.detectMultiScale(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
                                1.15, 4, minSize=(int(iw * 0.07), int(ih * 0.07)))
        for (x, y, w, h) in d:
            boxes.append((x * W / iw, y * H / ih, (x + w) * W / iw, (y + h) * H / ih))
    if not boxes:
        return None
    # 取并集后再收缩到中位尺度，降低偶发误检：用最大的几张取并集
    xs0 = min(b[0] for b in boxes); ys0 = min(b[1] for b in boxes)
    xs1 = max(b[2] for b in boxes); ys1 = max(b[3] for b in boxes)
    return (xs0, ys0, xs1, ys1)


def main():
    args = sys.argv[1:]
    pid = None
    timeline = None
    faceband = None
    mp4 = None
    projects_root = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--project":
            pid = args[i + 1]; i += 2
        elif a == "--timeline":
            timeline = args[i + 1]; i += 2
        elif a == "--projects":
            projects_root = args[i + 1]; i += 2
        elif a == "--faceband":
            faceband = json.load(open(args[i + 1], encoding="utf-8")); i += 2
        elif a == "--mp4":
            mp4 = args[i + 1]; i += 2
        else:
            i += 1

    path = find_timeline(pid, timeline, projects_root)
    if not path:
        print("[X] 找不到 timeline 文件"); sys.exit(1)
    d = json.load(open(path, encoding="utf-8"))
    tl = d.get("timelines", [d])[0] if "timelines" in d else d
    W = tl.get("compositionWidth"); H = tl.get("compositionHeight")
    if not W or not H:
        print("[X] timeline 缺少 compositionWidth/Height"); sys.exit(1)

    band_src = "默认上中危险带(启发式)"
    if faceband:
        bx0, by0, bx1, by1 = (faceband["x0"], faceband["y0"],
                              faceband["x1"], faceband["y1"])
        band_src = "faceband.json(实测)"
    elif mp4 and os.path.exists(mp4):
        fb = detect_face_band_from_mp4(mp4, W, H)
        if fb:
            bx0, by0, bx1, by1 = fb
            band_src = "mp4 Haar 实测"
        else:
            bx0, by0, bx1, by1 = FX0R * W, FY0R * H, FX1R * W, FY1R * H
            band_src = "默认上中危险带(启发式, Haar未检到脸)"
    else:
        bx0, by0, bx1, by1 = FX0R * W, FY0R * H, FX1R * W, FY1R * H

    # 外扩边距
    bx0, by0, bx1, by1 = bx0 - PAD, by0 - PAD, bx1 + PAD, by1 + PAD

    tr = {t.get("id"): (t.get("name") or t.get("type")) for t in tl.get("tracks", [])}
    bad = []
    print(f"timeline: {path}")
    print(f"canvas {W}x{H}; 人脸框[{band_src}]: x[{bx0:.0f},{bx1:.0f}] y[{by0:.0f},{by1:.0f}]")
    if band_src.startswith("默认"):
        print("  [WARNING] 启发式危险带可能误报(尤其竖屏人脸偏下)，建议补 --faceband/--mp4")
    for it in tl.get("motionGraphicItems", []):
        if tr.get(it.get("trackId")) != "card":
            continue
        l = it.get("left"); t = it.get("top"); w = it.get("width"); h = it.get("height")
        if None in (l, t, w, h):
            continue
        ov = not (l + w < bx0 or l > bx1 or t + h < by0 or t > by1)
        po = it.get("propertyOverrides") or {}
        title = po.get("title") or po.get("kicker") or ""
        cx, cy = l + w / 2, t + h / 2
        tag = "[X] 遮脸" if ov else "[OK]"
        print(f"  {tag} card L={l} T={t} W={w} H={h} center=({cx:.0f},{cy:.0f}) | {title!r}")
        if ov:
            bad.append(title)

    if bad:
        print(f">>> 结论: ❌ {len(bad)} 张主卡压在人脸框，阻断导出")
        sys.exit(1)
    print(">>> 结论: ✅ 全部主卡避开人脸框")


if __name__ == "__main__":
    main()
