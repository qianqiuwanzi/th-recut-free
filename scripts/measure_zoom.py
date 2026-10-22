# -*- coding: utf-8 -*-
"""
放大倍率剖面 —— 量化成片相对源素材被放大了多少倍（含平移搜索，不受机位偏移影响）。

用途：
  · 报障"画面被放大 / 构图被裁 / 人变大了"，定位是 item 几何、还是 effect-zoom 特效；
  · 核对 `builtin:effect-zoom` 的 `magnification` 是否被写成默认值 1.5。

原理：
  若成片帧 = 源素材某个 (W/k, H/k) 子区域放大到整幅，则把成片帧下采样成 (W/k, H/k)
  当作模板，能在源帧中用归一化互相关（NCC）找到峰。峰值最高者即真实 k。
  平移由互相关自动搜索，所以机位偏移、focalPoint 偏移都不干扰。

用法：
  python measure_zoom.py --out 成片.mp4 --src 源素材.mp4 \
      --map 0:339:2660000 --map 339:479:14060000 [--map ...] \
      [--samples 22] [--t0 0.5] [--step 2.5] [--fps 30] [--w 1632] [--h 912]

  --map   = startFrame:endFrame:sourceInUs  （从 preview_timeline 的 V1 item 抄）
  输出每取样点的 k，并给出中位数与"k>=1.4 的时长占比"，退出码 0 = 全部 k<=1.15。
"""
import argparse, os, subprocess, sys
import numpy as np

DEFAULT_FF = r"D:\software\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
# 可移植：env THRECUT_FFMPEG / THRECUT_FFMPEG_DIR / THRECUT_<TOOL> 覆盖内置默认，最后回落 PATH。
_FFMPEG_EXE = os.environ.get("THRECUT_FFMPEG", "")
_FFMPEG_DIR = os.environ.get("THRECUT_FFMPEG_DIR", "")
_LLAMA_FF = r"C:\Users\Administrator\AppData\Local\llama-cpp\%s.exe"


def ff(tool="ffmpeg", override=None):
    cands = []
    if override:
        cands.append(override)
    if tool == "ffmpeg" and _FFMPEG_EXE:
        cands.append(_FFMPEG_EXE)
    env_exe = os.environ.get("THRECUT_%s" % tool.upper())
    if env_exe:
        cands.append(env_exe)
    if _FFMPEG_DIR:
        cands.append(os.path.join(_FFMPEG_DIR, "%s.exe" % tool))
    cands += [DEFAULT_FF, _LLAMA_FF % tool, tool]
    for p in cands:
        if p and (os.path.sep not in p or os.path.exists(p)):
            return p
    return tool


FFMPEG = None


def frame(path, t, w, h):
    """抽一帧 -> 灰度 float32 (h, w)"""
    cmd = [FFMPEG, "-v", "error", "-ss", "%.4f" % t, "-i", path, "-frames:v", "1",
           "-vf", "scale=%d:%d" % (w, h), "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    n = w * h
    if len(a) < n:
        return None
    return a[:n].reshape(h, w).astype(np.float32)


def down(a, w, h):
    iy = (np.arange(h) * a.shape[0] / h).astype(np.int32).clip(0, a.shape[0] - 1)
    ix = (np.arange(w) * a.shape[1] / w).astype(np.int32).clip(0, a.shape[1] - 1)
    return a[iy][:, ix]


def integral(a):
    z = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    z[1:, 1:] = a.cumsum(0).cumsum(1)
    return z


def boxsum(II, th, tw):
    H = II.shape[0] - th
    W = II.shape[1] - tw
    return II[th:, tw:] - II[:H, tw:] - II[th:, :W] + II[:H, :W]


def ncc_peak(T, S, II_S, S2_II):
    th, tw = T.shape
    H, W = S.shape
    if th >= H or tw >= W:
        return -1.0
    T0 = T - T.mean()
    tE = float(np.sqrt((T0 ** 2).sum()))
    if tE < 1e-6:
        return -1.0
    n = float(tw * th)
    s1 = boxsum(II_S, th, tw)
    s2 = boxsum(S2_II, th, tw)
    var = np.maximum(s2 - s1 * s1 / n, 0.0)
    F = np.fft.rfft2(S, s=(H + th - 1, W + tw - 1))
    G = np.fft.rfft2(T0[::-1, ::-1], s=(H + th - 1, W + tw - 1))
    c = np.fft.irfft2(F * G, s=(H + th - 1, W + tw - 1))
    c = c[th - 1:th - 1 + H - th + 1, tw - 1:tw - 1 + W - tw + 1]
    num = c - s1 * T0.sum() / n
    den = np.sqrt(var) * tE
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(den > 1e-6, num / den, 0.0)
    return float(v.max())


def best_k(E, S, ks, W, H):
    II_S, S2_II = integral(S), integral(S ** 2)
    res = []
    for k in ks:
        T = down(E, int(round(W / k)), int(round(H / k)))
        res.append((k, ncc_peak(T, S, II_S, S2_II)))
    return max(res, key=lambda x: x[1])


def main():
    global FFMPEG
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--map", action="append", default=[], required=True,
                    help="startFrame:endFrame:sourceInUs")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--w", type=int, default=1632)
    ap.add_argument("--h", type=int, default=912)
    ap.add_argument("--t0", type=float, default=0.5)
    ap.add_argument("--step", type=float, default=2.5)
    ap.add_argument("--samples", type=int, default=22)
    ap.add_argument("--ffmpeg")
    a = ap.parse_args()
    FFMPEG = ff("ffmpeg", a.ffmpeg)

    segs = []
    for m in a.map:
        f0, f1, us = [float(v) for v in m.split(":")]
        segs.append((f0, f1, us))

    def map_time(t):
        f = t * a.fps
        for f0, f1, us in segs:
            if f0 <= f < f1:
                return us / 1e6 + (f - f0) / a.fps
        return None

    coarse = [round(1.00 + 0.05 * i, 3) for i in range(17)]   # 1.00~1.80
    print("%-10s %-10s %-8s %-8s" % ("out_t(s)", "src_t(s)", "k", "NCC"))
    ks = []
    for i in range(a.samples):
        t = round(a.t0 + a.step * i, 3)
        st = map_time(t)
        if st is None:
            print("%-10.2f (no V1 coverage)" % t)
            continue
        E = frame(a.out, t, a.w, a.h)
        S = frame(a.src, st, a.w, a.h)
        if E is None or S is None:
            print("%-10.2f frame fail" % t)
            continue
        k1, p1 = best_k(E, S, coarse, a.w, a.h)
        k2, p2 = best_k(E, S, [round(k1 + d, 3) for d in np.arange(-0.05, 0.051, 0.01)],
                        a.w, a.h)
        k, p = (k2, p2) if p2 >= p1 else (k1, p1)
        if p >= 0.5:
            ks.append(k)
        print("%-10.2f %-10.4f %-8.3f %-8.4f%s" % (t, st, k, p, "" if p >= 0.5 else "  (low-conf)"))

    if ks:
        arr = np.array(ks)
        print("\n--- summary (n=%d, p>=0.5) ---" % len(arr))
        print("median k=%.3f  mean=%.3f  min=%.3f  max=%.3f" %
              (np.median(arr), arr.mean(), arr.min(), arr.max()))
        r = 100.0 * (arr >= 1.40).mean()
        print("share of samples with k>=1.40 = %.0f%%" % r)
        if np.median(arr) > 1.15:
            print("FAIL: 画面被系统性放大（中位数 %.3f）。查 builtin:effect-zoom 的 magnification "
                  "(默认 1.5) 与 item 的 width/height/crop。" % np.median(arr))
            return 1
    print("PASS: 未见系统性放大")
    return 0


if __name__ == "__main__":
    sys.exit(main())
