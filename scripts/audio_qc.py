# -*- coding: utf-8 -*-
"""
音频质检（audio QC）—— 口播成片导出后必跑。
用法:
    python audio_qc.py --out 成片.mp4 [--src 源素材.mp4]
    python audio_qc.py --out 成片.mp4 --seg 0:339:2660000:13960000 --seg 479:675:18780000:25313333
        (段格式: 起始帧:结束帧:源起us:源止us，可多次)

检测四件事:
  1. 削波  : 解码 f32le 后 |x| > 1.0 的样本数与簇（按原生声道解码，
             避免 ffmpeg √2 声道转换系数把 mono 源 vs stereo 成片错比）
  2. 增益  : 逐段 srcRMS vs outRMS，找异常高的段（=> 同一素材被两条轨叠加）
  3. 时序  : 包络互相关（20ms 窗），确认音频被正确搬运（corr 应 > 0.85）
  4. 断点  : |diff| 异常跳变（咔哒/爆音）

退出码: 0 = PASS（无削波且无异常段），1 = FAIL
"""
import argparse, os, subprocess, sys, tempfile
import numpy as np

SR = 48000

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
    cands += [
        r"D:\software\ffmpeg-8.1.1-full_build\bin\%s.exe" % tool,
        r"C:\Users\Administrator\AppData\Local\llama-cpp\%s.exe" % tool,
        tool,
    ]
    for p in cands:
        if os.path.sep not in p or os.path.exists(p):
            return p
    return tool

def probe_channels(path):
    """读音频流原生声道数（读不到按 2 处理）"""
    ffp = ff("ffprobe")
    cmd = [ffp, "-v", "error", "-select_streams", "a:0",
           "-show_entries", "stream=channels", "-of", "csv=p=0", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return int(r.stdout.strip().split(",")[0])
    except Exception:
        return 2

def decode(path, sr=SR, ch=None, flatten=True):
    """用 ffmpeg 解成 f32le raw，返回 numpy 数组。

    ⚠️ 声道路由是这里最大的坑，必须按**原生声道数**解码，才能保证"每声道幅度"量纲一致：
      · 单声道文件 → `-ac 1`，幅度即该声道幅度
      · 多声道文件 → `-ac 2` 后手工取 `(L+R)/2`（L≈R 时即每声道幅度）

    为什么不能一律 `-ac 1`/`-ac 2`：ffmpeg 的双向声道转换系数**都是 √2 ≈ 3.01dB**
    （stereo→mono downmix 与 mono→stereo upmix 各乘 0.7071）。
    一律 `-ac 1` → 立体声成片被 ×0.7071，凭空造出 +3dB 假增益/假削波；
    一律 `-ac 2` → 单声道源被 ×0.7071，又给出 −3dB 的假偏差（还可能和真实偏差
    相互抵消，把真问题伪装成 PASS）。2026-09-21 两种都踩过。
    """
    if ch is None:
        ch = 1 if probe_channels(path) == 1 else 2
    ffm = ff("ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as t:
        raw = t.name
    cmd = [ffm, "-y", "-v", "error", "-i", path, "-vn", "-ac", str(ch), "-ar", str(sr),
           "-f", "f32le", raw]
    subprocess.run(cmd, check=True)
    x = np.fromfile(raw, dtype='<f4')
    os.unlink(raw)
    if ch == 2:
        n = len(x)//2
        x = x[:n*2].reshape(n, 2)
        if flatten:
            x = x.mean(axis=1)   # (L+R)/2 —— 0.5 系数，不带 √2
    return x

def rdb(x):
    r = np.sqrt((x**2).mean()) if len(x) else 0
    return 20*np.log10(max(r, 1e-9))

def clusters(mask, sr=SR, gap_ms=10):
    idx = np.where(mask)[0]
    if len(idx) == 0: return []
    g = []; s = idx[0]; p = idx[0]
    for i in idx[1:]:
        if i - p <= int(gap_ms/1000.0*sr): p = i
        else: g.append((s, p)); s = i; p = i
    g.append((s, p))
    return g

def env(x, w=960):
    m = len(x)//w
    return np.sqrt((x[:m*w].reshape(m, w)**2).mean(axis=1)) if m else np.array([])

def env_corr(o, s, max_lag=25):
    eo = env(o); es = env(s)
    m = min(len(eo), len(es))
    if m < 30: return 0.0, 0
    eo = eo[:m] - eo[:m].mean(); es = es[:m] - es[:m].mean()
    best = (0.0, 0)
    for lag in range(-max_lag, max_lag+1):
        if lag >= 0: a = eo[lag:]; b = es[:len(a)]
        else: b = es[-lag:]; a = eo[:len(b)]
        if len(a) < 20: continue
        d = np.linalg.norm(a)*np.linalg.norm(b)
        if d < 1e-9: continue
        c = float(np.dot(a, b)/d)
        if c > best[0]: best = (c, lag)
    return best

def seg_slice(x, t0, t1, sr=SR):
    return x[max(0, int(round(t0*sr))):min(len(x), int(round(t1*sr)))]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--src")
    ap.add_argument("--seg", action="append", default=[],
                    help="f0:f1:s0us:s1us  时间轴帧起:止:源微秒起:止")
    ap.add_argument("--fps", type=float, default=30.0)
    args = ap.parse_args()

    out = decode(args.out, SR)
    print("成片 %s  %.3fs  %d samples" % (args.out, len(out)/SR, len(out)))
    fail = []

    # ---- 1. 削波 ----
    pk = np.abs(out).max()
    over = int((np.abs(out) > 1.0).sum())
    cl = int((np.abs(out) >= 0.999).sum())
    print("\n[1] 削波")
    print("    peak = %.4f (%+.2f dBFS)   |x|>=0.999 = %d   |x|>1.0 = %d (%.4f%%)" % (
        pk, 20*np.log10(max(pk, 1e-9)), cl, over, 100.0*over/len(out)))
    if over:
        g = clusters(np.abs(out) > 1.0, SR)
        tot = sum(b-a+1 for a, b in g)
        worst = max(g, key=lambda t: t[1]-t[0])
        print("    FAIL: %d 簇削波, 共 %.1f ms; 最长 %.1f ms @ %.3fs" % (
            len(g), tot/SR*1000, (worst[1]-worst[0]+1)/SR*1000, worst[0]/SR))
        fail.append("削波 %d 样本" % over)
    else:
        print("    PASS: 无削波")

    # ---- 4. 断点 ----
    d = np.abs(np.diff(out))
    ps = np.percentile(d, [99, 99.9, 99.99])
    thr = max(0.08, ps[2]*2.5)
    ncl = int((d > thr).sum())
    print("\n[4] 样本级断点: |diff| p99=%.4f p99.9=%.4f p99.99=%.4f max=%.4f" % (
        ps[0], ps[1], ps[2], d.max()))
    print("    |diff| > %.4f 的点 = %d 个" % (thr, ncl))

    if not args.src:
        print("\n(未提供 --src，跳过增益/时序对比)")
        return 1 if fail else 0

    src = decode(args.src, SR)
    print("\n源素材 %s  %.3fs" % (args.src, len(src)/SR))
    print("    源 peak = %.4f   全片 RMS 源=%.2f 成片=%.2f  差=%+.2f dB" % (
        np.abs(src).max(), rdb(src), rdb(out), rdb(out)-rdb(src)))

    if args.seg:
        print("\n[2]/[3] 逐段增益与包络对齐")
        print("    %-30s %-8s %-8s %-8s %-8s" % ("段(timeline s)", "srcRMS", "outRMS", "gain", "envCorr"))
        gains = []
        for spec in args.seg:
            parts = [v for v in spec.split(":")]
            if len(parts) != 4:
                print("    FAIL: --seg 格式错误 %r —— 应为 f0:f1:s0us:s1us（4 段，源起与源止都要给）"
                      % spec)
                fail.append("--seg 格式错误: %s" % spec)
                continue
            f0, f1, s0, s1 = [float(v) for v in parts]
            o = seg_slice(out, f0/args.fps, f1/args.fps)
            s = seg_slice(src, s0/1e6, s1/1e6)
            n = min(len(o), len(s))
            if n < 2000:
                print("    %-30s too short" % ("%.2f-%.2f" % (f0/args.fps, f1/args.fps)))
                continue
            g = rdb(o[:n]) - rdb(s[:n])
            c, lag = env_corr(o[:n], s[:n])
            gains.append((g, c, f0/args.fps, f1/args.fps))
            print("    %-30s %-8.2f %-8.2f %-8.2f %-8.3f" % (
                "%.2f-%.2f" % (f0/args.fps, f1/args.fps), rdb(s[:n]), rdb(o[:n]), g, c))
        if gains:
            gs = [g for g, _, _, _ in gains]
            cs = [c for _, c, _, _ in gains]
            med = float(np.median(gs))
            print("    gain 中位数 = %+.2f dB  (所有段应接近此值)" % med)
            for g, c, a, b in gains:
                if g - med > 3.0:
                    print("    FAIL: 段 %.2f-%.2fs 增益 %+.2f dB 比中位数高 %.2f dB "
                          "=> 疑似同一素材被两条轨叠加播放" % (a, b, g, g-med))
                    fail.append("段 %.2f-%.2fs 异常增益" % (a, b))
                if c < 0.85:
                    print("    WARN: 段 %.2f-%.2fs 包络相关 %.3f 偏低 => 音频可能未按预期搬运" % (a, b, c))
    return 1 if fail else 0

if __name__ == "__main__":
    sys.exit(main())
