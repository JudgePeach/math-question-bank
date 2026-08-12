# AGENTS.md - 本地化数学题库管理系统 开发与 AI 代理指南

## 1. 项目概述
本项目是一个本地运行的半自动化数学题库管理工作台。核心目标是通过极简的本地化部署，实现高质量图文混排数学题目（尤其是高中及更高阶数学内容）的收集、标签化管理、OCR 识别以及 AI 辅助生成解析。

## 2. 核心技术栈
本项目追求极简配置与极致体验，严格遵循以下技术选型，**不要引入复杂的现代前端构建工具（如 Webpack/Vite/Node.js 生态）**：
- **后端**：Python + FastAPI。
- **后端渐进式模块架构**：根目录 `main.py` 继续作为 `uvicorn main:app` 兼容入口；后端领域能力统一集中在 `mathbank/`。`database.py` 提供 SQLite ORM 与 Session，`paper_helper.py` 提供 LaTeX/PDF 编译排版，`sync_helper.py` 提供备份与 AI 题库导出，`paths.py` 统一锚定数据库、静态资源、上传、模板、备份、环境变量和构建路径，`curriculums.py` 加载四套教材 JSON，`prompts.py` 提供 OCR、解题、分类、拆卷、TikZ 与 AI 组卷的纯提示构建器，`ai_providers.py` 统一解析模型供应商，`ai_http.py` 统一负责 AI HTTP 请求，`ai_json.py` 统一解析 AI 结构化输出。运维、迁移、检索与 Release 工具统一位于 `scripts/`，从项目根目录使用 `python3 -m scripts.<模块名>` 运行。严禁重新在根目录新增业务模块或复制供应商判断规则。
- **数据库**：SQLite + SQLAlchemy（轻量级，数据存储在本地 `.db` 文件中）。
- **前端页面**：纯 HTML + 原生 JavaScript。
- **前端脚本拆分**：前端 JS 采用无编译的“渐进式级联加载”架构，按 `api.js`、`editor.js`、`ocr.js`、`import.js`、`paper.js` 的顺序级联加载；前四个模块负责 API/全局状态、编辑与渲染、OCR 图像交互、导入拆卷，`paper.js` 负责组卷工作台。加载顺序严格依存，不允许产生任何编译及捆绑动作。
- **前端样式与字体**：Tailwind CSS + FontAwesome 图标库 + Inter/Outfit 字体包（均已下载至本地 `/static/lib` 支持 100% 离线使用与跨平台系统降级）。
- **公式渲染**：KaTeX（已下载至本地支持 100% 离线数学公式渲染），必须支持题干与解析框实时解析、秒级渲染。
- **中转站模型 7:3 弹性 UI 布局与 Reasoning Effort 自动解析**：在系统 API 设置中选择中转站平台（`zhongzhan_gpt` 或 `zhongzhan_claude`）时，模型输入区域自动转换为 7:3 弹性比例（70% 模型名称，30% 推理强度）。后端由 `mathbank.ai_providers.parse_model_and_effort` 自动提取纯净模型名称并注入请求参数。
- **全局 Tooltip 提示系统**：基于纯原生事件代理接管 `title` / `data-tooltip` 浮现（详见第 6 节交互规范）。


> [!IMPORTANT]
> **开发规范单一来源规则**：
> 根目录 `AGENTS.md` 是本项目面向 AI 代理与开发者的唯一开发规范来源。进行系统更新、重构、功能新增或回滚（Rollback）时，如变更影响本文记录的技术设计、接口规范、验证方式或发布流程，必须同步更新本文件，确保规范与实际代码实现准确一致；禁止再维护内容重复的平行代理指南。

> [!IMPORTANT]
> **Release 发布与版本号确认规则**：
> 全局系统版本号统一定义于 `mathbank/__init__.py` 的 `__version__`。在执行任何 Release 打包（如 `scripts/build_release.py`）或准备打 Tag 发布新版本前，AI 代理必须**主动提醒并询问用户是否需要递增更新版本号**，确保本地版本号、便携包构建产物与 GitHub Release Tag 保持绝对一致。

> [!IMPORTANT]
> **项目路径单一来源规则**：
> 所有持久化文件和捆绑资源路径必须从 `mathbank.paths` 获取，并以 `PROJECT_ROOT` 为锚点。禁止新增依赖当前工作目录的 `./data_backup`、`os.getcwd()` 或“脚本所在目录就是数据库目录”等隐式假设，确保从任意工作目录启动服务或 CLI 都访问同一份数据。

> [!WARNING]
> **JavaScript 语法防错与浏览器兼容性警示**：前端的五个脚本文件在浏览器中级联加载。任何人在修改 JS 代码时，必须遵循以下规则：
> 1. **确保无任何语法错误**：语法错误会导致浏览器停止解析后续脚本，挂起 `DOMContentLoaded` 事件，使界面死锁。修改后建议运行 `node -c static/js/*.js` 校验。
> 2. **禁用正则后行断言**：前端代码中**严禁使用正则后行断言 `(?<!...)` 和 `(?<=...)`**，此类语法在旧版浏览器（如 Safari < 16.4）或移动端 WebView 中会触发致命的 `SyntaxError` 中断加载。必须改用捕获组或字符串拆分逻辑。
> 3. **版本号缓存击穿 (Cache Busting)**：后端在首页路由 `read_index` 中根据前端 JS/CSS/Favicon 修改时间戳自动追加版本号后缀（如 `?v=时间戳`）。开发者无需手动修改 HTML 中的版本号。
> 4. **Tailwind 自定义色彩与透明度定义**：CSS 变量含逗号分隔符时（如 `124, 58, 237`），**严禁使用 `rgb(var(--brand-xxx-rgb) / <alpha-value>)`**。必须使用 `rgba(var(--brand-xxx-rgb), <alpha-value>)` 格式以保证所有浏览器的解析兼容。

## 3. 核心业务逻辑与模块设计
### 3.1 题目收集与存储
- **题干录入**：支持多行纯文本与 LaTeX 代码混合输入，界面配备实时 KaTeX 渲染预览区。
- **插图管理**：提供图片上传与 TikZ 绘图代码输入。图片保存在本地文件系统（`static/uploads/`），数据库存储相对路径。
- **插图排版位置联动与多图复合渲染**：
  - **多模式与多插图支持**：插图在后端存储 `figure_align` 属性（支持 `right` 题干右侧、`center` 下方居中、`bottom_right` 下方居右）。
  - **全量插图抓取与预览同步**：前端与后端统一使用全量正则匹配捕获插图，多图横向弹性排列组合输出。
  - **交互弹窗切换**：在 A4 试卷预览框中点击或右击插图可弹出气泡菜单切换排版位置，并通过 `POST /api/questions/{qid}/figure_align` 持久化。
  - **解答题留白调控**：解答题支持留白高度调控。若插图设为 `bottom_right` 或 `center`，插图包含在留白空间顶侧，避免垂直叠加过长。切换为 `exam_19`（高考卷）时自动恢复紧凑布局。
- **选择题与填空题环境规范**：
  - 选择题选项统一格式化为 LaTeX `choices` 环境（`\begin{choices}` 和 `\item`），剥离原本的 A., B., C., D. 标号前缀。
  - **选择题 choices 网格对齐**：前端使用 `choices-grid` 容器与首行基线对齐，确保题干右侧括号 `（   ）` 靠右，选项独占下方 A4 栅格，且标号与首行文本基线精准对齐。
  - **选择题题干末尾括号净化**：后端与前端预编译自动物理抹除题干末尾的全角/半角空括号（如 `(\quad)`、`(   )`），防止与 TeX 模板右侧 `\paren` 宏重叠。
  - **填空题 \fillin 宏规范**：下划线统一生成与清洗规范化为 `\fillin` 宏。前端负责数学环境感知与句末标点位置自愈。
  - **HTML 解析器小于号转义**：前端预处理 KaTeX 公式时将数学环境内的 `<` 与 `>` 安全替换为 `\lt ` 与 `\gt `（禁用后行断言），防止浏览器 `innerHTML` 解析时切割 DOM 树。
  - **LaTeX tabular 表格网格渲染**：`\begin{tabular}` 自动解析转换为现代居中、带微边框的响应式 HTML5 表格，保留 LaTeX 原生源码导出。
  - **LaTeX 段落与换行规范**：双回车（`\n\n+`）代表起新段落（`<br><br>`）；显式双反斜杠（`\\\\`）代表硬换行（`<br>`）；单回车仅视为空格不打断自然段。解答题小问标号（如 `(1)`、`①`）自动前置插入段落换行。
- **教材大纲多版本预设与共存**：
  - 快捷切换人教 A 版、人教 B 版、苏教版、沪教版标准大纲预设（`mathbank/resources/curriculums/`）。
  - **活跃-镜像模式 (`question_curriculums`)**：存放题目在每套大纲中的分类镜像。主表字段反映当前活跃配置，切换大纲时后台自动运行增量迁移。
  - **小节隔离自愈**：校验并清洗非法跨版小节，防止分类下拉菜单发生混排污染。
- **全局试题序号同步 (#seq_num)**：
  - 题库卡片与 Toast 交互统一采用 SQLite 物理升序计算的纯净序号 `seq_num`（1 ~ N）展示。
  - **编辑会话状态 (`EditorState`)**：`api.js` 的 `EditorState` 是当前题目 ID、序号、草稿 ID 与编辑模式的唯一状态来源，禁止引入平行全局变量。
- **草稿箱与未保存决策流**：
  - 草稿统一存放在 LocalStorage 键 `mathbank_local_drafts`。离开未保存 Dirty 页面时提供“存入本地库/暂存草稿/离开/返回”决策流，入库后自动从草稿箱移除。

### 3.2 解答与解析模块
- **多途径解析汇总**：解答区包含手动输入、AI 智能生成（关联 OCR 上下文与引导指令）、OCR 识图、教师点评 (`review`) 与自定义标签 (`tags`) 5 个 Tab，统一汇总至编辑框。
- **OCR 预览与灯箱**：支持粘贴 (`Cmd+V`/`Ctrl+V`)、上传图片发起 OCR，提供本地预览与全局放大灯箱。
- **TikZ 几何绘图**：编辑 TikZ 代码可调用 `/api/render_tikz` 生成 PNG 预览；结合修改意见可调用 `/api/correct_tikz` 进行 AI 闭环纠错。
- **双阶段多模态识图**：单题 OCR 识别到插图时注入 `[ILLUSTRATION_BOX: ...]` 标记，后端自动擦除标记并调用高级绘图模型（`PREFER_DRAW_MODEL`）重绘 TikZ 矢量代码并编译为 PNG 静态图片追加引用。

### 3.3 异步实时备份与 AI 只读题库
- **JSON 完整备份 (`data_backup/questions_backup.json`)**：后台异步备份全表字段。
- **AI 专属只读题库 (`data_backup/questions_library.md`)**：只输出学段、章节、知识点和题干，过滤答案与点评，清洗 `\item`、`\\` 等排版命令，完全保留 `$` 公式。
- **终端检索工具 (`scripts/search_questions.py`)**：提供 CLI 工具支持模糊匹配与结构化题目拉取（运行 `python3 -m scripts.search_questions -q <关键词>`）。
- **填空题下划线迁移工具 (`scripts/migrate_fillin.py`)**：批量规范化旧下划线格式为 `\fillin` 并刷新备份。

### 3.4 题目双向关联
- 通过 `association_group_id` 进行变式题、子母题双向绑定，提供 `GET/POST/DELETE /api/questions/{id}/associated` 路由。

### 3.5 批量图片上传与 AI 智能拆卷
- **批量图片上传 (`/api/upload/batch`)**：拖拽上传多张试卷截图，限制 1–20 张、单张 10MB、总计 50MB，使用 Pillow 验证真实图片。
- **TeX 安全预处理**：只接受单文件 `.tex`（≤5MB），展开无参/单参简单宏，规范 `choices` 结构，锁定 TeX 数学环境（`$...$`、`$$...$$` 等）。
- **AI 智能拆卷 (`/api/ai/parse-paper`) 与两阶段解答**：阶段一完成切片、分类与提取；解析格式统一经 `mathbank.ai_json.parse_ai_json` 处理。勾选自动生成解答时，阶段二由前端并发队列（上限 3）调用 `/api/ai/solve` 推导无答案题目。
- **拆卷状态高亮与重置**：拆卷日志仅当前执行步骤显示高亮 (`aria-current="step"`)，进入下一阶段自动转为灰色完成态。提供“一键清除”确认重置操作。

### 3.6 存储空间自愈
- **垃圾图片清理**：删除或编辑题目发生图片变更时自动删除孤儿图片。
- **启动净化**：启动 2.5 秒后静默清理 `static/uploads/` 中创建时间超过 1 小时的孤儿图片及 `/tmp/` 临时截图。

### 3.7 启动就绪与网络容错
- **启动器自愈**：`启动题库系统.command` 自动校验 Python 依赖完整性，缺失时自动重跑安装。
- **自适应健康检查**：启动脚本每 0.5 秒探测后台 `/api/questions` 响应（最长 10 秒），接收到 200 后拉起浏览器。
- **前端静默重试与 UI 兜底**：首屏抓取异常时自动重试（间隔 1.5 秒，上限 3 次），多次失败展现带有 `[重新加载]` 按钮的错误提示面板。分类填充入口设置 DOM 空值防护。

### 3.8 PDF 试卷多模态拆解与双轨探测
- **双轨分流架构与解析策略 (`pdf-inspector`)**：
  - **阶段 0 探测与分流**：PDF 上传后支持选择解析策略（`native_preferred` 原生文字公式提取 vs `force_ocr` 全图视觉 OCR）。
  - **原生电子卷直提 (`native_preferred`)**：`TextBased` 直接毫秒级提取排版与文本流拆题（0 视觉 Token），结合 `_has_math_formula_loss` 自动校验公式完备性。若检测到 Word/MathType 特殊导出卷（公式硬转化为了内联图片导致文本丢公式），系统自动翻转 `needs_ocr = True` 平滑降级至 VLM 识图补全。
  - **全图视觉转译 (`force_ocr`)**：绕过文本直提，强制将所有页面渲染为 PyMuPDF 150DPI 图像并调用多模态 VLM 进行全图 OCR 识别与转译，兜底应对极端排版复杂或规则失效的试卷。
  - **逐页可信分流与跨页合并**：按页码提取 Markdown，无需直提的页面单独调用 VLM OCR，页标使用 `<!-- MATHBANK_PDF_PAGE:N -->` 合并且不切断跨页题目。
- **配图关联**：题目拆解默认不含配图。若原题有插图，由用户点击【手动截图】在 PDF 灯箱中框选，向 `/api/ai/manual-crop-pdf` 发送百分比坐标进行精准裁剪。
- **任务轮询与 ESC 中断**：前端轮询 `/api/tasks/{task_id}/status`，支持点击【中止拆分】或按 `ESC` 调用 `/api/tasks/{task_id}/cancel` 安全取消任务。

### 3.9 Word (.docx) 安全保真拆分与公式提取
- **公式结构化转换**：`mathbank/omml_helper.py` 处理 OMML，`mathbank/mtef_helper.py` 解析 MathType/MTEF。统一经 `normalize_word_formula_latex` 规范化。未知私用字符保留 `[公式结构待核对]`；MTEF 节点拼接自动增加字母边界空格，防止控制词粘连。
- **Word 语义保真**：解析 `word/numbering.xml` 恢复自动编号；普通段落上标、下标、下划线转化为 LaTeX/Markdown 表达；表格转为 `tabular`（支持合并单元格转义）；图片校验格式与大小。
- **公式可见性锁定协议 (`lock_visible_math`)**：拆卷前用带唯一 ID 的 `<mathbank-math>` 锁定公式，模型只返回 ID 引用，提取完成后在解题前逐字恢复原公式，ID 异常立即中断报错。

### 3.10 组卷排版工作台
- **A4 Live Preview 渲染引擎**：结合 KaTeX + 动态 DOM 模拟 A4 试卷（210mm x 297mm）。支持密封线 (`\secret`)、大/副标题双向 WYSIWYG 编辑、注意事项 (`notice`) 显隐控制与解答题留白调控。
- **面板架构**：右侧控制面板静态固定，下方 A4 画布具备独立滚动条，避免重叠。
- **拖拽与管理**：支持 HTML5 原拖拽试题卡片排序，具备侧边栏折叠及已保存试卷的数据库存档与载入管理。

### 3.11 高考级 LaTeX/PDF 编译引擎
- **试卷模板与排版**：
  - 提供 `exam`（常规）、`quiz`（小练）、`exam_19`（高考 19 题跳跃）三套模板。插图自动映射 `wrapfigure` (右侧)、`figure` (居中)、`adjustbox` (右下)。
  - 导言区注入 `\raggedbottom` 防止大题标题下方拉伸。
- **模板内置与编译缓存**：
  - `templates/exam-zh/` 全量内置 `exam-zh.cls` 及其依赖宏包，保障离线免配置编译。
  - 后端线程安全 LRU 内存缓存（容量 20）根据源码 MD5 与配图修改时间复用 PDF 字节流。
- **编译诊断与导出**：
  - XeLaTeX 启用 `-halt-on-error`；每题前写入 `% MathBank-Question-ID: <id>` 标记；日志进行 UTF-8/GBK 进程级安全平滑解码。
  - 自动补包失败时通过 `mathbank.latex_diagnostics` 和 `PREFER_PARSE_MODEL` 输出结构化诊断。支持 PDF、LaTeX ZIP 源码包及 Word ZIP 导出。

### 3.12 可编辑 Word 试卷导出
- **解耦与原生 OMML**：`mathbank.word_export_helper` 使用 python-docx 生成试卷结构，公式批量由 Pandoc 转为 Word 原生 OMML (`m:oMath`)，导出默认返回包含试卷正文与含答案解析两份文档的 `.zip` 打包。
- **降级机制**：Pandoc 缺失或转换失败时调用 XeLaTeX 栅格化为 PNG 兜底，失败显示红色 `[公式待核对：...]`。
- **字体与版面规范**：
  - 正文中文字体使用宋体 10.5pt，英文/数字使用 Times New Roman 10.5pt；主标题使用华文中宋，大题标题使用宋体 11pt 加粗。
  - OMML 变量使用 Times New Roman 斜体（加 `m:nor` 保护），数字/运算符使用正体；严禁对所有数学 run 注入 `w:sz` 防止 WPS 显示异常。
  - 页面采用 A4，四边 2.54cm，正文使用至少 18pt 行距。

### 3.13 版本检测系统
- 后端接口 `GET /api/version/check-update` 异步拉取 GitHub Releases，比对语义化版本号。
- 前端启动 1 秒静默检测，有新版本时设置齿轮亮起红点；提供专属【版本更新】控制台与版本忽略功能。

## 4. 外部 API 接入规范
- **密钥与鉴权**：读取 `.env` 密钥，修改类接口必须携带 `X-Local-Token` 头部。
- **模型配置**：
  - OCR 首选阿里百炼 `qwen3.7-flash` 或硅基流动 `Qwen/Qwen3-VL-8B-Instruct`（中转站推荐 `gpt-5.6-luna`）。
  - 阿里百炼预设按任务隔离：OCR、拆卷与分类默认 `qwen3.7-flash`，解答与绘图默认 `qwen3.7-plus`，`qwen3.8-max` 仅作为高性能可选项；旧型号不再列为预设，但既有配置与自定义模型必须继续可见且不得被静默改写。
  - **阿里百炼思考策略隔离**：仅对 `provider_code == "bailian"` 的 Qwen3.7/3.8 生效。OCR、拆卷、分类、AI 选题和 LaTeX 诊断显式关闭思考；解答服从前端开关；TikZ 绘图显式开启思考。Qwen3.7 使用 `thinking_budget`，Qwen3.8 Max 使用 `reasoning_effort=medium`，两者禁止同时发送；当前型号使用 `max_completion_tokens`，不得改变 DeepSeek、硅基流动和中转站载荷。
  - 解答 (`PREFER_SOLVE_MODEL`)、拆卷 (`PREFER_PARSE_MODEL`)、分类 (`PREFER_CLASSIFY_MODEL`) 与绘图 (`PREFER_DRAW_MODEL`) 可单独配置。

## 5. 启动诊断与双平台 Release 构建
- **启动诊断**：服务启动打印 Python 环境、PDF Inspector、PyMuPDF、XeLaTeX、Pandoc 及数据库状态。
- **打包脚本 (`scripts/build_release.py`)**：构建 Windows (`MathBank-Windows-x64.zip`，内嵌 Python 3.10 + 依赖 + `.bat`) 与 macOS (`MathBank-macOS.zip`，含 `.command`) 便携包。

## 6. 界面设计与交互规范
- **视觉与色彩**：极简教研卡片风格，支持 6 套主题（默认曜石黑 `.theme-obsidian`）。图标采用平面极简设计（Flat Minimalist）。
- **暗色模式规范**：高通透玻璃底 + 10% 品牌色透光微光与高对比文字；下拉菜单统一使用 `.glass-dropdown`；深色编辑器采用高对比选中样式（`selection:bg-indigo-600`）。
- **全局悬浮提示 (Global Fast Tooltip)**：`api.js` 事件代理接管带 `title` 或 `data-tooltip` 的元素，移入停顿 500ms 显示提示气泡，移出 0ms 隐藏，自动进行边缘碰撞检测并防重叠。
- **交互与留白**：按钮与 Tab 具备平滑过渡动画（`duration-300`），参考 Notion 注重留白与呼吸感。

## 7. 开发与运行指令
- **本地启动**：`uvicorn main:app --reload`
- **单元测试**：运行 `python3 -m pytest tests/`（必须限定在 `tests/` 目录下，防止扫描 `scratch/` 脚本）。
