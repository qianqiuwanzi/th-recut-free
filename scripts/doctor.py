#!/usr/bin/env python3
"""doctor.py — 口播视频一次性本地体检。

跑两项最通用的检查并汇总成一张表：
  1. detect_faces.py  人脸禁区 / 显示画幅探测（视频画面构图）
  2. audio_qc.py      音频验收（削波 / 增益 / 时序）

其余脚本（check_pip / check_card_layout / audio_band_check …）针对特定环节，
按需单独跑，见 README「检查项清单」。

用法：
    python doctor.py --video your.mp4
    python doctor.py --video your.mp4 --src raw.mp4   # 有原始素材时附带增益/时序对比

退出码：0 = 全部 PASS；1 = 有 FAIL 项；2 = 环境/用法错误。
纯本地运行，视频不出本机。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(script, extra):
    cmd = [sys.executable, os.path.join(HERE, script)] + extra
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def main():
    args = sys.argv[1:]
    if "--video" not in args:
        print(__doc__)
        return 2
    video = args[args.index("--video") + 1]
    if not os.path.isfile(video):
        print("找不到视频文件: %s" % video)
        return 2

    rows = []

    # 1) 人脸禁区 / 画幅探测
    rc, out, err = run("detect_faces.py", ["--video", video])
    if rc in (0, 1) and out.strip():
        try:
            data = json.loads(out)
            zones = data.get("zones") or data.get("safeZones") or []
            verdict = data.get("verdict", "PASS" if rc == 0 else "FAIL")
            detail = "探测到 %d 个显示画幅安全区" % len(zones)
        except Exception:
            verdict = "PASS" if rc == 0 else "FAIL"
            detail = "见 JSON 输出"
    else:
        verdict = "ERROR"
        detail = (err or out).strip().splitlines()[-1] if (err or out).strip() else "运行失败"
    rows.append(("人脸禁区 / 显示画幅", verdict, detail))

    # 2) 音频验收
    extra = ["--out", video]
    if "--src" in args:
        extra += ["--src", args[args.index("--src") + 1]]
    rc, out, err = run("audio_qc.py", extra)
    if rc in (0, 1) and out.strip():
        verdict = "PASS" if rc == 0 else "FAIL"
        clipping = "无削波"
        for line in out.splitlines():
            if "削波" in line and ("FAIL" in line or "簇削波" in line):
                clipping = line.strip()
        detail = clipping
    else:
        verdict = "ERROR"
        detail = (err or out).strip().splitlines()[-1] if (err or out).strip() else "运行失败"
    rows.append(("音频验收（削波/增益/时序）", verdict, detail))

    print("\n体检报告 · %s\n" % video)
    print("| 检查项 | 结果 | 说明 |")
    print("|---|---|---|")
    for name, verdict, detail in rows:
        mark = {"PASS": "✅ PASS", "FAIL": "⚠️ FAIL", "ERROR": "❌ ERROR",
                "NO_FACE_DETECTED": "ℹ️ 未检出人脸"}.get(verdict, verdict)
        print("| %s | %s | %s |" % (name, mark, detail))

    bad = [r for r in rows if r[1] in ("FAIL", "ERROR")]
    if not bad:
        print("\n无未通过项。更细的环节检查见 README，按需单独运行。")
        return 0
    print("\n有 %d 项未通过，按上面说明处理后可重跑。" % len(bad))
    return 1


if __name__ == "__main__":
    sys.exit(main())
