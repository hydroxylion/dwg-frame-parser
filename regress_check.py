# -*- coding: utf-8 -*-
"""回归校验：当前解析结果 vs 基线快照（regression_baseline.json）

用法（在项目根目录）：
  .venv/Scripts/python.exe regress_check.py                # 对比模式：单跑当前版本，diff 基线
  .venv/Scripts/python.exe regress_check.py --update       # 重新生成基线（确认变化符合预期后）
  .venv/Scripts/python.exe regress_check.py --files a.dwg,b.dwg   # 冒烟：只跑指定图纸
  .venv/Scripts/python.exe regress_check.py --folder X     # 指定图纸目录
  .venv/Scripts/python.exe regress_check.py --serial      # 强制串行（调试用）

基线内容：每张图的 frame_count + best + 尺寸分布（WxHxN 列表）。
回归只跑当前版本一次（对比旧的 HEAD 副本双跑省一半时间），逐图输出
SAME/DIFF，全部一致退出码 0，有差异退出码 1。
R78 并行化：各图解析互相独立，multiprocessing 并行（瓶颈是 ODA 转换 +
ezdxf 解析的文档加载，占单张耗时 ~90%，与 app.py 代码无关），16 核实测
全量 32min → ~4min。结果与串行逐图一致（同一 app.py、同一 parse_one）。
基线更新原则：跑 --update 前必须人工确认每处 DIFF 都是本轮改动的预期效果。
"""
import argparse
import collections
import functools
import importlib.util
import json
import os
import sys
import time

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


# ---------- R78 并行 worker ----------
_WORKER_APP = None


def _init_worker():
    """每个 worker 进程加载一次 app.py（Windows spawn 模式下经 initializer 传递）"""
    global _WORKER_APP
    _WORKER_APP = load_app()


def _worker_parse(filepath):
    """worker 任务：解析一张图 → (文件名, 摘要)。异常已由 parse_one 内部捕获。"""
    return os.path.basename(filepath), parse_one(_WORKER_APP, filepath)


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
    ap.add_argument('--serial', action='store_true', help='强制串行（调试用，默认并行）')
    args = ap.parse_args()

    only = [f.strip() for f in args.files.split(',') if f.strip()]
    files = collect_files(args.folder, only)
    if not files:
        print('未找到待回归图纸')
        sys.exit(2)

    t_start = time.time()
    # R78：worker 数 8——实测 10 worker 全量跑时，25MB 级大图（四川自贡）与
    #   其他大图并发，ODA 转换/ezdxf 解析内存峰值过高偶发报错（单跑即恢复）；
    #   8 worker 兼顾速度与稳定，另有 error 串行重试兜底。
    n_workers = 1 if (args.serial or len(files) <= 1) else min(8, os.cpu_count() or 4)

    if args.update:
        # 增量补录语义：--update --files 指定图时保留其余基线条目（目录新增图纸
        # 补录基线不必全量重跑）；全量 --update（不带 --files）重建基线
        if only and os.path.exists(BASELINE_PATH):
            with open(BASELINE_PATH, encoding='utf-8') as f:
                baseline = json.load(f)
        else:
            baseline = {}
        results = _run_parse(files, n_workers)
        for fn, cur in results:
            baseline[fn] = cur
            tag = cur.get('fc', cur.get('error', '?'))
            print(f'  [基线] {fn}: fc={tag}')
        with open(BASELINE_PATH, 'w', encoding='utf-8') as f:
            json.dump(baseline, f, ensure_ascii=False, indent=1)
        print(f'\n✅ 基线已写入 {BASELINE_PATH}（共 {len(baseline)} 张，'
              f'耗时 {(time.time() - t_start) / 60:.1f} 分钟）')
        return

    if not os.path.exists(BASELINE_PATH):
        print(f'基线文件不存在: {BASELINE_PATH}\n先用 --update 生成基线')
        sys.exit(2)
    with open(BASELINE_PATH, encoding='utf-8') as f:
        baseline = json.load(f)

    print(f'并行 worker: {n_workers}（--serial 可强制串行）')
    n_same = n_diff = 0
    for fn, cur in _run_parse(files, n_workers):
        if fn not in baseline:
            print(f'[NEW]   {fn}（基线中无此图，用 --update 补录）')
            n_diff += 1
            continue
        if cur == baseline[fn]:
            n_same += 1
            print(f'[SAME]  {fn}')
        else:
            n_diff += 1
            print(f'[DIFF]  {fn}')
            print(f'    基线: {json.dumps(baseline[fn], ensure_ascii=False)}')
            print(f'    当前: {json.dumps(cur, ensure_ascii=False)}')

    total = n_same + n_diff
    print(f'\n=== 一致 {n_same} / 变化 {n_diff}（共 {total} 张，基线含 {len(baseline)} 张，'
          f'耗时 {(time.time() - t_start) / 60:.1f} 分钟） ===')
    sys.exit(0 if n_diff == 0 else 1)


def _run_parse(files, n_workers):
    """按并行度解析全部图纸，返回 [(文件名, 摘要), ...]（完成序）。

    R78 稳定性兜底：并行下大图资源竞争可能偶发 ODA/解析报错（error 摘要），
    自动对这些图串行重跑一次覆盖结果；重跑仍错则保留错误摘要如实上屏。
    """
    if n_workers <= 1:
        appmod = load_app()
        return [(os.path.basename(fp), parse_one(appmod, fp)) for fp in files]
    import multiprocessing as mp
    with mp.Pool(n_workers, initializer=_init_worker) as pool:
        results = list(pool.imap_unordered(_worker_parse, files, chunksize=1))
    err_fns = [fn for fn, cur in results if 'error' in cur]
    if err_fns:
        print(f'⚠️ {len(err_fns)} 张并行解析报错，串行重试: ' + ', '.join(err_fns))
        appmod = load_app()
        by_name = {os.path.basename(fp): fp for fp in files}
        results = [(fn, parse_one(appmod, by_name[fn]) if fn in err_fns else cur)
                   for fn, cur in results]
    return results


if __name__ == '__main__':
    main()
