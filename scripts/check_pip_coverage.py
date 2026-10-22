#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_pip_coverage.py —— PIP 计划一致性闸门（技能层，2026-10-08）

用途：在导出前校验 plans_<k>.json 的「PIP 布局」两数组是否对齐。
PIP 布局 = fs（满屏卡，phase_place 生成）+ pips（人物小窗，phase_pip 生成）覆盖同一时间段。
不变式：**pips ⊆ fs** —— 每个 pips 段必须被某个 fs 段完整覆盖。

若不满足，渲染结果就是「人物小窗悬空在裸口播画面上、背后没有满屏卡」
（用户 2026-10-08 反馈 action-3 两个 PIP 布局只有人物窗、没有全屏 broll 的根因）。

用法：
    python check_pip_coverage.py --plans plans_3.json            # 单文件
    python check_pip_coverage.py --dir tmp/b931 --glob 'plans_*.json'  # 批量
    python check_pip_coverage.py --plans plans_3.json --tol 0.5  # 覆盖容差(秒)

退出码：0 = 通过（pips 全部被 fs 覆盖）；1 = 存在孤儿 pip（**阻断导出**）。
"""
import argparse
import glob
import json
import os
import sys


def _span(x):
    """把 fs/pips 条目解析成 (start, end)。fs 条目形如 [s, e, {props}]，pips 形如 [a, b]。"""
    if isinstance(x, dict):
        s, e = x.get('start'), x.get('end')
    else:
        s, e = x[0], x[1]
    return float(s), float(e)


def check_plans(plans_path, tol=0.5, verbose=True):
    """返回 (ok, orphans, fs_only)。orphans = 无 fs 覆盖的 pips 段；fs_only = 无 pip 对齐的 fs 段。"""
    P = json.load(open(plans_path, encoding='utf-8'))
    fs = [_span(x) for x in P.get('fs', [])]
    pips = [_span(x) for x in P.get('pips', [])]

    orphans = []
    for a, b in pips:
        covered = any(s - tol <= a and b <= e + tol for s, e in fs)
        if not covered:
            orphans.append((a, b))

    fs_only = []
    for s, e in fs:
        paired = any(a - tol <= s and e <= b + tol for a, b in pips)
        if not paired:
            fs_only.append((s, e))

    ok = not orphans
    if verbose:
        print('  plans: %s' % plans_path)
        print('    fs  : %s' % fs)
        print('    pips: %s' % pips)
        for a, b in orphans:
            print('    [X] 孤儿 pips (%.1f-%.1fs)：无任何 fs 段覆盖 → 该段会「只有人物窗、没有全屏 broll」' % (a, b))
        for s, e in fs_only:
            print('    [!] 独占 fs (%.1f-%.1fs)：无 pip 对齐（若非「独立满屏卡」意图，应补 pip）' % (s, e))
        if ok:
            print('    [OK] pips 全部被 fs 覆盖（%d 段）' % len(pips))
    return ok, orphans, fs_only


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plans')
    ap.add_argument('--dir')
    ap.add_argument('--glob', default='plans_*.json')
    ap.add_argument('--tol', type=float, default=0.5)
    a = ap.parse_args()

    targets = []
    if a.plans:
        targets = [a.plans]
    elif a.dir:
        targets = sorted(glob.glob(os.path.join(a.dir, a.glob)))
    else:
        ap.error('需要 --plans 或 --dir')

    if not targets:
        print('未找到任何计划文件'); sys.exit(2)

    all_ok = True
    for t in targets:
        ok, _, _ = check_plans(t, tol=a.tol, verbose=True)
        all_ok = all_ok and ok
    print('\n[GATE] %s' % ('PASS — 允许导出' if all_ok else 'FAIL — 存在孤儿 pip，阻断导出'))
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
