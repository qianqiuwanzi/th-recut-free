# -*- coding: utf-8 -*-
"""
频带占比验收（band-ratio QC）—— 定位 / 证伪口播成片里"滋滋滋"这类高频宽带噪声。
配套 SKILL.md 坑 64。

用法:
    # 1) 只看全片（最快，几十秒）
    python audio_band_check.py --out 成片.mp4 --src 源素材.aac

    # 2) 加逐段核对（推荐，给出成片时间线的 V1 片段表）
    python audio_band_check.py --out 成片.mp4 --src 源素材.aac \
        --timeline "<...>/timelines/Timeline_xxxx.json" --track-order 0 --fps 30

    # 3) 只打印 MG 源码以外的机器可读结果
    python audio_band_check.py ... --json band.json

判据（为什么不用"静音段噪声底 RMS"）:
  · 本类噪声是**乘性/调制类**失真 —— 静音段几乎为零、人声一响才出现。
    用静音段 RMS 判会得出"没问题"的假结论（2026-09-22 踩过，把 BGM 误当元凶）。
  · **频带占比 = 10*log10(Σ能量[band] / Σ能量[ref])** 是无量纲比值，
    ffmpeg 的 √2 声道系数、整体增益都会**自动约掉** → 天然免于"声道路由"这类坑。

真凶（2026-09-22 定性）: 音频挂在 **video asset** 上被取用 → ChatCut 管线灌入
12k~24k 宽带噪声（成片 12-16k 比源高 +12~+28dB）。换成**独立音频文件 asset**
并把视频轨全部 `audioStreamId:"off"`，同一渲染链即可回到源的 ±0.2dB。

退出码: 0 = PASS，1 = FAIL
"""
import argparse, json, os, subprocess, sys, tempfile
import numpy as np

SR_DEF = 48000
BAND_DEF = "4000:8000,8000:12000,12000:16000,16000:20000,20000:24000"
REF_DEF = "100:4000"
HOT_DEF = "12000:16000"


def ff(tool):
    # 可移植：env THRECUT_FFMPEG(_DIR) / THRECUT_<TOOL> 覆盖内置默认，最后回落 PATH。
    env_exe = os.environ.get("THRECUT_%s" % tool.upper())
    env_dir = os.environ.get("THRECUT_FFMPEG_DIR", "")
    cands = []
    if tool == "ffmpeg" and os.environ.get("THRECUT_FFMPEG"):
        cands.append(os.environ["THRECUT_FFMPEG"])
    if env_exe:
        cands.append(env_exe)
    if env_dir:
        cands.append(os.path.join(env_dir, "%s.exe" % tool))
    cands += [r"D:\software\ffmpeg-8.1.1-full_build\bin\%s.exe" % tool, tool]
    for p in cands:
        if os.path.sep not in p or os.path.exists(p):
            return p
    return tool


def decode(path, sr):
    """解码为 48k / mono / f32le 数组。

    这里一律 `-ac 1` 是**安全**的：本脚本只算**频带占比**（同一条信号内部的能量比），
    声道转换的固定系数会被约掉。若将来要改成比较绝对电平，必须按原生声道解码
    （见 scripts/audio_qc.py 的 probe_channels / 坑 48）。
    """
    ffm = ff("ffmpeg")
    fd, raw = tempfile.mkstemp(suffix=".f32"); os.close(fd)
    try:
        subprocess.run([ffm, "-y", "-v", "error", "-i", path, "-vn",
                        "-ac", "1", "-ar", str(sr), "-f", "f32le", raw],
                       check=True, capture_output=True)
        return np.fromfile(raw, dtype="<f4").astype(np.float64)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass


def probe_dur(path):
    ffp = ff("ffprobe")
    try:
        r = subprocess.run([ffp, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
        return float(r.stdout.strip())
    except Exception:
        return float("nan")


def spec(x, sr, W):
    """平均功率谱（Hann 窗，50% 重叠）"""
    if x.size < W:
        return None, None
    w = np.hanning(W)
    H = W // 2
    acc = None; cnt = 0
    for s in range(0, x.size - W + 1, H):
        S = np.abs(np.fft.rfft(x[s:s + W] * w)) ** 2
        acc = S if acc is None else acc + S
        cnt += 1
    if not cnt:
        return None, None
    return acc / cnt, np.fft.rfftfreq(W, 1.0 / sr)


def band_db(P, f, lo, hi):
    m = (f >= lo) & (f < hi)
    if not m.any():
        return float("nan")
    return 10.0 * np.log10(float(P[m].mean()) + 1e-30)


def profile(P, f, bands, ref):
    r = band_db(P, f, ref[0], ref[1])
    return {b: band_db(P, f, b[0], b[1]) - r for b in bands}


def parse_bands(s):
    out = []
    for seg in s.split(","):
        a, b = seg.strip().split(":")
        out.append((int(float(a)), int(float(b))))
    return out


def load_v1_segments(tl_path, track_order, fps, asset=None):
    """从时间线 JSON 抽 V1 片段表 -> [(film_start_s, dur_s, source_in_s), ...]"""
    d = json.load(open(tl_path, encoding="utf-8"))
    if track_order is None:
        # 自动：条目最多的 video 轨
        cnt = {}
        for it in d.get("videoItems", []):
            cnt[it.get("trackId")] = cnt.get(it.get("trackId"), 0) + 1
        tr = {t["id"]: t for t in d.get("tracks", [])}
        vid = [tid for tid, t in tr.items() if t.get("type") == "video" and tid in cnt]
        vid.sort(key=lambda tid: -cnt[tid])
        tid = vid[0] if vid else None
    else:
        tr = {t["id"]: t for t in d.get("tracks", [])}
        cand = [tid for tid, t in tr.items()
                if t.get("type") == "video" and t.get("order") == track_order]
        tid = cand[0] if cand else None
    segs = []
    for it in d.get("videoItems", []):
        if it.get("trackId") != tid:
            continue
        if asset and it.get("assetId") != asset:
            continue
        df = it.get("durationFrames") or 0
        if df <= 0:
            continue
        segs.append(((it.get("startFrame") or 0) / fps, df / fps,
                     (it.get("sourceIn") or 0) / 1e6))
    segs.sort()
    return segs


def main():
    ap = argparse.ArgumentParser(description="频带占比验收（滋滋滋 / 高频宽带噪声）")
    ap.add_argument("--out", required=True, help="成片（mp4/mov/wav 均可）")
    ap.add_argument("--src", required=True, help="源素材（原始音频或原始视频）")
    ap.add_argument("--timeline", help="时间线 JSON，给了就做逐段核对")
    ap.add_argument("--track-order", type=int, default=None,
                    help="V1 轨的 order（默认自动取条目最多的 video 轨）")
    ap.add_argument("--asset", help="只取该 assetId 的片段（多素材时用）")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--band", default=BAND_DEF)
    ap.add_argument("--ref", default=REF_DEF, help="参考带（占比的基准），默认 100:4000")
    ap.add_argument("--hot", default=HOT_DEF, help="判 PASS/FAIL 的敏感带，默认 12000:16000")
    ap.add_argument("--tol", type=float, default=1.0, help="逐段允许偏差 dB，默认 1.0")
    ap.add_argument("--max-bad-ratio", type=float, default=0.15,
                    help="允许超标的片段比例，默认 0.15")
    ap.add_argument("--seg-len", type=float, default=0.6, help="逐段取样窗口秒，默认 0.6")
    ap.add_argument("--W", type=int, default=8192, help="FFT 窗长，默认 8192")
    ap.add_argument("--sr", type=int, default=SR_DEF)
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    for p in (args.out, args.src):
        if not os.path.exists(p):
            print("!! 文件不存在: %s" % p); return 1

    bands = parse_bands(args.band)
    ref = tuple(int(x) for x in args.ref.split(":"))
    hot = tuple(int(x) for x in args.hot.split(":"))

    print("=== 解码 ===")
    src = decode(args.src, args.sr)
    out = decode(args.out, args.sr)
    print("  源    %8.3fs  (%.1fs)" % (src.size / args.sr, probe_dur(args.src)))
    print("  成片  %8.3fs  (%.1fs)" % (out.size / args.sr, probe_dur(args.out)))

    Ps, f = spec(src, args.sr, args.W)
    Po, _ = spec(out, args.sr, args.W)
    if Ps is None or Po is None:
        print("!! 素材太短，无法分析"); return 1
    prs = profile(Ps, f, bands, ref)
    pro = profile(Po, f, bands, ref)

    print("\n=== 全片频带占比（相对 %d-%dHz，dB）===" % ref)
    hdr = "  %-10s" % "对象" + "".join("%11s" % ("%d-%dk" % (a // 1000, b // 1000)) for a, b in bands)
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for nm, pr in (("源", prs), ("成片", pro), ("差值", {b: pro[b] - prs[b] for b in bands})):
        print("  %-10s" % nm + "".join("%11.2f" % pr[b] for b in bands))

    d_hot_file = pro[hot] - prs[hot]
    print("\n  敏感带 %d-%dk 与源差: %+.2f dB" % (hot[0] // 1000, hot[1] // 1000, d_hot_file))
    if d_hot_file > 10:
        print("  🔴 高出 >10dB → 极可能音频仍在走 video asset 路径（见 SKILL.md 坑 64）")

    seg_stats = None
    if args.timeline:
        segs = load_v1_segments(args.timeline, args.track_order, args.fps, args.asset)
        print("\n=== 逐段核对 %d-%dk（V1 片段 %d 条）===" % (hot[0] // 1000, hot[1] // 1000, len(segs)))
        half = args.seg_len / 2.0
        diffs = []
        for (ts, dur, s0) in segs:
            if dur < args.seg_len * 1.5:
                continue
            mid = ts + dur / 2.0
            a = int((mid - half) * args.sr); b = int((mid + half) * args.sr)
            co = out[a:b]
            a2 = int((s0 + dur / 2.0 - half) * args.sr); b2 = int((s0 + dur / 2.0 + half) * args.sr)
            cs = src[a2:b2]
            if co.size < args.W or cs.size < args.W:
                continue
            Po_, f_ = spec(co, args.sr, args.W); Ps_, _ = spec(cs, args.sr, args.W)
            ho = band_db(Po_, f_, hot[0], hot[1]) - band_db(Po_, f_, ref[0], ref[1])
            hs = band_db(Ps_, f_, hot[0], hot[1]) - band_db(Ps_, f_, ref[0], ref[1])
            diffs.append(ho - hs)
        if diffs:
            diffs = np.array(diffs)
            bad = int((np.abs(diffs) > args.tol).sum())
            ratio = bad / float(diffs.size)
            seg_stats = dict(n=int(diffs.size), mean=float(diffs.mean()),
                             max=float(diffs.max()), min=float(diffs.min()),
                             bad=bad, bad_ratio=float(ratio))
            print("  有效片段 %d" % diffs.size)
            print("  与源差: 均值 %+.2f / 最大 %+.2f / 最小 %+.2f dB"
                  % (diffs.mean(), diffs.max(), diffs.min()))
            print("  超过 ±%.1fdB 的片段: %d / %d (%.0f%%)" % (args.tol, bad, diffs.size, ratio * 100))
        else:
            print("  !! 没有可比片段（源时长/片段太短？）")

    ok = (abs(d_hot_file) <= max(args.tol * 3, 3.0))
    if seg_stats is not None:
        ok = ok and (seg_stats["bad_ratio"] <= args.max_bad_ratio)
    print("\n=== 结论: %s ===" % ("PASS ✅" if ok else "FAIL 🔴"))

    if args.json_out:
        json.dump({"src": args.src, "out": args.out,
                   "bands": {"%d-%d" % b: {"src": prs[b], "out": pro[b], "delta": pro[b] - prs[b]}
                             for b in bands},
                   "hot_delta_file": d_hot_file, "segments": seg_stats, "pass": bool(ok)},
                  open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
