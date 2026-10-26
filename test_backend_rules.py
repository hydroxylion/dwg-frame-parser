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
import os
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


def expect_error_no_frame_env(data, **switches):
    """带环境开关的"未检测到图框"断言（用后恢复）"""
    import os as _os
    _saved = {k: _os.environ.get(k) for k in switches}
    for _k, _v in switches.items():
        _os.environ[_k] = _v
    try:
        try:
            parse(data)
            return False
        except RuntimeError as e:
            return '未检测到图框' in str(e)
    finally:
        for _k, _v in _saved.items():
            if _v is None:
                _os.environ.pop(_k, None)
            else:
                _os.environ[_k] = _v


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


# ---- 用例 53：S015——非标比例双线图框被"分母塌缩+包裹剔除"连环误杀 ----
# 真实场景复现：外框 66900×42050（ratio 1.591，偏离√2 12.5%）+ 内框
# 65150×41050（嵌套双线画法，面积比 95.07%）+ 图签块 TUQIAN2（3500×8802
# 竖长 ratio 2.51）+ 框内 4 个内容矩形（4941×2582×2 水箱等轴测轮廓 +
# 1350×3100×2 坑井轮廓）+ 框外小实体撑大 layout。
# 旧逻辑链条：两大框命中分母排除判据①（area_ratio≥0.5 且 ratio 偏离√2>10%）
# 被踢出 rel 分母 → 分母塌缩到图签块（30.8e6，占 layout ~1.1% 恰好躲过
# 1% 塌缩防护）→ 4 个内容矩形 rel 被放大 91 倍（0.45%/0.15% → 41%/14%）
# 全部越过条件 C 且同尺寸互证通过孤证复核 → 两大框反因"各含 2 个独立
# 尺寸组"被包裹剔除 → fc=4（内容冒充图框）。
# 修复：判据① 加"近√2 页面级候选存在性"前提——layout 内不存在近√2 且
# area_ratio≥2% 的候选时，非标比例大框不踢出分母（无稀释对象、踢出只会
# 塌缩）。分母恢复 2.813e9 → 内容矩形 rel 回真实值全拒 → 无包裹剔除 →
# 内框（IoU 95.07%）被去重收掉 → fc=1（外框 66900×42050）。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, -20200, -5500, 66900, 42050)   # 外框（闭合多段线，ratio 1.591）
add_closed_rect(msp, -18950, -5000, 65150, 41050)   # 内框（双线画法）
add_line_rect(msp, -15614, 18693, 4941, 2582)       # 水箱等轴测轮廓 #1
add_line_rect(msp, -13187, 20094, 4941, 2582)       # 水箱等轴测轮廓 #2（部分重叠）
add_line_rect(msp, 14850, -200, 1350, 3100)         # 坑井轮廓 #1
add_line_rect(msp, 17800, -200, 1350, 3100)         # 坑井轮廓 #2
_blk_tq = doc.blocks.new(name='TUQIAN2')            # 图签块（竖长表格，ratio 2.51）
add_closed_rect(_blk_tq, 0, 0, 3500, 8802)
msp.add_blockref('TUQIAN2', (42700, -5000))
msp.add_line((50000, -6000), (50500, -5700))        # 框外小实体撑大 layout bbox
data = to_bytes(doc)
check('S015: 非标比例双线图框保留, 内容矩形不冒充, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['width'] - 66900) < 5 and abs(r['height'] - 42050) < 5)


# ---- 用例 54：D011_t3——45° 斜向内容主导 + 轴对齐图框：旋转矫正不得误转 ----
# 真实场景复现：两个 A2 图框（59400×42000 外框 + 55900×40000 内框，闭合多段线
# 双线画法，轴对齐，孤立摆在 x≈1e6 处）+ 大量 45° 方向长线（斜向轴网，线长占比
# 65.5% ≥ 主体共旋门限 65%）。旧逻辑：主峰 45.5° 偏离水平/垂直超阈值且族占比
# 达标 → 整图旋转 45.5° → 轴对齐图框被转成 45° 斜 → LWPOLYLINE bbox 变
# 71592×71808 近正方形（ratio 1.003 < 1.05）被比例过滤拒 → fc=0。
# 修复：旋转触发前探测「轴对齐图框级闭合矩形」（4 顶点、x/y 各≤2 个不同值、
# ratio∈[1.34,2.5]、短边≥2000）——存在则豁免不旋转（检测目标本来就是正的）。
# 修复后：不旋转 → 图框正常识别，内框被嵌套去重剔（面积比 89.6%<95%）→ fc=2。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 1084256, -110661, 59400, 42000)   # 图框 #1 外框（A2×100）
add_closed_rect(msp, 1086756, -109661, 55900, 40000)   # 图框 #1 内框
add_closed_rect(msp, 993782, -169274, 59400, 42000)    # 图框 #2 外框
add_closed_rect(msp, 996282, -168274, 55900, 40000)    # 图框 #2 内框
for _i in range(20):                                    # 45° 斜向轴网线（长 74965×20）
    _x0 = _i * 3000
    msp.add_line((_x0, 0), (_x0 + 53000, 53000))
data = to_bytes(doc)
check('D011: 45度斜向内容不误触发旋转, 轴对齐图框 fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and all(abs(c['width'] - 59400) < 5 and abs(c['height'] - 42000) < 5
              for c in r['candidates']))


# ---- 用例 55：UCS 真斜图框——豁免不得误触发，矫正仍生效 ----
# 总图-排水场景：图框整体斜存（顶点 x/y 各 4 个不同值，非轴对齐矩形）
# → 豁免不触发 → 旋转矫正照常把图框转正 → fc=1。锁定豁免只拦"图框本来是正的"
# 的场景，不误伤真斜图。
# 注：角度取 3.5°（1° 直方图桶中心）——旋转矫正按峰值桶中心转正，若真实角度
# 在桶内偏移（如 3.3°）会残留 ≤0.5° 量化误差使 bbox 膨胀 ~500mm（固有行为，
# 与本修复无关）；取桶中心使转正后尺寸精确、断言可用严格容差。
import math as _math
doc = make_doc()
msp = doc.modelspace()
_ang = _math.radians(3.5)
_cos, _sin = _math.cos(_ang), _math.sin(_ang)


def _rot_pts(x, y, w, h):
    return [(x + dx * _cos - dy * _sin, y + dx * _sin + dy * _cos)
            for dx, dy in ((0, 0), (w, 0), (w, h), (0, h))]


msp.add_lwpolyline(_rot_pts(0, 0, 59400, 42000), close=True)      # 外框（斜 3.3°）
msp.add_lwpolyline(_rot_pts(1750, 1000, 55900, 40000), close=True)  # 内框
data = to_bytes(doc)
check('UCS 斜图框: 豁免不误触发, 矫正转正 fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['width'] - 59400) < 2 and abs(r['height'] - 42000) < 2)


# ---- 用例 56：√2 纸张保护的"空壳范围框"破例（d012.dwg：3 实际 4 检出） ----
# BZ 层范围框 518495×335087（ratio 1.5473 偏离 √2 仅 9.41% ≤10% 擦线）罩住
# 真图框 321093×259386 的 91.6% 面积但底边溢出 21845mm（错位半包含）→
# ①条件C rel=100% 自我循环入选（最大候选=分母）②孤证复核靠 ⑥内含
# 200700×126150 嵌套框群拿 1.0 分存活 ③包裹剔除被 √2 保护 continue 跳过
# （且 [1] 错位不进 inners、嵌套框同尺寸归一组 → 主判据 groups=1 也剔不掉）
# ④去重双路失效（非完全嵌套 + IoU 0.42<0.5）→ fc 多 1。
# 修复判据（四条全中才破例剔除大框）：存在候选 面积∈(25%,50%)×big、
# 交集≥90%×其面积、未被完全包含（错位溢出）、且 big bbox 内自由内容
# 线长占比 <10%（排除自身边线与被同 layout 候选 bbox 认领的线段）。
doc = make_doc()
msp = doc.modelspace()
# [0] BZ 层范围框（闭合多段线，ratio 1.5473，√2±10% 内擦线）
add_closed_rect(msp, 2090800, -4552675, 518495, 335087)
# [1][2] 真图框块参照（块定义 321093×259386 = k1，ratio 1.2379 <1.343 走条件I）
_blk_d012 = doc.blocks.new(name='D012_FRAME')
_blk_d012.add_lwpolyline([(0, 0), (321093, 0), (321093, 259386), (0, 259386)], close=True)
msp.add_blockref('D012_FRAME', (2190078, -4574520))   # [1] 底边溢出 [0] 底边 21845mm
msp.add_blockref('D012_FRAME', (2190078, -4979330))   # [2] 在 [0] 下方（不相交）
# 条件I 表格线：框内顶层横线 ≥30 + 竖线 ≥6（d012 实测 382/48 条，取最小充分集）
for _bx, _by in ((2190078, -4574520), (2190078, -4979330)):
    for _k in range(32):
        _yy = _by + 3000 + _k * 7500
        msp.add_line((_bx + 2000, _yy), (_bx + 319093, _yy))
    for _k in range(7):
        _xx = _bx + 30000 + _k * 42000
        msp.add_line((_xx, _by + 2000), (_xx, _by + 257386))
# 嵌套框群（[0] 的孤证 ⑥ 互证来源；真实 d012 坐标——[1][2] 框内各一组
# 200700+195450（尺寸差 2.6% 聚同组 → same_n=2 <3 触发块参照豁免，[1][2]
# 自身不被包裹剔除），去重阶段被 [1][2] 嵌套收掉）
add_closed_rect(msp, 2265921, -4456370, 200700, 126150)   # [1] 内
add_closed_rect(msp, 2269671, -4454870, 195450, 123150)
add_closed_rect(msp, 2265921, -4861180, 200700, 126150)   # [2] 内
add_closed_rect(msp, 2269671, -4859680, 195450, 123150)
# [0] bbox 内自由散线（真实 d012：左缘竖线 + 上方斜线，自由占比实测 3.82%）
msp.add_line((2091000, -4350000), (2091000, -4420000))          # 左缘竖线 ~70000
msp.add_line((2300000, -4217000), (2352000, -4269000))          # 斜线 ~73500
# 远距离散实体撑大 msp 总 bbox（真实 d012：msp_total=3.466e13 → [0] area_ratio
# 0.50% <5% 进孤证复核；用例近似复刻该占比量级）
add_line_rect(msp, 1000000, -1000000, 3000, 2000)
add_line_rect(msp, 3500000, -5500000, 3000, 2000)
data = to_bytes(doc)
check('d012空壳范围框: √2保护破例剔除, fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and not any(abs(c['width'] - 518495) < 5 for c in r['candidates'])
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 321093) < 5 and abs(c['height'] - 259386) < 5) == 2)


# ---- 用例 57：R36 非矩形边界块让位内含真框（d012.dwg 真实块形态复刻） ----
# hfzxhx 块定义内仅 1 条闭合不规则边界多段线（38 顶点 + 3 圆弧、宽 0.5m，矩形度
# 实测 0.7005——基坑/用地范围线形态，非图框）。块 bbox 321093×259386 经 R27 闭合
# 证据通道（旧判据把任何闭合多段线都当边框证据）+ 条件I 表格救援入池，每块 bbox
# 恰完全包住一个真图框 200700×126150（顶层 4 顶点矩形）→ 去重"留大剔小"把真框
# 吃掉 → 报 321093×2（用户在 CAD 里找不到：8 号灰点划线不规则边界是范围线）。
# 修复：包裹剔除后、去重前，"闭合成环但非矩形"的块参照让位给内含显式矩形真框。
# 注意：不能在 R27 入口直接淘汰——R34 空壳破例剔 BZ 518495 范围框依赖 321093
# 候选触发（面积 47.9% ∈(25%,50%)×big + 错位半包含），入口淘汰会让 BZ 复活并
# 反向吃掉 200700A。本用例同时守护该连锁（BZ 必须仍被剔除）。
doc = make_doc()
msp = doc.modelspace()
# [0] BZ 层范围框（同用例 56）
add_closed_rect(msp, 2090800, -4552675, 518495, 335087)
# [1][2] "图框"块参照——块定义换成不规则闭合边界（矩形度 0.835 <0.92，带斜边）
_blk_r36 = doc.blocks.new(name='HFZXHX_IRREG')
_blk_r36.add_lwpolyline([
    (0, 0), (321093, 0), (321093, 50000), (240000, 120000), (321093, 190000),
    (321093, 259386), (180000, 259386), (90000, 170000), (0, 259386),
], close=True)
msp.add_blockref('HFZXHX_IRREG', (2190078, -4574520))
msp.add_blockref('HFZXHX_IRREG', (2190078, -4979330))
# 条件I 表格线（同用例 56：每框内顶层横线 ≥30 + 竖线 ≥6）
for _bx, _by in ((2190078, -4574520), (2190078, -4979330)):
    for _k in range(32):
        _yy = _by + 3000 + _k * 7500
        msp.add_line((_bx + 2000, _yy), (_bx + 319093, _yy))
    for _k in range(7):
        _xx = _bx + 30000 + _k * 42000
        msp.add_line((_xx, _by + 2000), (_xx, _by + 257386))
# 每块 bbox 内的真图框组（200700 + 195450，去重时 195450 被嵌套收掉，200700 应存活）
add_closed_rect(msp, 2265921, -4456370, 200700, 126150)   # [1] 内
add_closed_rect(msp, 2269671, -4454870, 195450, 123150)
add_closed_rect(msp, 2265921, -4861180, 200700, 126150)   # [2] 内
add_closed_rect(msp, 2269671, -4859680, 195450, 123150)
# [0] bbox 内自由散线 + 远距离散实体（同用例 56）
msp.add_line((2091000, -4350000), (2091000, -4420000))
msp.add_line((2300000, -4217000), (2352000, -4269000))
add_line_rect(msp, 1000000, -1000000, 3000, 2000)
add_line_rect(msp, 3500000, -5500000, 3000, 2000)
data = to_bytes(doc)
check('R36非矩形边界块让位: 不规则块输给内含真框, fc=2=200700x2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 200700) < 5 and abs(c['height'] - 126150) < 5) == 2
      and not any(abs(c['width'] - 321093) < 5 for c in r['candidates'])
      and not any(abs(c['width'] - 518495) < 5 for c in r['candidates']))


# ---- 用例 58：非矩形块但无内含真框 → 不让位、照常保留（防误杀守卫） ----
# 让位规则的语义边界：块参照闭合成环但非矩形（范围线形态）时，只有当其 bbox
# 内完全包含显式类型真框候选才让位。无内含真框的非矩形块（若通过全部条件）
# 仍按原口径报告——本用例锁定该行为，防止规则过度外溢。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 2090800, -4552675, 518495, 335087)
_blk_r36b = doc.blocks.new(name='HFZXHX_IRREG')
_blk_r36b.add_lwpolyline([
    (0, 0), (321093, 0), (321093, 50000), (240000, 120000), (321093, 190000),
    (321093, 259386), (180000, 259386), (90000, 170000), (0, 259386),
], close=True)
msp.add_blockref('HFZXHX_IRREG', (2190078, -4574520))
msp.add_blockref('HFZXHX_IRREG', (2190078, -4979330))
for _bx, _by in ((2190078, -4574520), (2190078, -4979330)):
    for _k in range(32):
        _yy = _by + 3000 + _k * 7500
        msp.add_line((_bx + 2000, _yy), (_bx + 319093, _yy))
    for _k in range(7):
        _xx = _bx + 30000 + _k * 42000
        msp.add_line((_xx, _by + 2000), (_xx, _by + 257386))
msp.add_line((2091000, -4350000), (2091000, -4420000))
msp.add_line((2300000, -4217000), (2352000, -4269000))
add_line_rect(msp, 1000000, -1000000, 3000, 2000)
add_line_rect(msp, 3500000, -5500000, 3000, 2000)
data = to_bytes(doc)
check('R36非矩形块无内含真框: 不让位照常保留, fc=2=321093x2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 321093) < 5 and abs(c['height'] - 259386) < 5) == 2
      and not any(abs(c['width'] - 518495) < 5 for c in r['candidates']))


# ---- 用例 59：d013 修复（R37）：空壳幽灵锚点 + 孤证 A 系纸（d013.dwg 3 实际 4 检出） ----
# d013 与 d012 同系列（配电房大样及接地）但两处形态关键差异：
#   差异1：38 顶点不规则边界（矩形度 0.70）在 **顶层**（d012 在块内走 R36 让位）
#     → 被顶层矩形度过滤正确拒绝、从未成候选 → R34 空壳剔除的 ① 找不到
#     [25%,50%)×big 窗口内的被罩者（200700A 仅占 518495 面积 14.6%）
#     → 518495 存活 → 去重"留大剔小"吃掉完全包含于其中的真图框 200700A。
#     修1（幽灵锚点）：候选锚点搜索无果时，追加接受布局内**闭合 LWPOLYLINE
#     原始实体 bbox**（38v 边界 bbox 321093×259386：面积 47.9%∈[25,50)、
#     交集 91.6%、错位溢出，四条件全过）；排除与 big 自身 bbox 相同者
#     （d012 BZ 518495 自身是闭合多段线的自锚防护）；④ 认领逻辑不变。
#   差异2：独立 A1 大样框 84100×59400（直线矩形，与所有候选无嵌套、无同尺寸
#     副本）——rel=2.88%（分母被 518495 抬大）过不了 C、area_ratio 1.2% 过不了
#     A、同尺寸仅 1 份过不了 D、非块候选 E 不适用 → 四通道全败漏检。
#     修2（条件F 孤证 A 系纸）：直线矩形 + ratio 近√2(±2%) + rel∈[1%,10%)
#     + short_side≥20000 + 框内严格内部实体≥12。
#   两修缺一不可：仅修1 → 缺模型 84100；仅修2 → 518495 仍在、200700A 仍被吃。
def _build_d013_like_doc():
    doc = make_doc()
    msp = doc.modelspace()
    # [0] 518495×335087 出图范围框——d013 中是 4 条 LINE 的直线矩形（d012 是
    # BZ 层闭合多段线）；ratio 1.5473 偏离 √2 9.41% 擦线进 √2 保护
    add_line_rect(msp, 2090800, -4552675, 518495, 335087)
    # [1][2] 38 顶点不规则闭合边界 ×2——顶层 LWPOLYLINE（矩形度 <0.92 正确拒绝，
    # 不成候选；但作为闭合 LWPOLYLINE 其实体 bbox 是修1 的幽灵锚点）
    _irreg_pts = [(0, 0), (321093, 0), (321093, 50000), (240000, 120000),
                  (321093, 190000), (321093, 259386), (180000, 259386),
                  (90000, 170000), (0, 259386)]
    msp.add_lwpolyline([(x + 2190078, y - 4574520) for x, y in _irreg_pts], close=True)
    msp.add_lwpolyline([(x + 2190078, y - 4979330) for x, y in _irreg_pts], close=True)
    # [3][4] 真图框 200700×126150 ×2（直线矩形；rel=14.6% 走条件C；
    # [3] 完全包含于 518495 → 修1 失效时被去重"留大剔小"吃掉）
    add_line_rect(msp, 2265921, -4456370, 200700, 126150)
    add_line_rect(msp, 2265921, -4861180, 200700, 126150)
    # 框内内容横线 ×2×25（真实 d013 框内 ≥40 实体；横线略短于框宽不成假矩形，
    # y 在框内自底边向上均布）。同时承担 R34 条件④ 的线长基数：真实 d013
    # 518495 bbox 内总线长 1.53e7（38v 边界自由段仅占 6.8%），单测需等量级
    # 认领线复现 <10% 口径。
    for _bx, _by in ((2265921, -4456370), (2265921, -4861180)):
        for _k in range(25):
            msp.add_line((_bx + 1079, _by + 4370 + _k * 4900),
                         (_bx + 199079, _by + 4370 + _k * 4900))
    # [5][6] 内框 195450×123150 ×2（rel=13.9% 过 C，去重被 200700 嵌套收掉）
    add_line_rect(msp, 2269671, -4454870, 195450, 123150)
    add_line_rect(msp, 2269671, -4859680, 195450, 123150)
    # [7] 独立 A1 大样框 84100×59400（d013 真实坐标；与所有候选无嵌套交集）
    add_line_rect(msp, 2135989, -4842605, 84100, 59400)
    # 框内内容证据 13 条横线（条件F 要求严格内部实体 ≥12；单 y 簇不成假矩形）
    for _k in range(13):
        msp.add_line((2140000 + _k * 4000, -4820000),
                     (2140000 + _k * 4000 + 3000, -4820000))
    # 远距离散实体撑大 msp 总 bbox（复刻 area_ratio 量级，同用例 56/57）
    add_line_rect(msp, 1000000, -1000000, 3000, 2000)
    add_line_rect(msp, 3500000, -5500000, 3000, 2000)
    # 布局"配电房大样及接地"：84100×59400 图框 + 80600×57400 内框（去重被嵌套收掉）
    _lay13 = doc.layouts.new('配电房大样及接地')
    add_closed_rect(_lay13, 0, 0, 84100, 59400)
    add_closed_rect(_lay13, 1000, 1000, 80600, 57400)
    return to_bytes(doc)


_data13 = _build_d013_like_doc()
check('d013: 幽灵锚点剔范围框 + 条件F救孤证A1, fc=4 (smart)',
      lambda: parse(_data13),
      lambda r: r['frame_count'] == 4
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 200700) < 5 and abs(c['height'] - 126150) < 5) == 2
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5) == 2
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 84100) < 5 and c['layout'] == '模型空间') == 1
      and not any(abs(c['width'] - 518495) < 5 for c in r['candidates'])
      and not any(abs(c['width'] - 321093) < 5 for c in r['candidates'])
      and not any(abs(c['width'] - 195450) < 5 for c in r['candidates']))


def _parse_env(data, **switches):
    """带环境开关的解析（用后恢复），供开关守卫用例。"""
    import os as _os
    _saved = {k: _os.environ.get(k) for k in switches}
    for _k, _v in switches.items():
        _os.environ[_k] = _v
    try:
        return parse(data)
    finally:
        for _k, _v in _saved.items():
            if _v is None:
                _os.environ.pop(_k, None)
            else:
                _os.environ[_k] = _v


# ---- 用例 60：开关守卫 NO_SHELL_GHOST=1（修1 关、修2 开） ----
# 范围框 518495 复活（无幽灵锚点）→ 去重吃掉 200700A；84100model 靠条件F 仍被
# 救回 → fc=4 但构成退化为 {518495, 200700B, 84100模, 84100布}。
check('d013开关 NO_SHELL_GHOST=1: 范围框复活吃200700A, fc=4含518495',
      lambda: _parse_env(_data13, FRAME_PARSER_NO_SHELL_GHOST='1'),
      lambda r: r['frame_count'] == 4
      and any(abs(c['width'] - 518495) < 5 for c in r['candidates'])
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 200700) < 5 and abs(c['height'] - 126150) < 5) == 1
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5) == 2)

# ---- 用例 61：开关守卫 NO_COND_F=1（修1 开、修2 关） ----
# 范围框被幽灵锚点剔除、200700A 存活；独立 A1 因无条件F 漏检 → fc=3。
check('d013开关 NO_COND_F=1: 独立A1漏检, fc=3=200700x2+84100布',
      lambda: _parse_env(_data13, FRAME_PARSER_NO_COND_F='1'),
      lambda r: r['frame_count'] == 3
      and not any(abs(c['width'] - 518495) < 5 for c in r['candidates'])
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 200700) < 5 and abs(c['height'] - 126150) < 5) == 2
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5) == 1
      and any(abs(c['width'] - 84100) < 5 and c['layout'] != '模型空间'
              for c in r['candidates']))


# ---- R39 用例：d018.dwg 预放大图框块 fc=0 —— 条件E 等效比例链 ----
# 场景（tmp_diag_d018_r39.py 实测复现）：真图框 = 块 AW_FRAME_PRE100（块定义即
# 59400×42000 = A2 横放 ×100，缩放做进块定义，实例 ×1 插入，ratio 1.4143 精确√2）。
# 六通道全灭：
#   A 灭：msp_total(≈3.27e10) 被 R27 拒绝并计回的巨型内容块 BIG_CONTENT
#         （223536×146407，闭合框长边 34200 < 50% 判内容块）撑大 → ar=7.62%<15%
#   C 灭：R27 计回分母（防塌缩机制）→ rel=7.62%<10%
#   B 不适用（块非 EXPLICIT_TYPES）；D/F 仅直线矩形适用
#   E 灭：块定义短边 42000 ∉ A 系 —— 旧判据未覆盖"预放大模板"
# 线上链路：smart 抛"未检测到图框"→ 前端自动 force_max 重试 → 尺寸对
# （59400×42000）但 frame_count=0。修法：条件E 扩展等效比例链
# （实例 = A系短边×白名单倍率 + 实例相对块定义等比 ≤0.5% + 长边≈短边×√2）。
def _build_d018_like_doc():
    doc = make_doc()
    msp = doc.modelspace()
    # [0] 预放大图框块：块定义 59400×42000（A2×100），实例 ×1 插入
    _blk_f = doc.blocks.new(name='AW_FRAME_PRE100')
    add_closed_rect(_blk_f, 0, 0, 59400, 42000)
    # 标题栏内部横线 ×3（长 30000 不撑 bbox）
    for _k in range(3):
        _blk_f.add_line((3000, 3000 + _k * 1500), (33000, 3000 + _k * 1500))
    msp.add_blockref('AW_FRAME_PRE100', (10000, 10000))
    # [1] 巨型内容块：bbox 223536×146407；闭合证据仅 34200 小框（<50% → R27 拒）；
    #     最长直边构件 80000（≥30% → 构件证据合格 → R27 计回 rel 分母）；
    #     横簇重叠带 [0,80000] 不含竖线 x=223536 → LINE 簇不成框
    _blk_b = doc.blocks.new(name='BIG_CONTENT')
    _blk_b.add_line((0, 0), (80000, 0))
    _blk_b.add_line((223536, 0), (223536, 80000))
    _blk_b.add_line((0, 146407), (150000, 146407))
    _blk_b.add_line((100000, 20000), (223536, 100000))
    add_closed_rect(_blk_b, 20000, 20000, 34200, 24000)
    msp.add_blockref('BIG_CONTENT', (0, 0))
    # [2] 顶层对角斜线（斜线不进 P1 横竖簇；保证 msp_total 无论 ezdxf fast 对
    #     INSERT 的展开行为如何都被撑到巨块量级 → ar 复现 7.62%）
    msp.add_line((0, 0), (223536, 146407))
    # [3] 顶层散斜线 ×6（复刻 d018 顶层 LINE 不成矩形；bbox 在巨块内）
    for _k in range(6):
        msp.add_line((120000 + _k * 8000, 60000 + _k * 5000),
                     (150000 + _k * 8000, 70000 + _k * 5000))
    # 空布局1（d018 实况：布局无实体、无候选）
    doc.layouts.new('布局1')
    return to_bytes(doc)


_data18 = _build_d018_like_doc()
check('R39 d018: 预放大图框块(A2x100)条件E等效比例链救回, fc=1 (smart)',
      lambda: parse(_data18),
      lambda r: r['frame_count'] == 1
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 59400) < 5 and abs(c['height'] - 42000) < 5
              and c['layout'] == '模型空间') == 1
      and r['width'] == 59400 and r['height'] == 42000)


def _run18_no_pre():
    try:
        return _parse_env(_data18, FRAME_PARSER_NO_COND_E_PRE='1')
    except RuntimeError as e:
        return {'error': str(e)}


# ---- R40 用例：d006,007.dwg fc=20 实际 4 —— 块内图框救援 + rel 分母补强 ----
# 场景（tmp_diag_d0607_r40.py 实测复现）：模型空间 = 巨型拼接底图块 BIG_WRAP
# （R27 拒：闭合框长边 200700 < 块长边×50%；其内部藏着 2 份 200700×126150 闭合
# 矩形真图框——d012/d013 同源图打包成块）+ 18 个 CSW-1499 设备块散布
# （771×514×13 + 844×506×5，闭合外形框=块边界 → R27 通过入库）。
# 红形态链条：设备块 rel 分母塌缩（巨块被拒且超 2.5e6 计回上限 → 分母=设备块
# 自身 427064）→ 18 块 rel 93~100% 全过条件C（ratio 1.5/1.667 均∈[1.343,2.5]）；
# 771 组 ratio 1.4999 擦线进 √2±10% 对齐保护窗躲过对齐剔除；844 组散布对齐
# 判据不命中 → fc = 18假 + 2真(布局) = 20，且模型 2 真图框漏检。
# 修法（R40，单扫描点双效果）：R27 拒绝块内若存在 ≥2 份同尺寸矩形闭合 LWPOLYLINE
# （矩形度≥0.92、短边≥20000、ratio∈[1.05,5.5]——图框族强证据）→
# ①提升为候选（type='块内闭合线'，INSERT matrix 变换到世界坐标）；
# ②该尺寸面积计入 _r27_rej_max（rel 分母补强）→ 设备块 rel=0.0016% 全灭
# （六通道逐一核查：A ar≈0/B 非 EXPLICIT/C rel 崩/D,F 非直线矩形/E 514 不在
# A系×白名单倍率）。
# 理想口径（用户确认）：模型 200700×126150×2 + 布局 841×594 + 1338×841 = fc=4。
def _build_d0607_like_doc():
    doc = make_doc()
    msp = doc.modelspace()
    # [0] 巨型拼接底图块：内含 2 份 200700×126150 闭合矩形（真图框，d006,007 实测
    #     块内坐标）+ 框内横线 + 远距离散件（撑出 5261364 跨度 → R27 拒且不计回）
    _blk = doc.blocks.new(name='BIG_WRAP')
    _bx, _by = 2265921, -4756370
    _blk.add_lwpolyline([(_bx, _by), (_bx + 200700, _by),
                         (_bx + 200700, _by + 126150), (_bx, _by + 126150)], close=True)
    _bx2, _by2 = 2265921, -3855370
    _blk.add_lwpolyline([(_bx2, _by2), (_bx2 + 200700, _by2),
                         (_bx2 + 200700, _by2 + 126150), (_bx2, _by2 + 126150)], close=True)
    for _k in range(6):              # 框内内容横线（每框 3 条，长 150000）
        _oy = _by if _k < 3 else _by2
        _kk = _k % 3
        _blk.add_line((_bx + 20000, _oy + 20000 + _kk * 30000),
                      (_bx + 170000, _oy + 20000 + _kk * 30000))
    _blk.add_line((0, 0), (5261364, 0))          # 远距离散件（撑块定义 bbox）
    _blk.add_line((5261364, 0), (5261364, 1309513))
    msp.add_blockref('BIG_WRAP', (0, 0))
    # [1] 设备块 ×2 型号散布（闭合外形框=块边界、内部近空 → R27 通过入库；
    #     散布坐标复刻实测区域，x/y 双向散布 → 对齐剔除不命中）
    _blk_a = doc.blocks.new(name='DEV_A')        # 771×514 ratio 1.4999
    _blk_a.add_lwpolyline([(0, 0), (771, 0), (771, 514), (0, 514)], close=True)
    _blk_a.add_line((100, 100), (200, 200))
    _blk_b = doc.blocks.new(name='DEV_B')        # 844×506 ratio 1.667
    _blk_b.add_lwpolyline([(0, 0), (844, 0), (844, 506), (0, 506)], close=True)
    for _x, _y in [(2322790, -4445180), (2333192, -4441920), (2338445, -4437027),
                   (2326043, -4433822), (2328643, -4439434), (2333216, -4445092),
                   (2380907, -4402021), (2380907, -4401249), (2377323, -4406907),
                   (2377766, -4406275), (2399771, -4359624), (2399637, -4358864),
                   (2399047, -4367634)]:
        msp.add_blockref('DEV_A', (_x, _y))
    for _x, _y in [(2327667, -4438712), (2375921, -4395526),
                   (2375914, -4408918), (2395967, -4363076), (2329878, -4444248)]:
        msp.add_blockref('DEV_B', (_x, _y))
    # [2] 顶层散斜线（设备连线，不进 P1 横竖簇）。多角度分散：真实 d006,007
    #     的 830 条设备连线杂角度分布、族占比 < 65%，不触发全局旋转矫正；
    #     若同角度（如全 45°）会误触发旋转矫正，把轴对齐实体整体转斜 45°，
    #     设备块 bbox 771x514→907x910（ratio 1.003 出条件C 窗）、巨块同毁
    #     → 模型空间候选全灭（tmp_diag3_d0607_r40 实证 fc=2 假象）。
    _dirs = [(11591, 3107), (10392, 6000), (8485, 8485), (6000, 10392),
             (3107, 11591), (-3107, 11591), (-6000, 10392), (-8485, 8485)]
    for _k, (_dx, _dy) in enumerate(_dirs):
        _sx = 2320000 + _k * 9000
        _sy = -4430000 + _k * 7000
        msp.add_line((_sx, _sy), (_sx + _dx, _sy + _dy))
    # [3] 布局1：双真框 + 内框（内框被嵌套去重收掉）
    _lay = doc.layouts.new('布局1')
    add_closed_rect(_lay, 984, 2, 1338, 841)
    add_closed_rect(_lay, 1009, 12, 1303, 821)
    add_closed_rect(_lay, 2, 2, 841, 594)
    add_closed_rect(_lay, 28, 12, 806, 574)
    return to_bytes(doc)


_data607 = _build_d0607_like_doc()
check('R40 d006,007: 块内图框救援+rel分母补强, fc=4=200700x2+841布+1338布 (smart)',
      lambda: parse(_data607),
      lambda r: r['frame_count'] == 4
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 200700) < 10 and abs(c['height'] - 126150) < 10
              and c['layout'] == '模型空间') == 2
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 841) < 5 and abs(c['height'] - 594) < 5
              and c['layout'] != '模型空间') == 1
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 1338) < 5 and abs(c['height'] - 841) < 5
              and c['layout'] != '模型空间') == 1
      and not any(c['width'] < 2000 and c['layout'] == '模型空间'
                  for c in r['candidates'])
      and r['width'] == 200700 and r['height'] == 126150)


def _run607_no_rescue():
    try:
        return _parse_env(_data607, FRAME_PARSER_NO_BLOCK_FRAME_RESCUE='1')
    except RuntimeError as e:
        return {'error': str(e)}


# ---- R40 开关守卫：NO_BLOCK_FRAME_RESCUE=1（回退：无救援、无分母补强）----
# 设备块 rel 分母回到塌缩态（自身量级）→ 18 个全过条件C 复活 → fc=20 红形态。
check('R40开关 NO_BLOCK_FRAME_RESCUE=1: 设备块复活, fc=20红形态',
      _run607_no_rescue,
      lambda r: r.get('frame_count') == 20
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 771) < 5 and c['layout'] == '模型空间') >= 10)


# ==================== R43 d008：嵌套图框对救援 + 计回按块记账/让位 ====================
def _build_d008_like_doc():
    doc = make_doc()
    msp = doc.modelspace()
    # [0] 巨块：内含 1 份外框 200700×126150 + 1 份嵌套内框（四边内缩 2000 →
    #     196700×122150，面积比 0.949 ∈ [0.85,0.995]）+ 两条长 LINE 散件撑大
    #     块定义 bbox 至 1800000×586909（复刻 d008 实测 1806307×586909 量级）：
    #     闭合框长边 200700 < 1800000×50% → R27 拒绝；LINE 1800000 ≥ 30% →
    #     构件证据合格 → 按块计回 1.056e12 压死外框 rel=2.4%——R43 让位机制
    #     必须把它替换成组面积 2.53e10 → rel=100%。
    _blk = doc.blocks.new(name='13013-11-AW-FP-R43')
    _blk.add_lwpolyline([(0, 0), (200700, 0), (200700, 126150), (0, 126150)],
                        close=True)
    _blk.add_lwpolyline([(2000, 2000), (198700, 2000), (198700, 124150),
                         (2000, 124150)], close=True)
    _blk.add_line((0, 0), (1800000, 0))       # 散件撑块 bbox（水平跨度）
    _blk.add_line((0, 0), (0, 586909))        # 散件撑块 bbox（垂直跨度）
    msp.add_blockref('13013-11-AW-FP-R43', (0, 0))
    # [1] 顶层小闭合矩形（表格/部件形态）：位于外框内部、被 200700 救援候选
    #     罩住，面积占比 10500×26406/200700×126150 = 1.1% < 10% → 验证 R43
    #     面积比门槛（正常图框罩小部件不触发让位撤销）。
    add_closed_rect(msp, 50000, 30000, 10500, 26406)
    return to_bytes(doc)


_data008 = _build_d008_like_doc()
check('R43 d008: 嵌套图框对救援+计回按块让位+面积比门槛, fc=1 200700x126150 (smart)',
      lambda: parse(_data008),
      lambda r: r['frame_count'] == 1
      and r['width'] == 200700 and r['height'] == 126150
      and r['candidates'][0]['type'] == '块内闭合线'
      and r['candidates'][0]['layout'] == '模型空间')


def _run008_no_rescue():
    try:
        return _parse_env(_data008, FRAME_PARSER_NO_BLOCK_FRAME_RESCUE='1')
    except RuntimeError as e:
        return {'error': str(e)}


# ---- R43 开关守卫：NO_BLOCK_FRAME_RESCUE=1（回退：无救援、无让位）----
# 巨块被拒不提升 → 顶层小框 10500×26406（ratio 2.515 出条件C 窗、rel 1.1%
# ar 0.03%）全灭 → fc=0 抛错"未检测到图框"。
check('R43开关 NO_BLOCK_FRAME_RESCUE=1: 无救援, 抛错未检测到图框',
      _run008_no_rescue,
      lambda r: 'error' in r and '未检测到图框' in r['error'])


# ---- 用例 59：立面图.dwg 形态——双线框图纸孪生 + 空壳锚点，误剔真框须救回 ----
# 两张 173100×123150 大图（ratio 1.4052，√2 偏差 0.64%）上下排列互不重叠，
# 各带同左下角 162600×123150 内框（双线框图纸）。一个 118900×84100 框
# （√2 纸张形态）与图 A 交集 97.1% 且右缘溢出 3412mm → 满足空壳锚点
# 四条件（面积 46.9%∈(25,50)×big、交集≥90%、错位溢出、自由占比 0%——
# 内容全被内框认领）→ 旧逻辑误剔图 A。修复双保险：
#   修3 自身内框反证：big 完全包含 ≥85%×85% 自身尺寸内框 → 真纸张，跳过空壳剔除；
#   修2 孪生反证：同尺寸同布局存在位置不重叠孪生 → 保留。
# 断言：两张 173100×123150 全部存活（旧逻辑只剩 1 张）。
doc = make_doc()
msp = doc.modelspace()
# 图 A（下）：外框 + 内框（同左下角，宽 93.9%/高 100%）
add_closed_rect(msp, 158049, -106453, 173100, 123150)
add_closed_rect(msp, 158049, -106453, 162600, 123150)
# 图 B（上）：外框 + 内框
add_closed_rect(msp, 167872, 40180, 173100, 123150)
add_closed_rect(msp, 167872, 40180, 162600, 123150)
# 空壳锚点框：与图 A 交集 97.1%（x 重叠 115488/118900、y 全高）、右缘溢出 3412mm
add_closed_rect(msp, 215661, -100451, 118900, 84100)
# 第二份同尺寸锚点框（立面图真实形态：块内 4 框，此处 1 份足够触发）
add_closed_rect(msp, 105351, -153255, 118900, 84100)
data = to_bytes(doc)
check('R45立面图: 孪生反证+自身内框反证, 两张173100双线框全存活 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 173100) < 5
                    and abs(c['height'] - 123150) < 5) == 2)


# ---- 用例 60：单张双线框图纸（无孪生）→ 自身内框反证独立生效 ----
# 只有一张 173100×123150 双线框图 + 97% 交集锚点框：无孪生可依，
# 修3（自身内框反证）必须独立救回——守护"修2 失效时修3 兜底"。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 158049, -106453, 173100, 123150)
add_closed_rect(msp, 158049, -106453, 162600, 123150)
add_closed_rect(msp, 215661, -100451, 118900, 84100)
data = to_bytes(doc)
check('R45立面图: 单张双线框(无孪生)靠自身内框反证存活 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 173100) < 5
                    and abs(c['height'] - 123150) < 5) == 1)


# ---- 用例 61：R46 同块镜像实例整组去重（立面图.dwg 13→5） ----
# 巨块内含 2 份同尺寸闭合框（外 118900×84100 + 内 115400×82100 模拟），块被
# INSERT 两次：1 正常 (1,1) + 1 水平镜像 (-1,1) → 救援提升 4 份同尺寸候选。
# 判据（全满足才整组剔）：同块名同尺寸组、≥2 来源实例、|s| 一致、实例间
# 镜像（sx 异号）。断言：整组清零（含内框组，防"留大剔小"失去大框后复活）。
def _build_mirror_dup_doc():
    doc = make_doc()
    msp = doc.modelspace()
    blk = doc.blocks.new(name='ELEV_BASE_MIRROR')
    blk.add_lwpolyline([(0, 0), (118900, 0), (118900, 84100), (0, 84100)], close=True)
    blk.add_lwpolyline([(2000, 2000), (117400, 2000), (117400, 82100), (2000, 82100)], close=True)
    # 散线撑大块 bbox 至 300000 宽 → R27 闭合证据 118900<300000×50% 拒块
    # （复刻立面图巨块"散件撑大 bbox 被拒→救援提升"的真实链路）
    blk.add_line((0, 0), (300000, 0))
    msp.add_blockref('ELEV_BASE_MIRROR', (7619, -74396))                      # 实例① 正常
    _ins2 = msp.add_blockref('ELEV_BASE_MIRROR', (432293, -21592))            # 实例② 镜像
    _ins2.dxf.xscale = -1.0
    # 顶层真图框 ×2（同尺寸互证，避免孤证复核剔除干扰断言）
    add_closed_rect(msp, 0, 300000, 84100, 59400)
    add_closed_rect(msp, 100000, 300000, 84100, 59400)
    return to_bytes(doc)


_data61 = _build_mirror_dup_doc()
check('R46同块镜像实例: 整组去重, 无118900/115400候选 (smart)',
      lambda: parse(_data61),
      lambda r: r['frame_count'] >= 1
      and not any(abs(c['width'] - 118900) < 5 for c in r['candidates'])
      and not any(abs(c['width'] - 115400) < 5 for c in r['candidates']))

# ---- 用例 62：R46 护栏——单实例巨块（d006,007 形态）不受镜像去重影响 ----
doc = make_doc()
msp = doc.modelspace()
blk2 = doc.blocks.new(name='ELEV_BASE_SINGLE')
blk2.add_lwpolyline([(0, 0), (118900, 0), (118900, 84100), (0, 84100)], close=True)
blk2.add_lwpolyline([(2000, 2000), (117400, 2000), (117400, 82100), (2000, 82100)], close=True)
blk2.add_line((0, 0), (300000, 0))    # 散线撑大 bbox → R27 拒块走救援路径
msp.add_blockref('ELEV_BASE_SINGLE', (0, 0))    # 仅 1 个实例
data = to_bytes(doc)
check('R46护栏: 单实例巨块救援不受影响, 118900 候选仍在 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 118900) < 5) >= 1)

# ---- 用例 63：R46 护栏——同块无镜像多实例（图集排版形态）不去重 ----
# 块定义内 2 份同尺寸框（d006,007 形态），块插 2 次、均无镜像 → 救援提升
# 4 份候选（2 实例 × 2 份），镜像去重不得触发（无 sx/sy 符号差）。
doc = make_doc()
msp = doc.modelspace()
blk3 = doc.blocks.new(name='ELEV_BASE_ROW')
blk3.add_lwpolyline([(0, 0), (118900, 0), (118900, 84100), (0, 84100)], close=True)
blk3.add_lwpolyline([(200000, 0), (318900, 0), (318900, 84100), (200000, 84100)], close=True)
blk3.add_line((0, 0), (400000, 0))    # 散线撑大 bbox → R27 拒块走救援路径
msp.add_blockref('ELEV_BASE_ROW', (0, 0))            # 实例① 无镜像
_msi2 = msp.add_blockref('ELEV_BASE_ROW', (0, 300000))  # 实例② 平移不重叠无镜像
data = to_bytes(doc)
check('R46护栏: 无镜像多实例(图集排版)不去重, 4 份候选均在 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 118900) < 5) == 4)


# ---- 用例 64：R48 条件 F 扩展——低 rel 闭合多段线 A 系纸（泛悦国际总平图形态） ----
# 巨型图廓线框（730112×425000）撑大 rel 分母 → 两真框 rel 仅 0.80%/0.40%，
# 旧 F 只认直线矩形且 rel 下限 0.01 → 全灭。扩展后（EXPLICIT_TYPES 含闭合
# 多段线 + rel 下限 0.003）两框经 F 通道救援。各带 13 条横线满足实体数门槛。
doc = make_doc()
msp = doc.modelspace()
# 巨型图廓（分母毒源）复刻真实形态：块内闭合线 + INSERT（真实 DWG 中为
# A$C3E115B39 块，rot≈359°，后被孤证复核按 score=0 剔除）
_gk = doc.blocks.new(name='GIANT_BORDER')
_gk.add_lwpolyline([(0, 0), (730112, 0), (730112, 425000), (0, 425000)], close=True)
msp.add_blockref('GIANT_BORDER', (-2000000, -1000000))


def _add_a_series(x, y, w, h):
    add_closed_rect(msp, x, y, w, h)
    # 横线两端内缩 100mm：条件 D/F 实体计数要求 bbox 严格内缩 1mm（压线不算，
    # 与真实 DWG 框内实体均在边界内一致）
    for _i in range(1, 14):
        _yy = y + h * _i / 14.0
        msp.add_line((x + 100, _yy), (x + w - 100, _yy))


_add_a_series(741525, 245073, 59400, 42000)
_add_a_series(684505, 244722, 42000, 29700)
data = to_bytes(doc)
check('R48条件F扩展: 低rel闭合多段线A系纸两框均存活 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 59400) < 5
                    and abs(c['height'] - 42000) < 5) == 1
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 42000) < 5
              and abs(c['height'] - 29700) < 5) == 1)


# ---- 用例 65：R49 条件I 护栏——隐形块（全实体 off 层）不判表格页 ----
# 复刻 08，09-MW-FP006 形态：块 DOTE_GRID（35950×33400 纯网格，全部实体在
# off 图层）插在真框左侧，bbox 内恰有相邻可见管线折线（41 横+16 竖）——
# 修复前条件I 把"别人的线"数进隐形块的 bbox，救回一张隐形表格页（fc 3→2）。
# 断言：无表格页候选、无 35950 候选；两张真框互证存活。
doc = make_doc()
msp = doc.modelspace()
doc.layers.new('DOTE_OFF', dxfattribs={'color': -7})    # color<0 = 图层关闭（ezdxf is_off 口径）
_blk_inv = doc.blocks.new(name='DOTE_GRID')
for _i in range(10):     # 块内画网格（横 10 × 竖 6，全部在 off 层）
    _blk_inv.add_line((0, _i * 3340), (35950, _i * 3340), dxfattribs={'layer': 'DOTE_OFF'})
for _j in range(6):
    _blk_inv.add_line((_j * 6500, 0), (_j * 6500, 33400), dxfattribs={'layer': 'DOTE_OFF'})
msp.add_blockref('DOTE_GRID', (-72525, -810))
# 隐形块 bbox 内的可见管线折线（修复前被条件I 误数成表格线）
for _i in range(41):
    _x0 = -72000 + _i * 500
    msp.add_line((_x0, 5000 + (_i % 4) * 800), (_x0 + 500, 5000 + (_i % 4) * 800 + 300))
for _j in range(16):
    msp.add_line((-70000 + _j * 1500, 4000), (-70000 + _j * 1500, 20000))
# 两张真框互证（远离隐形块区域，避免孤证复核干扰）
add_closed_rect(msp, 100000, 0, 84100, 59400)
add_closed_rect(msp, 200000, 0, 84100, 59400)
data = to_bytes(doc)
check('R49条件I护栏: 隐形块(全实体off层)不判表格页 (smart)',
      lambda: parse(data),
      lambda r: not any(c.get('rescued_by') == 'table_page' for c in r['candidates'])
      and not any(abs(c['width'] - 35950) < 5 for c in r['candidates']))


# ---- 用例 66：R49 对照——可见表格页（顶层闭合框+网格）仍被条件I 救回 ----
# 复刻香槟半岛图纸目录页真实形态：顶层闭合多段线外框 35950×33400（可见）
# + 框内网格线（横 ≥30 / 竖 ≥6）→ 条件I 必须照常救援，护栏不得误伤。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 35950, 33400)      # 表格外框（可见，顶层）
for _i in range(1, 33):                        # 横 32 条（≥30，内缩避免压线）
    msp.add_line((100, _i * 1000), (35850, _i * 1000))
for _j in range(1, 7):                         # 竖 6 条（≥6）
    msp.add_line((_j * 5500, 100), (_j * 5500, 33300))
# 表格页旁边放两张真框互证
add_closed_rect(msp, 100000, 0, 84100, 59400)
add_closed_rect(msp, 200000, 0, 84100, 59400)
data = to_bytes(doc)
check('R49对照: 可见表格页仍被条件I救回 (smart)',
      lambda: parse(data),
      lambda r: any(c.get('rescued_by') == 'table_page'
                    and abs(c['width'] - 35950) < 5 for c in r['candidates']))


# ---- 用例 67：R50 贴边附栏条带并入（08，09-MW-FP004 形态） ----
# 主框 94600×57400 闭合多段线 + 右侧贴边 7000 宽附栏（3 段闭合矩形零间隙、
# 纵向拼满框高）→ 整体口径 101600×57400。另放 1 张 84100×59400 互证防孤证。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 94600, 57400)               # 主框
_edge_spans = [(0, 21800), (21800, 36600), (36600, 57400)]
for _a, _b in _edge_spans:
    add_closed_rect(msp, 94600, _a, 7000, _b - _a)     # 右侧贴边条带 ×3
add_closed_rect(msp, 200000, 0, 84100, 59400)          # 互证框
data = to_bytes(doc)
check('R50贴边附栏: 右侧7000条带拼满框高, 94600→101600 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 101600) < 5
                    and abs(c['height'] - 57400) < 5) == 1)


# ---- 用例 68：R50 护栏——条带只拼半个框高，不得并入 ----
# 同形态但条带纵向只覆盖 50%（<90% 门槛）→ 主框保持 94600 原宽。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 94600, 57400)
add_closed_rect(msp, 94600, 0, 7000, 28700)            # 只拼下半段
add_closed_rect(msp, 200000, 0, 84100, 59400)
data = to_bytes(doc)
check('R50护栏: 条带不满90%框高不并入, 主框保持94600 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 94600) < 5
                    and abs(c['height'] - 57400) < 5) == 1
      and not any(abs(c['width'] - 101600) < 5 for c in r['candidates']))


# ---- 用例 69：R52 布局空间优先收窄——模型空间 √2 比例真框豁免剔除 ----
# 场景来源：大样图(内外保温).dwg。布局 5 张页框正常，模型空间另有 2 张真图框
#   42050×29700、23780×16820（均 A 系 √2 比例），旧"布局空间优先"一刀切把
#   模型候选全剔 → 漏检 2 张。收窄：模型候选 ratio ≈√2（±2%）豁免保留。
# 复刻形态：布局1 3×2 网格页 + 模型空间两个 √2 真框 → fc=8（模型 2 + 布局 6）。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 42050, 29700)      # ratio 1.4158，偏差 0.11%
add_closed_rect(msp, 60000, 0, 23780, 16820)  # ratio 1.4138，偏差 0.03%
blk3 = doc.blocks.new('PAGE_FRAME3')
add_closed_rect(blk3, 0, 0, 434.1, 311.1)
lay3 = doc.layouts.new('布局1')
for _cy in range(2):
    for _cx in range(3):
        lay3.add_blockref('PAGE_FRAME3', (_cx * 434.1, _cy * 311.1))
data = to_bytes(doc)
check('R52布局优先收窄: 模型√2真框豁免保留, 模型2+布局6 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 8
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 42050) < 5 and abs(c['height'] - 29700) < 5) == 1
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 23780) < 5 and abs(c['height'] - 16820) < 5) == 1
      and r['frame_counts_by_layout'].get('布局 "布局1"') == 6)

# ---- 用例 70：R52 护栏——模型空间非 √2 内容框仍被布局优先剔除 ----
# 同形态但模型框为非 √2 内容区轮廓（13423×9014，ratio 1.489，偏差 5.3% >2%）
#   → 不得豁免，仍整体剔除（守护 DS4 等布局网格页图纸不因收窄而复活模型候选）。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 13423, 9014)      # ratio 1.489，非 √2
add_closed_rect(msp, 20000, 0, 8926, 13464)  # 同上
blk4 = doc.blocks.new('PAGE_FRAME4')
add_closed_rect(blk4, 0, 0, 434.1, 311.1)
lay4 = doc.layouts.new('布局1')
for _cy in range(2):
    for _cx in range(3):
        lay4.add_blockref('PAGE_FRAME4', (_cx * 434.1, _cy * 311.1))
data = to_bytes(doc)
check('R52护栏: 模型非√2内容框仍被布局优先剔除, fc=6 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 6
      and r['frame_counts_by_layout'] == {'布局 "布局1"': 6}
      and not any(abs(c['width'] - 13423) < 5 for c in r['candidates']))


# ---- 用例 71：R55 让位连坐——参照底图块的"他图页框"随让位块整组撤销 ----
# 场景来源：S011.dwg。巨块（底图参照，散线撑大 bbox 被 R27 拒）内含：
#   组A 2 份 100000×70000（其中 1 份罩住顶层真框 → 让位整组撤销）+
#   组B 2 份 148600×84100（什么都不罩、与真框零空间交集 → 让位幸存，
#   实为被参照图纸集"另一张图"的加长总图页框）。R55 连坐：同块剩余救援组
#   与全部非救援候选零交集 → 一并撤销。断言：fc=1 只剩真框 59450×42050。
doc = make_doc()
msp = doc.modelspace()
_blk = doc.blocks.new(name='REF_BASE')
# 顶层真框 A2 59450×42050（块内坐标 = 模型坐标，INSERT 在原点）+ 13 条内缩横线
# ×2 张（同尺寸互证，避免孤证复核剔除干扰断言）
_t1x, _t1y = 0, 0
for _tx, _ty in [(_t1x, _t1y), (_t1x, 600000)]:
    add_closed_rect(msp, _tx, _ty, 59450, 42050)
    for _k in range(13):
        msp.add_line((_tx + 100, _ty + 2000 + _k * 3000),
                     (_tx + 59450 - 100, _ty + 2000 + _k * 3000))
# 组A：100000×70000 ×2，#1 罩住真框（面积比 35.7% ≥10% → 触发让位）
_blk.add_lwpolyline([(-20000, -20000), (80000, -20000),
                     (80000, 50000), (-20000, 50000)], close=True)
_blk.add_lwpolyline([(200000, 0), (300000, 0),
                     (300000, 70000), (200000, 70000)], close=True)
# 组B：148600×84100 ×2，与真框/组A 全部零交集（连坐目标）
_blk.add_lwpolyline([(500000, -200000), (648600, -200000),
                     (648600, -115900), (500000, -115900)], close=True)
_blk.add_lwpolyline([(900000, -200000), (1048600, -200000),
                     (1048600, -115900), (900000, -115900)], close=True)
# 远距离散件撑大块 bbox → R27 拒（3e6 跨度超计回上限 2.5e6，不计回分母）
_blk.add_line((0, -500000), (3000000, -500000))
_blk.add_line((3000000, -500000), (3000000, -400000))
msp.add_blockref('REF_BASE', (0, 0))
data = to_bytes(doc)
check('R55让位连坐: 底图块他图页框零交集随连坐撤销, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and r['frame_counts_by_layout'] == {'模型空间': 2}
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 59450) < 5 and abs(c['height'] - 42050) < 5) == 2
      and not any(c['type'] == '块内闭合线' for c in r['candidates']))

# ---- 用例 72：R55 护栏——幸存救援组与真框有交集时不得连坐 ----
# 同块但组B 120000×80000 之一与真框部分重叠（非包住，不触发让位；有交集，
# 不满足零交集 → 不连坐）→ 组B 2 份全存活。守护：连坐只清"与真框完全无关"
# 的他图页框，正常救援（如 d008 幸存框罩部件）不受影响。
doc = make_doc()
msp = doc.modelspace()
_blk2 = doc.blocks.new(name='REF_BASE2')
for _tx, _ty in [(0, 0), (0, 600000)]:      # 真框 ×2 同尺寸互证
    add_closed_rect(msp, _tx, _ty, 59450, 42050)
    for _k in range(13):
        msp.add_line((_tx + 100, _ty + 2000 + _k * 3000),
                     (_tx + 59450 - 100, _ty + 2000 + _k * 3000))
# 组A：100000×70000 ×2，#1 罩住真框 → 让位整组撤销
_blk2.add_lwpolyline([(-20000, -20000), (80000, -20000),
                      (80000, 50000), (-20000, 50000)], close=True)
_blk2.add_lwpolyline([(400000, 0), (500000, 0),
                      (500000, 70000), (400000, 70000)], close=True)
# 组B：120000×80000 ×2，#1 与真框角部部分重叠（交集 9450×20000 >0）
_blk2.add_lwpolyline([(50000, -60000), (170000, -60000),
                      (170000, 20000), (50000, 20000)], close=True)
_blk2.add_lwpolyline([(700000, -60000), (820000, -60000),
                      (820000, 20000), (700000, 20000)], close=True)
_blk2.add_line((0, -500000), (3000000, -500000))
_blk2.add_line((3000000, -500000), (3000000, -400000))
msp.add_blockref('REF_BASE2', (0, 0))
data = to_bytes(doc)
check('R55护栏: 幸存救援组与真框有交集不连坐, 组B双份存活 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 120000) < 5
              and abs(c['height'] - 80000) < 5) == 2
      and not any(abs(c['width'] - 100000) < 5 for c in r['candidates'])
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 59450) < 5
              and abs(c['height'] - 42050) < 5) == 2)


# ---- 用例 73：R56 纸面比例模式——A3 图框（纸面 mm，整体倾斜）识别 ----
# 场景来源：总图-排水.dwg。全模型空间按出图比例（纸面 mm）绘制，总包围盒
#   1265×929（<4m² 触发纸面模式），真图框 A3 297×420 整体旋转 3.3°（全图内容
#   同角度倾斜，触发旋转矫正）。矫正后外框 ~297×420.5（ratio 1.4124），A 面积
#   占比 ~3.6%<15%、B 短边 297<400、C rel ~3.6%<10%（分母被 R27 拒绝计回的
#   底图块撑大）、F 短边<20000 四通道全败 → 新增条件F-纸面通道：短边命中
#   A 系列 ±1% 代替 ≥20000。断言 fc=1 只收外框（内框 287×403 短边偏离 A3
#   达 2.9% 不命中）。合成要点：①全图内容（框+线+信用块）均倾斜 3.3° 触发
#   旋转矫正；②R27 拒绝计回的内容块（散线撑大 bbox）把 rel 压到 <10%，
#   否则外框 rel=100% 走条件 C、测不到 F-纸面通道；③框内 ≥12 个内容实体。
import math as _math56

doc = make_doc()
msp = doc.modelspace()
_ang = _math56.radians(3.3)
_ca, _sa = _math56.cos(_ang), _math56.sin(_ang)


def _rot_add_rect(lay, cx, cy, w, h):
    """以 (cx,cy) 为中心画旋转 3.3° 的闭合矩形"""
    _hw, _hh = w / 2, h / 2
    _pts = []
    for _dx, _dy in [(-_hw, -_hh), (_hw, -_hh), (_hw, _hh), (-_hw, _hh)]:
        _pts.append((cx + _dx * _ca - _dy * _sa, cy + _dx * _sa + _dy * _ca))
    lay.add_lwpolyline(_pts, close=True)


# 外框 A3 297×420.5 + 内框 287×403（均旋转 3.3°，中心 (150, 210)）
_rot_add_rect(msp, 150, 210, 297, 420.5)
_rot_add_rect(msp, 150, 210, 287, 403)
# 框内内容：13 条横线（倾斜系，框内局部坐标）+ 2 个小闭合框
for _k in range(13):
    _ly = -190 + _k * 30
    _x1 = 150 + (-128) * _ca - _ly * _sa
    _y1 = 210 + (-128) * _sa + _ly * _ca
    _x2 = 150 + 128 * _ca - _ly * _sa
    _y2 = 210 + 128 * _sa + _ly * _ca
    msp.add_line((_x1, _y1), (_x2, _y2))
_rot_add_rect(msp, 100, 150, 60, 40)
_rot_add_rect(msp, 200, 260, 80, 50)
# R27 拒绝计回的内容块（复刻 XRef 底图块撑大 rel 分母）：1500×1000 闭合框 +
# 3200 长散线撑大 bbox（1500 < 3195×50% → R27 拒；散线 ≥30% 构件证据 → 计回
# ~3.5e6），同角度倾斜维持主方向一致
_blk56 = doc.blocks.new(name='CREDIT_BLK')
_rot_add_rect(_blk56, 900, 300, 1500, 1000)
_blk56.add_line((0, 0), (3195, 184))
msp.add_blockref('CREDIT_BLK', (0, 0))
data = to_bytes(doc)
check('R56纸面比例: A3图框(纸面mm,倾斜3.3°)经F-纸面通道识别, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['width'] - 297) < 3 and abs(r['height'] - 420.5) < 3)

# ---- 用例 74：R56 护栏——纸面模式下非 A 系短边框不收 ----
# 同形态但外框短边 305（偏离 A3 297 达 2.7% >±1%）→ F-纸面不命中，其余通道
# （A 占比/C rel/B 短边/F 短边）同样过不了 → fc=0。守护：纸面通道只收标准 A 系纸。
doc = make_doc()
msp = doc.modelspace()
_rot_add_rect(msp, 150, 210, 305, 431)
for _k in range(13):
    _ly = -190 + _k * 30
    _x1 = 150 + (-128) * _ca - _ly * _sa
    _y1 = 210 + (-128) * _sa + _ly * _ca
    _x2 = 150 + 128 * _ca - _ly * _sa
    _y2 = 210 + 128 * _sa + _ly * _ca
    msp.add_line((_x1, _y1), (_x2, _y2))
_blk56b = doc.blocks.new(name='CREDIT_BLK2')
_rot_add_rect(_blk56b, 900, 300, 1500, 1000)
_blk56b.add_line((0, 0), (3195, 184))
msp.add_blockref('CREDIT_BLK2', (0, 0))
data = to_bytes(doc)
check('R56护栏: 纸面模式非A系短边框不收, fc=0 (smart)',
      lambda: expect_error_no_frame(data),
      lambda ok: ok)


# ---- 用例 75：R58 巨块救援触达 + 嵌套子块 + 内容块误报剔除（P_10#给排水平面图） ----
# 场景复刻：模型空间 6 张内外双框图（4×59400×84100 + 84100×59400 + 59400×42000，
# 内框同心内缩 1000）+ 巨型拼接底图块（世界 bbox ratio 远超 5.5——子块几何离散
# 分布撑大，R58 前预筛直接剔除、R40 救援只挂 R27 拒绝分支永不触达）内含 7 张图
# （3×84100×59400 + 4×42050×29700，内外双框）+ 嵌套子块内 1 张 A3 缩印
# （23780×16820+23080×16420 嵌套对）+ 2 个 S-FLOOR 楼板环内容块（9 顶点非矩形环
# bbox 恰为块尺寸 30200×47400，冒充闭合证据过 R27、rel 过 C）。R58 修复后应
# fc=14：4+4+1+3+1+1，无 30200×47400。
doc = make_doc()
msp = doc.modelspace()
# 模型空间 6 张双框图（外框 + 内框）
for _x, _y, _w, _h in ((0, 300000, 59400, 84100), (70000, 300000, 59400, 84100),
                       (140000, 300000, 59400, 84100), (210000, 300000, 59400, 84100),
                       (0, 200000, 84100, 59400), (0, 100000, 59400, 42000)):
    add_closed_rect(msp, _x, _y, _w, _h)
    add_closed_rect(msp, _x + 1000, _y + 1000, _w - 2000, _h - 2000)
# 巨型拼接底图块：顶层 7 张双框图 + 嵌套子块 1 张缩印 + 远端散线撑大 bbox
_blk_big = doc.blocks.new(name='BIG_XREF')
for _x, _y, _w, _h in ((0, 0, 84100, 59400), (90000, 0, 84100, 59400),
                       (180000, 0, 84100, 59400),
                       (0, 70000, 42050, 29700), (90000, 70000, 42050, 29700),
                       (180000, 70000, 42050, 29700), (270000, 70000, 42000, 29700)):
    add_closed_rect(_blk_big, _x, _y, _w, _h)
    add_closed_rect(_blk_big, _x + 1000, _y + 1000, _w - 2000, _h - 2000)
_blk_sub = doc.blocks.new(name='SUB_SHEET')
add_closed_rect(_blk_sub, 0, 0, 23780, 16820)
add_closed_rect(_blk_sub, 350, 200, 23080, 16420)
for _k in range(15):   # 缩印框内内容（虽走嵌套对豁免，保持真实感）
    _blk_sub.add_line((1000, 1000 + _k * 900), (22000, 1000 + _k * 900))
_blk_big.add_blockref('SUB_SHEET', (360000, 0))
_blk_big.add_line((800000, 5000), (800000, 120000))   # 散线撑大 bbox（ratio≈8 >5.5 触发预筛剔除）
msp.add_blockref('BIG_XREF', (1000000, 0))
# 2 个楼板环内容块（9 顶点非矩形环 bbox = 块尺寸，R27 旧口径全尺寸闭合证据）
for _i, _bn in enumerate(('SLAB_A', 'SLAB_B')):
    _blk = doc.blocks.new(name=_bn)
    _ox = _i * 40000
    _blk.add_lwpolyline([
        (_ox, 0), (_ox, 15000), (_ox + 5000, 15000), (_ox + 5000, 47400),
        (_ox + 30200, 47400), (_ox + 30200, 5000), (_ox + 25000, 5000),
        (_ox + 25000, 0),
    ], close=True)
    for _k in range(20):   # 块内平面图内容
        _blk.add_line((_ox + 2000, 2000 + _k * 2000), (_ox + 28000, 2000 + _k * 2000))
    msp.add_blockref(_bn, (2000000 + _i * 50000, 50000))
data = to_bytes(doc)
check('R58巨块救援+嵌套子块+楼板环误报剔除: fc=14 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 14
      and sum(1 for c in r['candidates'] if abs(c['width'] - 59400) < 5 and abs(c['height'] - 84100) < 5) == 4
      and sum(1 for c in r['candidates'] if abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5) == 4
      and sum(1 for c in r['candidates'] if abs(c['width'] - 42050) < 5) == 3
      and sum(1 for c in r['candidates'] if abs(c['width'] - 42000) < 5) == 1
      and sum(1 for c in r['candidates'] if abs(c['width'] - 23780) < 5) == 1
      and sum(1 for c in r['candidates'] if abs(c['width'] - 59400) < 5 and abs(c['height'] - 42000) < 5) == 1
      and not any(abs(c['width'] - 30200) < 5 for c in r['candidates']))

# ---- 用例 76：R58 护栏——普通预筛（ratio 失配但非巨型）仍剔除 ----
# 60000×8000 内容块（ratio 7.5 >5.5，长边 <200000）维持预筛剔除，不放行 R27，
# 守护 R58 放行门槛（≥200000）不外溢。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 84100, 59400)
add_closed_rect(msp, 1000, 1000, 82100, 57400)
_blk_n = doc.blocks.new(name='NARROW_STRIP')
add_closed_rect(_blk_n, 0, 0, 60000, 8000)
for _k in range(10):
    _blk_n.add_line((2000, 1000 + _k * 600), (58000, 1000 + _k * 600))
msp.add_blockref('NARROW_STRIP', (200000, 0))
data = to_bytes(doc)
check('R58护栏: 非巨型ratio失配块仍预筛剔除, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['width'] - 84100) < 5 and abs(r['height'] - 59400) < 5
      and not any(abs(c['width'] - 60000) < 5 for c in r['candidates']))


# ---- 用例 77：R59 闭合多段线条件C上限收窄（X-11号平面.dwg 4→0） ----
# 场景复刻：图中无任何图框，但 4 个"完美矩形"闭合多段线冒充图框——
# 1× 49000×21500（填充层大框，框内 92 实体）+ 3× 19600×8600（A-WALL 墙轮廓，
# 框内仅 3~4 实体），全部 4 顶点矩形度 1.000、ratio 2.2791。原 2.5 上限放行
# 过条件 C（大框 rel=100%、小框 rel≈16%）→ fc=4 误报。R59 将闭合多段线
# 条件 C 上限收到 2.15（合法观测上限 2.124）→ 4 个全部拒绝，fc=0。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, -35852, -14758, 49000, 21500)
for _k in range(20):   # 大框内内容（填充边界周边几何，保持真实感）
    msp.add_line((-34000, -13000 + _k * 900), (10000, -13000 + _k * 900))
for _i, _x0 in enumerate((95389, 188941, 286218)):
    _dy = -33358 + _i * 640
    add_closed_rect(msp, _x0, _dy, 19600, 8600)
    for _k in range(3):   # 墙轮廓内稀疏内容
        msp.add_line((_x0 + 2000, _dy + 2000 + _k * 1500), (_x0 + 17000, _dy + 2000 + _k * 1500))
data = to_bytes(doc)
check('R59闭合多段线C上限收窄: 墙轮廓矩形(2.28)全部拒绝, fc=0 (smart)',
      lambda: expect_error_no_frame(data),
      lambda ok: ok)

# ---- 用例 78：R59 护栏——合法加长比例闭合多段线图框不受收窄影响 ----
# 126150×59409（ratio 2.124 ≤ 2.15，回归中合法观测上限）闭合多段线图框
# 仍应经条件 C 正常识别，守护收窄边界不误杀临界合法图框。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 126150, 59409)
for _k in range(15):
    msp.add_line((2000, 2000 + _k * 3500), (124000, 2000 + _k * 3500))
data = to_bytes(doc)
check('R59护栏: 合法ratio 2.124闭合多段线图框仍识别, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['width'] - 126150) < 5 and abs(r['height'] - 59409) < 5)


# ---- 用例 79：R60 多页拼版块拆分（美立方-说明.dwg 9→18） ----
# 场景复刻：说明模板块内含 2 张互不嵌套的 84100×59400 页框（上下排布）
# + 各 1 张 80600×57400 嵌套内框。块本体合并 bbox 84100×125300（ratio 1.49）
# 形状合格 → 旧代码把整个块当 1 个图框（2 实例 fc=2，18 张页漏 9 张）。
# R60 拆分后：2 实例 × 2 页 = fc=4，且无 84100×125300 块本体候选。
doc = make_doc()
msp = doc.modelspace()
_blk_2p = doc.blocks.new(name='TMP_2PAGE')
add_closed_rect(_blk_2p, 0, 0, 84100, 59400)         # 页框 1
add_closed_rect(_blk_2p, 1000, 1000, 80600, 57400)   # 嵌套内框 1
add_closed_rect(_blk_2p, 0, -65900, 84100, 59400)    # 页框 2（上下排布，不嵌套）
add_closed_rect(_blk_2p, 1000, -64900, 80600, 57400) # 嵌套内框 2
msp.add_blockref('TMP_2PAGE', (0, 0))
msp.add_blockref('TMP_2PAGE', (200000, 0))
data = to_bytes(doc)
check('R60多页拼版块拆分: 块内2页框×2实例, fc=4 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and sum(1 for c in r['candidates'] if abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5) == 4
      and not any(abs(c['width'] - 84100) < 5 and abs(c['height'] - 125300) < 5 for c in r['candidates']))

# ---- 用例 80：R60 护栏——单页模板块（外框+嵌套内框）不受拆分影响 ----
# 块内仅 1 个极大页框（嵌套内框被极大框过滤）→ 不拆分，块本体照常入库。
# 2 个实例 → fc=2，尺寸=块 bbox 84100×59400。
doc = make_doc()
msp = doc.modelspace()
_blk_1p = doc.blocks.new(name='TMP_1PAGE')
add_closed_rect(_blk_1p, 0, 0, 84100, 59400)
add_closed_rect(_blk_1p, 1000, 1000, 80600, 57400)
msp.add_blockref('TMP_1PAGE', (0, 0))
msp.add_blockref('TMP_1PAGE', (200000, 0))
data = to_bytes(doc)
check('R60护栏: 单页模板块不拆分, fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and sum(1 for c in r['candidates'] if abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5) == 2)


# ---- 用例 81：R61 旋转豁免探测扩展到块内页框（美立方-说明 尺寸 64423x87569→84100x59400） ----
# 场景复刻：顶层无图框级几何、仅大量 10° 斜线主导方向直方图（触发旋转条件），
# 页框全在模板块内且轴对齐。R61 前豁免探测只扫顶层 → 误施加旋转矫正，页框
# 外接框膨胀（84100×59400 转 10° → ≈94000×71000）；R61 块内页框同样豁免 →
# 不旋转，尺寸精确 84100×59400。
doc = make_doc()
msp = doc.modelspace()
_blk_2q = doc.blocks.new(name='TMP_2PAGE_ROT')
add_closed_rect(_blk_2q, 0, 0, 84100, 59400)
add_closed_rect(_blk_2q, 1000, 1000, 80600, 57400)
add_closed_rect(_blk_2q, 0, -65900, 84100, 59400)
add_closed_rect(_blk_2q, 1000, -64900, 80600, 57400)
msp.add_blockref('TMP_2PAGE_ROT', (0, 0))
_math81 = __import__('math')
for _k in range(20):   # 10° 斜线主导直方图（总长 20×1500=30k ≪ 页框周长 574k → 支配性豁免触发）
    _x0 = _k * 3000.0
    _dx = 1500.0 * _math81.cos(_math81.radians(10))
    _dy = 1500.0 * _math81.sin(_math81.radians(10))
    msp.add_line((_x0, -800000 + _k * 500), (_x0 + _dx, -800000 + _k * 500 + _dy))
data = to_bytes(doc)
check('R61块内页框豁免旋转: 斜线主导+块内轴对齐页框不旋转, fc=2 尺寸精确 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and all(abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5
              for c in r['candidates']))


# ---- 用例 82：R62 形态A——单页图框块免被底图包络巨框「留大剔小」 ----
# 场景复刻（1#,8#住宅0721出图_t5）：单页模板块（块定义 84100×59400+内框）实例
# 通过特征检查后，被顶层闭合多段线巨框（底图包络，rel=100%，面积 ≥4×、比例差
# >10%）按去重规则 1 嵌套剔小。R62 保护后块实例候选保留。
doc = make_doc()
msp = doc.modelspace()
_blk_spb = doc.blocks.new(name='TMP_SPB')
add_closed_rect(_blk_spb, 0, 0, 84100, 59400)
add_closed_rect(_blk_spb, 1000, 1000, 80600, 57400)
msp.add_blockref('TMP_SPB', (0, 0))
add_closed_rect(msp, 0, -200000, 280000, 95000)   # 底图包络巨框（面积 5.3×、ratio 2.95）
data = to_bytes(doc)
check('R62形态A去重保护: 单页图框块免被包络巨框剔小, 块候选保留 (smart)',
      lambda: parse(data),
      lambda r: any(abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5
                    for c in r['candidates']))


# ---- 用例 83：R62 形态B——同尺寸表格页互证救援块参照加长页框 ----
# 场景复刻（wetwetwt 块）：加长页框 16820×32700（ratio 1.944 非 √2）以两种形态
# 存在——顶层闭合多段线 1 张（框内 30 横+6 竖表格 LINE，条件I 主判据救）+ 模板
# 块 2 实例（块内表格是 LWPOLYLINE 单元格，无 LINE → 横竖线计数 0）。R62 前块
# 实例漏检；互证通道：与已确认表格页同尺寸（±2%）→ 放行，fc=3。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 16820, 32700)            # 顶层表格页（闭合多段线）
for _k in range(30):                                 # 表格行线（横）
    msp.add_line((1000, 2000 + _k * 1000), (15800, 2000 + _k * 1000))
for _k in range(6):                                  # 列分隔线（竖）
    msp.add_line((2000 + _k * 2500, 1000), (2000 + _k * 2500, 31700))
_blk_tp = doc.blocks.new(name='TMP_TBLPEER')
add_closed_rect(_blk_tp, 0, 0, 16820, 32700)         # 页框（块内表格为 LWPOLYLINE 单元格，非 LINE）
msp.add_blockref('TMP_TBLPEER', (200000, 0))
msp.add_blockref('TMP_TBLPEER', (200000, -100000))
data = to_bytes(doc)
check('R62形态B同尺寸表格页互证: 块参照加长页框×2 放行, fc=3 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 16820) < 340 and abs(c['height'] - 32700) < 660) == 3)


# ---- 用例 84：R62b 表格块误报——块参照无闭合边框不被条件I 当表格页救援 ----
# 场景复刻（1#,8#住宅 qewrwetqrqwtqr）：门窗表内容块（块定义 33 条 LINE 横竖
# 网格、无闭合 LWPOLYLINE 边框）bbox 恰好罩住户型内容散线 → 条件I 数线（顶层
# 散线 ≥30/6）误判为"表格页"→ 误报 32143×20725。R62b 加护栏：块定义内无闭合
# LWPOLYLINE → 完全跳过条件I 救援。真图框（84100×59400）照常识别，fc=1。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 84100, 59400)            # 真图框（rel 分母 + 真框）
_blk_tbl = doc.blocks.new(name='TMP_TABLE')
for _k in range(30):                                 # 块内表格横线（无 LWPOLYLINE）
    _blk_tbl.add_line((0, _k * 400), (20000, _k * 400))
for _k in range(6):                                  # 块内表格竖线
    _blk_tbl.add_line((_k * 3500, 0), (_k * 3500, 11600))
msp.add_blockref('TMP_TABLE', (200000, 0))           # 表格块实例（远离真图框）
for _k in range(30):                                 # 顶层散线（落入表格块 bbox）
    msp.add_line((200100, _k * 400 + 50), (219900, _k * 400 + 50))
for _k in range(6):
    msp.add_line((200000 + _k * 3500 + 100, 100), (200000 + _k * 3500 + 100, 11500))
data = to_bytes(doc)
check('R62b表格块误报护栏: 块内无闭合边框不被条件I救援, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and all(abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5
              for c in r['candidates']))


# ---- 用例 85：R62b 底图包络巨框剔除——内含 ≥2 个单页图框块 → 包裹框 ----
# 场景复刻（1#,8#住宅 巨框 388331×215417 内包 2 个 qwetwetwtt 单页图框块）：
# 顶层闭合多段线巨框（ratio 非标准）内包 2 个单页图框块（块定义≈候选尺寸），
# 仅 1 组×2 份达不到 MIN_WRAPPED_SAME_SIZE=3 → 旧代码漏剔巨框。R62b 判据：
# 闭合多段线内 ≥2 个「块参照插入 + 块定义比例≈候选比例」→ 巨框包裹框剔除。
doc = make_doc()
msp = doc.modelspace()
_blk_spb2 = doc.blocks.new(name='TMP_SPB2')
add_closed_rect(_blk_spb2, 0, 0, 84100, 59400)
add_closed_rect(_blk_spb2, 1000, 1000, 80600, 57400)
msp.add_blockref('TMP_SPB2', (0, 0))
msp.add_blockref('TMP_SPB2', (100000, 0))
add_closed_rect(msp, 0, -20000, 200000, 90000)   # 底图包络巨框（ratio 1.818，包住 2 图框块）
data = to_bytes(doc)
check('R62b底图包络巨框剔除: 内包2个单页图框块, 巨框被剔 fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and all(abs(c['width'] - 84100) < 5 and abs(c['height'] - 59400) < 5
              for c in r['candidates']))


# ---- 用例 86：R62c 单页图框块尺寸——用图框边框而非含溢出的块 bbox ----
# 场景复刻（1#,8#住宅 qwetwetwtt 块 134300×84600→133800×84100、wetwetwt
# 16920×32800→16820×32700、ewryeutuuuuu 42300×29950→42050×29700）：单页
# 图框块的块 bbox 被内容溢出撑大，用户口径是图框边框尺寸。R62c：块内恰有
# 1 个极大页框（图框边框）且 ≥块 bbox 95% → 用页框尺寸×缩放作为候选尺寸。
doc = make_doc()
msp = doc.modelspace()
_blk_fs = doc.blocks.new(name='TMP_FRAMESIZE')
add_closed_rect(_blk_fs, 0, 0, 84100, 59400)            # 图框边框
_blk_fs.add_line((84100, 29700), (84600, 29700))        # 内容溢出 500mm（右伸）
msp.add_blockref('TMP_FRAMESIZE', (0, 0))
data = to_bytes(doc)
check('R62c单页图框块尺寸: 用图框边框而非含溢出bbox, 84100x59400 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['candidates'][0]['width'] - 84100) < 5
      and abs(r['candidates'][0]['height'] - 59400) < 5)

# ---- 用例 87：R63 包裹框内同尺寸互证补捞（图纸深化0722 场景） ----
# 底图包络（闭合多段线 90000×40000，ratio 2.25）内含 1 个主导尺寸真框
# （40000×28284，主导 ratio 1.414 由框外 3 份同款确立）+ 2 个 16820×29720
# 孤儿页框。孤儿因"被已入选框（包络）包住"被条件I 跳过救援；包络被剔除后
# 沦为漏检。R63：包络剔除后按"与已入选框同尺寸 ±2% 互证"补捞孤儿。
# 框外 2 张 16820×29720 带表格线（横 ≥30/竖 ≥6）经条件I 入选提供互证锚。
doc = make_doc()
msp = doc.modelspace()
for _i in range(3):                                   # 主导尺寸组（确立主导比例）
    add_line_rect(msp, _i * 45000, 0, 40000, 28284)
for _i, _ox in enumerate((120000, 145000)):           # 条件I 表格页 ×2（互证锚）
    add_closed_rect(msp, _ox, 0, 16820, 29720)
    for _k in range(32):
        _yy = 200 + _k * 900
        msp.add_line((_ox + 150, _yy), (_ox + 16820 - 150, _yy))
    for _k in range(7):
        _xx = _ox + 2400 + _k * 1800
        msp.add_line((_xx, 150), (_xx, 29720 - 150))
add_closed_rect(msp, 400000, 0, 90000, 40000)         # 底图包络（ratio 2.25）
add_line_rect(msp, 410000, 5000, 40000, 28284)        # 包络内的主导尺寸真框
add_closed_rect(msp, 410000, 35000, 16820, 29720)     # 孤儿 1（无表格线）
add_closed_rect(msp, 460000, 35000, 16820, 29720)     # 孤儿 2（无表格线）
data = to_bytes(doc)
check('R63包裹框内补捞: 包络剔除后孤儿按同尺寸互证救回, fc=8 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 8
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 16820) < 5 and abs(c['height'] - 29720) < 5) == 4
      and not any(abs(c['width'] - 90000) < 5 for c in r['candidates']))

# ---- 用例 88：R63 相邻同尺寸共边单元格剔除（0722 户型表分格） ----
# 两个 23938×34315 闭合多段线左右共边（层 0、无内框、无图签、同尺寸组恰 2 份）
# → 大表拆分格，成对剔除（fc=0 报未检测到图框）。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 23938, 34315)
add_closed_rect(msp, 23938, 0, 23938, 34315)
data = to_bytes(doc)
ok = expect_error_no_frame(data)
results.append(ok)
print(('PASS' if ok else 'FAIL') + '  R63相邻单元格: 同尺寸共边分格成对剔除 (smart)')

# ---- 用例 89：R63 护栏——≥3 份同尺寸网格拼版页豁免（江南铭庭场景） ----
# 2×2 网格 4 张 14100×10000 闭合多段线页框（同尺寸组 4 份 ≥3）→ 是刻意拼版
# 图纸页而非分格，不受相邻单元格剔除影响，fc=4。
doc = make_doc()
msp = doc.modelspace()
for _i in range(2):
    for _j in range(2):
        add_closed_rect(msp, _i * 14100, _j * 10000, 14100, 10000)
data = to_bytes(doc)
check('R63护栏: 同尺寸≥3份网格拼版页豁免, fc=4 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 14100) < 5 and abs(c['height'] - 10000) < 5) == 4)

# ---- 用例 90：R64 判据③——近正方形巨型包络不作 rel 分母（1、8#商业平面图0606_t6HVAC） ----
# N-AIR-文字层 1239572×1232218（ratio 1.006）垃圾边界矩形罩住图框簇：面积是真框
# 128 倍、area_ratio 仅 4.4%（离群实体撑大 layout 总面积）——判据①② 都不命中，
# 霸占 rel 分母后真框 rel 0.78% 全拒 → fc=0。判据③：ratio < √2×0.95 的近正方形
# 候选内含 ≥2 个近√2 候选（各 ≤ 自身 1/20）→ 踢出分母。
doc = make_doc()
msp = doc.modelspace()
for _i in range(3):
    add_closed_rect(msp, 10000 + _i * 100000, 10000, 89100, 133650)
add_closed_rect(msp, 0, 0, 600000, 600000)            # 近正方形巨框（ratio 1.0）
add_closed_rect(msp, 3000000, 3000000, 10000, 10000)  # 离群实体撑大 layout 总面积
data = to_bytes(doc)
check('R64近正方形巨框包络: 踢出rel分母后框内真框按C入选, fc=3 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 89100) < 5 and abs(c['height'] - 133650) < 5) == 3
      and not any(abs(c['width'] - 600000) < 5 for c in r['candidates']))

# ---- 用例 91：R64 条件F 图签层（TTLB）短边豁免（HVAC 小附表框 16820×23780） ----
# TTLB 图签层闭合多段线短边 16820（<20000）：ratio 1.4139 偏差 0.02%、rel 3.4%，
# F 主门槛够不着 → 漏检。图签层是图框专用层，短边 ≥15000 即放行。
doc = make_doc()
msp = doc.modelspace()
doc.layers.add('A-ANNO-TTLB')
add_closed_rect(msp, 0, 0, 89100, 133650)             # 大框走条件C
_pl = msp.add_lwpolyline(
    [(200000, 0), (200000 + 16820, 0), (200000 + 16820, 23780), (200000, 23780)],
    close=True)
_pl.dxf.layer = 'A-ANNO-TTLB'
for _k in range(14):                                  # 框内内容证据 ≥12
    msp.add_line((200100 + _k * 500, 500), (200100 + _k * 500, 23000))
data = to_bytes(doc)
check('R64条件F图签层豁免: TTLB层16820x23780短边16820按F入选, fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and any(abs(c['width'] - 16820) < 5 and abs(c['height'] - 23780) < 5
              for c in r['candidates']))

# ---- 用例 92：R65 条件H2——TTLB 图签层嵌套双框块救援（10#商业平面图场景） ----
# 标准图框块（63050×29700 横版 ratio 2.1229，块内 TTLB 层双线框 63050×29700 +
# 61300×28700 面积比 0.941）：与同图竖版主导框（ratio 1.414）比例完全不同 →
# 条件H 不命中；rel 4.2% <10%（分母被 300000×150000 包裹框抬高，该包裹框随后
# 被包裹框剔除收掉）→ 条件C 拒；块参照不在条件F 范围 → 漏检。H2：块内 TTLB
# 嵌套双框 = 标准图框画法，救援入选。
doc = make_doc()
msp = doc.modelspace()
doc.layers.add('A-ANNO-TTLB')
add_closed_rect(msp, 0, 0, 300000, 150000)            # 大地板候选（ratio 2.0 过C
msp.add_line((5000000, 5000000), (5100000, 5000000))  #   后作包裹框被剔；远端离群
for _i in range(3):                                   #   实体撑大 total 使其
    add_closed_rect(msp, 5000 + _i * 70000, 5000, 63000, 89100)  #   area_ratio<50%
_blk = doc.blocks.new(name='TTLB_FRAME_BLK')          #   不触发判据①踢分母，
_p1 = _blk.add_lwpolyline(
    [(0, 0), (63050, 0), (63050, 29700), (0, 29700)], close=True)
_p1.dxf.layer = 'A-ANNO-TTLB'
_p2 = _blk.add_lwpolyline(
    [(875, 500), (62175, 500), (62175, 29200), (875, 29200)], close=True)
_p2.dxf.layer = 'A-ANNO-TTLB'
msp.add_blockref('TTLB_FRAME_BLK', (230000, 0))
data = to_bytes(doc)
check('R65条件H2: TTLB嵌套双框块横版图框救援, fc=4 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 63050) < 5 and abs(c['height'] - 29700) < 5) == 1
      and not any(abs(c['width'] - 300000) < 5 for c in r['candidates']))

# 开关回退对照：关掉 H2 后横版块回到漏检状态（fc=3）
check('R65开关回退: NO_I_TTLB_BLOCK=1 时横版块漏检, fc=3 (smart)',
      lambda: _parse_env(data, FRAME_PARSER_NO_I_TTLB_BLOCK='1'),
      lambda r: r['frame_count'] == 3
      and not any(abs(c['width'] - 63050) < 5 for c in r['candidates']))

# ---- 用例 93：R66 共边网格链剔除（地下室接地 分格阵列场景） ----
# 模型空间 5×2 分格阵列（相邻共边、尺寸微差 7802/7803/7798/7797——手工画边界）+
# 2 个失去互证的散块。rel 10~16% 全过条件C、互证评分 ≥1 逃过孤证复核。
# R66：相邻共边连通分量 ≥3 → 整分量剔除（无同尺寸组≥3/图签层/内框豁免）；
# 散块随之失去"同比例兄弟"互证 → 孤证复核收掉。加 1 张 TTLB 真图框守护豁免。
doc = make_doc()
msp = doc.modelspace()
doc.layers.add('A-ANNO-TTLB')
_xs = [266848, 274648, 281845, 289647, 297445, 305248, 313045]
for _i, _x in enumerate(_xs):                         # 下行 7 个共边分格
    _w = (7803, 7197, 7802, 7798, 7803, 7797, 7803)[_i]
    add_line_rect(msp, _x, 39308, _w, 12992)
for _i, _x in enumerate(_xs[:5]):                     # 上行 5 个共边分格
    _w = (7550, 7197, 7802, 7798, 7803)[_i]
    add_line_rect(msp, _x, 175950, _w, 16000)
add_line_rect(msp, 259295, 79977, 15353, 8490)        # 散块（互证靠下行分格）
add_line_rect(msp, 259295, 88467, 15353, 7483)
_pl = msp.add_lwpolyline(                             # TTLB 真图框（豁免守护）
    [(600000, 0), (684100, 0), (684100, 59400), (600000, 59400)], close=True)
_pl.dxf.layer = 'A-ANNO-TTLB'
data = to_bytes(doc)
check('R66共边网格链: 分格阵列整分量剔除, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and any(abs(c['width'] - 84100) < 5 for c in r['candidates'])
      and not any(abs(c['width'] - 7803) < 100 for c in r['candidates']))

# 开关回退对照：无 TTLB 框时（分母=阵列自身最大者）关掉链剔除 →
# 阵列经条件C 全部混入（fc>1）；链剔除开启则 fc=0 报未检测到图框。
doc2 = make_doc()
msp2 = doc2.modelspace()
for _i, _x in enumerate(_xs):
    _w = (7803, 7197, 7802, 7798, 7803, 7797, 7803)[_i]
    add_line_rect(msp2, _x, 39308, _w, 12992)
for _i, _x in enumerate(_xs[:5]):
    _w = (7550, 7197, 7802, 7798, 7803)[_i]
    add_line_rect(msp2, _x, 175950, _w, 16000)
data2 = to_bytes(doc2)
ok = expect_error_no_frame(data2)
results.append(ok)
print(('PASS' if ok else 'FAIL') + '  R66链剔除: 纯阵列图 fc=0 (smart)')
check('R66开关回退: NO_GRID_CHAIN=1 时纯阵列混入, fc>1 (smart)',
      lambda: _parse_env(data2, FRAME_PARSER_NO_GRID_CHAIN='1'),
      lambda r: r['frame_count'] > 1)

# ---- 用例 94：R67 独立双页外包络剔除（美立方结施平面图8.1 场景） ----
# R 层大框 146990×201938（√2 偏差 2.9% ≤10%）受"√2 纸张边框保护"跳过包裹剔除，
# 罩住并排独立页 84100×59400×2 + 126200×59400×1 → 嵌套去重"留大剔小"吃掉 3 张
# 真图框。R67：√2 保护大框内存在同尺寸、bbox 分离的候选对（并排独立页，区别于
# 同心重复画法）→ 破例判外包络剔除。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 700571, -239325, 146990, 201938)   # √2 保护大框（ratio 1.374）
add_closed_rect(msp, 708063, -169001, 84100, 59400)     # 独立页 1
add_closed_rect(msp, 708063, -109001, 84100, 59400)     # 独立页 2（y 分离）
add_closed_rect(msp, 708063, -229001, 126200, 59400)    # 独立页 3（不同尺寸）
data = to_bytes(doc)
check('R67独立双页: √2保护大框罩并排独立页仍被剔, fc=3 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and not any(abs(c['width'] - 146990) < 5 for c in r['candidates'])
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 84100) < 5) == 2)

# 开关回退对照：关掉 R67 后大框幸存 → 嵌套去重吃掉独立页
check('R67开关回退: NO_WRAP_SEP=1 时大框幸存吃掉独立页, fc=1 (smart)',
      lambda: _parse_env(data, FRAME_PARSER_NO_WRAP_SEP='1'),
      lambda r: r['frame_count'] == 1
      and any(abs(c['width'] - 146990) < 5 for c in r['candidates']))

# ---- 用例 95：R68 被剔外包络内的条件H 孤立候选撤销（美立方结施 11221×7961） ----
# 条件H 候选（主导比例擦线命中 + 内容丰富）位于"边线"层范围框内部：该大框经
# 条件I 入选后被包裹剔除，但 H 救援发生在条件I 之前、护栏看不到未入选的大框
# → 孤立 H 候选幸存成多余图框。R68：被剔外包络内、无同尺寸副本的 cond_h 候选撤销。
# 构造：主导比例真框 ×3（确立主导 1.414）+ 边线层范围框（ratio 2.45 经条件I+包裹
# 剔除）内 1 个 11221×7961 H 候选（框内内容 ≥12）。
doc = make_doc()
msp = doc.modelspace()
doc.layers.add('边线')
for _i in range(3):
    add_closed_rect(msp, _i * 90000, 0, 84100, 59400)
_pl = msp.add_lwpolyline(
    [(400000, 0), (400000 + 191760, 0), (400000 + 191760, 78166), (400000, 78166)],
    close=True)
_pl.dxf.layer = '边线'
msp.add_line((3000000, 3000000), (3100000, 3000000))  # 离群实体撑大 total：
for _k in range(40):                                  # 191760 的 ar<15% 不过 A
    msp.add_line((401000, 2000 + _k * 1900), (590000, 2000 + _k * 1900))
for _k in range(8):                                   # （与真实场景同时序：H 先跑
    msp.add_line((402000 + _k * 23000, 500), (402000 + _k * 23000, 77000))
_pl2 = msp.add_lwpolyline(
    [(450000, 20000), (450000 + 11221, 20000),
     (450000 + 11221, 20000 + 7961), (450000, 20000 + 7961)], close=True)
for _k in range(14):                                  # 小框内内容 ≥12（条件H）
    msp.add_line((450100 + _k * 500, 20500), (450100 + _k * 500, 27000))
data = to_bytes(doc)
check('R68被剔外包络内H候选: 边线框内孤立小框撤销, fc=3 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and not any(abs(c['width'] - 11221) < 5 for c in r['candidates']))

# 开关回退对照：关掉 R68（R69 量级断层在 20% 阈值下覆盖本场景——11221 短边
# 7961×5=39805 < 59400 同样被撤），须双开关全关才能看到 R68 单独行为（fc=4）
check('R68开关回退: NO_H_EVICT=1 时小框幸存, fc=4 (smart)',
      lambda: _parse_env(data, FRAME_PARSER_NO_H_EVICT='1',
                         FRAME_PARSER_NO_SCALE_CLIFF='1'),
      lambda r: r['frame_count'] == 4
      and any(abs(c['width'] - 11221) < 5 for c in r['candidates']))

# ---- 用例 96：R69 救援候选量级断层撤销（1#8#平面、墙、柱 场景） ----
# 三个小详图框 5950×8200（条件I table_page）、5919×8200（table_peer 互证）、
# 3100×6600（条件H）聚集在图框网格外角落——同图 24 张真框短边全部 ≥40000，
# 三框短边仅 3100~5950（7.8%~14.9%）。R69：救援候选短边 ×5 < 同 layout 非救援
# 已入选图框最小短边 → 撤销（20% 断层线，乘法形式规避浮点边界）。
doc = make_doc()
msp = doc.modelspace()
for _i in range(3):                                   # 真框网格（短边 42000）
    add_closed_rect(msp, _i * 80000, 0, 74300, 42000)
add_closed_rect(msp, -100000, -50000, 5950, 8200)     # 小框 1（条件I 锚：表格线）
for _k in range(32):
    msp.add_line((-99800, -49000 + _k * 250), (-94200, -49000 + _k * 250))
for _k in range(10):
    msp.add_line((-99000 + _k * 500, -49500), (-99000 + _k * 500, -39000))
add_closed_rect(msp, -60000, -50000, 5919, 8200)      # 小框 2（peer 互证）
_hx, _hy = -130000, -60000
add_line_rect(msp, _hx, _hy, 4000, 7076)              # 小框 3（ratio 1.769 命中主导，条件H）
for _k in range(14):
    msp.add_line((_hx + 100 + _k * 250, _hy + 100),
                 (_hx + 100 + _k * 250, _hy + 300 + (_k % 3) * 200))
data = to_bytes(doc)
check('R69量级断层: 三小框短边<20%被撤, fc=3 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and not any(c['width'] < 10000 for c in r['candidates']))

# 开关回退对照
check('R69开关回退: NO_SCALE_CLIFF=1 时小框混入, fc>3 (smart)',
      lambda: _parse_env(data, FRAME_PARSER_NO_SCALE_CLIFF='1'),
      lambda r: r['frame_count'] > 3)

# ---- 用例 97：R70 判据④——巨型页面外包络踢出 rel 分母（g001、g003~g017 场景） ----
# 全图外包络 200000×135000（ratio 1.4815，√2 偏差 4.7% 恰好近 √2 逃过判据③的
# "近正方形"窗口），面积是真框 89100×42000 的 7.6 倍（断层 ≥10 倍），内部含
# 2 张并排独立页（89100×42000，ratio 2.1214）+ 各自内框（85600×40000 与外框
# 聚同组但同心重叠——组内存在分离对即可）。判据④：踢出 rel 分母 → 页面框
# rel=100% 过条件C。远端散点撑大 total 压低 area_ratio 复刻真实场景。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 400000, 270000)            # 全图外包络（ratio 1.4815，
                                                      #   面积=页框 28.9 倍 → rel 3.5%）
add_closed_rect(msp, 5000, 5000, 89100, 42000)        # 页 1（ratio 2.1214 走C）
add_closed_rect(msp, 5000, 50000, 89100, 42000)       # 页 2（y 分离）
add_closed_rect(msp, 7000, 7000, 85600, 40000)        # 页 1 内框（与外框聚同组）
add_closed_rect(msp, 7000, 52000, 85600, 40000)       # 页 2 内框
add_closed_rect(msp, 100000, 5000, 59400, 42000)      # 近√2 页 ×2（判据②触发源）
add_closed_rect(msp, 100000, 50000, 59400, 42000)
for _k in range(14):                                  # 近√2 页内容 ≥12（F 通道用）
    msp.add_line((101000 + _k * 400, 6000), (101000 + _k * 400, 46000))
for _k in range(14):
    msp.add_line((101000 + _k * 400, 51000), (101000 + _k * 400, 91000))
msp.add_line((3000000, 3000000), (3100000, 3000000))  # 离群实体撑大 total
data = to_bytes(doc)
check('R70巨框分母: 近√2全图外包络踢出rel分母, fc=4 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 89100) < 5) == 2
      and not any(abs(c['width'] - 400000) < 5 for c in r['candidates']))

# 开关回退对照：关掉判据④ → 分母被巨框霸占 → 89100 页框 rel 3.5% 全灭
# （59400 页框 ratio 1.4143 走条件F 幸存）
check('R70开关回退: NO_GIANT_PAGE=1 时 89100 页框全灭, fc=2 (smart)',
      lambda: _parse_env(data, FRAME_PARSER_NO_GIANT_PAGE='1'),
      lambda r: r['frame_count'] == 2
      and not any(abs(c['width'] - 89100) < 5 for c in r['candidates']))

# ---- 用例 97b：R74 判据④修正——top2 基于①②③预排除后候选 ----
# 场景（20130729地下室-(0版)-0-1_t8）：1611187×408405（ratio 3.945）被判据②
# 踢出分母后，502191×301192（ar 10.6%、ratio 1.667）①②③全不命中成分母；
# 旧版判据④ top1=1611187（断层 4.35<10）→ 502191 逃过 → 66900×42050
# （ratio 1.591，rel 1.86%<10%）与 23780×16820 漏检。修正后 top2 基于
# 剩余候选 → 502191 断层 54 倍被踢 → 两类框恢复条件C。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 1611187, 408405)           # 超宽总框（判据②：ar+ratio>2.5）
add_closed_rect(msp, 229001, 506690, 502191, 301192)  # 二级大框（判据④目标）
add_closed_rect(msp, 457356, 658780, 59450, 42050)    # 页 1（近√2，判据②触发源）
add_closed_rect(msp, 518056, 658780, 59450, 42050)    # 页 2（分离）
add_closed_rect(msp, 287318, 566488, 66900, 42050)    # 独立框（ratio 1.591）
add_closed_rect(msp, 288568, 566988, 65150, 41050)    # 其内框（孤证复核⑥互证）
data = to_bytes(doc)
check('R74判据4修正: 二级大框踢出分母后66900框按C入选, fc=4 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and any(abs(c['width'] - 66900) < 5 for c in r['candidates'])
      and not any(abs(c['width'] - 502191) < 5 for c in r['candidates']))
# ---- 用例 97c：R52 rel 豁免（同图布局空间并存场景） ----
# 模型空间同时存在 √2 网格页（59450×42050 ×4）与 66900×42050 独立大框
# （rel ≥30%）；布局空间有 6 张网格页触发"布局空间优先"→ R52 豁免 rel ≥30%
# 且模型候选 ≥3 的主导框。
doc = make_doc()
msp = doc.modelspace()
for _i in range(2):
    for _j in range(2):
        add_closed_rect(msp, 457356 + _i * 60700, 658780 + _j * 43050,
                        59450, 42050)
add_closed_rect(msp, 287318, 566488, 66900, 42050)
lay1 = doc.layouts.get('Layout1')
for _i in range(3):
    for _j in range(2):
        add_closed_rect(lay1, 40 + _i * 2522, 104 + _j * 1490, 1189, 841)
data = to_bytes(doc)
check('R52豁免: rel>=30%主导框在布局页并存时保留, fc=11 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 11
      and any(abs(c['width'] - 66900) < 5 for c in r['candidates'])
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 59450) < 5) == 4)

check('R52豁免: rel>=30%主导框在布局页并存时保留, fc=11 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 11
      and any(abs(c['width'] - 66900) < 5 for c in r['candidates'])
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 59450) < 5) == 4)

# ---- 用例 97d：R74 判据②ar 门槛取消——细长/未闭合内容线不作 rel 分母 ----
# 场景（20130729地下室 21000×14850 漏检）：A-FLOR-FIRE 层细长防火分区轮廓
#   （ratio 2.833，ar 仅 0.45%）与未闭合 43 顶点边界线（ratio 2.452，w×h
#   3.96e9）先后霸占 rel 分母 → A4 加长页 21000×14850（ratio 1.4141）rel
#   9.946% 差 0.054pp 卡在条件C 门槛下。R74：判据②取消 ar 门槛（ratio>2.5
#   一律踢出分母——真页框长宽比 ≤2.1+余量，ratio>2.5 绝非图框）；
#   未闭合多段线不进候选（collect 仅收闭合），无需额外处理。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 229001, 506690, 502191, 301192)  # 二级大框（判据④踢分母）
add_closed_rect(msp, 457356, 658780, 59450, 42050)    # 页 1（近√2，判据④触发源）
add_closed_rect(msp, 518056, 658780, 59450, 42050)    # 页 2（分离）
add_closed_rect(msp, 457356, 744502, 21000, 14850)    # A4 加长页（21000×14850）
# 细长防火分区轮廓（闭合，ratio 2.833，ar 0.45%——旧判据②ar≥15% 挡不住）
add_closed_rect(msp, 124952, 48028, 51566, 146102)
data = to_bytes(doc)
check('R74判据2收紧: 细长轮廓踢出分母后A4加长页按C入选, fc=3 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and any(abs(c['width'] - 21000) < 5 for c in r['candidates'])
      and not any(abs(c['width'] - 502191) < 5 for c in r['candidates']))

check('R74判据2收紧: 细长轮廓踢出分母后A4加长页按C入选, fc=3 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and any(abs(c['width'] - 21000) < 5 for c in r['candidates'])
      and not any(abs(c['width'] - 502191) < 5 for c in r['candidates']))

# ---- 用例 100：R75 布局空间超纸面候选不作 rel 分母（配电房布置-7.11 场景） ----
# 布局空间是纸面坐标系（真页框长边 ≤ A0 加长 1486mm），TK 轴网线/内容框
# （7000×17604、7016×6014）把分母撑爆 → 841×594 布局页 rel 1.184% 卡在
# 条件B 的 10% 门槛下漏检。R75：布局空间候选长边 >2500mm → 不作 rel 分母。
doc = make_doc()
msp = doc.modelspace()
lay1 = doc.layouts.get('Layout1')
# TK 竖长轴网线（闭合，长边 17604）
add_closed_rect(lay1, -3337313, -5502682, 7000, 17604)
# TK 内容框（7016×6014——分母真凶）
add_closed_rect(lay1, 200, 500, 7016, 6014)
# 双层框两页（841×594 外框 + 806×574 内框，TEL_TITLE 纸面尺寸）
add_closed_rect(lay1, 2404, 775, 841, 594)
add_closed_rect(lay1, 2429, 785, 806, 574)
add_closed_rect(lay1, 2404, 87, 841, 594)
add_closed_rect(lay1, 2429, 97, 806, 574)
data = to_bytes(doc)
check('R75布局分母: 超纸面候选不作分母, 841x594按B入选, fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 841) < 5) == 2
      and not any(c['width'] > 3000 for c in r['candidates']))

check('R75布局分母: 超纸面候选不作分母, 841x594按B入选, fc=2 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 841) < 5) == 2
      and not any(c['width'] > 3000 for c in r['candidates']))

# ---- 用例 101：R76 R67 同尺寸容差 5%→1%（火警系统.dwg 场景） ----
# 84100×59400 页内画系统图表格：分格 27657×18687（左）+ 27115×18680（右）并排
# + 27657×19599（下）——分格彼此差 2~4.9%，旧 R67 容差 5% 把它们聚成"同尺寸
# 并排独立页"→ 3 张标准页（84100×59400×2 + 内框 80600×57400）被误剔、6 张
# 分格漏检成图框。R76：R67 独立双页"同尺寸"容差收 1%（同模板页差 ≤0.5%），
# 分格自然拆组（各组 1 份 <2 不触发）→ 嵌套去重收分格、标准页保留。
doc = make_doc()
msp = doc.modelspace()
# 页 A（84100×59400 外框 + 80600×57400 内框）+ 表格分格 3 个
add_closed_rect(msp, 0, 0, 84100, 59400)
add_closed_rect(msp, 2500, 1000, 80600, 57400)
add_line_rect(msp, 11453, 26878, 27657, 18687)        # 分格左上（#2）
add_line_rect(msp, 44343, 26878, 27115, 18680)        # 分格右上（#3）
add_line_rect(msp, 11453, 3138, 27657, 19599)         # 分格下（#4）
# 页 B（同构）
add_closed_rect(msp, 0, -64148, 84100, 59400)
add_closed_rect(msp, 2500, -63148, 80600, 57400)
add_line_rect(msp, 11453, -37270, 27657, 18687)       # 分格左上（#5）
add_line_rect(msp, 44343, -37278, 27115, 18680)       # 分格右上（#6）
add_line_rect(msp, 11453, -60010, 27657, 19599)       # 分格下（#7）
# 独立 A1 页（59400×42000）
add_closed_rect(msp, -560449, -3096030, 59400, 42000)
data = to_bytes(doc)
check('R76容差收紧: 表格分格不触发独立双页, 标准页保留 fc=3 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 84100) < 5) == 2
      and any(abs(c['width'] - 59400) < 5 for c in r['candidates'])
      and not any(c['width'] < 30000 for c in r['candidates']))

# ---- 用例 102：R77b/c/d 内容外框与比例空窗（g013~g016 场景缩微） ----
# 模型空间：205000×120000 修改范围线（S-修改）内含 118900×84100 √2 页框
# （TKA0/S-BORDER）+ 4 张结构基坑轮廓（117348/116423×3/113796，ratio
# 1.933~1.984 落真页空窗 1.822~2.058）。R77b 剔外框（内含 √2 页 40.7%）、
# R77c 剔空窗 4 张、R77d 兜底嵌套反转 → 只留 118900。
doc = make_doc()
msp = doc.modelspace()
doc.blocks.new('TKA0')
_blk = doc.blocks.get('TKA0')
add_closed_rect(_blk, 0, 0, 118900, 84100)
add_closed_rect(_blk, 2500, 1000, 115400, 81600)
msp.add_blockref('TKA0', (28295798, 21832268),
                   dxfattribs={'layer': 'S-BORDER'})
add_closed_rect(msp, 28289698, 21822268, 205000, 120000)
add_closed_rect(msp, 28612566, 21498058, 117348, 228659)
add_closed_rect(msp, 28636392, 21121524, 116423, 225000)
add_closed_rect(msp, 28877988, 21499355, 116423, 225000)
add_closed_rect(msp, 28900340, 21121224, 116423, 225000)
add_closed_rect(msp, 29132129, 21992748, 113796, 225772)
data = to_bytes(doc)
check('R77内容外框+比例空窗: 结构轮廓剔除只留√2页, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['candidates'][0]['width'] - 118900) < 5)


print(f'\n结果: {sum(results)} 通过, {len(results) - sum(results)} 失败')
# ---- 用例 103：R79 实物尺度详图空间 rel 分母切换（井道大样 场景缩微） ----
# 模型空间是 1:1 实物详图（内容总面积 50m²：100000×50000 画一幅 5000×3000
# 管井剖面图案外框 + 2 个 800×1200 井道轮廓 + 散线撑大范围），零标题栏证据
# ——旧逻辑 rel 分母=最大候选（5000×3000）→ 井道轮廓 rel 6.4% 被撑过门槛？
# 否——分母塌缩场景：最大候选=图案自身时 rel 自举虚高。R79：4~100m² 且零
# 标题证据 → 分母改用内容总面积，图案 rel 掉到 0.1~0.7% 被条件C 拒。
# 对照组：同样构造但加 TEL_TITLE 层实体（有标题栏证据）→ 保持原分母。
doc = make_doc()
msp = doc.modelspace()
add_closed_rect(msp, 0, 0, 100000, 50000)      # 大范围散框撑出 5m² 外框？不——这是外包络候选（面积50%豁免不适用分母逻辑）
add_closed_rect(msp, 100000, 0, 2000, 1200)    # "井道"图案 1（右侧独立）
add_closed_rect(msp, 102500, 0, 800, 1200)     # "井道"图案 2
data = to_bytes(doc)
check('R79实物尺度: 零标题证据详图空间图案按内容总面积分母拒之, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['candidates'][0]['width'] - 100000) < 5)


# ---- 用例 104：R80 R62 保护条件⑤——蹭保护的小设备轮廓块（水箱间电梯间 场景） ----
# 平面图内 2 个 3450×5290 电梯井轮廓块（块定义比例=候选比例、与外框比例差>10%、
# 外框面积≥4×——R62 前四条件全中）蹭"单页图框块"保护致嵌套去重失效误检。
# R80 条件⑤：块定义长边 ≥ 外框长边×10% 才保护（电梯井 5290/189150=2.8% 拒；
# R62 原场景 134300/388331=34.6%、用例82 30% 不受影响）。
doc = make_doc()
msp = doc.modelspace()
_blk_sh = doc.blocks.new(name='TMP_SHAFT')
add_closed_rect(_blk_sh, 0, 0, 3450, 5290)
add_closed_rect(_blk_sh, 200, 200, 3050, 4890)
msp.add_blockref('TMP_SHAFT', (440112, 41045))
# 第二个井道尺寸稍异——避免"同尺寸≥2"触发包裹框剔除反吃真框
_blk_sh2 = doc.blocks.new(name='TMP_SHAFT2')
add_closed_rect(_blk_sh2, 0, 0, 3450, 5150)
add_closed_rect(_blk_sh2, 200, 200, 3050, 4750)
msp.add_blockref('TMP_SHAFT2', (458611, 41045))
add_closed_rect(msp, 350000, 5000, 189150, 89100)   # 真框（大平面）
data = to_bytes(doc)
check('R80保护条件5: 电梯井轮廓块不享R62保护被嵌套收掉, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['candidates'][0]['width'] - 189150) < 5)


# ---- 用例 105：R82 标题栏层页框（暖通（防排烟）_t3 场景缩微） ----
# 天正 t3 图框装配块：9 个图框块（TEL_TITLE 层 189150×89100）水平一行排列
# → 命中"对齐排列装饰阵列"启发式；ratio 2.12 加长横版不在 √2±10% 白名单。
# R82d：块内含标题栏层页框 → 保护不对齐剔除。另有 1 个整页装配块（bbox
# 249386×151370 含溢出内容，真框 189150×89100 在 TEL_TITLE 层占 45%）——
# R82b：标题栏层页框 < 块 bbox → 以页框尺寸替换（否则 249×151 撑大 rel 分母
# 把 9 个图框块的 rel 压到 8.9% 被条件C 拒）。
def _add_title_rect(layout, x, y, w, h):
    layout.add_lwpolyline(
        [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
        close=True, dxfattribs={'layer': 'TEL_TITLE'})

doc = make_doc()
msp = doc.modelspace()
doc.layers.new('TEL_TITLE')
# 页 1：直绘标题栏层页框
_add_title_rect(msp, 0, 500000, 189150, 89100)
# 图框装配块 *U21 形态：9 实例水平一行（y 同、x 每 189150 一格）
doc.blocks.new('TMP_FB')
_blk_fb = doc.blocks.get('TMP_FB')
_add_title_rect(_blk_fb, 0, 0, 189150, 89100)
for _k in range(9):
    msp.add_blockref('TMP_FB', (_k * 189150, 0))
# 整页装配块（含溢出内容撑大 bbox + 标题栏层真框）
doc.blocks.new('TMP_ASM')
_blk_asm = doc.blocks.get('TMP_ASM')
add_closed_rect(_blk_asm, 0, 0, 249386, 151370)   # 溢出内容（0 层）
_add_title_rect(_blk_asm, 0, 0, 189150, 89100)     # 真页框（TEL_TITLE）
msp.add_blockref('TMP_ASM', (2000000, 0))
data = to_bytes(doc)
check('R82标题栏层页框: 9图框块水平排列不误删+装配块按页框替换, fc=11 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 11
      and all(abs(c['width'] - 189150) < 5 and abs(c['height'] - 89100) < 5
              for c in r['candidates']))



# ---- 用例 98：R72 跨框重叠夹缝框剔除（地下室柱平面布置0816 场景） ----
# 三组"上下双大框"排版（上下真框排版重叠 5.8%），组内拼接缝上各有一个竖长
# 闭合多段线罩在两框之间——与每框交集占对方 61~72%、占自己 51%。R72：
# 候选的重叠伙伴中存在 ≥2 个"彼此可忽略重叠"（交集 < 双方 ×15%）的成员
# → 罩框剔除；真框的重叠伙伴只有夹缝框 1 个 → 不剔。
doc = make_doc()
msp = doc.modelspace()
for _j, _y in enumerate((0, 130000)):                 # 左组上下双框（重叠 4370≈3.5%）
    add_closed_rect(msp, 0, _y, 173100, 123150)
add_closed_rect(msp, 8000, 20000, 117348, 228659)     # 夹缝框（罩两框）
for _j, _y in enumerate((0, 130000)):                 # 右组上下双框
    add_closed_rect(msp, 200000, _y, 178350, 126150)
add_closed_rect(msp, 208000, 20000, 129555, 241435)   # 夹缝框
data = to_bytes(doc)
check('R72跨框重叠: 拼接缝夹缝框剔除, fc=4 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and not any(abs(c['width'] - 117348) < 5 or abs(c['width'] - 129555) < 5
                  for c in r['candidates']))

# 开关回退对照：夹缝框幸存（fc=6，夹缝框与双框互为重叠对）
check('R72开关回退: NO_OVERLAP_EVICT=1 时夹缝框幸存, fc=6 (smart)',
      lambda: _parse_env(data, FRAME_PARSER_NO_OVERLAP_EVICT='1',
                         FRAME_PARSER_NO_STD_RATIO='1'),
      lambda r: r['frame_count'] == 6)

# ---- 用例 99：R73 条件H3 同比例家族互证（P_9#给排水平面图 场景） ----
# 巨块内 29720×16820（ratio 1.7683 A3 加长比例）经块内救援提升后全通道否决：
# F 只认 √2±2%；H 主导比例需同簇已入选 ≥3 份，同簇 105100×59400 只有 2 份
# → 差 1 票。R73：块内闭合线候选与同 layout 已入选 ratio ±1% 成员 ≥2 个
# （rel ≥0.1%）→ block_family 救援。
doc = make_doc()
msp = doc.modelspace()
doc.blocks.new('BLK_G')
_blk = doc.blocks.get('BLK_G')
# 容器长条（R27 拒形态：3 条 LINE 开口围出，无闭合边框证据）
msp2 = None
for _ln_pts in (((0, 0), (752479, 0)), ((752479, 0), (752479, 242321)),
                ((0, 0), (0, 242321))):
    _blk.add_line(_ln_pts[0], _ln_pts[1])
# 块内 2 份 29720×16820 + 2 份 42050×29700（≥2 个尺寸组 → 块内救援触发）
add_closed_rect(_blk, 500000, 30000, 29720, 16820)
add_closed_rect(_blk, 500000, 60000, 29720, 16820)
add_closed_rect(_blk, 200000, 30000, 42050, 29700)
add_closed_rect(_blk, 200000, 80000, 42050, 29700)
msp.add_blockref('BLK_G', (0, -250000))
msp.add_line((3000000, 3000000), (3100000, 3000000))  # 离群实体压容器 area_ratio
# 顶层 2 张 105100×59400（ratio 1.7694 同簇，rel=100% 互为分母走 C）
add_closed_rect(msp, 0, 0, 105100, 59400)
add_closed_rect(msp, 110000, 0, 105100, 59400)
data = to_bytes(doc)
check('R73同比例家族: 块内A3加长框按家族互证救回, fc=6 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 6
      and sum(1 for c in r['candidates']
              if abs(c['width'] - 29720) < 5) == 2
      and not any(abs(c['width'] - 752479) < 5 for c in r['candidates']))

# 开关回退对照：关掉 H3 → 块内 A3 加长框全灭（105100×2 + 42050×2 幸存）
check('R73开关回退: NO_COND_H3=1 时块内加长框全灭, fc=4 (smart)',
      lambda: _parse_env(data, FRAME_PARSER_NO_COND_H3='1'),
      lambda r: r['frame_count'] == 4
      and not any(abs(c['width'] - 29720) < 5 for c in r['candidates']))


# ---- 用例 106：R83 双线画法闭合多段线图框被同心空壳大框包住（n036_t8 场景） ----
# 真图框 79968×59400（LEVEL_1 层闭合多段线，首尾坐标重合但 closed=False——
# 修复 is_polyline_closed 漏判"几何闭合"）+ 内框 77168×58000（同层同心闭合，
# 面积比 94.2% 双线画法）。外层 173232×85241（0 层空壳大框，ratio 2.03 落在
# 真页空窗 1.822~2.058，但图内无 √2 标准页故 R77c 不启用）同心全包真图框，
# 框内除图框外全空 → 抢 rel 分母（真框 rel 32%）+ 去重"留大剔小"。
# 修复：① is_polyline_closed 认几何闭合 ② 孤证评分⑧双线内框互证 ③ 去重阶段
# 带双线内框的真图框免被非块参照空壳外框剔小（留小剔大）。
doc = make_doc()
msp = doc.modelspace()
doc.layers.new('LEVEL_1')
# 真图框外框：5 顶点首尾重合但 close=False（复刻 n036 的 79968×59400）
msp.add_lwpolyline(
    [(200464, -234798), (280432, -234798), (280432, -175398),
     (200464, -175398), (200464, -234798)],
    close=False, dxfattribs={'layer': 'LEVEL_1'})
# 真图框内框：闭合双线（77168×58000，面积比 94.2%）
add_closed_rect(msp, 202564, -234098, 77168, 58000)
for _e in msp:
    if _e.dxftype() == 'LWPOLYLINE' and _e.dxf.layer == '0':
        _e.dxf.layer = 'LEVEL_1'  # 内框也归 LEVEL_1（同层双线才互证）
# 外层空壳大框（173232×85241，0 层）
add_closed_rect(msp, 154640, -245136, 173232, 85241)
# 远距离散实体撑大 msp 总 bbox（使 173232 area_ratio <5% 进孤证复核、真框 area_ratio<5%）
add_line_rect(msp, 1000000, -1000000, 3000, 2000)
add_line_rect(msp, 3500000, -5500000, 3000, 2000)
data = to_bytes(doc)
check('R83双线闭合多段线: 空壳大框同心包真图框留小剔大, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['width'] - 79968) < 5 and abs(r['height'] - 59400) < 5
      and not any(abs(c['width'] - 173232) < 5 for c in r['candidates']))


# ---- 用例 107：R84 判据④「页级尺寸」护栏（总平面定位及竖向设计图 场景） ----
# 纸面比例模式（msp_total ≈ 743,150mm² < 4m²）：真图框 892×631（块参照，ratio
# 1.4138 精确√2，area_ratio 75.7%）。框内有 4 份 7.8×12 建筑构件符号（00-建筑
# 层，ratio 1.538 落 √2±10%）位置分离 + 图签表格格子（49×24 / 52×27 /
# 32×22×2 / 27×13，实测各带双线内框 → 孤证⑧互证存活）。
# 旧判据④ 把 7.8×12 当成"独立图纸页"→ 判真图框为巨型包络踢出 rel 分母 →
# 分母塌缩到格子面积（1409）→ 格子 rel 暴涨 25~100% 全过条件C → 真图框反因
# "内含 4 个独立尺寸组"被包裹框剔除 → 输出小格子。
# 修复：被罩候选短边须 ≥ 外框短边×5%（真页占包络短边通常 ≥15%，用例97 实测
# 15.6%；构件符号/表格格子相对图框仅 1~4%，本图最大 52×27 → 4.3%）→ 判据④
# 不触发，分母保持真图框面积，格子 rel 回落 <10% 被条件C 拒。
doc = make_doc()
msp = doc.modelspace()
# 真图框块（892×631，纸面 mm）——块参照插入（复刻「图框做进块定义」画法）
doc.blocks.new('ZPM_FRAME')
_blk_zpm = doc.blocks.get('ZPM_FRAME')
_blk_zpm.add_lwpolyline([(0, 0), (892, 0), (892, 631), (0, 631)], close=True)
msp.add_blockref('ZPM_FRAME', (0, 0))
# 框内 4 份 7.8×12 构件符号（ratio 1.538 落 √2±10%，位置分离）
for _px, _py in ((100, 100), (300, 100), (500, 100), (700, 100)):
    add_closed_rect(msp, _px, _py, 7.8, 12.0)
# 图签格子 4 组，每组外框+内框双线（孤证⑧互证 → 不被孤证剔除；4 个独立尺寸组
# 使真图框的「块参照包裹剔除豁免」失效 → 旧口径下真图框被剔）
add_closed_rect(msp, 640, 480, 49, 24);  add_closed_rect(msp, 641, 481, 48, 23)
add_closed_rect(msp, 640, 450, 52, 27);  add_closed_rect(msp, 641, 451, 51, 26)
add_closed_rect(msp, 600, 420, 32, 22);  add_closed_rect(msp, 601, 421, 31, 21)
add_closed_rect(msp, 560, 420, 32, 22);  add_closed_rect(msp, 561, 421, 31, 21)
add_closed_rect(msp, 640, 400, 27, 13);  add_closed_rect(msp, 641, 401, 26, 12)
# 框内内容线（撑住 layout 总 bbox ≈ 纸面，使 msp_total <4m² 走纸面比例模式）
for _k in range(12):
    msp.add_line((50 + _k * 20, 50), (50 + _k * 20, 600))
data = to_bytes(doc)
check('R84页级尺寸护栏: 构件符号不算独立页, 真图框不被判巨型包络, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['width'] - 892) < 5 and abs(r['height'] - 631) < 5)

# 开关回退对照：关掉页级尺寸护栏 → 7.8×12 构件符号被当独立页 → 真图框判巨型
# 包络踢出分母 → 分母塌缩到格子面积 → 格子 rel 暴涨过条件C → 真图框反被包裹框
# 剔除、图签格子当选（复现原 bug）
check('R84开关回退: NO_GIANT_PAGE_MIN=1 时真图框被判包络丢失, 图签格子当选',
      lambda: _parse_env(data, FRAME_PARSER_NO_GIANT_PAGE_MIN='1'),
      lambda r: r['frame_count'] > 1
      and not any(abs(c['width'] - 892) < 5 for c in r['candidates'])
      and any(abs(c['width'] - 49) < 2 for c in r['candidates']))


# ---- 用例 108：R86 块内页框递归下探（暖通（防排烟）场景缩微） ----
# 真图框 189150×89100 全部由块参照提供，其中一页的图框藏在**两层嵌套**里：
# 平面图内容块 002（3010 实体，块 bbox 312492×154326 含墙外溢出内容）→
# INSERT *U22 → TEL_TITLE 层 189150×89100 页框。
# 旧 _scan_title_layer_page_rects 只扫块定义**顶层** → 002 扫不到页框 →
# R82b 尺寸替换不生效 → 002 以块 bbox（312492×154326）被当图框输出。
# 修复：递归下探子块（限深 3），子块页框经 insert + R(rot)·S·(p−base) 变换
# 转到父块坐标系。全部 11 页应输出 189150×89100。
_W, _H, _STEP = 189150, 89100, 189150


def _add_title_page(layout, x, y):
    """在图框层画 189150×89100 页框 + 内框（双线画法）。"""
    layout.add_lwpolyline(
        [(x, y), (x + _W, y), (x + _W, y + _H), (x, y + _H)],
        close=True, dxfattribs={'layer': 'TEL_TITLE'})
    layout.add_lwpolyline(
        [(x + 2100, y + 1500), (x + _W - 3150, y + 1500),
         (x + _W - 3150, y + _H - 1500), (x + 2100, y + _H - 1500)],
        close=True, dxfattribs={'layer': 'TEL_TITLE'})


doc = make_doc()
msp = doc.modelspace()
doc.layers.new('TEL_TITLE')
# 图框块 *U22：块内页框放在 (365098,-2790)（偏离原点，复刻真实块的排版位置）
doc.blocks.new('U22_FRAME')
_blk_u22 = doc.blocks.get('U22_FRAME')
_blk_u22.add_lwpolyline(
    [(365098, -2790), (365098 + _W, -2790), (365098 + _W, -2790 + _H),
     (365098, -2790 + _H)], close=True, dxfattribs={'layer': 'TEL_TITLE'})
_blk_u22.add_lwpolyline(
    [(365098 + 2100, -2790 + 1500), (365098 + _W - 3150, -2790 + 1500),
     (365098 + _W - 3150, -2790 + _H - 1500), (365098 + 2100, -2790 + _H - 1500)],
    close=True, dxfattribs={'layer': 'TEL_TITLE'})
# 平面图内容块 002：块内嵌 *U22（页框在子块里），本体只有溢出内容线，无页框
doc.blocks.new('PLAN_002')
_blk_p = doc.blocks.get('PLAN_002')
for _k in range(20):                       # 平面图墙线（不含图框几何）
    _blk_p.add_line((_k * 9000, 0), (_k * 9000, 90000))
_blk_p.add_line((0, 150000), (189150, 150000))   # 溢出内容（撑大块 bbox 到 154326）
_blk_p.add_blockref('U22_FRAME', (-365098, -86310))
# 顶层：10 页直插 *U22 + 1 页用 002 内容块（页框在嵌套子块）
for _k in range(11):
    if _k == 0:
        msp.add_blockref('PLAN_002', (0, 89100))       # 002 占第 1 页位置
    else:
        msp.add_blockref('U22_FRAME', ((_k - 1) * _STEP + 13202, 2790))
data = to_bytes(doc)
check('R86块内页框递归: 嵌套子块的TEL_TITLE页框替换块bbox, 11页全189150x89100 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 11
      and all(abs(c['width'] - 189150) < 5 and abs(c['height'] - 89100) < 5
              for c in r['candidates']))


# ---- 用例 109：R87 包裹框内补捞放开块参照插入（暖通 1~12F 场景缩微） ----
# 场景结构（复刻真实图的决定性环节）：
#   包络 P（250000×200000，ratio 1.25 偏离 √2 11.6%）罩住 2 个不同尺寸的
#   闭合多段线页框 A(105000×49000) / B(120000×56000) → 独立尺寸组 2 → 包络剔除。
#   包络外另有页框 C(60000×28000)，它与包络内的**块参照**页框 D 同尺寸。
#   A/B 靠条件C 进 frame_like（rel 10.3%/13.4%）；C 的 rel 只有 3.4% 过不了 C，
#   走条件I 表格页救援（框内画横竖表格线）——与真实图 12 页靠条件I 同路径。
#   D 是块参照：rel 同样是 3.4% 过不了 C，条件I 又被「未被已入选框完全包住」
#   护栏挡住（此刻包络 P 还在 frame_like 里，包裹剔除发生在条件I 之后）→
#   唯一出路是包裹剔除后的 R63 补捞。旧代码 R63 只认 EXPLICIT_TYPES
#   （闭合多段线/直线矩形），块参照插入被类型门槛过滤 → 漏检。
# R87 修复：R63 补捞放开块参照插入，但要求块定义内有矩形闭合边框证据
#   （block_rect_len ≥ 块定义长边×50%，与 R58 条件C 块参照通道同口径）。
# 对照组：把 D 的块内闭合边框换成"只有散线、无闭合矩形"的内容块形态 →
#   无边框证据 → 不得被捞回（fc 少 1）。
_W87 = None


def _build_r87(with_closed_border=True):
    """构造 R87 缩微场景；with_closed_border=False 时块内无闭合边框（对照组）"""
    d = make_doc()
    lay = d.modelspace()

    def rect(x, y, w, h):
        lay.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)

    def table(x, y, w, h):
        """框内画表格线：≥30 横 + ≥6 竖，满足条件I 门槛"""
        for k in range(1, 32):
            yy = y + h * k / 32.0
            lay.add_line((x + 100, yy), (x + w - 100, yy))
        for k in range(1, 8):
            xx = x + w * k / 8.0
            lay.add_line((xx, y + 100), (xx, y + h - 100))

    # 包络 P（分母）：ratio 1.25，偏离 √2 >10% 不受包裹剔除的 √2 保护
    rect(0, 0, 250000, 200000)
    # 包络内 2 个不同尺寸页框（条件C：rel 10.3% / 13.4%）
    rect(20000, 20000, 105000, 49000)
    rect(130000, 20000, 120000, 56000)
    # 包络外的页框 C（rel 3.4% 过不了 C，靠条件I 救援）
    cx1, cy1, cw, chh = 0, -80000, 60000, 28000
    rect(cx1, cy1, cw, chh)
    table(cx1, cy1, cw, chh)

    # 包络内的块参照页框 D（与 C 同尺寸）
    if with_closed_border:
        d.blocks.new('PAGE_D')
        bd = d.blocks.get('PAGE_D')
        bd.add_lwpolyline([(0, 0), (cw, 0), (cw, chh), (0, chh)], close=True)
        for k in range(1, 6):     # 标题栏分隔线
            bd.add_line((0, chh * k / 6.0), (cw * 0.3, chh * k / 6.0))
        lay.add_blockref('PAGE_D', (20000, 100000))
    else:
        # 对照组：块内有闭合环但**不是矩形**（L 形楼板环形态）——R27 闭合证据
        # 能过（闭合环长边=块长边），但 R58/R87 的「矩形闭合边框」口径不认，
        # block_rect_len < 块长边×50% → 不得被补捞。
        d.blocks.new('PLAN_D')
        bd = d.blocks.get('PLAN_D')
        bd.add_lwpolyline(
            [(0, 0), (cw, 0), (cw, chh * 0.45), (cw * 0.45, chh * 0.45),
             (cw * 0.45, chh), (0, chh)],
            close=True)
        for k in range(1, 6):
            bd.add_line((0, chh * k / 6.0), (cw * 0.3, chh * k / 6.0))
        lay.add_blockref('PLAN_D', (20000, 100000))
    return to_bytes(d)


data = _build_r87(True)
check('R87包裹框内补捞: 块参照页框(有闭合边框证据)被 R63 救回, 4 页全出 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and sorted(round(c['width']) for c in r['candidates']) == [60000, 60000, 105000, 120000])

data = _build_r87(False)
check('R87对照: 包络内块参照无闭合边框证据(内容块)不被补捞, 只 3 页 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and sorted(round(c['width']) for c in r['candidates']) == [60000, 105000, 120000])


# ---- 用例 110：R88 判据⑤ 多页包络不作 rel 分母（暖通 1~12F 场景缩微） ----
# 面积关系复刻真实图：
#   A 系 1/2 加长页 64000×30170（ratio 2.12131 ≈ √2×1.5=2.12132，偏差 0.0004%）
#   共 3 页，其中 2 页并排落在包络 P（230000×145000，ratio 1.5862）内、1 页在外。
#   包络既非 √2（偏离 12.16% >10%）也非任何 √2×加长倍率（最近的是 √2×1.25，
#   差 10.27%）→ 判据⑤ 的护栏①「容器自身不得是页面形状」不会挡它。
#   包络 area_ratio 59.55%（走条件A 入选）、每页 area_ratio 仅 3.45%（条件A 灭），
#   每页 rel = 页/包络 = 5.79% < 10%（条件C 灭）→ 页框全部漏检、只输出包络。
# R88 判据⑤：包络内含 ≥2 个「同尺寸且位置分离」页面级候选（ratio 命中
#   √2×1.5±1%、面积 ≥ 包络 5%、bbox 分离）→ 不作 rel 分母 → 分母回落到单页
#   → 3 页 rel=100% 全靠条件C 入选，包络自己由包裹剔除（独立双页判据）收掉。
# 对照组：同一几何，页框比例改成 1.90（非 √2、非 √2×倍率）→ 判据⑤ 不认这些
#   是"页" → 包络继续当分母 → 页框漏检，只输出包络。
_W110 = 64000, 30170        # ratio = 2.12131 ≈ √2×1.5
_ENV110 = 230000, 145000    # 包络（ratio 1.5862，非页面比例）


def _build_r88(page_ratio_ok=True):
    d = make_doc()
    lay = d.modelspace()
    pw, ph = _W110
    if not page_ratio_ok:
        ph = round(pw / 1.90)          # 非页面比例（对照组）
    ew, eh = _ENV110

    def rect(x, y, w, h):
        lay.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)

    rect(0, 0, ew, eh)                            # 包络
    # 包络内 2 页并排（x 方向分离）
    rect(10000, 20000, pw, ph)
    rect(80000, 20000, pw, ph)
    # 包络外 1 页
    rect(0, eh + 20000, pw, ph)
    # 远处散线：把 layout 总面积撑到 5.6e10（约包络的 1.68 倍），
    # 使包络 area_ratio 59.55%、页 area_ratio 3.45%——与真图同量级
    lay.add_line((0, 0), (280000, 200000))
    return to_bytes(d)


data = _build_r88(True)
check('R88判据⑤: 多页包络不作rel分母, 3 页全出且全走条件C(无救援标记) (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 3
      and all(c.get('rescued_by') is None for c in r['candidates'])
      and all(abs(c['width'] - _W110[0]) < 5 for c in r['candidates']))

data = _build_r88(False)
check('R88判据⑤对照: 内框为非页面比例(1.90)时不踢包络分母, 只 1 个包络 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and abs(r['width'] - _ENV110[0]) < 5)


# ---- 用例 111：R89 对齐白名单补 √2×加长倍率（汽配城 A2栋A3栋水施 漏 6 张） ----
# 场景：真图框块 gh5 尺寸 74300×42000（ratio 1.7690 = √2×1.25 = A2 1/4 加长），
#   6 个实例分两组、每组 3 个 x 完全同列（x_std=0）y 等距 42000 排列 →
#   命中"垂直对齐装饰块"启发式整组删除。
#   原白名单只认 A 系列（短边 42000 不在 {841..105}）与 √2±10%
#   （1.769 偏离 √2 达 25%），两处都够不着 → 6 张全漏。
# R89：白名单比例判定改用 is_page_ratio()（含 √2×k, k=1.25/1.5/1.75, ±1%）。
# 对照：同样 3 个同列等距排列，但尺寸比例改成非页面比例（1.90）→
#   不被白名单保护 → 仍按装饰阵列剔除。
_W111 = 74300, 42000        # ratio 1.7690 = √2×1.25


def _build_r89(page_ratio_ok=True):
    d = make_doc()
    lay = d.modelspace()
    w, h = _W111
    if not page_ratio_ok:
        h = round(w / 1.90)          # 非页面比例（对照组）
    d.blocks.new('GH5_FRAME')
    bd = d.blocks.get('GH5_FRAME')
    bd.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)
    bd.add_lwpolyline([(1500, 1500), (w - 1500, 1500),
                       (w - 1500, h - 1500), (1500, h - 1500)], close=True)
    # 6 个实例：两组、每组 3 个 x 几乎同列、y 等距（复刻真实图"垂直对齐"命中形态）。
    #   两组 x 只差 4348（真实图 gh5 两组 x=406406 / 410754 的实测差值）——
    #   若把两组拉开很远，x 标准差会变大，反而躲过"垂直对齐"判据。
    for gx in (0, 4348):
        for k in range(3):
            lay.add_blockref('GH5_FRAME', (gx, k * h))
    return to_bytes(d)


data = _build_r89(True)
check('R89对齐白名单: √2×1.25 加长图框块(74300x42000)6实例垂直排列不被当装饰块剔, fc=6 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 6
      and all(abs(c['width'] - _W111[0]) < 5 for c in r['candidates']))

data = _build_r89(False)
check('R89对齐白名单对照: 非页面比例(1.90)的同列排列块仍按装饰阵列剔除 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 0
      or not any(abs(c['width'] - _W111[0]) < 5 for c in r['candidates']))


# ---- 用例 112：R89 块内页框须占块定义面积 ≥40%（汽配城 ga/lo 构件轮廓误报） ----
# 场景：建筑平面块 ga（块定义 56753×115866）内含屋面轮廓 42600×17400（ROOF 层）、
#   楼梯间轮廓 47200×22000（STAIR 层）等 5 个互不嵌套大矩形 —— 它们是建筑
#   构件，却被 R60 多页拼版块拆分当"拼版页"逐份提升为候选，最终 4 张
#   （47200×22000 / 42600×17400 及 lo 块的对应项）进了结果（用户确认误报）。
#   构件轮廓只占块定义面积 11.3%~15.8%，而真拼版块每页占 47.4%~100%。
# R89：页框面积 ≥ 块定义 bbox 面积 × 40% 才认作"块内页框"。
# 正向对照：用例 79（R60）的 TMP_2PAGE 每页占 47.4%，仍须正常拆分 → 已由
#   既有用例守护；此处的对照组把构件轮廓做小到 15%，验证被正确拒绝。
def _build_r89_block_pages(page_ratio):
    """块定义做大（页框只占小块），模拟 ga 块内构件轮廓。
    page_ratio: 页框面积 / 块定义面积"""
    d = make_doc()
    lay = d.modelspace()
    # 块定义 115866×56753（与 ga 同量级）
    BW, BH = 115866, 56753
    d.blocks.new('PLAN_BLK')
    bd = d.blocks.get('PLAN_BLK')
    # 块外轮廓（撑出块定义 bbox）
    bd.add_lwpolyline([(0, 0), (BW, 0), (BW, BH), (0, BH)], close=True)
    # 块内 2 个互不嵌套的"构件轮廓"（ROOF 层），尺寸各占 page_ratio
    tw = round(BW * 0.35)
    th = round(BH * (page_ratio / 0.35))
    bd.add_lwpolyline([(1000, 1000), (1000 + tw, 1000),
                       (1000 + tw, 1000 + th), (1000, 1000 + th)], close=True)
    bd.add_lwpolyline([(1000, 1000 + th + 500), (1000 + tw, 1000 + th + 500),
                       (1000 + tw, 1000 + 2 * th + 500), (1000, 1000 + 2 * th + 500)],
                      close=True)
    lay.add_blockref('PLAN_BLK', (0, 0))
    return to_bytes(d)


data = _build_r89_block_pages(0.13)   # 构件轮廓仅占块 13%（ga 实测 11.3~15.8%）
check('R89块内页框面积护栏: 仅占块定义13%的构件轮廓不被当拼版页提升, 无该尺寸候选 (smart)',
      lambda: parse(data),
      lambda r: not any(abs(c['width'] - round(115866 * 0.35)) < 50
                        for c in r['candidates']))


# ---- 用例 113：R90 「布局空间优先」房间量级不豁免（雅安平面参照 101→4） ----
# 场景复刻（140822雅安综合艺术楼平面参照_t7.dwg）：
#   布局"布局1"排出 4 张 841×594 真页 → 触发 R52「布局空间优先」；但模型空间
#   那 97 个候选全是**房间轮廓**（8100×5600×40、5400×8100×36…，绝对尺寸
#   1~10m），它们同时占满 R52 的两个豁免口子：
#     · 房间比例恰好贴近 √2（8100/5600=1.4464 偏 2.3%）→ 命中 √2 豁免；
#     · 模型空间里没有大真框，rel 分母取的就是房间自己 → 每个房间 rel
#       高达 42%~71% → 命中 rel≥25% 豁免。
#   此后条件C 的 10% 门槛也形同虚设（分母与候选同类）。
# R90：短边 < ROOM_SCALE_MAX_SIDE(10000mm) 的候选视为真实房间/构件尺寸，
#   不享受任何豁免。门槛依据全回归库 14 张「布局有真页且模型有候选」的图实测
#   的短边最小值空隙：本图 3450~5750 vs 罗马都市 14813 / 大样图 16820 /
#   其余 29700+，10000 落在空隙中间。
# 对照组：模型空间放 1 张真实图框量级的 59400×42050（短边 42050 ≥ 10000）
#   → 仍享受 √2 豁免、必须保留（证明这不是"布局有页就清空模型"的一刀切）。
def _build_r90(model_kind):
    d = make_doc()
    # 布局空间 2×2 共 4 张 A1 真页（841×594）
    blk = d.blocks.new('PAGE_A1')
    add_closed_rect(blk, 0, 0, 841, 594)
    lay1 = d.layouts.new('布局1')
    for _cy in range(2):
        for _cx in range(2):
            lay1.add_blockref('PAGE_A1', (_cx * 841, _cy * 594))
    msp = d.modelspace()
    if model_kind == 'room':
        # 4 个房间轮廓 8100×5600（用直线矩形，与真实图的"直线矩形"类型一致），
        # 互相留 2000 间隙 → 不共边，避开 R66 共边网格链剔除，确保测到 R90 本尊
        for _cy in range(2):
            for _cx in range(2):
                add_line_rect(msp, _cx * 10100, _cy * 7600, 8100, 5600)
    else:
        # 真实图框量级（短边 42050 ≥ 10000）：应按 √2 豁免保住
        add_line_rect(msp, 0, 0, 59400, 42050)
    return to_bytes(d)


data = _build_r90('room')
check('R90布局空间优先: 模型空间房间量级轮廓(8100x5600, 短边<10000)不豁免, 只剩布局4页 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 4
      and r['frame_counts_by_layout'].get('布局 "布局1"') == 4
      and '模型空间' not in r['frame_counts_by_layout'])

data = _build_r90('frame')
check('R90对照: 模型空间的真实图框量级(59400x42050, 短边>=10000)仍享√2豁免保留, fc=5 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 5
      and r['frame_counts_by_layout'].get('布局 "布局1"') == 4
      and r['frame_counts_by_layout'].get('模型空间') == 1
      and any(abs(c['width'] - 59400) < 5 and abs(c['height'] - 42050) < 5
              for c in r['candidates']))


# ---- 用例 114：R91 R52 豁免补「√2×加长倍率」（RF雅安综合艺术楼水施 模型 2→3） ----
# 场景复刻（RF雅安综合艺术楼水施_t3.dwg）：布局"布局1"有 4 张 841×594 真页 →
#   触发 R52「布局空间优先」；模型空间 3 张页框各带内框：
#     105100×59400（外）+ 101600×57400（内）—— 该图漏的就是这张
#     89100×63000（外）+ 83850×60000（内）×2 套
#   105100×59400 的 ratio = 1.7694 = √2×1.25（A1 的 1/4 加长 + 附栏口径，
#   数值上 = 84100+21000），对 √2 偏离 25.11% 够不着 R52 原有的 √2±2%；
#   rel 仅 13.60% 够不着 rel≥25% → 两条豁免全失被剔。
# R91（保守做法）：保留 √2 仍 ±2%，**额外**加一条 √2×加长倍率 ±1% 的或分支
#   （模块级 is_mult_ratio）。不动 √2 阈值——直接换成 is_page_ratio 的 ±10%
#   会把所有"比例接近 √2 的内容轮廓"一并放开，风险远大于只补倍率分支。
# 对照组：模型空间放同样带内框、但比例为非页面比例（ratio 1.90）的页框
#   → 不走倍率分支 → 仍按内容源剔除。
def _build_r91(model_ratio_mult):
    d = make_doc()
    # 布局空间 2×2 共 4 张 A1 真页（841×594）
    blk = d.blocks.new('PAGE_A1')
    add_closed_rect(blk, 0, 0, 841, 594)
    lay1 = d.layouts.new('布局1')
    for _cy in range(2):
        for _cx in range(2):
            lay1.add_blockref('PAGE_A1', (_cx * 841, _cy * 594))
    msp = d.modelspace()
    # 模型空间：一张带内框的页框（外框 + 内框，复刻真实图的双线画法）
    #   model_ratio_mult=True  → 105100×59400（ratio 1.7694 = √2×1.25）
    #   model_ratio_mult=False → 外框高度改成 105100/1.90（非页面比例）
    ow, oh = 105100, (59400 if model_ratio_mult else round(105100 / 1.90))
    add_closed_rect(msp, 0, 0, ow, oh)
    add_closed_rect(msp, 2500, 2000, ow - 3500, oh - 2000)
    return to_bytes(d)


data = _build_r91(True)
check('R91布局空间优先: √2×1.25 加长页框(105100x59400)享豁免保留, 模型空间保住1张 (smart)',
      lambda: parse(data),
      lambda r: r['frame_counts_by_layout'].get('模型空间') == 1
      and r['frame_counts_by_layout'].get('布局 "布局1"') == 4
      and any(abs(c['width'] - 105100) < 50 and abs(c['height'] - 59400) < 50
              for c in r['candidates']))

data = _build_r91(False)
check('R91对照: 非页面比例(1.90)的模型页框不享倍率豁免, 模型空间为0 (smart)',
      lambda: parse(data),
      lambda r: '模型空间' not in r['frame_counts_by_layout']
      and r['frame_counts_by_layout'].get('布局 "布局1"') == 4)


# ---- 用例 115：R94 xref 未解析时必须告警（不能静默漏检） ----
# 场景：water-2014.09.03综合艺术楼_t3.dwg 漏 8 张图框却毫无提示。
#   根因之一是旧 _detect_xref_warnings 读 blk.is_xref，而 ezdxf 把该属性挂在
#   blk.block_record 上（block_record.is_xref → True），blk 层取不到 →
#   looks_xref 恒 False → 整个告警链路静默失效。
# R94：改为两处都读，并支持 resolved 状态区分"已加载/仍缺失"。
#   这里构造一个"实体列表为空的 xref 块 + 1 个 INSERT 引用"的最小场景，
#   断言 xref_warnings 非空且 status='missing'（因为找不到参照文件）。
doc = make_doc()
msp = doc.modelspace()
# ezdxf 的 xref 判定看 block_record 的 flags 位（bit2 = xref），
# 这里用 dxfattribs 在建块时直接带上，模拟 ODA 转换后的空 xref 块。
doc.blocks.new(name='XREF_MISSING', dxfattribs={'flags': 4})
msp.add_blockref('XREF_MISSING', (0, 0))
add_closed_rect(msp, 0, 0, 84100, 59400)     # 主文件自身的 1 张真图框
data = to_bytes(doc)

check('R94 xref 告警: 空 xref 块 + 1 处引用 → xref_warnings 非空, 主框仍正常 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and len(r.get('xref_warnings') or []) == 1
      and r['xref_warnings'][0]['block_name'] == 'XREF_MISSING'
      and r['xref_warnings'][0]['status'] in ('missing', 'resolved'))


# ---- 用例 116：R94 LINE 簇配对须接近块尺寸（图元块 11123×7906 误报） ----
# 场景：块 A$C560B6EBA 是平面详图图元块（146 LINE / 29 HATCH / 30 INSERT，
#   块内最大闭合多段线仅 1800×600），但 4 条墙线（水平 y=0/-4990 长 8096、
#   竖直 x=0/5850 长 6475）配对出 5850×4990 的"矩形"，占块长边 11021 的 53%
#   —— 刚好越过原 50% 门槛 → 被认定"有闭合边框证据"入库 → 2 张误报。
#   而这 2 张误报又恰好落在 42050×29700 真图框内、同尺寸且并排，于是连带触发
#   R67「独立双页外包络剔除」把真图框也删了。修掉误报后 R67 不再误判。
# R94：LINE 簇配对出的矩形须 ≥ 块长边 × 70%，否则是墙线偶然围合。
def _build_r94_wall_block(wall_ratio):
    """构造一个"块内只有墙线围合、无真外框"的图元块。

    块 bbox 由两个远角小矩形撑到 11021×7906（模拟被散件撑大的 bbox）；
    墙线围合矩形宽度 = 11021 × wall_ratio，高度固定 5000。
    wall_ratio=0.53 → 占 53%，应判非图框；=0.98 → 占 98%，应视为真边框。
    """
    d = make_doc()
    BW, BH = 11021, 7906
    bd = d.blocks.new('PLAN_ELEM')
    # 两个远角小件，把块 bbox 精确撑到 BW×BH
    bd.add_lwpolyline([(0, 0), (500, 0), (500, 500), (0, 500)], close=True)
    bd.add_lwpolyline([(BW - 500, BH - 500), (BW, BH - 500),
                       (BW, BH), (BW - 500, BH)], close=True)
    # 墙线围合的内部矩形（4 条线，长度均 > _min_seg = BW×30% = 3306）
    ww = BW * wall_ratio
    hh = 5000.0
    x0, y0 = 500.0, 1000.0
    bd.add_line((x0, y0), (x0 + ww, y0))
    bd.add_line((x0, y0 + hh), (x0 + ww, y0 + hh))
    bd.add_line((x0, y0), (x0, y0 + hh))
    bd.add_line((x0 + ww, y0), (x0 + ww, y0 + hh))
    d.modelspace().add_blockref('PLAN_ELEM', (0, 0))
    return to_bytes(d)


data = _build_r94_wall_block(0.53)      # 墙线只占块 53% → 判为非图框
check('R94 LINE配对护栏: 墙线围合矩形仅占块53%的图元块不被当图框 (smart)',
      lambda: parse(data),
      # 该块不得以「块参照插入」身份入库（降级兜底的全实体包围盒不算图框识别）
      lambda r: not any(c['type'] == '块参照插入' for c in r['candidates']))

data = _build_r94_wall_block(0.98)      # 墙线 ≈ 块外框 → 视为真图框边框
check('R94 LINE配对对照: 占到块98%的LINE边框块仍被认作图框 (smart)',
      lambda: parse(data),
      lambda r: any(c['type'] == '块参照插入' and abs(c['height'] - 7906) < 50
                    for c in r['candidates']))


# ---- 用例 117：R95 块内「子块实例页框」提升（02食堂 识 5 实际 10） ----
# 场景复刻（02食堂sw-2014.9.3.dwg）：图框不是直接画的矩形，而是块
#   A$C7129040E 内 5 个 BOUNDARY-A1-1 **子块实例**（scale=100 → 84100×59400）。
#   块内没有任何 84100×59400 的闭合矩形/LINE 矩形，只有子块实例的 bbox 是
#   该尺寸 → 原链路（只扫块定义顶层 LWPOLYLINE）完全看不到。
# R95：在本实例块定义里找**子块实例页框**，按四重判据筛后提升为独立候选。
#   判据：①只扫被引用块（调用侧天然满足）②乘父+自身 scale 得世界尺寸
#        ③短边≥15000 且 ratio∈[1.30,5.5] ④A 系图纸尺寸整数倍 ±3mm、偏离√2≤1%
# 对照：子块实例尺寸非 A 系整数倍（如 90000×60000）→ 不提升。
#
# 注意用例构造：装配块本体的 bbox 也会走特征判定。真实图（02食堂）里
#   A$C7129040E 是 458935×137558（ratio 3.34）——虽然也过 ratio 区间，但它
#   罩住 5 个页框时走的是"包裹框剔除"（含 ≥2 个图框候选 → 本身判为外包络），
#   不会把页框吃掉。这里刻意让装配块尺寸与真实图同量级，避免构造偏差。
def _build_r95_inst_pages(use_a_series):
    d = make_doc()
    ab = d.blocks.new('ASSEMBLY')
    if use_a_series:
        # 页框块：定义 841×594（标准 A1），实例 scale=100 → 84100×59400
        # = A594×100，命中判据④（A 系图纸尺寸整数倍）
        pb = d.blocks.new('BOUNDARY_A1')
        add_closed_rect(pb, 0, 0, 841, 594)
        _child, _step, _sc = 'BOUNDARY_A1', 89100, 100
    else:
        # 对照组：900×600 的"纸"（×100 → 90000×60000，ratio 1.5）
        # ratio 偏离 √2 达 6%，不满足判据④ → 不该被提升为页框
        pb = d.blocks.new('BOUNDARY_BIG')
        add_closed_rect(pb, 0, 0, 900, 600)
        _child, _step, _sc = 'BOUNDARY_BIG', 95000, 100
    # 5 个页框等距排一行。**间距按块定义坐标给**：子块 insert 位置在父块
    #   坐标系内、不被子块 scale 放大，而子块展开后宽 84100 —— 间距须留出
    #   间隙不重叠（真实图 BOUNDARY-A1-1 的 insert 是 0/89100/178200/...）。
    #   若间距小于子块展开宽度，5 个页框会大幅重叠，被去重规则2（IoU）
    #   合并成 1 个 —— 那是构造错误，不是识别错误。
    for _i in range(5):
        ab.add_blockref(_child, (_i * _step, 0),
                        dxfattribs={'xscale': _sc, 'yscale': _sc})
    # 装配块内还要有散件把 bbox 撑到远大于单个页框，复刻真实图
    #   A$C7129040E 的 458935×137558（5 页只占其中一条横带）。
    ab.add_lwpolyline([(0, -78158), (458900, -78158),
                       (458900, 0), (0, 0)], close=True)
    d.modelspace().add_blockref('ASSEMBLY', (0, 0))
    return to_bytes(d)


data = _build_r95_inst_pages(True)
check('R95块内子块实例页框: 装配块内5个 BOUNDARY_A1(×100)=A594×100 全部提升 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 84100) < 50
                    and abs(c['height'] - 59400) < 50) == 5)

data = _build_r95_inst_pages(False)
check('R95对照: 子块实例尺寸非A系整数倍(90000x60000)不提升为页框 (smart)',
      lambda: parse(data),
      lambda r: not any(abs(c['width'] - 90000) < 100 and abs(c['height'] - 60000) < 100
                        for c in r['candidates']))


# ---- 用例 118：R95 子块尺寸必须乘【父实例 scale】（井道大样 1 个图框） ----
# 场景（井道大样.dwg）：块 A1 以 scale=1/150 插入**布局1**，块定义 126150×89100
#   → 世界尺寸 841×594，正是布局页里那 1 张真页框，**不该**额外算一页。
#   若漏乘父实例 scale，会误判成 126150×89100（= A594×150）而多加 1 页。
# 这里直接验证"乘父 scale"这条算术：块定义 126150×89100 × 1/150 = 841×594，
#   小于 15000 门槛 → 不提升。
def _build_r95_parent_scale():
    d = make_doc()
    # 内层：126150×89100 的"纸"（A1 按 1:150 的定义尺寸）
    pb = d.blocks.new('A1_INNER')
    add_closed_rect(pb, 0, 0, 126150, 89100)
    # 中层：块 A1，内含 1 个 A1_INNER
    ab = d.blocks.new('A1')
    ab.add_blockref('A1_INNER', (0, 0))
    # 模型空间：把 A1 以 1/150 插入 → 世界 841×594
    msp = d.modelspace()
    msp.add_blockref('A1', (0, 0),
                     dxfattribs={'xscale': 1.0 / 150, 'yscale': 1.0 / 150})
    return to_bytes(d)


data = _build_r95_parent_scale()
check('R95父scale: 块定义126150x89100经1/150缩放后世界841x594, 不提升为页框 (smart)',
      lambda: parse(data),
      lambda r: not any(abs(c['width'] - 126150) < 500 for c in r['candidates']))

# 对照组：同样的结构但**按 1:1 插入** → 世界 126150×89100 = A594×150，应提升
def _build_r95_parent_scale_1x():
    d = make_doc()
    pb = d.blocks.new('A1_INNER')
    add_closed_rect(pb, 0, 0, 126150, 89100)
    ab = d.blocks.new('A1')
    ab.add_blockref('A1_INNER', (0, 0))
    d.modelspace().add_blockref('A1', (0, 0))
    return to_bytes(d)


data = _build_r95_parent_scale_1x()
check('R95父scale对照: 同样结构按1:1插入(A594×150)应提升为页框 (smart)',
      lambda: parse(data),
      lambda r: any(abs(c['width'] - 126150) < 500 and abs(c['height'] - 89100) < 500
                    for c in r['candidates']))


# ---- 用例 119：R96 R62 单页图框块保护须验「块定义本身像个页框」 ----
# 场景（门卫电气.dwg）：模型空间 3 个真图框 BOUNDARY-A2-1（594×420 ×100 →
#   59400×42000），图框内各嵌一个建筑底图块（门卫 / 门卫平面，
#   定义 25400×15500，块内最大闭合框仅 14300 = 块长边 56%）。
#   底图块比例 1.639 恰好满足 R62 保护②③（块定义比例=候选比例、与外框
#   1.414 差 15.9%>10%），于是被当「单页图框块」保护、绕过嵌套去重
#   → 图框内的底图块被当独立页（fc 6 实际 3）。
# R96 在 _single_page_block_protect 加 ⑥：块内最大闭合矩形须 ≥ 块定义
#   长边 × 70%（真页框块的外框贴着块 bbox：BOUNDARY-A2-1 为 100%）。
def _build_r96_base_block(base_rect_w, base_rect_h):
    """构造「1 个真图框 + 框内 1 个建筑底图块」的图。

    base_rect_w/h：底图块内**主闭合矩形**尺寸。它决定 block_rect_len，
    从而决定 ⑥ 判据是否放行 R62 保护。

    只放 1 组：3 组同尺寸水平排列会被「对齐排列剔除装饰块」整组移除，
    那样测不到 R62 保护这条路（也掩盖了要验的东西）。
    """
    d = make_doc()
    # 真图框块：块定义就是一张 A2（594×420），闭合框贴着块 bbox → rect比 100%
    fb = d.blocks.new('BOUNDARY_A2')
    add_closed_rect(fb, 0, 0, 594, 420)
    # 建筑底图块：定义 bbox 撑到 25400×15500，但块内主闭合矩形只有
    #   base_rect_w×base_rect_h（复刻房子平面轮廓：外框是墙线不是闭合框）
    pb = d.blocks.new('PLAN_BASE')
    add_closed_rect(pb, 0, 0, base_rect_w, base_rect_h)
    # 散件把 bbox 撑到 25400×15500（墙角小构件，200×200 落在角上）
    add_closed_rect(pb, 25200, 15300, 200, 200)
    msp = d.modelspace()
    # 真图框 ×100 → 59400×42000
    msp.add_blockref('BOUNDARY_A2', (0, 0),
                     dxfattribs={'xscale': 100, 'yscale': 100})
    # 底图块落在图框内部（完全被图框 bbox 包含）→ 触发嵌套去重
    msp.add_blockref('PLAN_BASE', (10000, 10000))
    return to_bytes(d)


# 主闭合矩形 14300 → rect比 14300/25400 = 56% < 70% → 不给保护 → 嵌套剔小
data = _build_r96_base_block(14300, 8800)
check('R96底图块: 块内闭合框仅占块长边56%(建筑平面块)不被R62保护, fc=1 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 1
      and all(abs(c['width'] - 59400) < 100 for c in r['candidates']))

# 对照：主闭合矩形 25400 → rect比 100%（块定义本身即页框）→ 照旧受保护
data = _build_r96_base_block(25400, 15500)
check('R96对照: 块内闭合框占满块长边(块定义本身即页框)仍受R62保护, 底图块保留 (smart)',
      lambda: parse(data),
      lambda r: any(abs(c['width'] - 25400) < 50 and abs(c['height'] - 15500) < 50
                    for c in r['candidates']))


# ---- 用例 120：R97 块内 4 条 LINE 围成的页框须替换被溢出污染的块 bbox ----
# 场景（教学楼修改.dwg 电气）：块 BOUNDARY-A1-V 按 A1 幅面 1:1 绘制，外框是
#   **4 条 LINE**，精确 594×841；但块内一段 2 顶点 LWPOLYLINE 向下溢出
#   0.0791mm，把块 bbox 撑成 594×841.079 → 实例 scale=150 时输出
#   89100×126161.86，而实际应为 89100×126150（差 11.86mm）。
# 既有两条通道都扫不到：`_scan_block_max_page_rects` / `_scan_title_layer_page_rects`
#   只认闭合 LWPOLYLINE（块内唯一的 4 顶点闭合多段线是 559×821 标题栏内框），
#   且都用硬编码 `_ls < 15000` 判块定义坐标尺寸（594 < 15000 直接出局）。
# R97：新增 `_scan_line_page_rects`，4 条 LINE 配对成矩形，线段长 ≥ 15000/scale、
#   矩形面积 ≥ 块 bbox × 85%，仅在闭合多段线通道为空且恰 1 个时兜底给 R62c。
def _build_r97_line_frame(overflow):
    """overflow：溢出的 polyline 比 0 低多少（0 表示不溢出）。"""
    d = make_doc()
    bd = d.blocks.new('BOUNDARY_A1V')
    # 外框：4 条 LINE，精确 594×841（A1 纵向）
    add_line_rect(bd, 0, 0, 594, 841)
    # 标题栏内框：唯一的 4 顶点闭合多段线（559×821，不是外框）
    add_closed_rect(bd, 25, 10, 559, 821)
    # 剖切符号引线：上端正好到 841，下端（本例）溢出 -0.0791
    bd.add_lwpolyline([(313.2, 826.0), (313.8, 841.0)])
    if overflow:
        bd.add_lwpolyline([(313.2, -overflow), (313.8, 14.9209)])
    else:
        bd.add_lwpolyline([(313.2, 0.0), (313.8, 14.9209)])
    d.modelspace().add_blockref('BOUNDARY_A1V', (0, 0),
                                dxfattribs={'xscale': 150, 'yscale': 150})
    return to_bytes(d)


data = _build_r97_line_frame(0.079078)
check('R97 LINE页框: 块bbox被0.079溢出污染时用4条LINE外框尺寸, 89100x126150 (smart)',
      lambda: parse(data),
      lambda r: any(abs(c['width'] - 89100) < 1 and abs(c['height'] - 126150) < 1
                    for c in r['candidates'])
      and not any(abs(c['height'] - 126161.86) < 5 for c in r['candidates']))

# 对照：不溢出时块 bbox 本来就等于外框，输出仍是 89100×126150
data = _build_r97_line_frame(0)
check('R97对照: 无溢出时同为89100x126150(未被新通道改变) (smart)',
      lambda: parse(data),
      lambda r: any(abs(c['width'] - 89100) < 1 and abs(c['height'] - 126150) < 1
                    for c in r['candidates']))


# ---- 用例 121：R97 LINE 通道须排除「内边框线」（整体缩进一圈） ----
# 回归现场（电子称皮带输送系统（套图）.dwg）：PDJTK 等 4 个块的外框与内边框
#   都用 LINE 画，内框比块 bbox 小 3%（87320×61763 → 84750×60294，宽×0.9706、
#   高×0.9762）。R62c 的 ≥95% 门槛拦不住（0.9706/0.9762 都 >0.95），会把
#   正确尺寸改小。R97 加 ⑤：矩形四边须贴合块 bbox（相对偏差 ≤0.5%）。
def _build_r97_inner_border(inset_ratio):
    """inset_ratio：内边框相对外框的缩进比例。0 表示没有内边框。"""
    d = make_doc()
    bd = d.blocks.new('PDJTK')
    # 外框：4 条 LINE，87320/150 × 61763/150 的纸幅面（这里直接按块定义坐标写）
    W, H = 873.2, 617.63
    add_line_rect(bd, 0, 0, W, H)
    if inset_ratio:
        # 内边框：整体缩进 inset_ratio（两端各缩）
        dx, dy = W * inset_ratio, H * inset_ratio
        add_line_rect(bd, dx, dy, W - 2 * dx, H - 2 * dy)
    d.modelspace().add_blockref('PDJTK', (0, 0),
                                dxfattribs={'xscale': 100, 'yscale': 100})
    return to_bytes(d)


# 缩进 1.5% → 内框 84750×60294（实测回归值），四边偏差 1.5% > 0.5% → 不采用
data = _build_r97_inner_border(0.015)
check('R97内边框: 内边框比外框小1.5%时不被LINE通道当页框(保留外层尺寸) (smart)',
      lambda: parse(data),
      lambda r: any(abs(c['width'] - 87320) < 10 and abs(c['height'] - 61763) < 10
                    for c in r['candidates'])
      and not any(abs(c['width'] - 84750) < 20 and abs(c['height'] - 60294) < 20
                  for c in r['candidates']))

# 对照：没有内边框 → 外框照常被采纳（尺寸 = 块 bbox，无变化）
data = _build_r97_inner_border(0)
check('R97内边框对照: 无内边框时外框照常被LINE通道采纳 (smart)',
      lambda: parse(data),
      lambda r: any(abs(c['width'] - 87320) < 10 and abs(c['height'] - 61763) < 10
                    for c in r['candidates']))


# ---- 用例 122：R98 孤例「包络框」不得顶替成组真页框 ----
# 场景（邮电学院-平面图11.22.dwg）：DOTLN 层闭合多段线 157576×89818 把真页框
#   148700×84100 四边各外扩 3~4% 罩住（IoU=0.822）→ 去重规则2「留大剔小」
#   把真页框剔掉、包络框顶替（148700 应 8 实 7）。
# 几何上它与 R83 双线画法同域（面积比 88.4%、ratio 差 0.7%），唯一可靠区别是
#   **成组性**：真页框 8 个副本，包络框 1 个孤例。
# R98 判据（全满足剔 C）：①C 无同尺寸副本 ②IoU≥0.60 ③C 面积更大
#   ④ratio 差≤3% ⑤O 成组（除自身外 ≥2 副本）。
def _build_r98_envelope(cls_copies, env_offset_ratio):
    """真页框 cls_copies 个（横排）+ 1 个外扩包络框。

    env_offset_ratio：包络框相对真页框的单边外扩比例（0 表示不画包络框）。
    """
    d = make_doc()
    W, H = 1487.0, 841.0        # 块定义坐标（×100 → 148700×84100）
    bd = d.blocks.new('PAGE_BLK')
    add_closed_rect(bd, 0, 0, W, H)
    msp = d.modelspace()
    step = W * 1.06             # 间距留 6%，避免真页框互相重叠
    for i in range(cls_copies):
        msp.add_blockref('PAGE_BLK', (i * step * 100, 0),
                         dxfattribs={'xscale': 100, 'yscale': 100})
    if env_offset_ratio:
        dx = W * env_offset_ratio
        dy = H * env_offset_ratio
        # 包络框罩住第 2 个真页框（与其 IoU 足够高）
        bx = step * 100 - dx * 100
        by = -dy * 100
        add_closed_rect(msp, bx, by, (W + 2 * dx) * 100, (H + 2 * dy) * 100)
    return to_bytes(d)


# 8 个真页框 + 1 个外扩 3.8% 的孤例包络框 → 包络框被剔，8 个真页框全部保留
data = _build_r98_envelope(8, 0.038)
check('R98包络框: 孤例外扩框不顶替成组真页框(8个全保留) (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 148700) < 100 and abs(c['height'] - 84100) < 100) == 8
      and not any(c['width'] > 154000 and c['width'] < 161000
                  and abs(c['height'] - 89818) < 500 for c in r['candidates']))

# 对照：同样 8 个真页框但不画包络框 → 仍是 8 个（新判据不误伤）
data = _build_r98_envelope(8, 0)
check('R98包络框对照: 无包络框时 8 个真页框照常识别 (smart)',
      lambda: parse(data),
      lambda r: sum(1 for c in r['candidates']
                    if abs(c['width'] - 148700) < 100 and abs(c['height'] - 84100) < 100) == 8)


# ---- 用例 123：R98 条件I 表格页救援须「线多块少」 ----
# 场景（邮电学院-平面图11.22.dwg）：WIRE-照明 层回路范围框 140682×167328，
#   框内 953 LINE（正交 887）+ **577 个 INSERT** → 被条件I 误判为"表格页"
#   （横线 632/竖线 255）救回并输出，用户确认为误报（实际是照明回路覆盖范围）。
# R98 判据：块参照 ≥100 且 ≥ 横竖线总数×0.5 → 判为图元内容而非表格页，不救。
#
# 为何不用 ezdxf 现构造：条件I 的入场要同时避开条件A（area_ratio≥15%）、
#   条件B/C（ratio 闸门 + rel≥10%）三道闸，而 area_ratio/rel 都取决于该
#   layout 里**其他实体**的包围盒。用散线伪造分母试了 3 次（300mm 短线、
#   巨型块、不同跨度）都无法稳定复现"恰好落到条件I"的状态——这种用例
#   一旦 layout 细节变动就会失败，是脆弱测试。
#   改为直接读真实图纸（走 regress_dxf_cache 的 DXF 缓存；无图纸/无缓存时跳过），
#   断言的是端到端真实行为，这比构造更可靠。
def _load_real_drawing(name):
    """读取真实校验图纸的 DXF bytes（优先用回归缓存，避免每次跑 ODA）。"""
    _dwgs = [
        Path(r'C:\Users\hui_ou\Desktop\建筑图纸（必须校验）') / name,
        Path(r'C:\Users\hui_ou\Desktop\建筑项目图纸 - 非标') / name,
    ]
    _cache_dir = Path(__file__).parent / 'regress_dxf_cache'
    for _p in _dwgs:
        if not _p.exists():
            continue
        _st = os.stat(_p)
        _stem = _p.stem
        _safe = ''.join(c if (c.isascii() and (c.isalnum() or c in '-_')) else '_'
                        for c in _stem)[:48]
        _cp = _cache_dir / f'{_safe}-{_st.st_size}-{int(_st.st_mtime)}.dxf.gz'
        if _cp.exists():
            try:
                import gzip
                return gzip.decompress(_cp.read_bytes())
            except Exception:
                return None
    return None


_real = _load_real_drawing('邮电学院-平面图11.22.dwg')
if _real is not None:
    _rr = parse(_real)
    check('R98真实图: 邮电学院 剔掉2个误报后 fc=26 = 148700x84100x8 + 148600x84100x4 + 126100x59400x14',
          lambda: _rr,
          lambda r: r['frame_count'] == 26
          and sorted(r['frame_counts_by_layout'].values()) == [26]
          and not any(abs(c['width'] - 140682) < 300 and abs(c['height'] - 167328) < 300
                      for c in r['candidates'])
          and not any(abs(c['width'] - 157576) < 300 and abs(c['height'] - 89818) < 300
                      for c in r['candidates']))
    check('R98真实图: 148700x84100 有内容的8张全部保留(不再被包络框顶替)',
          lambda: _rr,
          lambda r: sum(1 for c in r['candidates']
                        if abs(c['width'] - 148700) < 50 and abs(c['height'] - 84100) < 50) == 8)
else:
    print('SKIP  R98真实图用例（未找到 邮电学院-平面图11.22.dwg 或其 DXF 缓存）')


# ---- 用例 124：R99 R63 相邻单元格剔除须认全「图签层」五类命名 ----
# 场景（暖通专业施工图11.7.dwg）：2 张 21000×29700 真页框画在 **PUB_TITLE** 层，
#   同尺寸恰 2 份 + 水平共边 → 命中 R63「相邻单元格剔除」，fc 14 实际 16。
# 根因：R63 的「图签层」判据只写了 'TTLB'，而 R82 的
#   _scan_title_layer_page_rects 一直用 5 个关键词
#   （TEL_TITLE / TTLB / PUB_TITLE / BORDER / 图框）。两边口径不一致。
# R99：统一成同一套关键词。
def _build_r99_adj_cells(layer):
    """两个同尺寸水平共边的闭合框，画在指定图层上。

    尺寸 210×297（×100 → 21000×29700），比例 0.7071 竖放。
    """
    d = make_doc()
    msp = d.modelspace()
    W, H = 210.0 * 100, 297.0 * 100
    for i in range(2):
        msp.add_lwpolyline(
            [(i * W, 0), ((i + 1) * W, 0), ((i + 1) * W, H), (i * W, H)],
            close=True, dxfattribs={'layer': layer})
    return to_bytes(d)


# PUB_TITLE 层 → 属图签层，R63 不得剔除（回归现场）
data = _build_r99_adj_cells('PUB_TITLE')
check('R99相邻单元格: PUB_TITLE 层的2个共边页框不被R63剔除 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2)

# 对照：TEL_TITLE 层（R63 原本就认）→ 同样保留，确认没破坏既有行为
data = _build_r99_adj_cells('TEL_TITLE')
check('R99相邻单元格对照: TEL_TITLE 层照常保留 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 2)

# 反向对照：真正的中性层表格分格 → 仍须被剔（护栏不能被放宽到失效）
#   全剔光时 parse 会抛「未检测到图框」，那正是期望结果，故用 try/except 兜。
def _r99_neutral_dropped():
    try:
        _r = parse(_build_r99_adj_cells('0'))
        return _r['frame_count'] < 2
    except RuntimeError as _e:
        return '未检测到图框' in str(_e)


check('R99相邻单元格反向对照: 中性层(0层)的共边同尺寸分格仍被剔除 (smart)',
      _r99_neutral_dropped,
      lambda ok: ok)


# ---- 用例 125：R100 R79「实物尺度详图空间」不得误吃「1:1 多页图纸」 ----
# 场景（附图02-剖面图.dwg）：11 张 A3 图框（420×297，ratio 1.414141，偏离 √2
#   仅 0.0051%）横向并排，msp_total = 5.3m² 恰好落进 R79 的 [4m², 100m²) 带。
#   R79 把 rel 分母换成内容总面积 → 每张 rel 仅 2.35% < 10% 门槛 → 条件C 全灭；
#   短边 297 < MIN_FRAME_SHORT_SIDE(400) 又过不了条件B → 11 张一张都认不出（fc=0）。
# R100 护栏：layout 内存在 ≥3 个同尺寸 A 系图幅候选 → 是按 1:1 出图的多页图纸，
#   不是"无页框参照系的详图空间" → R79 不触发，rel 分母保持 layout_max_area。
#   为何用「A 系图幅 + 同尺寸≥3」：详图空间的图案（井道大样 570×830 ratio 1.4561）
#   不是 A 系图幅；单张真图框（非圆减速器 2378×1682=1 份）同尺寸 <3。两者都不触发。
def _build_r100_paper_pages(n, paper_w=420.0, paper_h=297.0):
    """n 张同尺寸纸面比例闭合框横向并排 + 纵向散线填充。

    关键：必须让 msp_total 落进 R79 的 [4e6, 1e8) 区间（4~100 m²），
    否则 R79 压根不触发，测不到本护栏。
    填充用 5 条水平长线（长 3000，间距 600，最高 y≈3270），把包围盒抬高到
    ≈3300；n 张并排宽度 = n*450。
      n=2 → 900×3300 = 2.97e6  ← 不够 4e6，需再加宽
    故宽度也补一条横线到 2000：总 2000×3300 = 6.6e6 ✓ 恒落在区间内，
    与 n 无关（避免"张数少就测不到"）。
    只有水平线、无竖线配对 → 不构成矩形候选。
    """
    d = make_doc()
    msp = d.modelspace()
    step = paper_w + 30.0
    for i in range(n):
        add_closed_rect(msp, i * step, 0, paper_w, paper_h)
    # 纵向填充：5 条长 2000 的水平线，把包围盒抬到 2000×~3280
    for k in range(5):
        y = paper_h + 100.0 + k * 700.0
        msp.add_line((0, y), (2000.0, y))
    return to_bytes(d)


# 11 张 A3 并排 → 全部识别（护栏生效）
data = _build_r100_paper_pages(11)
check('R100纸面多页: 11张420x297(1:1出图)全部识别, 不被R79判成详图空间 (smart)',
      lambda: parse(data),
      lambda r: r['frame_count'] == 11
      and all(abs(c['width'] - 420) < 2 and abs(c['height'] - 297) < 2
              for c in r['candidates']))

# 对照：只有 2 张（同尺寸 <3，护栏不触发）→ R79 照旧，图框认不出
#   这条断言的是「护栏范围没有放宽到 2 张」——2 张共边是表格分格形态，
#   不应因本护栏就全放行。
data = _build_r100_paper_pages(2)
def _r100_two_pages():
    try:
        _r = parse(data)
        return _r['frame_count']
    except RuntimeError:
        return 0

check('R100纸面多页对照: 仅2张同尺寸A系不触发护栏(仍按R79处理) (smart)',
      _r100_two_pages,
      lambda fc: fc < 2)

# 反向对照：3 张同尺寸但**不是 A 系幅面**（如 500×300）→ 护栏不触发，R79 照旧
def _build_r100_non_a_pages(n=4):
    d = make_doc()
    msp = d.modelspace()
    W, H = 500.0, 300.0
    step = W + 30.0
    for i in range(n):
        add_closed_rect(msp, i * step, 0, W, H)
    # 同样把 msp_total 撑进 [4e6, 1e8)，让 R79 有机会触发
    for k in range(5):
        y = H + 100.0 + k * 700.0
        msp.add_line((0, y), (2000.0, y))
    return to_bytes(d)


data = _build_r100_non_a_pages(4)
def _r100_non_a():
    try:
        _r = parse(data)
        return _r['frame_count']
    except RuntimeError:
        return 0

check('R100纸面多页反向对照: 非A系幅面(500x300)x4不触发护栏 (smart)',
      _r100_non_a,
      lambda fc: fc < 4)


print(f'\n结果: {sum(results)} 通过, {len(results) - sum(results)} 失败')
