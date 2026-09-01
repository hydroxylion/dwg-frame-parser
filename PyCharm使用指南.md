# dwg-frame-parser（DWG 图框解析工具）— PyCharm 使用指南

> 项目路径：`C:\Users\hui_ou\PycharmProjects\dwg-frame-parser`  
> 服务地址：启动后访问 `http://127.0.0.1:5000`  
> 已完成：项目创建、文件迁移、虚拟环境（.venv）、依赖安装（flask / flask-cors / ezdxf），并实测接口解析 A1 图框成功（返回 841 × 594）。

---

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
>
>

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

我已经在 `app.py` 里加了静态文件托管：`/` 会返回 `index.html`，`/style.css`、`/app.js` 也会正常提供。所以**最推荐的打开方式**是：

### 方式 A（推荐）：直接访问服务地址

服务启动后，打开浏览器，地址栏输入：
```
http://127.0.0.1:5000
```
按回车，即可看到上传页面。页面会自动调用后端的 `/upload` 接口，不需要关心文件路径。

> 把 `http://127.0.0.1:5000` 加入浏览器书签，以后每次启动服务后点书签就行。

### 方式 B：在文件管理器中双击
打开 `C:\Users\hui_ou\PycharmProjects\dwg-frame-parser`，**双击 `index.html`**，浏览器会以 `file:///` 方式打开，上传功能也照常可用。

### 方式 C：在 PyCharm 中打开（如果看不到浏览器图标）
不同 PyCharm 版本/主题可能没有右上角浏览器图标，可以这样操作：

1. 在左侧项目树中**右键 `index.html`**。
2. 选择 **Open In → Default Browser**（或你安装的 Chrome/Edge）。

如果右键菜单里没有浏览器选项，直接用方式 A 即可，最简单。

> 页面上出现「请确保本地解析服务运行在 http://localhost:5000」的提示是正常现象——只要后端 Run 窗口还在跑着就不用管。

---

## 五、使用服务

- 服务监听 `0.0.0.0:5000`，接口为 `POST http://127.0.0.1:5000/upload`。
- 注意：`app.py` 开启了 `debug=True`（调试模式），代码修改保存后服务会自动重启，适合开发阶段。
- **运行日志**：所有解析日志（候选矩形、选中结果、异常堆栈）会写入项目目录下的 `parser.log` 文件，批量解析后可打开它排查某个文件为什么失败/为什么尺寸是那个值。

---

## 六、常见问题

| 问题                                                 | 处理办法                                                                                                                            |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 端口 5000 被占用                                        | 修改 `app.py` 最后一行的 `port=5000` 为其他端口（如 5001），同时修改 `app.js` 中对应的 API 地址                                                           |
| 运行报 `ModuleNotFoundError: No module named 'flask'` | 说明解释器没选对，回到第二节检查 Python Interpreter 是否指向 `.venv`                                                                                |
| 上传 `.dwg` 文件报错                                     | `.dwg` 格式依赖 **ODA File Converter**（ezdxf 的 odafc 插件调用它转换）。若本机未安装，请到 ODA 官网下载安装 `ODAFileConverter`，并保持默认安装路径；`.dxf` 文件不受影响，可直接解析 |
| 解析失败: [Errno 22] Invalid argument                  | **已修复（2026-08-31）**。原因是 Flask 在工作线程里执行 `print()` 输出日志时，PyCharm 控制台环境会抛 `OSError [Errno 22]`，把已经解析成功的请求搞挂。现已改为安全日志（`safe_log`），控制台写失败不影响解析，日志写入 `parser.log` |
| 上传超过 50MB 的大图纸被拒                                   | **已放宽到 200MB**（`app.py` 中 `MAX_CONTENT_LENGTH`）                                                                                      |
| 修改代码后不生效                                           | debug 模式下保存即自动重启；若未生效，手动点 Run 窗口的重新运行按钮 ⟳                                                                                       |

---

## 项目文件说明

| 文件                 | 作用                                        |
| ------------------ | ----------------------------------------- |
| `app.py`           | Flask 后端服务：接收 DXF/DWG 上传，智能识别图框并返回宽×高（mm） |
| `index.html`       | 前端上传页面                                    |
| `app.js`           | 前端逻辑（调用 /upload 接口）                       |
| `style.css`        | 前端样式                                      |
| `requirements.txt` | 依赖清单（flask、flask-cors、ezdxf）              |
| `.venv/`           | 项目虚拟环境（已在 PyCharm 中可直接使用）                 |
