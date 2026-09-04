import os
import tempfile
import logging
import traceback
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
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'parser.log')
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    encoding='utf-8',
)
logger = logging.getLogger('dwg-parser')


def safe_log(msg):
    """打印到控制台（失败不中断），并写入 parser.log 便于批量排查"""
    logger.info(msg)
    try:
        print(msg)
    except Exception:
        pass


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
MAX_LINE_RECT_CANDIDATES = 200    # 直线矩形候选数上限，防止异常图纸拖垮解析
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
    # v_pairs 按间距升序建立索引，组合循环内用「宽高比例剪枝」只遍历与当前 h_pair
    # 间距成合理比例的 v_pair 子区间，避免外层每对横线都全扫十几万竖线对。
    # 比例边界：图框归一化长宽比上限约 5.5（FRAME_RATIO_MAX），剪枝放宽到 8，
    # 超出该比例的横竖线对组合不可能是图框（要么极端细长被 is_frame_like 拒，
    # 要么本就是假组合），跳过不损失召回。夹具装配图式的远边界配对仍在区间内。
    import bisect as _bisect
    PAIR_RATIO_BOUND = 8.0
    v_by_gap = sorted(v_pairs, key=lambda p: p[1] - p[0])
    v_gaps = [p[1] - p[0] for p in v_by_gap]
    for (ya, yb, h_common) in h_pairs:
        _H = yb - ya
        if _H < MIN_LINE_RECT_SIDE:
            continue  # 矩形高 < 短边下限：任何组合短边必 < 下限，整层跳过
        _i0 = _bisect.bisect_left(v_gaps, max(MIN_LINE_RECT_SIDE, _H / PAIR_RATIO_BOUND))
        # 右界同时受比例上限与公共覆盖总长限制：gap 超过本线对的公共覆盖总长时
        # coverage 校验必失败（横线覆盖不了那么宽的矩形），无需扫描白白耗预算
        _i1 = _bisect.bisect_right(v_gaps, min(_H * PAIR_RATIO_BOUND,
                                              sum(hi - lo for lo, hi in h_common)))
        for _vi in range(_i0, _i1):
            xa, xb, v_common = v_by_gap[_vi]
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
        else:
            continue
        break
    rects.sort(key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)
    return rects

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
                    _bb = _get_block_world_bbox(_ent.dxf.name, _visited)
                    if _bb is not None:
                        _ipt = (_ent.dxf.insert.x, _ent.dxf.insert.y)
                        _xs = _ent.dxf.xscale; _ys = _ent.dxf.yscale
                        _pts.append((_ipt[0] + _bb[0]*_xs, _ipt[1] + _bb[1]*_ys))
                        _pts.append((_ipt[0] + _bb[2]*_xs, _ipt[1] + _bb[3]*_ys))
            except Exception:
                continue
        if not _pts:
            _block_bbox_cache[_name] = None
            return None
        _bb_ret = (min(p[0] for p in _pts), min(p[1] for p in _pts),
                   max(p[0] for p in _pts), max(p[1] for p in _pts))
        _block_bbox_cache[_name] = _bb_ret
        return _bb_ret

    _insert_total = 0
    _insert_kept = 0
    _insert_filtered = 0  # 预筛剔除数（非图框级尺寸）
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
            _bx1, _by1, _bx2, _by2 = _bb_def
            _local_pts = [(_bx1*_xs, _by1*_ys), (_bx2*_xs, _by1*_ys),
                          (_bx1*_xs, _by2*_ys), (_bx2*_xs, _by2*_ys)]
            if _ins_rot:
                _cr = _math.cos(_ins_rot); _sr = _math.sin(_ins_rot)
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
            if _w <= 0 or _h <= 0:
                continue
            # 预筛：尺寸必须"图框级"才入库，避免家具/符号类小块污染候选库
            _w_short = min(_w, _h)
            _w_ratio = (max(_w, _h) / _w_short) if _w_short > 0 else 0
            if _w_short < INSERT_MIN_SHORT_SIDE or not (FRAME_RATIO_MIN <= _w_ratio <= FRAME_RATIO_MAX):
                _insert_filtered += 1
                continue
            _bbox = (_xm, _ym, _xM, _yM)
            _key = (round(_xm, 3), round(_ym, 3), round(_xM, 3), round(_yM, 3))
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
        _STANDARD_A_SHORT_SIDES = (841, 594, 420, 297, 210, 148, 105)
        def _is_standard_a_series(w, h, eps=1.0):
            """尺寸是否为标准 A 系列整数尺寸（短边在标准集合内、长边≈短边×√2）
            eps=1.0 容差：尺寸规整容差 + 些许画图误差"""
            _short = min(w, h); _long = max(w, h)
            for _std in _STANDARD_A_SHORT_SIDES:
                if abs(_short - _std) <= eps and abs(_long - _std * _math.sqrt(2)) <= eps:
                    return True
            return False
        _bn_groups = {}
        for _idx, _c in enumerate(candidates):
            if _c.get('type') == '块参照插入':
                _bn_groups.setdefault(_c.get('block_name', '<unknown>'), []).append(_idx)
        _aligned_log = []
        _a_series_protected = []  # 白名单保护（标准 A 系列图框，不参与对齐剔除）
        _rm_idx = set()
        for _bn, _grp in _bn_groups.items():
            if len(_grp) < 3:
                continue
            # 白名单：尺寸是标准 A 系列 → 不剔除
            _sample = candidates[_grp[0]]
            if _is_standard_a_series(_sample['width'], _sample['height']):
                _a_series_protected.append(f"{_bn}({len(_grp)}个,{_sample['width']:.0f}x{_sample['height']:.0f})")
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
    # 3. 如果没有候选，取全实体包围盒（降级）
    #    排除 VIEWPORT：视口是布局空间的显示窗口（透视模型空间的"取景框"），
    #    不是图纸内容。布局里只有 VIEWPORT 说明图纸内容全在模型空间，
    #    布局本身没有画图框——这种空布局的视口边框没有图框意义，
    #    不应产生候选（泛悦通风 11-MW-FP001：布局1 只有 2 个 VIEWPORT，
    #    降级路径把视口框 29.17×12.37 当图框，frame_count 虚增 2→实际应 1）。
    #    排除后若无其他实体（空布局），不产生候选。
    if not candidates:
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        found = False
        for entity in visible_entities:
            if entity.dxftype() == 'VIEWPORT':
                continue  # 视口框不是图纸内容，跳过
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
    return candidates

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

        msp = doc.modelspace()
        msp_total = calculate_layout_total_bbox(msp, doc)
        for c in collect_candidates_from_layout(msp, doc, '模型空间'):
            c['area_ratio'] = (c['area'] / msp_total) if msp_total > 0 else 1.0
            all_candidates.append(c)

        for layout in doc.layouts:
            if layout.name == 'Model':
                continue
            layout_total = calculate_layout_total_bbox(layout, doc)
            for c in collect_candidates_from_layout(layout, doc, f'布局 "{layout.name}"'):
                c['area_ratio'] = (c['area'] / layout_total) if layout_total > 0 else 1.0
                all_candidates.append(c)

        if not all_candidates:
            raise ValueError("未找到任何有效边界")

        def build_payload(cands):
            """把候选列表转成前端可展示的结构（按面积降序，最多 20 条）"""
            result_cands = []
            for c in sorted(cands, key=lambda x: x['area'], reverse=True)[:20]:
                cw, ch = c['width'], c['height']
                if unit.lower() == 'inch':
                    cw, ch = cw * 25.4, ch * 25.4
                result_cands.append({
                    'layout': c['layout'],
                    'type': c['type'],
                    'width': round(cw),
                    'height': round(ch),
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
        # 条件 C：大尺寸真图框识别（相对面积法，跟单位无关）
        #   场景：多张图框画在同一模型空间（如整本图纸的 9 张图都在 Model 里），
        #   layout 总包围盒巨大 → 单图框面积占比极低（<0.1%），条件 A 全挂；
        #   而真图框短边可能因单位非标（0.01mm 等）数值放大到几万，超出条件 B 的 2000 上限。
        #   用"候选面积 / 该 layout 最大候选面积"代替绝对占比：真图框彼此同量级（≥10%），
        #   小矩形（标题栏/设备表/标注框）面积远小于最大图框，被自然过滤。
        #   跟单位无关：无论 mm / 0.01mm / cm，比例关系不变。
        #   风险：A0+A4 混排时 A4 仅占 A0 的 6.25%，可能被 10% 阈值排除；暂先上 10% 复测。
        REL_AREA_THRESHOLD = 0.10     # 条件 C 相对面积阈值：候选面积 ≥ 该 layout 最大候选面积 × 10%

        for c in all_candidates:
            c['ratio'] = normalized_ratio(c['width'], c['height'])
            c['short_side'] = min(c['width'], c['height'])

        # 预计算每个 layout 的最大候选面积，供条件 C 使用（相对面积法分母）。
        # 排除 area_ratio>1.0 的异常候选（面积超过 layout 总面积必为计算异常/离群大块，
        # 如底图块 X-总图排水底图 88 万级别 INSERT），否则真图框 rel_area 被稀释到 <10%
        # 条件C 全挂（UCS图纸/含远距离底图块的图纸）。
        layout_max_area = {}
        for c in all_candidates:
            if c['area_ratio'] > 1.0:
                continue  # 异常候选不作为相对面积参考
            ln = c['layout']
            if ln not in layout_max_area or c['area'] > layout_max_area[ln]:
                layout_max_area[ln] = c['area']
        for c in all_candidates:
            ma = layout_max_area.get(c['layout'], 0)
            c['rel_area_ratio'] = (c['area'] / ma) if ma > 0 else 0.0

        def is_frame_like(c):
            if not (FRAME_RATIO_MIN <= c['ratio'] <= FRAME_RATIO_MAX):
                return False
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
            #   _at_least_sqrt2：长宽比 ≥ √2×0.99（≈1.400）——条件C 用，
            #     允许加长版图框（长宽比 > √2，如 A4 加长版 297×525.5 长宽比1.769），
            #     过滤接近正方形的误判候选（38200×40550 长宽比1.06、39400×31850 长宽比1.24）。
            #     真图框（标准+加长版）长宽比都 ≥ √2：雅安1.4158/1.4143、澜山12fas1.4141、
            #     841×594=1.4143、297×525.5=1.769。0.99 容差让澜山12fas（1.4141 略<√2）通过。
            _SQRT2 = 2 ** 0.5
            _near_sqrt2 = abs(c['ratio'] - _SQRT2) / _SQRT2 <= 0.10
            _at_least_sqrt2 = c['ratio'] >= _SQRT2 * 0.99
            # 条件A：面积占比达标
            if c['area_ratio'] >= 0.15:
                return True
            # 条件C：相对面积达标（跟单位无关，处理多图框同 layout 场景）
            #   必须在条件B 之前：雅安类图纸短边超 2000 过不了B，但相对面积能过C。
            #   附加约束：长宽比 ≥ √2（允许加长版图框，过滤接近正方形的误判候选）
            if c['rel_area_ratio'] >= REL_AREA_THRESHOLD and _at_least_sqrt2:
                return True
            # 条件B：显式检测 + 短边在合理范围（绕过面积占比，适用于密集几何场景）
            #   + 尺寸规整：宽高都接近整数，过滤墙线交错产生的非整数闭合多段线轮廓
            #   + 长宽比接近 √2（±10%）
            #   + 直线矩形额外要求相对面积达标：LINE 线框若连"同 layout 最大候选的
            #     10%"都不到，只是空间里众多小矩形之一，不是图框（一层平面图模型空间
            #     450×600——模型空间没有图框，该矩形是 CAD 远处残留几何）。真 LINE 图框
            #     （夹具 3×841×594）是所在空间最大候选，rel ≈100% 不受影响。
            #     闭合多段线不设此限：730×540 等装饰框需留在 frame_like 供外层包裹框
            #     识别作"内含尺寸组"证据（澜山 76736×28029 外包络）。
            if c['type'] in EXPLICIT_TYPES and MIN_FRAME_SHORT_SIDE <= c['short_side'] <= MAX_FRAME_SHORT_SIDE:
                if (abs(c['width'] - round(c['width'])) <= SIZE_ROUNDNESS_EPS and
                        abs(c['height'] - round(c['height'])) <= SIZE_ROUNDNESS_EPS and
                        _near_sqrt2 and
                        (c['type'] != '直线矩形' or c['rel_area_ratio'] >= REL_AREA_THRESHOLD)):
                    return True
            return False

        frame_like = [c for c in all_candidates if is_frame_like(c)]
        pass_feature_count = len(frame_like)

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
                    # INSERT 块参照是图框的常见画法（一张图就一个图框），不应被当包裹框——
                    # 其内部的标题栏/家具/房间小矩形尺寸组数多，会被错判为"内含多个图框"。
                    if big.get('type') == '块参照插入':
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
                    if (len(groups) >= MIN_WRAPPED_FRAMES or
                            any(g[1] >= MIN_WRAPPED_SAME_SIZE for g in groups)):
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
                    for outer in result:
                        if outer['layout'] != layout_name:
                            continue
                        ox1, oy1, ox2, oy2 = outer['bbox']
                        # 规则1：嵌套——c 的 bbox 完全包含在 outer 内，且面积明显更小
                        if (cx1 >= ox1 - NESTED_EPS and cy1 >= oy1 - NESTED_EPS and
                            cx2 <= ox2 + NESTED_EPS and cy2 <= oy2 + NESTED_EPS and
                            c['area'] < outer['area'] * NESTED_AREA_RATIO):
                            should_remove = True
                            break
                        # 规则2：IoU 重叠——c 与 outer 重叠度 > 阈值，且面积不大于 outer
                        #   <= 而非 <：面积完全相等的重叠矩形（同一图框被画两遍/检测两次）
                        #   也必须去重，否则同一图框会被重复计数（IoU=1.0 却因面积相等被跳过）
                        #   处理"图框边线+内部标注线围出的部分区域假矩形"
                        if c['area'] <= outer['area']:
                            iou = bbox_iou(c['bbox'], outer['bbox'])
                            if iou > IOU_THRESHOLD:
                                should_remove = True
                                break
                    if not should_remove:
                        result.append(c)
            return result

        frame_like = deduplicate_candidates(frame_like)
        dedup_count = len(frame_like)

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

        frame_count = len(frame_like) if frame_like else len(all_candidates)

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
                'candidates': build_payload(frame_like if frame_like else all_candidates),
                'xref_warnings': xref_warnings,
            }

        # ---------- 智能检测模式（特征筛选） ----------
        valid_candidates = frame_like

        if not valid_candidates:
            safe_log("⚠️ 未检测到图框：所有候选矩形均不符合图框特征")
            safe_log("   - 候选矩形列表：")
            for i, c in enumerate(all_candidates, 1):
                safe_log(f"     {i}. {c['type']} | 布局: {c['layout']} | 尺寸: {c['width']:.2f} x {c['height']:.2f} | "
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
            safe_log(f"  {i}. {c['type']} | 布局: {c['layout']} | 尺寸: {c['width']:.2f} x {c['height']:.2f} | "
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
    """托管前端引用的 app.js、style.css 等静态文件。"""
    allowed = {'app.js', 'style.css'}
    if filename not in allowed:
        return '', 404
    return send_from_directory('.', filename)


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
            'candidates': result['candidates'],
            'xref_warnings': result.get('xref_warnings', []),
        })
    except Exception as e:
        # 写日志文件（不能用 print_exc，控制台写入本身可能失败）
        logger.error('解析异常 [%s]: %s\n%s', filename, e, traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)