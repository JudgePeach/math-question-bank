# CLAUDE.md - 本地化数学题库管理系统 (MathBank) 开发指南

## 1. 项目概述
本项目是一个本地运行的半自动化数学题库管理工作台。核心目标是通过极简的本地化部署，实现高质量图文混排数学题目（尤其是高中及更高阶数学内容）的收集、标签化管理、OCR 识图转 LaTeX、以及大模型（DeepSeek / 阿里百炼）辅助生成解析、一键分类和关联题组管理。

## 2. 核心技术栈与无构建原则
本项目追求极致流畅的用户体验和极简的本地环境配置，严格禁止引入现代复杂前端构建工具（如 Webpack/Vite/Node.js 生态）：
- **后端**：Python 3.10+ + FastAPI + Uvicorn。
- **后端渐进式模块架构**：根目录 `main.py` 保留为 `uvicorn main:app` 兼容入口；共享能力集中在 `mathbank/`。`paths.py` 是数据库、静态资源、上传、模板、备份、`.env` 与构建路径的唯一来源，`curriculums.py` 负责四套教材 JSON，`prompts.py` 负责 OCR、解题、分类、拆卷、TikZ 与 AI 组卷提示构建，`ai_providers.py` 负责纯文本模型的平台、密钥变量、API Base、真实模型名及推理强度解析。不得在路由或前端重新复制这些数据与供应商判断规则。
- **数据库**：SQLite + SQLAlchemy（轻量化本地数据库，数据存储在项目根目录下的 `./math_question_bank.db` 中）。
- **前端页面**：单页面应用 `static/index.html`（纯 HTML5 + 原生 JavaScript，无编译，秒级加载）。
- **前端样式与字体**：Tailwind CSS + FontAwesome 图标库 + Inter/Outfit Webfonts（均已下载至本地 `/static/lib` 支持 100% 离线使用与跨平台字体降级）。
- **公式渲染**：KaTeX（已下载至本地支持 100% 离线数学公式渲染），支持题干与解析框实时解析、秒级渲染。
- **前端脚本拆分**：前端 JS 采用无编译的“渐进式级联加载”架构，分模块存放在 `static/js/` 目录下（`api.js`、`editor.js`、`ocr.js`、`import.js`），保证代码结构极其清爽。
- **图文识图 (OCR)**：高精度、多通道的云端 LaTeX OCR 引擎，支持 SiliconFlow (实名送16元代金券) 和 阿里百炼 (注册享受3个月免费额度) 的双通道并发识图。
- **接口安全校验**：所有的非只读操作（POST / PUT / DELETE）在后端均有强置 `X-Local-Token` 安全令牌拦截保护，前端由 `api.js` 进行 fetch 全局劫持代理，确保本地数据防跨站越权篡改。
- **中转站模型 7:3 弹性 UI 布局与推理强度 (Reasoning Effort) 自动映射**：系统 API 设置界面针对“中转站 A”和“中转站 B”支持 7:3 分割布局（70% 宽度填写/选择模型名称，30% 宽度选择推理强度 `Default (空白)` / `Low` / `Medium` / `High` / `XHigh` / `Max`）。前端自动导出形如 `gpt-5.6-sol:high` 的模型配置，后端由 `mathbank.ai_providers` 提纯真实模型名称，解题路由再按既有规则向 JSON 请求体中静默注入 `reasoning_effort` 或 `enable_thinking` 参数。
- **路径单一来源约束**：新增文件访问必须从 `mathbank.paths` 取绝对路径并锚定 `PROJECT_ROOT`；禁止依赖 `os.getcwd()`、裸 `./data_backup` 或脚本目录推断数据库位置，保证任意工作目录启动行为一致。

---

## 3. 核心架构与业务模块

### 3.1 题目收集与级联标签系统
- **编辑与实时预览**：题干编辑区与答案终审区采用分栏设计，左侧输入 LaTeX/Markdown，右侧 KaTeX 进行试卷级精美排版和实时预览。
- **插图管理**：提供单独的图片上传窗口（亦可直接粘贴或拖拽上传）。图片仅保存在本地 `./static/uploads/` 相对路径下，不可在录入阶段固化排版（如居中等样式），排版样式由展示时动态应用。
- **选择题 choices 环境规范与填空题 \fillin 宏规范**：
  - 所有选择题在入库和存储时，选项部分必须统一格式化为 LaTeX 的 `choices` 环境（使用 `\begin{choices}` 和 `\item` 包裹，且剥离原本的 A., B., C., D. 等标号前缀）。
    - **选择题 choices 网格对齐与高度自适应 (Choice Grid Column Extraction & Vertical Alignment)**：前端 KaTeX 预渲染（`editor.js` 中的 `preprocessFormulaForKaTeX` 与 `parseMarkdownWithMath`）统一为选择题 `\begin{choices}` 环境生成带 `choices-grid` 标识的弹性网格容器。组卷工作台 A4 Live Preview (`paper.js`) 自动捕获此标识将题干与选项提取分离，实现题干右侧括号 `（   ）` 顶格靠右，选项独占下方 100% 满宽 A4 栅格。同时通过 `.choices-grid` 与 `items-baseline` 顶部/首行基线对齐规则，彻底解决了单行及多行折行选项下 A、B、C、D 序号标号与第一行文本基线完美对齐的问题，既杜绝了分式撑高顶部导致的标号偏高，又防止了选项折行时标号悬浮在多行文本中间。
  - **选择题题干末尾括号自动净化 (Choice Stem Parentheses Cleanup & Self-Healing)**：后端 `paper_helper.py` (`clean_choice_stem_parentheses`)、前端 KaTeX 预编译 (`cleanChoiceStemParentheses`) 与全量自愈脚本 (`migrate_choice_parentheses.py`) 会自动物理抹除题干末尾录入的各种全角/半角供填答用空括号（如 `(\quad)`、`(   )`、`（  ）`），防止与 LaTeX 模板右侧自动生成的 `\paren` 宏产生二次重叠。
  - **填空题 \fillin 规范与自动自愈 (Fillin Macro Standardization & Self-Healing)**：系统在多模态 OCR 识图（SiliconFlow / 阿里百炼 / 中展 AI）Prompt、试卷 AI 智能拆卷 Prompt 中均强制要求将填空题下划线生成为 `\fillin` 宏。同时在后端录入/修改/拆题逻辑中内置了 `normalize_fillin_macro` 自愈清洗器，会自动将 `______` 或 `\underline{\hspace{...}}` 等旧格式静默升级为标准的 `\fillin`。前端 `preprocessFormulaForKaTeX` 默认将纯 `\fillin` 转化为 1.5cm 标准精美下划线渲染。
  - **裸露数学环境自动自愈 (Exposed Math Environment Self-Healing)**：针对录入或识别到的裸露 LaTeX 数学环境（如 `\begin{cases}...\end{cases}`、`aligned`、`matrix` 等），前端 KaTeX 预渲染函数（`preprocessFormulaForKaTeX` 与 `parseMarkdownWithMath`）会自动识别并使用单美元符号 `$...$` 智能包裹，彻底杜绝未加 `$` 导致渲染成纯文本的异常。
  - **LaTeX/Markdown 换行规范与右侧插图顶格组装算法 (Line Break Rules & Right-Aligned Minipage Hangindent Reset)**：在 LaTeX 试卷导出中，引入了 `format_stem_paragraphs` 算法结合 `\noindent\begin{minipage}` 容器及内部 `\hangindent=0pt\setlength{\parindent}{2em}` 参数重置。消除了外层 `problem` 环境施加给 `minipage` 盒子的外层 2em 缩进偏移以及折行悬挂缩进，既保证了在 `minipage` 右侧插图窄版下同一段落内的自然续行（如 `AC=BC, D, E分别为...`）与全宽题完全一致地 **100% 绝对顶格对齐**，又使小问另起新行并保留 2em 标准首行缩进，且绝不破坏公式或坐标点。
  - **前端渲染**：前端将自动依据选项最长字符数 `maxLen` 自适应网格排版（小于等于 10 字为 1行4列，大于 10 且小于等于 24 字为 2行2列，大于 24 字为 1行1列），并自动补全 `A.`, `B.`, `C.`, `D.` 标号。
  - **AI 题库转换**：后端导出 AI 专属只读题库时，会自动将此 `choices` 环境清洗为 Markdown 标准列表 `- A.` / `- B.` 形式，防止干扰大模型。
- **级联目录体系**：
  - **学段（必修/选修）** -> **章节** -> **小节/知识点** 级联，自动下拉补全。
  - **分类必填校验**：当保存题目未选学段或章节时，页面会**平滑滚动**至对应下拉框，并触发临时的**红色高亮聚焦光晕**（`ring-2 ring-red-400 border-red-400`），动画持续 2.5 秒。
- **自定义标签顶栏展示与智能折叠**：自定义标签平移至卡片顶部右侧（紧靠难度标签），采用 items-start 与 flex-1 弹性对齐。为防标签过多破坏布局，最多直接展示 2 个标签（单个最大宽度 80px 自动截断），其余折叠至 `+N` 徽章。悬浮在徽章上瞬间唤起定制琥珀色气泡展示全部标签，且阻断点击冒泡。
- **PDF 编译 LRU 哈希缓存**：后端 `compile_tex_to_pdf` 内置基于全套 LaTeX 源码 MD5 哈希与关联插图修改时间戳的线程安全 LRU 内存缓存（容量 50）。在组卷预览与合并导出时，若 LaTeX 源码与配图未发生任何变动，直接 0ms 瞬间从内存复用已编译的 PDF 字节流，大幅降低 CPU 负载并提升合并导出响应速度。
- **教材大纲多版本预设模版 (Syllabus Preset Templates)**：系统设置中支持快捷切换**人教A版**、**人教B版**、**苏教版**和**沪教版**。四套预设统一存放在 `mathbank/resources/curriculums/A.json`、`B.json`、`S.json`、`H.json`，后端通过 `mathbank.curriculums` 加载，前端通过 `GET /api/config/curriculum-presets/{version}` 获取，禁止在 `api.js` 重复硬编码。切换只替换待保存的元数据配置，不直接改写题库数据。
- **多教材大纲身份共存与迁移系统 (Curriculum Coexistence & Migration)**：
  - **活跃-镜像模式 (Active-Mirror Pattern)**：使用 `question_curriculums` 表独立保存题目在每套大纲（`A`、`B`、`S`、`H`）中的分类投影镜像。主表 `questions` 的分类字段仅实时缓存当前活跃配置，无需重构任何查询过滤或检索代码。
  - **版本切换与自动增量迁移**：保存并切换大纲版本（`POST /api/config/metadata`）时，后端自动获取旧活跃版本和新目标版本。若版本不同，自动在后台查找目标版本中尚未分类的题目，通过“通用关键字路由算法”将原版本的分类翻译为对应新版本的章级大纲镜像（增量翻译，不覆盖用户手动修改的分类记录），最后通过 SQLite 子查询极速将目标版本的分类同步刷入 `questions` 表的主字段。
  - **跨大纲小节隔离自愈 (Knowledge Field Isolation Self-Healing)**：系统会在初始化或切换大纲时校验主表及镜像表的小节字段（`category_knowledge`），若旧大纲章名或非法小节残存在新大纲的章节下，系统会自动将其清洗置空为 `""`（即标准的默认整章），且前端取消对全局 `categoryTree` 的盲目 `.push()` 追加，彻底防止多大纲下下拉菜单选项发生跨版交叉混排污染。
- **全工作台试题序号同步 (#seq_num Sync)**：
  - 后端接口（`GET /api/questions`、`GET /api/questions/{id}`）自动基于 SQLite 物理升序索引计算全局纯净序号 `seq_num` (1 ~ N)。
  - 题库研讨工作台（`editor.js`）与组卷排版工作台（`paper.js`）均统一优先采用 `q.seq_num` 作为试题卡片与 Toast 交互的视觉编号（如 `#22`），彻底规避历史删题导致的数据库主键 ID 断号/跳号给用户带来的困扰。
  - **编辑会话状态与预览单一来源 (EditorState & Single Renderer)**：`api.js` 中的只读方法对象 `EditorState` 是当前题目 ID、纯净序号、录入时间、草稿 ID 与编辑模式的唯一状态来源，状态切换必须使用 `reset()`、`useQuestion()`、`useDraft()`、`setDraftId()` 或 `clearDraft()`，严禁重新引入平行全局变量。`editor.js` 的 `renderEditorPaperMeta()` 是右侧 `paperBadges` 与来源栏的唯一写入入口；`import.js` 等数据加载模块只能填充表单、更新 `EditorState` 并调用该渲染函数，不得自行拼接同一区域 HTML。题目详情异步返回时还必须核对请求 ID 与当前 `EditorState.questionId`，丢弃快速跨题切换产生的过期响应。这样可确保切换题型、难度、来源、标签、题目或草稿时，编号与“录入于”时间不会丢失、串题或被旧请求覆盖。
- **AI 智能组卷与教育学理推理 (AI Paper Auto-Selection with Pedagogical Reasoning)**：
  - 一键组卷功能升级调用 `PREFER_SOLVE_MODEL` 核心解题大模型，融入中学数学教学教研思维（双向细目表、知识点覆盖率与难度阶梯分布），自动从题库中挑选最适配的题目组合。
- **工作台状态无缝持久化与零闪烁首屏渲染 (Workspace State Persistence & Zero Flash)**：
  - 前端自动记录当前活跃工作台（`PaperStore.activeWorkspace`）。在页面刷新或重新打开标签页时，系统会在 DOM 初始渲染前通过 HTML `init-ws-paper` 样式控制实现无闪烁的精准还原；仅在服务器全新启动（Fresh Server Boot）时重置为默认的题库研讨工作台。


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
- **历史填空题下划线批量升级迁移工具 (`migrate_fillin.py`)**：
  - **功能用途**：扫描本地 SQLite 数据库中所有已入库题目，自动将题干中遗留的旧下划线格式（如 `______`、`\underline{...}`、`\fillin[...]`）批量规范化清洗为 100% 纯粹干净的 `\fillin` 宏。
  - **自动落盘与数据同步**：迁移完成后会自动触发 `export_database_to_files()`，同步刷新 `data_backup/questions_backup.json` 备份文件与 `data_backup/questions_library.md` AI 专属题库文件。
  - **运行指令**：在项目根目录下直接执行 `python3 migrate_fillin.py`。
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
> - **版本号自动缓存击穿 (Cache Busting)**：系统已在 Python 后端（`main.py` 的首页路由 `read_index` 中）实现自动缓存击穿机制。每次浏览器请求首页时，后端会自动获取前端 JS 文件、CSS 以及 Favicon 图标文件（`favicon.png`/`favicon.ico`）的最新修改时间戳作为版本号后缀（如 `?v=时间戳`）注入 HTML。开发者无需手动修改 HTML 中的版本号。
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
- **双阶段多模态识图与 TikZ 绘图联动**：在单题 OCR 公式识图过程中（`/api/ocr`），多模态模型如果检测到插图，会在 LaTeX 文本中植入 `[ILLUSTRATION_BOX: ...]` 定位标签。后端解析到该标签时，会自动将其剥离，并触发第二阶段联动：将整张原始题目图片和提取的 LaTeX 题干文本直接发送给高级绘图视觉大模型（由 `PREFER_DRAW_MODEL` 指定，推荐使用 `ZHONGZHAN_GPT/gpt-5.6-luna` 或 Gemini 系列；不建议使用国内模型进行 TikZ 绘图），由其绘制出 TikZ 源码，并在后台静默编译为 PNG 插图，自动追加到题干末尾（以 Markdown 图片语法 `![](/static/uploads/tikz_xxx.png)` 引用），实现一键图文公式识别与几何绘图矢量化。

### 3.9 题目双向关联与题组管理
- **双向绑定机制**：系统通过 `association_group_id` 进行关联。同组题目（变式题、子母题、一题多解等）均具备相同的 `association_group_id`。绑定两个已属于不同组的题目时，系统会在后端自动将旧组的题目批量迁移合并到新组。
- **管理操作**：前端可通过 API 动态查询同组题目、一键添加关联（合并现有组关系）以及解除当前题目的关联（安全解绑，不会导致其他关联题目失联）。

### 3.10 批量图片上传与 AI 智能拆卷/解析系统
- **批量图片分发**：用户可在前端一次性拖入多张截图，通过 `/api/upload/batch` 瞬间在后台保存，并返回形如 `[图片1]` 的占位符映射表，方便在解析文本中插入对应位置。
- **大模型文本切片与两阶段解耦拆解**：用户提交完整的试卷 LaTeX/Markdown 文本及图片映射后，`/api/ai/parse-paper` 接口调用 `.env` 配置文件中的 `PREFER_PARSE_MODEL`，并通过 `mathbank.ai_providers.resolve_text_provider` 统一解析平台、密钥、API Base 与真实模型名，再将文本按题目智能切片，自动识别其类型、章节、难度、题干与配图，并提取原版答案（秒级返回）。PDF 后台拆卷内部路径采用同一 Provider 解析规则。若勾选自动生成解答，第二阶段由前端发起并发队列（上限 3）异步调用 `/api/ai/solve` 为无答案题目平滑推导解析，彻底解决 Output Token 溢出与 HTTP 超时问题。

### 3.11 PDF 试卷多模态拆解与手动截图系统
- **切片与异步任务**：支持上传 PDF 文件，后端在后台利用 `fitz` (PyMuPDF) 将 PDF 栅格化为高清图片。利用 ThreadPoolExecutor 并行发起 VLM 多模态 OCR 转译。
- **PDF 导入规范与 Token 防溢出**：导入试卷 PDF 时，建议优先选用仅含题干（无冗长解析）的短试卷。详细解析推荐在题目拆解入库后，使用系统内置的 AI 一键推导或手动补充，避免因原文件文本过多导致大模型上下文 Token 超限。
- **配图裁剪机制（取消自动裁剪，拆解题目默认不含配图）**：由于自动识别裁切容易产生不准的误差，系统已取消自动裁剪机制，拆解出的题目默认是不包含任何图片的。若原题包含配图，必须由用户在前端点击「手动截图」弹窗，通过拖拽框选进行 100% 零误差的高精度配图关联，这确保了配图插图的高画质和 100% 零误差。
- **进度轮询与状态展示**：前端通过 `/api/upload/pdf-task` 提交文件并在右侧展现毛玻璃遮罩层与进度条，以每 1.5 秒的频率请求 `/api/tasks/{task_id}/status` 直至 `completed`。
- **手动拖拽框选截图**：每个拆解卡片均提供“手动截图”选项，点击可调出 PDF 页面查看灯箱，支持在页面图上左键点击并拖拽框选区域，向 `/api/ai/manual-crop-pdf` 发送百分比坐标进行精准的物理裁剪配图。
  - **生命周期管理与净化**：
    - **升级晋升 (Promotion)**：保存题目或更新题目时，若检测到 `/tmp/` 下的临时裁剪图，系统自动在后端将其 `shutil.move` 到 `static/uploads/` 永久保存并同步修改正文中的引用。
    - **临时清理 (Cleanup)**：如果用户点击“一键清除”或关闭拆卷面板，前端会调用 `/api/ai/clear-temp-crops` 将所有产生的未保存裁剪配图从磁盘上彻底物理删除。
    - **定时净化 (Self-Healing)**：自愈清理任务中加入对 `static/uploads/tmp/` 目录的扫描，定时物理删除修改时间超过 1 小时的孤儿或废弃临时裁剪图片。

### 3.12 组卷排版工作台与 A4 Live Preview 实时渲染引擎
- **A4 仿真纸张与 Live Preview 秒级渲染引擎**：
  - 前端无需同步等待后端 PDF 编译，纯前端结合 KaTeX + 动态 DOM 模拟真实 A4 试卷（210mm x 297mm 纸张比例）。
  - 支持渲染卷头密封线 (`\secret`)、大标题、副标题（连续空格 `&nbsp;` 与 `pre-wrap` 保持与 LaTeX `\large\bfseries` 粗体一致）、考试注意事项框 (`notice` 环境) 和大题分值信息。
  - **主副标题双向 WYSIWYG 实时编辑 (Bi-directional Title Editing)**：支持在左侧配置栏与右侧 A4 Live Preview 试卷画布上双向自由修改主标题与副标题。A4 画布节点具备 `contenteditable="true"` 属性与琥珀微光 Focus 框，副标题为空时自动展示 `+ 点击在此直接添加副标题 / 备注` 占位提示，打字过程进行增量 DOM 局部更新，做到零闪烁与零丢焦点。
  - **试卷密封线与注意事项可视化交互显隐 (Secret & Notice Interactive Toggle)**：在 Live Preview 预览中，鼠标悬浮左上角绝密标记可触发 `[移除标记]` 隐去，悬浮注意事项可触发 `[移除注意事项]` 隐去。原位置分别保留精致虚线交互占位块 `[+ 已移除绝密标记]` / `[+ 已移除注意事项]` 支持随时点击一键恢复。导出与编译 LaTeX 时根据 `show_secret` 和 `show_notice` 状态自动将 LaTeX 的 `\secret` 与 `notice` 环境整块进行注释/解注释（`% \secret` / `% \begin{notice}`）。
- **解答题留白调控 (Solution Space Control)**：
  - 在 A4 Live Preview 视图中，解答题下方实时渲染浅蓝虚线留白区，并提供快捷内联调控按钮 (`[- 1cm]`, `[- 0.5cm]`, `[+ 0.5cm]`, `[+ 1cm]`)。
  - 支持全局留白档位设定（如 `0cm` / `7cm` 等），微调值随试卷序列化存入 `cart`。
- **HTML5 原生拖拽排序与交互**：
  - 支持拖拽试题卡片（`ondragstart`, `ondragover`, `ondrop`）灵活调整卷面题目顺序。
- **侧边栏折叠伸缩与无缝工作台切换 (Collapsible Sidebar & Workspace Switcher)**：
  - 顶部导航栏提供动态 Dropdown 工作台切换（“题库研讨工作台” ↔ “组卷排版工作台”）。
  - 试卷编辑区配备左侧栏一键折叠伸缩按钮（`toggleSidebarBtn`），隐藏侧栏以释放全屏宽幅预览空间，并配合平滑 CSS 动画与图标联动。
- **已保存试卷的存档与载入管理 (Paper Management System)**：
  - 组卷结果可序列化持久化至数据库 `papers` 与 `paper_questions` 级联表中。
  - “已保存试卷”弹窗提供历史试卷一键全量载入组卷工作台、快速编译 PDF、以及删除试卷等全生命周期管理。

### 3.13 高考级 LaTeX/PDF 编译引擎与 LRU 哈希缓存
- **三套标准试卷模板**：
  - `exam`：常规试卷（含密封线、考生信息填涂框、试卷注意事项、解答题留白，插图默认位于题干右侧 `right`）。
  - `quiz`：日常小练（紧凑布局，去除了繁复的试卷注意事项与密封线，且大题标题简化为纯题型名称如“一、单选题”、“二、解答题”，隐藏具体的题数与分值说明，插图默认位于下方居右 `bottom_right`）。
  - `exam_19`：新高考 19 题专属答题卡联动卷（运用 LaTeX3 整数变量 `\g__examzh_question_index_int` 强置题号跳跃：单选 1~8、多选 9~11、填空 12~14、解答 15~19）。
- **多模式插图排版映射**：
  - 题目插图 `figure_align` 属性在导出/编译 LaTeX 时自动映射为对应宏包：`right` 映射为 `wrapfigure` 题干右侧图文双栏混排，`center` 映射为 `figure` 居中，`bottom_right` 映射为 `adjustbox`/`wrapfigure` 下方居右。
- **`\raggedbottom` 顶格防拉伸控制**：
  - 在 LaTeX 导言区自动注入 `\raggedbottom`，防止分页时大题标题与第一道题目之间出现垂直填充空白。
- **线程安全 LRU 内存编译字节缓存**：
  - 后端 `_PDF_CACHE` 容量为 20，基于全套 LaTeX 源码 MD5 哈希与关联插图文件修改时间戳进行防碰撞校验。相同源码 0ms 复用已有 PDF 字节流，极大减轻本地 CPU 编译开销。
- **多格式导出能力**：
  - 支持单 PDF 字节流快速下载，以及完整 LaTeX 源码包（`.tex` 主文件与相关配图打包为 `.zip`）导出，供线下 XeLaTeX 高级排版微调。
- **Windows 编码与宏包依赖自愈与智能诊断 (Windows Encoding & Missing Package Diagnostics)**：
  - **进程级 UTF-8/GBK 智能平滑解码**：Python 后端对 `xelatex` 的 `proc.stdout`、`proc.stderr` 及 `paper.log` 日志文件全量以 `bytes` 字节流捕获，并采用 `_safe_decode_bytes()` (优先 UTF-8，失败降级 GBK) 进行安全解码；同时在 `main.py` 与 `paper_helper.py` 启动入口配置 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，彻底清除终端日志打印非 GBK Emoji (如 `⚡`, `🚀`) 触发的 `UnicodeEncodeError` 崩溃。
  - **`exam-zh.cls` 与 21 个扩展宏包零依赖内置自愈**：项目已将 `exam-zh` 完整宏包（`exam-zh.cls` 及 `exam-zh-*.sty`）以及关联的 21 个第三方 helper 宏包（`tabularray.sty`, `ninecolors.sty`, `varwidth.sty`, `linegoal.sty`, `wrapstuff.sty`, `filehook.sty`, `environ.sty`, `trimspaces.sty`, `xeCJKfntef.sty`, `ulem.sty` 等）全量内置放置在 `templates/exam-zh/` 目录中。每次编译 PDF 或导出全量 ZIP 包时，后端会自动将其动态注入复制到临时编译目录与 Zip 压缩包中。即使 Windows 电脑安装的是旧版 TeX Live (如 2018/2021) 且完全未安装 `exam-zh` 宏包，也可实现 100% 零依赖免配置成功编译！
- **组卷工作台右侧上下解耦与静态沉稳面板 (Split Right Panel & Grounded Studio Panel)**：
  - 右侧 `paperCanvasSection` 重构为 `overflow-hidden flex flex-col` 上下完全独立分离架构。
  - 顶部控制面板为静态固定节点 (`shrink-0 mb-3`)，使用纯粹实底卡片 (`bg-white dark:bg-slate-900 border`) 替代半透明玻璃悬浮，并禁用了 `:hover` 起伏/位移动画 (`transform: none`)，移动鼠标时完全静止沉稳。
  - 下方 A4 试卷 Live Preview 画布使用独立滚动条 (`flex-1 overflow-y-auto custom-scrollbar`)，滚动试卷时试卷平滑隐入独立区域上边界，完全杜绝了向控制栏底部的重叠与遮挡。
- **左侧组卷配置栏极简高利用率重构 (Compact Configuration Panel & 12px Typography)**：
  - 左侧 `paperFilterSection` 布局精细化压缩重构，通过收紧内边距 (`p-2.5 sm:p-3 space-y-2`) 和间距 (`mb-0.5`)，在保持 `12px` (`text-xs font-semibold`) 标准清晰字号的前提下，整体高度大幅缩减 ~35%，极大释放了纵向屏幕空间给下方的题目列表。

### 3.14 现代化暗色模式视觉规范 (Modern Dark Mode & Anti-Gloom Protocol)
- **玻璃底 + 霓虹透光微光 (Vibrant Glass & Neon Tint)**：在暗色模式下，严禁使用浓重晦暗的深纯色（如 `indigo-950`, `emerald-950`, `rose-950`, `amber-950`），此类调色在深色底板上会产生浑浊阴森感。必须采用**高通透玻璃底 + 10% 品牌色霓虹透光微光**（如 `dark:bg-indigo-500/10 dark:border-indigo-500/25`），搭配高对比度亮彩文字（`indigo-200`, `emerald-200`, `amber-200`, `rose-200`）与发光 Icon。
- **下拉菜单与 Hover 悬浮态规范 (Dropdown & Hover Protocol)**：严禁在下拉菜单项或触发按钮上使用单纯的 `hover:bg-slate-100` 类，防止在暗色模式下被误判为“浅灰底 + 白字”无法识别。必须统一采用 `.glass-dropdown` 容器与 `.glass-dropdown-item` 类，暗色 Hover 状态自动映射为深石墨灰背景 (`rgba(51, 65, 85, 0.85)` Slate-700) 与高亮纯白字 (`#ffffff`)。
- **弹窗与卡片暗色覆盖层防护 (Modal & Card Overlay Protection)**：全局在 `app.css` 中建立对 `.dark .bg-slate-50\/40` ~ `/90` 等全套透明度变体及 `glass-card` 的暗色保护；同时保护试卷/A4仿真纸张 (`.a4-paper-sheet`) 在暗色模式下强制维持 `#ffffff` 背景与 `#0f172a` 文字。
- **深色文本框与编辑器高对比选中样式 (High-Contrast Selection Protocol for Dark Textareas/Editors)**：在 `app.css` 及 HTML 文本框中建立深色/代码框高对比选中机制（`selection:bg-indigo-600 selection:text-white` 与 `.bg-slate-900::selection`），彻底杜绝全局 `::selection` 暗色字体导致在深色底文本框（如自定义维度 JSON 配置编辑器）中选中文字变成黑字黑底不可读的问题。

### 3.15 系统图标与 Logo 视觉设计规范 (Flat Minimalist Icon & Logo Design Protocol)
- **平面极简设计**：项目图标与 Logo 统一采用**平面极简风格（Flat Minimalist Style）**。
- **禁忌规则**：**严禁使用廉价的 3D 浮雕、塑料质感渲染或俗套的蓝紫大渐变**。
- **标准构图与配色**：底座统一采用深藏青/黑曜石圆角矩形（Dark Slate Squircle `#0f172a`），配合极简几何线条（Golden Ratio/Monogram、线条手稿、发光高对比度元素），确保在全平台 Favicon、App 图标与 Header Logo 各种尺寸下均兼具极致辨识度与现代科技教研感。

### 3.16 多主题色彩系统与极简曜石黑预设 (Multi-Theme System & Obsidian Monochromatic Preset)
- **6大内置调色盘**：包含曜石黑 (`.theme-obsidian`)、罗兰紫 (`.theme-violet`)、深海蓝 (`.theme-ocean`)、翡翠绿 (`.theme-emerald`)、琥珀橙 (`.theme-amber`) 与玫瑰红 (`.theme-crimson`)。
- **曜石黑 (Obsidian Monochromatic)**：“曜石黑”作为旗舰极简黑白单色调色，在亮色模式下主按键、Tab 高亮与 Badge 以 `#0f172a`（Slate-900）深藏青/黑曜石为主色，搭配高纯白文字与优雅阴影，与黑白 Logo 及标志文本形成极高一致性的现代冷冽教研美学。

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

### 4.3 AI 智能解答生成与 Prompt 规范 (`/api/generate-solution`)
- **逻辑严密与极简凝练**：解答过程必须逻辑完备、推理严谨（写明定理/公理前提条件与因果推导，不能盲目跳步），但语言极简干练直奔得分点，拒绝任何多余的口水废话。
- **强制结构化板块**：解题输出必须依次包含 `\textbf{【规范解答】}` (或客观题 `\textbf{【参考答案】}` + `\textbf{【解析过程】}`)、`\textbf{【解析思路】}` (按小问或思考脉络概括破题关窍与定理应用)、`\textbf{【核心知识点】}`。

### 4.4 写入操作鉴权认证 `/api/*` (POST / PUT / DELETE)
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
运行环境使用 `pip install -r requirements.txt`；参与开发或运行测试使用：
```bash
pip install -r requirements-dev.txt
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
- **`tests/test_architecture.py`**：验证统一绝对路径、四套教材共享资源以及提示构建器边界。

#### 测试执行指令
运行测试时，**必须指定 `tests/` 目录**，以防止 Pytest 自动扫描并执行 `scratch/` 目录下的临时未就绪测试脚本：
```bash
python3 -m pytest tests/
```
