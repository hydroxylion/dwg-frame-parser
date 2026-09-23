import os
import re
import sys
import contextlib
import socket
import tempfile
import logging
import traceback
from logging.handlers import RotatingFileHandler
import ezdxf
from ezdxf import bbox as ezdxf_bbox
from ezdxf.addons import odafc
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from ezdxf.math import Vec2

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

# ---------- 日志 ----------
# 重要：Flask 的请求在工作线程中处理，在 PyCharm 控制台/后台管道等环境下，
# 工作线程内向 stdout 执行 print() 可能抛出 OSError [Errno 22] Invalid argument，
# 会直接把整个解析请求搞挂（解析本身其实是成功的）。
# 因此所有日志一律走 safe_log()：控制台打印失败不影响解析结果，同时写入 parser.log。
#
# 按大小轮转（RotatingFileHandler）：单文件超过 LOG_MAX_BYTES 即切分，最多保留
# LOG_BACKUP_COUNT 个历史文件（parser.log.1 / .2 / .3 / .4），磁盘占用上限
# ≈ 10MB × (1+4) = 50MB。
#   背景：排查密集期（每天上传十几张图纸）日志增长很快，2026-09-01~09-10 已累积
#   6.6MB 且从未轮转；一次批量回归（20 张 × 2 版本串行解析）就能写掉数 MB。
#   delay=True：启动时先不打开文件，首次写日志才创建，避免无谓占用文件句柄。
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'parser.log')
LOG_MAX_BYTES = 10 * 1024 * 1024   # 单文件上限 10MB
LOG_BACKUP_COUNT = 4               # 保留 4 个历史文件（parser.log.1 ~ .4）


def _archive_oversized_log():
    """把"已经超过阈值"的 parser.log 预归档为 .1，然后轮转历史文件序号。

    RotatingFileHandler 自身就能处理超限文件（首次写入即切分），但它只在"写日志
    那一刻"动作，结果是新日志先追加进那个巨大的旧文件、混在一起才切走。启动前
    先归档，可让新一轮运行从干净的 parser.log 开始，历史记录完整留在 .1。
      典型场景：切换到轮转机制时，parser.log 里已累积 6.6MB 旧记录（2026-09-01
      起），归档后新日志的排查体验更清爽。
      失败不抛异常：文件被其它进程占用（Flask 正在运行）时直接跳过，交给
      RotatingFileHandler 在写入时自行处理。
    """
    try:
        if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) < LOG_MAX_BYTES:
            return
        # 历史序号整体后移：.3 → .4、.2 → .3、.1 → .2，最旧的 .4 被覆盖丢弃
        for i in range(LOG_BACKUP_COUNT - 1, 0, -1):
            src, dst = f'{LOG_FILE}.{i}', f'{LOG_FILE}.{i + 1}'
            if os.path.exists(src):
                os.replace(src, dst)
        os.replace(LOG_FILE, f'{LOG_FILE}.1')
    except OSError:
        pass  # 被占用 / 权限不足：不阻断启动，交给 handler 在写入时切分


_archive_oversized_log()

logger = logging.getLogger('dwg-parser')
logger.setLevel(logging.INFO)
# 幂等：Flask debug 模式会用 reloader 重启进程、或模块被重复导入时，避免重复挂 handler
if not logger.handlers:
    _file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8',
        delay=True,
    )
    _file_handler.setFormatter(
        logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(_file_handler)
    # 不向 root logger 传播：否则 Flask/werkzeug 的 root handler（basicConfig 或
    # 默认 lastResort）会让同一条日志在控制台重复输出一遍
    logger.propagate = False


def safe_log(msg):
    """打印到控制台（失败不中断），并写入 parser.log 便于批量排查"""
    logger.info(msg)
    try:
        print(msg)
    except Exception:
        pass


# 日志分级开关：FRAME_PARSER_LOG_LEVEL=DEBUG 恢复逐候选明细（排查期用），
# 默认 INFO——逐候选明细不写文件也不刷控制台，parser.log 体积降约 80%
#   背景：统计 2026-09-11~17 的 parser.log，53361 行里约 80% 是"逐候选明细"
#   （每个候选一行尺寸/长宽比/面积占比 + 每次去重剔除一行），排查期临时加的，
#   日常运行价值低但把日志撑到 8MB+/6天。
LOG_LEVEL = os.environ.get('FRAME_PARSER_LOG_LEVEL', 'INFO').upper()


def log_debug(msg):
    """逐候选明细日志：DEBUG 级写文件；仅 DEBUG 模式下同时打印控制台"""
    logger.debug(msg)
    if LOG_LEVEL == 'DEBUG':
        try:
            print(msg)
        except Exception:
            pass


# ---------- ezdxf 内部告警路由 ----------
# 背景：'ezdxf' logger 默认无 handler，WARNING 级消息走 logging.lastResort
# 直接刷到控制台（sys.stderr）。典型如 DIMASSOC 关联标注：解析含关联标注的
# 图纸时，ezdxf 字典复制（dictionary.py 的 copy_linked_entities）对每个无法
# 复制的 DIMASSOC 对象发一条
#   "copy process ignored DIMASSOC(#xxxx) - this may cause problems in AutoCAD"
# 一张图刷 4~8 条，PyCharm 控制台被淹没。
#   实质：DIMASSOC 只是"标注与图形的关联关系"元数据，标注几何本身完整，
#   ezdxf 图框解析完全不用它——告警无害。
# 修法：把 'ezdxf' logger 挂上同一个文件 handler 并降级为 DEBUG——默认不写
# 文件、不刷控制台；FRAME_PARSER_LOG_LEVEL=DEBUG 时可入文件供排查。
class _EzdxfDemoteToDebugFilter(logging.Filter):
    """把 ezdxf logger 发来的记录降级为 DEBUG（不影响本项目自身日志）"""
    def filter(self, record):
        if record.name == 'ezdxf':
            record.levelno = logging.DEBUG
            record.levelname = 'DEBUG'
        return True


_file_handler.addFilter(_EzdxfDemoteToDebugFilter())
_ezdxf_logger = logging.getLogger('ezdxf')
_ezdxf_logger.addHandler(_file_handler)
_ezdxf_logger.propagate = False  # 不再传给 root → lastResort 不再刷控制台


def load_document(path):
    """加载 DXF/DWG 文档（尽量容错）：
    - DXF：直接用 ezdxf 读取（不再绕道 ODA 转换，更快），失败后用 recover 模式重试
    - DWG：通过 ODA File Converter 转换后读取，出错时翻译成可读的提示
    """
    if path.lower().endswith('.dxf'):
        try:
            return ezdxf.readfile(path)
        except Exception:
            # 损坏/非标准 DXF，用修复模式再试一次
            doc, _ = ezdxf.recover.readfile(path)
            return doc
    try:
        return odafc.readfile(path)
    except Exception as e:
        # 某些 DWG 普通转换产物会被截断（块记录表中途停止，缺 ENDSEC/EOF），
        # ezdxf 读入抛 DXFStructureError: missing ENDSEC tag（非圆减速器装配.dwg）。
        # ODA 带 audit=True 会先审计/修复源文件流再转换，产物完整可正常解析。
        # 兜底重试：audit 也失败时保留原错误往下走。
        try:
            return odafc.readfile(path, audit=True)
        except Exception:
            pass
        msg = str(e)
        if 'ODAFileConverter' in msg or 'Could not find' in msg:
            raise RuntimeError('未找到 ODA File Converter，无法解析 DWG 文件，请先安装 ODA File Converter')
        if 'UnsupportedVersion' in type(e).__name__ or 'unsupported DWG version' in msg:
            raise RuntimeError(f'DWG 版本不受支持: {msg}')
        # 其他转换错误（UnknownODAFCError 等），附上原始信息的前 200 字符便于定位
        raise RuntimeError(f'DWG 转换失败: {msg[:200]}')

# ---------- 辅助函数 ----------
def get_polyline_vertices(entity):
    """提取多段线顶点，并强制转换为 WCS 坐标（如果该实体定义了 OCS）"""
    vertices = []
    dxftype = entity.dxftype()
    # 获取 OCS 转换器（LWPOLYLINE 和 POLYLINE 都有 ocs() 方法）
    ocs = entity.ocs() if hasattr(entity, 'ocs') else None

    if dxftype == 'LWPOLYLINE':
        for point in entity.get_points():
            # point 是 OCS 坐标 (x, y, [z])
            p = Vec2(point[0], point[1]) if len(point) >= 2 else Vec2(point[0], 0)
            if ocs:
                p = ocs.to_wcs(p)
            vertices.append((p.x, p.y))
    elif dxftype == 'POLYLINE':
        for vertex in entity.vertices:
            loc = vertex.dxf.location
            if ocs:
                loc = ocs.to_wcs(loc)
            vertices.append((loc.x, loc.y))
    return vertices

def polygon_area(vertices):
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0

def is_polyline_closed(entity):
    """安全判断多段线是否闭合，兼容 LWPOLYLINE 和 POLYLINE"""
    if hasattr(entity, 'closed'):
        return entity.closed
    if entity.dxftype() == 'POLYLINE':
        return bool(entity.dxf.flags & 1)
    vertices = get_polyline_vertices(entity)
    return len(vertices) >= 3 and vertices[0] == vertices[-1]

def get_entity_bbox(entity, doc):
    """单个实体的包围盒 (x1, y1, x2, y2)，失败返回 None。
    注意：ezdxf 实体没有 bounding_box() 方法（旧代码在此静默失败导致
    面积占比分母恒为 0），必须用 ezdxf.bbox.extents 计算。"""
    try:
        bb = ezdxf_bbox.extents([entity], fast=True)
        if bb.has_data:
            return (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y)
    except Exception:
        pass
    return None

# ---------- 全局旋转矫正 ----------
# 场景：部分图纸在自定义 UCS 下绘制（整体绕 Z 旋转一个小角度），实体坐标存 WCS 时是斜的。
# 图框/墙线等本应水平/垂直的线会整体倾斜（总图-排水.dwg：UCS 旋转 3.3°，图框 297×420
# 存成 320.75×436.92 的轴对齐 bbox，矩形度 0.891 < 0.92 被收集逻辑误杀）。
# 处理：统计可见实体线段的主方向 θ（按线段长度加权，长边=结构线优先），若明显偏离
# 水平/垂直（> ROT_TRIGGER_DEG）且"主方向族+正交族"合计长度占比 ≥ ROT_DOMINANT_RATIO
# （整图主体确实共用一个旋转 UCS），后续收集坐标时先施加旋转矩阵把图纸"转正"，
# 使图框变为轴对齐，原有矩形度/直线矩形检测恢复有效。
# 注意：混合方向图纸不触发——如美立方总平图（图框轴对齐，但 ~56% 长度的块内容转 3.5°），
# 若按全图主峰旋转会把轴对齐图框转歪导致回归，需主体共旋门限保护。
ROT_BIN_COUNT = 180            # 角度直方图分桶（1°/桶），量化误差 ≤0.5°
ROT_TRIGGER_DEG = 1.5          # 主方向偏离最近 90° 倍数超过该角度才触发旋转矫正
ROT_MIN_SAMPLES = 6            # 参与角度统计的最少线段数（太少不可信，不旋转）
ROT_DOMINANT_RATIO = 0.65      # 主方向族(+正交 90°)合计长度占比 ≥ 此值才视为整图共旋


def _estimate_global_rotation(entities):
    """估计图纸整体主方向并返回旋转矩阵参数 (cosθ, sinθ)（把主方向转回水平/垂直）。
    参与线段：LINE 方向 + LWPOLYLINE 相邻边方向（无向角，归约到 [0, π)），按长度加权。
    判定（全部满足才旋转）：
      1. 主峰方向 θ 偏离最近水平/垂直（0/90° 倍数）> ROT_TRIGGER_DEG
      2. 主方向族（θ±1.5°）与其正交族（θ+90°±1.5°）的加权长度占比 ≥ ROT_DOMINANT_RATIO
         —— 避免"图框轴对齐 + 部分内容旋转"的混合图纸被误旋转
    否则返回 None（无需矫正）。
    """
    import math as _m
    _hist = [0.0] * ROT_BIN_COUNT
    _total_len = 0.0
    _seg_cnt = 0
    for _ent in entities:
        try:
            _t = _ent.dxftype()
            if _t == 'LINE':
                _p1, _p2 = _ent.dxf.start, _ent.dxf.end
                _segs = [((_p1.x, _p1.y), (_p2.x, _p2.y))]
            elif _t == 'LWPOLYLINE':
                _pts = list(_ent.get_points('xy'))
                _segs = [((a[0], a[1]), (b[0], b[1]))
                         for a, b in zip(_pts, _pts[1:] + _pts[:1])]
            else:
                continue
            for (_x1, _y1), (_x2, _y2) in _segs:
                _dx, _dy = _x2 - _x1, _y2 - _y1
                _len = _m.hypot(_dx, _dy)
                if _len < 1e-9:
                    continue
                _seg_cnt += 1
                _total_len += _len
                _a = _m.atan2(_dy, _dx) % _m.pi
                _hist[int(_a / _m.pi * ROT_BIN_COUNT) % ROT_BIN_COUNT] += _len
        except Exception:
            continue
    if _seg_cnt < ROT_MIN_SAMPLES or _total_len <= 0:
        return None
    _peak = max(range(ROT_BIN_COUNT), key=lambda i: _hist[i])
    _theta = (_peak + 0.5) / ROT_BIN_COUNT * _m.pi  # 峰值桶中心（度）
    # 主方向族：θ±ROT_TRIGGER_DEG 与其正交族 (θ+90°)±ROT_TRIGGER_DEG 的加权占比
    _win = int(ROT_TRIGGER_DEG)
    def _bin_w(_deg):
        # 把 [0,180) 内的度区间（可能跨 180 回绕）计入直方图
        _s = 0.0
        for _i in range(-_win, _win + 1):
            _b = int((_deg + _i) % 180)
            _s += _hist[_b]
        return _s
    _theta_deg = _peak + 0.5
    _fam = _bin_w(_theta_deg) + _bin_w((_theta_deg + 90.0) % 180.0)
    if _fam < _total_len * ROT_DOMINANT_RATIO:
        return None  # 主体未共旋（混合方向图纸），不旋转避免误伤轴对齐图框
    # 主方向偏离最近 90° 倍数（水平/垂直）的角度
    _nearest = round(_theta / (_m.pi / 2)) * (_m.pi / 2)
    if abs(_theta - _nearest) <= _m.radians(ROT_TRIGGER_DEG):
        return None
    return (_m.cos(_theta), _m.sin(_theta))  # 旋转矩阵参数，使主方向转回 0°

# ---------- 直线矩形检测（多矩形版） ----------
# 旧实现把一个空间里所有 LINE 的全局最外框当成唯一矩形：一个布局里画了 2 个图框时，
# 得到的是包住两者的外包络——一个不属于任何真实图框的错误尺寸。
# 新实现分三步：
#   1) 共线聚合：水平线按 y 聚类、垂直线按 x 聚类（容差 LINE_CLUSTER_EPS），每类记录端点跨度
#   2) 线对配对：横线对 × 纵线对 生成矩形候选，要求两组线的端点跨度互相覆盖
#      （即 4 条线真的围出一个区域）；聚类数少时全配对（可捕获嵌套图框外框），
#      聚类数多时（轴线网格密集）只配相邻对，避免组合爆炸
#   3) 有效性过滤：短边 ≥ MIN_LINE_RECT_SIDE，过滤家具/表格/标题栏等小矩形
LINE_CLUSTER_EPS = 0.5            # 近似共线容差（图形单位，mm 图纸即 0.5mm）
LINE_CLUSTER_MAX_FOR_ALL_PAIRS = 16  # 每个方向聚类数不超过该值时才做全配对
MIN_LINE_RECT_SIDE = 100          # 直线矩形最小短边（图形单位）
MAX_LINE_RECT_CANDIDATES = 5000   # 直线矩形候选数上限，防止异常图纸拖垮解析。
                                  # 坐标包含遍历在预算内处理的窗口远多于旧 gap 扫描，
                                  # 200 上限会在真图框组合到达前被表格/格栅小矩形占满
                                  # （电子称皮带输送系统：200 个时图框还没轮到，5000 仍
                                  #  远小于该类图纸可能检出的真实候选量；后续 is_frame_like
                                  #  的 √2/相对面积约束会过滤绝大多数小矩形）
LINE_PAIR_FULL_SPAN = 180         # 聚类合并跨度 ≥ 该值视为"图框级长边"，密集聚类时也做全配对
                                  # （机械图纸细节线可致聚类数百组，真实图框边界相隔几十组，
                                  #   仅相邻配对永远配对不到；长边全配对找回远距离图框边界）
                                  # 300→180：原 300 对"短边<300 的 LINE 图框"漏检——锁芯总装图
                                  # 280×195 图框竖边覆盖仅 194.8 < 300，进不了长配通道，只相邻
                                  # 配对隔 98 个聚类永远够不到 → 真图框漏识别、8 个 2.5×4 微孔
                                  # 反成候选。180 覆盖 A4 竖边 210/A3 297 等常用下限（A5 148 仍
                                  # 不入，A5 LINE 图框极罕见）。防组合爆炸由预过滤+比例剪枝+预算兜底。


def _merge_segments(segs, eps=LINE_CLUSTER_EPS):
    """合并重叠/相接触的线段列表（已排序），返回 [(lo, hi), ...]"""
    segs = sorted(segs)
    merged = []
    for lo, hi in segs:
        if merged and lo <= merged[-1][1] + eps:
            if hi > merged[-1][1]:
                merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return merged


def _coverage_contains(segments, lo, hi, eps=LINE_CLUSTER_EPS):
    """判断区间 [lo, hi] 是否被线段列表（已合并、有序）完全覆盖（允许 eps 误差）"""
    cur = lo
    for s_lo, s_hi in segments:
        if s_hi < cur - eps:
            continue
        if s_lo > cur + eps:
            return False
        if s_hi > cur:
            cur = s_hi
        if cur >= hi - eps:
            return True
    return cur >= hi - eps


def _intersect_segments(a, b):
    """两个已合并线段列表的交集"""
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _cluster_parallel_lines(lines):
    """把平行线按垂直坐标聚类。lines: [(coord, lo, hi)]，
    返回 [(coord, 合并后的线段列表)]。同一坐标上多段线保留为多段（不跨缝隙合并），
    用于后续覆盖判断识别"两图框并排时中间有缝隙"的场景。"""
    if not lines:
        return []
    lines = sorted(lines, key=lambda t: t[0])
    clusters = []
    cur_coord = lines[0][0]
    cur_segs = [ (lines[0][1], lines[0][2]) ]
    for coord, lo, hi in lines[1:]:
        if abs(coord - cur_coord) <= LINE_CLUSTER_EPS:
            cur_segs.append((lo, hi))
        else:
            clusters.append((cur_coord, _merge_segments(cur_segs)))
            cur_coord = coord
            cur_segs = [(lo, hi)]
    clusters.append((cur_coord, _merge_segments(cur_segs)))
    return clusters


def _has_full_side_line(lines, coord, lo, hi, eps=LINE_CLUSTER_EPS):
    """严格矩形判定（P1）：检查是否存在至少一条完整 LINE 覆盖指定侧边。
    lines: [(coord, lo, hi)] 原始线段列表
    coord: 目标坐标（如 ya/yb/xa/xb，即矩形边的位置）
    lo, hi: 需要覆盖的区间
    返回 True 当存在至少一条线段的单体跨度覆盖 [lo, hi]（允许 eps 误差）。
    这防止了"边线残段+内部标注线拼凑出假矩形"的情况。"""
    for c, l, h in lines:
        if abs(c - coord) <= eps and l <= lo + eps and h >= hi - eps:
            return True
    return False


def detect_rectangles_from_lines(entity_list, rot=None):
    """从 LINE 实体中检测多个矩形候选，返回 bbox 列表（面积降序）。

    判定规则：
    - 横线对 × 纵线对配对，两组线的线段覆盖范围必须互相覆盖对方围出的区间
      （4 条线真的围出一个闭合区域；两图框中间有缝隙时外包络会被覆盖判断否决）
    - P1 严格矩形判定：矩形 4 条边的每一侧都必须至少有一条完整 LINE 覆盖，
      不允许用边线残段+内部标注线拼凑
    - 矩形内部若存在整条穿越的分隔线（如两个并排图框的公共边），判定为
      拼合外包络，予以剔除，避免把"包住多个图框的大矩形"当成图框
    - 短边 ≥ MIN_LINE_RECT_SIDE，过滤家具/表格/标题栏等小矩形

    rot: 全局旋转矫正参数 (cosθ, sinθ)。图纸在自定义 UCS 下整体倾斜绘制时，
    线段端点先经该矩阵旋转回水平/垂直，使水平/垂直聚类恢复有效。
    """
    h_lines = []
    v_lines = []
    for ent in entity_list:
        if ent.dxftype() != 'LINE':
            continue
        # LINE 的 start/end 存储在 OCS 中（受 dxf.extrusion 影响，非 (0,0,1) 时与
        # WCS 不同），统一经 ocs().to_wcs() 转换到 WCS 后再参与聚类/覆盖判断，
        # 避免与 LWPOLYLINE 等 WCS 坐标实体比较错位（倾斜 extrusion 的图纸）。
        start = ent.dxf.start
        end = ent.dxf.end
        try:
            ocs = ent.ocs()
            start = ocs.to_wcs(start)
            end = ocs.to_wcs(end)
        except Exception:
            pass  # 无 extrusion / 转换失败时按原坐标处理
        if rot is not None:
            # 全局旋转矫正：把整体倾斜的图纸"转正"为水平/垂直
            from ezdxf.math import Vec2
            c, s = rot
            start = Vec2(start.x * c + start.y * s, -start.x * s + start.y * c)
            end = Vec2(end.x * c + end.y * s, -end.x * s + end.y * c)
        if abs(start.y - end.y) <= LINE_CLUSTER_EPS:
            # 近似水平线：按 y 聚类，记录 x 线段
            h_lines.append(((start.y + end.y) / 2.0,
                            min(start.x, end.x), max(start.x, end.x)))
        elif abs(start.x - end.x) <= LINE_CLUSTER_EPS:
            # 近似垂直线：按 x 聚类，记录 y 线段
            v_lines.append(((start.x + end.x) / 2.0,
                            min(start.y, end.y), max(start.y, end.y)))
    if len(h_lines) < 2 or len(v_lines) < 2:
        return []

    h_clusters = _cluster_parallel_lines(h_lines)
    v_clusters = _cluster_parallel_lines(v_lines)
    if len(h_clusters) < 2 or len(v_clusters) < 2:
        return []

    def make_pairs(clusters):
        """生成线对 (coord1, coord2, 公共覆盖线段)。
        公共覆盖 = 两条线各自线段覆盖的交集，为空则这对线围不出区域。

        双通道配对（防组合爆炸 + 找回远距离图框边界）：
        - 聚类数 ≤ MAX 时直接两两全配对（小图纸）
        - 聚类数多（密集网格/机械图细节线多）时：
          ① 相邻通道：仅配相邻聚类（原有逻辑，可捕获相邻小框）
          ② 长线全配通道：跨度 ≥ LINE_PAIR_FULL_SPAN 的"图框级长边"聚类两两全配对，
             覆盖相隔很多聚类才出现的真实图框左右边界
              （夹具装配图：水平 402 组/垂直 627 组远超 MAX，841×594 图框边界
               相隔几十个内部细节线聚类，仅相邻配对永远够不到 → 3 张 A1 图框全漏）

        预过滤：公共覆盖总长 < MIN_LINE_RECT_SIDE 的线对直接丢弃——公共覆盖短
        说明两线无法共同"横跨/纵跨"任何 ≥ 短边下限的区间，无论与哪个方向配对都
        不可能形成合法图框，保留只会浪费组合预算
        （长中苑202室：全图 6206 LINE → 横/纵聚类 810/1390 → h_pairs 2.2万×v_pairs
         15.9万，海量"超远装饰线对"公共覆盖仅数百，占满 20 万组合预算，封面
         28261×19985 的真实线对排在 #3752×#111168 永远轮不到 → 封面漏识别）
        """
        pairs = []
        n = len(clusters)
        if n < 2:
            return pairs

        def add_pair(i, j):
            c1, s1 = clusters[i]
            c2, s2 = clusters[j]
            common = _intersect_segments(s1, s2)
            if not common:
                return
            if sum(hi - lo for lo, hi in common) < MIN_LINE_RECT_SIDE:
                return  # 公共覆盖过短，不可能形成合法矩形，预过滤丢弃
            pairs.append((min(c1, c2), max(c1, c2), common))

        if n <= LINE_CLUSTER_MAX_FOR_ALL_PAIRS:
            # 小图纸：全配对
            for i in range(n):
                for j in range(i + 1, n):
                    add_pair(i, j)
        else:
            # 通道①：相邻配对（原有逻辑）
            for i in range(n - 1):
                add_pair(i, i + 1)
            # 通道②：图框级长边聚类全配对
            long_idx = [i for i in range(n)
                        if clusters[i][1][-1][1] - clusters[i][1][0][0] >= LINE_PAIR_FULL_SPAN]
            for a in range(len(long_idx)):
                for b in range(a + 1, len(long_idx)):
                    add_pair(long_idx[a], long_idx[b])
        # 按坐标去重（相邻通道与长线全配通道可能产生同一线对）
        uniq = {}
        for p in pairs:
            uniq.setdefault((p[0], p[1]), p)
        return list(uniq.values())

    h_pairs = make_pairs(h_clusters)  # (ya, yb, 两条横线的公共 x 覆盖)
    v_pairs = make_pairs(v_clusters)  # (xa, xb, 两条纵线的公共 y 覆盖)
    if not h_pairs or not v_pairs:
        return []

    def has_internal_divider(x1, y1, x2, y2):
        """矩形内部是否存在整条穿越的分隔线（说明该矩形是多个图框的拼合外包络）"""
        for cx, segs in v_clusters:
            if x1 + LINE_CLUSTER_EPS < cx < x2 - LINE_CLUSTER_EPS:
                if _coverage_contains(segs, y1, y2):
                    return True
        for cy, segs in h_clusters:
            if y1 + LINE_CLUSTER_EPS < cy < y2 - LINE_CLUSTER_EPS:
                if _coverage_contains(segs, x1, x2):
                    return True
        return False

    rects = []
    seen = set()
    examined = 0
    MAX_EXAMINED = 2000000  # 组合数上限，防止异常图纸（超密集网格）拖垮解析
    # 排序键：先按「公共覆盖总长」降序，再按「平行线间距」降序。
    # 公共覆盖长度代表这对线能撑起的矩形另一方向的最大跨度——真实图框的边线
    # 覆盖长（长中苑封面横线对公共覆盖 28261、竖线对 19985），而密集图纸里海量
    # "超远装饰线对"间距虽大但公共覆盖只有几百，按覆盖排序会沉底，不再抢占预算。
    # 次级键间距降序保留夹具装配图的修复（图框远边界对优先于坐标相邻对）。
    def _pair_order_key(p):
        return (sum(hi - lo for lo, hi in p[2]), p[1] - p[0])
    h_pairs.sort(key=_pair_order_key, reverse=True)
    # v_pairs 按「x 起点」升序建立索引，每个 h_pair 只遍历 x 起点落在其公共覆盖
    # 段内的竖线对（xa∈[xLo,xHi] 且 xb≤xHi）。这样：
    #   - 真矩形两侧竖对的 xa 必然等于某条横线的公共覆盖段起点（图框左边界），
    #     按 xa 升序遍历时边界对排最前，命中即确认，不需要扫完整个 gap 区间；
    #   - 大量"x 范围根本不在本横线公共覆盖下"的竖线对（机械图内部细节线、
    #     其它区域的长线）被坐标直接排除，不再逐个白试。
    # 背景：电子称皮带输送系统（套图）横/纵聚类 901/1441 → h_pairs 29905 ×
    # v_pairs 50497。旧方案按竖对 gap 升序全窗口扫描，前 116 个 h_pair 就把
    # 200 万组合预算耗尽（实际只在扫"x 对不上的竖对"），真图框横线对排在
    # #414 永远轮不到 → 6 张 LINE 页框（21475×30372/29788×21062/14894×10531）
    # 全漏。坐标包含遍历后全部图框在 examined≈80 万内即被找到。
    import bisect as _bisect
    PAIR_RATIO_BOUND = 8.0
    v_by_xa = sorted(v_pairs, key=lambda p: p[0])
    v_xas = [p[0] for p in v_by_xa]
    # 竖线对公共覆盖总长预计算：覆盖总长 < 矩形高的竖对必然纵跨不住 [ya,yb]，
    # 是必要条件剪枝（地下室电力t3：横/竖长边聚类 8164/4931 个 → 长边全配
    # 产生 109 万×51 万线对，84100×59400 图框的线对虽排覆盖降序 #569，
    # 但此前 570 万次无效检查把 200 万组合预算耗尽在 ~#200 → 16 张 LINE
    # 图框全漏。该剪枝把目标前的检查量从 572 万降到 41.5 万，零召回损失）
    v_covs = [sum(hi - lo for lo, hi in p[2]) for p in v_by_xa]
    for (ya, yb, h_common) in h_pairs:
        _H = yb - ya
        if _H < MIN_LINE_RECT_SIDE:
            continue  # 矩形高 < 短边下限：任何组合短边必 < 下限，整层跳过
        _g_lo = max(MIN_LINE_RECT_SIDE, _H / PAIR_RATIO_BOUND)
        _g_hi = _H * PAIR_RATIO_BOUND
        for (xLo, xHi) in h_common:
            _seg_w = xHi - xLo
            if _seg_w < MIN_LINE_RECT_SIDE:
                continue
            _lo_i = _bisect.bisect_left(v_xas, xLo - LINE_CLUSTER_EPS)
            _hi_i = _bisect.bisect_right(v_xas, xHi + LINE_CLUSTER_EPS)
            for _vi in range(_lo_i, _hi_i):
                xa, xb, v_common = v_by_xa[_vi]
                _gap = xb - xa
                # 宽高比例剪枝：图框归一化长宽比上限约 5.5（FRAME_RATIO_MAX），
                # 剪枝放宽到 8，超出比例的横竖线对不可能是图框，跳过不损失召回
                if _gap < _g_lo or _gap > _g_hi:
                    continue
                if xb > xHi + LINE_CLUSTER_EPS:
                    continue  # 竖对右界超出本覆盖段：横线盖不住，必非矩形
                if v_covs[_vi] < _H - 2.0:
                    continue  # 竖对公共覆盖总长 < 矩形高(留2mm浮点/聚类容差)：必然纵跨不住，
                              # 不计预算直接跳过。容差不可省——横对高度与竖对覆盖在端点
                              # 重合时仅有 ~1e-8 级浮点尾差，严格比较会把真图框误杀
                              # （地下室电力t3 TB 层 126100×59400 即因此消失）
                examined += 1
                if examined > MAX_EXAMINED:
                    rects.sort(key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)
                    return rects
                # 覆盖校验：横线必须横跨 [xa, xb]，纵线必须纵跨 [ya, yb]
                if not _coverage_contains(h_common, xa, xb):
                    continue
                if not _coverage_contains(v_common, ya, yb):
                    continue
                # P1 严格矩形判定：4 条边的每一侧都必须至少有一条完整 LINE 覆盖
                # 防止"边线残段+内部标注线拼凑出假矩形"
                if not _has_full_side_line(h_lines, ya, xa, xb):
                    continue
                if not _has_full_side_line(h_lines, yb, xa, xb):
                    continue
                if not _has_full_side_line(v_lines, xa, ya, yb):
                    continue
                if not _has_full_side_line(v_lines, xb, ya, yb):
                    continue
                w = xb - xa
                h = yb - ya
                if min(w, h) < MIN_LINE_RECT_SIDE:
                    continue
                if has_internal_divider(xa, ya, xb, yb):
                    continue
                key = (round(xa, 3), round(ya, 3), round(xb, 3), round(yb, 3))
                if key in seen:
                    continue
                seen.add(key)
                rects.append((xa, ya, xb, yb))
                if len(rects) >= MAX_LINE_RECT_CANDIDATES:
                    break
            if len(rects) >= MAX_LINE_RECT_CANDIDATES:
                break
        if len(rects) >= MAX_LINE_RECT_CANDIDATES:
            break
    rects.sort(key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)
    return rects


# ---------- 标题栏条纹检测（条件G：同模板缩放套图救援 用，2026-09-14） ----------
def _merge_stripe_intervals(intervals, gap=15.0):
    """合并 y 区间（同一条竖线被文字/洞口打断成多段时拼回覆盖范围）"""
    if not intervals:
        return []
    intervals = sorted(intervals)
    out = [list(intervals[0])]
    for lo, hi in intervals[1:]:
        if lo <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def has_title_stripe(h_segs, v_segs, rect):
    """检测矩形的「标题栏条纹」结构签名。

    场景（胜利公寓２ 4→6）：6 个图框是同一模板按 1×/1:2.5/1:4/1:4.46 四种出图
    比例排版，长宽比全部精确等于 1.4257。小尺寸实例 rel 6.25%/5.02% 过不了条件C
    （分母是最大的图框）、短边 4613/4133 超条件B 上限 2000、尺寸互不相同条件D
    也不适用 → 全通道否决漏检。但 6 个框共享同一模板结构：框右侧有一条竖向
    标题栏条纹（两条全高竖线围出带宽 ≈11%×框宽，带内横向分隔线 ≥2 条）。

    判据（对矩形左右两侧各检查一遍）：
      - 侧边 25% 区域内，覆盖 ≥80% 框高且两端触及框边的竖线（合并断段后统计，
        框边界本身也计为候选边）≥2 条，相邻两条围出带宽 ∈ [5%, 25%]×框宽；
      - 带内横向分隔线 ≥2 条（线段横跨带宽 ≥60%，允许略超出带边 10 单位——
        分隔线常延伸到框边界）。
    decoy 校验（胜利公寓２ 实测 10 个房间/构件矩形全部不命中）：
      房间右缘的柜/架类符号也会围出窄竖带，但带宽仅 ≈2.4%×框宽，被 5% 下限滤除。

    h_segs/v_segs：[(lo, hi, y/x), ...] 已转正（全局旋转矫正后）的水平/垂直线段
    rect：(x1, y1, x2, y2)
    """
    x1, y1, x2, y2 = rect[0], rect[1], rect[2], rect[3]
    W, H = x2 - x1, y2 - y1
    if W <= 0 or H <= 0:
        return False
    for side in ('right', 'left'):
        if side == 'right':
            z0, z1 = x2 - W * 0.25, x2
            edges = {x2}
        else:
            z0, z1 = x1, x1 + W * 0.25
            edges = {x1}
        # 带内垂直线按 x 聚类，合并断段后要求"全高柱"（覆盖 ≥80% 且触及两端）
        vcl = {}
        for lo, hi, x in v_segs:
            if z0 - 5 <= x <= z1 + 5 and lo >= y1 - 5 and hi <= y2 + 5:
                vcl.setdefault(round(x, 0), []).append((lo, hi))
        for xk, ivs in vcl.items():
            m = _merge_stripe_intervals(ivs)
            total = sum(hi - lo for lo, hi in m)
            if (total >= H * 0.8 and m[0][0] <= y1 + H * 0.06
                    and m[-1][1] >= y2 - H * 0.06):
                edges.add(xk)
        xs = sorted(edges)
        for i in range(len(xs) - 1):
            xa, xb = xs[i], xs[i + 1]
            bw = xb - xa
            if not (W * 0.05 <= bw <= W * 0.25):
                continue
            divs = set()
            for lo, hi, y in h_segs:
                if y1 - 2 <= y <= y2 + 2 and xa - 10 <= lo and hi <= xb + 10 \
                        and (hi - lo) >= bw * 0.6:
                    divs.add(round(y / 10))
            if len(divs) >= 2:
                return True
    return False

# ---------- 图框特征判定 ----------
# 归一化长宽比：横版/竖版统一用 长/短边 表示，避免竖版图框（如 841x1189）被误杀
# 合理区间 [1.05, 5.5]：
#   - 下限 1.05：覆盖特例 841x891（≈1.06），排除接近正方形的随机边界
#   - 上限 5.5：覆盖最长标准加长 A1+5/2（2944/594 ≈ 4.95），排除极端细长条
FRAME_RATIO_MIN = 1.05
FRAME_RATIO_MAX = 5.5

def normalized_ratio(width, height):
    if width <= 0 or height <= 0:
        return float('inf')
    return max(width, height) / min(width, height)


# ---------- 第23轮：图签附栏外扩合并（105100口径，2026-09-17） ----------
# 场景（地下室电力t3.dwg）：84100×59400 主图框 + 左侧 21000 图签附栏应按 105100×59400
# 整体输出（用户实测口径）。附栏在该模板中不形成独立闭合边界——J-图框/TK 层的图签条
#（约 7000×18220 表格）画在主图框边线内侧右缘，而"左侧邻位"看到的是错排布局中
# 相邻同排图纸的图签条。8 张主框左侧 2000~22000mm 邻位存在"同排同规格图纸内部的
# 图签条"→ 外扩 21000 后与 3 张已按 105100 闭合检出的框合计 11 张，与用户实测一致。
# 守卫（防其他图纸误扩）：
#   a) 仅 主图框级 尺寸参与（短边 ≥50000 且宽 <95000——已含附栏口径的不再扩）；
#   b) 图签条中心距主框边 2000~22000（附栏宽度量级；更远的是邻框自身结构）；
#   c) 图签条与主框纵向重叠 ≥40% 条高（错排容差）；
#   d) 图签条宿主框（条中心所在候选）高度 ≥0.85×主框高度（同排同规格；
#      宿主是 42000 小页等不同规格 → 是小页拼排不是附栏）；
#   e) 图签条宿主 ≠ 主框自身（自身内侧条是本框图签，不是外扩依据）。
TITLE_STRIP_LAYERS = ('J-图框', 'TK')
TITLE_STRIP_MERGE_WIDTH = 21000.0   # 附栏标准宽度（外扩量）
TITLE_STRIP_BAND_NEAR = 2000.0      # 图签条中心距主框边下限
TITLE_STRIP_BAND_FAR = 22000.0      # 图签条中心距主框边上限（≈附栏宽度）
TITLE_STRIP_SHORT_RANGE = (4000.0, 13000.0)  # 图签条短边范围
TITLE_STRIP_MIN_LONG = 14000.0      # 图签条长边下限
TITLE_STRIP_MAIN_MIN_SIDE = 50000.0 # 可带附栏的主图框短边下限
TITLE_STRIP_MAX_W = 95000.0         # 宽度达此值视为已含附栏口径
TITLE_STRIP_OWNER_H = 0.85          # 宿主框高度 / 主框高度 下限


def _cluster_title_boxes(boxes, gap=2000.0):
    """把图签层实体 bbox 聚成簇（间隙 ≤gap 合并，迭代至稳定）"""
    clusters = []
    for x1, y1, x2, y2 in boxes:
        for c in clusters:
            if not (x2 < c[0] - gap or x1 > c[2] + gap or
                    y2 < c[1] - gap or y1 > c[3] + gap):
                c[0] = min(c[0], x1); c[1] = min(c[1], y1)
                c[2] = max(c[2], x2); c[3] = max(c[3], y2)
                break
        else:
            clusters.append([x1, y1, x2, y2])
    while True:
        merged = []
        changed = False
        for c in clusters:
            for o in merged:
                if not (c[2] < o[0] - gap or c[0] > o[2] + gap or
                        c[3] < o[1] - gap or c[1] > o[3] + gap):
                    o[0] = min(o[0], c[0]); o[1] = min(o[1], c[1])
                    o[2] = max(o[2], c[2]); o[3] = max(o[3], c[3])
                    changed = True
                    break
            else:
                merged.append(list(c))
        clusters = merged
        if not changed:
            break
    return clusters


def _filter_title_strips(clusters):
    """从簇中筛选「图签条」尺寸：短边 4000~13000 且长边 ≥14000 的表格条"""
    strips = []
    for c in clusters:
        w = c[2] - c[0]
        h = c[3] - c[1]
        lo, hi = min(w, h), max(w, h)
        if (TITLE_STRIP_SHORT_RANGE[0] <= lo <= TITLE_STRIP_SHORT_RANGE[1]
                and hi >= TITLE_STRIP_MIN_LONG):
            strips.append((c[0], c[1], c[2], c[3]))
    return strips


def _expand_frames_with_title_strips(frame_like, strips, layout_name='模型空间'):
    """主图框 + 左侧图签附栏 外扩合并。返回 (更新后的候选列表, 外扩数)。
    frame_like: 候选 dict 列表（含 bbox/width/height/area/ratio/layout）
    strips:     图签条 bbox 列表 [(x1,y1,x2,y2), ...]"""
    if not frame_like or not strips:
        return frame_like, 0
    if os.environ.get('FRAME_PARSER_NO_TITLE_STRIP') == '1':
        return frame_like, 0
    model_cands = [c for c in frame_like if c.get('layout') == layout_name]
    if not model_cands:
        return frame_like, 0

    def _owner(scx, scy, exclude):
        """图签条中心所在的候选框（宿主），取面积最大者"""
        best = None
        for g in model_cands:
            if g is exclude:
                continue
            gx1, gy1, gx2, gy2 = g['bbox']
            if gx1 - 500 <= scx <= gx2 + 500 and gy1 - 500 <= scy <= gy2 + 500:
                a = (gx2 - gx1) * (gy2 - gy1)
                if best is None or a > best[0]:
                    best = (a, g)
        return best[1] if best else None

    expanded = 0
    for c in model_cands:
        x1, y1, x2, y2 = c['bbox']
        w = x2 - x1
        h = y2 - y1
        if min(w, h) < TITLE_STRIP_MAIN_MIN_SIDE or w >= TITLE_STRIP_MAX_W:
            continue  # 小页/竖版窄框不参与；已达附栏口径的不再扩
        if w <= h:
            continue  # 仅横版主图框参与附栏口径（竖版图纸图签条在左内侧，不外扩）
        for side in ('left', 'right'):
            triggered = None
            for (sx1, sy1, sx2, sy2) in strips:
                scx = (sx1 + sx2) / 2.0
                scy = (sy1 + sy2) / 2.0
                dist = (x1 - scx) if side == 'left' else (scx - x2)
                if not (TITLE_STRIP_BAND_NEAR <= dist <= TITLE_STRIP_BAND_FAR):
                    continue  # b) 图签条中心须在主框边外侧 2000~22000
                ovy = min(sy2, y2) - max(sy1, y1)
                if ovy < 0.4 * (sy2 - sy1):
                    continue  # c) 纵向重叠 ≥40% 条高（错排容差）
                g = _owner(scx, scy, exclude=c)
                if g is None:
                    continue  # e) 宿主缺失（游离条）不作为外扩依据
                gh = g['bbox'][3] - g['bbox'][1]
                if gh < TITLE_STRIP_OWNER_H * h:
                    continue  # d) 宿主不是同排同规格图纸
                triggered = (side, scx, scy, gh)
                break
            if triggered:
                side, scx, scy, gh = triggered
                if side == 'left':
                    x1 -= TITLE_STRIP_MERGE_WIDTH
                else:
                    x2 += TITLE_STRIP_MERGE_WIDTH
                w = x2 - x1
                c['bbox'] = (x1, y1, x2, y2)
                c['width'] = w
                c['height'] = h
                c['area'] = w * h
                c['ratio'] = normalized_ratio(w, h)
                expanded += 1
                log_debug(f"  [附栏外扩] {side} 侧 +{TITLE_STRIP_MERGE_WIDTH:.0f} → "
                          f"{w:.0f}x{h:.0f}（图签条中心@({scx:.0f},{scy:.0f})，宿主高 {gh:.0f}）")
                break  # 每框只外扩一侧
    return frame_like, expanded


def _collect_title_strips(doc):
    """从模型空间收集 J-图框/TK 层图签条 bbox 列表"""
    if doc is None:
        return []
    try:
        msp = doc.modelspace()
    except Exception:
        return []
    boxes = []
    for e in msp:
        try:
            if e.dxf.layer not in TITLE_STRIP_LAYERS:
                continue
            bb = get_entity_bbox(e, doc)
        except Exception:
            continue
        if bb is not None:
            boxes.append(bb)
    if not boxes:
        return []
    return _filter_title_strips(_cluster_title_boxes(boxes))


def calculate_layout_total_bbox(layout, doc):
    """计算单个 layout 中所有实体的总包围盒面积。
    面积占比的分母必须与候选同源（同一 layout）：
    模型空间按 1:100 出图时几何巨大，若拿它当布局空间图框的分母会把图框误杀。
    优先用 ezdxf.bbox.extents 批量计算（快），失败时逐实体兜底。
    """
    # 快路径：整个 layout 一次性计算（fast 模式只取控制点，大图纸也可接受）
    try:
        bb = ezdxf_bbox.extents(layout, fast=True)
        if bb.has_data:
            return (bb.extmax.x - bb.extmin.x) * (bb.extmax.y - bb.extmin.y)
    except Exception:
        pass
    # 慢路径兜底：逐实体计算
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    found = False
    for entity in layout:
        bbox = get_entity_bbox(entity, doc)
        if bbox:
            x1, y1, x2, y2 = bbox
            min_x = min(min_x, x1, x2)
            min_y = min(min_y, y1, y2)
            max_x = max(max_x, x1, x2)
            max_y = max(max_y, y1, y2)
            found = True
    if found:
        return (max_x - min_x) * (max_y - min_y)
    return 0

# ---------- XREF 外部参照检测（方案B：只打警告，不加载） ----------
def _detect_xref_warnings(doc):
    """检测图纸是否使用了 XREF 外部参照（方案B：只打警告，不加载外部文件）。

    背景：XREF 块定义指向外部 DWG 文件，ezdxf 默认不加载外部文件，
    块定义内实体列表为空——引用这些块的 INSERT 实体在
    _get_block_world_bbox 返回 None 时被跳过，会漏识别图框。

    本函数只做检测 + 警告，不阻塞主流程：
    - 没用 XREF：返回空列表，无任何副作用（绝大多数图纸）
    - 用了 XREF：返回警告列表，主函数写日志 + 返回字段，前端可选展示

    返回：[{'block_name', 'xref_path', 'insert_count', 'layouts'}]
          layouts: {layout_name: count} 各 layout 引用次数
    """
    # 1. 收集所有"实体列表为空"的 XREF 块定义
    #    只警告空实体的：已加载的 XREF（entity_count > 0）说明外部文件
    #    已被 ezdxf 解析，_get_block_world_bbox 能算出 bbox，无需警告。
    xref_blocks = {}  # block_name -> xref_path
    for blk in doc.blocks:
        try:
            name = blk.name
            is_xref = getattr(blk, 'is_xref', False)
            xref_path = getattr(blk.dxf, 'xref', None) if hasattr(blk, 'dxf') else None
            # XREF 块的常见特征：is_xref=True，或 name 含路径分隔符，或 xref_path 不为空
            looks_xref = bool(is_xref) or bool(xref_path) or ('\\' in name) or ('/' in name)
            if not looks_xref:
                continue
            ent_count = sum(1 for _ in blk)
            if ent_count == 0:
                xref_blocks[name] = xref_path or name
        except Exception:
            continue

    if not xref_blocks:
        return []

    # 2. 扫描模型空间 + 布局空间，统计引用 XREF 块的 INSERT 实例数
    refs_by_layout = {}  # block_name -> {layout_name: count}

    def scan_layout(layout, layout_name):
        for ent in layout:
            try:
                if ent.dxftype() != 'INSERT':
                    continue
                bn = ent.dxf.name
                if bn in xref_blocks:
                    refs_by_layout.setdefault(bn, {}).setdefault(layout_name, 0)
                    refs_by_layout[bn][layout_name] += 1
            except Exception:
                continue

    scan_layout(doc.modelspace(), '模型空间')
    for layout in doc.layouts:
        if layout.name == 'Model':
            continue
        scan_layout(layout, f'布局 "{layout.name}"')

    # 3. 只保留实际被引用的 XREF（块定义存在但未引用的不会漏识别图框）
    warnings = []
    for bn, layouts in refs_by_layout.items():
        total = sum(layouts.values())
        warnings.append({
            'block_name': bn,
            'xref_path': xref_blocks.get(bn, ''),
            'insert_count': total,
            'layouts': layouts,
        })
    return warnings

# ---------- 统一扫描函数 ----------
def collect_candidates_from_layout(layout, doc, layout_name):
    candidates = []
    # 跳过"CAD 里不可见但 ezdxf 仍读取"的实体，两层过滤：
    #   ① 图层级：隐藏(off)/冻结(frozen)图层的实体。常见噪声如"防火分区""面积""厨房荷载"等
    #     辅助图层常被冻结，其闭合多段线会被误识别为图框候选（雅安图 102→12 后多出的 3 个
    #     非标候选即源于此）。注意：锁定(locked)图层不影响可见性，不过滤。
    #   ② 实体级：invisible 标志（DXF 组码 60=1）。单个实体被标记为不可见，图层正常显示但
    #     该实体在 CAD 里不画出，ezdxf 仍会读取。设计师可能把图框边线设成 invisible 做参考线，
    #     会被直线矩形检测拼出"看不见的图框"。getattr 默认 0（可见），兼容无此属性的实体类型。
    invisible_layers = set()
    for layer in doc.layers:
        if not layer.is_on() or layer.is_frozen():
            invisible_layers.add(layer.dxf.name)
    all_entities = list(layout)
    layer_filtered = [e for e in all_entities if e.dxf.layer not in invisible_layers]
    visible_entities = [e for e in layer_filtered if getattr(e.dxf, 'invisible', 0) != 1]
    skipped_layer = len(all_entities) - len(layer_filtered)
    skipped_invisible = len(layer_filtered) - len(visible_entities)
    if skipped_layer or skipped_invisible:
        parts = []
        if skipped_layer:
            parts.append(f"隐藏/冻结图层实体 {skipped_layer} 个")
        if skipped_invisible:
            parts.append(f"实体级 invisible {skipped_invisible} 个")
        log_parts = [f"跳过 {'，'.join(parts)}", f"可见实体 {len(visible_entities)} 个"]
        if invisible_layers:
            log_parts.append(f"冻结图层: {sorted(invisible_layers)}")
        safe_log(f"  [{layout_name}] " + " | ".join(log_parts))
    # 全局旋转矫正：图纸在自定义 UCS 下整体倾斜绘制（实体坐标斜存 WCS）时，
    # 先估计主方向并"转正"，使图框变为轴对齐，矩形度/水平垂直检测恢复有效。
    _rot = _estimate_global_rotation(visible_entities)
    if _rot:
        safe_log(f"  [{layout_name}] 检测到整体倾斜图纸，施加旋转矫正 (cos={_rot[0]:.4f}, sin={_rot[1]:.4f})")

    def _rot_pt(x, y):
        """施加全局旋转（rot 为 None 时原样返回）"""
        if _rot is None:
            return (x, y)
        return (x * _rot[0] + y * _rot[1], -x * _rot[1] + y * _rot[0])

    # 1. 闭合多段线（加矩形度过滤：bbox面积/多边形面积 > 阈值才算候选）
    #    L 形标题栏、T 形会签栏等非矩形闭合线的矩形度远低于 1.0，
    #    真正的图框边线（矩形或近矩形）矩形度 > 0.95。
    RECTANGULARITY_THRESHOLD = 0.92
    seen_bbox = set()  # 同一 layout 内 bbox 去重：同一位置画两遍的闭合多段线只保留 1 个候选
    for entity in visible_entities:
        dxftype = entity.dxftype()
        if dxftype in ('LWPOLYLINE', 'POLYLINE'):
            if is_polyline_closed(entity):
                vertices = get_polyline_vertices(entity)
                if len(vertices) >= 3:
                    if _rot is not None:
                        # 全局旋转矫正：倾斜图纸先"转正"，矩形度/宽高才反映真实图框
                        vertices = [_rot_pt(p[0], p[1]) for p in vertices]
                    area = polygon_area(vertices)
                    if area > 0:
                        xs = [p[0] for p in vertices]
                        ys = [p[1] for p in vertices]
                        bbox = (min(xs), min(ys), max(xs), max(ys))
                        bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                        # 矩形度 = 多边形面积 / 其 bbox 面积，越接近 1.0 越像矩形
                        if bbox_area > 0:
                            rectangularity = area / bbox_area
                        else:
                            rectangularity = 0
                        if rectangularity >= RECTANGULARITY_THRESHOLD:
                            key = (round(bbox[0], 3), round(bbox[1], 3),
                                   round(bbox[2], 3), round(bbox[3], 3))
                            if key in seen_bbox:
                                continue
                            seen_bbox.add(key)
                            candidates.append({
                                'type': '闭合多段线',
                                'area': area,
                                'bbox': bbox,
                                'width': bbox[2] - bbox[0],
                                'height': bbox[3] - bbox[1],
                                'layout': layout_name,
                                'rectangularity': rectangularity,
                            })
    # 1.5. 块参照 INSERT：插入点 + 块定义 bbox × xscale/yscale(+ rotation) = 真实图框 bbox。
    #    建筑图纸图框常用 INSERT 块参照插入（澜山"12fas" 块 ×15 个，每块缩放 1.0714 后真实
    #    31500×22275mm），原代码只处理 LWPOLYLINE+LINE 会全部漏识别。
    #    策略分两步——先尺寸预筛"图框级"块入库，再位置启发判别对齐装饰块：
    #    (1) 预筛：短边 ≥ INSERT_MIN_SHORT_SIDE、长宽比 ∈ [FRAME_RATIO_MIN, FRAME_RATIO_MAX]，
    #        家具/符号类小块（短边通常 < 500mm）直接不入库，减少后续位置启发式数据量；
    #    (2) 位置启发：见下方"对齐排列剔除"，在预筛通过的候选上做。
    INSERT_MIN_SHORT_SIDE = 500  # mm：真建筑图框短边几乎都 ≥ 420mm（A2），家具/符号块通常 < 500
    # 标准 A 系列短边集合：A0~A5 的短边。短边命中该集合（且长宽比合规）的 INSERT 视作
    # "A 系图框块"放行入库——小图幅用块画图框很常见（支座.dwg 的 TILED_BLOCK 210×297=A4
    # 竖版，短边 210<500 曾被误当家具块剔除 → 图框漏识别、全实体包围盒把外溢的"技术要求"
    # MTEXT 撑到 225 → 输出 225×297 而非 210×297）。家具/门窗符号块短边一般不在 A 系序列。
    _A_SERIES_SHORT_SIDES = (841, 594, 420, 297, 210, 148, 105)
    import math as _math
    _block_bbox_cache = {}
    def _get_block_world_bbox(_name, _visited=None):
        """递归求块的 bbox（块定义内坐标，未应用 INSERT xscale/yscale）。"""
        if _name in _block_bbox_cache:
            return _block_bbox_cache[_name]
        if _visited is None:
            _visited = set()
        if _name in _visited:
            return None
        if _name not in doc.blocks:
            return None
        _visited.add(_name)
        _pts = []
        for _ent in doc.blocks[_name]:
            try:
                _t = _ent.dxftype()
                if _t == 'LWPOLYLINE':
                    for _p in _ent.get_points('xy'):
                        _pts.append(_p)
                elif _t == 'LINE':
                    _pts.append((_ent.dxf.start.x, _ent.dxf.start.y))
                    _pts.append((_ent.dxf.end.x, _ent.dxf.end.y))
                elif _t == 'CIRCLE':
                    _c = (_ent.dxf.center.x, _ent.dxf.center.y)
                    _r = _ent.dxf.radius
                    _pts.append((_c[0]-_r, _c[1]-_r))
                    _pts.append((_c[0]+_r, _c[1]+_r))
                elif _t == 'INSERT':
                    # 嵌套 INSERT：按 DXF 变换规范组合 p_world = insert + R(rot)·S·(p − base)。
                    # 旧实现既不减子块基点也不处理子块旋转——子块带旋转时父块 bbox 直接算错。
                    _bb = _get_block_world_bbox(_ent.dxf.name, _visited)
                    if _bb is not None:
                        _ipt = (_ent.dxf.insert.x, _ent.dxf.insert.y)
                        _xs = _ent.dxf.xscale; _ys = _ent.dxf.yscale
                        try:
                            _nb = doc.blocks[_ent.dxf.name].block.dxf.base_point
                            _nbx, _nby = _nb.x, _nb.y
                        except Exception:
                            _nbx = _nby = 0.0
                        _nc = [(_bb[0]-_nbx, _bb[1]-_nby), (_bb[2]-_nbx, _bb[1]-_nby),
                               (_bb[0]-_nbx, _bb[3]-_nby), (_bb[2]-_nbx, _bb[3]-_nby)]
                        _nc = [(_p[0]*_xs, _p[1]*_ys) for _p in _nc]
                        _nrot = getattr(_ent.dxf, 'rotation', 0)
                        if _nrot:
                            _nr = _math.radians(_nrot)   # DXF rotation 单位是度，cos/sin 需弧度
                            _ncr = _math.cos(_nr); _nsr = _math.sin(_nr)
                            _nc = [(_p[0]*_ncr - _p[1]*_nsr, _p[0]*_nsr + _p[1]*_ncr) for _p in _nc]
                        for _p in _nc:
                            _pts.append((_ipt[0] + _p[0], _ipt[1] + _p[1]))
            except Exception:
                continue
        if not _pts:
            _block_bbox_cache[_name] = None
            return None
        _bb_ret = (min(p[0] for p in _pts), min(p[1] for p in _pts),
                   max(p[0] for p in _pts), max(p[1] for p in _pts))
        _block_bbox_cache[_name] = _bb_ret
        return _bb_ret

    # ---------- R26：图框块「边框证据」校验 ----------
    # 场景：6、10号楼住宅户型.dwg —— 块 -1fxhs0421 定义 bbox 143285×105800（图框级、
    #   ratio 1.354），3 个实例曾被当图框（R25 还为它做了斜放块去旋转口径）。渲染+实体
    #   级诊断证实它根本不是图框：块内 56 条多段线全是 200~279mm 的 S-辅助层小方块
    #   散件，最长单条直边仅 200mm（块长边的 0.14%）——bbox 被稀疏散布的小构件撑大，
    #   覆盖区基本空白。这类「内容块」霸占 rel 分母（面积 1.5e10），把同图真框的
    #   rel 压到阈值之下（42000 上框 8.2%、26760 小框 3.0% <10% 被拒，fc 构成出错）。
    # 判据：真图框块内部必有接近块尺寸的边框线（长边框线 ≥ 块长边的 80~100%），
    #   内容块的最大构件远小于块尺寸。块内「最长直边构件」< 块 bbox 长边 × 30% →
    #   判为内容块，不入候选库。构件含：LINE 长度、LWPOLYLINE 边段/闭合 bbox 长边、
    #   嵌套 INSERT 子块 bbox 长边（图框边线可能在子块里，递归收集）。
    _BLOCK_EVIDENCE_RATIO = 0.30
    _block_evidence_cache = {}

    def _block_max_member_len(_name, _visited=None):
        """块定义内「最长直边构件」的长度（块定义内坐标，未应用 INSERT 缩放）"""
        if _name in _block_evidence_cache:
            return _block_evidence_cache[_name]
        if _visited is None:
            _visited = set()
        if _name in _visited or _name not in doc.blocks:
            return 0.0
        _visited.add(_name)
        _mx = 0.0
        for _ent in doc.blocks[_name]:
            try:
                _t = _ent.dxftype()
                if _t == 'LINE':
                    _s, _e = _ent.dxf.start, _ent.dxf.end
                    _mx = max(_mx, _math.hypot(_e.x - _s.x, _e.y - _s.y))
                elif _t == 'LWPOLYLINE':
                    _ps = _ent.get_points('xy')
                    if len(_ps) >= 2:
                        for _a, _b in zip(_ps, _ps[1:]):
                            _mx = max(_mx, _math.hypot(_b[0] - _a[0], _b[1] - _a[1]))
                        if _ent.closed:
                            _a, _b = _ps[-1], _ps[0]
                            _mx = max(_mx, _math.hypot(_b[0] - _a[0], _b[1] - _a[1]))
                        _xs2 = [p[0] for p in _ps]; _ys2 = [p[1] for p in _ps]
                        _mx = max(_mx, max(max(_xs2) - min(_xs2), max(_ys2) - min(_ys2)))
                elif _t == 'INSERT':
                    _sub_bb = _get_block_world_bbox(_ent.dxf.name)
                    if _sub_bb is not None:
                        _sx = getattr(_ent.dxf, 'xscale', 1) or 1
                        _sy = getattr(_ent.dxf, 'yscale', 1) or 1
                        _sub_len = max(_sub_bb[2] - _sub_bb[0], _sub_bb[3] - _sub_bb[1]) * max(abs(_sx), abs(_sy))
                        _mx = max(_mx, _sub_len)
                        _mx = max(_mx, _block_max_member_len(_ent.dxf.name, _visited))
            except Exception:
                continue
        _block_evidence_cache[_name] = _mx
        return _mx

    # ---------- R27：图框块「闭合边框证据」通道（升级 R26 边框证据校验） ----------
    # 场景：拼接图.dwg —— 页内平面图内容块 9ZZZW（块定义 71846×50889，108 条 LINE +
    #   6 条 LWPOLYLINE + proxy 散件），最长直边构件 45317mm = 块长边的 63%，R26 的
    #   「最长构件 ≥30%」判据拦不住 → 5 个实例全部混入候选库。后果双重：
    #   ① rel 11.8% 恰过条件C 门槛直接误检（5 个假图框）；② 上/下页框内部恰各含
    #   2 个同尺寸 9ZZZW 实例，触发包裹框剔除（MIN_WRAPPED_SAME_SIZE=2）把真页框剔掉
    #   （fc 5 = 3 真 + 2 假，真页框反而失踪）。
    # 判据：真图框块内部必有「闭合矩形边框」——闭合 LWPOLYLINE 的 bbox、LINE 横竖
    #   线配对成环（两横簇 + 两竖簇互相覆盖 ≥80% 成矩形）、或嵌套子块内的闭合框
    #   （递归×缩放）。内容块（平面图）的轮廓线是开放散线，单条再长也凑不成闭合
    #   矩形 → 闭合证据长边 < 块长边 × 50% → 判为内容块（主判）。
    #   R26 的构件判据降为辅判（主判通过、但最长直边 <30% 的碎线拼框）。
    _BLOCK_CLOSED_RATIO = 0.50
    _block_closed_cache = {}

    def _block_closed_frame_len(_name, _bb_len, _visited=None):
        """块定义内「闭合矩形边框」的长边（块定义内坐标，未应用 INSERT 缩放）。
        证据 = max(闭合 LWPOLYLINE bbox 长边, LINE 横竖簇配对矩形长边, 嵌套子块证据×缩放)。
        无闭合框返回 0。"""
        if _name in _block_closed_cache:
            return _block_closed_cache[_name]
        if _visited is None:
            _visited = set()
        if _name in _visited or _name not in doc.blocks or _bb_len <= 0:
            return 0.0
        _visited.add(_name)
        _ev = 0.0
        _tol_p = max(1.0, _bb_len * 0.002)      # 平行线/端点重合容差
        _min_seg = _bb_len * 0.30               # 参与成框的边线最短长度
        _h_lines = []                           # (y, x1, x2) 近似水平线
        _v_lines = []                           # (x, y1, y2) 近似竖直线
        for _ent in doc.blocks[_name]:
            try:
                _t = _ent.dxftype()
                if _t == 'LINE':
                    _s, _e = _ent.dxf.start, _ent.dxf.end
                    _dx, _dy = _e.x - _s.x, _e.y - _s.y
                    if _math.hypot(_dx, _dy) < _min_seg:
                        continue
                    if abs(_dy) <= _tol_p:
                        _h_lines.append((min(_s.y, _e.y), min(_s.x, _e.x), max(_s.x, _e.x)))
                    elif abs(_dx) <= _tol_p:
                        _v_lines.append((min(_s.x, _e.x), min(_s.y, _e.y), max(_s.y, _e.y)))
                elif _t == 'LWPOLYLINE':
                    _ps = _ent.get_points('xy')
                    if len(_ps) < 3:
                        continue
                    _xs2 = [p[0] for p in _ps]; _ys2 = [p[1] for p in _ps]
                    # closed 标志，或首尾点重合（画框忘设 closed 标志的常见画法）
                    _is_loop = bool(_ent.closed) or _math.hypot(
                        _ps[-1][0] - _ps[0][0], _ps[-1][1] - _ps[0][1]) <= _tol_p * 2
                    if _is_loop:
                        _ev = max(_ev, max(max(_xs2) - min(_xs2), max(_ys2) - min(_ys2)))
                elif _t == 'INSERT':
                    _sub_bb = _get_block_world_bbox(_ent.dxf.name)
                    if _sub_bb is not None:
                        _sub_bblen = max(_sub_bb[2] - _sub_bb[0], _sub_bb[3] - _sub_bb[1])
                        if _sub_bblen > 0:
                            _sx = getattr(_ent.dxf, 'xscale', 1) or 1
                            _sy = getattr(_ent.dxf, 'yscale', 1) or 1
                            _ev = max(_ev, _block_closed_frame_len(
                                _ent.dxf.name, _sub_bblen, _visited) * max(abs(_sx), abs(_sy)))
            except Exception:
                continue
        # LINE 横竖簇配对成矩形：横线按 y 聚类、竖线按 x 聚类；两横簇
        # （y 距 ≥_min_seg、x 重叠 ≥_min_seg 且覆盖短者 80%）+ 竖簇支撑
        # （x 落在重叠带内、y 覆盖矩形高度 ≥80%）→ 判定存在闭合矩形。
        _h_lines.sort()
        _v_lines.sort()
        _h_cl = []
        for _y, _x1, _x2 in _h_lines:
            if _h_cl and _y - _h_cl[-1][0] <= _tol_p:
                _py, _px1, _px2 = _h_cl[-1]
                _h_cl[-1] = (_py, min(_px1, _x1), max(_px2, _x2))
            else:
                _h_cl.append((_y, _x1, _x2))
        _v_cl = []
        for _x, _y1, _y2 in _v_lines:
            if _v_cl and _x - _v_cl[-1][0] <= _tol_p:
                _px, _py1, _py2 = _v_cl[-1]
                _v_cl[-1] = (_px, min(_py1, _y1), max(_py2, _y2))
            else:
                _v_cl.append((_x, _y1, _y2))
        for _i in range(len(_h_cl)):
            _y1, _ha1, _ha2 = _h_cl[_i]
            for _j in range(_i + 1, len(_h_cl)):
                _y2, _hb1, _hb2 = _h_cl[_j]
                _hh = _y2 - _y1
                if _hh < _min_seg:
                    continue
                _ox1 = max(_ha1, _hb1); _ox2 = min(_ha2, _hb2)
                if _ox2 - _ox1 < _min_seg:
                    continue
                if _ox2 - _ox1 < 0.80 * min(_ha2 - _ha1, _hb2 - _hb1):
                    continue
                _left = None; _right = None
                for _vx, _vy1, _vy2 in _v_cl:
                    if _vx > _ox2 + _tol_p:
                        break           # _v_cl 已按 x 升序，后面只会更靠右
                    if _vx < _ox1 - _tol_p:
                        continue
                    if min(_vy2, _y2) - max(_vy1, _y1) < 0.80 * _hh:
                        continue
                    if _left is None or _vx < _left:
                        _left = _vx
                    if _right is None or _vx > _right:
                        _right = _vx
                if _left is not None and _right is not None:
                    _ww = min(_right, _ox2) - max(_left, _ox1)
                    if _ww >= _min_seg:
                        _ev = max(_ev, _ww, _hh)
        _block_closed_cache[_name] = _ev
        return _ev

    _rescued_keys = set()  # 布局空间网格图框救援命中的 bbox key（修法A，见下）

    # ---------- 修法A：布局空间「网格排版图框」救援（2026-09-11） ----------
    # 场景：DS4 四层 阁楼 平面系统图 (1).dwg —— 真实图框有 6 个，程序只报 5 个。
    #   6 个真图框全部在 **布局1**（图纸空间），是块参照 A$C5AB964F6 的 6 个实例：
    #   尺寸全等 434.1×311.1、位置 3 列 × 2 行（列距 434.1、行距 311.1，严丝合缝）。
    #   但块尺寸短边 311.1 < INSERT_MIN_SHORT_SIDE(500)，且 311.1 不在 A 系列
    #   (841,594,420,297,210,148,105) 中 —— 原因是这张图把 A4 框按 1.0475 倍
    #   (311.1 = 297 × 1.0475) 画在布局空间，整数判定失效 → 6 个全被预筛当
    #   "家具符号块"剔除，布局1 候选入库 0 个；程序转而采纳**模型空间**的 5 个
    #   内容区大块（9900×12900 等），于是报 5 个、且报错了空间。
    #
    # 判据（"同尺寸 + 成网格"是排版图纸页的强信号，与绝对尺寸无关）：
    #   ① 仅对**图纸空间布局**生效（layout_name != '模型空间'）。布局空间本就是
    #      用来排图纸页的，出现多个等大框即"一版多页"；模型空间不做救援，避免
    #      影响大量仅有模型空间的图纸（本地 21 张里 19 张只有模型空间）。
    #   ② 同尺寸（宽高各 ±2mm 且同块名）INSERT ≥ REQUIRED_MIN_COUNT(4) 个。
    #      4 个起步：DS4 是 6 个；门槛过低会放进"图例阵列/家具阵列"。
    #   ③ 长宽比 ∈ [FRAME_RATIO_MIN, FRAME_RATIO_MAX]，确保是纸张形状。
    #   ④ 排列成网格：按 X 聚类得列数 ≥2、按 Y 聚类得行数 ≥2，且网格覆盖率
    #      ≥ 0.8（实际个数 / 行列乘积），排除"一列排开"（对齐排列的特征）。
    #   ⑤ 至少一维重复（列数≥2 且行数≥2 已覆盖）。
    #   命中后把这些 INSERT 的 bbox key 存入 _rescued_keys，预筛时对它们豁免
    #   短边下限。救援只影响"是否入库"，是否算图框仍由 is_frame_like 决定。
    _GRID_RESCUE_MIN_COUNT = 4        # 同尺寸实例数下限
    _GRID_RESCUE_TOL = 2.0            # 同尺寸判定容差（mm）：宽高各自允许偏差
    _GRID_RESCUE_COVERAGE = 0.80      # 网格覆盖率下限
    _GRID_RESCUE_ENABLED = os.environ.get('FRAME_PARSER_NO_GRID_RESCUE') != '1'
    if _GRID_RESCUE_ENABLED and layout_name != '模型空间':
        # 先按 (块名, 宽, 高) 分桶统计，桶内再做网格判定
        _grid_buckets = {}
        for _e in visible_entities:
            if _e.dxftype() != 'INSERT':
                continue
            try:
                _bn2 = _e.dxf.name
                _bd2 = _get_block_world_bbox(_bn2)
                if _bd2 is None:
                    continue
                _xs2 = _e.dxf.xscale; _ys2 = _e.dxf.yscale
                if abs(_xs2) < 1e-9 or abs(_ys2) < 1e-9:
                    continue
                # 块基点 + 旋转（度→弧度）：DXF 变换 insert + R·S·(p − base)，与主 INSERT 路径同规范
                try:
                    _nb2 = doc.blocks[_bn2].block.dxf.base_point
                    _nb2x, _nb2y = _nb2.x, _nb2.y
                except Exception:
                    _nb2x = _nb2y = 0.0
                _r2 = getattr(_e.dxf, 'rotation', 0)
                _lp = [(_bd2[0]-_nb2x, _bd2[1]-_nb2y), (_bd2[2]-_nb2x, _bd2[1]-_nb2y),
                       (_bd2[0]-_nb2x, _bd2[3]-_nb2y), (_bd2[2]-_nb2x, _bd2[3]-_nb2y)]
                _lp = [(p[0]*_xs2, p[1]*_ys2) for p in _lp]
                if _r2:
                    _rad2 = _math.radians(_r2)
                    _cr2 = _math.cos(_rad2); _sr2 = _math.sin(_rad2)
                    _lp = [(p[0]*_cr2 - p[1]*_sr2, p[0]*_sr2 + p[1]*_cr2) for p in _lp]
                _ip2 = (_e.dxf.insert.x, _e.dxf.insert.y)
                _wp = [(p[0] + _ip2[0], p[1] + _ip2[1]) for p in _lp]
                if _rot is not None:
                    _wp = [_rot_pt(p[0], p[1]) for p in _wp]
                _x0 = min(p[0] for p in _wp); _y0 = min(p[1] for p in _wp)
                _x1 = max(p[0] for p in _wp); _y1 = max(p[1] for p in _wp)
                _ww = _x1 - _x0; _hh = _y1 - _y0
                if _ww <= 0 or _hh <= 0:
                    continue
            except Exception:
                continue
            _grid_buckets.setdefault(_bn2, []).append({
                'w': _ww, 'h': _hh, 'x': _x0, 'y': _y0,
                'x1': _x1, 'y1': _y1,
            })

        for _bn2, _items in _grid_buckets.items():
            if len(_items) < _GRID_RESCUE_MIN_COUNT:
                continue
            # 桶内再按尺寸细分（同块名可能以不同比例插入）：以首个为基准聚类，
            # 用贪心 grouping，避免 O(n²) 全比较。
            _size_groups = []
            for _it in _items:
                _placed = False
                for _g in _size_groups:
                    if (abs(_it['w'] - _g['w']) <= _GRID_RESCUE_TOL
                            and abs(_it['h'] - _g['h']) <= _GRID_RESCUE_TOL):
                        _g['members'].append(_it)
                        _placed = True
                        break
                if not _placed:
                    _size_groups.append({'w': _it['w'], 'h': _it['h'], 'members': [_it]})
            for _g in _size_groups:
                _mem = _g['members']
                if len(_mem) < _GRID_RESCUE_MIN_COUNT:
                    continue
                _gw, _gh = _g['w'], _g['h']
                _gshort = min(_gw, _gh)
                _gratio = (max(_gw, _gh) / _gshort) if _gshort > 0 else 0
                if not (FRAME_RATIO_MIN <= _gratio <= FRAME_RATIO_MAX):
                    continue
                # 网格判定：按 X/Y 投影聚类。容差取尺寸的 15%（同列必然 y 重叠，
                # 列与列之间至少隔一个框宽，15% 足以分开且容忍画图误差）。
                _tolx = max(_gw * 0.15, 1.0)
                _toly = max(_gh * 0.15, 1.0)

                def _cluster(vals, tol):
                    """一维聚类：返回簇列表（每簇是若干值）"""
                    _vs = sorted(vals)
                    _out = []
                    _cur = [_vs[0]]
                    for _v in _vs[1:]:
                        if _v - _cur[-1] <= tol:
                            _cur.append(_v)
                        else:
                            _out.append(_cur)
                            _cur = [_v]
                    _out.append(_cur)
                    return _out

                _cols = _cluster([m['x'] for m in _mem], _tolx)
                _rows = _cluster([m['y'] for m in _mem], _toly)
                _ncol, _nrow = len(_cols), len(_rows)
                if _ncol < 2 or _nrow < 2:
                    continue
                _coverage = len(_mem) / float(_ncol * _nrow)
                if _coverage < _GRID_RESCUE_COVERAGE:
                    continue
                safe_log(f"  [{layout_name}] 布局空间网格图框救援: 块 {_bn2!r} "
                         f"{len(_mem)} 个 {_gw:.1f}×{_gh:.1f} (ratio {_gratio:.3f}, 短边 {_gshort:.1f}) "
                         f"排列成 {_ncol} 列 × {_nrow} 行（覆盖率 {_coverage:.0%}）"
                         f"→ 豁免短边预筛")
                for _m in _mem:
                    _rescued_keys.add((round(_m['x'], 3), round(_m['y'], 3),
                                       round(_m['x1'], 3), round(_m['y1'], 3)))

    _insert_total = 0
    _insert_kept = 0
    _insert_filtered = 0  # 预筛剔除数（非图框级尺寸）
    _r27_rej_max = 0.0    # R27 闭合通道拒绝、但构件证据合格的内容块最大面积（rel 分母计回用）
    # R30：计回尺寸上限。真图框长边物理上限 ≈ A0 纸 1189mm × 1:1000 出图 ≈ 1.19e6mm，
    # 取 2.5e6 留两倍余量。超限的巨型底图/xref 外包络（S010 北区商业街底图
    # 6345273×1806446，块内含超长道路线使构件证据"合格"）不得计回——否则 rel
    # 分母被污染 1833 倍，真图框（105100×59400）rel 仅 0.054%，条件 C 永远
    # 拒之门外 → fc=0 漏检（前端 force_max 降级显示"尺寸对但图框数 0"）。
    _R27_CREDIT_MAX_SIDE = 2_500_000
    for _entity in visible_entities:
        if _entity.dxftype() != 'INSERT':
            continue
        _insert_total += 1
        try:
            _bn = _entity.dxf.name
            _bb_def = _get_block_world_bbox(_bn)
            if _bb_def is None:
                continue
            _xs = _entity.dxf.xscale; _ys = _entity.dxf.yscale
            if abs(_xs) < 1e-9 or abs(_ys) < 1e-9:
                continue
            _ins_rot = getattr(_entity.dxf, 'rotation', 0)
            # 块基点：DXF 变换规范为 p_world = insert + R(rot)·S·(p − base)。
            # 基点非 0 的块若不减去，bbox 会整体平移 base×scale（本图 3/106 个块非零基点）。
            try:
                _bp = doc.blocks[_bn].block.dxf.base_point
                _bpx, _bpy = _bp.x, _bp.y
            except Exception:
                _bpx = _bpy = 0.0
            _bx1, _by1, _bx2, _by2 = (_bb_def[0]-_bpx, _bb_def[1]-_bpy,
                                      _bb_def[2]-_bpx, _bb_def[3]-_bpy)
            _local_pts = [(_bx1*_xs, _by1*_ys), (_bx2*_xs, _by1*_ys),
                          (_bx1*_xs, _by2*_ys), (_bx2*_xs, _by2*_ys)]
            if _ins_rot:
                # 关键：DXF rotation 单位是「度」，math.cos/sin 只吃「弧度」——
                # 旧实现把度直接喂给 cos/sin（rot=180 被当成 180 弧度≈233°），
                # 胜利公寓２ 马桶块 500×750 rot=180 被算成 900×850、位置飞出 100 万 mm。
                _rad = _math.radians(_ins_rot)
                _cr = _math.cos(_rad); _sr = _math.sin(_rad)
                _local_pts = [(p[0]*_cr - p[1]*_sr, p[0]*_sr + p[1]*_cr)
                              for p in _local_pts]
            _ip = (_entity.dxf.insert.x, _entity.dxf.insert.y)
            _wpts = [(p[0] + _ip[0], p[1] + _ip[1]) for p in _local_pts]
            if _rot is not None:
                # 全局旋转矫正：倾斜图纸的块框转正后尺寸/方向才真实
                _wpts = [_rot_pt(p[0], p[1]) for p in _wpts]
            _xm = min(p[0] for p in _wpts); _ym = min(p[1] for p in _wpts)
            _xM = max(p[0] for p in _wpts); _yM = max(p[1] for p in _wpts)
            _w = _xM - _xm; _h = _yM - _ym
            # 斜放块（旋转非 90° 倍数）的 bbox 是「旋转后外接矩形」，面积/比例双双失真：
            #   6、10号楼住宅户型.dwg：图框块 -1fxhs0421 定义 143285×105800（ratio 1.354），
            #   某实例旋转 125° → bbox 168851×178056（面积膨胀 1.98 倍、ratio 1.055）。
            #   失真后果：① 膨胀面积虚增 rel 分母，同图 12 个 59450×42050 真框的
            #   rel 被压到 8.31%（<10%）→ 条件C 全拒，fc 20→5；② ratio 1.055 <
            #   1.343 被条件C 的 _at_least_sqrt2 拒，斜放图框本体漏检。
            #   修法：width/height/area 改用「去旋转真实图幅」（块定义×缩放，旋转不改
            #   面积），bbox 保留膨胀框用于空间关系（去重/包裹/嵌套判断）。
            #   并打 tilted 标记：bbox 是「旋转外接矩形」，不代表真实足迹（真实足迹
            #   只是框内斜带），去重规则1 不得以其 bbox 为包含区域剔内部候选——
            #   否则斜放框 bbox 角落处的邻位真框（74300/59450）被误剔（fc 20→18）。
            _tilted = 1e-6 < (_ins_rot % 90.0) < 90.0 - 1e-6
            if _tilted:
                _sw = abs(_xs) * (_bx2 - _bx1)
                _sh = abs(_ys) * (_by2 - _by1)
                if _sw > 0 and _sh > 0:
                    _w = max(_sw, _sh)
                    _h = min(_sw, _sh)
            if _w <= 0 or _h <= 0:
                continue
            _bbox = (_xm, _ym, _xM, _yM)
            _key = (round(_xm, 3), round(_ym, 3), round(_xM, 3), round(_yM, 3))
            # 预筛：尺寸必须"图框级"才入库，避免家具/符号类小块污染候选库。
            # 短边 <500 但命中标准 A 系列短边（A4 210/A3 297/A2 420…）的块也放行——
            # 小图幅图框块（A4 竖 210×297 等）不能因阈值被误杀。
            # 另：布局空间「网格排版图框」（修法A）豁免短边下限，见上方救援段。
            _w_short = min(_w, _h)
            _w_ratio = (max(_w, _h) / _w_short) if _w_short > 0 else 0
            _is_a_series_short = any(abs(_w_short - _s) <= 1.0 for _s in _A_SERIES_SHORT_SIDES)
            _is_grid_rescued = _key in _rescued_keys
            if ((_w_short < INSERT_MIN_SHORT_SIDE and not _is_a_series_short
                    and not _is_grid_rescued) or
                    not (FRAME_RATIO_MIN <= _w_ratio <= FRAME_RATIO_MAX)):
                _insert_filtered += 1
                continue
            # R26/R27 图框块「边框证据」校验（双通道）：
            #   主判（R27 闭合边框证据）：真图框块必有闭合矩形边框；内容块
            #     （如 拼接图.dwg 的 9ZZZW：最长构件 63% 块长但无闭合矩形）剔除。
            #   辅判（R26 构件证据）：最长直边构件 < 块长边×30% → bbox 被稀疏散件
            #     撑大（如 -1fxhs0421：最长直边 200mm vs 块长边 143285mm）。
            _bb_len = max(_bb_def[2] - _bb_def[0], _bb_def[3] - _bb_def[1])
            _reject_reason = None
            if _bb_len > 0:
                _closed_len = _block_closed_frame_len(_bn, _bb_len)
                if _closed_len < _bb_len * _BLOCK_CLOSED_RATIO:
                    _reject_reason = (f"无闭合边框证据（闭合框长边 {_closed_len:.0f}mm < 块长边 "
                                      f"{_bb_len:.0f}mm × {_BLOCK_CLOSED_RATIO:.0%}）")
                else:
                    _member_len = _block_max_member_len(_bn)
                    if _member_len < _bb_len * _BLOCK_EVIDENCE_RATIO:
                        _reject_reason = (f"最长直边构件 {_member_len:.0f}mm < 块长边 {_bb_len:.0f}mm × "
                                          f"{_BLOCK_EVIDENCE_RATIO:.0%}（闭合框疑为碎线拼成）")
            if _reject_reason:
                _insert_filtered += 1
                safe_log(f"  [内容块剔除] 块 {_bn}（{_w:.0f}×{_h:.0f}）{_reject_reason}，判为非图框内容块")
                # R27 回归修复（一层.dwg fc 0→11）：闭合通道拒掉的块若构件证据合格
                #   （最长直边构件 ≥ 块长边×30%，即"若非闭合拦截本可通过 R26 入库"），
                #   其面积按 layout 记账，供 rel 分母计回。
                #   根因：R26 时代这类块（如一层.dwg 标注内容块 A$C04810A0A
                #   12240×8770）会入库成为 rel 分母；R27 拒掉后分母塌缩到家具级
                #   候选（1.07e8→2.06e7），原本被条件C 10% 门槛压住的家具矩形
                #   （957~5160mm）全部涌入并靠互证链逃生。计回 = 恢复 R26 分母。
                #   不计回的：构件证据也不合格的块（S-0-COLS 26.2%、-1fxhs0421
                #   0.19%）——R26 时代它们同样被拒、从未进过分母（1号2号楼
                #   S-0-COLS 1.5e12 / 6、10号楼 -1fxhs0421 1.5e10 若计回会把
                #   真框 rel 全部压死，fc 崩）。
                if _bb_len > 0:
                    _member_len_r27 = _block_max_member_len(_bn)
                    if (_member_len_r27 >= _bb_len * _BLOCK_EVIDENCE_RATIO
                            and max(_w, _h) <= _R27_CREDIT_MAX_SIDE):
                        if _w * _h > _r27_rej_max:
                            _r27_rej_max = _w * _h
                continue
            if _key in seen_bbox:
                continue
            seen_bbox.add(_key)
            candidates.append({
                'type': '块参照插入',
                'area': _w * _h,
                'bbox': _bbox,
                'width': _w,
                'height': _h,
                'layout': layout_name,
                'rectangularity': 1.0,
                'block_name': _bn,
                'insert_layer': _entity.dxf.layer,
                # 块定义 bbox 尺寸（未应用 INSERT 缩放）：供特征筛选的 A+ 判据
                # "块模板本身是不是标准图幅"使用（2.8米皮带线 的 A4-横-杨勇 块定义
                # 即 297×210=A4，12 个实例以 ×1/×2/×4/×10/×20 插入）。
                'block_def_w': abs(_bb_def[2] - _bb_def[0]),
                'block_def_h': abs(_bb_def[3] - _bb_def[1]),
                # 斜放块标记（旋转非 90° 倍数）：bbox 是旋转外接矩形（膨胀框），
                # 去重规则1 不得以其 bbox 为包含区域剔除内部候选
                'tilted': _tilted,
            })
            _insert_kept += 1
        except Exception:
            continue

    if _insert_total:
        safe_log(f"  [{layout_name}] INSERT 块参照: 共 {_insert_total} 个 | 预筛剔除 {_insert_filtered} 个（非图框级尺寸）| 入库 {_insert_kept} 个")

        # 1.6. 对齐排列剔除：同块名 ≥3 个 INSERT 实例，若它们在 x 或 y 方向紧贴成线
        #    （短方向标准差 < 平均尺寸 0.5，另一方向散布 >5 倍），视为对齐排列的装饰块——
        #    常见如柱块（1 楼柱、楼层柱网）、墙线、门窗阵列、家具等。这类块每个尺寸
        #    都是"图框级"（按用户策略会被收），但实际不是图框，必须按位置辨别。
        #    真图框位置散布、不规则，不会被排除（参照 12fas 在澜山的 15 个 散布位置）。
        #    <3 个实例不判断（无法判断是否对齐，保留进框架判别）。
        #    白名单保护（方案2）：尺寸为标准 A 系列整数尺寸的块（短边 ∈ {841,594,420,297,
        #    210,148,105}、长边 ≈ 短边×√2）不参与对齐剔除——真图框块常以标准 A 系列尺寸画
        #    （841×594=A1、594×420=A2 等），多张图框并排排成一行也命中对齐启发式（一层平面图
        #    3.25: 块名"图框2" 11 个水平排列，841×594 命中 A1 → 白名单保护，不剔除）。
        #    装饰块尺寸通常非 A 系列（柱 700×1215、家具 1200×4800 等），不受白名单影响。
        import statistics as _stats
        def _is_standard_a_series(w, h, eps=1.0):
            """尺寸是否为标准 A 系列整数尺寸（短边在标准集合内、长边≈短边×√2）
            eps=1.0 容差：尺寸规整容差 + 些许画图误差"""
            _short = min(w, h); _long = max(w, h)
            for _std in _A_SERIES_SHORT_SIDES:
                if abs(_short - _std) <= eps and abs(_long - _std * _math.sqrt(2)) <= eps:
                    return True
            return False
        _bn_groups = {}
        for _idx, _c in enumerate(candidates):
            if _c.get('type') == '块参照插入':
                _bn_groups.setdefault(_c.get('block_name', '<unknown>'), []).append(_idx)
        _aligned_log = []
        _a_series_protected = []  # 白名单保护（图框类块，不参与对齐剔除）
        _rm_idx = set()
        # 第一遍：先收集所有命中 A系列/√2 保护的图框块短边 →
        #   "同模板短边保护"用（2026-09-14，1号2号楼柱平法施工图）：
        #   同一套图的图框块常存在新旧版本（template_院标准图框_a0 与
        #   ..._a020221031172552，图框本体同为 841×1189@150），新版本块内多贴了
        #   参照标签等外溢内容 → bbox 高被撑到 222937（ratio 1.767），√2 保护
        #   失效 → 3 个实例被当装饰阵列剔除 → 漏 3 张图框。信号：真图框是同
        #   模板复制，其**短边与已保护图框块的短边一致（±1%）**——装饰块
        #   （柱 700×1215 等）短边不会恰好等于图框短边。
        _frame_short_sides = set()
        for _bn, _grp in _bn_groups.items():
            if len(_grp) < 3:
                continue
            _s0 = candidates[_grp[0]]
            _s0min = min(_s0['width'], _s0['height'])
            _s0max = max(_s0['width'], _s0['height'])
            if _is_standard_a_series(_s0['width'], _s0['height']):
                _frame_short_sides.add(_s0min)
            elif _s0min > 0 and abs(_s0max / _s0min - _math.sqrt(2)) / _math.sqrt(2) <= 0.10:
                _frame_short_sides.add(_s0min)
        for _bn, _grp in _bn_groups.items():
            if len(_grp) < 3:
                continue
            # 白名单：标准 A 系列整数尺寸 或 长宽比接近 √2±10% → 不剔除。
            #   √2 保护覆盖自定义尺寸的图框块模板：效果图.dwg A$C5242259E 图框块 ×3
            #   同排并排（17098×12319 / 14534×10471，ratio 1.388 接近 √2），命中水平
            #   对齐启发式被当装饰阵列全删 → 漏 3 张。纸张框比例必接近 √2，而装饰块
            #   （柱/门窗/家具）比例远离 √2，√2 保护不会误放装饰阵列。
            _sample = candidates[_grp[0]]
            if _is_standard_a_series(_sample['width'], _sample['height']):
                _a_series_protected.append(f"{_bn}({len(_grp)}个,{_sample['width']:.0f}x{_sample['height']:.0f})")
                continue
            _s_min = min(_sample['width'], _sample['height'])
            _s_max = max(_sample['width'], _sample['height'])
            if _s_min > 0 and abs(_s_max / _s_min - _math.sqrt(2)) / _math.sqrt(2) <= 0.10:
                _a_series_protected.append(f"{_bn}({len(_grp)}个,{_sample['width']:.0f}x{_sample['height']:.0f},ratio{_s_max/_s_min:.3f})")
                continue
            # 同模板短边保护：短边与已保护图框块的短边一致（±1%）→ 视为同套
            #   图框的另一版本（bbox 被块内外溢内容撑大），不剔除。
            if _s_min > 0 and any(abs(_s_min - _fs) / _fs <= 0.01 for _fs in _frame_short_sides):
                _a_series_protected.append(
                    f"{_bn}({len(_grp)}个,{_sample['width']:.0f}x{_sample['height']:.0f},短边{_s_min:.0f}同图框族)")
                continue
            _ctr = [((candidates[_i]['bbox'][0]+candidates[_i]['bbox'][2])/2,
                     (candidates[_i]['bbox'][1]+candidates[_i]['bbox'][3])/2) for _i in _grp]
            _xs = [c[0] for c in _ctr]; _ys = [c[1] for c in _ctr]
            _x_std = _stats.pstdev(_xs) if len(_xs) > 1 else 0
            _y_std = _stats.pstdev(_ys) if len(_ys) > 1 else 0
            _avg = (candidates[_grp[0]]['width'] + candidates[_grp[0]]['height']) / 2
            # 垂直对齐判据：x 紧贴一列，y 散布多
            if _x_std < _avg * 0.5 and _y_std > _x_std * 5 + 1:
                _aligned_log.append(f"{_bn}({len(_grp)}个,垂直)")
                _rm_idx.update(_grp)
                continue
            # 水平对齐判据：y 紧贴一行，x 散布多
            if _y_std < _avg * 0.5 and _x_std > _y_std * 5 + 1:
                _aligned_log.append(f"{_bn}({len(_grp)}个,水平)")
                _rm_idx.update(_grp)

        if _a_series_protected:
            safe_log(f"  [{layout_name}] 标准A系列图框保护: {', '.join(_a_series_protected)} → 不参与对齐剔除")
        if _rm_idx:
            candidates = [c for _i, c in enumerate(candidates) if _i not in _rm_idx]
            safe_log(f"  [{layout_name}] 对齐排列剔除装饰块: {', '.join(_aligned_log)} → 共移除 {len(_rm_idx)} 个候选")

    # 2. 直线矩形（多矩形检测：每个由 4 条直线围出的区域都是一个候选）
    lines = [ent for ent in visible_entities if ent.dxftype() == 'LINE']
    if lines:
        _rect_start = len(candidates)
        for rect_bbox in detect_rectangles_from_lines(lines, rot=_rot):
            x1, y1, x2, y2 = rect_bbox
            width = x2 - x1
            height = y2 - y1
            if width > 0 and height > 0:
                candidates.append({
                    'type': '直线矩形',
                    'area': width * height,
                    'bbox': rect_bbox,
                    'width': width,
                    'height': height,
                    'layout': layout_name
                })
        # 2.5 标题栏条纹标记（条件G 用，2026-09-14）：为图框级的直线矩形候选
        #     预计算「右侧/左侧竖向标题栏条纹」结构签名，主流程二扫时用于
        #     「同模板缩放套图救援」（胜利公寓２：6 框同模板四种比例，小尺寸
        #     实例 rel/短边全不过既有通道，靠条纹签名 + 与已入选框同 ratio 救回）。
        #     性能护栏：detect_rectangles_from_lines 按面积降序返回，只标记前
        #     _STRIPE_MARK_MAX 个且 ratio 在图框区间内的候选（真图框必是大候选，
        #     小构件矩形不会进入前 400 也进不了救援）。
        _STRIPE_MARK_MAX = 400
        _rect_cands = candidates[_rect_start:]
        _mark_n = 0
        if _rect_cands:
            _h_segs, _v_segs = [], []
            for ent in visible_entities:
                _t = ent.dxftype()
                try:
                    if _t == 'LINE':
                        _sx, _sy = ent.dxf.start.x, ent.dxf.start.y
                        _ex, _ey = ent.dxf.end.x, ent.dxf.end.y
                    elif _t == 'LWPOLYLINE':
                        _pts = list(ent.get_points('xy'))
                        _cl = _pts + ([_pts[0]] if ent.closed else [])
                    else:
                        continue
                except Exception:
                    continue
                if _t == 'LINE':
                    _pair = [(_sx, _sy, _ex, _ey)]
                else:
                    _pair = [(_cl[i][0], _cl[i][1], _cl[i+1][0], _cl[i+1][1])
                             for i in range(len(_cl) - 1)]
                for _sx, _sy, _ex, _ey in _pair:
                    _p1 = _rot_pt(_sx, _sy)
                    _p2 = _rot_pt(_ex, _ey)
                    if abs(_p1[1] - _p2[1]) < 1.0 and abs(_p2[0] - _p1[0]) > 1.0:
                        _h_segs.append((min(_p1[0], _p2[0]), max(_p1[0], _p2[0]), _p1[1]))
                    elif abs(_p1[0] - _p2[0]) < 1.0 and abs(_p2[1] - _p1[1]) > 1.0:
                        _v_segs.append((min(_p1[1], _p2[1]), max(_p1[1], _p2[1]), _p1[0]))
            for _c in _rect_cands:
                if _mark_n >= _STRIPE_MARK_MAX:
                    break
                _w, _h = _c['width'], _c['height']
                _short = min(_w, _h)
                if _short <= 0:
                    continue
                _r = max(_w, _h) / _short
                if not (FRAME_RATIO_MIN <= _r <= 2.5):
                    continue  # 条纹救援只面向页形候选；细长条不算
                _mark_n += 1
                _c['title_stripe'] = has_title_stripe(_h_segs, _v_segs, _c['bbox'])
    # 3. 如果没有候选，取全实体包围盒（降级）
    #    排除 VIEWPORT：视口是布局空间的显示窗口（透视模型空间的"取景框"），
    #    不是图纸内容。布局里只有 VIEWPORT 说明图纸内容全在模型空间，
    #    布局本身没有画图框——这种空布局的视口边框没有图框意义，
    #    不应产生候选（泛悦通风 11-MW-FP001：布局1 只有 2 个 VIEWPORT，
    #    降级路径把视口框 29.17×12.37 当图框，frame_count 虚增 2→实际应 1）。
    #    排除后若无其他实体（空布局），不产生候选。
    #    同时排除文字/标注类实体（TEXT/MTEXT/DIMENSION/ATTRIB/ATTDEF 等）：图框边界
    #    由绘图几何线决定，文字与尺寸标注允许外溢（标题栏文字、技术要求、尺寸文字常
    #    伸出图框线外几毫米），若计入会把图框尺寸撑大——
    #    齿轮箱装配图-846x584：LINE 图框未识别走降级，一处 MTEXT 标注文字外溢到
    #    x=1160.6（图框右界 1150.1），包围盒被撑成 856.5 宽 → 输出 857×584 而非 846×584；
    #    支座.dwg 同类（"技术要求"文字外溢撑到 225）。降级语义是"内容区域边界"，
    #    文字不应改变该边界。
    if not candidates:
        # 降级包围盒只统计绘图几何实体（可能成为图框边的类型）
        NON_GEOMETRY_TYPES = {
            'TEXT', 'MTEXT', 'DIMENSION', 'ATTRIB', 'ATTDEF',
            'VIEWPORT', 'LEADER', 'MLEADER', 'IMAGE', 'OLE2FRAME',
        }
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        found = False
        for entity in visible_entities:
            if entity.dxftype() in NON_GEOMETRY_TYPES:
                continue  # 文字/标注/视口不是绘图几何，跳过
            bbox = get_entity_bbox(entity, doc)
            if bbox:
                x1, y1, x2, y2 = bbox
                min_x = min(min_x, x1, x2)
                min_y = min(min_y, y1, y2)
                max_x = max(max_x, x1, x2)
                max_y = max(max_y, y1, y2)
                found = True
        if found:
            width = max_x - min_x
            height = max_y - min_y
            area = width * height
            candidates.append({
                'type': '全实体包围盒',
                'area': area,
                'bbox': (min_x, min_y, max_x, max_y),
                'width': width,
                'height': height,
                'layout': layout_name
            })
        elif any(e.dxftype() == 'VIEWPORT' for e in visible_entities):
            safe_log(f"  [{layout_name}] 空布局（仅含视口，无绘图实体），跳过全实体包围盒降级")
    return candidates, _r27_rej_max

# ---------- 主解析函数 ----------
def get_bounding_box_from_bytes(file_bytes, filename, priority='polyline', unit='mm', mode='smart'):
    tmp_path = None
    try:
        if not file_bytes:
            raise ValueError("文件内容为空，可能是上传中断或文件损坏")

        suffix = '.dwg' if filename.lower().endswith('.dwg') else '.dxf'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        doc = load_document(tmp_path)

        # ---------- XREF 外部参照检测（方案B：只打警告，不加载外部文件） ----------
        # ezdxf 默认不加载 XREF 外部文件，引用这些块的 INSERT 实体会漏识别图框。
        # 现阶段只做检测 + 日志 + 返回字段，主流程不阻塞；
        # 等真遇到 XREF 图纸再决定是否升级到方案A（主动加载外部文件）。
        xref_warnings = _detect_xref_warnings(doc)
        if xref_warnings:
            safe_log("⚠️ [XREF 警告] 检测到外部参照，可能漏识别图框：")
            for w in xref_warnings:
                layouts_desc = ", ".join(f"{ln} {cnt} 处" for ln, cnt in w['layouts'].items())
                safe_log(f"   - 块 {w['block_name']!r} (xref: {w['xref_path']!r}) | "
                         f"{w['insert_count']} 处 INSERT | {layouts_desc}")
            safe_log("   建议拆开源 DWG 单独解析，或后续启用 XREF 加载方案")

        # ---------- 逐 layout 收集候选，并计算同源的分母（该 layout 总包围盒面积） ----------
        all_candidates = []
        # R27 回归修复：各 layout 被 R27 闭合通道拒绝、但构件证据合格的内容块最大面积，
        # 计回条件C 的 rel 分母（恢复 R26 时代分母状态，防止塌缩后家具级候选涌入）
        _r27_rejected_max_area = {}

        msp = doc.modelspace()
        msp_total = calculate_layout_total_bbox(msp, doc)
        _msp_cands, _msp_rej = collect_candidates_from_layout(msp, doc, '模型空间')
        _r27_rejected_max_area['模型空间'] = _msp_rej
        for c in _msp_cands:
            c['area_ratio'] = (c['area'] / msp_total) if msp_total > 0 else 1.0
            all_candidates.append(c)

        for layout in doc.layouts:
            if layout.name == 'Model':
                continue
            layout_total = calculate_layout_total_bbox(layout, doc)
            _lay_name = f'布局 "{layout.name}"'
            _lay_cands, _lay_rej = collect_candidates_from_layout(layout, doc, _lay_name)
            _r27_rejected_max_area[_lay_name] = _lay_rej
            for c in _lay_cands:
                c['area_ratio'] = (c['area'] / layout_total) if layout_total > 0 else 1.0
                all_candidates.append(c)

        if not all_candidates:
            raise ValueError("未找到任何有效边界")

        def build_payload(cands):
            """把候选列表转成前端可展示的结构（按面积降序，全量返回）。

            注意：此处必须与 frame_count / frame_counts_by_layout 同源同量——
            早期为控制 payload 只返回前 20 条，导致多图框图纸（如 21 框的
            春风公寓2）气泡标题按截断后明细统计出 20，而空间分布按全量
            统计出 21，前端两处数字对不上。明细是小对象，全量返回无压力。
            """
            result_cands = []
            for c in sorted(cands, key=lambda x: x['area'], reverse=True):
                cw, ch = c['width'], c['height']
                bx1, by1, bx2, by2 = c['bbox']
                if unit.lower() == 'inch':
                    cw, ch = cw * 25.4, ch * 25.4
                    bx1, by1, bx2, by2 = bx1 * 25.4, by1 * 25.4, bx2 * 25.4, by2 * 25.4
                result_cands.append({
                    'layout': c['layout'],
                    'type': c['type'],
                    'width': round(cw),
                    'height': round(ch),
                    # 世界坐标 bbox（供调试/测试断言位置；前端当前不用此字段）
                    'bbox': [round(bx1), round(by1), round(bx2), round(by2)],
                })
            return result_cands

        # ---------- 图框特征预判（两种模式共用） ----------
        # frame_count 统计"像图框"的候选数量（模型空间 + 布局空间合计），
        # 供前端展示"这张图纸有几个图框"；为启发式统计，允许少量误差。
        # 判定标准（满足任一即可）：
        #   条件A：归一化长宽比在区间内 + 面积占比 ≥ 15%（常规单图框 layout 场景）
        #   条件B：归一化长宽比在区间内 + 短边在合理图框尺寸范围内 + 显式检测（闭合多段线/直线矩形）
        #         + 尺寸规整（宽高接近整数）+ 短边下限 400mm
        #         ——密集轴线网格场景下，图框面积占比可能极低（<0.1%），但短边在 400~2000mm
        #           之间、且是显式画出的矩形，仍然可信；而网格格子短边常 >2000mm，被此条件过滤。
        #         尺寸规整用于过滤"墙线交错产生的闭合多段线轮廓"：真图框尺寸几乎总是整数
        #         （2500×1500、841×594），而墙体相交产生的轮廓尺寸常带小数（1895.28×2629.36）。
        #         短边下限 400mm 过滤门窗等构件小矩形（800×300、200×150 等）。注意：短边
        #         <400 的小图框（如 A4 横向 210mm）若面积占比达标仍可通过条件 A 保留，
        #         此下限只作用于条件 B 的"绕过面积占比"放宽通道。
        #   条件C：归一化长宽比在区间内 + 候选面积 ≥ 该 layout 最大候选面积 × 10%（相对面积法）
        #         ——多张图框画在同一模型空间时（如整本图纸都在 Model），layout 总包围盒巨大，
        #           单图框面积占比极低（<0.1%），条件 A 全挂；真图框短边可能因单位非标
        #           （0.01mm 等）数值放大到几万，超出条件 B 的 2000 上限。用相对面积代替绝对占比：
        #           真图框彼此同量级（≥10%），小矩形（标题栏/设备表/标注框）面积远小于最大图框被过滤。
        #           跟单位无关：无论 mm / 0.01mm / cm，比例关系不变。
        MAX_FRAME_SHORT_SIDE = 2000  # 合理图框短边上限（mm）；A0 竖版短边 841mm，留足余量
        MIN_FRAME_SHORT_SIDE = 400   # 条件 B 短边下限（mm）；过滤门窗等构件小矩形，真图框经条件 A 保底
        SIZE_ROUNDNESS_EPS = 0.1     # 尺寸规整容差（mm）：宽高与最近整数的差 ≤ 0.1 视为整数
        EXPLICIT_TYPES = {'闭合多段线', '直线矩形'}
        # 标准 A 系列短边集合（A0~A5）：块模板尺寸校验用（条件E / A+ 通道）
        A_SERIES_SHORT_SIDES = (841, 594, 420, 297, 210, 148, 105)
        # 常用制图比例白名单（与前端 app.js COMMON_PLOT_SCALES 同口径）：
        #   条件E（A+）用它校验"块模板 × k = 实例尺寸"中的 k 是不是合法出图倍率，
        #   挡住 39.05 / 95.16 之类自定义倍数缩放块（长中苑模板框、封面套图等）。
        COMMON_PLOT_SCALES = (1, 2, 2.5, 4, 5, 10, 20, 25, 40, 50, 75, 80,
                              100, 150, 200, 250, 300, 400, 500)
        # 条件 C：大尺寸真图框识别（相对面积法，跟单位无关）
        #   场景：多张图框画在同一模型空间（如整本图纸的 9 张图都在 Model 里），
        #   layout 总包围盒巨大 → 单图框面积占比极低（<0.1%），条件 A 全挂；
        #   而真图框短边可能因单位非标（0.01mm 等）数值放大到几万，超出条件 B 的 2000 上限。
        #   用"候选面积 / 该 layout 最大候选面积"代替绝对占比：真图框彼此同量级（≥10%），
        #   小矩形（标题栏/设备表/标注框）面积远小于最大图框，被自然过滤。
        #   跟单位无关：无论 mm / 0.01mm / cm，比例关系不变。
        #   风险：A0+A4 混排时 A4 仅占 A0 的 6.25%，可能被 10% 阈值排除；暂先上 10% 复测。
        REL_AREA_THRESHOLD = 0.10     # 条件 C 相对面积阈值：候选面积 ≥ 该 layout 最大候选面积 × 10%
        # 条件 C 对「直线矩形」单独放宽的长宽比下限（2026-09-07，55555555.dwg 3→5）：
        #   直线矩形 = P1 严格矩形（四边各有完整 LINE 覆盖），是"刻意画的框"，几乎无
        #   "内容轮廓冒充图框"风险——近方形内容 bbox 冒充（38200×40550 1.06、
        #   39400×31850 1.24、世欧澜山 45641×55212 1.21）都来自闭合多段线/INSERT 块/
        #   降级路径，闭合多段线不放宽，维持 √2×0.95≈1.343 下限即可。模型空间排版的
        #   大页面框被非等比拉伸时比例偏离 √2（55555555 上部两张 25904×22069 直线
        #   矩形 ratio 1.174 → 1.343 下限漏 2 张）→ 直线矩形下限放到 1.10（仍高于
        #   真·近正方形 1.0x，且须同时满足 rel≥10% 与 ratio≤2.5）。
        LINE_RECT_C_RATIO_MIN = 1.10

        for c in all_candidates:
            c['ratio'] = normalized_ratio(c['width'], c['height'])
            c['short_side'] = min(c['width'], c['height'])

        # 预计算每个 layout 的最大候选面积，供条件 C 使用（相对面积法分母）。
        # 排除 area_ratio>1.0 的异常候选（面积超过 layout 总面积必为计算异常/离群大块，
        # 如底图块 X-总图排水底图 88 万级别 INSERT），否则真图框 rel_area 被稀释到 <10%
        # 条件C 全挂（UCS图纸/含远距离底图块的图纸）。
        layout_max_area = {}
        # 被排除的大框（命中判据①② 的内容级底图/排版外框）单独记一份：它们不能作分母，
        # 但当 layout 里别无图框级候选时，需要它们来判定"这个 layout 压根没有参照系"。
        layout_excluded_big = {}
        for c in all_candidates:
            if c['area_ratio'] > 1.0:
                continue  # 异常候选不作为相对面积参考
            # 内容级底图/排版大外框不作 rel 分母（两条判据互补，覆盖两类底图）：
            #   ① 面积占比 ≥50% 且比例偏离 √2 超 10%（一楼大厅及展厅：261665×214254
            #      闭合多段线 ratio 1.22、area_ratio 92% 包住全部页面——若留作分母，10 张
            #      真页面块（333×4=57776×40851 + ytnuyi×6，ratio 精确 1.4143）rel 被稀释
            #      到 1.3~4.2% <10%，条件 C 全拒 → fc=1）。
            #   ② 面积占比 ≥15% 且 长宽比 > 2.5（滨江 江南铭庭 PM：532588×132430 超宽内容
            #      排版外框 ratio 4.02、area_ratio 23.7%——底框外还有大量内容拉大 layout 总
            #      面积，未达 50% 逃过判据①，仍霸占 rel 分母 → 25 张 √2 页框（12600×8910×15
            #      + 25200×17820×10，ratio 精确 1.4142）rel 被压到 0.16~0.64% 全拒 → fc=1。
            #      真图纸页框长宽比必 ≤ ~2.1（A 系 + 常用加长），2.5 留足余量；ratio>2.5 又
            #      占 layout ≥15% 的只可能是内容排版外框/条带）。
            #  真整页图框即使 area_ratio≈100%（图纸目录 42000×29700）也近 √2 且 ratio≤2.5，
            #  两种判据都不命中，仍作分母无害（单图框 layout 中即 rel=1）。
            #  这类大底框自身仍可被条件 A 收入候选，最终由包裹剔除收掉（内含 ≥2 独立尺寸组）。
            if (c['area_ratio'] >= 0.5 and abs(c['ratio'] / (2 ** 0.5) - 1) > 0.10) or \
               (c['area_ratio'] >= 0.15 and c['ratio'] > 2.5):
                _ln = c['layout']
                if _ln not in layout_excluded_big or c['area'] > layout_excluded_big[_ln]['area']:
                    layout_excluded_big[_ln] = c
                continue
            ln = c['layout']
            if ln not in layout_max_area or c['area'] > layout_max_area[ln]:
                layout_max_area[ln] = c['area']
        # rel 分母塌缩防护（2026-09-11，运煤胶带机.dwg 7→1）：
        #   判据①② 会把「整张图纸的外轮廓」踢出分母。若此时 layout 里**没有**留下
        #   任何图框级候选，分母就会塌缩到某个内容构件上，rel 被放大量级地虚高。
        #   运煤胶带机（1:1 设备布置图，全图 149823×77215 ratio 1.94 占 layout 97.6%）：
        #   外轮廓命中判据① 被踢出 → 分母塌缩到一个 8000×7500 的场地符号（占 0.51%）
        #   → 所有 rel 被放大 192.8 倍 → 7 个设备构件（4054×3159 / 4000×2000 /
        #   4000×1600 / 4011×1777…）rel 从真实的 0.06~0.11% 虚报成 10.7~21.3%，
        #   全部越过条件C 的 10% 门槛。
        #   判据：分母相对 layout 占比过小（<1%），且被排除的大框占 layout 绝大部分
        #   （≥50%）——即"layout 被一个非纸框大轮廓整体包住，里面全是内容"。
        #   这种 layout 的特质是：那个大轮廓就是图框（整张图即一页），不该再从里面
        #   挑构件；把该空间 rel 置 0，只保留条件A/B 等不依赖 rel 的通道，于是大轮廓
        #   自身经条件A（占比≥15%）被选中，构件全部落选。
        #   为什么占比阈值取 1% 而不是 5%：被误伤的既有场景里，分母占比可以低到
        #   0.11%（RF雅安 84100×59400），但它**同时**满足"大框占比不高"（RF雅安无
        #   被排除大框、分母本身即真图框）——本判据要求"分母占比 <1%" **且**
        #   "存在占比 ≥50% 的被排除大框"两个条件同时成立，RF雅安/滨江/一层平面图/
        #   一楼大厅等都不满足后者（它们的分母是真图框或内容框但未被整体包住）。
        _REL_DENOM_MIN_SHARE = 0.01
        if os.environ.get('FRAME_PARSER_NO_DENOM_GUARD') == '1':
            _REL_DENOM_MIN_SHARE = 0.0     # A/B 回归对照用
        _no_ref_layouts = set()
        for _ln, _ma in layout_max_area.items():
            _total = msp_total if _ln == '模型空间' else None
            if _total is None:
                # 布局空间总面积：用该 layout 候选的 area/area_ratio 反推最稳妥
                _total = 0
                for _c in all_candidates:
                    if _c['layout'] == _ln and _c['area_ratio'] > 0:
                        _total = max(_total, _c['area'] / _c['area_ratio'])
            if _total <= 0 or _ma / _total >= _REL_DENOM_MIN_SHARE:
                continue
            _big = layout_excluded_big.get(_ln)
            if _big is None or _big['area'] / _total < 0.5:
                continue
            _no_ref_layouts.add(_ln)
            safe_log(f"  [{_ln}] rel 分母（{_ma:,.0f}）仅占 layout {_ma / _total * 100:.2f}%，"
                     f"且存在占 layout {_big['area'] / _total * 100:.1f}% 的被排除大框"
                     f"（{_big['width']:.0f}×{_big['height']:.0f} ratio {_big['ratio']:.3f}）"
                     f"→ 判定为『整图即图框、内无图框级参照系』，该空间 rel 置 0，"
                     f"仅保留条件A/B 通道")
        for c in all_candidates:
            if c['layout'] in _no_ref_layouts:
                c['rel_area_ratio'] = 0.0
                continue
            ma = layout_max_area.get(c['layout'], 0)
            # R27 回归修复（一层.dwg fc 0→11）：入库阶段被 R27 闭合通道拒绝、但构件
            # 证据合格的内容块面积计回分母。R26 时代这类块（一层.dwg A$C04810A0A
            # 12240×8770）会入库成为分母；R27 拒掉后分母塌缩到家具级（1.07e8→2.06e7），
            # 原本被 10% 门槛压住的家具矩形（rel 1.9~9.7%）全部涌入条件C 并靠互证链
            # 逃生 → fc 0→11。取 max 计回天然自限：只修"塌缩"，无塌缩的图零影响。
            _rej_ma = _r27_rejected_max_area.get(c['layout'], 0)
            if _rej_ma > ma:
                ma = _rej_ma
            c['rel_area_ratio'] = (c['area'] / ma) if ma > 0 else 0.0

        # 同 layout 同尺寸「直线矩形」计数（条件D：套图小页框重复排版判定）
        _rect_size_count = {}
        for _c in all_candidates:
            if _c.get('type') == '直线矩形':
                _rk = (_c['layout'], round(_c['width']), round(_c['height']))
                _rect_size_count[_rk] = _rect_size_count.get(_rk, 0) + 1

        # R30 条件D 内容证据：框内**严格内部**完全包含的实体数（惰性缓存 per layout）。
        # 口径与条件 H 的实体收集一致（同实体类型白名单），但判定窗口为边界内缩
        # COND_D_INNER_TOL——排除候选自身 4 条边线（边线 bbox 落在候选边界上，
        # ±1 容差会误收，导致"空框也有 4 个实体"）。惰性缓存：D 候选通常极少，
        # 无 D 候选的图零开销。
        COND_D_MIN_ENTITIES = 12   # 框内实体数下限（电子称真页框 517/485 vs 坡道大样内容框 ≤1）
        COND_D_INNER_TOL = 1.0     # 严格内缩容差（mm）
        _cond_d_ent_bbs = {}

        def _cond_d_entity_count(c):
            _ln = c['layout']
            if _ln not in _cond_d_ent_bbs:
                if _ln == '模型空间':
                    _lay = msp
                else:
                    try:
                        _lay = doc.layouts.get(_ln[3:-1] if _ln.startswith('布局 "') else _ln)
                    except Exception:
                        _lay = None
                _bbs = []
                if _lay is not None:
                    for ent in _lay:
                        _t = ent.dxftype()
                        if _t not in ('LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE',
                                      'INSERT', 'ELLIPSE', 'SPLINE', 'HATCH', 'SOLID',
                                      'TEXT', 'MTEXT', 'DIMENSION'):
                            continue
                        try:
                            if _t == 'LINE':
                                _bb = (min(ent.dxf.start.x, ent.dxf.end.x),
                                       min(ent.dxf.start.y, ent.dxf.end.y),
                                       max(ent.dxf.start.x, ent.dxf.end.x),
                                       max(ent.dxf.start.y, ent.dxf.end.y))
                            else:
                                _bb4 = get_entity_bbox(ent, doc)
                                _bb = None if _bb4 is None else tuple(_bb4[:4])
                        except Exception:
                            continue
                        if _bb is not None:
                            _bbs.append(_bb)
                _cond_d_ent_bbs[_ln] = _bbs
            _x1, _y1, _x2, _y2 = c['bbox']
            _tol = COND_D_INNER_TOL
            _n = 0
            for _bx1, _by1, _bx2, _by2 in _cond_d_ent_bbs[_ln]:
                if (_bx1 >= _x1 + _tol and _by1 >= _y1 + _tol and
                        _bx2 <= _x2 - _tol and _by2 <= _y2 - _tol):
                    _n += 1
                    if _n >= COND_D_MIN_ENTITIES:
                        break
            return _n

        def is_frame_like(c):
            if not (FRAME_RATIO_MIN <= c['ratio'] <= FRAME_RATIO_MAX):
                return False
            # 同 layout 同尺寸「直线矩形」出现次数预统计（条件D 用）：
            # 多尺度"套图"里小页框面积可能远小于最大图框（rel 只有 2~3% 过不了
            # 条件C），但同尺寸 ≥2 份说明是刻意重复排版的一页（电子称皮带输送系统：
            # 14894×10531 页框 ×2，rel=2.9% 被 C 拒）；真页框以外极少有"同尺寸
            # √2 比例 + 四边完整 LINE"的矩形刻意重复 ≥2 次。
            if c['type'] == '直线矩形':
                _rk = (c['layout'], round(c['width']), round(c['height']))
                _rect_same_size = _rect_size_count.get(_rk, 0)
            else:
                _rect_same_size = 0
            # 异常候选过滤：area_ratio 显著 > 1.0 → 候选面积超过 layout 总面积，
            # 不可能是真图框（必是计算异常或外包络伪候选）。
            # 场景：一层平面图3.25 的 374988×144477 巨块（INSERT 块 A$C6BED430F）
            #   area_ratio=8296%——calculate_layout_total_bbox 在 fast 模式下不算
            #   INSERT 块参照内部内容，layout_total 被算小，area_ratio 爆表。该巨块
            #   作为伪候选嵌套剔除了 11 个 841×594 真图框（去重阶段）。area_ratio>1.0
            #   是计算异常的可靠信号——真图框 area_ratio 必 ≤ 100%（不可能超过它
            #   所在 layout 的总面积）。雅安/澜山 area_ratio 都 < 100%，不受影响。
            #   容差 0.1%：图框面积恰等于 layout 总面积时（图框即 layout 最大外框），
            #   候选面积(polygon_area)与 layout_total(ezdxf bbox)两条路径的浮点末位差
            #   可能让 area_ratio 微超 1.0（支架.dwg 外框 594×420=layout 外框，
            #   249479.99999999994 / 249479.9999999999 > 1.0 被误杀 → 输出内框
            #   574×400）。真实异常（巨块 8296%）远超 0.1%，不受影响。
            if c['area_ratio'] > 1.0 + 0.001:
                return False
            # 长宽比约束（条件B/C 共用基础）：
            #   _near_sqrt2：长宽比接近 √2（±10%）——条件B 用，
            #     过滤家具/设备外框等小矩形（2000×5050 长宽比2.525 等）。
            #     注：曾尝试收紧到 ±3% 挡一层平面图模型空间 450×600（长宽比1.3333 偏差
            #     5.7%），但会连带把澜山 730×540 之类装饰框挡出 frame_like，导致外包络
            #     76736×28029 的包裹识别失去"第二尺寸组"证据而误保留 → 已回滚 ±10%，
            #     450×600 类误判改走 rel_area 约束（见条件B）。
            #   _at_least_sqrt2：长宽比 ≥ √2×0.95（≈1.343）——条件C 用，
            #     允许加长版图框（长宽比 > √2，如 A4 加长版 297×525.5 长宽比1.769），
            #     过滤接近正方形的误判候选（38200×40550 长宽比1.06、39400×31850 长宽比1.24）。
            #     真图框（标准+加长版）长宽比都 ≥ √2：雅安1.4158/1.4143、澜山12fas1.4141、
            #     841×594=1.4143、297×525.5=1.769。0.95 容差（原 0.99≈1.400）放宽到约 1.343：
            #     图框块若被按非等比缩放插入，真实 bbox 长宽比会偏离 √2 但仍属图纸框
            #     （效果图.dwg A$C5242259E 图框块 ×3 缩放后 ratio 1.388——与 √2 偏差仅 1.9%，
            #     被 1.400 门槛以 0.9% 之差误拒 → 漏 3 张）；1.343 仍远高于近正方形
            #     误判（1.06/1.24/1.33），不损失原有过滤能力。
            _SQRT2 = 2 ** 0.5
            _near_sqrt2 = abs(c['ratio'] - _SQRT2) / _SQRT2 <= 0.10
            _at_least_sqrt2 = c['ratio'] >= _SQRT2 * 0.95
            # 条件A：面积占比达标
            if c['area_ratio'] >= 0.15:
                c['_via'] = 'A'
                return True
            # 条件 C：相对面积达标（跟单位无关，处理多图框同 layout 场景）
            #   必须在条件 B 之前：雅安类图纸短边超 2000 过不了B，但相对面积能过C。
            #   附加约束：长宽比 ≥ 下限（直线矩形 1.10 / 其余含闭合多段线·块·降级 √2×0.95≈1.343）
            #   且 ≤ 2.5（上限防"内容块 bbox"冒充图框——机械/总装图里大块内容的外形
            #   包围盒常是 3~4 倍细长比，如电子称皮带输送系统 A$C67EE090B
            #   127686×34157 长宽比 3.74 靠 rel=80% 过 C 被误收；真图纸页框长宽比
            #   通常 ≤ ~2.1（A 系 + 常用加长），2.5 留足余量）。
            #   下限仅对「直线矩形」放宽（P1 严格矩形：四边各有完整 LINE 覆盖，是刻意
            #   画出的框，几乎无"内容轮廓冒充"风险）——闭合多段线不放宽：矩形度≥0.92
            #   仍拦不住"内容区/平面外框画成闭合矩形"（世欧澜山 45641×55212 ratio 1.21
            #   闭合多段线曾作 layout 最大候选包裹剔除 2 个真页框，fc 15→14）。
            if (c['rel_area_ratio'] >= REL_AREA_THRESHOLD
                    and c['ratio'] <= 2.5
                    and ((c['type'] == '直线矩形' and c['ratio'] >= LINE_RECT_C_RATIO_MIN)
                         or (c['type'] != '直线矩形' and _at_least_sqrt2))):
                c['_via'] = 'C'
                return True
            # 条件B：显式检测 + 短边在合理范围（绕过面积占比，适用于密集几何场景）
            #   + 尺寸规整：宽高都接近整数，过滤墙线交错产生的非整数闭合多段线轮廓
            #   + 长宽比接近 √2（±10%）
            #   + 显式类型（直线矩形/闭合多段线）都要求相对面积 ≥10%：若连"同 layout
            #     最大候选的 10%"都不到，只是空间里众多小矩形之一，不是图框（一层平面图
            #     模型空间 450×600 直线矩形、新古典式 1185×830×2 闭合多段线——主图框
            #     下方的孤立小矩形，rel 0.3%，都因此被过滤）。真图框通常是所在空间
            #     最大候选或占比可观，不受影响。
            #     历史注：曾豁免闭合多段线（730×540 等装饰框需留在 frame_like 给澜山
            #     76736 外包络当"第二尺寸组"证据）——现外包络剔除已有"单尺寸组实例
            #     ≥3 判拼版"规则兜底，不再依赖小装饰框的陪跑角色。
            if c['type'] in EXPLICIT_TYPES and MIN_FRAME_SHORT_SIDE <= c['short_side'] <= MAX_FRAME_SHORT_SIDE:
                if (abs(c['width'] - round(c['width'])) <= SIZE_ROUNDNESS_EPS and
                        abs(c['height'] - round(c['height'])) <= SIZE_ROUNDNESS_EPS and
                        _near_sqrt2 and
                        c['rel_area_ratio'] >= REL_AREA_THRESHOLD):
                    c['_via'] = 'B'
                    return True
            # 条件D：同尺寸重复的 √2 直线矩形（套图小页框，绕过条件C 的 10% rel 门槛）
            #   直线矩形 + 长宽比接近 √2 + 同 layout 同尺寸 ≥2 份 + 相对面积 ≥1%
            #   + 短边 ≥500 → 判为刻意重复排版的图纸页（电子称皮带输送系统：
            #   14894×10531 页框 ×2，rel=2.9% 过不了 C 但确为真页框）。
            #   rel≥1% 排除重复表格/阵列矩形（如 1300×2000×12 rel=0.05%、1575×1100），
            #   它们不是图框却同尺寸成批出现。
            #   R30 内容证据：以上判据不再充分——坡道大样6月 三个同尺寸 4700×6000
            #   "机动车库"房间轮廓（ratio 1.2766 恰在 √2±10% 内擦线 9.73%、
            #   rel 2.26%≥1%）全过 D 误检 1 个（fc 12→13）。真页框内必有图纸内容
            #   （电子称 14894×10531×2 实测框内 517/485 个实体），重复内容轮廓内部
            #   近乎空白（实测 0/0/1 个）——框内**严格内部**（边界内缩 1mm，排除候选
            #   自身 4 条边线）完全包含实体 ≥ COND_D_MIN_ENTITIES 才判页框。
            #   阈值与条件 H 对齐；分离度极端（517 vs ≤1），12 留足余量。
            if (c['type'] == '直线矩形' and c['short_side'] >= 500
                    and _near_sqrt2 and _rect_same_size >= 2
                    and c['rel_area_ratio'] >= 0.01
                    and _cond_d_entity_count(c) >= COND_D_MIN_ENTITIES):
                c['_via'] = 'D'
                return True
            # 条件E（A+ 通道）："块模板本身就是标准图幅" + 实例按常用制图比例等比缩放。
            #   场景：同一个标准图幅图框块被以多种比例重复插入（2.8米皮带线.DWG：块
            #   A4-横-杨勇 块定义 297×210=A4，12 个实例以 ×1/×2×8/×4/×10/×20 插入，
            #   尺寸跨度 20 倍）。此时相对面积法（条件C）必败——最大实例 ×20 占了
            #   rel=100%，最小的 ×1 只剩 0.25%，10 个实例全被 10% 门槛拒（fc 3/13）。
            #   而"块定义就是 A4"这件事与缩放倍数无关：判据改为看块模板本身——
            #     ① 块定义短边命中标准 A 系列、长边 ≈ 短边×√2（块模板是标准图幅）；
            #     ② 实例尺寸 / 块定义尺寸 = k（宽高各自比值一致，即等比；兼容块被
            #        rotation 90° 插入时宽高互换），且 k 落在 COMMON_PLOT_SCALES 白名单。
            #   与条件B/C 的区别：不依赖面积占比、不依赖绝对尺寸，只看"模板标准 + 倍率合法"。
            #   挡住装饰块：家具/门窗符号块定义尺寸不在 A 系列、比例也远离 √2；自定义
            #   倍率缩放（39.05× 等）被白名单拦下。
            if c['type'] == '块参照插入':
                _bdw = c.get('block_def_w') or 0
                _bdh = c.get('block_def_h') or 0
                if _bdw > 0 and _bdh > 0:
                    _b_short = min(_bdw, _bdh)
                    _b_long = max(_bdw, _bdh)
                    _is_std_paper = any(
                        abs(_b_short - _s) <= 1.0 and abs(_b_long - _s * _SQRT2) <= 1.0
                        for _s in A_SERIES_SHORT_SIDES)
                    if _is_std_paper:
                        # 宽高分别对块定义宽高求比（两种对应关系：原向 / 旋转 90° 互换）
                        _k_ok = False
                        for _dw, _dh in ((_bdw, _bdh), (_bdh, _bdw)):
                            _kw = c['width'] / _dw
                            _kh = c['height'] / _dh
                            _kmax = max(_kw, _kh)
                            if _kmax <= 0:
                                continue
                            # 等比：两方向比值相对差 ≤ 0.5%（吸收浮点/取整噪声）
                            if abs(_kw - _kh) > _kmax * 0.005:
                                continue
                            _k = (_kw + _kh) / 2
                            # k 落在常用制图比例白名单（相对容差 1%，容纳 2.5 等非整数比例）
                            if any(abs(_k - _s) <= max(0.01, _s * 0.01)
                                   for _s in COMMON_PLOT_SCALES):
                                _k_ok = True
                                break
                        if _k_ok:
                            c['_via'] = 'E'
                            return True
            return False

        frame_like = [c for c in all_candidates if is_frame_like(c)]
        pass_feature_count = len(frame_like)

        # ---------- 同块名族"一荣俱荣"传播（2.8米皮带线 fc 3→13） ----------
        # 场景：同一个标准图框块被以多种比例重复插入，尺寸跨度极大（2.8米皮带线：
        #   块 A4-横-杨勇 以 ×1 / ×2×8 / ×4 / ×10 / ×20 共 12 次插入，插入后尺寸
        #   297×210 ~ 5940×4200）。特征筛选是"逐实例"判定的，小尺度实例在相对面积法
        #   （条件C）下必然吃亏：即使 ×20 那张已确认是图框，×1 那张的 rel 也只有
        #   0.25%。但"这个块是图框"其实是一个整体事实——同一个块画了 12 次，没有理由
        #   只有最大的那张算图框。故按块名分组做一次传播：族内只要有 ≥1 个实例通过
        #   既有判定（A/B/C/D/A+），整族一并放行。
        #   与条件E（A+）的分工：A+ 看"块模板是不是标准图幅"（不依赖族内是否已有种子，
        #   可兜住"整族都是小尺度、连最大实例也过不了原判定"的情形）；本传播则适用于
        #   自定义尺寸图框块（块定义非 A 系列，但族内大尺寸实例已靠条件A/C 通过）——
        #   例如效果图.dwg 的 A$C5242259E 图框块（17098×12319，ratio 1.388）。
        #   防误传播约束：族内**所有**实例的长宽比都须接近 √2（±10%）——装饰块阵列
        #   （柱网/门窗/家具，比例远离 √2）不会被连带放行；同类族里出现一个比例异常
        #   的实例即整族放弃传播（保守取向，宁可漏收不可误收）。
        _SQRT2_FAMILY = 2 ** 0.5   # 纸张框比例基准，供族内一致性校验
        _seed_names = {c.get('block_name') for c in frame_like
                       if c.get('type') == '块参照插入' and c.get('block_name')}
        if _seed_names:
            _block_family = {}
            for c in all_candidates:
                if c.get('type') == '块参照插入' and c.get('block_name'):
                    _block_family.setdefault(c['block_name'], []).append(c)
            _in_frame_like = {id(c) for c in frame_like}
            _propagated_total = 0
            for _bn in _seed_names:
                _fam = _block_family.get(_bn, [])
                if len(_fam) < 2:
                    continue  # 单实例族无"重复排版"语义，不做传播
                if not all(abs(c['ratio'] - _SQRT2_FAMILY) / _SQRT2_FAMILY <= 0.10
                           for c in _fam):
                    continue  # 族内存在比例异常的实例 → 整族放弃（防装饰块误传播）
                _add = [c for c in _fam if id(c) not in _in_frame_like]
                if not _add:
                    continue
                frame_like.extend(_add)
                _propagated_total += len(_add)
                _sizes = sorted({f"{c['width']:.0f}x{c['height']:.0f}" for c in _add})
                safe_log(f"  [同块名族传播] {_bn}（{len(_fam)} 个实例，均已过种子判定）"
                         f" → 补入 {len(_add)} 个候选：{', '.join(_sizes)}")
            if _propagated_total:
                pass_feature_count = len(frame_like)

        # ---------- 条件G：同模板缩放套图救援（2026-09-14，胜利公寓２ 4→6） ----------
        # 场景：6 个图框是同一模板按 1×/1:2.5/1:4/1:4.46 四种出图比例排版，长宽比
        #   全部精确等于 1.4257。特征判定是"逐实例"的，小尺寸实例必然吃亏：
        #   rel 分母是最大的图框 → 26305×18450 rel=100%、10522×7380 rel=16% 过条件C，
        #   但 6576×4613 rel=6.25%、5892×4133 rel=5.02% <10% 被拒；短边 4613/4133
        #   超 2000 条件B 结构性不可达；area_ratio 0.66%/0.53% <15% 条件A 不可达；
        #   尺寸互不相同（同尺寸≥2 的条件D 也不适用）→ 全通道否决漏检 2 张。
        # 判据（两个信号同时成立才救援）：
        #   ① 结构签名：候选是直线矩形且带「标题栏条纹」（右侧/左侧 25% 内两条
        #      全高竖线围出带宽 5%~25%×框宽的竖带、带内横向分隔线 ≥2 条）——
        #      这是标题栏的典型画法，decoy 校验：同图 10 个房间/构件矩形全部不命中；
        #   ② 同模板：长宽比与同 layout 内某个已入选「直线矩形」图框一致（±1%）——
        #      同一模板不同出图比例，比例不会变。
        # 与同块名族传播的分工：那条管"块参照"族（同块名多次插入），本条管
        #   "直线矩形画的套图"（无块、纯线段，大小实例尺寸无公度，4.46 倍非整数倍）。
        _COND_G_ENABLED = os.environ.get('FRAME_PARSER_NO_COND_G') != '1'
        if _COND_G_ENABLED and frame_like:
            _sel_rect_ratios = {c['ratio'] for c in frame_like
                                if c['type'] == '直线矩形'}
            if _sel_rect_ratios:
                _in_fl = {id(c) for c in frame_like}
                _g_added = []
                for c in all_candidates:
                    if id(c) in _in_fl or c['type'] != '直线矩形':
                        continue
                    if not c.get('title_stripe'):
                        continue
                    if not any(abs(c['ratio'] - _r) / _r <= 0.01
                               for _r in _sel_rect_ratios):
                        continue
                    frame_like.append(c)
                    _in_fl.add(id(c))
                    _g_added.append(c)
                if _g_added:
                    pass_feature_count = len(frame_like)
                    _sizes_g = ', '.join(
                        '%.0fx%.0f' % (c['width'], c['height']) for c in _g_added)
                    safe_log(f"  [条件G·同模板套图救援] 补入 {len(_g_added)} 个"
                             f"带标题栏条纹的同比例直线矩形: {_sizes_g}")

        # ---------- 条件H：主导模板比例 + 内容丰富救援（2026-09-14，春风公寓2 16→21） ----------
        # 场景：21 个图框是同一模板按四种出图比例排版，长宽比全部精确等于 1.4444。
        #   右侧 5 张小图框（17472×12096 / 14560×10080×3 / 10735×7432）逐实例判定
        #   全通道否决：rel 3.7~9.9% <10%（条件C 拒，分母是最大图框）、area_ratio
        #   0.1~0.4% <15%（条件A 拒）、短边 7432~12096 >2000（条件B 结构性不可达）、
        #   非直线矩形（条件D 不适用）、标题栏只有一条全高分隔线没有横向分隔线
        #   （条件G 条纹签名不命中）→ 漏检 5 张。
        # 判据（三个信号同时成立才救援）：
        #   ① 主导模板比例：候选长宽比与同 layout 内 ≥3 个已入选图框共享的比例一致
        #      （±1%）——同一模板缩放出图，比例是指纹、不会变；主导门槛 ≥3 排除
        #      "只有一个大框"的孤立场景；
        #   ② 内容丰富：候选 bbox 内完全包含 ≥ COND_H_MIN_ENTITIES 个实体——真页面
        #      框内必有大量图纸内容（春风公寓2 实测 52~839 个），而比例恰好落在
        #      1.4444±1% 的窗户 decoy（1277×882，ratio 1.4478）框内只有自身边线
        #      （实测 5~6 个）→ 被内容数干净排除；
        #   ③ 显式矩形类型（闭合多段线/直线矩形）——块参照族已有"同块名族传播"覆盖。
        #   救援对象的内框（虚线层 17203/14336/10570 等）同样命中（内容相同、比例
        #   1.455 在 1.4444±1% 内），随后由嵌套去重"留大剔小"收掉，不影响计数。
        # 与条件G 的分工：G 靠"标题栏条纹"结构签名（条纹带+横向分隔线），H 靠
        #   "比例指纹+框内内容量"——两签名互补，覆盖标题栏画法不同的套图。
        _COND_H_ENABLED = os.environ.get('FRAME_PARSER_NO_COND_H') != '1'
        COND_H_MIN_ENTITIES = 12   # 框内完全包含实体数下限（春风公寓2: 真框 52+ vs decoy ≤11）
        COND_H_DOMINANT_MIN = 3    # 主导比例至少 shared by 3 个已入选图框
        # 比例聚类/匹配容差 ±0.5%：模板拷贝（复制+缩放）的比例精确到 1e-6 量级，
        # 0.5% 已足够宽松；更重要的是**防止近邻比例混簇**——新古典式 21728×15365
        # （ratio 1.4141）与其内框 21302×14939（ratio 1.4259，同一页的均匀内缩重复
        # 画法，绝对内缩使 ratio 偏移 0.83%）在 ±1% 下会合并成"伪主导簇"，导致
        # 家具详图边框 3900×2720（ratio 1.4338，距 1.4259 仅 0.55%）被误救（fc 2→3）。
        # 收紧到 ±0.5% 后两簇各自凑不满 ≥3 → 主导不存在 → 该图 H 自动失效。
        COND_H_RATIO_TOL = 0.005
        if _COND_H_ENABLED and frame_like:
            _dominant = {}   # layout -> {ratio, ...}
            for _ln in {c['layout'] for c in frame_like}:
                _rs = [c['ratio'] for c in frame_like if c['layout'] == _ln]
                for _r in set(_rs):
                    if sum(1 for _x in _rs if abs(_x - _r) / _r <= COND_H_RATIO_TOL) >= COND_H_DOMINANT_MIN:
                        _dominant.setdefault(_ln, set()).add(_r)
            if _dominant:
                _in_fl_h = {id(c) for c in frame_like}
                _h_cands = []
                for c in all_candidates:
                    if id(c) in _in_fl_h or c['type'] not in ('闭合多段线', '直线矩形'):
                        continue
                    _drs = _dominant.get(c['layout'])
                    if not _drs:
                        continue
                    if not any(abs(c['ratio'] - _r) / _r <= COND_H_RATIO_TOL for _r in _drs):
                        continue
                    _h_cands.append(c)
                if _h_cands:
                    # 每个 layout 一次性收集实体 bbox（O(N)），再对少量候选做包含计数
                    _ent_bbs = {}
                    for _ln in {c['layout'] for c in _h_cands}:
                        if _ln == '模型空间':
                            _lay = doc.modelspace()
                        else:
                            try:
                                _lay = doc.layouts.get(_ln[3:-1] if _ln.startswith('布局 "') else _ln)
                            except Exception:
                                continue
                        _bbs = []
                        for ent in _lay:
                            _t = ent.dxftype()
                            if _t not in ('LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE',
                                          'INSERT', 'ELLIPSE', 'SPLINE', 'HATCH', 'SOLID',
                                          'TEXT', 'MTEXT', 'DIMENSION'):
                                continue
                            try:
                                if _t == 'LINE':
                                    _bb = (min(ent.dxf.start.x, ent.dxf.end.x),
                                           min(ent.dxf.start.y, ent.dxf.end.y),
                                           max(ent.dxf.start.x, ent.dxf.end.x),
                                           max(ent.dxf.start.y, ent.dxf.end.y))
                                else:
                                    _bb4 = get_entity_bbox(ent, doc)
                                    _bb = None if _bb4 is None else tuple(_bb4[:4])
                            except Exception:
                                continue
                            if _bb is not None:
                                _bbs.append(_bb)
                        _ent_bbs[_ln] = _bbs
                    _h_added = []
                    # 防护栏：已被某个"已入选"图框完全包住的候选不救援——嵌套去重
                    # 反正会把它剔掉，救入只是噪音。春风公寓2 实证：1280×1850 虚线
                    # 窗套（ratio 1.4453 命中主导比例、框内 12+ 实体）在已检出的
                    # 26000×18000 框内，若无此护栏会被救入（全靠去重兜底）。
                    # 注：右侧 4 个内框（17203/14336×3/10570）不受影响——它们的外框
                    # 此时尚未入选，不在"已入选"之列。
                    _fl_by_lay = {}
                    for _fc in frame_like:
                        _fl_by_lay.setdefault(_fc['layout'], []).append(_fc)
                    for c in _h_cands:
                        _skip = False
                        for _fc in _fl_by_lay.get(c['layout'], ()):
                            _fb = _fc['bbox']
                            if (c['bbox'][0] >= _fb[0] - 1 and c['bbox'][1] >= _fb[1] - 1 and
                                    c['bbox'][2] <= _fb[2] + 1 and c['bbox'][3] <= _fb[3] + 1):
                                _skip = True
                                break
                        if _skip:
                            continue
                        _bbs = _ent_bbs.get(c['layout'])
                        if _bbs is None:
                            continue
                        _x1, _y1, _x2, _y2 = c['bbox']
                        _n = 0
                        for _bx1, _by1, _bx2, _by2 in _bbs:
                            if (_bx1 >= _x1 - 1 and _by1 >= _y1 - 1 and
                                    _bx2 <= _x2 + 1 and _by2 <= _y2 + 1):
                                _n += 1
                                if _n >= COND_H_MIN_ENTITIES:
                                    break
                        if _n >= COND_H_MIN_ENTITIES:
                            frame_like.append(c)
                            _in_fl_h.add(id(c))
                            _h_added.append(c)
                    if _h_added:
                        pass_feature_count = len(frame_like)
                        _sizes_h = ', '.join(
                            '%.0fx%.0f' % (c['width'], c['height']) for c in _h_added)
                        safe_log(f"  [条件H·主导比例内容救援] 补入 {len(_h_added)} 个"
                                 f"同主导比例且框内内容丰富的矩形: {_sizes_h}")

        # ---------- 条件I：表格内容页救援（2026-09-14） ----------
        # 场景（香槟半岛 实际 27 / 检出 26）：「图纸目录」页画在图框条带下方——
        #   闭合多段线外框 19945×15703，内部是一张规则表格（大量横线行 + 竖向
        #   列分隔线 + 目录文字），ratio 1.2703 非 √2、非主导比例 1.418、rel 面
        #   积小、无标题栏条纹 —— 条件A~H 全拒。
        # 信号（通用）：图纸目录 / 材料表 / 设计说明这类"表格页"的共同结构是
        #   外框内部存在**密集的水平表格行线 + 竖向列分隔线**。表格线常按单元格
        #   分段绘制（目录框贯穿横线仅 3 条，但框内横线总数 59 / 竖线 13），
        #   故按"框内横竖线总数"计数而非要求贯穿。立面图的楼层线数量远达不到
        #   该密度；装饰性矩形内部不会有几十条正交线。
        # 判据（全部满足才救）：
        #   ① 宽、高 ≥ 5000mm（图框级，防家具/符号块）
        #   ② ratio ∈ [FRAME_RATIO_MIN, FRAME_RATIO_MAX]（是"页"的形状）
        #   ③ 未被任何已入选图框完全包住
        #   ④ 框内水平 LINE ≥ 30 条（表格行）
        #   ⑤ 框内竖直 LINE ≥ 6 条（列分隔）
        _COND_I_ENABLED = os.environ.get('FRAME_PARSER_NO_COND_I') != '1'
        if _COND_I_ENABLED and frame_like:
            # 预收集各 layout 对象（表格线扫描用）
            _lay_objs = {'模型空间': msp}
            for _lname in {c['layout'] for c in all_candidates}:
                if _lname != '模型空间' and _lname not in _lay_objs:
                    try:
                        _lay_objs[_lname] = doc.layouts.get(_lname.strip('"').strip('“”'))
                    except Exception:
                        pass

            def _table_lines_in(_bb, _lay):
                """统计完全落在 _bb 内部的水平/竖直 LINE 数（不分段长度）"""
                _x1, _y1, _x2, _y2 = _bb
                _tol = max(_x2 - _x1, _y2 - _y1) * 0.005   # 正交容差 0.5%
                _nh = _nv = 0
                _obj = _lay_objs.get(_lay)
                if _obj is None:
                    return 0, 0
                for _e in _obj:
                    if _e.dxftype() != 'LINE':
                        continue
                    try:
                        _sx, _sy = _e.dxf.start.x, _e.dxf.start.y
                        _ex, _ey = _e.dxf.end.x, _e.dxf.end.y
                    except Exception:
                        continue
                    if not (_x1 <= _sx <= _x2 and _x1 <= _ex <= _x2 and
                            _y1 <= _sy <= _y2 and _y1 <= _ey <= _y2):
                        continue
                    if abs(_sy - _ey) <= _tol:          # 水平线
                        _nh += 1
                    elif abs(_sx - _ex) <= _tol:        # 竖直线
                        _nv += 1
                return _nh, _nv

            _in_fl_i = {id(c) for c in frame_like}
            _i_added = []
            for _c in all_candidates:
                if id(_c) in _in_fl_i:
                    continue
                if _c['width'] < 5000 or _c['height'] < 5000:
                    continue
                if not (FRAME_RATIO_MIN <= _c['ratio'] <= FRAME_RATIO_MAX):
                    continue
                if any(_c['bbox'][0] >= _f['bbox'][0] - 1 and _c['bbox'][1] >= _f['bbox'][1] - 1 and
                       _c['bbox'][2] <= _f['bbox'][2] + 1 and _c['bbox'][3] <= _f['bbox'][3] + 1
                       for _f in frame_like):
                    continue   # 被已入选框包住：是内部内容，不救
                _nh, _nv = _table_lines_in(_c['bbox'], _c['layout'])
                if _nh >= 30 and _nv >= 6:
                    _c['rescued_by'] = 'table_page'
                    frame_like.append(_c)
                    _in_fl_i.add(id(_c))
                    _i_added.append(_c)
                    safe_log(f"   [条件I·表格内容页救援] {_c['type']} "
                             f"{_c['width']:.0f}×{_c['height']:.0f}"
                             f"（框内横线 {_nh} 条 / 竖线 {_nv} 条，判为表格页）")
            if _i_added:
                pass_feature_count = len(frame_like)

        # ---------- 孤证复核（2026-09-14，一层.dwg 0 图框误检 4 个） ----------
        # 场景：整张图没有任何图框（纯平面图 + 大样标注）。条件C 的 rel 分母是
        # "layout 内最大候选"——当最大候选本身就是标注块（一层.dwg 电梯井道大样
        # 外轮廓 12240×8770），其余 3 个标注块/矩形（7280×3440、3290×4775、
        # 5160×2730）相对它都 ≥10%，全部经条件C 混入。
        # 反向信号（通用）：真图框几乎从不"孤证"——同一张图里必能找到互证：
        #   ① 同 layout 存在同尺寸副本（同模板拷贝，≥2 份）；
        #   ② 存在同比例兄弟（±1%，同模板不同比例缩放，≥2 份）；
        #   ③ 自带标题栏条纹（条件G 签名）；
        #   ④ 内部完全包含带标题栏条纹的子候选（标题栏在框内 = 真页框。罗马都市
        #     21121×14813 图框内含 174 个子候选、其中 2 个带条纹；一层.dwg 电梯
        #     大样外轮廓内含候选 0 条纹）；
        #   ⑤ 块名含"图框/frame"（设计者显式声明的图框块）；R29 扩词：图幅代号
        #     A0~A9 同为图幅命名证据（13013-11-AW-FP.dwg 独立图纸块名 A1+0.25，
        #     A1 加长 1/4 图幅——全图唯一无副本、ratio 无兄弟、图签条纹未检出，
        #     六通道 score=0 被误杀。词边界防 A$C... 匿名块名与 A31005 类编号）。
        # 而标注类轮廓（大样外框、图例框、说明框）通常尺寸比例各不相同、无条纹。
        # 判据（全部满足才剔除）：
        #   ① 仅经条件C 通过（_via == 'C'。A 是面积主导、B/D/E 本身就是强证据）；
        #   ② area_ratio < 0.05（在 layout 里占比很小——大图框经 A 通道早走，
        #     rel≥10% 但 area_ratio≥5% 的候选保留，避免误伤依赖纯 C 的真页框）；
        #   ③ 无任何互证（同尺寸副本 / 同比例兄弟 / 标题栏条纹均无）。
        _SOLO_RECHECK_ENABLED = os.environ.get('FRAME_PARSER_NO_SOLO_RECHECK') != '1'
        # ---------- 孤证复核·证据评分制（2026-09-23 试点：等价重构，行为不变） ----------
        # R27 回归期间的教训：在"互证任一成立即救援"的布尔结构上加资格限制
        # （area_ratio/尺寸/类型下限）两次被全量插桩数据证伪——32 张基线 413 条
        # 救援样本证明尘埃与真框在单维特征上不可分（420mm 的 A3 真框 < 尘埃
        # 957mm；滨江 11093×8535×15 弱+弱真框副本对 vs 一层.dwg 960×2400 门副本）。
        # 未来新案例的正确演进路径是"调权重 / 加特征"，而不是"加 if 分支"。
        # 故把六通道布尔逻辑重写为加权评分：score = Σ wᵢ·fᵢ（fᵢ∈{0,1}），
        # score < _SOLO_SCORE_THRESHOLD → 孤证剔除。
        # 等价性：初始权重全部 1.0、阈值 0.5 → score>0 ⟺ 原 _corr=True，
        # 与"任一互证即救、无互证则剔"逐位一致（证据独立累加只增大分值，
        # 不改变保留结论；原逻辑的短路语义在正权重下与求和语义等价）。
        # 后续按全量插桩数据分级调优：如 ③④⑤ 定为强证据（权重 ≥ 阈值
        # 单独成立即救）、①②⑥ 依伙伴类型/副本数细分权重——只改本表。
        _SOLO_SCORE_THRESHOLD = 0.5
        _SOLO_WEIGHTS = {
            'same_size_copy':     1.0,  # ① 同 layout 同尺寸副本（round 宽高一致）
            'same_ratio_sibling': 1.0,  # ② 同 layout 同比例兄弟（±1%）
            'title_stripe':       1.0,  # ③ 自带标题栏条纹
            'stripe_inside':      1.0,  # ④ 内含带条纹子候选（标题栏在框内）
            'name_declared':      1.0,  # ⑤ 块名含 图框/frame/图幅代号(A0~A9)
            'nested_frame':       1.0,  # ⑥ 内含不同尺寸通过候选（嵌套内外框）
        }
        # ⑤ 图幅代号：A0~A9 独立词。(?<![a-z0-9]) 防匿名块名 A$C6A6C4DA6
        # （其 a6 前是字母数字无词边界）；(?![0-9]) 防 A31005 类图纸编号误伤。
        _SHEET_SIZE_NAME_RE = re.compile(r'(?<![a-z0-9])a[0-9](?![0-9])')
        _SOLO_DEBUG = os.environ.get('FRAME_PARSER_SOLO_DEBUG') == '1'

        def _solo_evidence_score(_c):
            """孤证候选的证据评分：返回 (score, hits)，hits 为命中证据明细。"""
            _score = 0.0
            _hits = []
            _lk = _c['layout']
            _cr, _sr = round(_c['width']), round(_c['height'])
            for _o in frame_like:                       # ① 同尺寸副本
                if _o is _c or _o['layout'] != _lk:
                    continue
                if round(_o['width']) == _cr and round(_o['height']) == _sr:
                    _score += _SOLO_WEIGHTS['same_size_copy']
                    _hits.append(f"①同尺寸 {_o['type']}")
                    break
            for _o in frame_like:                       # ② 同比例兄弟（±1%）
                if _o is _c or _o['layout'] != _lk:
                    continue
                if abs(_o['ratio'] - _c['ratio']) / _c['ratio'] <= 0.01:
                    _score += _SOLO_WEIGHTS['same_ratio_sibling']
                    _hits.append(f"②同比例 {_o['width']:.0f}x{_o['height']:.0f}"
                                 f"({_o['type']})")
                    break
            if _c.get('title_stripe'):                  # ③ 标题栏条纹
                _score += _SOLO_WEIGHTS['title_stripe']
                _hits.append('③标题栏条纹')
            for _o in all_candidates:                   # ④ 内含带条纹子候选
                if _o is _c or not _o.get('title_stripe'):
                    continue
                _ob = _o['bbox']
                if (_ob[0] >= _c['bbox'][0] - 1 and _ob[1] >= _c['bbox'][1] - 1 and
                        _ob[2] <= _c['bbox'][2] + 1 and _ob[3] <= _c['bbox'][3] + 1):
                    _score += _SOLO_WEIGHTS['stripe_inside']
                    _hits.append(f"④内含条纹子 {_o['width']:.0f}x{_o['height']:.0f}")
                    break
            _bn5 = (_c.get('block_name') or '').lower()  # ⑤ 块名显式声明
            _m5 = _SHEET_SIZE_NAME_RE.search(_bn5)
            if '图框' in _bn5 or 'frame' in _bn5:
                _score += _SOLO_WEIGHTS['name_declared']
                _hits.append('⑤块名声明')
            elif _m5:
                _score += _SOLO_WEIGHTS['name_declared']
                _hits.append(f"⑤块名图幅({_m5.group(0).upper()})")
            for _o in frame_like:                       # ⑥ 嵌套内外框
                if _o is _c or _o['layout'] != _lk:
                    continue
                if round(_o['width']) == _cr and round(_o['height']) == _sr:
                    continue  # 同尺寸副本是重复画法，不算嵌套互证
                _ob = _o['bbox']
                if (_ob[0] >= _c['bbox'][0] - 1 and _ob[1] >= _c['bbox'][1] - 1 and
                        _ob[2] <= _c['bbox'][2] + 1 and _ob[3] <= _c['bbox'][3] + 1 and
                        _o['area'] < _c['area']):
                    _score += _SOLO_WEIGHTS['nested_frame']
                    _hits.append(f"⑥内含 {_o['width']:.0f}x{_o['height']:.0f}"
                                 f"({_o['type']})")
                    break
            return _score, _hits

        if _SOLO_RECHECK_ENABLED and frame_like:
            _solo_rm = []
            for _c in frame_like:
                if _c.get('_via') != 'C' or _c['area_ratio'] >= 0.05:
                    continue
                _score, _hits = _solo_evidence_score(_c)
                if _score < _SOLO_SCORE_THRESHOLD:
                    _solo_rm.append(_c)
                elif _SOLO_DEBUG:
                    safe_log(f"    [孤证评分] {_c['width']:.0f}x{_c['height']:.0f} 保留 "
                             f"score={_score:.1f}（{'；'.join(_hits)}）")
            if _solo_rm:
                _rm_desc = ', '.join('%.0fx%.0f' % (c['width'], c['height']) for c in _solo_rm)
                _solo_ids = {id(c) for c in _solo_rm}
                frame_like = [c for c in frame_like if id(c) not in _solo_ids]
                all_candidates = [c for c in all_candidates if id(c) not in _solo_ids]
                safe_log(f"  [孤证复核] 剔除 {len(_solo_rm)} 个仅凭相对面积混入、"
                         f"无同尺寸/同比例/标题栏互证的候选: {_rm_desc}")

        # ---------- 外层包裹框识别（优化点1） ----------
        # 场景：几张图框外面又画了一个大框，把多个图框包在里面。这种大框会被当成图框，
        # 且去重时会把内部真图框当嵌套物剔掉（去重方向"留大剔小"正好反了）。
        # 判据：某候选 X 内部完全包含 ≥ MIN_WRAPPED_FRAMES 个其他图框候选 → X 是外包络，剔除。
        # 必须在去重之前执行：此时内部小框还没被当嵌套物剔除，能统计到包含数量。
        # 真图框内部最多 1 个标题栏小框，故 N=2 能干净区分"外包络"与"正常嵌套"。
        MIN_WRAPPED_FRAMES = 2  # 大框内含 ≥2 个图框候选即判定为外包络
        # 单尺寸组实例数 ≥ 该值也判为外包络：外包络包住 N 个同尺寸图框整齐排列时，
        # 独立尺寸组可能只有 1 个（澜山 76736×28029 ≈3×31500×22275，包住 3 张
        # 12fas 图框的整版大框），而真图框"内含同尺寸重复画法"至多 2~3 份
        # （雅安 43031×49000/42724×48700 两种画法 = 2 份，≥3 必是拼版外框）。
        # 注意：该判定在去重前执行，此时被包住的子框尚未被嵌套剔除。
        MIN_WRAPPED_SAME_SIZE = 3
        WRAPPED_EPS = 1.0       # 坐标容差（mm），与嵌套去重一致
        # 统计"内部包含的独立图框"时，内部候选面积需 < big×此比例才计数。
        # 排除同图框的重复画法（如 84100×59400 外框 + 80600×57400 内框，面积 91% 互相嵌套），
        # 这类重复画法面积接近 big，不算独立图框。真正被外包络包住的图框面积必 <big×50%
        # （2 个图框+间隔塞进外包络，每个 <50%，否则放不下）。
        WRAP_INNER_AREA_RATIO = 0.5
        WRAP_SIZE_EPS = 0.05  # 尺寸聚类容差：宽高相对差 <5% 视为同组（同图框重复画法）

        def _is_contained(inner, outer, eps=WRAPPED_EPS):
            """inner 的 bbox 是否完全落在 outer 的 bbox 内"""
            ix1, iy1, ix2, iy2 = inner['bbox']
            ox1, oy1, ox2, oy2 = outer['bbox']
            return (ix1 >= ox1 - eps and iy1 >= oy1 - eps and
                    ix2 <= ox2 + eps and iy2 <= oy2 + eps)

        def strip_wrapping_frames(cands):
            """剔除"外层包裹框"：内部包含 ≥ MIN_WRAPPED_FRAMES 个其他图框候选的大框"""
            if len(cands) <= 2:
                return cands, 0
            # 主导模板比例（2026-09-14，春风公寓2）：同 layout 内 ≥3 个候选共享的
            # ratio（±1% 聚类）。用途：见下方"主导比例内容区边界"判定。
            _wrap_dominant = {}
            _wrap_ratios = {}
            for _c in cands:
                _wrap_ratios.setdefault(_c['layout'], []).append(_c['ratio'])
            for _ln, _rs in _wrap_ratios.items():
                _s = set()
                for _r in set(_rs):
                    # 与条件H 同口径的 ±0.5% 聚类（防近邻比例混簇，见 COND_H_RATIO_TOL 注）
                    if sum(1 for _x in _rs if abs(_x - _r) / _r <= 0.005) >= 3:
                        _s.add(_r)
                if _s:
                    _wrap_dominant[_ln] = _s
            # 按 layout 分组，组内判断包含（不同 layout 不在同一坐标系）
            layout_groups = {}
            for idx, c in enumerate(cands):
                layout_groups.setdefault(c['layout'], []).append(idx)
            remove_set = set()
            for layout_name, indices in layout_groups.items():
                if len(indices) <= 2:
                    continue
                for i in indices:
                    big = cands[i]
                    # （块参照豁免见下方 groups 计算之后——需先统计内部尺寸组）
                    # 自身为 √2±10% 的显式矩形（闭合多段线/直线矩形）→ 是"纸张边框"而非拼版外框，
                    # 不做包裹剔除，其内部内容（表格/标题栏/内圈线）交给嵌套去重收掉。
                    #   场景：泛悦国际 图纸目录.dwg——真图框是 PUB_TITLE 图层双层框
                    #   42000×29700（ratio 1.414）+ 39000×28700（ratio 1.359），内含"图纸目录
                    #   表格外框 24308×21700 + 西南院标题栏块 7000×28700"两个尺寸组，被旧规则
                    #   当外包络剔除 → 真框没了、两个内容残留成 2 个"图框"。
                    #   拼版/外包络真目标（澜山 76736×28029 ratio 2.74、一层 374988×144477
                    #   ratio 2.59）比例远离 √2，不受影响；真图框（纸张）必接近 √2。
                    _bw = max(big['width'], big['height'])
                    _bs = min(big['width'], big['height'])
                    if (_bs > 0 and big.get('type') in EXPLICIT_TYPES
                            and abs(_bw / _bs - 2 ** 0.5) / (2 ** 0.5) <= 0.10):
                        continue
                    # 收集 big 内部、面积显著小于 big 的候选（排除同图框重复画法本身的互相嵌套）
                    inners = [cands[j] for j in indices
                              if j != i and _is_contained(cands[j], big)
                              and cands[j]['area'] < big['area'] * WRAP_INNER_AREA_RATIO]
                    if not inners:
                        continue
                    # 按尺寸聚类：宽高相对差 <5% 视为同组（同图框的重复画法尺寸几乎相同），
                    # 只统计"独立尺寸组数"，避免重复画法虚增 contained。
                    #   例：84100×59400 图框内含 43031×49000 和 42724×48700（差<1%），
                    #   归同组 → contained=1，不误剔 A1 真图框。
                    #   真·外包络包 2 个不同尺寸图框 → 2 组 → contained=2 → 剔除外包络。
                    #   补充规则：单个尺寸组实例数 ≥ MIN_WRAPPED_SAME_SIZE（如澜山 76736×28029
                    #   外包络内含 15 个同尺寸 12fas 图框整齐排列）也应判为包裹框——
                    #   真图框"内含同尺寸重复画法"至多 2~3 份（雅安），≥6 份必然是
                    #   "包住一排子图框的外包络"，不该当成图框。
                    groups = []  # 每组存 [代表候选, 实例数]
                    for ic in inners:
                        placed = False
                        for g in groups:
                            if (abs(ic['width'] - g[0]['width']) / max(ic['width'], g[0]['width'], 1) < WRAP_SIZE_EPS and
                                    abs(ic['height'] - g[0]['height']) / max(ic['height'], g[0]['height'], 1) < WRAP_SIZE_EPS):
                                g[1] += 1
                                placed = True
                                break
                        if not placed:
                            groups.append([ic, 1])
                    # 块参照豁免收紧（2026-09-14，1号2号楼柱平法施工图）：原先对
                    # "块参照插入"一律豁免包裹剔除（当年理由：单页图框块内部常有
                    # 标题栏/家具小矩形，会被错判）。但"底图参照外框"同样是块——
                    # S-0-COLS 630801×2407287（占整图 76%）包住 11 个真图框，
                    # 豁免导致包裹剔除失效 → 嵌套去重"留大剔小"把真图框全吃了。
                    # 改为强证据豁免：块参照仅在"内部尺寸组弱"时豁免（组数 <3 且
                    # 无同尺寸组 ≥3）——单页图框块内部至多 1~2 个小尺寸组；底图
                    # 外框内部必有 ≥3 个同尺寸真图框（本例 126164×178350 ×5）或
                    # ≥3 个独立尺寸组。
                    if big.get('type') == '块参照插入':
                        _grp_n = len(groups)
                        _same_n = max((g[1] for g in groups), default=0)
                        if _grp_n < 3 and _same_n < MIN_WRAPPED_SAME_SIZE:
                            continue
                    # 主导比例内容区边界判定（2026-09-14，春风公寓2 16→21）：
                    #   虚线层 67627×31514 ratio 2.146 内容区边界包住 1 张 26000×18000
                    #   （ratio 1.4444 主导模板）真页框 + 其 25600×17600 内框（同一尺寸组），
                    #   旧规则"组数≥2 或 同组≥3"都不满足 → 包裹框漏剔 → 去重"留大剔小"
                    #   把真页框吃了。补判据：某尺寸组的比例命中主导模板比例（±1%）
                    #   且与外框自身比例差异 >10%（排除同框重复画法——重复画法比例必
                    #   与外框一致）→ 该外框是"内容区边界"而非图框，剔除。
                    #   decoy 校验：标题栏小框比例（竖向 2~3）不命中主导横版比例，
                    #   且面积通常 <50%×外框也不会进 inners……即便进组，其比例与主导
                    #   模板比例不同，不触发。真·加长图框（ratio 2.0 左右）内部只有
                    #   同比例重复画法，比例差 <10% 不触发。
                    _dom_hit = False
                    _drs_w = _wrap_dominant.get(layout_name, set())
                    if _drs_w and os.environ.get('FRAME_PARSER_NO_WRAP_DOM') != '1':
                        _big_r = max(big['width'], big['height']) / min(big['width'], big['height'])
                        for g in groups:
                            _gr = max(g[0]['width'], g[0]['height']) / min(g[0]['width'], g[0]['height'])
                            if (any(abs(_gr - _dr) / _dr <= 0.005 for _dr in _drs_w)
                                    and abs(_gr - _big_r) / _big_r > 0.10):
                                _dom_hit = True
                                break
                    if (len(groups) >= MIN_WRAPPED_FRAMES or
                            any(g[1] >= MIN_WRAPPED_SAME_SIZE for g in groups) or
                            _dom_hit):
                        remove_set.add(i)
                        inner_desc = "; ".join(f"{g[0]['width']:.0f}x{g[0]['height']:.0f}×{g[1]}" for g in groups)
                        safe_log(f"  [包裹框剔除] {big['layout']} | {big['width']:.1f}x{big['height']:.1f} | 独立尺寸组{len(groups)} | bbox={big['bbox']} | 组: {inner_desc}")
            if not remove_set:
                return cands, 0
            kept = [c for k, c in enumerate(cands) if k not in remove_set]
            return kept, len(remove_set)

        frame_like, wrap_removed = strip_wrapping_frames(frame_like)
        wrap_stripped_count = len(frame_like)
        if wrap_removed:
            safe_log(f"🧹 [包裹框识别] 剔除外层包裹框 {wrap_removed} 个（各含 ≥{MIN_WRAPPED_FRAMES} 个图框候选）")

        # ---------- 去重（嵌套 + IoU 重叠） ----------
        # 同一个 layout 内，逐步剔除"与更大候选高度重叠"的候选：
        #   1) 嵌套去重：候选 A 完全包含在 B 内，且面积 < B × 95% → 剔除 A
        #   2) IoU 去重：候选 A 与 B 的交并比 > 50%，且 A 面积 < B → 剔除 A
        # 不同 layout 的候选不在同一坐标系，不互相去重。
        NESTED_AREA_RATIO = 0.95  # 面积小于外框的 95% 才算"明显嵌套"
        NESTED_EPS = 1.0          # 坐标容差（mm），处理端点微小偏差
        IOU_THRESHOLD = 0.5      # 交并比阈值：> 50% 认为是同一图框的不同画法或假矩形

        def bbox_iou(a, b):
            """两个 bbox 的交并比 (Intersection over Union)"""
            ax1, ay1, ax2, ay2 = a
            bx1, by1, bx2, by2 = b
            ix1 = max(ax1, bx1)
            iy1 = max(ay1, by1)
            ix2 = min(ax2, bx2)
            iy2 = min(ay2, by2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            area_a = (ax2 - ax1) * (ay2 - ay1)
            area_b = (bx2 - bx1) * (by2 - by1)
            union = area_a + area_b - inter
            if union <= 0:
                return 0.0
            return inter / union

        def _same_size_offset_dup(c, outer):
            """同尺寸错排豁免：宽高一致（含互换）但中心错位 ≥5% 边长 → 两张独立错排图。

            场景（6、10号楼住宅户型.dwg）：同列上下两张 105800×143285 图框纯 y 向
            错位 19781mm（13.8%），IoU=75.7% > 50% 被规则2 误剔（fc 20→19）。
            真重复画法（同框画两遍/双检测路径）中心偏移 ≈0，不受影响；
            内外框（74300/72550 差 2.4%、59450/57700 差 2.9%）尺寸超 2% 容差
            不算"同尺寸"，照旧按嵌套/IoU 剔除。
            """
            cw, ch = c['width'], c['height']
            ow, oh = outer['width'], outer['height']
            if min(cw, ch) <= 0 or min(ow, oh) <= 0:
                return False
            _same = ((abs(cw - ow) <= 0.02 * max(cw, ow) and
                      abs(ch - oh) <= 0.02 * max(ch, oh)) or
                     (abs(cw - oh) <= 0.02 * max(cw, oh) and
                      abs(ch - ow) <= 0.02 * max(ch, ow)))
            if not _same:
                return False
            _mx = max(ow, oh)
            _off_x = abs((c['bbox'][0] + c['bbox'][2]) - (outer['bbox'][0] + outer['bbox'][2])) / 2.0
            _off_y = abs((c['bbox'][1] + c['bbox'][3]) - (outer['bbox'][1] + outer['bbox'][3])) / 2.0
            return max(_off_x, _off_y) / _mx >= 0.05

        def deduplicate_candidates(cands):
            """按 layout 分组，组内去除嵌套候选和高度重叠候选，保留面积最大者"""
            if len(cands) <= 1:
                return cands
            # 按 layout 分组
            layout_groups = {}
            for c in cands:
                layout_groups.setdefault(c['layout'], []).append(c)
            result = []
            for layout_name, group in layout_groups.items():
                if len(group) <= 1:
                    result.extend(group)
                    continue
                # 按面积降序排列，大框在前
                sorted_group = sorted(group, key=lambda c: c['area'], reverse=True)
                for c in sorted_group:
                    cx1, cy1, cx2, cy2 = c['bbox']
                    should_remove = False
                    hit_outer = None
                    for outer in result:
                        if outer['layout'] != layout_name:
                            continue
                        ox1, oy1, ox2, oy2 = outer['bbox']
                        # 规则1：嵌套——c 的 bbox 完全包含在 outer 内，且面积明显更小
                        #   outer 为斜放块（tilted，旋转非 90° 倍数）时跳过：其 bbox 是
                        #   旋转外接矩形（膨胀框），真实足迹只是框内斜带，bbox「包含」
                        #   ≠真实包含（6、10号楼住宅户型：斜放框 bbox 误剔了其 bbox
                        #   角落处的邻位真框 74300×42050 与 59450×42050，fc 20→18）
                        if (not outer.get('tilted') and
                                cx1 >= ox1 - NESTED_EPS and cy1 >= oy1 - NESTED_EPS and
                                cx2 <= ox2 + NESTED_EPS and cy2 <= oy2 + NESTED_EPS and
                                c['area'] < outer['area'] * NESTED_AREA_RATIO):
                            should_remove = True
                            hit_outer = outer
                            break
                        # 规则2：IoU 重叠——c 与 outer 重叠度 > 阈值，且面积不大于 outer
                        #   <= 而非 <：面积完全相等的重叠矩形（同一图框被画两遍/检测两次）
                        #   也必须去重，否则同一图框会被重复计数（IoU=1.0 却因面积相等被跳过）
                        #   处理"图框边线+内部标注线围出的部分区域假矩形"
                        if c['area'] <= outer['area']:
                            iou = bbox_iou(c['bbox'], outer['bbox'])
                            if iou > IOU_THRESHOLD and not _same_size_offset_dup(c, outer):
                                should_remove = True
                                hit_outer = outer
                                break
                    if should_remove:
                        log_debug(f"    [去重剔除] {c['width']:.0f}x{c['height']:.0f}@{c['layout']} 被 {hit_outer['width']:.0f}x{hit_outer['height']:.0f}@{hit_outer['layout']} (bbox {tuple(round(v) for v in c['bbox'])} ⊂/≈ {tuple(round(v) for v in hit_outer['bbox'])})")
                    if not should_remove:
                        result.append(c)
            return result

        frame_like = deduplicate_candidates(frame_like)
        dedup_count = len(frame_like)

        # ---------- 第23轮：图签附栏外扩合并（105100口径） ----------
        # 主图框 + 左侧 21000 图签附栏按整体输出（地下室电力t3.dwg：8 张 84100×59400
        # 主框左侧邻位有同排图纸的 J-图框/TK 图签条 → 外扩为 105100×59400，与用户
        # 实测一致；已在检出时闭合为 105100/126100 的框不再重复外扩）。
        _strips = _collect_title_strips(doc)
        if _strips:
            frame_like, _strip_expanded = _expand_frames_with_title_strips(frame_like, _strips)
            if _strip_expanded:
                safe_log(f"  [附栏外扩] {_strip_expanded} 个主图框按主框+图签附栏整体口径外扩")

        # ---------- 模型空间降级包围盒清洗 ----------
        # 降级路径"全实体包围盒"在无闭合矩形时把"该空间所有实体 bbox"当候选兜底，
        # area_ratio 恒 = 100%（分母即自身 layout 总面积）→ 无条件命中条件A。
        # 机械图纸/出图常见结构：图框放在布局空间（如 Gb A1 标题栏），模型空间只画
        # 零件内容（后桥半轴总成A1-840x593：模型空间 346 LINE 等无图框边线 → 降级
        # 包围盒 773×416 被误收为第 2 个"图框"）。
        # 规则：文档布局空间已识别出"非降级类型"的真图框 → 模型空间的降级包围盒
        # 必是内容外框，剔除。若整图只有模型空间候选，规则不触发（降级兜底仍有效）。
        if any(c['layout'] != '模型空间' and c['type'] != '全实体包围盒' for c in frame_like):
            _before = len(frame_like)
            frame_like = [c for c in frame_like
                          if not (c['type'] == '全实体包围盒' and c['layout'] == '模型空间')]
            if len(frame_like) < _before:
                safe_log(f"  [模型空间降级清洗] 布局空间已识别真图框，剔除模型空间降级包围盒 "
                         f"{_before - len(frame_like)} 个（模型空间仅是内容区，其包围盒不是图框）")

        # ---------- 每 layout 帧数上限 ----------
        # 图框数量筛选主要靠算法（is_frame_like 特征 + 嵌套/IoU 去重），
        # 此上限仅作防爆炸兜底：防止异常图纸（如数百个表格矩形全过特征筛选）
        # 让 frame_count 虚高。去重已修复"面积相等的重叠矩形重复计数"，
        # 正常图纸（含住宅拼版 18+ 图框）远低于 100，几乎不可能触到该上限。
        MAX_FRAMES_PER_LAYOUT = 100
        layout_frame_count = {}
        for c in frame_like:
            layout_frame_count.setdefault(c['layout'], []).append(c)
        capped = []
        for layout_name, group in layout_frame_count.items():
            sorted_group = sorted(group, key=lambda c: c['area'], reverse=True)
            capped.extend(sorted_group[:MAX_FRAMES_PER_LAYOUT])
        frame_like = capped

        # ---------- 修法A（续）：布局空间已排版图纸页时，模型空间的内容区块全部让位 ----------
        # DS4 四层 阁楼 平面系统图 (1).dwg：布局1 识别出 6 个网格排版的真图纸页，
        # 而模型空间同时"认出"5 个 9900×12900 级的巨型块（实为 1:1 的内容区轮廓，
        # 不是页面框）。二者叠加会把 fc 从正确的 6 抬高到 11，且主框尺寸会取错
        # （取模型空间最大的 13423×9014 而不是布局空间的 434×311）。
        # 判据：布局空间存在「网格排版图框」（即命中了上面的救援，或本 layout 内
        # 同尺寸同块名 INSERT ≥4 个且过 is_frame_like）→ 该图按"一版多页"处理，
        # 模型空间候选全部剔除。理由：布局空间是出图页面空间，一旦其中排出多个
        # 等大页面框，模型空间就只是这些页面的内容源，其内的任何大框都不是图框。
        # 与既有的"模型空间降级清洗"同源思路，但更彻底（那条只清 全实体包围盒，
        # 本条清模型空间的全部候选）。
        if 'FRAME_PARSER_NO_LAYOUT_WINS' not in os.environ:
            _paper_layouts = [ln for ln in {c['layout'] for c in frame_like}
                              if ln != '模型空间']
            _paper_frame_n = sum(1 for c in frame_like if c['layout'] != '模型空间')
            _model_frame_n = sum(1 for c in frame_like if c['layout'] == '模型空间')
            if _paper_layouts and _paper_frame_n >= 4 and _model_frame_n > 0:
                _before = len(frame_like)
                frame_like = [c for c in frame_like if c['layout'] != '模型空间']
                safe_log(f"  [布局空间优先] 布局空间 {_paper_layouts} 已识别 {_paper_frame_n} 个"
                         f"网格排版图纸页 → 剔除模型空间候选 {_before - len(frame_like)} 个"
                         f"（模型空间仅为页面内容源，其大框不是图框）")

        # frame_count 只统计"像图框"的候选数。旧写法在 frame_like 为空时兜底成
        # len(all_candidates)，导致真·无图框图纸（如 一层.dwg，全是家具/构件小矩形）
        # 在 smart 模式报"未检测到图框"→ 前端自动 force_max 重试 → 页面显示
        # frame_count=401（其实是全量候选数），严重误导。无图框就如实报 0。
        frame_count = len(frame_like)

        # ---------- 按布局分组图框数 ----------
        # frame_count 是模型空间 + 所有布局空间的合计；此处按 layout 字段分开统计
        # （如 {"模型空间": 3, "布局 \"Sheet1\"": 2}），方便前端展示"每个空间几张图框"。
        # 只统计通过特征筛选与清洗后的候选（frame_like）；force_max 模式下若无符合
        # 特征的图框（frame_like 为空），返回空字典，但主框尺寸仍取最大候选。
        frame_counts_by_layout = {}
        for c in frame_like:
            ln = c.get('layout', '未知')
            frame_counts_by_layout[ln] = frame_counts_by_layout.get(ln, 0) + 1

        # ---------- 强制最大矩形模式 ----------
        if mode == 'force_max':
            best = max(all_candidates, key=lambda c: c['area'])
            safe_log(f"✅ [强制最大矩形] 选中: {best['type']} | 布局: {best['layout']} | 尺寸: {best['width']:.2f} x {best['height']:.2f}")
            x1, y1, x2, y2 = best['bbox']
            width = x2 - x1
            height = y2 - y1
            if unit.lower() == 'inch':
                width *= 25.4
                height *= 25.4
            return {
                'width': round(width),
                'height': round(height),
                'frame_count': frame_count,
                'frame_counts_by_layout': frame_counts_by_layout,
                # 与 frame_count 同源同量：frame_like 为空时明细也应为空，
                # 避免"0 个图框 + 401 行明细"的自相矛盾展示。
                'candidates': build_payload(frame_like),
                'xref_warnings': xref_warnings,
            }

        # ---------- 智能检测模式（特征筛选） ----------
        valid_candidates = frame_like

        if not valid_candidates:
            safe_log("⚠️ 未检测到图框：所有候选矩形均不符合图框特征")
            safe_log("   - 候选矩形列表：")
            for i, c in enumerate(all_candidates, 1):
                log_debug(f"     {i}. {c['type']} | 布局: {c['layout']} | 尺寸: {c['width']:.2f} x {c['height']:.2f} | "
                          f"归一化长宽比: {c['ratio']:.4f} | 面积占比: {c['area_ratio']:.2%}")
            # 注意：此错误信息前缀被前端用于触发自动重试，勿随意修改
            raise ValueError("未检测到图框（图纸可能没有标准图框）")

        weight_map = {'polyline': 1000, 'line_rect': 500}
        for c in valid_candidates:
            if priority == 'polyline' and c['type'] == '闭合多段线':
                c['weighted_area'] = c['area'] * weight_map['polyline']
            elif priority == 'line_rect' and c['type'] == '直线矩形':
                c['weighted_area'] = c['area'] * weight_map['line_rect']
            else:
                c['weighted_area'] = c['area']

        valid_candidates.sort(key=lambda c: c['weighted_area'], reverse=True)

        safe_log("========== 检测结果 ==========")
        safe_log(f"全部候选: {len(all_candidates)} | 通过特征: {pass_feature_count} | 去包裹框: {wrap_stripped_count} | 去重后: {dedup_count} | 上限后: {len(frame_like)}")
        for i, c in enumerate(valid_candidates, 1):
            bx1, by1, bx2, by2 = c['bbox']
            extra = ''
            if c['type'] == '块参照插入':
                extra = f" | 块名: {c.get('block_name', '?')} | 图层: {c.get('insert_layer', '?')}"
            log_debug(f"  {i}. {c['type']} | 布局: {c['layout']} | 尺寸: {c['width']:.2f} x {c['height']:.2f} | "
                      f"归一化长宽比: {c['ratio']:.4f} | 面积占比: {c['area_ratio']:.2%} | "
                      f"bbox=({bx1:.0f},{by1:.0f},{bx2:.0f},{by2:.0f}){extra}")
        safe_log("============================")

        best = valid_candidates[0]
        safe_log(f"✅ 选中: {best['type']} | 布局: {best['layout']} | 尺寸: {best['width']:.2f} x {best['height']:.2f}")

        x1, y1, x2, y2 = best['bbox']
        width = x2 - x1
        height = y2 - y1

        if unit.lower() == 'inch':
            width *= 25.4
            height *= 25.4

        width = round(width)
        height = round(height)

        if width <= 0 or height <= 0:
            raise ValueError("尺寸无效")

        return {
            'width': width,
            'height': height,
            'frame_count': frame_count,
            'frame_counts_by_layout': frame_counts_by_layout,
            'candidates': build_payload(valid_candidates),
            'xref_warnings': xref_warnings,
        }

    except Exception as e:
        raise RuntimeError(f"解析失败: {str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

# ---------- Flask 路由 ----------
@app.route('/')
def index():
    """托管前端页面，访问 http://127.0.0.1:5000 即可打开上传界面。"""
    return send_from_directory('.', 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    """托管前端引用的静态文件。

    白名单原本只有 app.js/style.css——vendor/xlsx-style.bundle.js（Excel 导出
    组件，2026-09-11 引入 index.html）被 404 拦截，页面 XLSX 全局未定义，
    导出全部 Excel 时弹"Excel 导出组件未加载"。vendor/ 下整体放行
    （send_from_directory 自带路径穿越防护），其余仍拒绝。
    """
    if filename in {'app.js', 'style.css'} or filename.startswith('vendor/'):
        return send_from_directory('.', filename)
    return '', 404


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': '未提供文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '文件名为空'}), 400

    filename = file.filename.lower()
    if not (filename.endswith('.dxf') or filename.endswith('.dwg')):
        return jsonify({'error': '仅支持 .dxf 或 .dwg 文件'}), 400

    priority = request.form.get('priority', 'polyline')
    unit = request.form.get('unit', 'mm')
    mode = request.form.get('mode', 'smart')

    try:
        file_bytes = file.read()
        result = get_bounding_box_from_bytes(file_bytes, filename, priority, unit, mode)
        return jsonify({
            'width': result['width'],
            'height': result['height'],
            'unit': unit,
            'frame_count': result['frame_count'],
            'frame_counts_by_layout': result.get('frame_counts_by_layout', {}),
            'candidates': result['candidates'],
            'xref_warnings': result.get('xref_warnings', []),
        })
    except Exception as e:
        # 写日志文件（不能用 print_exc，控制台写入本身可能失败）
        logger.error('解析异常 [%s]: %s\n%s', filename, e, traceback.format_exc())
        return jsonify({'error': str(e)}), 500

def assert_port_free(port: int, host: str = '0.0.0.0'):
    """启动前端口占用预检。

    背景：Windows 下 Flask dev server 默认 SO_REUSEADDR，同端口两个进程可以
    同时 bind 成功（历史事故：5000 端口新旧两个服务并存，上传 POST 连接随机
    被重置 HTTP 000，页面行为时对时错极难排查）。预检占用即报错退出，
    提示排查命令，杜绝双进程抢端口。
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        raise SystemExit(
            f'[启动失败] 端口 {port} 已被占用（可能存在旧进程/其他项目占用）。\n'
            f'  排查: netstat -ano | findstr :{port}\n'
            f'  结束: taskkill /F /PID <PID>\n'
            f'  或换端口启动: set FLASK_PORT={port + 1} && python app.py'
        )
    finally:
        probe.close()


if __name__ == '__main__':
    # 端口可通过环境变量 FLASK_PORT 配置（默认 5000），避免与其他本地项目冲突
    _port = int(os.environ.get('FLASK_PORT', '5000'))
    # debug=True 时 Werkzeug reloader 会 fork 子进程重跑本文件：此时父进程已
    # 监听端口，子进程预检必然误报"端口被占用"。WERKZEUG_RUN_MAIN 仅在
    # reloader 子进程中为 'true'——只在父进程（真正首次 bind 前）做预检。
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        assert_port_free(_port)
    print(f' * 图框解析服务: http://127.0.0.1:{_port}  (换端口: set FLASK_PORT=端口号)')
    app.run(host='0.0.0.0', port=_port, debug=True)