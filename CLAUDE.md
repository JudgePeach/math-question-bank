# CLAUDE.md - 本地化数学题库管理系统 (MathBank) 开发指南

## 1. 项目概述
本项目是一个本地运行的半自动化数学题库管理工作台。核心目标是通过极简的本地化部署，实现高质量图文混排数学题目（尤其是高中及更高阶数学内容）的收集、标签化管理、OCR 识图转 LaTeX、以及大模型（DeepSeek / 阿里百炼）辅助生成解析、一键分类和关联题组管理。

## 2. 核心技术栈与无构建原则
本项目追求极致流畅的用户体验和极简的本地环境配置，严格禁止引入现代复杂前端构建工具（如 Webpack/Vite/Node.js 生态）：
- **后端**：Python 3.10+ + FastAPI + Uvicorn。
- **数据库**：SQLite + SQLAlchemy（轻量化本地数据库，数据存储在项目根目录下的 `./math_question_bank.db` 中）。
- **前端页面**：单页面应用 `static/index.html`（纯 HTML5 + 原生 JavaScript，无编译，秒级加载）。
- **前端样式**：Tailwind CSS + FontAwesome 图标库（均已下载至本地 `/static/lib` 支持 100% 离线使用）。
- **公式渲染**：KaTeX（已下载至本地支持 100% 离线数学公式渲染），支持题干与解析框实时解析、秒级渲染。
- **前端脚本拆分**：前端 JS 采用无编译的“渐进式级联加载”架构，分模块存放在 `static/js/` 目录下（`api.js`、`editor.js`、`ocr.js`、`import.js`），保证代码结构极其清爽。
- **图文识图 (OCR)**：高精度、多通道的云端 LaTeX OCR 引擎，支持 SiliconFlow (Qwen3.5-4B) 和 阿里百炼 (qwen3-vl-flash) 的双通道并发识图。
- **接口安全校验**：所有的非只读操作（POST / PUT / DELETE）在后端均有强置 `X-Local-Token` 安全令牌拦截保护，前端由 `api.js` 进行 fetch 全局劫持代理，确保本地数据防跨站越权篡改。

---

## 3. 核心架构与业务模块

### 3.1 题目收集与级联标签系统
- **编辑与实时预览**：题干编辑区与答案终审区采用分栏设计，左侧输入 LaTeX/Markdown，右侧 KaTeX 进行试卷级精美排版和实时预览。
- **插图管理**：提供单独的图片上传窗口（亦可直接粘贴或拖拽上传）。图片仅保存在本地 `./static/uploads/` 相对路径下，不可在录入阶段固化排版（如居中等样式），排版样式由展示时动态应用。
- **级联目录体系**：
  - **学段（必修/选修）** -> **章节** -> **小节/知识点** 级联，自动下拉补全。
  - **分类必填校验**：当保存题目未选学段或章节时，页面会**平滑滚动**至对应下拉框，并触发临时的**红色高亮聚焦光晕**（`ring-2 ring-red-400 border-red-400`），动画持续 2.5 秒。
- **自定义标签顶栏展示与智能折叠**：自定义标签平移至卡片顶部右侧（紧靠难度标签），采用 items-start 与 flex-1 弹性对齐。为防标签过多破坏布局，最多直接展示 2 个标签（单个最大宽度 80px 自动截断），其余折叠至 `+N` 徽章。悬浮在徽章上瞬间唤起定制琥珀色气泡展示全部标签，且阻断点击冒泡。

### 3.2 草稿箱与三选项决策流
侧边栏包含双标签卡片切换，无缝管理两个数据源：
1. **本地题库 (SQLite Core)**：直接存储于本地持久化 SQLite。
2. **草稿箱 (LocalStorage)**：直接存储于浏览器 LocalStorage 键名 `mathbank_local_drafts` 下。
- **未保存决策弹窗 (3 Options)**：当编辑器内容被修改（Dirty 状态）且用户尝试切换题目、切换草稿或新建录入时，会弹出定制的毛玻璃确认框：
  - **存入本地库 (正式题库)**：触发章节格式校验，直接存入持久化 SQLite。
  - **暂存至草稿箱**：免校验直接序列化进本地草稿，并在侧边栏草稿动态角标中递增。
  - **直接离开 (不保存)**：直接抛弃当前编辑器中的未保存更改。
  - **返回编辑**：保持编辑器不变。
- **零误差 Dirty 判定**：通过在数据载入/清空完成后，调用 `backupEditorState(id, draftId)` 直接读取最终 DOM 节点值作为 `originalQuestionState` 原始对比指针，规避由于级联下拉动态更新所造成的差异误判。
- **草稿自动收纳**：当草稿箱中的草稿成功在 SQLite 中“存入本地库”后，系统会自动将其从 LocalStorage 中移除并清空。

### 3.3 高级 OCR 极速预览与灯箱交互系统
- **瞬时本地预览**：上传图片或粘贴图片后，前端通过 `FileReader` 瞬间渲染图片预览，并在下方展示“正在解析......”状态，完全不遮挡或隐藏拖拽上传框。
- **灯箱放大（Lightbox）**：OCR 解析完成或加载完毕后，**单击预览图即可平滑弹出半透明遮罩层放大原图**；再次点击原图或遮罩层可平滑收回。
- **剪贴板监听（Command+V）**：支持在页面任意位置直接按 `Command+V` 粘贴图片进行 OCR 识别，或随时点击预览图周围的上传区上传新图片进行覆盖重传，无需手动清理旧缓存。

### 3.4 异步、线程安全的实时备份与 AI 专属只读题库系统
当进行题目的新增、更新、删除、关联、解除关联等任何写操作时，系统会通过 FastAPI 的 `BackgroundTasks` 在后台异步触发同步逻辑，自动在后台重新生成两个备份文件，完全不阻塞网页端主线程：
1. **JSON 完整备份 (`data_backup/questions_backup.json`)**：完整备份数据库，用于版本控制及安全存档。
2. **AI 专属只读题库 (`data_backup/questions_library.md`)**：
   - **安全防泄露**：只输出学段、章节、知识点、题干、插图与基础元数据，**彻底过滤答案与解析**，防 AI 在备课或生成练习时发生答案泄露或产生解析干扰。
   - **排版命令自动清洗**：自动将原题中的 `\item`、`\begin{itemize}`、`\\`、`\underline` 等 Markdown 不兼容/对 AI 造成阅读噪点的排版命令**转换为标准 Markdown 列表和纯净下划线**。
   - **数学公式 100% 保留**：完好保留夹在 `$` 和 `$$` 之间的专业数学公式。
   - **语义检索优化**：提供精美的全局目录大纲树，便于普通在线 AI（如 Web 版 ChatGPT）作为静态 RAG 知识库上传使用。
- **AI 本地终端数据库智能检索系统 (`search_questions.py`)**：
  - **极致的 AI 终端交互通道**：针对拥有“执行终端命令”权限的本地 AI 智能体（如 Claude Code、Cursor、Cline 等），直接加载巨大的静态 Markdown 极其低效且昂贵。
  - **交互工作流**：系统在根目录设计了专门的 `search_questions.py` CLI 查询工具。AI 通过直接在控制台运行 `python3 search_questions.py -q <关键词>`，即可利用底层的 SQLite 索引在毫秒级模糊筛选出最贴切的题目列表（学段、章节、知识点或题干包含关键词均可自动匹配），默认单次返回限制为 50 道（使用 `-n -1` 可获取全部匹配）。
  - **关键选项**：
    - `-q`, `--query`：模糊检索词。
    - `-n`, `--limit`：返回数量上限（默认值：`50`，`-1` 代表无上限）。
    - `-a`, `--with-answers`：是否携带答案与详细解析（默认隐藏以防 AI 答案泄露）。
    - `-t`, `--type`：过滤特定题型 (`single_choice`, `multi_choice`, `fill_in_blank`, `detailed_answer`)。
    - `-d`, `--difficulty`：过滤难度等级 (`easy`, `medium`, `hard`)。
    - `-r`, `--related-to`：查询与特定 ID 题目发生双向关联的全部题目。
- **并发写保护锁 (`threading.Lock`)**：后台写盘操作被全局线程锁保护。即使连续高频修改，写入任务也会在后台自动排队执行，绝对防止多线程冲突带来的文件损坏。

### 3.5 存储空间自动净化与自愈系统
系统实现了完备的存储自愈（Self-Healing）和零垃圾残留机制，全面解决本地运行由于插图导致的磁盘空间虚高：
- **删除即清理**：删除题目时，系统会自动将该题所绑定在 `static/uploads/` 下的所有物理图片文件一并删除。
- **修改即清理**：编辑/修改题目时，系统会自动对比修改前后的插图列表，自动从本地磁盘上物理删除所有被用户删掉、弃用的旧图片文件。
- **启动自动静默净化（孤儿图片）**：在每次服务启动 2.5 秒后，系统会自动在后台静默运行一次对 `static/uploads/` 的扫描净化。自动将那些由于编辑中途取消、关闭网页、或早期手动测试残留的“未被任何题目引用的孤儿图片”物理删除。
  - **1小时安全防误删线**：净化任务极度智能，只删除文件创建时间超过 1 小时的孤儿图片，完美避免误杀用户当前正在录入但尚未保存的临时上传插图。
  - **极速 $O(1)$ 哈希检索**：采用 Python 哈希集合比对算法，即便本地有上万道题目，整个净化耗时仅需十几毫秒，彻底消除对系统性能的影响。

### 3.6 前端 JS 模块化加载流、浏览器兼容性与缓存机制
前端的交互代码已完全拆分到 `static/js/` 下，并在 `static/index.html` 中按以下级联顺序加载：
1. **`api.js`**：API 请求管理、配置读取及 Fetch token 全局代理。
2. **`editor.js`**：KaTeX 渲染预处理、LaTeX 排版解析及 Markdown 渲染逻辑。
3. **`ocr.js`**：剪贴板粘贴、拖拽、上传、灯箱放大等图像处理逻辑。
4. **`import.js`**：DOM 启动监听、页面级渲染绑定与草稿/题库列表业务中枢。

> [!IMPORTANT]
> **文档指南同步更新规则**：
> 在进行任何系统更新、重构、功能新增或回滚（Rollback）操作时，AI 代理与开发者**必须同步更新 `AGENTS.md` 和 `CLAUDE.md`**，确保两个文档中记录的技术设计、接口规范与实际代码实现 100% 准确一致，严防信息滞后。

> [!WARNING]
> - **禁用正则后行断言**：为了确保与旧版本 Safari / 移动端 WebView 的极致兼容，前端代码中**严禁使用正则后行断言 `(?<!...)` 与 `(?<=...)`**，此类语法在不支持的设备上会导致 fatal `SyntaxError` 并挂起 `DOMContentLoaded`。必须使用捕获组配合 callback 或普通字符比对进行替代。
> - **版本号自动缓存击穿 (Cache Busting)**：系统已在 Python 后端（`main.py` 的首页路由 `read_index` 中）实现自动缓存击穿机制。每次浏览器请求首页时，后端会自动获取前端 JS 文件的最新修改时间戳作为版本号后缀（如 `?v=时间戳`）注入 HTML。开发者无需手动修改 HTML 中的版本号。
> - **Tailwind 色彩 Alpha 透明度适配**：在 `tailwind.config` 中扩展自定义颜色（如品牌色 `brand` 等）时，由于颜色变量中带逗号（如 `124, 58, 237`），**严禁写成 `rgb(var(--brand-xxx-rgb) / <alpha-value>)`** 语法。这类混用语法会导致浏览器在解析带透明度修饰符的类名时判定为无效规则，最终按钮背景将不可见。必须写成 `rgba(var(--brand-xxx-rgb), <alpha-value>)` 格式以确保高兼容。

### 3.7 启动端口竞态自愈与前端级联防挂起重试机制
为了彻底杜绝由于本地多进程拉起时序不一致（Uvicorn 尚未完全就绪但浏览器已抢先加载）导致的 API 请求报错 (502 / TCP Connection Refused) 以及前端 JS 未捕获异常引起的首屏死锁，系统实现了以下稳定性架构：
- **自适应健康检查就绪检测**：在 `启动题库系统.command` 脚本中，舍弃了不可控的 `sleep 1.5` 硬编码延迟。改为启动服务后以每 `0.5` 秒一次的频率轻量化探测后台 `/api/questions` 响应。一旦探测到 `200` 状态码（代表后端完全准备就绪），才会瞬间拉起浏览器，最长等待 10 秒。
- **前端后台自适应静默重试 (Silent Auto-Retry)**：首屏在 `DOMContentLoaded` 事件中触发的 `/api/categories` 和 `/api/questions` 抓取加入了严格的 `.catch()`。当检测到请求异常时，前端会自动执行后台静默重新加载，每次间隔 1.5 秒，上限 3 次，使用户对瞬间的时序偏差达到“无感就绪”。
- **友好 UI 容错兜底与重新加载**：如果多次重试均失败，前端会捕获异常并拦截 promise 抛出，同时在左侧题库区渲染成精致的“连接题库列表失败”红字提示面板，提供一键 `[重新加载]` 按钮，允许用户手动触发重新拉取。
- **Dropdown 防御性校验**：所有在首屏数据装载前会触发的分类填充操作（如 `populateCategoryDropdowns` 与 `populateFilterDropdowns`），其入口处均内置了健壮的 DOM 及 categoryTree 级联数据空判定安全防护，杜绝由于异步时序不同步产生的页面挂起。

### 3.8 TikZ 几何绘图与智能纠错工作流
- **自动编译与预览**：编辑器下方集成了 TikZ 代码专属编辑和预览区。前端会将 TikZ 代码通过 `/api/render_tikz` 送往后端，后端在本地调用编译链转换为 PNG 并返回相对路径，在前端实现无缝的几何图像秒级预览。
- **AI 几何绘图与纠错**：支持人工与大模型闭环协同。遇到几何图题，AI 可优先直接生成 TikZ 代码编译。如遇到 TikZ 语法编译报错，用户可写入“指导意见”调用 `/api/correct_tikz` 交由高级大模型进行智能纠正，修复编译报错直至绘图完美。
- **双阶段多模态识图与 TikZ 绘图联动**：在单题 OCR 公式识图过程中（`/api/ocr`），多模态模型如果检测到插图，会在 LaTeX 文本中植入 `[ILLUSTRATION_BOX: ...]` 定位标签。后端解析到该标签时，会自动将其剥离，并触发第二阶段联动：将整张原始题目图片和提取的 LaTeX 题干文本直接发送给高级绘图视觉大模型（由 `PREFER_DRAW_MODEL` 指定，如 GPT-5.5 / Qwen-VL-32B），由其绘制出 TikZ 源码，并在后台静默编译为 PNG 插图，自动追加到题干末尾（以 Markdown 图片语法 `![](/static/uploads/tikz_xxx.png)` 引用），实现一键图文公式识别与几何绘图矢量化。

### 3.9 题目双向关联与题组管理
- **双向绑定机制**：系统通过 `association_group_id` 进行关联。同组题目（变式题、子母题、一题多解等）均具备相同的 `association_group_id`。绑定两个已属于不同组的题目时，系统会在后端自动将旧组的题目批量迁移合并到新组。
- **管理操作**：前端可通过 API 动态查询同组题目、一键添加关联（合并现有组关系）以及解除当前题目的关联（安全解绑，不会导致其他关联题目失联）。

### 3.10 批量图片上传与 AI 智能拆卷/解析系统
- **批量图片分发**：用户可在前端一次性拖入多张截图，通过 `/api/upload/batch` 瞬间在后台保存，并返回形如 `[图片1]` 的占位符映射表，方便在解析文本中插入对应位置。
- **大模型文本切片与拆解**：用户提交完整的试卷 LaTeX/Markdown 文本及图片映射后，`/api/ai/parse-paper` 接口调用 `.env` 配置文件中的 `PREFER_PARSE_MODEL` 将文本按题目智能切片，自动识别其类型、章节、难度、题干与配图，并可选择是否同步生成详细的解答解析，最终整卷产出结构化 JSON 存入草稿箱或导入题库。

### 3.11 PDF 试卷多模态拆解与手动截图系统
- **切片与异步任务**：支持上传 PDF 文件，后端在后台利用 `fitz` (PyMuPDF) 将 PDF 栅格化为高清图片。利用 ThreadPoolExecutor 并行发起 VLM 多模态 OCR 转译。
- **配图裁剪机制（取消自动裁剪，拆解题目默认不含配图）**：由于自动识别裁切容易产生不准的误差，系统已取消自动裁剪机制，拆解出的题目默认是不包含任何图片的。若原题包含配图，必须由用户在前端点击「手动截图」弹窗，通过拖拽框选进行 100% 零误差的高精度配图关联，这确保了配图插图的高画质和 100% 零误差。
- **进度轮询与状态展示**：前端通过 `/api/upload/pdf-task` 提交文件并在右侧展现毛玻璃遮罩层与进度条，以每 1.5 秒的频率请求 `/api/tasks/{task_id}/status` 直至 `completed`。
- **手动拖拽框选截图**：每个拆解卡片均提供“手动截图”选项，点击可调出 PDF 页面查看灯箱，支持在页面图上左键点击并拖拽框选区域，向 `/api/ai/manual-crop-pdf` 发送百分比坐标进行精准的物理裁剪配图。
- **生命周期管理与净化**：
  - **升级晋升 (Promotion)**：保存题目或更新题目时，若检测到 `/tmp/` 下的临时裁剪图，系统自动在后端将其 `shutil.move` 到 `static/uploads/` 永久保存并同步修改正文中的引用。
  - **临时清理 (Cleanup)**：如果用户点击“一键清除”或关闭拆卷面板，前端会调用 `/api/ai/clear-temp-crops` 将所有产生的未保存裁剪配图从磁盘上彻底物理删除。
  - **定时净化 (Self-Healing)**：自愈清理任务中加入对 `static/uploads/tmp/` 目录的扫描，定时物理删除修改时间超过 1 小时的孤儿或废弃临时裁剪图片。

---

## 4. 接口规范与参数配置

### 4.1 云端多通道 OCR 接口 `/api/ocr`
- **自适应切边**：后端自动调用自适应图像去噪和切边预处理。
- **接口参数**：接收 `file` 表单数据和可选参数 `engine`（取值：`default`、`siliconflow`、`ali_bailian`）。
- **默认引擎**：
  - 未指定引擎时，读取 `.env` 中 `OCR_PREFER_ENGINE` 配置（默认值：`siliconflow`）。

### 4.2 大模型解答接口 `/api/ai/solve`
- **接口参数**：接收 `content` (题干), `question_type`, `ocr_result` (可选，已有的 OCR 解析/草稿内容), `custom_prompt` (可选，用户补充引导指令), `thinking` (是否开启深度思考), `model`。
- **OCR 整合与引导机制**：若提供了 `ocr_result`，AI 将基于该解析草稿进行润色、修正公式错误并补全步骤；同时，AI 会根据 `custom_prompt` 里的用户指令（如简化/细化等）调整输出风格。
- **默认模型与多源参数配置**：
  - 解题模型默认读取自 `.env` 中的 `PREFER_SOLVE_MODEL`（默认值：`deepseek-v4-pro`）。
  - 试卷智能拆解模型默认读取自 `.env` 中的 `PREFER_PARSE_MODEL`（默认值：`deepseek-v4-flash`）。
  - 题目分类模型默认读取自 `.env` 中的 `PREFER_CLASSIFY_MODEL`（默认值：`deepseek-v4-flash`）。
  - 高级 TikZ 绘图与纠错模型默认读取自 `.env` 中的 `PREFER_DRAW_MODEL`（默认值：`Qwen/Qwen3-VL-32B-Instruct`）。
  - 若调用 DeepSeek 引擎，需配置 `DEEPSEEK_API_KEY`；若调用通义千问系列（如 `qwen-max`），则会从 `ALI_BAILIAN_API_KEY` 读取秘钥；若指定 `ZHONGZHAN_GPT` / `ZHONGZHAN_CLAUDE` 专属前缀，则会自动解析并调用对应的中转站 API（从 `ZHONGZHAN_GPT_API_KEY` / `ZHONGZHAN_CLAUDE_API_KEY` 获取密钥），支持多源、跨平台的 AI 解题与多模态绘图推理。
  - `max_tokens` 强置为 **`8192`**，预防由于复杂的数学思维链（Thinking Chain）导致 Token 溢出使得最终 LaTeX 答案被强行切断。

### 4.3 写入操作鉴权认证 `/api/*` (POST / PUT / DELETE)
- 所有对题库、草稿箱和系统配置进行新增、更新、删除的写操作接口均需安全鉴权。
- **鉴权核心机制**：后端会在第一次连接或通过特定校验后生成/验证 Local Token。前端在发送 POST/PUT/DELETE 请求时，必须在请求头中附带 `X-Local-Token` 字段，且内容需与后端的 `LOCAL_TOKEN` 吻合。
- 前端 `api.js` 中重写了 `window.fetch` 方法，在发送非 GET 请求时自动附加对应的 Token，使得该过程对业务开发完全透明。

### 4.4 TikZ 编译与 AI 纠错接口
- **编译预览 (`/api/render_tikz`)**：POST 请求，参数为 `tikz_code`。后端通过本地 LaTeX 环境编译后，返回预览图片的相对路径。
- **AI 智能纠错 (`/api/correct_tikz`)**：POST 请求，接收 `tikz_code`、`error_message`（编译报错信息，可选）与 `guidance`（用户修改指导意见，可选），由高级大模型（如 `qwen-max` 或中转站模型）智能返回修改后的 TikZ 完整代码。

### 4.5 批量图片上传与 AI 智能拆卷接口
- **批量上传 (`/api/upload/batch`)**：POST 请求，接收 `files: List[UploadFile]`，在 `static/uploads/` 保存并返回图片文件名与其对应的临时标签。
- **智能拆解 (`/api/ai/parse-paper`)**：POST 请求，参数接收 `latex_content` (试卷文本), `paper_title` (试卷名称), `image_mapping_json` (图片映射 JSON), `generate_answers` ("true"/"false")。后端根据 `.env` 中 `PREFER_PARSE_MODEL` 调用 AI 将试卷源码进行切片并结构化导出。

### 4.6 题目关联管理接口
- **获取关联 (`/api/questions/{question_id}/associated`)**：GET 请求，返回与当前题目相同 `association_group_id` 的所有题目。
- **绑定关联 (`/api/questions/{question_id}/associate`)**：POST 请求，参数接收 `target_id: int`（待关联的题目 ID），自动在后端进行关联组分配和旧组关系的安全合并。
- **解除关联 (`/api/questions/{question_id}/associated`)**：DELETE 请求，将当前题目从所属的关联组中安全移出。

### 4.7 代理绕过与网络稳定性 (Robust Networking)
- **原理与实现**：当系统调用国内大模型接口（阿里百炼、SiliconFlow 等）时，如遭遇本地代理引发的 Refused 或超时报错，后端在 `main.py` 的网络访问函数 `robust_request_post` / `robust_request_get` 中会自动清除当前环境变量中的系统代理设置并进行直连重试，确保数学题库系统的核心识图与解题服务高可用。

---

## 5. 开发与运行指令

### 5.1 依赖项安装
在终端中执行以下命令安装运行本项目所需的所有 Python 环境依赖（已彻底剔除臃肿且容易报错的本地 `pix2text` 相关库）：
```bash
pip install fastapi uvicorn sqlalchemy python-multipart python-dotenv requests pillow pytest
```

### 5.2 启动开发服务
使用以下命令启动支持热重载（Auto-reload）的本地 Web 服务：
```bash
uvicorn main:app --reload
```
或直接在 Finder 中双击运行工作台根目录下的 **`启动题库系统.command`**。该脚本会使用自适应健康检查技术在后台以每 0.5 秒一次的频率持续探测主服务就绪状态（直至 `/api/questions` 返回 HTTP 200，上限 10 秒），一旦检测到服务可用即瞬间拉起默认浏览器展示 MathBank UI，实现极其稳定可靠的“就绪秒开”体验。

### 5.3 代理与超时排查
如果 API 请求出现超时或网络受限，可直接关闭当前的 Terminal 终端窗口并双击运行根目录下的 `启动题库系统.command`。重新拉期的 Python 进程将自动读取 macOS 最新的系统级代理配置，确保 API 畅联。

### 5.4 单元测试套件与执行
本项目配备了高可用且完全隔离的单元测试套件，文件结构位于 `tests/` 目录：
- **`tests/conftest.py`**：利用 SQLAlchemy `StaticPool` 和 SQLite 内存数据库 (`sqlite:///:memory:`) 自动搭建高度隔离的独立测试数据库环境，覆盖 FastAPI `get_db` 依赖注入，绝不污染本地持久化 `.db`。
- **`tests/test_database.py`**：测试数据库模型 `Question` CRUD、字典序列化、以及面向 AI 安全的摘要输出。
- **`tests/test_api.py`**：测试 API 的写鉴权逻辑、设置查询、分类聚合、统计计算及题目完整 CRUD 交互。
- **`tests/test_sync.py`**：测试 AI 排版 Markdown 清洗算法、并发写保护后台文件备份、以及 1 小时安全防错垃圾图片清理系统。

#### 测试执行指令
运行测试时，**必须指定 `tests/` 目录**，以防止 Pytest 自动扫描并执行 `scratch/` 目录下的临时未就绪测试脚本：
```bash
PYTHONPATH=. pytest tests/
```