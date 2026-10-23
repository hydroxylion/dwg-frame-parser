# -*- coding: utf-8 -*-
"""回归校验：当前解析结果 vs 基线快照（regression_baseline.json）

用法（在项目根目录）：
  .venv/Scripts/python.exe regress_check.py                # 对比模式：单跑当前版本，diff 基线
  .venv/Scripts/python.exe regress_check.py --update       # 重新生成基线（确认变化符合预期后）
  .venv/Scripts/python.exe regress_check.py --files a.dwg,b.dwg   # 冒烟：只跑指定图纸
  .venv/Scripts/python.exe regress_check.py --folder X     # 指定图纸目录
  .venv/Scripts/python.exe regress_check.py --serial      # 强制串行（调试用）
  .venv/Scripts/python.exe regress_check.py --no-cache   # 禁用 DXF 转换缓存

基线内容：每张图的 frame_count + best + 尺寸分布（WxHxN 列表）。
回归只跑当前版本一次（对比旧的 HEAD 副本双跑省一半时间），逐图输出
SAME/DIFF，全部一致退出码 0，有差异退出码 1。
R78 并行化：各图解析互相独立，multiprocessing 并行（瓶颈是 ODA 转换 +
ezdxf 解析的文档加载，占单张耗时 ~90%，与 app.py 代码无关），16 核实测
全量 32min → ~4min。结果与串行逐图一致（同一 app.py、同一 parse_one）。
R78b DXF 转换缓存：DWG→DXF 的 ODA 转换结果只依赖 DWG 内容（mtime+size
做键）+ ODA 转换器版本，与 app.py 代码无关——首次转换后 gzip 缓存到
regress_dxf_cache/（可随时整个删除），后续回归直接喂 DXF（与生产 odafc
路径的 ezdxf 读取语义一致：odafc 的 audit 传给的是 ODA 转换器，产物已含
audit），单张再省 ~20%（ODA 转换的 25s 级开销）。转换失败自动回退 DWG
原路径，不影响正确性。
基线更新原则：跑 --update 前必须人工确认每处 DIFF 都是本轮改动的预期效果。
"""
import argparse
import collections
import functools
import gzip
import importlib.util
import json
import os
import sys
import tempfile
import time

print = functools.partial(print, flush=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(BASE_DIR, 'regression_baseline.json')
DEFAULT_FOLDER = r'C:\Users\hui_ou\Desktop\建筑图纸（必须校验）'
DXF_CACHE_DIR = os.path.join(BASE_DIR, 'regress_dxf_cache')

# R94：外部参照（xref）搜索目录。
#   get_bounding_box_from_bytes 只拿到文件 bytes（主文件被写到临时目录），
#   无法从路径推断原始所在目录，因此 xref 参照文件必须靠环境变量
#   FRAME_PARSER_XREF_DIRS 指定。回归时若不设，含 xref 的图纸会加载不到
#   参照 → fc 与"设了变量"的基线不一致（water-2014.09.03 会从 14 掉到 6）。
#   这里给回归配置默认值：图纸目录本身 + 几个已知的参照文件存放处。
#   用户环境不同的话，在自己 shell 里 export FRAME_PARSER_XREF_DIRS 覆盖。
_DEFAULT_XREF_DIRS = [
    DEFAULT_FOLDER,
    r'C:\Users\hui_ou\Desktop\建筑项目图纸 - 非标\雅安中学\雅中水电\雅中给排水图\综合艺术楼',
]
if not os.environ.get('FRAME_PARSER_XREF_DIRS'):
    _exist = [d for d in _DEFAULT_XREF_DIRS if os.path.isdir(d)]
    if _exist:
        os.environ['FRAME_PARSER_XREF_DIRS'] = ';'.join(_exist)


def load_app():
    """加载当前工作区 app.py（含全部未提交改动）"""
    spec = importlib.util.spec_from_file_location('app_current', os.path.join(BASE_DIR, 'app.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- R78b DXF 转换缓存 ----------
_DXF_CACHE = True      # --no-cache 置 False
_CACHE_STATS = {'hit': 0, 'miss': 0, 'fail': 0}


def _cache_path(filepath):
    st = os.stat(filepath)
    stem = os.path.splitext(os.path.basename(filepath))[0]
    safe = ''.join(c if (c.isascii() and (c.isalnum() or c in '-_')) else '_'
                   for c in stem)[:48]
    return os.path.join(DXF_CACHE_DIR, f'{safe}-{st.st_size}-{int(st.st_mtime)}.dxf.gz')


def _cache_get_or_convert(filepath):
    """DWG → DXF 转换结果缓存。返回 DXF bytes；失败返回 None（回退 DWG 原路径）。

    转换段复刻 ezdxf.addons.odafc.readfile 的内部实现（_detect_version →
    _odafc_arguments(audit=True) → _execute_odafc），产物即生产路径读取的
    同一中间 DXF——缓存不改变解析语义。
    """
    cpath = _cache_path(filepath)
    if os.path.exists(cpath):
        try:
            with open(cpath, 'rb') as f:
                data = gzip.decompress(f.read())
            _CACHE_STATS['hit'] += 1
            return data
        except Exception:
            try:
                os.remove(cpath)   # 缓存损坏，删掉重转
            except OSError:
                pass
    try:
        from pathlib import Path
        from ezdxf.addons import odafc as _odafc
        infile = Path(filepath).absolute()
        version = _odafc._detect_version(str(infile))
        with tempfile.TemporaryDirectory(prefix='odafc_cache_') as tmp_dir:
            args = _odafc._odafc_arguments(
                infile.name, str(infile.parent), tmp_dir,
                output_format='DXF', version=version, audit=True)
            _odafc._execute_odafc(args)
            out_file = Path(tmp_dir) / infile.with_suffix('.dxf').name
            if not out_file.exists():
                _CACHE_STATS['fail'] += 1
                return None
            data = out_file.read_bytes()
        os.makedirs(DXF_CACHE_DIR, exist_ok=True)
        with open(cpath, 'wb') as f:
            f.write(gzip.compress(data, compresslevel=1))
        _CACHE_STATS['miss'] += 1
        return data
    except Exception:
        # 私有 API 变动 / ODA 异常 → 回退 DWG 原路径（正确性不受影响）
        _CACHE_STATS['fail'] += 1
        return None


# ---------- R78 并行 worker ----------
_WORKER_APP = None


def _init_worker(no_cache=False):
    """每个 worker 进程加载一次 app.py（Windows spawn 模式下经 initializer 传递）。

    no_cache 须经 initargs 显式传入——spawn 模式 worker 是全新进程，主进程里
    的 global _DXF_CACHE=False 不会带过去。
    """
    global _WORKER_APP, _DXF_CACHE
    _DXF_CACHE = not no_cache
    _WORKER_APP = load_app()


def _worker_parse(filepath):
    """worker 任务：解析一张图 → (文件名, 摘要)。异常已由 parse_one 内部捕获。

    R78b：DWG 且缓存开启时优先走 DXF 缓存（与生产 odafc 路径 ezdxf 读取
    语义一致），失败回退 DWG 原路径。
    """
    fn = os.path.basename(filepath)
    if _DXF_CACHE and fn.lower().endswith('.dwg'):
        dxf = _cache_get_or_convert(filepath)
        if dxf is not None:
            return fn, parse_one(_WORKER_APP, filepath, data=dxf,
                                 name=fn[:-4] + '.dxf')
    return fn, parse_one(_WORKER_APP, filepath)


def parse_one(appmod, filepath, data=None, name=None):
    """解析一张图纸 → 可对比的摘要 dict（data/name 供 DXF 缓存路径传入）"""
    from_bytes = getattr(appmod, 'get_bounding_box_from_bytes', None)
    if data is None:
        with open(filepath, 'rb') as f:
            data = f.read()
    if name is None:
        name = os.path.basename(filepath)
    try:
        r = from_bytes(data, name, unit='mm', mode='smart')
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
    ap.add_argument('--no-cache', action='store_true', help='禁用 DXF 转换缓存（全走 DWG 原路径）')
    args = ap.parse_args()

    global _DXF_CACHE
    _DXF_CACHE = not args.no_cache

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

    def _cached_n():
        """DXF 缓存已存在的 DWG 数（并行下 worker 内计数主进程不可见，
        按缓存文件存在性前后差值统计命中/新增）"""
        return sum(1 for fp in files if fp.lower().endswith('.dwg')
                   and os.path.exists(_cache_path(fp)))

    cache_pre = _cached_n() if _DXF_CACHE else 0

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
    cache_tag = ''
    if _DXF_CACHE:
        cache_tag = (f'，DXF缓存 命中{cache_pre}'
                     f'/新增{_cached_n() - cache_pre}')
    print(f'\n=== 一致 {n_same} / 变化 {n_diff}（共 {total} 张，基线含 {len(baseline)} 张，'
          f'耗时 {(time.time() - t_start) / 60:.1f} 分钟{cache_tag}） ===')
    sys.exit(0 if n_diff == 0 else 1)


def _run_parse(files, n_workers):
    """按并行度解析全部图纸，返回 [(文件名, 摘要), ...]（完成序）。

    R78 稳定性兜底：并行下大图资源竞争可能偶发 ODA/解析报错（error 摘要），
    自动对这些图串行重跑一次覆盖结果；重跑仍错则保留错误摘要如实上屏。
    """
    if n_workers <= 1:
        global _WORKER_APP
        if _WORKER_APP is None:
            _WORKER_APP = load_app()
        return [_worker_parse(fp) for fp in files]
    import multiprocessing as mp
    with mp.Pool(n_workers, initializer=_init_worker,
                 initargs=(not _DXF_CACHE,)) as pool:
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
