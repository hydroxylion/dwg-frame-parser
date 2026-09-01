import os
import tempfile
import logging
import traceback
import ezdxf
from ezdxf import bbox as ezdxf_bbox
from ezdxf.addons import odafc
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

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
    """提取 LWPOLYLINE 或 POLYLINE 的所有顶点 (x,y)"""
    vertices = []
    if entity.dxftype() == 'LWPOLYLINE':
        for point in entity.get_points():
            vertices.append((point[0], point[1]))
    elif entity.dxftype() == 'POLYLINE':
        for vertex in entity.vertices:
            loc = vertex.dxf.location
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


def detect_rectangles_from_lines(entity_list):
    """从 LINE 实体中检测多个矩形候选，返回 bbox 列表（面积降序）。

    判定规则：
    - 横线对 × 纵线对配对，两组线的线段覆盖范围必须互相覆盖对方围出的区间
      （4 条线真的围出一个闭合区域；两图框中间有缝隙时外包络会被覆盖判断否决）
    - P1 严格矩形判定：矩形 4 条边的每一侧都必须至少有一条完整 LINE 覆盖，
      不允许用边线残段+内部标注线拼凑
    - 矩形内部若存在整条穿越的分隔线（如两个并排图框的公共边），判定为
      拼合外包络，予以剔除，避免把"包住多个图框的大矩形"当成图框
    - 短边 ≥ MIN_LINE_RECT_SIDE，过滤家具/表格/标题栏等小矩形
    """
    h_lines = []
    v_lines = []
    for ent in entity_list:
        if ent.dxftype() != 'LINE':
            continue
        start = ent.dxf.start
        end = ent.dxf.end
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
        公共覆盖 = 两条线各自线段覆盖的交集，为空则这对线围不出区域。"""
        pairs = []
        n = len(clusters)
        all_pairs = n <= LINE_CLUSTER_MAX_FOR_ALL_PAIRS
        for i in range(n):
            j_list = range(i + 1, n) if all_pairs else ([i + 1] if i + 1 < n else [])
            for j in j_list:
                c1, s1 = clusters[i]
                c2, s2 = clusters[j]
                common = _intersect_segments(s1, s2)
                if common:
                    pairs.append((min(c1, c2), max(c1, c2), common))
        return pairs

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
    MAX_EXAMINED = 200000  # 组合数上限，防止异常图纸（超密集网格）拖垮解析
    for (ya, yb, h_common) in h_pairs:
        for (xa, xb, v_common) in v_pairs:
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

# ---------- 统一扫描函数 ----------
def collect_candidates_from_layout(layout, doc, layout_name):
    candidates = []
    # 1. 闭合多段线（加矩形度过滤：bbox面积/多边形面积 > 阈值才算候选）
    #    L 形标题栏、T 形会签栏等非矩形闭合线的矩形度远低于 1.0，
    #    真正的图框边线（矩形或近矩形）矩形度 > 0.95。
    RECTANGULARITY_THRESHOLD = 0.92
    seen_bbox = set()  # 同一 layout 内 bbox 去重：同一位置画两遍的闭合多段线只保留 1 个候选
    for entity in layout:
        dxftype = entity.dxftype()
        if dxftype in ('LWPOLYLINE', 'POLYLINE'):
            if is_polyline_closed(entity):
                vertices = get_polyline_vertices(entity)
                if len(vertices) >= 3:
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
    # 2. 直线矩形（多矩形检测：每个由 4 条直线围出的区域都是一个候选）
    lines = [ent for ent in layout if ent.dxftype() == 'LINE']
    if lines:
        for rect_bbox in detect_rectangles_from_lines(lines):
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
    if not candidates:
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
        #   条件A：归一化长宽比在区间内 + 面积占比 ≥ 15%（常规场景）
        #   条件B：归一化长宽比在区间内 + 短边在合理图框尺寸范围内 + 显式检测（闭合多段线/直线矩形）
        #         + 尺寸规整（宽高接近整数）+ 短边下限 400mm
        #         ——密集轴线网格场景下，图框面积占比可能极低（<0.1%），但短边在 400~2000mm
        #           之间、且是显式画出的矩形，仍然可信；而网格格子短边常 >2000mm，被此条件过滤。
        #         尺寸规整用于过滤"墙线交错产生的闭合多段线轮廓"：真图框尺寸几乎总是整数
        #         （2500×1500、841×594），而墙体相交产生的轮廓尺寸常带小数（1895.28×2629.36）。
        #         短边下限 400mm 过滤门窗等构件小矩形（800×300、200×150 等）。注意：短边
        #         <400 的小图框（如 A4 横向 210mm）若面积占比达标仍可通过条件 A 保留，
        #         此下限只作用于条件 B 的"绕过面积占比"放宽通道。
        MAX_FRAME_SHORT_SIDE = 2000  # 合理图框短边上限（mm）；A0 竖版短边 841mm，留足余量
        MIN_FRAME_SHORT_SIDE = 400   # 条件 B 短边下限（mm）；过滤门窗等构件小矩形，真图框经条件 A 保底
        SIZE_ROUNDNESS_EPS = 0.1     # 尺寸规整容差（mm）：宽高与最近整数的差 ≤ 0.1 视为整数
        EXPLICIT_TYPES = {'闭合多段线', '直线矩形'}

        for c in all_candidates:
            c['ratio'] = normalized_ratio(c['width'], c['height'])
            c['short_side'] = min(c['width'], c['height'])

        def is_frame_like(c):
            if not (FRAME_RATIO_MIN <= c['ratio'] <= FRAME_RATIO_MAX):
                return False
            # 条件A：面积占比达标
            if c['area_ratio'] >= 0.15:
                return True
            # 条件B：显式检测 + 短边在合理范围（绕过面积占比，适用于密集几何场景）
            #   + 尺寸规整：宽高都接近整数，过滤墙线交错产生的非整数闭合多段线轮廓
            if c['type'] in EXPLICIT_TYPES and MIN_FRAME_SHORT_SIDE <= c['short_side'] <= MAX_FRAME_SHORT_SIDE:
                if (abs(c['width'] - round(c['width'])) <= SIZE_ROUNDNESS_EPS and
                        abs(c['height'] - round(c['height'])) <= SIZE_ROUNDNESS_EPS):
                    return True
            return False

        frame_like = [c for c in all_candidates if is_frame_like(c)]
        pass_feature_count = len(frame_like)

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
        safe_log(f"全部候选: {len(all_candidates)} | 通过特征: {pass_feature_count} | 去重后: {dedup_count} | 上限后: {len(frame_like)}")
        for i, c in enumerate(valid_candidates, 1):
            safe_log(f"  {i}. {c['type']} | 布局: {c['layout']} | 尺寸: {c['width']:.2f} x {c['height']:.2f} | "
                     f"归一化长宽比: {c['ratio']:.4f} | 面积占比: {c['area_ratio']:.2%}")
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
        })
    except Exception as e:
        # 写日志文件（不能用 print_exc，控制台写入本身可能失败）
        logger.error('解析异常 [%s]: %s\n%s', filename, e, traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)