# -*- coding: utf-8 -*-
"""check_pip.py —— PIP 布局「导出前」实时闸门（技能层，2026-10-09 增补，配 check_pip_coverage.py）。

与 check_pip_coverage.py（计划层 pips⊆fs）互补，本脚本直接读**工程磁盘时间线**，
校验渲染期真正生效的四条不变式（SKILL §3.6.6）：

  (1) pips ⊆ fs        : 每个 pip 段必须被某个 fs 段完整覆盖（容差 0.5s）
  (2) z-order          : pip.order > fs.order 且 pip.order > graf.order（人像永在背景卡之上）
  (3) 人像永可见(fs)   : 每个 fs 满屏卡段都要与某 pip 段「时间相交」（孤儿 fs = 满屏卡盖人）
  (4) PIP 源人脸居中    : 竖屏 pip 的源资产须为「按人脸中心正方形裁切」版（make_pip_source.py 产物），
                         而非顶部裁切（crop=W:W:0:0）——后者是「人头不居中」根因

任一条不过 → 退出码 1（阻断导出）；全过 → 退出码 0。

用法：
  python check_pip.py --project <pid>
  python check_pip.py --project <pidA> --project <pidB> --project <pidC>
  python check_pip.py --timeline "<path>/Timeline_xxx.json" --assets assets_C.json
  python check_pip.py --projects-dir "C:/.../ChatCut/projects" --match b930

注意：--assets 传入 plans 的 assets_<k>.json（含 'PIP源-V' 键=人脸中心版 assetId）时，
才校验 (4)；否则跳过 (4)。
"""
import argparse
import glob
import json
import os
import sys

FPS = 30.0


def _default_projects_root():
    """ChatCut 工程根目录：env CHATCUT_PROJECTS > %APPDATA%\\ChatCut\\projects（跨账号可移植）。"""
    env = os.environ.get("CHATCUT_PROJECTS")
    if env:
        return env
    appdata = os.environ.get("APPDATA") or r"C:\Users\Administrator\AppData\Roaming"
    return os.path.join(appdata, "ChatCut", "projects")


PROJ_ROOT_DEFAULT = _default_projects_root()


def find_timeline(pid=None, timeline=None, projects_dir=None, match=None):
    if timeline:
        return timeline
    if pid:
        allj = glob.glob(os.path.join(projects_dir, pid,
                         "project.chatcutproject", "timelines", "*.json"))
        cand = [f for f in allj if "Timeline" in f] or allj   # 🔴 UUID 命名时间线也要能找到
        return cand[0] if cand else None
    # --match 扫整个 projects 目录
    res = []
    for d in glob.glob(os.path.join(projects_dir, "*")):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        if match and match not in name:
            continue
        cand = [f for f in glob.glob(os.path.join(d, "project.chatcutproject",
                 "timelines", "*.json")) if "Timeline" in f]
        if cand:
            res.append((name, cand[0]))
    return res  # list of (name, path)


def load_items(tl):
    tracks = tl["tracks"]
    items = []
    for key, val in tl.items():
        if key.endswith("Items") and isinstance(val, list):
            for it in val:
                if isinstance(it, dict) and "trackId" in it:
                    it = dict(it); it["_kind"] = key; items.append(it)
    return tracks, items


def audit(tl_path, assets_map=None, verbose=True):
    tl = json.load(open(tl_path, encoding="utf-8"))
    tracks, items = load_items(tl)
    tr_by_name = {t["name"]: t for t in tracks}
    order = {t["name"]: t.get("order") for t in tracks}
    name_by_id = {v: k for k, v in (assets_map or {}).items()}

    def items_of(name):
        t = tr_by_name.get(name)
        return [i for i in items if t and i.get("trackId") == t["id"]]

    def segs(name):
        out = []
        for i in sorted(items_of(name), key=lambda x: x["startFrame"]):
            out.append((i["startFrame"] / FPS, (i["startFrame"] + i["durationFrames"]) / FPS))
        return out

    fs = segs("fs"); pip = segs("pip"); graf = segs("graf")
    problems = []
    # 🔴 空真闸（2026-10-16 增）：fs 与 pip 全空 = place 静默失败（编辑器工程未就绪，
    #    push_asset 全败）—— 本产线必有 B-roll 与人像小窗，空时间线直接阻断，不许空真 PASS
    if not fs and not pip:
        problems.append("时间线 fs/pip 轨全空（place 静默失败？重跑 place 直至落轨成功）")

    if verbose:
        print("  fs  :", [(round(a, 1), round(b, 1)) for a, b in fs])
        print("  pip :", [(round(a, 1), round(b, 1)) for a, b in pip])
        print("  graf:", [(round(a, 1), round(b, 1)) for a, b in graf])

    # (1) pips ⊆ fs
    orphans = []
    for s, e in pip:
        if not any(fs0 - 0.5 <= s and e <= fe + 0.5 for fs0, fe in fs):
            orphans.append((round(s, 1), round(e, 1)))
    if orphans:
        problems.append("pips⊆fs 违例(孤儿 pip): %s" % orphans)
        print("  [X](1) 孤儿 pip(无 fs 覆盖):", orphans)
    else:
        print("  [OK](1) pips⊆fs 全部覆盖")

    # (2) z-order
    po, fo, go = order.get("pip"), order.get("fs"), order.get("graf")
    zok = True
    if po is None or fo is None:
        problems.append("缺 pip/fs 轨(z-order 无法校验)"); print("  [X](2) 缺 pip/fs 轨"); zok = False
    else:
        if po <= fo:
            problems.append("z-order pip(%s)<=fs(%s)" % (po, fo)); print("  [X](2) pip<fs"); zok = False
        if go is not None and po <= go:
            problems.append("z-order pip(%s)<=graf(%s)" % (po, go)); print("  [X](2) pip<graf"); zok = False
    if zok:
        print("  [OK](2) z-order pip(%s)>fs(%s)%s" % (po, fo, (",>graf(%s)" % go) if go is not None else ""))

    # (3) 人像永可见(fs): 每个 fs 段须与某 pip 段相交
    orphan_fs = []
    for s, e in fs:
        if not any(ps0 < e - 1e-6 and s < pe - 1e-6 for ps0, pe in pip):
            orphan_fs.append((round(s, 1), round(e, 1)))
    if orphan_fs:
        problems.append("孤儿 fs(无重叠 pip): %s" % orphan_fs)
        print("  [X](3) 孤儿 fs:", orphan_fs)
    else:
        print("  [OK](3) 每个 fs 段都有重叠 pip")

    # (4) 竖屏 PIP 源是否人脸中心版
    if assets_map and "PIP源-V" in assets_map:
        expected = assets_map["PIP源-V"]
        used = {i.get("assetId") for i in items_of("pip")}
        if expected and expected not in used:
            problems.append("pip 轨未使用人脸中心 PIP 源(仍用旧源/顶部裁切)")
            print("  [X](4) pip 轨未用 PIP源-V(人脸中心版), 实际:", used)
        else:
            print("  [OK](4) pip 轨使用 PIP源-V(人脸中心版)")

    ok = not problems
    if verbose:
        print("  >>>", "✅ 通过" if ok else "❌ " + " | ".join(problems))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--timeline")
    ap.add_argument("--projects-dir", default=PROJ_ROOT_DEFAULT)
    ap.add_argument("--match", help="扫 projects 目录时按名称过滤(如 b930)")
    ap.add_argument("--assets", help="assets_<k>.json（含 PIP源-V）用于校验(4)")
    a = ap.parse_args()

    assets_map = None
    if a.assets and os.path.exists(a.assets):
        assets_map = json.load(open(a.assets, encoding="utf-8"))

    targets = []
    if a.timeline:
        targets = [("timeline", a.timeline)]
    elif a.project:
        p = find_timeline(pid=a.project, projects_dir=a.projects_dir)
        targets = [("project:%s" % a.project, p)] if p else []
    else:
        found = find_timeline(projects_dir=a.projects_dir, match=a.match)
        targets = [("match:%s" % n, p) for n, p in found]

    if not targets:
        print("未找到任何时间线"); sys.exit(2)

    all_ok = True
    for label, p in targets:
        print("=" * 70)
        print("[%s] %s" % (label, p))
        if not p or not os.path.exists(p):
            print("  !! 文件不存在"); all_ok = False; continue
        ok = audit(p, assets_map=assets_map, verbose=True)
        all_ok = all_ok and ok

    print("\n[GATE] %s" % ("PASS — 允许导出" if all_ok else "FAIL — 阻断导出"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
