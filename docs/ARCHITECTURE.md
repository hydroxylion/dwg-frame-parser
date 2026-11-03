# 架构评估：app.py 该不该拆

> **结论**：该拆，但不按「控制器 / 服务 / 仓储」三层拆。按「阶段 + 判据」拆，分三批，
> 只做前两批。第三批（判据 pass 化）现在不做。
>
> 本文档是**决策依据 + 启动判据**，不是待办清单。没有出现「六、启动判据」里写的
> 具体情况之前，不要动手。

评估时间 2026-11-03，基线提交 `084a8f2`，`app.py` 9506 行 / 608KB。

---

## 一、量化事实

| 项目 | 数值 |
| --- | --- |
| `app.py` 总行数 | 9506（608 KB） |
| 顶层条目 | 106 个（50 函数 + 55 常量 + 1 类；另有 16 处 import） |
| `get_bounding_box_from_bytes` | **4595 行（48%）** |
| `collect_candidates_from_layout` | **2379 行（25%）** |
| 两者合计 | **6974 行（73%）** |
| 其余 104 个条目 | 2530 行（27%），平均 24 行/个 |
| Flask 路由 | **3 个，共 46 行** |
| 环境变量读取点 | 68 处，约 61 个开关 |
| 模块级可变状态 | **1 个**（`_LAYOUT_ROT`，旋转缓存） |

巨型函数内部结构：

| | `get_bounding_box_from_bytes` | `collect_candidates_from_layout` |
| --- | --- | --- |
| 行数 | 4595 | 2379 |
| **局部变量** | **694 个** | **499 个** |
| 嵌套 def | 36 个 | 23 个 |
| if / for / return | 564 / 171 / 104 | 304 / 75 / 57 |
| 注释阶段标记 | 100+ 处（`# ---------- … ----------`） | — |

---

## 二、为什么不能按三层架构拆

这个项目的「控制器」已经薄到不能再薄：

```
upload_file()  →  5 行参数校验 + 1 句 get_bounding_box_from_bytes(...) + jsonify
```

所谓「控制器薄、服务层承载业务」，**这里本来就是对的**。真按三层拆，结果是把
4595 行的 `get_bounding_box_from_bytes` 原封不动搬进 `services/frame_parser.py`——
文件数从 1 变 10，代码一行没动，只是换了文件名。

真正的结构问题不是**分层缺失**，而是**一个函数里顺序跑了 100 多个判据**。
所以拆分的正确单位是「阶段」和「判据」，不是「层」。

另一条支撑事实：模块级可变状态只有 1 个（`_LAYOUT_ROT`）。这意味着**拆模块几乎没有
全局状态迁移成本**——障碍全在函数内部，不在模块之间。

---

## 三、真正的阻力：699 个局部变量

内层函数现在靠闭包白拿外层变量，提到模块级必须显式传参。这是**任何拆分方案的
硬成本**，也是「按 pass 拆」比「按函数拆」更难的根因（pass 之间共享 `all_candidates`
的当前状态）。

捕获分布很不均匀，这是唯一的好消息：

| 捕获外层变量数 | `get_bounding_box…` | `collect_candidates…` |
| --- | --- | --- |
| 0（零捕获，可无条件模块化） | 7 个 | 6 个 |
| 1~3 个 | 20 个 | 12 个 |
| ≥4 个 | 9 个 | 5 个 |

捕获最多的两个恰是最难搬的：`strip_wrapping_frames`（496 行，13 个）、
`is_frame_like`（342 行，17 个）。

注：`_block_closed_frame_len`、`_resc_scan`、`_block_max_member_len`、
`_scan_block_text_len`、`_block_has_closed_frame` 都**捕获了自己**（递归调用）。
提到模块级后递归反而更自然，这几个不算障碍。

---

## 四、三批方案

### 第一批：常量与纯函数搬运（2530 行，零闭包）

全部是模块级函数/常量，不涉及闭包，是真正的 cut-paste + import。

| 新模块 | 内容 |
| --- | --- |
| `frame_parser/geometry.py` | `polygon_area`、`is_polyline_closed`、`get_polyline_vertices`、`_intersect_segments`、`_merge_segments`、`_coverage_contains`、`_cluster_parallel_lines`、`_merge_stripe_intervals` |
| `frame_parser/entities.py` | `is_entity_invisible`、`is_xref_block`、`xref_block_names`、`noplot_layer_names`、`is_grid_layer`、`get_entity_bbox`、`block_rect_list`、`_insert_ocs_mirrored`、`_insert_point_wcs` |
| `frame_parser/sizes.py` | `normalized_ratio`、`is_page_ratio`、`is_mult_ratio`、`is_paper_short_side`、`is_paper_sheet_size`、`is_a_series_page_size` + 全部纸幅常量 |
| `frame_parser/line_rect.py` | `detect_rectangles_from_lines` + 10 个内层 def + 相关常量 |
| `frame_parser/title.py` | `has_title_stripe`、`_cluster_title_boxes`、`_filter_title_strips`、`_expand_frames_with_title_strips`、`_expand_frames_with_edge_strips`、`_collect_title_strips` |
| `frame_parser/xref.py` | `_xref_search_dirs`、`_is_unresolved_xref`、`_detect_xref_warnings`、`_resolve_xref_file`、`_load_xref_candidates` |
| `frame_parser/blocks.py` | `block_world_bbox`、`calculate_layout_total_bbox` |
| `frame_parser/config.py` | 61 个环境变量开关集中读取，启动时校验 |
| `frame_parser/logging_setup.py` | `safe_log`、`log_debug`、日志轮转与 ezdxf 降噪 |
| `frame_parser/web.py` | 3 个 Flask 路由 + `assert_port_free` |

**顺带解决的历史债**：R132 发现的纸幅白名单三副本且容差不一致——
`COMMON_PLOT_SCALES`（函数内局部）、`_PAPER_SCALES`（模块级，注释自认「改动时两处需
同步」）、`app.js` 一份（`TOL_ABS=2mm` vs 后端 `PAPER_SIDE_REL_TOL=0.05%`）。搬进
`sizes.py` 时合成单一来源。

**验证**：407 条单测 + 115 张全量回归。纯搬运，跑一遍即可。

### 第二批：提取 25 个内层 def（约 400 行）

范围是上表「0 个捕获」的 13 个 + 「1~3 个捕获」的 32 个里挑，约 25 个。剩下 14 个
（捕获 ≥4）不动。

**验证手段**：复用 R132 加的静态守卫（`symtable` 扫「模块级未定义却被 global 引用」
的名字，跑单测即红）。R132 那个「两个判据自上线起从未生效」的 bug 就出在这种改动上，
有这个守卫兜底才敢动。

### 第三批：判据 pass 化（100+ 判据抽成有序规则表）

**现在不做。** 这是唯一能真正「简化」的路径，但：

- `all_candidates` 被 100 多个 pass 反复读写，顺序敏感；
- `_lay_top2`、`_giant_kicked_layouts` 这类中间量跨 pass 存在；
- 救援判据和剔除判据互相打补丁（R63 补捞就要处理「包裹框剔除发生在它之前」）。

改错不报错，只会让某张图的两个数字变化，而定位成本极高。

---

## 五、拆了**不**解决什么

1. **不解决「判据之间无法验算」**。这是语义问题，不是文件组织问题。拆成 10 个模块后，
   `all_candidates` 在 100 个 pass 之间流转的复杂度一点没变。
2. **会提高跨模块的定位成本**。现在 `grep -n "R123" app.py` 一把定位全部出现位置；
   拆开后判据的执行顺序只能靠 `git log` 和文档追。**所以如果拆，必须同步产出「判据
   执行顺序清单」**，否则未来的自己会比现在更累。
3. **不改运行性能**。import 期开销差异可忽略，解析耗时全在 ezdxf 和判据本身。

---

## 六、启动判据

满足**任意一条**才动手，否则保持现状：

1. **编辑体验**：改一个判据需要滚动超过 2000 行定位上下文，或 IDE 在大文件上出现
   明显的输入延迟 / 补全卡顿 → 做第一批。
2. **协作**：有第二个人要在同一时间改判据，`git` 冲突无法靠分工规避 → 做第一批 + 第二批。
3. **交接**：需要把解析器交给别人维护，或对外提供调用接口 → 做第一批 + 第二批，并把
   `config.py` 与入口整理出来。
4. **要加第 100+ 个判据且已经数不清顺序** → 先补「判据执行顺序清单」，再评估第三批。

**只有「文件看起来太大」这一条不构成动手理由。** 9506 行单文件在这个项目是已经付出的
历史成本，不是新增风险；为美观拆分会净亏。

---

## 附：评估方法留档

本次数据由 AST 静态分析得到，可复现：

- 顶层条目与行数范围：`ast.parse` 后遍历 `tree.body`，取 `lineno` / `end_lineno`。
- 内层 def 的闭包捕获量：对每个内层函数求「引用的名字 − 自身局部定义」，再与
  外层函数的局部变量全集取交集。零交集 = 可无条件模块化。
- 模块级可变状态：`tree.body` 中值为 `List` / `Dict` / `Set` / `dict()` / `Cache()` 的赋值。

这套分析以后加判据后可以重跑，用来判断「现在到第几批的启动线了」。
