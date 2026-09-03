# dwg-frame-parser（DWG/DXF 图框解析工具）

> 项目路径：`C:\Users\hui_ou\PycharmProjects\dwg-frame-parser`
> 服务地址：启动后访问 `http://127.0.0.1:5000`
> 技术栈：Flask + ezdxf + 原生 JS（前端）

上传 DXF/DWG 图纸，后端智能识别图框并返回宽×高（mm）、图框数量及候选明细。

---

## 一、图框识别算法（核心流程）

### 1. 候选收集（模型空间 + 所有布局空间）

| 候选来源 | 说明 |
| --- | --- |
| 闭合多段线 | LWPOLYLINE/POLYLINE，含矩形度过滤（≥0.92，剔除 L 形标题栏等） |
| INSERT 块参照 | 块定义 bbox × xscale/yscale + rotation + insert 点 = 真实图框 bbox；预筛短边 ≥500mm 且长宽比 ∈[1.05, 5.5] |
| 直线矩形 | 共线聚合 + 线对配对 + P1 严格矩形判定（4 边各有完整 LINE 覆盖），多矩形检测 |
| 全实体包围盒（降级） | 以上全无时兜底；**排除 VIEWPORT**（仅含视口的空布局不产生候选） |

**可见性过滤**（收集前）：跳过冻结/隐藏图层实体 + 实体级 invisible（组码 60），只统计 CAD 里可见的实体。

### 2. 图框特征匹配（is_frame_like，按序判定）

- **前置**：归一化长宽比 ∈ [1.05, 5.5]；`area_ratio > 1.0`（候选面积超过 layout 总面积）直接判异常剔除
- **条件 A**：面积占比 ≥ 15%（常规单图框 layout）
- **条件 C**：相对面积 ≥ 该 layout 最大候选面积 × 10% **且长宽比 ≥ √2**（多图框同 layout / 非标单位；≥√2 允许 A 系列加长版，过滤接近正方形的建筑外框）
- **条件 B**：显式矩形 + 短边 ∈[400, 2000]mm + 宽高接近整数 + **长宽比接近 √2±10%**（密集几何场景，过滤家具/设备小矩形）

### 3. 候选清洗（匹配通过后）

1. **包裹框剔除**：内部完全包含 ≥2 个独立尺寸图框候选的大框判为外包络剔除（按 5% 尺寸聚类去重，避免同图框重复画法误剔；跳过 INSERT 块参照）
2. **对齐排列剔除**：同块名 ≥3 个 INSERT 实例短方向紧贴成线判为装饰块（柱网/门窗阵列）；**标准 A 系列尺寸白名单**保护（短边 ∈{841,594,420,297,210,148,105} 且长边 ≈ 短边×√2 不参与剔除）
3. **去重**：嵌套（面积 < 外框 95%）+ IoU > 50%
4. **每 layout 上限 100**（防爆炸兜底）

### 4. XREF 外部参照警告

ezdxf 默认不加载 XREF 外部文件，引用块会漏识别图框。检测到时写日志警告（不阻塞解析），API 返回 `xref_warnings` 字段供前端展示。

### 5. 输出

- `width` / `height`：选中图框尺寸（面积最大候选，可按优先级加权）
- `frame_count`：图框数量
- `candidates`：候选明细（前 20）
- `xref_warnings`：XREF 警告列表

---

## 二、测试基准（回归用图纸）

| 图纸 | 实际图框数 | 验证要点 |
| --- | --- | --- |
| RF雅安综合艺术楼电气1_t3.dwg | 9 | 多图框同模型空间 + 0.01mm 非标单位（条件C） |
| 世欧澜山6#1901.dwg | 15 | INSERT 块参照图框（12fas ×15，缩放 1.0714） |
| 一层平面图3.25_t3.dwg | 11 | 布局空间 841×594 闭合多段线；模型空间无图框 |
| 中水电美立方-总平图7.22-1.dwg | 2 | A4 加长版 297×525.5（长宽比 1.769，条件C ≥√2） |
| 泛悦通风 11-MW-FP001.dwg | 1 | 空布局仅含视口（VIEWPORT 过滤） |

---

## 三、优化记录

### 2026-09-03

- **包裹框识别 + 条件C 相对面积法 + 可见性过滤**（雅安 102→9）：外层大框内含 ≥2 个独立尺寸图框候选判为外包络剔除；相对面积法适配多图框同 layout 与非标单位；冻结图层/实体级 invisible 过滤
- **INSERT 块参照路径 + 对齐排列剔除**（澜山 100→15）：块参照插入是图框常见画法，补全识别路径；同块名对齐排列实例判为装饰块剔除
- **XREF 检测警告（方案B）**：只警告不加载，等遇到真实 XREF 图纸再升级
- **标准 A 系列白名单 + area_ratio 异常过滤**（一层平面图 3→11）：对齐排列的标准 A 系列图框不误剔；面积超过 layout 总面积的候选必为伪候选
- **长宽比 √2 约束**：条件C ≥√2（允许加长版）过滤接近正方形的误判候选；条件B ±10% 过滤小矩形
- **空布局视口过滤**（FP001 2→1）：降级路径排除 VIEWPORT，仅含视口的空布局不产生伪图框

### 2026-08-31 及以前

- 多矩形直线检测（替代全局最外框）、归一化长宽比、同源面积占比分母、safe_log 日志体系、上传上限 200MB 等基础能力

---

## 四、API

`POST /upload`（multipart/form-data）

| 参数 | 说明 |
| --- | --- |
| file | .dxf / .dwg 文件 |
| priority | polyline（默认）/ line_rect |
| unit | mm（默认）/ inch |
| mode | smart（默认）/ force_max |

返回：`{ width, height, unit, frame_count, candidates[], xref_warnings[] }`

---

# 附录：PyCharm 使用指南

## 一、在 PyCharm 中打开项目

1. 启动 PyCharm。
2. 点击 **File → Open**（欢迎界面则点 **Open**）。
3. 选择目录 `C:\Users\hui_ou\PycharmProjects\dwg-frame-parser`，点击 **OK**。
4. 如果弹出 "Trust project?" 对话框，点击 **Trust Project**。

---

## 二、配置 Python 解释器（只需做一次）

项目内已建好虚拟环境 `.venv`，PyCharm 通常会自动识别。若没有自动识别，手动配置：

1. 打开 **File → Settings**（快捷键 `Ctrl + Alt + S`）。
2. 左侧进入 **Project: dwg-frame-parser → Python Interpreter**。
3. 点击右上角齿轮图标 ⚙ → **Add Interpreter → Add Local Interpreter**。
4. 选择 **Virtualenv Environment → Existing**，解释器路径选择：
   ```
   C:\Users\hui_ou\PycharmProjects\dwg-frame-parser\.venv\Scripts\python.exe
   ```
5. 点击 **OK** 保存。

配置正确后，Settings 页面会显示已安装的包：`flask`、`flask-cors`、`ezdxf` 等。

> 如果以后需要重装依赖：打开 PyCharm 底部的 **Terminal**（会自动激活 .venv），运行：
>
> ```
> pip install -r requirements.txt
> ```

---

## 三、启动服务（核心操作）

### 方式 A：直接运行（最简单，推荐）

1. 在左侧项目树中**双击打开 `app.py`**。
2. 点击 `app.py` 代码编辑区右上角的绿色运行按钮 ▶（或右键编辑区 → **Run 'app'**，或按 `Shift + F10`）。
3. 底部自动弹出 **Run 窗口**，看到如下输出即启动成功：
   ```
    * Serving Flask app 'app'
    * Running on http://127.0.0.1:5000
   ```
4. 停止服务：点击 Run 窗口左侧的红色方块 ■（`Ctrl + F2`）。

### 方式 B：配置 Run Configuration（更规范）

1. 菜单栏 **Run → Edit Configurations...**
2. 点击左上角 **＋ → Python**。
3. 填写：
   - **Name**：`dwg-frame-parser 服务`
   - **Script path**：`C:\Users\hui_ou\PycharmProjects\dwg-frame-parser\app.py`
   - **Python interpreter**：选 `.venv` 里的 Python（上一步配置的那个）
   - **Working directory**：`C:\Users\hui_ou\PycharmProjects\dwg-frame-parser`
4. 点击 **OK**。
5. 以后启动只需按 `Shift + F10`，或点击右上角下拉框选中该配置后点 ▶。

### 方式 C：在 PyCharm 内置 Terminal 里跑（等同你原来的命令行方式）

1. 点击 PyCharm 底部的 **Terminal** 标签（`Alt + F12`）。
2. Terminal 会自动激活 `.venv`（命令行前缀有 `(venv)` 字样）。
3. 输入：
   ```
   python app.py
   ```
4. 停止：在 Terminal 中按 `Ctrl + C`。

---

## 四、打开前端页面

前提：**先启动后端服务**（见第三节），页面才能解析文件。

`app.py` 已加静态文件托管：`/` 会返回 `index.html`，`/style.css`、`/app.js` 也会正常提供。

### 方式 A（推荐）：直接访问服务地址

服务启动后，打开浏览器，地址栏输入：
```
http://127.0.0.1:5000
```
按回车，即可看到上传页面。页面会自动调用后端的 `/upload` 接口，不需要关心文件路径。

### 方式 B：在文件管理器中双击

打开 `C:\Users\hui_ou\PycharmProjects\dwg-frame-parser`，**双击 `index.html`**，浏览器会以 `file:///` 方式打开，上传功能也照常可用。

> 页面上出现「请确保本地解析服务运行在 http://localhost:5000」的提示是正常现象——只要后端 Run 窗口还在跑着就不用管。

---

## 五、常见问题

| 问题 | 处理办法 |
| --- | --- |
| 端口 5000 被占用 | 修改 `app.py` 最后一行的 `port=5000` 为其他端口（如 5001），同时修改 `app.js` 中对应的 API 地址 |
| 运行报 `ModuleNotFoundError: No module named 'flask'` | 说明解释器没选对，回到"配置 Python 解释器"一节检查是否指向 `.venv` |
| 上传 `.dwg` 文件报错 | `.dwg` 格式依赖 **ODA File Converter**（ezdxf 的 odafc 插件调用它转换）。若本机未安装，请到 ODA 官网下载安装 `ODAFileConverter`，并保持默认安装路径；`.dxf` 文件不受影响，可直接解析 |
| 解析失败: [Errno 22] Invalid argument | **已修复（2026-08-31）**。原因是 Flask 在工作线程里执行 `print()` 输出日志时，PyCharm 控制台环境会抛 `OSError [Errno 22]`。现已改为安全日志（`safe_log`），控制台写失败不影响解析，日志写入 `parser.log` |
| 上传超过 50MB 的大图纸被拒 | **已放宽到 200MB**（`app.py` 中 `MAX_CONTENT_LENGTH`） |
| 修改代码后不生效 | debug 模式下保存即自动重启；若未生效，手动点 Run 窗口的重新运行按钮 ⟳ |

---

## 项目文件说明

| 文件 | 作用 |
| --- | --- |
| `app.py` | Flask 后端服务：接收 DXF/DWG 上传，智能识别图框并返回宽×高（mm） |
| `index.html` | 前端上传页面 |
| `app.js` | 前端逻辑（调用 /upload 接口） |
| `style.css` | 前端样式 |
| `parser.log` | 运行日志（候选明细、选中结果、异常堆栈），批量解析后可排查具体文件 |
