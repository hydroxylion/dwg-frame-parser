# -*- coding: utf-8 -*-
"""后端图框识别规则端到端验证（直接调用 get_bounding_box_from_bytes，不依赖 HTTP）。
用 ezdxf 构造测试图纸，验证：
  1. 竖版图框（841x1189）在 smart 模式下能被选中（旧逻辑会被比例过滤误杀）
  2. 加长图框（1783x841）在 smart 模式下能被选中（旧逻辑同样被误杀）
  3. 布局空间图框 + 模型空间 1:100 大几何：面积占比分母按 layout 计算，布局图框不被误杀
  4. 多图框：candidates 返回全部候选，frame_count 正确，选中面积最大者
  5. 正方形垃圾边界：smart 模式拒绝（触发前端 force_max 重试链路）
  6. rel 分母塌缩防护：整图即图框时内部构件不得入选（运煤胶带机.dwg）
  7. 布局空间网格图框救援 + 布局空间优先：布局空间成版页框识别（DS4 四层阁楼平面系统图）
  8. 条件G 同模板缩放套图救援：带标题栏条纹的同比例小尺寸实例救回（胜利公寓２）
"""
import io
import sys
import ezdxf
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import get_bounding_box_from_bytes


def make_doc():
    return ezdxf.new('R2010', setup=False)


def add_closed_rect(layout, x, y, w, h):
    layout.add_lwpolyline(
        [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
        close=True,
    )


def add_line_rect(layout, x, y, w, h):
    """用 4 条独立 LINE 画一个矩形（模拟直线图框）"""
    layout.add_line((x, y), (x + w, y))
    layout.add_line((x + w, y), (x + w, y + h))
    layout.add_line((x + w, y + h), (x, y + h))
    layout.add_line((x, y + h), (x, y))


def to_bytes(doc):
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode('utf-8')


results = []


def check(name, fn, expect):
    try:
        got = fn()
        ok = expect(got)
        results.append(ok)
        print(('PASS' if ok else 'FAIL') + f'  {name}  ->  {got}')
    except Exception as e:
        ok = expect.__name__ != '_reject' if False else False
        # 异常也交给 expect 判定（expect 需自行捕获）
        results.append(False)
        print(f'FAIL  {name}  ->  异常: {e}')


def parse(data, mode='smart'):
    return get_bounding_box_from_bytes(data, 'test.dxf', 'polyline', 'mm', mode)


def expect_error_no_frame(data):
    try:
        parse(data)
        return False
    except RuntimeError as e:
        return '未检测到图框' in str(e)


# ---- 用例 1：竖版 A0 图框（841 x 1189），旧逻辑 ratio 得分 0.5 会被拒 ----
doc = make_doc()
add_closed_rect(doc.modelspace(), 0, 0, 841, 1189)
data = to_bytes(doc)
check('竖版 A0 图框 (smart)', lambda: parse(data),
      lambda r: r['width'] == 841 and r['height'] == 1189 and r['frame_count'] == 1)

# ---- 用例 2：标准加长 A0+1/2（1783 x 841），旧逻辑比例得分 0.5 被拒 ----
doc = make_doc()
add_closed_rect(doc.modelspace(), 0, 0, 1783, 841)
data = to_bytes(doc)
check('加长 A0+1/2 图框 (smart)', lambda: parse(data),
      lambda r: r['width'] == 1783 and r['height'] == 841)

# ---- 用例 3：模型空间 1:100 大几何 + 布局空间 A2 图框 ----
# 旧逻辑分母只算模型空间总包围盒，布局图框面积占比 <0.0001 被误杀（报"未检测到图框"）。
# 新逻辑：分母按候选所在 layout 计算，布局图框存活（占比 100%）；
# 模型空间大几何同样合法（比例 1.414），按"最大候选胜出"规则被选中属设计行为。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 59400, 42000)      # 模型空间大几何（1:100 出图）
add_closed_rect(msp, 100000, 0, 60000, 60000)  # 另一处大几何（近正方形，应被比例过滤拒绝）
layout = doc.layouts.new('图纸布局')
add_closed_rect(layout, 0, 0, 594, 420)        # 布局空间 A2 图框
data = to_bytes(doc)
check('布局空间 A2 图框 + 模型大几何 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and (r['width'], r['height']) == (59400, 42000)  # 最大合法候选胜出
      and any(c['layout'] == '布局 "图纸布局"' and c['width'] == 594 for c in r['candidates']))

# ---- 用例 4：同一图纸多个图框，验证 candidates / frame_count / 选中逻辑 ----
doc = make_doc()
add_closed_rect(doc.modelspace(), 0, 0, 841, 594)      # A1
add_closed_rect(doc.modelspace(), 1000, 0, 594, 420)   # A2
layout = doc.layouts.new('多框布局')
add_closed_rect(layout, 0, 0, 420, 297)                # A3
data = to_bytes(doc)
check('多图框 candidates (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and len(r['candidates']) == 3
      and sorted((c['width'], c['height']) for c in r['candidates']) == [(420, 297), (594, 420), (841, 594)]
      and (r['width'], r['height']) == (841, 594))

# ---- 用例 4b：force_max 模式下也应返回全部候选 ----
check('多图框 candidates (force_max)', lambda: parse(data, mode='force_max'),
      lambda r: r['frame_count'] == 3 and (r['width'], r['height']) == (841, 594))

# ---- 用例 5：正方形边界，smart 应拒绝（返回"未检测到图框"错误） ----
doc = make_doc()
add_closed_rect(doc.modelspace(), 0, 0, 1000, 1000)
data = to_bytes(doc)
ok = expect_error_no_frame(data)
results.append(ok)
print(('PASS' if ok else 'FAIL') + '  正方形垃圾边界 (smart)  ->  未检测到图框')

# ---- 用例 6：两个 LINE 画的 A3 图框并排（共享一条边）----
# 旧逻辑只能产出全局外包络 840x297（不属于任何真实图框）；
# 新逻辑：覆盖判断 + 内部分隔线过滤剔除拼合外包络，产出两个 420x297
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 420, 297)
add_line_rect(msp, 420, 0, 420, 297)
data = to_bytes(doc)
check('LINE 并排双图框 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and sorted((c['width'], c['height']) for c in r['candidates']) == [(420, 297), (420, 297)]
      and (r['width'], r['height']) == (420, 297))

# ---- 用例 6b：两个 LINE 图框并排且中间有缝隙 ----
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 420, 297)
add_line_rect(msp, 500, 0, 420, 297)
data = to_bytes(doc)
check('LINE 带缝隙双图框 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and sorted((c['width'], c['height']) for c in r['candidates']) == [(420, 297), (420, 297)])

# ---- 用例 7：LINE 嵌套图框（外框 841x594 + 内框 821x574）----
# 嵌套去重：内框被外框包含且面积 <95%，应被剔除 → frame_count=1
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 841, 594)
add_line_rect(msp, 10, 10, 821, 574)
data = to_bytes(doc)
check('LINE 嵌套图框去重 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (841, 594))

# ---- 用例 8：LINE 图框 + 标题栏小矩形（不应计入图框数）----
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 420, 297)
add_line_rect(msp, 240, 0, 180, 56)  # 标题栏：短边 56 < 100，应被最小边过滤
data = to_bytes(doc)
check('LINE 图框 + 标题栏干扰 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (420, 297))

# ---- 用例 9：模型空间 LINE 图框 + 布局空间 LINE 图框（跨空间计数）----
doc = make_doc()
add_line_rect(doc.modelspace(), 0, 0, 841, 594)
layout = doc.layouts.new('线框布局')
add_line_rect(layout, 0, 0, 594, 420)
data = to_bytes(doc)
check('LINE 跨空间双图框 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and (r['width'], r['height']) == (841, 594)
      and any(c['layout'] == '布局 "线框布局"' and (c['width'], c['height']) == (594, 420)
              for c in r['candidates']))

print(f'\n结果: {sum(results)} 通过, {len(results) - sum(results)} 失败')

# ---- 用例 10：L 形标题栏（闭合多段线），矩形度 <0.92 应被过滤 ----
# L 形：外框 841x594，右下角切掉标题栏区域，矩形度≈0.89
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 841, 594)  # 真正的图框
msp.add_lwpolyline(
    [(0, 0), (841, 0), (841, 514), (180, 514), (180, 594), (0, 594)],
    close=True,
)  # L 形标题栏
data = to_bytes(doc)
# L 形标题栏应被矩形度过滤（矩形度 0.89 < 0.92），只有图框自身通过
# 但闭合多段线图框 + LINE 图框的嵌套去重也生效 → frame_count 应为 1
check('L形标题栏被矩形度过滤 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (841, 594))

# ---- 用例 11：闭合多段线外框 + 闭合多段线内框（嵌套去重）----
# 外框 841x594，内框 821x574（面积比 = 821*574/(841*594) ≈ 0.94 < 0.95）
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 841, 594)
add_closed_rect(msp, 10, 10, 821, 574)
data = to_bytes(doc)
check('闭合多段线嵌套去重 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (841, 594))

# ---- 用例 12：接近等大的内框会被 IoU 去重 ----
# 外框 841x594，内框 840x593（偏移 0.5,0.5），IoU≈99.97% > 50%
# 两者高度重叠，IoU 去重会去除较小的内框 → frame_count=1
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 841, 594)
add_closed_rect(msp, 0.5, 0.5, 840, 593)
data = to_bytes(doc)
check('接近等大内框 IoU 去重 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (841, 594))

# ---- 用例 13：真实多图框 + 内部标题栏（综合）----
# 两个独立图框 A1(841x594) + A2(594x420)，各自内部有 L 形标题栏
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 841, 594)       # A1 图框
msp.add_lwpolyline(                         # A1 的 L 形标题栏
    [(0, 0), (841, 0), (841, 514), (180, 514), (180, 594), (0, 594)],
    close=True,
)
add_closed_rect(msp, 1000, 0, 594, 420)    # A2 图框
msp.add_lwpolyline(                         # A2 的 L 形标题栏
    [(1000, 0), (1594, 0), (1594, 360), (1150, 360), (1150, 420), (1000, 420)],
    close=True,
)
data = to_bytes(doc)
check('多图框+L形标题栏综合 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and (r['width'], r['height']) == (841, 594))

print(f'\n结果: {sum(results)} 通过, {len(results) - sum(results)} 失败')

# ---- 用例 14：IoU 部分重叠去重 ----
# A1 图框用闭合多段线画（841×594），内部标注线与图框顶/左边围出 841×500 的假矩形。
# 两者 IoU = 500×841/(594×841 + 841×500 - 500×841) = 420500/499554 ≈ 84.2% > 50%
# → 假矩形被 IoU 去重去掉，只保留真图框
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 841, 594)       # 真图框（闭合多段线）
add_line_rect(msp, 0, 0, 841, 500)         # 假矩形（LINE，与真图框顶部重叠 84%）
data = to_bytes(doc)
check('IoU 部分重叠去重 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (841, 594))

# ---- 用例 15：同一 layout 内多尺寸标准幅面共存（当前上限 100，无截断） ----
# 在同一 layout 画 5 个标准幅面闭合矩形（A1~A5），实测入选 4 个、A5 落选。
# 各框走哪条通道（rel 分母 = layout 内最大候选 A1 = 499554，layout 总 bbox 面积 1669140）：
#   A1 841×594  面积占比 29.93% → 条件A 直接选中（占比 ≥15%，无需 rel）
#   A2 594×420  面积占比 14.95%（差 0.05% 够不到 15%）+ 短边 420 ∈[400,2000] → 条件B
#   A3 420×297  短边 297 < 400 过不了B，占比 7.47% 也过不了A → 靠条件C（rel 24.97% ≥10%）
#   A4 297×210  同理 → 条件C（rel 12.49% ≥10%）
#   A5 210×148  同理 → rel 仅 6.22% < 10%，**三条通道全挂** → 落选
# 历史注：本用例原断言 frame_count == 2，注释称"A3/A4/A5 被条件 B 的短边下限过滤"——
#   实测只对 A5 成立（且 A5 真正死因是 rel 6.22% < 10%，不是短边）。A3/A4 由条件 C 放行
#   是设计行为（rel 法本身与绝对尺寸无关）。现按实测修正断言与注释，锁住"rel 门槛随
#   候选相对尺寸递减"这条行为：同一批标准幅面里，最大的几个入选、最小的那个被 rel 门槛拒。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 841, 594)       # A1（29.93% → 条件A）
add_closed_rect(msp, 1000, 0, 594, 420)    # A2（短边 420 → 条件B）
add_closed_rect(msp, 1700, 0, 420, 297)    # A3（短边 297，靠条件C rel 24.97%）
add_closed_rect(msp, 2200, 0, 297, 210)    # A4（短边 210，靠条件C rel 12.49%）
add_closed_rect(msp, 2600, 0, 210, 148)    # A5（rel 6.22% < 10% → 三通道全挂，落选）
data = to_bytes(doc)
check('同 layout 多尺寸标准幅面: 入选数受 rel 门槛约束 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and (r['width'], r['height']) == (841, 594))

print(f'\n结果: {sum(results)} 通过, {len(results) - sum(results)} 失败')

# ---- 用例 16：P1 严格矩形判定——残段拼凑假矩形应被过滤 ----
# 构造一个 841x594 的"假矩形"：底边由两段 LINE 拼凑（0→400 和 401→841），
# 没有 LINE 实体能单独覆盖整条底边。P1 应拒绝此直线矩形候选。
# （全实体包围盒兜底仍会产出 841x594，但候选类型是全实体包围盒，不是直线矩形）
doc = make_doc()
msp = doc.modelspace()
# 顶边：完整 LINE 覆盖（P1 通过）
msp.add_line((0, 594), (841, 594))
# 底边：两段残片拼凑（P1 不通过）
msp.add_line((0, 0), (400, 0))
msp.add_line((401, 0), (841, 0))
# 左/右纵边：完整 LINE 覆盖（P1 通过）
msp.add_line((0, 0), (0, 594))
msp.add_line((841, 0), (841, 594))
data = to_bytes(doc)
check('P1 残段拼凑假矩形被过滤 (smart)', lambda: parse(data),
      lambda r: (r['width'], r['height']) == (841, 594)
      and all(c['type'] != '直线矩形' for c in r['candidates']))

# ---- 用例 17：P1 严格矩形判定——完整 LINE 四边应通过 ----
# 标准 add_line_rect 画的矩形，4 条边各有 1 条完整 LINE，P1 应通过
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 841, 594)
data = to_bytes(doc)
check('P1 完整 LINE 四边通过 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (841, 594)
      and r['candidates'][0]['type'] == '直线矩形')

# ---- 用例 18：P1 严格矩形判定——纵边残段拼凑也应被过滤 ----
# 构造假矩形：底/顶/左边完整，右边由两段拼凑
# P1 应拒绝直线矩形候选，全实体包围盒兜底
doc = make_doc()
msp = doc.modelspace()
msp.add_line((0, 0), (841, 0))          # 底边完整
msp.add_line((0, 594), (841, 594))      # 顶边完整
msp.add_line((0, 0), (0, 594))          # 左边完整
msp.add_line((841, 0), (841, 300))      # 右边下半段
msp.add_line((841, 301), (841, 594))    # 右边上半段
data = to_bytes(doc)
check('P1 纵边残段拼凑假矩形被过滤 (smart)', lambda: parse(data),
      lambda r: (r['width'], r['height']) == (841, 594)
      and all(c['type'] != '直线矩形' for c in r['candidates']))

# ---- 用例 19：同一图框画 3 遍（相同 bbox 闭合多段线）→ 源头去重只保留 1 个 ----
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 960, 2520)
add_closed_rect(msp, 0, 0, 960, 2520)
add_closed_rect(msp, 0, 0, 960, 2520)
data = to_bytes(doc)
check('相同 bbox 闭合多段线画3遍 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (960, 2520))

# ---- 用例 20：同一图框两种画法（闭合多段线 + LINE）→ IoU 去重只保留 1 个 ----
# 两个候选 bbox 完全相同、面积相等，旧逻辑 c['area'] < outer['area'] 为 False 跳过，
# 导致 frame_count=2；修复为 <= 后，IoU=1.0 > 0.5 触发去重 → frame_count=1
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 960, 2520)   # 多段线画法
add_line_rect(msp, 0, 0, 960, 2520)     # LINE 画法（同一图框）
data = to_bytes(doc)
check('同一图框多段线+LINE双画法 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (960, 2520))

# ---- 用例 21：同一 LINE 矩形画 2 遍 → 直线矩形检测去重 ----
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 841, 594)
add_line_rect(msp, 0, 0, 841, 594)
data = to_bytes(doc)
check('相同 LINE 矩形画2遍 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (841, 594))

# ---- 用例 22：条件 B 尺寸规整——非整数尺寸闭合多段线被过滤 ----
# 模拟"墙线交错产生的闭合多段线轮廓"：1895.28 x 2463.86（尺寸带小数，ratio 1.30）。
#
# 【为什么 ratio 必须取 1.30 而不是 √2】这是本用例的关键设计点，踩过一次坑：
#   条件C 在条件B **之前**判定，且两者对 ratio 的要求有重叠——
#     C 通道 ratio 下限：非直线矩形 √2×0.95 ≈ 1.344（直线矩形放宽到 1.10）
#     B 通道 ratio 区间：√2±10% 即 [1.273, 1.556]
#   若候选 ratio 落在 [1.344, 1.556]，**条件C 必然先放行**，而 C 不检查尺寸规整，
#   B 永远走不到 → 用例测不到"尺寸规整"（旧版构造 1895.28×2629.36 ratio 1.387 正是
#   栽在这里：它 rel=100%、ratio 1.387 ≥ 1.344，被 C 直接放行，与"尺寸是否规整"无关）。
#   只有 ratio ∈ [1.273, 1.344)（宽度仅 0.071）时，C 被 at_least_sqrt2 挡住、
#   B 的 near_sqrt2 仍通过，尺寸规整才真正成为决定性判据。故本用例取 ratio = 1.30。
#   另注：直线矩形因 C 下限仅 1.10 < B 下限 1.273，**永远走不到 B**——
#   "尺寸规整"目前只对非直线矩形（闭合多段线/块参照/降级包围盒）可达。
#
# 撑大 bbox 使面积占比 <15%（排除条件A 干扰），只剩 C / B 两条通道；
# 尺寸非整数（1895.28 与整数差 0.28 > 0.1）→ C 已被 ratio 挡住 → B 尺寸规整拒绝
# → 未检测到图框
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 1895.28, 2463.86)  # 非整数墙线轮廓（ratio 1.30，落在 B 可达区间）
msp.add_lwpolyline([(100000, 80000), (100010, 80000), (100010, 80010), (100000, 80010)], close=True)  # 撑大 bbox
data = to_bytes(doc)
ok_sz_frac = expect_error_no_frame(data)
results.append(ok_sz_frac)
print(('PASS' if ok_sz_frac else 'FAIL') + '  条件B尺寸规整: 非整数闭合多段线被过滤  ->  未检测到图框')

# ---- 用例 23：条件 B 尺寸规整——整数尺寸闭合多段线在低面积占比下仍保留 ----
# 与用例 22 唯一变量是"尺寸是否取整"（同 ratio 1.30 区间）：
#   1895x2464（ratio 1.3003，取整）→ B 尺寸规整通过 → frame_count = 1
#   两例对照才能证明"尺寸规整"是真正的决定性判据，而非其他通道的副作用。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 1895, 2464)
msp.add_lwpolyline([(100000, 80000), (100010, 80000), (100010, 80010), (100000, 80010)], close=True)
data = to_bytes(doc)
check('条件B尺寸规整: 整数闭合多段线低占比保留 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (1895, 2464))

# ---- 用例 24：条件 B 短边下限——整数但短边 <400 的低占比候选被过滤 ----
# 800 x 300（整数尺寸，短边 300 < 400，门窗类构件）+ 远处小矩形撑大 bbox
# → 面积占比 < 15%，只能走条件 B：尺寸规整通过但短边下限拒绝 → 未检测到图框
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 800, 300)  # 门窗类构件：短边 300 < 400
msp.add_lwpolyline([(100000, 80000), (100010, 80000), (100010, 80010), (100000, 80010)], close=True)
data = to_bytes(doc)
ok_short = expect_error_no_frame(data)
results.append(ok_short)
print(('PASS' if ok_short else 'FAIL') + '  条件B短边下限: 整数短边300被过滤  ->  未检测到图框')

# ---- 用例 25：rel 分母塌缩防护——整图即图框时不得把内部构件当图框 ----
# 场景来源：运煤胶带机.dwg（1:1 设备布置图）。
#   整图外轮廓 149823×77215（ratio 1.94、占 layout 97.6%）命中"内容级底图大框"
#   排除判据①，被踢出 rel 分母；图内其余候选全是设备构件，没有图框级候选，
#   于是分母塌缩到一个 8000×7500 的场地符号（占 layout 0.51%），所有 rel 被放大
#   约 193 倍 → 7 个设备构件（4054×3159 / 4000×2000 / 4000×1600 / 4011×1777…）
#   rel 从真实的 0.06~0.11% 虚报成 10.7~21.3%，全部越过条件C 的 10% 门槛 → fc=7。
#   修复判据：分母占 layout < 1% 且存在占 layout ≥50% 的被排除大框
#   → 判"整图即图框、内无图框级参照系"，该空间 rel 置 0，
#   大外轮廓经条件A（占比≥15%）被选中，构件全部落选。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 149823, 77215)        # 整图外轮廓（ratio 1.94，命中判据①被排除）
add_closed_rect(msp, 30000, 50000, 8000, 7500)   # 场地符号：塌缩后的分母（占 layout 0.51%）
add_closed_rect(msp, 40000, 60000, 4000, 2000)   # 设备构件 ratio 2.0000
add_closed_rect(msp, 50000, 60000, 4000, 1600)   # 设备构件 ratio 2.5000
add_closed_rect(msp, 60000, 10000, 4011, 1777)   # 设备构件 ratio 2.2571
data = to_bytes(doc)
check('rel分母塌缩防护: 整图即图框时内部构件不入选 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and (r['width'], r['height']) == (149823, 77215))

# ---- 用例 26：布局空间网格图框救援——小尺寸页框块成网格时豁免短边预筛 ----
# 场景来源：DS4 四层 阁楼 平面系统图 (1).dwg。
#   6 个真图框全在 **布局1**（图纸空间），是块参照的 6 个实例：尺寸全等
#   434.1×311.1（= A4 短边 297 × 1.0475，布局空间被整体缩放过），3 列 × 2 行。
#   块尺寸短边 311.1 < INSERT_MIN_SHORT_SIDE(500) 且 311.1 不在 A 系列
#   → 原本被预筛当"家具符号块"整体剔除（入库 0 个），程序转而采纳模型空间的
#   5 个内容区大块 → 报 5 个、且报错了空间。
#   修复：布局空间内同块名同尺寸 INSERT ≥4 个 + 成网格（列 ≥2 且行 ≥2）
#   → 豁免短边下限入库，再由 is_frame_like 判定。
doc = make_doc()
msp = doc.modelspace()
blk = doc.blocks.new('PAGE_FRAME')
add_closed_rect(blk, 0, 0, 434.1, 311.1)
lay1 = doc.layouts.new('布局1')
for _cy in range(2):          # 2 行
    for _cx in range(3):      # 3 列
        lay1.add_blockref('PAGE_FRAME', (_cx * 434.1, _cy * 311.1))
data = to_bytes(doc)
check('布局空间网格救援: 3x2 等大页框块豁免短边预筛 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 6
      and r['frame_counts_by_layout'].get('布局 "布局1"') == 6)

# ---- 用例 27：布局空间网格救援的边界——仅"一列排开"不得触发 ----
# 与用例 26 唯一变量是"是否成二维网格"：6 个同尺寸块排成 1 列 × 6 行
#   （短方向紧贴成线，正是"对齐排列装饰块"的特征，如柱网/门窗阵列）
#   → 行列数不满足 ≥2 与 ≥2 → 不救援 → 短边 311.1 < 500 仍被剔除
#   → 布局空间无 INSERT 候选入库。此时仅剩"全实体包围盒"降级兜底
#   （434.1 × 1866.6），证明救援确实没有把这些块当页框收进来。
doc = make_doc()
msp = doc.modelspace()
blk = doc.blocks.new('COL_ARR')
add_closed_rect(blk, 0, 0, 434.1, 311.1)
lay1 = doc.layouts.new('布局1')
for _i in range(6):
    lay1.add_blockref('COL_ARR', (0, _i * 311.1))
data = to_bytes(doc)
check('布局空间网格救援: 单列排开不触发救援（无块参照入库）', lambda: parse(data),
      lambda r: r['candidates']
      and all(c['type'] != '块参照插入' for c in r['candidates'])
      and r['frame_count'] == 1
      and (r['width'], r['height']) == (434, 1867))

# ---- 用例 28：布局空间优先——布局空间已成版时剔除模型空间候选 ----
# 场景来源：DS4（同上）。布局1 识别出 6 个网格排版图纸页后，模型空间同时"认出"
#   5 个巨型内容区块（9900×12900 等，实为 1:1 的内容区轮廓），二者叠加会把
#   fc 从正确的 6 抬到 11、且主框尺寸取错（取模型空间最大的 13423×9014）。
#   修复：布局空间已识别 ≥4 个图框且模型空间也有候选 → 剔除模型空间全部候选。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 13423, 9014)     # 模型空间内容区块（会被 C 放行）
add_closed_rect(msp, 20000, 0, 8926, 13464) # 同上
blk2 = doc.blocks.new('PAGE_FRAME2')
add_closed_rect(blk2, 0, 0, 434.1, 311.1)
lay2 = doc.layouts.new('布局1')
for _cy in range(2):
    for _cx in range(3):
        lay2.add_blockref('PAGE_FRAME2', (_cx * 434.1, _cy * 311.1))
data = to_bytes(doc)
check('布局空间优先: 模型空间候选被整体剔除 (smart)', lambda: parse(data),
      lambda r: r['frame_count'] == 6
      and r['frame_counts_by_layout'] == {'布局 "布局1"': 6}
      and (r['width'], r['height']) == (434, 311))

# ---- 用例 31：条件G·同模板缩放套图救援——小尺寸实例带标题栏条纹被救回 ----
# 场景复刻胜利公寓２：2 个大图框（26305×18450 直线矩形，rel=100% 过条件C）+
#   1 个同比例小图框（6576×4613，ratio 同为 1.4257）。小框 rel=6.25% <10% 被 C 拒、
#   短边 4613 >2000 被 B 拒、area_ratio <15% 被 A 拒 → 必须靠条件G：
#   小框右侧画标题栏条纹（两条全高竖线围出带宽 11%×框宽 + 带内 2 条横向分隔线）
#   → 条纹签名命中 + 与已入选大框同 ratio（±1%）→ 救援，fc=3。
def build_scaled_sheets_doc(with_stripe):
    doc = make_doc()
    msp = doc.modelspace()
    add_line_rect(msp, 0, 0, 26305, 18450)
    add_line_rect(msp, 30000, 0, 26305, 18450)
    sx, sy, sw, sh = 60000, 0, 6576, 4613
    add_line_rect(msp, sx, sy, sw, sh)
    if with_stripe:
        bx0 = sx + sw * 0.89           # 条纹左边缘（带宽 11%×框宽）
        # 条纹左竖线两端各缩进 1%（真实图纸标题栏竖线不与框边完全贯通，
        # 否则会被 has_internal_divider 判为"拼合外包络"拒绝整框）
        msp.add_line((bx0, sy + sh * 0.01), (bx0, sy + sh * 0.99))
        # 右侧全高竖线 = 框边界本身（add_line_rect 已画），只需分隔横线
        msp.add_line((bx0, sy + sh * 0.45), (sx + sw, sy + sh * 0.45))
        msp.add_line((bx0, sy + sh * 0.75), (sx + sw, sy + sh * 0.75))
    return to_bytes(doc)

check('条件G同模板套图: 带标题栏条纹的小尺寸实例被救回 (smart)',
      lambda: parse(build_scaled_sheets_doc(True)),
      lambda r: r['frame_count'] == 3
      and r['frame_counts_by_layout'] == {'模型空间': 3})

# ---- 用例 32：条件G边界——无条纹结构不救援 ----
# 与用例 31 唯一变量是"小框是否画了标题栏条纹"：无条纹 → 结构签名不成立，
#   小框仍被 A/B/C/D 全通道否决 → fc=2（防"同 ratio 就放行"的过度救援）
check('条件G同模板套图: 无条纹的同比例小框不救援 (smart)',
      lambda: parse(build_scaled_sheets_doc(False)),
      lambda r: r['frame_count'] == 2
      and (r['width'], r['height']) == (26305, 18450))

# ---- 用例 33：条件H·主导比例内容救援——小尺寸实例框内内容丰富被救回 ----
# 场景复刻春风公寓２：4 个 26000×18000 闭合多段线图框（ratio 1.4444，rel=100%
#   全过条件C → 主导比例成立）+ 1 个同比例小框（5200×3600，ratio 1.4444）。
#   小框 rel=4% <10% 被 C 拒、area_ratio 极小被 A 拒、短边 3600 >2000 被 B 拒、
#   非直线矩形 D 不适用、无条纹 G 不命中 → 必须靠条件H：比例命中主导模板
#   （±1%）+ 框内完全包含 ≥12 个实体（真页面框内必有图纸内容）→ 救援，fc=5。
#   远端散点把 layout 总面积拉大（压低 area_ratio，模拟真实图纸的内容散布）。
def build_dominant_ratio_doc(small_inside_frame):
    doc = make_doc()
    msp = doc.modelspace()
    for _i in range(4):
        add_closed_rect(msp, _i * 30000, 0, 26000, 18000)
    if small_inside_frame:
        # decoy：与用例 33 唯一变量是位置——小框画在第一个已入选图框内部
        # （防护栏：已被已入选图框包住的候选不救援）→ fc=4
        sx, sy = 2000, 2000
    else:
        sx, sy = 130000, 0
    add_closed_rect(msp, sx, sy, 5200, 3600)
    for _k in range(12):   # 框内 12 条短线（图纸内容；竖线错位不成矩形）
        msp.add_line((sx + 100 + _k * 400, sy + 100),
                     (sx + 100 + _k * 400, sy + 300 + (_k % 3) * 200))
    msp.add_line((400000, 50000), (400100, 50100))   # 远端散点拉大总面积
    return to_bytes(doc)

check('条件H主导比例: 框内内容丰富的同比例小框被救回 (smart)',
      lambda: parse(build_dominant_ratio_doc(False)),
      lambda r: r['frame_count'] == 5
      and r['frame_counts_by_layout'] == {'模型空间': 5})

# ---- 用例 34：条件H边界——已被已入选图框包住的候选不救援 ----
# 同一小框放进 26000 图框内部：嵌套去重本会剔掉它，救援只是噪音且有虚增风险
#   （春风公寓２ 实证：1280×1850 虚线窗套 decoy）→ 防护栏跳过 → fc=4
check('条件H主导比例: 已入选图框内部的同比例小框不救援 (smart)',
      lambda: parse(build_dominant_ratio_doc(True)),
      lambda r: r['frame_count'] == 4
      and (r['width'], r['height']) == (26000, 18000))

# ---- 用例 35：包裹框剔除·主导比例——内容区边界包住单个真页框时剔外留内 ----
# 场景复刻春风公寓２：67627×31514（ratio 2.146，虚线内容区边界，rel=100% 过
#   条件C）包住 1 张 26000×18000 主导页框。旧规则"组数≥2 或 同组≥3"都不满足
#   → 包裹框漏剔 → 去重"留大剔小"把真页框吃了（主框错误地取 67627×31514）。
#   新判据：内部尺寸组比例命中主导模板（±1%）且与外框比例差 >10% → 剔外框。
#   断言主框 = 26000×18000（基线行为下主框会是 67627×31514）。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 67627, 31514)          # 内容区边界（ratio 2.146）
add_closed_rect(msp, 2000, 2000, 26000, 18000)    # 被包住的真页框
for _i in range(3):
    add_closed_rect(msp, 80000 + _i * 30000, 0, 26000, 18000)
data = to_bytes(doc)
check('包裹框主导比例: 内容区边界被剔、内部真页框保留 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and (r['width'], r['height']) == (26000, 18000))

# ---- 用例 36：INSERT rotation 单位是「度」——cos/sin 必须先转弧度（胜利公寓２ 马桶块教训） ----
# 块定义 297×210（A4 横版），以 rot=90、scale=10 插入。真实世界 bbox：
#   R(90°)·S·p = (−y, x)，四角 (0,0)(2970,0)(2970,2100)(0,2100) → (0,0)(0,2970)(−2100,2970)(−2100,0)
#   + insert(100000,100000) → bbox=(97900,100000)-(100000,102970)，尺寸 2100×2970。
# 旧实现把 90（度）直接喂给 math.cos（按弧度解释≈-0.448/0.894，相当于转 116.6°），
# bbox 尺寸/位置全错（胜利公寓２ 马桶块 500×750 rot=180 被算成 900×850、飞出 100 万 mm）。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='FRAME_ROT90')
add_closed_rect(_blk, 0, 0, 297, 210)
msp.add_blockref('FRAME_ROT90', (100000, 100000), dxfattribs={'rotation': 90, 'xscale': 10, 'yscale': 10})
data = to_bytes(doc)
def _check_rot_insert(r):
    c = r['candidates'][0]
    bx1, by1, bx2, by2 = c['bbox']
    return (c['width'], c['height']) == (2100, 2970) \
        and (round(bx1), round(by1), round(bx2), round(by2)) == (97900, 100000, 100000, 102970)
check('INSERT旋转: rot=90 度→弧度换算后 bbox 与 DXF 规范一致 (smart)',
      lambda: parse(data), _check_rot_insert)

# ---- 用例 37：非零块基点——p_world = insert + R·S·(p − base) 的 base 必须减掉 ----
# 同块但几何画在 (500,500)-(797,710)、base_point=(500,500)：世界 bbox 应与
# "几何绕原点画 + 基点 0" 完全等价（2100×2970 @ (97900,100000)）。
# 旧实现不减基点 → bbox 平移 base×scale×旋转，位置错 21 万 mm 以上。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='FRAME_BASE')
add_closed_rect(_blk, 500, 500, 297, 210)
_blk.block.dxf.base_point = (500, 500, 0)
msp.add_blockref('FRAME_BASE', (100000, 100000), dxfattribs={'rotation': 90, 'xscale': 10, 'yscale': 10})
data = to_bytes(doc)
def _check_base_insert(r):
    c = r['candidates'][0]
    bx1, by1, bx2, by2 = c['bbox']
    return (c['width'], c['height']) == (2100, 2970) \
        and (round(bx1), round(by1), round(bx2), round(by2)) == (97900, 100000, 100000, 102970)
check('INSERT基点: 非零 base_point 被正确扣除 (smart)',
      lambda: parse(data), _check_base_insert)

# ---- 用例 38：条件I 表格内容页救援（香槟半岛：实际 27 / 检出 26） ----
# 「图纸目录」页：闭合多段线外框 19945×15703（ratio 1.2703 非 √2、非主导、
# rel 面积小、无标题栏）→ 条件A~H 全拒；内部是规则表格（密集横线行 +
# 竖向列线）→ 条件I 依"框内横线 ≥30 且竖线 ≥6"救回。
# 4 个同尺寸大图框 2×2 排列（过条件A），目录框画在旁边，fc 应为 5。
doc = make_doc()
msp = doc.modelspace()
_bw, _bh = 40000, 28000
for _i in range(2):
    for _j in range(2):
        add_line_rect(msp, _i * _bw, _j * _bh, _bw, _bh)
# 目录框（外框闭合多段线 + 内部表格线）
_cx, _cy = 90000, -30000
add_closed_rect(msp, _cx, _cy, 19945, 15703)
for _k in range(32):     # 32 条横线（表格行，完全在框内）
    _yy = _cy + 200 + _k * 460
    msp.add_line((_cx + 150, _yy), (_cx + 19945 - 150, _yy))
for _k in range(7):      # 7 条竖线（列分隔）
    _xx = _cx + 2400 + _k * 2400
    msp.add_line((_xx, _cy + 150), (_xx, _cy + 15703 - 150))
data = to_bytes(doc)
check('条件I表格页: 图纸目录框被救回, fc=5 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 5
      and any(abs(c['width'] - 19945) < 5 and abs(c['height'] - 15703) < 5
              for c in r['candidates']))

# ---- 用例 39：条件I 防护栏——表格框被已入选图框包住时不救援 ----
# 内部表格框嵌在大图框里（是大图的插图内容）→ 条件I 不得救援，fc=1。
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 40000, 28000)
msp.add_line((45000, -1000), (46000, 1000))   # 框外小实体，撑开 layout 总 bbox
add_closed_rect(msp, 5000, 3000, 8000, 6000)
for _k in range(32):
    _yy = 3200 + _k * 180
    msp.add_line((5200, _yy), (12900, _yy))
for _k in range(7):
    _xx = 5600 + _k * 900
    msp.add_line((_xx, 3200), (_xx, 8900))
data = to_bytes(doc)
check('条件I防护栏: 被包住的表格框不救援, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1)

# ---- 用例 40：包裹框剔除的块参照强证据（1号2号楼柱平法施工图） ----
# 底图参照外框（INSERT 块 90000×180000）包住 4 个同尺寸直线矩形图框
# （2×2 网格）。旧规则对"块参照插入"一律豁免包裹剔除 → 4 个真图框被嵌套
# 去重"留大剔小"全吃掉，只剩底图外框（fc=1）。新规则：块参照在内部尺寸组
# 强证据（同尺寸组 ≥3）时不再豁免 → 底图外框被剔，fc=4。
# R30：4 个页框走条件 D（rel 6.8%<10%、同尺寸≥2），D 新增内容证据后
# 每框补 4 个内部小矩形（16 实体 ≥12）——真实柱网图页框内本就有内容。
doc = make_doc()
msp = doc.modelspace()
for _i in range(2):
    for _j in range(2):
        _fx, _fy = _i * 40000, _j * 86000
        add_line_rect(msp, _fx, _fy, 39598, 28000)   # ratio≈√2
        for _m in range(2):                           # 框内内容（R30 条件D 内容证据）
            for _n in range(2):
                add_line_rect(msp, _fx + 2000 + _m * 18000, _fy + 2000 + _n * 12000,
                              1000, 800)
_blk_w = doc.blocks.new(name='BASE_WRAP')
add_closed_rect(_blk_w, 0, 0, 90000, 180000)
msp.add_blockref('BASE_WRAP', (-1000, -1000))
msp.add_line((100000, 0), (100500, 300))    # 框外小实体撑开 layout 总 bbox
data = to_bytes(doc)
check('包裹框强证据: 底图外框块被剔, 4 个真图框保留 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and all(abs(c['width'] - 39598) < 5 for c in r['candidates']))

# ---- 用例 41：孤证复核（一层.dwg：实际 0 图框，误检 4 个） ----
# 无图框图纸（纯平面图+大样标注）：4 个尺寸比例各不相同的标注轮廓块
# （12240×8770 / 7280×3440 / 3290×4775 / 5160×2730，ratio 两两相差 >1%，
# 无同尺寸副本、无标题栏），靠条件C（rel≥10%，分母=最大候选 12240×8770
# 本身也是标注块）全部混入。孤证复核：仅经 C 通过 + area_ratio<5% +
# 无互证 → 全部剔除，fc=0。
doc = make_doc()
msp = doc.modelspace()
_blk1 = doc.blocks.new(name='ANNO_BIG')
add_closed_rect(_blk1, 0, 0, 12240, 8770)      # ratio 1.396
msp.add_blockref('ANNO_BIG', (30000, 0))
_blk2 = doc.blocks.new(name='ANNO_B2')
add_closed_rect(_blk2, 0, 0, 7280, 3440)       # ratio 2.116
msp.add_blockref('ANNO_B2', (50000, 0))
_blk3 = doc.blocks.new(name='ANNO_B3')
add_closed_rect(_blk3, 0, 0, 3290, 4775)       # ratio 1.451（竖向）
msp.add_blockref('ANNO_B3', (62000, 0))
add_line_rect(msp, 70000, 0, 75160, 2730)      # ratio 1.890 直线矩形
msp.add_line((300000, 0), (300500, 300))       # 框外远端小实体撑大 layout bbox（压低 area_ratio）
data = to_bytes(doc)
check('孤证复核: 无图框图纸的标注轮廓全被剔, fc=0 (smart)',
      lambda: expect_error_no_frame(data), lambda ok: ok)

# ---- 用例 42：孤证复核防护栏——有同尺寸副本的候选不剔除 ----
# 同用例 41 场景，但 12240×8770 画两份（同尺寸副本互证）→ 该对保留，
# 其余 3 个孤证仍剔除，fc=2。
doc = make_doc()
msp = doc.modelspace()
_blk1 = doc.blocks.new(name='ANNO_PAIR')
add_closed_rect(_blk1, 0, 0, 12240, 8770)
msp.add_blockref('ANNO_PAIR', (30000, 0))
msp.add_blockref('ANNO_PAIR', (50000, 0))      # 同尺寸副本（间距拉开，避免嵌套/IoU 去重）
_blk2 = doc.blocks.new(name='ANNO_B2x')
add_closed_rect(_blk2, 0, 0, 7280, 3440)
msp.add_blockref('ANNO_B2x', (70000, 0))
_blk3 = doc.blocks.new(name='ANNO_B3x')
add_closed_rect(_blk3, 0, 0, 3290, 4775)
msp.add_blockref('ANNO_B3x', (82000, 0))
add_line_rect(msp, 90000, 0, 95160, 2730)
msp.add_line((300000, 0), (300500, 300))
data = to_bytes(doc)
check('孤证复核防护栏: 有同尺寸副本互证的候选保留, fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and all(abs(c['width'] - 12240) < 5 for c in r['candidates']))

# ---- 用例 43：孤证复核互证⑥——封面双线框（嵌套内外框）不剔除 ----
# 四川自贡19 实证：封面是闭合多段线双线框（外 30443×20816 + 内 29243×19616，
# 内占外面积 90.6%），非块、无条纹、唯一尺寸、ratio 1.4625 与图纸主导 1.4082
# 不同——互证①~⑤全落空，外框被孤证复核误杀（fc 19→18）。
# 构造：18 个 28331×20119 主导块（撑起主导比例与 C 的分母）+ 封面双线框。
# 预期：外框经互证⑥（内含不同尺寸 frame_like 候选）保留，内框作为重复画法
# 被剔，fc=19。
doc = make_doc()
msp = doc.modelspace()
_blk_main = doc.blocks.new(name='SDSDFD')
add_closed_rect(_blk_main, 0, 0, 28331, 20119)
for _i in range(18):
    msp.add_blockref('SDSDFD', (30000 + _i * 32000, 0))
# 封面：外框 + 内框（双线，间距 ~600，面积比 90.6% 与实证一致）
add_closed_rect(msp, 351791, -92918, 30443, 20816)   # 外框
add_closed_rect(msp, 352391, -92318, 29243, 19616)   # 内框
data = to_bytes(doc)
check('孤证复核互证⑥: 封面双线框(嵌套内外框)保留, fc=19 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 19
      and any(abs(c['width'] - 30443) < 5 and abs(c['height'] - 20816) < 5
              for c in r['candidates'])
      and not any(abs(c['width'] - 29243) < 5 for c in r['candidates']))


# =====================================================================
# 第23轮：图签附栏外扩合并（105100口径）
# =====================================================================
from app import _expand_frames_with_title_strips, _cluster_title_boxes, _filter_title_strips

# ---- 用例：纯函数——图签条聚类与尺寸筛选 ----
_boxes = [(0, 0, 7000, 18200), (72000, 0, 79000, 18200)]   # 两个独立图签条
_strips = _filter_title_strips(_cluster_title_boxes(_boxes))
check('附栏外扩: 图签条聚类识别 2 条', lambda: _strips,
      lambda s: len(s) == 2)

# ---- 用例：纯函数——主框+左侧邻位图签条 → 外扩 21000 ----
def _cand(x1, y1, x2, y2, layout='模型空间'):
    return {'type': '直线矩形', 'bbox': (x1, y1, x2, y2),
            'width': x2 - x1, 'height': y2 - y1, 'area': (x2 - x1) * (y2 - y1),
            'layout': layout, 'ratio': max(x2 - x1, y2 - y1) / min(x2 - x1, y2 - y1)}

# 模拟错排双行：行1主框A(-100000,0)~(-15858,59400) 右内侧图签条；行2主框B在其右侧、y错开
cands = [_cand(-100000, 0, -15858, 59400),          # 主框A 84100x59400
         _cand(-88000, -2679, -3800, 56721)]        # 主框B 84200x59400 (近似)
strips = [(-21600, 300, -14600, 18500)]             # A 的右内侧图签条 7000x18200
# B 左边 -88000，图签条中心 -18100：距 B 左边 69900 太远 → B 不扩
# A 右边 -15858，图签条中心 -18100：距 A 右边 2242 → 在 A 右侧带内，但宿主是 A 自身 → 不扩
_out, _n = _expand_frames_with_title_strips([_cand(-100000, 0, -15858, 59400),
                                             _cand(-88000, -2679, -3800, 56721)], strips)
check('附栏外扩: 自身图签条不作为自身外扩依据', lambda: _n, lambda n: n == 0)

# 错排：B 在 A 右侧（不重叠，B 左边距 A 右边 13788）且 y 错开 2679，
# A 的右内侧图签条（中心 -18100）落在 B 左边 -2070 外侧 16030 → B 外扩 21000
cands2 = [_cand(-100000, 0, -15858, 59400),
          _cand(-2070, -2679, 82030, 56721)]
_out2, _n2 = _expand_frames_with_title_strips([dict(c) for c in cands2], strips)
check('附栏外扩: 错排邻位图签条 → 左侧外扩 21000',
      lambda: (_n2, _out2[1]['width'], _out2[1]['height']),
      lambda t: t[0] == 1 and abs(t[1] - 105100) < 1 and abs(t[2] - 59400) < 1)

# ---- 用例：守卫 a——竖版/小页/已含附栏口径的框不外扩 ----
cands3 = [_cand(-100000, 0, -15858, 59400),
          _cand(-2070, -2679, 82030, 108379)]       # B 竖版
_out3, _n3 = _expand_frames_with_title_strips([dict(c) for c in cands3], strips)
check('附栏外扩: 竖版主图框不外扩', lambda: _n3, lambda n: n == 0)

cands4 = [_cand(-100000, 0, -15858, 40000),          # B 高 40000 < 50000 小页
          _cand(-2070, -2679, 82030, 36721)]
_out4, _n4 = _expand_frames_with_title_strips([dict(c) for c in cands4], strips)
check('附栏外扩: 小页(短边<50000)不外扩', lambda: _n4, lambda n: n == 0)

cands5 = [_cand(-100000, 0, 5100, 59400),            # A 已 105100 宽
          _cand(23000, -2679, 107100, 56721)]
strips5 = [(-21600, 300, -14600, 18500), (32000, 300, 39000, 18500)]
_out5, _n5 = _expand_frames_with_title_strips([dict(c) for c in cands5], strips5)
check('附栏外扩: 宽度≥95000 已含附栏口径不重复外扩', lambda: _n5, lambda n: n == 0)

# ---- 用例：守卫 d——宿主是不同规格小页(42000高)不外扩 ----
cands6 = [_cand(-100000, 0, -15858, 59400),          # 主框 59400 高
          _cand(-2070, -2679, 82030, 36721)]         # 宿主 B 高 39400 < 0.85*59400
_out6, _n6 = _expand_frames_with_title_strips([dict(c) for c in cands6], strips)
check('附栏外扩: 宿主非同排同规格(42000小页)不外扩', lambda: _n6, lambda n: n == 0)

# ---- 用例：端到端——双行错排直线图框 + J-图框层图签条 → 105100 + 84100 (smart) ----
doc = make_doc()
msp = doc.modelspace()
# 行1 主框A (-100000,0) 84100x59400（4条LINE）
add_line_rect(msp, -100000, 0, 84100, 59400)
# A 右内侧图签条（J-图框层 LWPOLYLINE 7000x18200）
msp.add_lwpolyline([(-21600, 300), (-14600, 300), (-14600, 18500), (-21600, 18500)],
                   close=True, dxfattribs={'layer': 'J-图框'})
# 行2 主框B 错排 (-2070,-2679) 84100x59400（B 左边距 A 图签条中心 16030）
add_line_rect(msp, -2070, -2679, 84100, 59400)
data = to_bytes(doc)
check('附栏外扩: 端到端错排双框 → 105100 + 84100 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and any(abs(c['width'] - 105100) < 2 for c in r['candidates'])
      and any(abs(c['width'] - 84100) < 2 for c in r['candidates']))


# =====================================================================
# 第27轮：闭合边框证据通道（INSERT 内容块校验升级：闭合矩形成环判据）
# =====================================================================

# ---- 用例 44：LINE 横竖线配对成闭合矩形的块放行 ----
# 块内边框由 4 条独立 LINE 画成（无闭合多段线）→ 闭合证据通道应识别成环放行。
# 块内另散布 6 条 1000mm 小线段（< 30% 块长，不参与成框），fc=1。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='LINE_FRAME')
add_line_rect(_blk, 0, 0, 71846, 50889)          # 4 条 LINE 闭合矩形（复刻 9ZZZW 尺寸）
for _k in range(6):
    _blk.add_line((1000 + _k * 3000, 20000), (2000 + _k * 3000, 20000))
msp.add_blockref('LINE_FRAME', (0, 0))
data = to_bytes(doc)
check('闭合证据: LINE横竖线成环的图框块放行, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['candidates'][0]['width'] - 71846) < 5)

# ---- 用例 45：长散线无闭合矩形 → 块剔除（9ZZZW 场景复刻） ----
# 块内最长线 71846（100% 块长，远超 R26 的 30% 构件判据——R26 拦不住），
# 但横竖线凑不成闭合矩形：唯一横线对的竖向支撑覆盖 68.8% < 80% → 主判剔除。
# 注：入库即剔 → 0 候选 → app 走「全实体包围盒」降级兜底（区别于入库后被
# 孤证复核剔掉才抛"未检测到图框"），故断言 = 无『块参照插入』类型候选。
def _no_block_candidate(data):
    try:
        r = parse(data)
    except RuntimeError as e:
        return '未检测到图框' in str(e)
    return not any(c.get('type') == '块参照插入' for c in r.get('candidates', []))


doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='PLAN_CONTENT')
_blk.add_line((0, 0), (71846, 0))                # 横线 100% 块长
_blk.add_line((0, 50889), (45317, 50889))        # 横线 63% 块长
_blk.add_line((0, 0), (0, 35000))                # 竖线 y 覆盖 35000/50889=68.8% <80%
_blk.add_line((60000, 10000), (60000, 45000))    # 竖线在横线对重叠带外(45317)
msp.add_blockref('PLAN_CONTENT', (0, 0))
data = to_bytes(doc)
check('闭合证据: 长散线无闭合矩形的平面图块被剔 (smart)',
      lambda: _no_block_candidate(data), lambda ok: ok)

# ---- 用例 46：首尾重合的开放 LWPOLYLINE（忘设 closed 标志）放行 ----
# CAD 常见画法：矩形多段线首尾点重合但未设 closed 标志 → 视同闭合，fc=1。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='OPEN_LOOP')
_blk.add_lwpolyline([(0, 0), (40000, 0), (40000, 28000), (0, 28000), (0, 0)])
msp.add_blockref('OPEN_LOOP', (0, 0))
data = to_bytes(doc)
check('闭合证据: 首尾重合的开放多段线视同闭合, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['candidates'][0]['height'] - 28000) < 5)

# ---- 用例 47：R26 稀疏散件场景回归（-1fxhs0421 场景）仍被剔 ----
# 块内 56 个 200mm 小闭合方块散布成 124800×78200 大 bbox：闭合证据 200mm
# << 50% 块长（主判剔），最长构件同样不足（辅判兜底）。断言同用例 45。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='SCATTER')
for _k in range(56):
    _sx = (_k % 8) * 17800
    _sy = (_k // 8) * 13000
    _blk.add_lwpolyline([(_sx, _sy), (_sx + 200, _sy), (_sx + 200, _sy + 200), (_sx, _sy + 200)],
                        close=True)
msp.add_blockref('SCATTER', (0, 0))
data = to_bytes(doc)
check('闭合证据: 稀疏散件撑大bbox的块仍被剔 (smart)',
      lambda: _no_block_candidate(data), lambda ok: ok)


# ---- 用例 48：孤证互证——同尺寸副本对保留（R27 回归期间曾设想"资格下限"拦尘埃副本，
#      被全量插桩数据证伪后改为正路径断言） ----
# 两个 960×2400 门窗级小框（area_ratio ~0.03%，离群实体把 layout 撑到 300 万 mm 宽）
# 靠互证①保留。原设想给互证①②⑥加 area_ratio/尺寸/类型资格下限拦掉此类"尘埃副本"，
# 但 32 张基线图 413 条救援样本证明所有单维特征全部重叠：
#   尺寸（420mm A3 真框 < 尘埃 957mm）、type（尘埃有闭合多段线、真框有直线矩形副本对
#   滨江 11093×8535×15 / 地下室电力 73600×57400×11）、ar（DS4 真框 0.004% < 尘埃
#   0.033%）、rel（全部 10~100% 交叉）——互证资格路线整体不可行。
# 一层.dwg 的正确修法是 rel 分母计回（R27 拒块中构件合格者），本用例改测互证正路径；
# "微型无框图纸两副本误检 2 框"为已知局限（真实基线 32 张无此场景）。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='DOOR_SYM')
add_line_rect(_blk, 0, 0, 960, 2400)
msp.add_blockref('DOOR_SYM', (30000, 0))
msp.add_blockref('DOOR_SYM', (50000, 0))
msp.add_line((3000000, 0), (3000500, 800))   # 离群实体撑爆 layout 总 bbox
data = to_bytes(doc)
check('孤证互证: 同尺寸副本对经互证①保留 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and all(abs(c['width'] - 960) < 5 for c in r['candidates']))


# ---- 用例 49：孤证互证⑤扩词——块名图幅代号（A0~A9）救援独立图框 ----
# 真实场景（13013-11-AW-FP.dwg）：A1 加长 1/4 图幅（1:150 出图）的独立图纸
# 157650×89100，块名 A1+0.25，全图唯一、ratio 1.769 无同比例兄弟、图签条纹
# 未被检出（③盲点）→ 六通道 score=0 被孤证复核误杀。块名图幅代号是设计者
# 按图幅命名图框块的强证据，与"图框/frame"同权重。词边界 (?<![a-z0-9])a[0-9](?![0-9])
# 防 A$C... 匿名块名（其 a6 前是字母数字）与 A31005 类编号误伤。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='A1+0.25')
add_line_rect(_blk, 0, 0, 84100, 59400)
msp.add_blockref('A1+0.25', (100000, 0))
msp.add_line((3000000, 0), (3000500, 800))   # 离群实体撑爆 layout 总 bbox
data = to_bytes(doc)
check('孤证互证⑤扩词: 块名图幅代号 A1+0.25 救援独立图框 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['candidates'][0]['width'] - 84100) < 5)


# ---- 用例 50：孤证互证⑤扩词防误伤——匿名块名含"a6"不命中图幅代号 ----
# A$C6A6C4DA6 类 ACAD 匿名块名 lower 后含 a6，但 a6 前是字母数字（无词边界），
# 不得命中图幅代号——同构造的孤立尘埃块仍应被孤证复核剔除。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='A$C6A6C4DA6')
add_line_rect(_blk, 0, 0, 960, 2400)
msp.add_blockref('A$C6A6C4DA6', (30000, 0))
msp.add_line((3000000, 0), (3000500, 800))   # 离群实体撑爆 layout 总 bbox
data = to_bytes(doc)
check('孤证互证⑤扩词防误伤: 匿名块名 a6 无词边界仍剔除 (smart)',
      lambda: expect_error_no_frame(data), lambda ok: ok)


# ---- 用例 51：条件D 内容证据——同尺寸重复的空轮廓框不救援（坡道大样6月） ----
# 真实场景：坡道大样6月 三个同尺寸 4700×6000"机动车库"房间轮廓（ratio 1.2766
# 恰在 √2±10% 容差内擦线 9.73%、rel 2.26%≥1%、同尺寸≥2）全过条件 D 误检
# fc 12→13。真页框内必有图纸内容（电子称 14894×10531×2 实测 517/485 个实体），
# 重复内容轮廓内部近乎空白（实测 0/0/1）——条件 D 要求框内严格内部实体
# ≥12 才判页框。大框 40000×30000 撑 rel 分母（其自身 rel=100% 走条件 C，
# 随后被孤证复核剔除：无任何互证）。
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 40000, 30000)
add_line_rect(msp, 60000, 0, 4700, 6000)
add_line_rect(msp, 80000, 0, 4700, 6000)
msp.add_line((3000000, 0), (3000500, 800))   # 离群实体撑爆 layout 总 bbox
data = to_bytes(doc)
check('条件D内容证据: 同尺寸空轮廓框不救援, fc=0 (smart)',
      lambda: expect_error_no_frame(data), lambda ok: ok)


# ---- 用例 52：条件D 内容证据正路径——框内有内容的同尺寸页框保留 ----
# 与用例 51 同构，但两个 4700×6000 框内各有 13 条横线（≥12）→ 条件 D 放行
# → 大框被孤证复核剔除 → fc=2。
doc = make_doc()
msp = doc.modelspace()
add_line_rect(msp, 0, 0, 40000, 30000)
add_line_rect(msp, 60000, 0, 4700, 6000)
for _k in range(13):
    msp.add_line((60100, 100 + _k * 400), (64000, 100 + _k * 400))
add_line_rect(msp, 80000, 0, 4700, 6000)
for _k in range(13):
    msp.add_line((80100, 100 + _k * 400), (84000, 100 + _k * 400))
msp.add_line((3000000, 0), (3000500, 800))   # 离群实体撑爆 layout 总 bbox
data = to_bytes(doc)
check('条件D内容证据: 框内有内容的同尺寸页框保留 fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and all(abs(c['width'] - 4700) < 5 for c in r['candidates']))


print(f'\n结果: {sum(results)} 通过, {len(results) - sum(results)} 失败')
sys.exit(0 if all(results) else 1)
