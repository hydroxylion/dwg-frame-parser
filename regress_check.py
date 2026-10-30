# -*- coding: utf-8 -*-
"""回归校验：当前解析结果 vs 基线快照（regression_baseline.json）

用法（在项目根目录）：
  .venv/Scripts/python.exe regress_check.py                # 对比模式：单跑当前版本，diff 基线
  .venv/Scripts/python.exe regress_check.py --update       # 补录基线（确认变化符合预期后）
  .venv/Scripts/python.exe regress_check.py --update --prune   # 补录并删除「本次没跑到」的旧条目
  .venv/Scripts/python.exe regress_check.py --files a.dwg,b.dwg   # 冒烟：只跑指定图纸
  .venv/Scripts/python.exe regress_check.py --folder X     # 指定图纸目录
  .venv/Scripts/python.exe regress_check.py --serial      # 强制串行（调试用）
  .venv/Scripts/python.exe regress_check.py --workers 4   # 指定并行度
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
R120 基线保护：
  ① 默认**保留**「本次没跑到」的既有条目（只列出来提醒），删除必须显式 `--prune`。
     旧逻辑在全量 --update 时把 baseline 清成 {} 整表重建，目录里已不存在的历史
     条目被静默删除（实测 R113~R119 连续 7 轮删掉同一条、每轮手工补回）。
  ② 解析报错**不覆盖**原本正常的条目（否则基线记下 error，之后永远 SAME，
     这张图的回归覆盖就静默消失了）。
  ③ 输出按文件名排序 + indent=2 定稿，避免并行完成序 / 缩进差异制造无意义 diff。

R127 并行度自适应（治「回归要 40 多分钟」）：
  R123~R126 那几轮为了避开 MemoryError 改用了 --serial，113 张串行 ≈ 45min。
  实测确认**不是 app.py 变慢**（同一张图同一份缓存 DXF，R122 版 41.05s vs
  R126 版 41.79s，±2% 噪声），慢的是并行度丢了。改成：
  ① worker 数 = min(CPU 数, 可用内存 // 2.2GB)，可用 --workers 覆盖；
  ② 按图纸体积**降序**提交，压掉「最后只剩一张 29MB 大图单线程收尾」的长尾；
  ③ 每张完成即上屏（含耗时），不再等全部跑完一次性刷屏；
  ④ 结尾输出 TOP 慢图——并行墙钟由最慢那张决定。
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


_APP_MOD = None


def load_app():
    """加载当前工作区 app.py（含全部未提交改动）。

    R102：结果缓存。同一进程里多次调用只 exec_module 一次——app.py 顶层有
    日志 handler 初始化，logging 的 logger 是进程级单例，重复 exec 会走到
    「handlers 已存在则不复用」的分支（已在 app.py 内加固，但缓存更省事）。
    """
    global _APP_MOD
    if _APP_MOD is None:
        spec = importlib.util.spec_from_file_location(
            'app_current', os.path.join(BASE_DIR, 'app.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _APP_MOD = mod
    return _APP_MOD


# ---------- R78b DXF 转换缓存 ----------
_DXF_CACHE = True      # --no-cache 置 False
_CACHE_STATS = {'hit': 0, 'miss': 0, 'fail': 0}


def _cache_path(filepath):
    st = os.stat(filepath)
    stem = os.path.splitext(os.path.basename(filepath))[0]
    safe = ''.join(c if (c.isascii() and (c.isalnum() or c in '-_')) else '_'
                   for c in stem)[:48]
    # R102：缓存键必须带上转换版本。转换目标版本会影响产物内容（R14 只支持
    #   一个图纸空间，会把多余布局丢掉），只靠 size+mtime 会让旧的 R14 缓存
    #   继续命中，掩盖生产路径的变化（回归假绿）。
    ver = _convert_version(filepath) or 'auto'
    return os.path.join(DXF_CACHE_DIR,
                        f'{safe}-{st.st_size}-{int(st.st_mtime)}-{ver}.dxf.gz')


def _convert_version(filepath):
    """与生产 load_document 同一套版本决策（app.dwg_convert_version）。"""
    try:
        return load_app().dwg_convert_version(str(filepath))
    except Exception:
        return None


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
        # R102：版本决策与生产 load_document 一致（低版本源文件抬到 ACAD2018）
        version = _convert_version(str(infile)) or _odafc._detect_version(str(infile))
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
_TIMINGS = {}   # 文件名 -> 解析耗时（秒）；R127：报告输出 TOP 慢图
_TRIM_WS = False   # R127：只有并行 worker 才压 working set（串行主进程压了纯亏 IO）


# ---------- R127 并行度自适应 ----------
# 背景：R123~R126 那几轮回归用了 --serial（串行 113 张 ≈ 45min），原因是并行
#   8 worker 反复卡死——不是抛 MemoryError 摘要，而是 worker 被系统杀掉后
#   mp.Pool 静默挂住，只能强杀重跑。根因是内存：单 worker 解析大图峰值 RSS
#   1.5~1.9GB（29MB 级大图 2~3GB），8 路 ≈ 15GB；而开发机上 PyCharm/CAD/浏览器
#   常驻后 32GB 内存只剩 6~11GB 空闲。**并行度必须跟着当前可用内存走**。
#
#   注意峰值不是累加的：worker 进程跑完一张图，Python 不会把 arena 还给 OS，
#   所以单个 worker 的水位≈"它处理过最大的那张图"，总占用≈workers × 该峰值。
MAX_WORKERS = 8         # 超过 8 路：ODA/ezdxf 在大图上并发争抢，稳定性下降


def _avail_phys_gb():
    """当前可用物理内存（GB）。取不到返回 None（调用方退回按 CPU 数决定）。"""
    try:
        import ctypes

        class _MEMSTATUS(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong),
                        ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong),
                        ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

        st = _MEMSTATUS()
        st.dwLength = ctypes.sizeof(_MEMSTATUS)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return st.ullAvailPhys / 1024 ** 3
    except Exception:
        return None


def _file_size(path):
    """文件字节数（取不到返回 0）。R127：按体积降序提交，压掉并行长尾。"""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _submit_order(files, n_workers):
    """并行时的任务提交顺序：体积降序；串行时保持原顺序（字母序，便于对读）。

    imap_unordered + chunksize=1 是动态调度，谁先提交谁先占 worker。按字母序
    提交时 29MB 的「总平面1116」可能排在末尾，最后一段只剩它一张单线程跑，
    把总分成的收益吃掉一大截。大图先跑，短图自然填满尾部空档。
    """
    if n_workers <= 1:
        return list(files)
    return sorted(files, key=_file_size, reverse=True)


# ---------- R127 负载分档与并行调度 ----------
# 为什么不能按 DWG 体积分档：体积是压缩后的，与解析负载（实体数）不成比例。
#   实测反例：`地下室电力t3.dwg` DWG 4.2MB / 缓存 DXF 2.9MB，看着是小图，
#   一旦跟另 7 张一起 8 路并行，单张从 30s 级劣化到 **152s**（换页拖累）。
#   而 `香槟半岛.dwg` DWG 9.4MB 却有 gz 4.5MB，真正的负载比它小一半。
#   → 用**缓存 DXF 的 gz 体积**当负载代理（≈实体数），比 DWG 体积准得多。
TIER_GZ_MB = (8.0, 3.0)     # 分档边界：≥8 超重 / ≥3 中等 / 其余轻量
# 单 worker 峰值内存(GB) ≈ 缓存 gz(MB) × 系数。**0.8 是实测标定值，不是估值**：
#   回归跑到一半用 Get-Process 抓 worker 的 PeakWorkingSet64，最大一个是
#   **5088MB**（对应 gz 6.5MB 的暖通图），即 ≈ 0.78 GB/MB。此前按 0.4 估，
#   把 3 路的中档算成"占 6GB、够用"，实际是 15GB → 立刻换页，单张从 45s
#   劣化到 134s（并行反而更慢）。
PEAK_PER_GZ_MB = 0.8
PEAK_FLOOR_GB = 0.6         # 再轻的图也按 0.6GB 计（Python/ezdxf 基线占用）
MEM_SAFETY = 0.75           # 只敢用 75% 的可用内存：peak 是"档内最重那张"的估算，
                            #   多个 worker 同时撞上重图才是峰值时刻，不留余量会打穿


# 缓存 DXF 的 gz 体积 ≈ DWG 体积 × 该系数（本机 113 张实测：29.5→18.2、24.3→15.9、
#   6.8→6.9、4.2→2.9 等，均值约 0.65）。用它把 DWG 体积折算成"负载 MB"。
GZ_PER_DWG = 0.65


def _load_mb(filepath):
    """解析负载代理（MB，口径 = 缓存 DXF 的 gz 体积）。

    **不直接读缓存文件体积**：那要走 `_cache_path()` → `dwg_convert_version()`，
    113 张图各探一次 DWG 版本，实测把启动阶段拖了两分钟（分档还没开始跑图）。
    改用 DWG 体积 × `GZ_PER_DWG` 折算——与真实 gz 单调同序，分档结果一致（唯一
    偏差 `g013~g016.dwg` 压缩比偏高，算进中档而非重档，但它两种档位下都是 1 路）。
    """
    return _file_size(filepath) / 1024 / 1024 * GZ_PER_DWG


def _peak_gb(load_mb):
    """单 worker 处理这张图时的峰值内存估算（GB）。"""
    return max(PEAK_FLOOR_GB, load_mb * PEAK_PER_GZ_MB)


def _plan_workers(peak_gb, avail_gb, cap=MAX_WORKERS):
    """这一档能开几路：可用内存（打 MEM_SAFETY 折扣）÷ 档内最重那张的峰值。

    折扣是必须的：peak 是"档内最重那张"的估算，实际只有少数 worker 会同时
    撞上重图，但**最坏情况就是它们同时撞上**——不留余量时 4 路 × 2.74GB 会
    正好吃满 11.5GB 可用内存，然后开始换页。
    """
    if not avail_gb:
        return max(1, cap)
    budget = avail_gb * MEM_SAFETY
    return max(1, min(cap, int(budget // peak_gb)))


def _plan_batches(files, avail_gb, max_workers=MAX_WORKERS):
    """把图纸按负载分档 → [(该档文件列表, 该档并行度), ...]（重档在前）。

    每档的并行度由**档内最重那张图**的峰值决定，不是按平均值——峰值叠加是
    OOM 的直接原因：实测 29.5MB/24.3MB/11.3MB 三张并发把 7.2GB 空闲打到 0.6GB。
    档数刻意控制在 3 档（每档起一个 Pool，起停成本约 2~3s，档太多反而慢）。
    """
    tiers = ([], [], [])
    for fp in files:
        g = _load_mb(fp)
        idx = 0 if g >= TIER_GZ_MB[0] else (1 if g >= TIER_GZ_MB[1] else 2)
        tiers[idx].append(fp)
    out = []
    for tier in tiers:
        if not tier:
            continue
        peak = _peak_gb(max(_load_mb(p) for p in tier))
        out.append((tier, _plan_workers(peak, avail_gb, max_workers)))
    return out


def _init_worker(no_cache=False):
    """每个 worker 进程加载一次 app.py（Windows spawn 模式下经 initializer 传递）。

    no_cache 须经 initargs 显式传入——spawn 模式 worker 是全新进程，主进程里
    的 global _DXF_CACHE=False 不会带过去。
    """
    global _WORKER_APP, _DXF_CACHE, _TRIM_WS
    _DXF_CACHE = not no_cache
    _TRIM_WS = True      # worker 进程才压 working set（见 _trim_working_set）
    _WORKER_APP = load_app()


def _trim_working_set():
    """把当前进程的 working set 换出到页面文件（仅 Windows 生效，失败静默）。

    为什么只在并行 worker 里做（`_TRIM_WS` 开关）：一个 worker 解析完 50MB DXF
    后 Python 不会把 arena 还给系统——峰值 5.0GB、跑完 WS 仍停在 2.3GB，几个
    worker 的水位叠起来就换页。但**串行路径绝对不能调**：主进程里 app.py 的
    全部模块都在工作集里，换出去之后下一张要整块 fault 回来，实测总平面从
    60.2s 劣化到 200s+（内存本来就没压力，纯亏 IO）。
    """
    try:
        import ctypes
        ctypes.windll.psapi.EmptyWorkingSet(
            ctypes.windll.kernel32.GetCurrentProcess())
    except Exception:
        pass


def _worker_parse(filepath):
    """worker 任务：解析一张图 → (文件名, 摘要, 耗时秒)。异常已由 parse_one 内部捕获。

    R78b：DWG 且缓存开启时优先走 DXF 缓存（与生产 odafc 路径 ezdxf 读取
    语义一致），失败回退 DWG 原路径。
    R127：第三个返回值是墙钟耗时，用于报告 TOP 慢图——并行的慢图就是长尾；
    并行 worker 在返回前压一次 working set（`_TRIM_WS`，串行路径不开）。
    """
    _t0 = time.time()
    fn = os.path.basename(filepath)
    out = None
    if _DXF_CACHE and fn.lower().endswith('.dwg'):
        dxf = _cache_get_or_convert(filepath)
        if dxf is not None:
            out = (fn, parse_one(_WORKER_APP, filepath, data=dxf,
                                 name=fn[:-4] + '.dxf'),
                   round(time.time() - _t0, 2))
    if out is None:
        out = fn, parse_one(_WORKER_APP, filepath), round(time.time() - _t0, 2)
    if _TRIM_WS:
        _trim_working_set()
    return out


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


def merge_baseline(baseline, results, prune=False):
    """把本次解析结果并入既有基线（R120）。抽成纯函数以便单测。

    results: [(文件名, 摘要), ...]（`_run_parse` 的返回）
    返回 (new_baseline, stale, err_kept)：
      stale    —— 基线里有、但本次没跑到的条目（文件名升序）。
                  prune=False 时**原样保留**在 new_baseline 里，只有 prune=True 才删。
      err_kept —— 本次解析报错、因而**没有覆盖**原正常条目的文件名（升序）。

    两条保护都是为了让「回归覆盖」不会静默缩小：
      ① 不删未命中条目（旧逻辑全量 --update 时整表重建，目录里已不存在的历史条目
         就这么没了，报告只打"共 N 张"看不出来）；
      ② 报错不覆盖正常条目（否则基线把 error 记成"预期结果"，之后永远 SAME）。
    """
    out = dict(baseline)
    parsed = set()
    err_kept = []
    for fn, cur in results:
        parsed.add(fn)
        if 'error' in cur:
            prev = out.get(fn)
            if prev is not None and 'error' not in prev:
                err_kept.append(fn)   # 保留原条目，不写入本次错误摘要
                continue
        out[fn] = cur
    stale = sorted(k for k in out if k not in parsed)
    if prune:
        for k in stale:
            del out[k]
    return out, stale, sorted(err_kept)


def classify_result(fn, cur, baseline):
    """把一张图的结果与基线比对 → 'same' / 'diff' / 'new'（纯函数，便于单测）。

    R127 为什么要单独抽出来：**error 项也必须比基线**。`一层.dwg` /
    `X-11号平面.dwg` 在基线里记的就是「未检测到图框」的预期失败，一模一样地
    复现属于 SAME。改造时曾把 error 无条件记成 DIFF，结果 111 SAME + 2 ERR
    被当成"本轮退化"，实际解析结果零变化——白查了一轮不存在的回归。
    """
    if fn not in baseline:
        return 'new'
    return 'same' if cur == baseline[fn] else 'diff'


def main():
    global _DXF_CACHE
    ap = argparse.ArgumentParser()
    ap.add_argument('--update', action='store_true', help='补录基线（先人工确认 DIFF 符合预期）')
    ap.add_argument('--prune', action='store_true',
                    help='配合 --update：删除「本次没跑到」的既有基线条目（默认保留并列出）')
    ap.add_argument('--folder', default=DEFAULT_FOLDER, help='图纸目录')
    ap.add_argument('--files', default='', help='逗号分隔的文件名（冒烟模式）')
    ap.add_argument('--serial', action='store_true', help='强制串行（调试用，默认并行）')
    ap.add_argument('--workers', type=int, default=0,
                    help=f'并行度上限（默认按可用内存自适应，上限 {MAX_WORKERS}）')
    ap.add_argument('--no-cache', action='store_true', help='禁用 DXF 转换缓存（全走 DWG 原路径）')
    args = ap.parse_args()

    _DXF_CACHE = not args.no_cache

    only = [f.strip() for f in args.files.split(',') if f.strip()]
    files = collect_files(args.folder, only)
    if not files:
        print('未找到待回归图纸')
        sys.exit(2)

    t_start = time.time()
    # R127：并行度不再写死 8，改为「CPU 数 与 当前可用内存」取小，且按负载分档。
    #   写死 8 的老逻辑在只有 6GB 空闲的机器上会撞 OOM，worker 被系统杀掉后
    #   pool 静默挂住——这才是 R123~R126 那几轮被迫 --serial 跑 45 分钟的原因。
    avail_gb = _avail_phys_gb()
    mem_txt = (f'可用内存 {avail_gb:.1f}GB' if avail_gb is not None
               else '内存探测不可用')
    cap = MAX_WORKERS
    if args.serial or len(files) <= 1:
        n_workers = 1
        why = '--serial 强制串行' if args.serial else '只跑 1 张'
    elif args.workers:
        n_workers = max(1, min(args.workers, MAX_WORKERS))
        cap = n_workers
        why = f'--workers {args.workers} 显式指定上限'
    else:
        n_workers = MAX_WORKERS
        why = f'{mem_txt} ÷ {PEAK_PER_GZ_MB}GB/每 MB 负载，按档位定每档路数'
    print(f'并行 worker: 上限 {min(n_workers, len(files))} 路（{why}）')

    def _cached_n():
        """DXF 缓存已存在的 DWG 数（并行下 worker 内计数主进程不可见，
        按缓存文件存在性前后差值统计命中/新增）"""
        return sum(1 for fp in files if fp.lower().endswith('.dwg')
                   and os.path.exists(_cache_path(fp)))

    cache_pre = _cached_n() if _DXF_CACHE else 0

    if args.update:
        # 补录语义（R120 改）：**一律**以现有基线为底，把本次跑到的图覆盖进去。
        #
        # 为什么不再"全量重建"：旧逻辑在不带 --files 时把 baseline 清成 {}，于是
        #   「目录里已不存在的历史条目」被静默删掉。AC-P02、E01、D03-B (1).dwg 是
        #   文件改名后的历史残留（现已重命名为不带 " (1)" 的名字），R113~R119 连续
        #   7 轮的 --update 每次都删掉它、每轮都要从备份手工补回。
        #   关键是**删除不可见**：报告只打"共 N 张"，少一条看不出来；代价是一张
        #   图纸彻底退出回归覆盖。所以默认保留 + 逐条列出，删除必须显式 --prune。
        baseline = {}
        if os.path.exists(BASELINE_PATH):
            with open(BASELINE_PATH, encoding='utf-8') as f:
                baseline = json.load(f)
        n_before = len(baseline)

        def _on_update(fn, cur, secs):
            """R127：逐张实时上屏（error 项留到后面统一提示，重试后可能变好）"""
            if 'error' in cur:
                return
            print(f'  [基线] {fn}: fc={cur.get("fc")}  ({secs:.1f}s)')

        results = _run_parse(files, n_workers, on_result=_on_update,
                             avail_gb=avail_gb)
        merged, stale, err_kept = merge_baseline(baseline, results, prune=args.prune)

        for fn, cur in results:
            if fn in err_kept:
                print(f'  [基线·跳过] {fn}: 解析报错，保留原条目 fc='
                      f'{baseline[fn].get("fc")}（本次错误: {cur["error"]}）')
            elif 'error' in cur:
                print(f'  [基线] {fn}: ⚠️ 解析报错 {cur["error"]}')

        if args.prune:
            if stale:
                print(f'\n🗑  --prune：已删除 {len(stale)} 条本次未跑到的基线条目:')
                for k in stale:
                    print(f'      - {k}')
            else:
                print('\n--prune：没有本次未跑到的条目，无需删除')
        elif stale:
            if only:
                # 局部补录（--files）时其余条目本就应该保留，不刷屏
                print(f'\nℹ️  保留 {len(stale)} 条未参与本次补录的既有条目（--files 局部模式）')
            else:
                print(f'\n⚠️  保留 {len(stale)} 条本次未跑到的既有基线条目（**未删除**）:')
                for k in stale:
                    print(f'      · {k}  fc={baseline[k].get("fc", "?")}')
                print('     这些图纸已不在校验目录里（多为改名残留）。确认彻底不需要回归'
                      '覆盖时，加 --prune 重跑即可删除。')

        # 排序 + indent=2 定稿：--update 的写入顺序受并行完成序影响，不排序会让
        #   每次补录产生大片"看起来变了其实没变"的 diff。
        out = {k: merged[k] for k in sorted(merged)}
        with open(BASELINE_PATH, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.write('\n')
        delta = len(out) - n_before
        delta_txt = f'{delta:+d}' if delta else '±0'
        print(f'\n✅ 基线已写入 {BASELINE_PATH}（{n_before} → {len(out)} 张，'
              f'{delta_txt}；耗时 {(time.time() - t_start) / 60:.1f} 分钟）')
        if err_kept:
            print(f'   ⚠️ 其中 {len(err_kept)} 张本次解析报错，已保留原基线条目未覆盖')
        _print_slowest()
        return

    if not os.path.exists(BASELINE_PATH):
        print(f'基线文件不存在: {BASELINE_PATH}\n先用 --update 生成基线')
        sys.exit(2)
    with open(BASELINE_PATH, encoding='utf-8') as f:
        baseline = json.load(f)

    n_same = n_diff = 0

    def _on_cmp(fn, cur, secs):
        """R127：逐张实时上屏——等 15 分钟一次性刷屏看不到任何进度。
        只处理成功结果；重试后仍报错的项交给下面循环补打。"""
        nonlocal n_same, n_diff
        if 'error' in cur:
            return
        verdict = classify_result(fn, cur, baseline)
        if verdict == 'new':
            print(f'[NEW]   {fn}  ({secs:.1f}s)（基线中无此图，用 --update 补录）')
            n_diff += 1
        elif verdict == 'same':
            n_same += 1
            print(f'[SAME]  {fn}  ({secs:.1f}s)')
        else:
            n_diff += 1
            print(f'[DIFF]  {fn}  ({secs:.1f}s)')
            print(f'    基线: {json.dumps(baseline[fn], ensure_ascii=False)}')
            print(f'    当前: {json.dumps(cur, ensure_ascii=False)}')

    for fn, cur in _run_parse(files, n_workers, on_result=_on_cmp,
                              avail_gb=avail_gb):
        if 'error' in cur:
            # R127 修正：error 项**也要跟基线比**。基线里本来就有两条「未检测到
            #   图框」的预期失败（一层.dwg / X-11号平面.dwg），一模一样地复现
            #   属于 SAME；旧写法无条件记成 DIFF，把「预期失败被稳定复现」误报
            #   成「本轮退化」，害得差点去查一个不存在的回归。
            verdict = classify_result(fn, cur, baseline)
            if verdict == 'same':
                n_same += 1
                print(f'[SAME]  {fn}（预期解析失败，与基线一致）')
            else:
                n_diff += 1
                print(f'[ERR]   {fn}: {cur["error"]}')
                if verdict == 'diff':
                    print(f'    基线: {json.dumps(baseline[fn], ensure_ascii=False)}')

    total = n_same + n_diff
    cache_tag = ''
    if _DXF_CACHE:
        cache_tag = (f'，DXF缓存 命中{cache_pre}'
                     f'/新增{_cached_n() - cache_pre}')
    print(f'\n=== 一致 {n_same} / 变化 {n_diff}（共 {total} 张，基线含 {len(baseline)} 张，'
          f'耗时 {(time.time() - t_start) / 60:.1f} 分钟{cache_tag}） ===')
    _print_slowest()
    sys.exit(0 if n_diff == 0 else 1)


def _run_parse(files, n_workers, on_result=None, avail_gb=None):
    """按并行度解析全部图纸，返回 [(文件名, 摘要), ...]（完成序）。

    R78 稳定性兜底：并行下大图资源竞争可能偶发 ODA/解析报错（error 摘要），
    自动对这些图串行重跑一次覆盖结果；重跑仍错则保留错误摘要如实上屏。

    R127：按负载分档调度
      · **负载分档**：用缓存 DXF 体积（≈实体数）而不是 DWG 体积做代理，见
        `_load_mb` 处的实测反例。三档各自按"档内最重那张"的峰值决定并行度，
        避免"重图峰值叠加把内存打穿"（实测 3 张重图并发：7.2GB → 0.6GB）。
      · **档内体积降序**提交：imap_unordered 是动态调度，先交重的能压掉长尾。
      · on_result(fn, summary, secs) 回调：每完成一张就通知一次，界面能实时
        看到进度，而不是等 15 分钟一次性刷屏；耗时汇总进 _TIMINGS。
      · n_workers=1 或 avail_gb 不可用时退化为全串行（保持旧行为可对照）。
    """
    def _consume(it):
        out = []
        for item in it:
            out.append(item)
            _TIMINGS[item[0]] = item[2]
            if on_result is not None:
                on_result(*item)
        return out

    def _batch(order, workers):
        global _WORKER_APP
        if workers <= 1:
            if _WORKER_APP is None:
                _WORKER_APP = load_app()
            return _consume(_worker_parse(fp) for fp in order)
        import multiprocessing as mp
        pool = mp.Pool(workers, initializer=_init_worker,
                       initargs=(not _DXF_CACHE,))
        try:
            return _consume(pool.imap_unordered(_worker_parse, order, chunksize=1))
        finally:
            pool.close()
            pool.join()

    if n_workers <= 1:
        batches = [(files, 1)]
    else:
        batches = _plan_batches(files, avail_gb if avail_gb is not None
                                else _avail_phys_gb(), n_workers)
        if len(batches) > 1:
            desc = ' → '.join(f'{len(b)} 张 @{w} 路' for b, w in batches)
            print(f'  分档调度：{desc}')

    raw = []
    for batch, workers in batches:
        raw += _batch(_submit_order(batch, workers), workers)

    results = [(fn, cur) for fn, cur, _secs in raw]
    err_fns = [fn for fn, cur in results if 'error' in cur]
    if err_fns:
        print(f'⚠️ {len(err_fns)} 张并行解析报错，串行重试: ' + ', '.join(err_fns))
        appmod = load_app()
        by_name = {os.path.basename(fp): fp for fp in files}
        fixed = []
        for fn, cur, _secs in raw:
            if fn in err_fns:
                _t0 = time.time()
                cur = parse_one(appmod, by_name[fn])
                _TIMINGS[fn] = round(time.time() - _t0, 2)
                if on_result is not None:
                    on_result(fn, cur, _TIMINGS[fn])   # 重试结果也要上屏
            fixed.append((fn, cur))
        results = fixed
    return results


def _print_slowest(top_n=8):
    """打印最慢的几张图——并行回归的墙钟时间由最慢的那张决定。"""
    if not _TIMINGS:
        return
    rows = sorted(_TIMINGS.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    if not rows or rows[0][1] < 1.0:
        return
    print(f'\n最慢 {len(rows)} 张（并行时单张就是长尾下限）:')
    for fn, secs in rows:
        print(f'    {secs:7.1f}s  {fn}')


if __name__ == '__main__':
    main()
