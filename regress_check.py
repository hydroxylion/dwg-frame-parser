# -*- coding: utf-8 -*-
"""回归校验：当前解析结果 vs 基线快照（regression_baseline.json）

用法（在项目根目录）：
  .venv/Scripts/python.exe regress_check.py                # 对比模式：单跑当前版本，diff 基线
  .venv/Scripts/python.exe regress_check.py --update       # 重新生成基线（确认变化符合预期后）
  .venv/Scripts/python.exe regress_check.py --files a.dwg,b.dwg   # 冒烟：只跑指定图纸
  .venv/Scripts/python.exe regress_check.py --folder X     # 指定图纸目录

基线内容：每张图的 frame_count + best + 尺寸分布（WxHxN 列表）。
回归只跑当前版本一次（对比旧的 HEAD 副本双跑省一半时间），逐图输出
SAME/DIFF，全部一致退出码 0，有差异退出码 1。
基线更新原则：跑 --update 前必须人工确认每处 DIFF 都是本轮改动的预期效果。
"""
import argparse
import collections
import functools
import importlib.util
import json
import os
import sys

print = functools.partial(print, flush=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(BASE_DIR, 'regression_baseline.json')
DEFAULT_FOLDER = r'C:\Users\hui_ou\Desktop\建筑图纸（必须校验）'


def load_app():
    """加载当前工作区 app.py（含全部未提交改动）"""
    spec = importlib.util.spec_from_file_location('app_current', os.path.join(BASE_DIR, 'app.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_one(appmod, filepath):
    """解析一张图纸 → 可对比的摘要 dict"""
    from_bytes = getattr(appmod, 'get_bounding_box_from_bytes', None)
    with open(filepath, 'rb') as f:
        data = f.read()
    try:
        r = from_bytes(data, os.path.basename(filepath), unit='mm', mode='smart')
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'[:200]}
    sizes = collections.Counter((round(c['width'], 1), round(c['height'], 1)) for c in r.get('candidates', []))
    return {
        'fc': r.get('frame_count'),
        'best': f"{round(r.get('width') or 0, 1)}x{round(r.get('height') or 0, 1)}",
        'sizes': sorted(f"{w}x{h}x{n}" for (w, h), n in sizes.items()),
    }


def collect_files(folder, only=None):
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith('.dwg'))
    if only:
        want = {f if f.lower().endswith('.dwg') else f + '.dwg' for f in only}
        files = [f for f in files if f in want]
        missing = want - set(files)
        if missing:
            print(f"⚠️ 目录中找不到: {sorted(missing)}")
    return [os.path.join(folder, f) for f in files]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--update', action='store_true', help='重新生成基线（先人工确认 DIFF 符合预期）')
    ap.add_argument('--folder', default=DEFAULT_FOLDER, help='图纸目录')
    ap.add_argument('--files', default='', help='逗号分隔的文件名（冒烟模式）')
    args = ap.parse_args()

    only = [f.strip() for f in args.files.split(',') if f.strip()]
    files = collect_files(args.folder, only)
    if not files:
        print('未找到待回归图纸')
        sys.exit(2)

    if args.update:
        appmod = load_app()
        baseline = {}
        for fp in files:
            fn = os.path.basename(fp)
            baseline[fn] = parse_one(appmod, fp)
            tag = baseline[fn].get('fc', baseline[fn].get('error', '?'))
            print(f'  [基线] {fn}: fc={tag}')
        with open(BASELINE_PATH, 'w', encoding='utf-8') as f:
            json.dump(baseline, f, ensure_ascii=False, indent=1)
        print(f'\n✅ 基线已写入 {BASELINE_PATH}（共 {len(baseline)} 张）')
        return

    if not os.path.exists(BASELINE_PATH):
        print(f'基线文件不存在: {BASELINE_PATH}\n先用 --update 生成基线')
        sys.exit(2)
    with open(BASELINE_PATH, encoding='utf-8') as f:
        baseline = json.load(f)

    appmod = load_app()
    n_same = n_diff = 0
    for fp in files:
        fn = os.path.basename(fp)
        if fn not in baseline:
            print(f'[NEW]   {fn}（基线中无此图，用 --update 补录）')
            n_diff += 1
            continue
        cur = parse_one(appmod, fp)
        if cur == baseline[fn]:
            n_same += 1
            print(f'[SAME]  {fn}')
        else:
            n_diff += 1
            print(f'[DIFF]  {fn}')
            print(f'    基线: {json.dumps(baseline[fn], ensure_ascii=False)}')
            print(f'    当前: {json.dumps(cur, ensure_ascii=False)}')

    total = n_same + n_diff
    print(f'\n=== 一致 {n_same} / 变化 {n_diff}（共 {total} 张，基线含 {len(baseline)} 张） ===')
    sys.exit(0 if n_diff == 0 else 1)


if __name__ == '__main__':
    main()
