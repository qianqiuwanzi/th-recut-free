# -*- coding: utf-8 -*-
"""make_pip_source.py —— 竖屏 PIP 人物源「按人脸中心正方形裁切」唯一正解生成器。

🔴 为什么必须用它（2026-10-06 根因）：
   竖屏圆形 PIP 小窗（200×200 @ borderRadius=100）要求源是「正方形且人脸在正中心」。
   若用「顶部正方形裁切」crop=W:W:0:0，人脸被挤到方形偏下 → 入圆后人头偏下、不居中
   （用户反馈「窗口只显示人物窗口四字 / 看不到人 / 人头没居中」）。
   本脚本强制按人脸中心裁正方形：脸中心 = 方形中心，等比映射入圆窗后人头正落圆心。

用法：
   python make_pip_source.py --video src.mp4 --out pip_src_V.mp4
   python make_pip_source.py --video src.mp4 --out pip_src_V.mp4 --frames 14
   python make_pip_source.py --video src.mp4 --out pip_src_V.mp4 --push-name "PIP源-V"

产出：正方形视频（边长 = min(W, 2*xc, 2*yc, 2*(W-xc), 2*(H-yc))），人脸精确居中。
配套：pipeline V_SPECS 的 'PIP源-V' 必须推本脚本产物；禁止任何 ad-hoc 的
       crop=W:W:0:0 顶部裁切（那是导致「人头不居中」的根因，已在 batch930 踩坑）。

依赖：opencv-python(cv2) + ffmpeg(在 PATH)。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import cv2

CASCADES = ["haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml"]
DEF_FRAMES = 12
DEF_SCALE = 1.05
DEF_NEIGH = 4
DEF_FACE_PAD = 0.10  # 人脸框外扩，避免贴边


def _ffprobe(video):
    out = subprocess.run(["ffprobe", "-v", "error",
                          "-select_streams", "v:0",
                          "-show_entries", "stream=width,height",
                          "-show_entries", "stream_side_data=rotation",
                          "-of", "json", video],
                         capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    rot = 0
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    if abs(rot) % 180 == 90:
        w, h = h, w
    return w, h


def _extract_frames(video, n):
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "format=duration", "-of", "default=nw=1:nk=1", video],
                               capture_output=True, text=True, check=True).stdout.strip())
    tmp = tempfile.mkdtemp(prefix="pipsrc_")
    paths = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        p = os.path.join(tmp, "f%02d.jpg" % i)
        subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", video,
                        "-frames:v", "1", "-y", p], check=True)
        paths.append(p)
    return paths


def _detect_center(img_path, scale, neigh):
    img = cv2.imread(img_path)
    if img is None:
        return None
    H, W = img.shape[:2]
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    best = None
    for cname in CASCADES:
        c = cv2.CascadeClassifier(cv2.data.haarcascades + cname)
        if c.empty():
            continue
        for (x, y, fw, fh) in c.detectMultiScale(gray, scaleFactor=scale, minNeighbors=neigh,
                                                 minSize=(int(W * 0.04), int(H * 0.04))):
            area = fw * fh
            if best is None or area > best[2] * best[3]:
                best = (x, y, fw, fh)
    if best is None:
        return None
    x, y, fw, fh = best
    return (x + fw / 2.0, y + fh / 2.0, W, H)


def detect_face_center(video, n, scale, neigh):
    frames = _extract_frames(video, n)
    centers = []
    for p in frames:
        c = _detect_center(p, scale, neigh)
        if c:
            centers.append(c[:2])
    if not centers:
        # 退一步：放宽
        for p in frames:
            c = _detect_center(p, 1.03, 2)
            if c:
                centers.append(c[:2])
    if not centers:
        return None
    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    # 去离群（保留落在中位数 25% 内）
    mx, my = sorted(xs)[len(xs) // 2], sorted(ys)[len(ys) // 2]
    fx = [x for x in xs if abs(x - mx) < 0.25 * max(xs)]
    fy = [y for y in ys if abs(y - my) < 0.25 * max(ys)]
    if fx and fy:
        xs, ys = fx, fy
    xc = sum(xs) / len(xs)
    yc = sum(ys) / len(ys)
    return xc, yc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=DEF_FRAMES)
    ap.add_argument("--scale", type=float, default=DEF_SCALE)
    ap.add_argument("--neighbors", type=int, default=DEF_NEIGH)
    ap.add_argument("--push-name", default=None, help="若给定，则 push_asset 为同名资产")
    a = ap.parse_args()

    W, H = _ffprobe(a.video)
    print("[make_pip_source] source %dx%d" % (W, H))
    c = detect_face_center(a.video, a.frames, a.scale, a.neighbors)
    if not c:
        print("[X] 未检测到人脸，无法居中裁切。请人工确认源或用 --frames/--scale 调整。", file=sys.stderr)
        sys.exit(2)
    xc, yc = c
    print("[make_pip_source] 人脸中心(像素): xc=%.1f yc=%.1f (画幅 %dx%d)" % (xc, yc, W, H))

    # 正方形边长：保证脸中心=方形中心且四边不越界
    Sc = int(min(W, 2 * xc, 2 * yc, 2 * (W - xc), 2 * (H - yc)))
    left = int(round(xc - Sc / 2.0))
    top = int(round(yc - Sc / 2.0))
    left = max(0, min(left, W - Sc))
    top = max(0, min(top, H - Sc))
    print("[make_pip_source] crop=%d:%d:%d:%d (边长 %d, 脸中心=方形中心)" % (Sc, Sc, left, top, Sc))

    subprocess.run(["ffmpeg", "-v", "error", "-i", a.video,
                    "-vf", "crop=%d:%d:%d:%d" % (Sc, Sc, left, top),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-y", a.out], check=True)
    print("[OK] 写出正方形源:", a.out, "(%dx%d)" % (Sc, Sc))

    if a.push_name:
        # 通过 ccmcp 桥 push_asset（ChatCut 桌面需处于打开状态）
        try:
            import importlib.util
            # 直接用 subprocess 调 ccmcp.py 若存在
            here = os.path.dirname(os.path.abspath(__file__))
            ccmcp = os.path.join(os.path.dirname(here), "..", "..", "tmp", "ccmcp.py")
            if not os.path.exists(ccmcp):
                ccmcp = os.path.join(here, "ccmcp.py")
            if os.path.exists(ccmcp):
                ops = [{"tool": "push_asset", "args": {"filePath": os.path.abspath(a.out),
                                                        "type": "video", "name": a.push_name}}]
                opj = os.path.join(here, "_push_pipsrc_ops.json")
                json.dump(ops, open(opj, "w", encoding="utf-8"), ensure_ascii=False)
                subprocess.run([sys.executable, ccmcp, opj], check=True)
                print("[OK] 已 push_asset 为 '%s'" % a.push_name)
            else:
                print("[!] 未找到 ccmcp.py，跳过 push；请手动 push_asset %s 命名 '%s'" % (a.out, a.push_name))
        except Exception as e:
            print("[!] push 失败(不影响产物):", e, file=sys.stderr)


if __name__ == "__main__":
    main()
