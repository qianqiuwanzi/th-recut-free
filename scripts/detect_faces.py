# -*- coding: utf-8 -*-
"""口播视频「画幅 + 人脸禁遮挡区 + 可用布局带」探测 / 校验（talking-head-recut 配套脚本）

设计原则（重要）：
  本脚本是**通用工具**，不绑定任何具体视频。
  所有人脸位置、布局带、字幕带一律**现场检测推导**，代码里不存在任何
  「某条视频的人脸在这里」式的硬编码坐标。
  唯一可配置的是**容差/尺寸类参数**（外扩比例、最小可用带尺寸、间隙），
  它们有合理默认值，可通过 CLI 覆盖，并在输出的 `params` 字段里完整回报，
  便于复现与追责。

两种用法：
  1) 探测（Step 0 / Step 8）
     python detect_faces.py --video in.mp4 --frames 10 --out zones.json
     → display_size（建画布用）/ forbidden_zone / available_bands / subtitle_band / verdict

  2) 零交叠校验（布点后、渲染前）
     python detect_faces.py --zones zones.json --check boxes.json
     → 逐张卡片算与 forbidden_zone / subtitle_band 的交叠，给 PASS/FAIL

依赖：opencv-python（cv2）、ffmpeg / ffprobe（在 PATH 中）
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import cv2

# ── 默认参数（全部可用 CLI 覆盖；输出会回报实际生效值）────────────────────
DEF_FRAMES = 10          # 均匀抽帧数
DEF_FACE_PAD = 0.06      # 人脸框外扩（占画幅比例），避免卡片贴脸
DEF_EDGE = 0.06          # 画布安全边距
DEF_MIN_BAND_W = 0.22    # 布局带最小可用宽度（低于此宽度放不下可读卡片）
DEF_MIN_BAND_H = 0.13    # 布局带最小可用高度
DEF_CLUSTER = 0.12       # 跨帧「同一人」聚类半径（按中心点距离）
DEF_GAP = 0.03           # 元素与禁区 / 字幕带之间的最小间隙
DEF_MIN_PRESENCE = 0.30  # 人脸簇需在 ≥30% 抽帧中出现，否则判为离群误检
DEF_MIN_SCALE = 0.25     # 人脸簇面积需 ≥ 主人物面积的该比例，否则判为误检
DEF_TRANSLUCENT_MAX = 0.08  # 半透明卡允许的交叠上限
DEF_EYE_BAND = 0.60      # 禁区上部 60% 视为「眉眼嘴区」，半透明卡也不得压
DEF_MIN_ZONE_W = 0.25    # 禁区最小宽度兜底（防漏检导致卡片敢往脸上放）
DEF_MIN_ZONE_H = 0.22    # 禁区最小高度兜底

CASCADES = ["haarcascade_frontalface_default.xml",
            "haarcascade_frontalface_alt2.xml"]


# ────────────────────────────────────────────────────────── 基础工具
def _ffprobe_json(args):
    return json.loads(subprocess.run(["ffprobe", "-v", "error"] + args,
                                     capture_output=True, text=True,
                                     check=True).stdout)


def probe_display_size(video):
    """返回播放器实际显示尺寸（已应用 rotation）。存储方向 ≠ 显示方向是画幅翻车主因。"""
    st = _ffprobe_json(["-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-show_entries", "stream_side_data=rotation",
                        "-of", "json", video])["streams"][0]
    sw, sh = int(st["width"]), int(st["height"])
    rot = 0
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    display = (sh, sw) if abs(rot) % 180 == 90 else (sw, sh)
    return {"storage": [sw, sh], "rotation": rot, "display": list(display)}


def extract_frames(video, n):
    """均匀抽 n 帧（ffmpeg 默认 autorotate，输出即显示坐标系）。"""
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", video],
        capture_output=True, text=True, check=True).stdout.strip())
    tmp = tempfile.mkdtemp(prefix="thfaces_")
    paths = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        p = os.path.join(tmp, f"f{i:02d}.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video,
                        "-frames:v", "1", "-y", p], check=True)
        paths.append(p)
    return paths, dur


def detect_raw(img_path, cascades):
    """双级联检测 + NMS 去重，返回归一化前的像素框。"""
    img = cv2.imread(img_path)
    h, w = img.shape[:2]
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    found = []
    for c in cascades:
        for (x, y, fw, fh) in c.detectMultiScale(
                gray, scaleFactor=1.08, minNeighbors=5,
                minSize=(int(w * 0.04), int(h * 0.04))):
            found.append([int(x), int(y), int(fw), int(fh)])
    return w, h, nms(found)


def nms(boxes, thr=0.40):
    """同帧内重合框去重（不同级联会对同一张脸各报一次）。"""
    boxes = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    kept = []
    for b in boxes:
        if all(iou(b, k) < thr for k in kept):
            kept.append(b)
    return kept


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def rect_union(rs):
    x0 = min(r[0] for r in rs); y0 = min(r[1] for r in rs)
    x1 = max(r[0] + r[2] for r in rs); y1 = max(r[1] + r[3] for r in rs)
    return [x0, y0, x1, y1]


def clip01(v):
    return max(0.0, min(1.0, v))


# ────────────────────────────────────────────────────────── 聚类：跨帧同一人
def cluster_persons(norm_frames, n_frames, radius):
    """把逐帧人脸框按中心点聚类成「人」，抗离群误检也支持多人镜头。

    返回 [{id, presence, box(归一化 x0,y0,x1,y1)}...]，presence = 出现帧占比。
    """
    flat = []
    for fi, faces in enumerate(norm_frames):
        for f in faces:
            flat.append({"f": fi, "c": (f[0] + f[2] / 2, f[1] + f[3] / 2), "b": f})
    clusters = []
    for item in flat:
        for cl in clusters:
            if abs(item["c"][0] - cl["cx"]) <= radius and abs(item["c"][1] - cl["cy"]) <= radius:
                cl["items"].append(item)
                n = len(cl["items"])
                cl["cx"] += (item["c"][0] - cl["cx"]) / n
                cl["cy"] += (item["c"][1] - cl["cy"]) / n
                break
        else:
            clusters.append({"cx": item["c"][0], "cy": item["c"][1], "items": [item]})

    persons = []
    for cl in clusters:
        frames_seen = {it["f"] for it in cl["items"]}
        box = rect_union([[it["b"][0], it["b"][1], it["b"][2], it["b"][3]]
                          for it in cl["items"]])
        # 同一人不同帧框大小差异大时不取 union（会过宽），取「覆盖帧并集」后再收敛
        x0 = min(it["b"][0] for it in cl["items"])
        y0 = min(it["b"][1] for it in cl["items"])
        x1 = max(it["b"][0] + it["b"][2] for it in cl["items"])
        y1 = max(it["b"][1] + it["b"][3] for it in cl["items"])
        persons.append({"presence": len(frames_seen) / n_frames,
                        "box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]})
    persons.sort(key=lambda p: (p["presence"],
                                (p["box"][2] - p["box"][0]) * (p["box"][3] - p["box"][1])),
                 reverse=True)
    for i, p in enumerate(persons, 1):
        p["id"] = f"P{i}"
        p["presence"] = round(p["presence"], 3)
    return persons


def filter_by_scale(persons, min_ratio):
    """过滤误检：Haar 常把下巴/衣领/字幕区的小块报成人脸。

    真实场景中同一镜头内各人脸尺度相近，故以「面积 × 出现率」最高者为基准，
    面积不足基准 `min_ratio` 的簇判为误检（多人并排时尺度接近，不会被误杀）。
    """
    if len(persons) <= 1:
        return persons, []
    def score(p):
        w = p["box"][2] - p["box"][0]
        h = p["box"][3] - p["box"][1]
        return w * h * p["presence"]
    base = max(persons, key=score)
    base_area = max(1e-9, (base["box"][2] - base["box"][0]) * (base["box"][3] - base["box"][1]))
    kept, dropped = [], []
    for p in persons:
        area = (p["box"][2] - p["box"][0]) * (p["box"][3] - p["box"][1])
        (kept if area >= base_area * min_ratio else dropped).append(p)
    return kept, dropped


def pad_zone(box, pad):
    x0, y0, x1, y1 = box
    z = {"x0": round(clip01(x0 - pad), 4), "y0": round(clip01(y0 - pad), 4),
         "x1": round(clip01(x1 + pad), 4), "y1": round(clip01(y1 + pad), 4)}
    z["w"] = round(z["x1"] - z["x0"], 4)
    z["h"] = round(z["y1"] - z["y0"], 4)
    return z


# ────────────────────────────────────────────────────────── 自适应推导
def derive_subtitle(aspect, forbid, edge, gap):
    """字幕带默认位置按画幅分档；若与禁区冲突则上移到禁区上方（自适应）。"""
    if aspect < 0.80:                       # 竖屏类
        band = [0.74, 0.79]
    elif aspect > 1.50:                     # 横屏类
        band = [0.84, 0.91]
    else:                                   # 方形 / 近方
        band = [0.80, 0.87]
    if forbid:
        h = band[1] - band[0]
        if not (band[1] <= forbid["y0"] - gap or band[0] >= forbid["y1"] + gap):
            top = forbid["y0"] - gap - h    # 与禁区垂直相交 → 整体上移
            if top >= edge:
                band = [round(top, 4), round(top + h, 4)]
    return [round(band[0], 4), round(band[1], 4)]


def derive_bands(forbid, sub, edge, min_w, min_h, gap):
    """按「有没有一块够大的空地」推导可用布局带 —— 不预设人脸在哪。"""
    bands = []
    if not forbid:
        return bands
    sub_top, sub_bot = sub[0], sub[1]
    v_top, v_bot = edge, 1 - edge

    # 上带：画布上沿 → min(人脸顶, 字幕带顶)
    y1 = min(forbid["y0"] - gap, sub_top - gap)
    if (y1 - v_top) >= min_h:
        bands.append({"id": "top-band", "x": [edge, round(1 - edge, 4)],
                      "y": [v_top, round(y1, 4)],
                      "w": round(1 - 2 * edge, 4), "h": round(y1 - v_top, 4),
                      "note": "横贯人物上方"})

    # 下带：人脸底 → 字幕带顶（优先），不够则看字幕带下方
    s = forbid["y1"] + gap
    e = min(sub_top - gap, v_bot)
    if (e - s) >= min_h:
        bands.append({"id": "bottom-band", "x": [edge, round(1 - edge, 4)],
                      "y": [round(s, 4), round(e, 4)],
                      "w": round(1 - 2 * edge, 4), "h": round(e - s, 4),
                      "note": "压在人物下方、字幕带上方"})
    else:
        s2 = sub_bot + gap
        if (v_bot - s2) >= min_h:
            bands.append({"id": "bottom-band", "x": [edge, round(1 - edge, 4)],
                          "y": [round(s2, 4), v_bot],
                          "w": round(1 - 2 * edge, 4), "h": round(v_bot - s2, 4),
                          "note": "字幕带下方"})

    # 左右竖排：横向上有足够留白即可，高度取垂直可用带（避开字幕带）
    avail_y0 = v_top
    avail_y1 = max(sub_top - gap, v_top)
    lx1 = forbid["x0"] - gap
    if (lx1 - edge) >= min_w:
        bands.append({"id": "left-stack", "x": [edge, round(lx1, 4)],
                      "y": [avail_y0, round(avail_y1, 4)],
                      "w": round(lx1 - edge, 4), "h": round(avail_y1 - avail_y0, 4),
                      "note": "左侧竖排"})
    rx0 = forbid["x1"] + gap
    if ((1 - edge) - rx0) >= min_w:
        bands.append({"id": "right-stack", "x": [round(rx0, 4), round(1 - edge, 4)],
                      "y": [avail_y0, round(avail_y1, 4)],
                      "w": round((1 - edge) - rx0, 4), "h": round(avail_y1 - avail_y0, 4),
                      "note": "右侧竖排"})
    return bands


# ────────────────────────────────────────────────────────── 校验模式
def overlap_area(a, b):
    """a/b 为 dict(x0,y0,x1,y1)，返回交集面积（归一化平方单位）。"""
    x0, y0 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"])
    x1, y1 = min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    return 0.0 if x1 <= x0 or y1 <= y0 else (x1 - x0) * (y1 - y0)


def run_check(boxes, zones, eye_band, translucent_max):
    forbid = zones.get("forbidden_zone")
    sub = zones.get("subtitle_band")
    sub_rect = {"x0": 0.0, "y0": sub[0], "x1": 1.0, "y1": sub[1]} if sub else None
    results, failures = [], []
    for b in boxes:
        rect = {"x0": b["x0"], "y0": b["y0"], "x1": b["x1"], "y1": b["y1"]}
        area = max(1e-9, (rect["x1"] - rect["x0"]) * (rect["y1"] - rect["y0"]))
        opp = b.get("opacity", "opaque")
        rec = {"id": b.get("id", "?"), "opacity": opp, "band": b.get("band")}

        if forbid:
            ov = overlap_area(rect, forbid) / area
            rec["overlap_of_box_vs_face"] = round(ov, 4)
            eye = {"x0": forbid["x0"], "y0": forbid["y0"],
                   "x1": forbid["x1"], "y1": forbid["y0"] + forbid["h"] * eye_band}
            ov_eye = overlap_area(rect, eye) / area
            rec["overlap_of_box_vs_eye_area"] = round(ov_eye, 4)
            limit = translucent_max if opp == "translucent" else 0.0
            if ov_eye > 0:
                rec["status"] = "FAIL"
                rec["reason"] = "压到眉眼嘴区（任何元素均禁止）"
            elif ov > limit:
                rec["status"] = "FAIL"
                rec["reason"] = f"与禁区交叠 {ov:.1%} > 允许 {limit:.0%}（{opp}）"
            else:
                rec["status"] = "PASS"
        else:
            rec["status"] = "SKIP"
            rec["reason"] = "未检测到人脸，无法校验"

        if sub_rect:
            ovs = overlap_area(rect, sub_rect) / area
            rec["overlap_of_box_vs_subtitle"] = round(ovs, 4)
            if ovs > 0:
                rec["status"] = "FAIL"
                rec["reason"] = (rec.get("reason", "") + "；压到字幕带").strip("；")
        results.append(rec)
        if rec["status"] == "FAIL":
            failures.append(rec)
    return {"results": results, "failures": failures,
            "verdict": "PASS" if not failures else "FAIL"}


# ────────────────────────────────────────────────────────── 主流程
def probe(args):
    info = {"source": args.video or args.image}
    if args.video:
        info.update(probe_display_size(args.video))
        frames, dur = extract_frames(args.video, args.frames)
        info["duration_s"] = round(dur, 2)
    else:
        img = cv2.imread(args.image)
        h, w = img.shape[:2]
        info.update({"storage": [w, h], "rotation": 0, "display": [w, h]})
        frames, dur = [args.image], None

    DW, DH = info["display"]
    aspect = DW / DH
    cascades = [cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, n))
                for n in CASCADES]

    per_frame, norm_frames = [], []
    for p in frames:
        w, h, fs = detect_raw(p, cascades)
        sx, sy = DW / w, DH / h
        norm = [[round(x * sx / DW, 4), round(y * sy / DH, 4),
                 round(fw * sx / DW, 4), round(fh * sy / DH, 4)] for (x, y, fw, fh) in fs]
        norm_frames.append(norm)
        per_frame.append({"frame": os.path.basename(p), "n": len(fs), "faces": norm})

    persons = cluster_persons(norm_frames, len(frames), args.cluster)
    by_presence = [p for p in persons if p["presence"] >= args.min_presence]
    dropped = [p for p in persons if p["presence"] < args.min_presence]
    kept, small = filter_by_scale(by_presence, args.min_scale)
    dropped = dropped + small

    warnings = []
    if dropped:
        warnings.append(f"{len(dropped)} 个人脸簇判为误检已丢弃"
                        f"（出现率 <{args.min_presence:.0%} 或面积 <主人物 {args.min_scale:.0%}）")

    for p in kept:
        p["forbidden"] = pad_zone(p["box"], args.face_pad)

    forbid = kept[0]["forbidden"] if kept else None
    forbid_all = pad_zone(rect_union([p["box"] for p in kept]), args.face_pad) if kept else None
    if forbid:
        # 兜底：禁区不得小于 min_zone（防漏检导致卡片敢往脸上放）
        cx = (forbid["x0"] + forbid["x1"]) / 2
        cy = (forbid["y0"] + forbid["y1"]) / 2
        if forbid["w"] < args.min_zone_w or forbid["h"] < args.min_zone_h:
            forbid = pad_zone([max(0, cx - args.min_zone_w / 2), max(0, cy - args.min_zone_h / 2),
                               min(1, cx + args.min_zone_w / 2), min(1, cy + args.min_zone_h / 2)], 0)
            warnings.append(f"检测框偏小，已按最小禁区 "
                            f"{args.min_zone_w:.2f}W×{args.min_zone_h:.2f}H 兜底放大")

    sub = derive_subtitle(aspect, forbid, args.edge, args.gap)
    bands = derive_bands(forbid, sub, args.edge, args.min_band_w, args.min_band_h, args.gap)

    if not kept:
        verdict = "NO_FACE_DETECTED"
        warnings.append("未检测到人脸：改 --frames 多抽几帧，或人工确认后手填 forbidden_zone")
    elif not bands:
        verdict = "TIGHT_NO_SAFE_BAND"
        warnings.append("无满足最小尺寸的安全带：缩小主卡、改半透明卡、或接受更小字号")
    else:
        verdict = "OK"

    return {
        "source": info["source"],
        "storage_size": info.get("storage"),
        "rotation_deg": info.get("rotation", 0),
        "display_size": [DW, DH],
        "aspect": ("9:16" if aspect < 0.8 else
                   "16:9" if abs(aspect - 16 / 9) < 0.06 else f"{DW}:{DH}"),
        "aspect_ratio": round(aspect, 4),
        "duration_s": info.get("duration_s"),
        "params": {                                   # 生效参数，便于复现
            "frames": args.frames, "face_pad": args.face_pad, "edge": args.edge,
            "min_band_w": args.min_band_w, "min_band_h": args.min_band_h,
            "cluster": args.cluster, "gap": args.gap,
            "min_presence": args.min_presence, "min_scale": args.min_scale,
            "min_zone_w": args.min_zone_w, "min_zone_h": args.min_zone_h,
        },
        "per_frame": per_frame,
        "persons": kept,
        "dropped_outliers": dropped,
        "primary_person": kept[0]["id"] if kept else None,
        "face_union_normalized": ({k: kept[0]["box"][i] for i, k in
                                   enumerate(["x0", "y0", "x1", "y1"])} if kept else None),
        "forbidden_zone": forbid,                     # 主人物（据此布点）
        "forbidden_zone_all": forbid_all,             # 全部人 union（保守参考）
        "subtitle_band": sub,
        "available_bands": bands,
        "verdict": verdict,
        "warnings": warnings,
    }


def main():
    ap = argparse.ArgumentParser(description="口播视频画幅/人脸禁区/布局带探测与校验")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video")
    src.add_argument("--image")
    src.add_argument("--zones", help="复用已探测的 zones.json（配合 --check）")
    ap.add_argument("--check", help="卡片矩形 JSON，做零交叠校验")
    ap.add_argument("--frames", type=int, default=DEF_FRAMES)
    ap.add_argument("--face-pad", type=float, default=DEF_FACE_PAD)
    ap.add_argument("--edge", type=float, default=DEF_EDGE)
    ap.add_argument("--min-band-w", type=float, default=DEF_MIN_BAND_W)
    ap.add_argument("--min-band-h", type=float, default=DEF_MIN_BAND_H)
    ap.add_argument("--cluster", type=float, default=DEF_CLUSTER)
    ap.add_argument("--gap", type=float, default=DEF_GAP)
    ap.add_argument("--min-presence", type=float, default=DEF_MIN_PRESENCE)
    ap.add_argument("--min-scale", type=float, default=DEF_MIN_SCALE,
                    help="人脸簇面积 / 主人物面积 的最低比值，低于此判为误检")
    ap.add_argument("--min-zone-w", type=float, default=DEF_MIN_ZONE_W)
    ap.add_argument("--min-zone-h", type=float, default=DEF_MIN_ZONE_H)
    ap.add_argument("--translucent-max", type=float, default=DEF_TRANSLUCENT_MAX,
                    help="半透明元素允许的禁区交叠上限")
    ap.add_argument("--eye-band", type=float, default=DEF_EYE_BAND,
                    help="禁区上部该比例视为眉眼嘴区（绝对禁止覆盖）")
    ap.add_argument("--out")
    a = ap.parse_args()

    # 校验模式
    if a.check:
        zones = (json.load(open(a.zones, encoding="utf-8")) if a.zones
                 else probe(a))
        boxes_raw = json.load(open(a.check, encoding="utf-8"))
        boxes = boxes_raw["boxes"] if isinstance(boxes_raw, dict) else boxes_raw
        res = run_check(boxes, zones, a.eye_band, a.translucent_max)
        text = json.dumps(res, ensure_ascii=False, indent=2)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(text)
        print(text)
        return 0 if res["verdict"] == "PASS" else 1

    result = probe(a)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
