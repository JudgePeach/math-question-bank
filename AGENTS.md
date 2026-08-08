# AGENTS.md - 本地化数学题库管理系统 开发与 AI 代理指南

## 1. 项目概述
本项目是一个本地运行的半自动化数学题库管理工作台。核心目标是通过极简的本地化部署，实现高质量图文混排数学题目（尤其是高中及更高阶数学内容）的收集、标签化管理、OCR 识别以及 AI 辅助生成解析。

## 2. 核心技术栈
本项目追求极简配置与极致体验，严格遵循以下技术选型，**不要引入复杂的现代前端构建工具（如 Webpack/Vite/Node.js 生态）**：
- **后端**：Python + FastAPI。
- **后端渐进式模块架构**：根目录 `main.py` 继续作为 `uvicorn main:app` 兼容入口；后端领域能力统一集中在 `mathbank/`。`database.py` 提供 SQLite ORM 与 Session，`paper_helper.py` 提供 LaTeX/PDF 编译排版，`sync_helper.py` 提供备份与 AI 题库导出，`paths.py` 统一锚定数据库、静态资源、上传、模板、备份、环境变量和构建路径，`curriculums.py` 加载四套教材 JSON，`prompts.py` 提供 OCR、解题、分类、拆卷、TikZ 与 AI 组卷的纯提示构建器，`ai_providers.py` 统一解析模型供应商，`ai_http.py` 统一负责 AI HTTP 请求，`ai_json.py` 统一解析 AI 结构化输出。运维、迁移、检索与 Release 工具统一位于 `scripts/`，从项目根目录使用 `python3 -m scripts.<模块名>` 运行。严禁重新在根目录新增业务模块或复制供应商判断规则。
- **数据库**：SQLite + SQLAlchemy（轻量级，数据存储在本地 `.db` 文件中）。
- **前端页面**：纯 HTML + 原生 JavaScript。
- **前端脚本拆分**：前端 JS 采用无编译的“渐进式级联加载”架构，分模块存放在 `static/js/` 目录下（`api.js`、`editor.js`、`ocr.js`、`import.js`），加载顺序严格依存，不允许产生任何编译及捆绑动作。
- **前端样式与字体**：Tailwind CSS + FontAwesome 图标库 + Inter/Outfit 字体包（均已下载至本地 `/static/lib` 支持 100% 离线使用与跨平台系统降级）。
- **公式渲染**：KaTeX（已下载至本地支持 100% 离线数学公式渲染），必须支持题干与解析框实时解析、秒级渲染。
- **中转站模型 7:3 弹性 UI 布局与 Reasoning Effort 自动解析**：在系统 API 设置中选择中转站平台（`zhongzhan_gpt` 或 `zhongzhan_claude`）时，模型输入区域自动转换为 7:3 弹性比例（70% 模型名称，30% 推理强度 `Default (空白)` / `Low` / `Medium` / `High` / `XHigh` / `Max`）。后端由 `mathbank.ai_providers.parse_model_and_effort` 自动提取纯净模型名称；`/api/ai/solve` 再按现有请求规则静默注入 `reasoning_effort` 或 `enable_thinking` 参数。

> [!IMPORTANT]
> **文档指南同步更新规则**：
> 在进行任何系统更新、重构、功能新增或回滚（Rollback）操作时，AI 代理与开发者**必须同步更新 `AGENTS.md` 和 `CLAUDE.md`**，确保两个文档中记录的技术设计、接口规范与实际代码实现 100% 准确一致，严防信息滞后。

> [!IMPORTANT]
> **项目路径单一来源规则**：
> 所有持久化文件和捆绑资源路径必须从 `mathbank.paths` 获取，并以 `PROJECT_ROOT` 为锚点。禁止新增依赖当前工作目录的 `./data_backup`、`os.getcwd()` 或“脚本所在目录就是数据库目录”等隐式假设，确保从任意工作目录启动服务或 CLI 都访问同一份数据。

> [!WARNING]
> **JavaScript 语法防错与浏览器兼容性警示**：前端的四个脚本文件在浏览器中级联加载。任何人在修改 JS 代码时，必须遵循以下规则：
> 1. **确保无任何语法错误**：因为语法错误会直接导致浏览器停止解析后续脚本，挂起 `DOMContentLoaded` 事件，从而使左侧栏永久死锁在“正在获取题库...”状态。推荐修改后运行 `node -c static/js/*.js` 进行验证。
> 2. **禁用正则后行断言**：在前端代码中**严禁使用正则后行断言 `(?<!...)` 和 `(?<=...)`**，此类语法在旧版浏览器（如 Safari < 16.4）或移动端 WebView 中会触发致命的 `SyntaxError` 中断加载。必须改用捕获组或字符串拆分逻辑。
> 3. **版本号缓存击穿 (Cache Busting)**：系统已在 Python 后端（`main.py` 的首页路由 `read_index` 中）实现自动缓存击穿机制。每次浏览器请求首页时，后端会自动获取前端 JS 文件、CSS 以及 Favicon 图标文件（`favicon.png`/`favicon.ico`）的最新修改时间戳作为版本号后缀（如 `?v=时间戳`）注入 HTML。开发者无需手动修改 HTML 中的版本号。
> 4. **Tailwind 自定义色彩与透明度定义**：在 `static/index.html` 的 `tailwind.config` 中配置色彩时，由于 CSS 变量中通常含有逗号分隔符（例如 `124, 58, 237`），**严禁使用 `rgb(var(--brand-xxx-rgb) / <alpha-value>)`**。此类混合语法在部分浏览器中会被判定为无效规则，导致带透明度的按钮背景完全消失/变成白底白字看不清。必须使用 `rgba(var(--brand-xxx-rgb), <alpha-value>)` 格式以保证所有浏览器的解析兼容。

## 3. 核心业务逻辑与模块设计
### 3.1 题目收集与存储
- **题干录入**：支持多行纯文本与 LaTeX 代码混合输入，界面需配备实时渲染的预览区。
- **插图管理**：提供单独的图片/插图上传窗口（或直接输入 TikZ 绘图代码）。图片仅保存在本地文件系统（`static/uploads/`），数据库中仅存储相对路径。
- **插图排版位置联动与多图复合渲染 (Figure Placement Sync & Multi-Figure Rendering)**：
  - **多模式与多插图支持**：支持题目包含多张图片或 TikZ 几何绘图与上传图片混合的复合插图模式。插图在后端存储 `figure_align` 属性（支持 `right` 常规试卷/高考卷默认题干右侧、`center` 下方居中、`bottom_right` 日常小练默认下方居右）。
  - **全量插图抓取与实时预览同步**：前端 `paper.js`（`formatQuestionContentHtml`）与后端 `mathbank/paper_helper.py` 完美升级为全量匹配 `[...matchAll(/!\[.*?\]\(([^)]+)\)/g)]`，取消了单匹配丢图问题。多图自动横向弹性排列组合在插图框内（支持按钮标识如 `题干右侧 (2图)`）。同时 LaTeX 编译与导出全量包裹多图并存输出。
  - **交互弹窗切换**：用户在右侧 A4 试卷预览框中点击或右击插图（及位置指示按钮）时，会弹出定制的气泡菜单允许实时切换这三种排版位置，并通过 `POST /api/questions/{qid}/figure_align` 实时持久化保存至数据库；左侧试题库卡片保持纯净展示，不渲染位置调控按钮。
  - **PDF 编译 LRU 哈希缓存与高考预设题号跳跃**：后端 `compile_tex_to_pdf` 内置基于全套 LaTeX 源码 MD5 哈希与关联插图修改时间戳的线程安全 LRU 内存缓存（容量 50）。在组卷预览与合并导出时，若 LaTeX 源码与配图未发生任何变动，直接 0ms 瞬间从内存复用已编译的 PDF 字节流，大幅降低 CPU 负载并提升合并导出响应速度。同时针对 `exam_19` (19题高考卷含答题卡预设)，通过设置 `exam-zh` 的 LaTeX3 全局整数变量 `\g__examzh_question_index_int`，使编译出的 PDF 题号按大型高考规范精准跳跃（单选为 1、多选为 9、填空为 12、解答为 15），与前端 A4 Live Preview 及 A3 答题卡完美联动。
  - **解答题留白调控 (Detailed Answer Solution Space Control)**：针对无独立答题卡的试卷类型（`exam` 试卷与 `quiz` 小练），系统支持对 `detailed_answer`（解答题）进行留白高度调控。留白计算统一自题干文字结束算起，若插图设为 `bottom_right` 或 `center`，插图自动包含在留白空间（如 `7.0 cm`）内部顶侧，避免留白垂直叠加过长。前端 A4 实时预览与 LaTeX 导出（`\smash` 嵌入）均精准同步此算力。若切换为 `exam_19`（高考卷含答题卡），试卷正文自动恢复紧凑布局（留白归零）。
  - **选择题 choices 环境规范与填空题 \fillin 宏规范**：
    - 所有选择题在入库和存储时，选项部分必须统一格式化为 LaTeX 的 `choices` 环境（使用 `\begin{choices}` 和 `\item` 包裹，且剥离原本的 A., B., C., D. 等标号前缀）。
    - **选择题 choices 网格对齐与高度自适应 (Choice Grid Column Extraction & Vertical Alignment)**：前端 KaTeX 预渲染（`editor.js` 中的 `preprocessFormulaForKaTeX` 与 `parseMarkdownWithMath`）统一为选择题 `\begin{choices}` 环境生成带 `choices-grid` 标识的弹性网格容器。组卷工作台 A4 Live Preview (`paper.js`) 自动捕获此标识将题干与选项提取分离，实现题干右侧括号 `（   ）` 顶格靠右，选项独占下方 100% 满宽 A4 栅格。同时通过 `.choices-grid` 与 `items-baseline` 顶部/首行基线对齐规则，彻底解决了单行及多行折行选项下 A、B、C、D 序号标号与第一行文本基线完美对齐的问题，既杜绝了分式撑高顶部导致的标号偏高，又防止了选项折行时标号悬浮在多行文本中间。
    - **选择题题干末尾括号自动净化 (Choice Stem Parentheses Cleanup & Self-Healing)**：后端 `mathbank/paper_helper.py` (`clean_choice_stem_parentheses`)、前端 KaTeX 预编译 (`cleanChoiceStemParentheses`) 与全量自愈脚本 (`scripts/migrate_choice_parentheses.py`) 会自动物理抹除题干末尾录入的各种全角/半角供填答用空括号（如 `(\quad)`、`(   )`、`（  ）`），防止与 LaTeX 模板右侧自动生成的 `\paren` 宏产生二次重叠。
    - **填空题 \fillin 规范与自动自愈 (Fillin Macro Standardization & Self-Healing)**：系统在多模态 OCR 识图（SiliconFlow / 阿里百炼 / 中展 AI）Prompt、试卷 AI 智能拆卷 Prompt 中均强制要求将填空题下划线生成为 `\fillin` 宏。同时在后端录入/修改/拆题逻辑中内置了 `normalize_fillin_macro` 自愈清洗器，会自动将 `______` 或 `\underline{\hspace{...}}` 等旧格式静默升级为标准的 `\fillin`。前端将 `preprocessFormulaForKaTeX` 与 `parseMarkdownWithMath` 统一为全局单一来源（Single Source of Truth），内置数学环境感知与句末闭合自愈能力（无论是正文中的 `则 \fillin.` 还是未闭合公式中的 `$B = \fillin.`，均自动规范化为带合法闭合的 KaTeX `\underline` 宏）。针对 `\fillin$` 紧贴闭合符（无空格）的情况，`transformFillinMacro` 自动保留补回闭合 `$`；针对 `\fillin$.` 包含标点符号的情况，前端自动将标点符号挪至 `$` 闭合符外侧，使拆卷预览（`import.js`）、试题研讨（`editor.js`）与组卷排版（`paper.js`）100% 共享相同的渲染表现。
    - **HTML 解析器小于号防切碎转义 (\lt and \gt Self-Healing Protocol)**：前端在预处理 KaTeX 公式时，会自动将数学环境 `$ ... $` 内部所有裸露的小于号 `<` 与大于号 `>` 安全替换为 KaTeX 标准的 `\lt ` 与 `\gt `（不使用后行断言，确保全浏览器兼容）。彻底解决浏览器 `innerHTML` 误将 `< 1` 等公式片段当作未闭合 HTML 标签而抹除切碎 DOM 树的深层 Bug。
    - **LaTeX tabular 表格环境现代响应式网格渲染 (LaTeX Tabular Responsive HTML Grid)**：针对试卷与题库中出现的 `\begin{tabular} ... \end{tabular}`（如频数表、数据统计表、分布列），前端 KaTeX 预渲染管线自动将其解析转换为现代居中、带微边框、带磨砂底色的响应式 HTML5 表格；完整支持 `|l|c|r|` 列对齐、`\toprule/\midrule/\bottomrule` 三线表加粗、`\multicolumn{N}{spec}{...}` 跨列单元格合并与 `\\[5pt]` 行高间隙参数清洗，单元格内部数学公式 100% 交由 KaTeX 高清渲染，且 LaTeX 编译与导出保留原生 `\begin{tabular}` 源码。
    - **裸露数学环境自动自愈 (Exposed Math Environment Self-Healing)**：针对录入或识别到的裸露 LaTeX 数学环境（如 `\begin{cases}...\end{cases}`、`aligned`、`matrix` 等），前端 KaTeX 预渲染函数（`preprocessFormulaForKaTeX` 与 `parseMarkdownWithMath`）会自动识别并使用单美元符号 `$...$` 智能包裹，彻底杜绝未加 `$` 导致渲染成纯文本的异常。
    - **LaTeX 标准段落与换行规范 (LaTeX Standard Paragraph & Line Break Spec)**：前端渲染管线（`preprocessFormulaForKaTeX` 与 `paper.js`）严格遵循 LaTeX 编译标准规范：双回车及以上（`\n\n+`）代表起新段落（转换为 `<br><br>`）；显式双反斜杠（`\\\\`）代表强制硬换行（转换为 `<br>`）；单回车（`\n`）仅作为源码代码折行视为空格，不打断同一自然段内的文字流，与真实 TeX 编译器排版行为 100% 保持一致。
    - **LaTeX/Markdown 换行规范与右侧插图顶格组装算法 (Line Break Rules & Right-Aligned Minipage Hangindent Reset)**：在 LaTeX 试卷导出中，引入了 `format_stem_paragraphs` 算法结合 `\noindent\begin{minipage}` 容器及内部 `\hangindent=0pt\setlength{\parindent}{2em}` 参数重置。消除了外层 `problem` 环境施加给 `minipage` 盒子的外层 2em 缩进偏移以及折行悬挂缩进，既保证了在 `minipage` 右侧插图窄版下同一段落内的自然续行（如 `AC=BC, D, E分别为...`）与全宽题完全一致地 **100% 绝对顶格对齐**，又使小问另起新行并保留 2em 标准首行缩进，且绝不破坏公式或坐标点。
  - **前端渲染**：前端将自动依据选项最长字符数 `maxLen` 自适应网格排版（小于等于 10 字为 1行4列，大于 10 且小于等于 24 字为 2行2列，大于 24 字为 1行1列），并自动补全 `A.`, `B.`, `C.`, `D.` 标号。
  - **AI 题库转换**：后端导出 AI 专属只读题库时，会自动将此 `choices` 环境清洗为 Markdown 标准列表 `- A.` / `- B.` 形式，防止干扰大模型。
- **标签系统**：
  - 目录级联（必修/选修 -> 章节 -> 知识点）。
  - 题型标签（单选、多选、填空、解答）。
  - 其他属性（难度、来源等）。
  - 自定义标签：卡片顶部右侧（紧靠难度标签）使用 items-start 与 flex-1 弹性对齐，智能限制至多显示 2 个（单标签超长截断），其余折叠至 `+N` 徽章，悬浮时使用定制琥珀色气泡瞬时展示，且阻断点击事件不干扰卡片跳转。
- **教材大纲多版本预设模版 (Syllabus Preset Templates)**：系统设置中支持快捷切换**人教A版**、**人教B版**、**苏教版**和**沪教版**（含必修四与选修三独立的数学建模活动群）的标准数学大纲预设。四套权威数据统一存放在 `mathbank/resources/curriculums/A.json`、`B.json`、`S.json`、`H.json`，后端由 `mathbank.curriculums` 加载，前端通过只读接口 `GET /api/config/curriculum-presets/{version}` 获取，禁止在 `api.js` 重复硬编码。切换大纲不会修改已入库题目数据，而是生成对应大纲的元数据配置 JSON，保存后全局生效。
- **多教材大纲身份共存与迁移系统 (Curriculum Coexistence & Migration)**：
  - **活跃-镜像模式 (Active-Mirror Pattern)**：新建表 `question_curriculums` 用以存放题目在每套大纲（`A`、`B`、`S`、`H`）中的分类镜像。`questions` 主表字段仅反映当前活跃配置。
  - **大纲切换与自动增量迁移**：当用户在设置中保存并切换教材版本时（`POST /api/config/metadata`），系统会自动检测原活跃版本与新目标版本。若版本发生变化，系统会在后台自动对目标大纲中尚未分类（增量模式）的题目运行“通用关键词路由翻译算法”，瞬间生成并同步保存对应的章级镜像，最后批量刷入 `questions` 表的主字段中，无需用户进行任何手动迁移操作。
  - **跨大纲小节隔离自愈 (Knowledge Field Isolation Self-Healing)**：系统会在初始化或切换大纲时校验主表及镜像表的小节字段（`category_knowledge`），若旧大纲章名或非法小节残存在新大纲的章节下，系统会自动将其清洗置空为 `""`（即标准的默认整章），且前端取消对全局 `categoryTree` 的盲目 `.push()` 追加，彻底防止多大纲下下拉菜单选项发生跨版交叉混排污染。
- **全工作台试题序号同步 (#seq_num Sync)**：
  - 后端接口（`GET /api/questions`、`GET /api/questions/{id}`）自动基于 SQLite 物理升序索引计算全局纯净序号 `seq_num` (1 ~ N)。
  - 题库研讨工作台（`editor.js`）与组卷排版工作台（`paper.js`）均统一优先采用 `q.seq_num` 作为试题卡片与 Toast 交互的视觉编号（如 `#22`），彻底规避历史删题导致的数据库主键 ID 断号/跳号给用户带来的困扰。
  - **编辑会话状态与预览单一来源 (EditorState & Single Renderer)**：`api.js` 中的只读方法对象 `EditorState` 是当前题目 ID、纯净序号、录入时间、草稿 ID 与编辑模式的唯一状态来源，状态切换必须使用 `reset()`、`useQuestion()`、`useDraft()`、`setDraftId()` 或 `clearDraft()`，严禁重新引入平行全局变量。`editor.js` 的 `renderEditorPaperMeta()` 是右侧 `paperBadges` 与来源栏的唯一写入入口（难度徽章统一调用 `getDifficultyColor()` 动态匹配大纲元数据色彩配置，与左侧题目卡片保持 100% 视觉色彩一致）；`import.js` 等数据加载模块只能填充表单、更新 `EditorState` 并调用该渲染函数，不得自行拼接同一区域 HTML。题目详情异步返回时还必须核对请求 ID 与当前 `EditorState.questionId`，丢弃快速跨题切换产生的过期响应。这样可确保切换题型、难度、来源、标签、题目或草稿时，编号与“录入于”时间不会丢失、串题或被旧请求覆盖。
- **AI 智能组卷与教育学理推理 (AI Paper Auto-Selection with Pedagogical Reasoning)**：
  - 一键组卷功能升级调用 `PREFER_SOLVE_MODEL` 核心解题大模型，融入中学数学教学教研思维（双向细目表、知识点覆盖率与难度阶梯分布），自动从题库中挑选最适配的题目组合。
- **工作台状态无缝持久化与零闪烁首屏渲染 (Workspace State Persistence & Zero Flash)**：
  - 前端自动记录当前活跃工作台（`PaperStore.activeWorkspace`）。在页面刷新或重新打开标签页时，系统会在 DOM 初始渲染前通过 HTML `init-ws-paper` 样式控制实现无闪烁的精准还原；仅在服务器全新启动（Fresh Server Boot）时重置为默认的题库研讨工作台。


### 3.2 解答与解析模块（多功能输入与工作流）
解答区包含以下 Tab 的交互组件，所有途径获取的内容最终都需汇总至“终审编辑框”作为草稿，由用户确认后方可入库：
1. **手动输入**：直接输入 LaTeX 答案和解析，支持 TikZ 几何代码。
2. **AI 智能生成与 OCR 联动**：调用大模型 API。当在此之前已完成了 OCR 图像识别时，系统将自动读取已识别的草稿解析作为上下文提供给 AI，在 OCR 基础上进行润色、修正公式错误和补充步骤。并且，AI 可根据用户在界面输入的「补充引导指令」（例如“简化解答步骤”或“细化并给出分类讨论过程”）进行个性化解答调整。
3. **OCR 图像识别**：上传包含答案截图的图片，自动调用配置的 OCR API 转化为 LaTeX 文本。
4. **教师点评 (Review)**：单独录入教学反思、考察重点或易错点提示，存储在 `review` 字段中。
5. **自定义标签 (Tags)**：支持输入逗号分隔的个性化标签，弥补级联分类系统的细粒度归纳不足，存储在 `tags` 字段中。

#### 3.2.1 TikZ 几何绘图与智能纠错工作流
- **编译预览**：系统提供 TikZ 渲染服务，编辑 TikZ 代码时，前端发送代码给 `/api/render_tikz`，后端本地调用 LaTeX 编译环境将其转换为标准的 PNG 预览图并返回相对路径展示。
- **AI 智能纠错**：若绘图编译报错或有细节不完美，用户可在纠错输入框中填写“修改意见”，调用 `/api/correct_tikz` 结合原有 TikZ 代码和指导意见由 AI 进行闭环智能修改。

#### 3.2.2 双阶段多模态识图与高级 TikZ 绘图模型联动
- **多模态 Provider 单一解析来源**：单题 OCR、PDF OCR 故障转移、自动 TikZ 绘图与 TikZ 纠错必须分别通过 `resolve_ocr_provider`、`resolve_ocr_fallbacks` 和 `resolve_draw_provider` 获取平台、密钥、地址及真实模型名。PDF OCR 必须尊重 `zhongzhan_claude`（中转站 B）首选项；`SILICONFLOW/模型名` 绘图配置必须剥离平台前缀后再发给接口。
- **多模态插图检测**：在单题 OCR 识图（`/api/ocr`）过程中，如果选择或默认的多模态大模型引擎识别到题目包含插图，会在返回的 LaTeX 文本中自动植入 `[ILLUSTRATION_BOX: ...]` 临时定位标记。
- **高阶 TikZ 自动生成与编译**：后端接口解析到此插图标记后，会自动将其擦除，并自动触发双阶段联动——将整张原始题目截图及提取出的 LaTeX 题干文本同时发送给配置的高级绘图模型（由 `PREFER_DRAW_MODEL` 指定），由其自动识别图文逻辑并生成高精度 LaTeX TikZ 几何绘图代码。生成后系统会在后台自动将其编译为 PNG 图片存入静态目录，并在题干末尾自动追加 Markdown 格式的插图引用（如 `![](/static/uploads/tikz_xxx.png)`），实现单题从图文识别到矢量几何图重绘的一键自愈流程。

### 3.3 异步实时备份与 AI 专属只读题库系统
系统在题目写操作时，通过 FastAPI 的 `BackgroundTasks` 在后台异步自动触发备份同步逻辑，并受全局线程锁 `threading.Lock` 保护：
- **JSON 完整备份 (`data_backup/questions_backup.json`)**：备份数据库的所有表字段。
- **AI 专属只读题库 (`data_backup/questions_library.md`)**：
  - **安全防泄露**：只输出学段、章节、知识点和题干，彻底过滤答案、解析与教师点评，防止 AI 在备课或生成练习时发生答案泄露或产生解析干扰。
  - **排版命令清洗**：自动将原题中的 `\item`、`\begin{itemize}`、`\\`、`\underline` 等 Markdown 不兼容的排版命令转换为标准 Markdown 列表与纯净下划线，函数为 `clean_latex_to_markdown_for_ai()`，但必须 **100% 完好保留夹在 `$` 和 `$$` 之间的公式代码**。
- **AI 专属本地终端 SQL 模糊检索系统 (`scripts/search_questions.py`)**：
  - **高可用终端交互**：为了在编写教案、备课或课件时让 AI（如 Claude Code, Cursor 等）能极速、大容量地访问本地题库并摆脱读取大文件的性能瓶颈，系统在 `scripts/` 中提供 CLI 检索工具。
  - **极速检索模式**：AI 代理应优先从项目根目录运行 `python3 -m scripts.search_questions -q <关键词>`，模糊匹配学段、章节、知识点、题干、来源或自定义标签，以结构化 Markdown + LaTeX 公式混合的形式拉取题目。
  - **命令参数速查**：支持 `-q` (模糊词), `-n` (返回数限制，默认 50，使用 `-1` 为无上限), `-a` (携带答案、解析与点评), `-t` (题型过滤), `-d` (难度过滤), `-r` (查询与特定 ID 题目发生双向关联的全部题目)。
- **历史填空题下划线批量升级迁移工具 (`scripts/migrate_fillin.py`)**：
  - **功能用途**：扫描本地 SQLite 数据库中所有已入库题目，自动将题干中遗留的旧下划线格式（如 `______`、`\underline{...}`、`\fillin[...]`）批量规范化清洗为 100% 纯粹干净的 `\fillin` 宏。
  - **自动落盘与数据同步**：迁移完成后会自动触发 `export_database_to_files()`，同步刷新 `data_backup/questions_backup.json` 备份文件与 `data_backup/questions_library.md` AI 专属题库文件。
  - **运行指令**：在项目根目录下执行 `python3 -m scripts.migrate_fillin`。

### 3.4 题目双向关联与题组管理
- **关联机制**：系统通过 `association_group_id` 对题目发生双向关联（如变式题、一题多问的子母题）。
- **路由接口**：
  - `GET /api/questions/{question_id}/associated`：获取同组的所有关联题目。
  - `POST /api/questions/{question_id}/associate`：将指定题目与当前题目绑定到同一个关联组，自动合并原有分组以维持传递关系。
  - `DELETE /api/questions/{question_id}/associated`：将当前题目从其所属关联组中安全解绑。

### 3.5 批量图片上传与 AI 智能拆卷/解析系统
- **批量图片上传 (`/api/upload/batch`)**：用户可将整张试卷的多张截图一次性拖拽上传，系统自动保存并在前端返回对应标签占位符（如 `[图片1]`）。
- **AI 智能拆卷 (`/api/ai/parse-paper`) 与两阶段异步解答架构**：输入试卷 LaTeX/Markdown 文本与图片映射，调用解析大模型（由 `.env` 中 `PREFER_PARSE_MODEL` 指定，并统一通过 `mathbank.ai_providers.resolve_text_provider` 解析平台、密钥、API Base 与真实模型名）。拆解第一阶段只专注文本切片、分类、难度评估与原版答案提取，秒级快速输出；PDF 后台拆卷的内部文本解析路径使用相同 Provider 解析规则。网页与 PDF 拆卷的结构化响应必须统一交给 `mathbank.ai_json.parse_ai_json`，先严格解析，再仅针对模型常见的 Markdown 代码围栏、JSON 字符串内真实控制字符及未转义 LaTeX 反斜杠进行安全修复；提示词必须要求换行使用 JSON `\n` 转义、LaTeX 反斜杠使用 JSON 双反斜杠，禁止要求在 JSON 字符串内输出真实回车。若勾选自动生成解答，第二阶段由前端使用并发队列（并发上限 3）异步调用 `/api/ai/solve` 为无答案题目平滑推导解析，彻底解决 Token 截断与 HTTP 超时问题。同时每个草稿卡片均支持“AI 补全/重生成解析”单题独立触发。
- **列表清空重置（一键清除与二次确认）**：系统在拆解结果顶部操作栏提供了**“一键清除”**按钮。点击后会弹出确认对话框提示用户。用户手动确认后，自动清除左栏所有输入（标题、Latex 内容、图片映射等）以及右栏所有拆解卡片，并安全重置系统至初始待拆解状态。

### 3.6 存储空间自愈与防误删静默净化
- **删除/编辑即清理**：删除题目或编辑题目发生插图变更时，系统会自动从本地磁盘物理删除所有不被任何题目引用的垃圾图片文件。
- **启动自动静默净化**：在每次服务启动 2.5 秒后，系统会自动在后台静默运行一次对 `static/uploads/` 目录下孤儿图片的扫描净化。
  - **1小时安全防误删线**：净化任务只删除创建时间超过 1 小时的孤儿图片，完美避免误杀用户当前正在录入但尚未保存的临时上传插图。

### 3.7 启动端口竞态自愈与前端级联防挂起重试机制
为了彻底杜绝由于本地多进程拉起时序不一致（Uvicorn 尚未完全就绪但浏览器已抢先加载）导致的 API 请求报错 (502 / TCP Connection Refused) 以及前端 JS 未捕获异常引起的首屏死锁，系统实现了以下稳定性架构：
- **自适应健康检查就绪检测**：在 `启动题库系统.command` 脚本中，舍弃了不可控的 `sleep 1.5` 硬编码延迟。改为启动服务后以每 `0.5` 秒一次的频率轻量化探测后台 `/api/questions` 响应。一旦探测到 `200` 状态码（代表后端完全准备就绪），才会瞬间拉起浏览器，最长等待 10 秒。
- **前端后台自适应静默重试 (Silent Auto-Retry)**：首屏在 `DOMContentLoaded` 事件中触发的 `/api/categories` 和 `/api/questions` 抓取加入了严格的 `.catch()`。当检测到请求异常时，前端会自动执行后台静默重新加载，每次间隔 1.5 秒，上限 3 次，使用户对瞬间的时序偏差达到“无感就绪”。
- **友好 UI 容错兜底与重新加载**：如果多次重试均失败，前端会捕获异常并拦截 promise 抛出，同时在左侧题库区渲染成精致的“连接题库列表失败”红字提示面板，提供一键 `[重新加载]` 按钮，允许用户手动触发重新拉取。
- **Dropdown 防御性校验**：所有在首屏数据装载前会触发的分类填充操作（如 `populateCategoryDropdowns` 与 `populateFilterDropdowns`），其入口处均内置了健壮的 DOM 及 categoryTree 级联数据空判定安全防护，杜绝由于异步时序不同步产生的页面挂起。

### 3.8 PDF 试卷多模态拆解与智能双轨探测系统
- **智能双轨分流架构（Smart Dual-Track Routing & pdf-inspector）**：
  - **前置极速探测（阶段 0）**：系统在 `mathbank.pdf_inspector_helper` 中封装了对高性能开源引擎 `pdf-inspector` 与 `PyMuPDF (fitz)` 的双核融合调用。PDF 上传后在 10~50ms 内快速完成文档类型检测与 CID/中文字形映射直提（`TextBased` 原生电子试卷 vs `Scanned` 扫描/纯图片试卷）。
  - **原生电子试卷毫秒级直提通道**：若判定为 `TextBased` 且具备高置信度，系统自动启用直提通道，在 50ms 内直接提取带双栏阅读顺序、表格与排版的纯净文本流，直接进入文本大模型切片拆题，**彻底跳过多模态视觉 OCR 网络请求**，实现 0 视觉 Token 消耗并将耗时从 20s 缩短至 2~3s。拆题 System Prompt（`build_pdf_parse_system_prompt`）内置了 Unicode 数学符号（如 `√`、`∈`、折行分式）向标准 LaTeX 语法的自动自愈规范。
  - **扫描试卷与异常优雅降级（Graceful Degradation）**：若判定为 `Scanned`、`Mixed`、检测到字体编码缺失或未安装 `pdf-inspector`，系统自动且无缝回退到现有的 PyMuPDF 150 DPI 栅格化 + ThreadPoolExecutor 并发多模态 VLM OCR 流程，保证 100% 向后兼容。
- **切片与异步任务**：支持上传 PDF 文件，后端通过异步任务 `run_pdf_parsing_task` 统一调度。
- **PDF 导入规范与 Token 防溢出**：导入试卷 PDF 时，建议优先选用仅含题干（无冗长解析）的短试卷。详细解析推荐在题目拆解入库后，使用系统内置的 AI 一键推导或手动补充，避免因原文件文本过多导致大模型上下文 Token 超限。
- **配图裁剪机制（取消自动裁剪，拆解题目默认不含配图）**：由于自动识别裁切容易产生不准的误差，系统已取消自动裁剪机制，拆解出的题目默认是不包含任何图片的。若原题包含配图，必须由用户在前端点击「手动截图」弹窗，纯手动拖拽框选出精准截图以进行关联，这确保了配图插图的高画质和 100% 零误差。
- **进度轮询与状态展示**：前端通过 `/api/upload/pdf-task` 提交文件并在右侧展现毛玻璃遮罩层与进度条，以每 1.5 秒的频率请求 `/api/tasks/{task_id}/status` 直至 `completed`。
- **任务手动中止与 ESC 快捷键支持 (PDF Task Cancellation & ESC Handler)**：支持用户在 PDF 拆分过程中通过单击遮罩层上的【中止拆分 (ESC)】按钮或直接按键盘 `ESC` 键立即安全中断任务。前端会立即清除轮询定时器并调用 `POST /api/tasks/{task_id}/cancel`，后端检测到 `cancelled` 状态后自动提前退出线程以释放 OCR/大模型算力，并将界面平滑复位。
- **手动拖拽框选截图**：每个拆解卡片均提供“手动截图”选项，点击可调出 PDF 页面查看灯箱，支持在页面图上左键点击并拖拽框选区域，向 `/api/ai/manual-crop-pdf` 发送百分比坐标进行精准的物理裁剪配图。

### 3.9 Word (.docx) 原生 OMML 试卷拆分与高保真公式/插图直提系统
- **原生 XML 与 OMML 数学流解析 (WordprocessingML & OMML to LaTeX)**：
  - 系统在 `mathbank/omml_helper.py` 中实现了纯 Python、零外部重型依赖的 OMML 转换器，完整支持分式（`<m:f>` $\rightarrow$ `\dfrac{}{}`）、根号（`<m:rad>` $\rightarrow$ `\sqrt[]{}`）、上下标（`<m:sSup>`/`<m:sSub>`）、定界符括号（`<m:d>` $\rightarrow$ `\left(...\right)`）、求和/积分/极限（`<m:nary>` $\rightarrow$ `\sum`/`\int`）、矩阵（`<m:matrix>` $\rightarrow$ `\begin{matrix}`）、方程组（`<m:eqArr>` $\rightarrow$ `\begin{cases}`）与矢量箭头（`<m:acc>` $\rightarrow$ `\vec{}`）等全套高保真转换。
  - **表格无损转换**：`mathbank/docx_helper.py` 自动将 Word 中的 `<w:tbl>` 表格转化为标准的 LaTeX `\begin{tabular}` 源码，结合前端已实现的响应式 HTML5 表格渲染引擎，实现表格的 100% 完美呈现。
  - **高清原画插图提取**：自动解包 `word/media/` 目录下的所有原画级配图，存入 `static/uploads/`，并通过 DrawingML 关系锚点（`<w:drawing>` 与 `rId`）将插图精准自动插入在对应题目的题干末尾（`![](/static/uploads/word_img_xxx.png)`），实现 100% 无损零误差配图绑定。
- **异步上传与进度感知**：前端提供对 `.docx` 的拖拽支持，通过 `POST /api/upload/docx-task` 发起任务，并复用统一的 `/api/tasks/{task_id}/status` 轮询与 ESC 中断控制。
  - **升级晋升 (Promotion)**：保存题目或更新题目时，若检测到 `/tmp/` 下的临时裁剪图，系统自动在后端将其 `shutil.move` 到 `static/uploads/` 永久保存并同步修改正文中的引用。
  - **临时清理 (Cleanup)**：如果用户点击“一键清除”或关闭拆卷面板，前端会调用 `/api/ai/clear-temp-crops` 将所有产生的未保存裁剪配图从磁盘上彻底物理删除。
  - **定时净化 (Self-Healing)**：自愈清理任务中加入对 `static/uploads/tmp/` 目录的扫描，定时物理删除修改时间超过 1 小时的孤儿或废弃临时裁剪图片。

### 3.9 组卷排版工作台与 A4 Live Preview 实时渲染引擎
- **A4 仿真纸张与 Live Preview 秒级渲染引擎**：
  - 前端无需同步等待后端 PDF 编译，纯前端结合 KaTeX + 动态 DOM 模拟真实 A4 试卷（210mm x 297mm 纸张比例）。
  - 支持渲染卷头密封线 (`\secret`)、大标题、副标题（连续空格 `&nbsp;` 与 `pre-wrap` 保持与 LaTeX `\large\bfseries` 粗体一致）、考试注意事项框 (`notice` 环境) 和大题分值信息。
  - **主副标题双向 WYSIWYG 实时编辑 (Bi-directional Title Editing)**：支持在左侧配置栏与右侧 A4 Live Preview 试卷画布上双向自由修改主标题与副标题。A4 画布节点具备 `contenteditable="true"` 属性与琥珀微光 Focus 框，副标题为空时自动展示 `+ 点击在此直接添加副标题 / 备注` 占位提示，打字过程进行增量 DOM 局部更新，做到零闪烁与零丢焦点。
  - **右侧上下解耦与静态沉稳 Studio 面板 (Split Right Panel & Grounded Studio Panel)**：
    - 右侧 `paperCanvasSection` 重构为 `overflow-hidden flex flex-col` 上下完全独立分离架构。
    - 顶部控制面板为静态固定节点 (`shrink-0 mb-3`)，完全独立于下方画布，且使用纯净实底卡片 (`bg-white dark:bg-slate-900 border`) 替代半透明悬浮玻璃，并禁用了 `:hover` 起伏/位移动画 (`transform: none`)，移动鼠标时完全静止沉稳。
    - 下方 A4 试卷 Live Preview 画布使用独立滚动条 (`flex-1 overflow-y-auto custom-scrollbar`)，滚动试卷时试卷平滑隐入独立区域上边界，完全杜绝了向控制栏底部的重叠与遮挡。
  - **全套 `exam-zh` 及其 21 个依赖宏包免配置内置 (Built-in `exam-zh` Class & 21 Helper Packages)**：
    - `templates/exam-zh/` 目录下全量内置了 `exam-zh.cls`、`exam-zh-*.sty` 以及全部 21 个第三方依赖宏包（`tabularray.sty`, `ninecolors.sty`, `varwidth.sty`, `linegoal.sty`, `wrapstuff.sty`, `filehook.sty`, `environ.sty`, `trimspaces.sty`, `xeCJKfntef.sty`, `ulem.sty` 等）。编译与导出时自动复制，保障 Windows (旧版 TeX Live 2018/2021) 与 Mac 100% 离线免配置秒级渲染。
  - **左侧组卷配置栏极简高利用率重构 (Compact Configuration Panel & 12px Typography)**：
    - 左侧 `paperFilterSection` 布局精细化压缩重构，通过收紧内边距 (`p-2.5 sm:p-3 space-y-2`) 和间距 (`mb-0.5`)，在保持 `12px` (`text-xs font-semibold`) 标准清晰字号的前提下，整体高度大幅缩减 ~35%，极大释放了纵向屏幕空间给下方的题目列表。
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

### 3.10 高考级 LaTeX/PDF 编译引擎与 LRU 哈希缓存
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
  - **进程级 UTF-8/GBK 智能平滑解码**：Python 后端对 `xelatex` 的 `proc.stdout`、`proc.stderr` 及 `paper.log` 日志文件全量以 `bytes` 字节流捕获，并采用 `_safe_decode_bytes()` (优先 UTF-8，失败降级 GBK) 进行安全解码；同时在 `main.py` 与 `mathbank/paper_helper.py` 启动入口配置 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，彻底清除终端日志打印非 GBK Emoji (如 `⚡`, `🚀`) 触发的 `UnicodeEncodeError` 崩溃。
  - **`exam-zh.cls` 零依赖内置自愈**：项目已将 `exam-zh` 完整宏包文件（`exam-zh.cls` 及 `exam-zh-*.sty`）全量内置放置在 `templates/exam-zh/` 目录中。每次编译 PDF 或导出全量 ZIP 包时，后端会自动将其动态注入复制到临时编译目录与 Zip 压缩包中。即使 Windows 电脑安装的是旧版 TeX Live (如 2018/2021) 且完全未安装 `exam-zh` 宏包，也可实现 100% 零依赖免配置成功编译！

## 4. 外部 API 接入与接口安全严格规范
必须读取根目录 `.env` 文件中的密钥进行 API 调用：
- **安全鉴权机制**：
  - 所有对题库和配置进行修改的接口（POST / PUT / DELETE）均需要鉴权令牌（本地 `LOCAL_TOKEN`）。
  - 前端由 `api.js` 全局劫持 `fetch`，自动附加 `X-Local-Token` 头部信息。所有 AI 代理在进行接口修改或直接发送 API 请求时必须严格遵守这一安全鉴权规范。
- **OCR 接口（多通道高可用设计）**：
  - **多模态 VLM 选型约束**：公式识图必须依赖多模态视觉大模型，DeepSeek 纯语言模型无法进行 OCR 识图。国内推荐通义千问（`qwen3-vl-flash` / `Qwen/Qwen3-VL-8B-Instruct`）或 MIMO。其中硅基流动专属链接注册并实名认证后赠送 16 元代金券，阿里云百炼平台注册后许多模型 3 个月内提供免费试用额度；海外/中转站极力推荐 `gpt-5.6-luna`（降价后性价比极高且精细度超越大部分模型）。
  - **通道机制**：默认首选阿里百炼（`qwen3-vl-flash`）或硅基流动（`Qwen/Qwen3-VL-8B-Instruct`），以及中转站 GPT（`gpt-5.6-luna`）和中转站 Claude（`claude-3-5-sonnet`）。
- **大模型智能解答与解析/拆卷/TikZ绘图接口 (DeepSeek / 阿里百炼 / 多源 API)**：
  - 各类核心模型可通过 `.env` 中的 `PREFER_SOLVE_MODEL`、`PREFER_PARSE_MODEL`、`PREFER_CLASSIFY_MODEL` 和 `PREFER_DRAW_MODEL` 指定。
  - **TikZ 绘图模型选型约束 (`PREFER_DRAW_MODEL`)**：建议不要使用国内模型（在 TikZ 代码拟合上效果欠佳），推荐使用 GPT 系列（如 `gpt-5.6-luna`）或 Gemini 系列（如 `gemini-3.6-flash`）。
  - 后端通过统一底座接口支持 DeepSeek (`DEEPSEEK_API_KEY`)、通义千问阿里百炼 (`ALI_BAILIAN_API_KEY`) 以及中转站等多元渠道。
### 3.6 启动自检诊断与双平台 Release 构建发布系统
- **启动自检诊断面板 (`main.py` -> `print_startup_diagnostics`)**：
  - 服务启动时自动检测并打印环境信息：Python 版本与虚拟环境识别（`sys.prefix`）、可执行路径、PDF Inspector 毫秒直提引擎就绪状态、PyMuPDF 渲染器、本地 LaTeX 编译器（XeLaTeX/pdfLaTeX）、本地 SQLite 数据库路径与项目根路径。
- **全新双平台打包发布系统 (`scripts/build_release.py`)**：
  - **路径单一来源**：从 `mathbank.paths` 锚定 `PROJECT_ROOT`、`DIST_DIR` 和 `BUILD_CACHE_DIR`。
  - **完整捆绑架构**：
    - 主程序入口 `main.py` 与配置模板 `.env.example`。
    - 后端领域包 `mathbank/`（包含 4 套教材大纲 `resources/curriculums/`、解题与排版引擎、双模态 OCR、TikZ 绘图与 PDF Inspector 适配器）。
    - 静态资源 `static/`（包含 100% 离线 KaTeX、Tailwind、FontAwesome、Inter/Outfit 字体包与前端脚本），并自动清空临时测试上传文件。
    - 命令行运维与检索工具 `scripts/`。
  - **Windows 便携包 (`MathBank-Windows-x64.zip`)**：内置 Python 3.10.11 嵌入式环境 + NuGet SQLite3 二进制补丁 + 预装全部 wheel 依赖（含 `pdf-inspector` 与 `pymupdf`）+ 智能释放端口的 `启动题库系统.bat`。
  - **macOS 发布包 (`MathBank-macOS.zip`)**：包含预赋权可执行的 `启动题库系统.command`，自动探测并创建独立 `venv` 虚拟环境并一键启动。

## 4. 接口设计与网络高可用规范 (执行 ui-ux-pro-max 标准)
前端界面必须具备现代化、极简的教研工作台风格：
- **视觉风格**：采用 Glassmorphism（毛玻璃）或现代极简卡片（Cards）风格。背景推荐使用柔和的 `slate-50` 或 `gray-50`。
- **组件细节**：卡片需带有优雅的阴影 (`shadow-md`, `shadow-lg`) 和圆角 (`rounded-xl`, `rounded-2xl`)。
- **侧边栏与布局控制**：支持侧边栏一键折叠伸缩（`toggleSidebarBtn`），展开状态下保持舒展的响应式网格与固定高度滚动区，折叠时释放沉浸式全屏渲染视图。
- **暗色模式设计与防阴森规则 (Modern Dark Mode & Anti-Gloom Protocol)**：
  - **玻璃底 + 霓虹透光微光 (Vibrant Glass & Neon Tint)**：在暗色模式下，严禁使用浓重晦暗的深纯色（如 `indigo-950`, `emerald-950`, `rose-950`, `amber-950`），此类调色在深色底板上会产生浑浊阴森感。必须采用**高通透玻璃底 + 10% 品牌色霓虹透光微光**（如 `dark:bg-indigo-500/10 dark:border-indigo-500/25`），搭配高对比度亮彩文字（`indigo-200`, `emerald-200`, `amber-200`, `rose-200`）与发光 Icon。
  - **下拉菜单与 Hover 悬浮态规范 (Dropdown & Hover Protocol)**：严禁在下拉菜单项或触发按钮上使用单纯的 `hover:bg-slate-100` 类，防止在暗色模式下被误判为“浅灰底 + 白字”无法识别。必须统一采用 `.glass-dropdown` 容器与 `.glass-dropdown-item` 类，暗色 Hover 状态自动映射为深石墨灰背景 (`rgba(51, 65, 85, 0.85)` Slate-700) 与高亮纯白字 (`#ffffff`)。
  - **弹窗与卡片暗色覆盖层防护 (Modal & Card Overlay Protection)**：全局在 `app.css` 中建立对 `.dark .bg-slate-50\/40` ~ `/90` 等全套透明度变体及 `glass-card` 的暗色保护；同时保护试卷/A4仿真纸张 (`.a4-paper-sheet`) 在暗色模式下强制维持 `#ffffff` 背景与 `#0f172a` 文字。
  - **深色文本框与编辑器高对比选中样式 (High-Contrast Selection Protocol for Dark Textareas/Editors)**：在 `app.css` 及 HTML 文本框中建立深色/代码框高对比选中机制（`selection:bg-indigo-600 selection:text-white` 与 `.bg-slate-900::selection`），彻底杜绝全局 `::selection` 暗色字体导致在深色底文本框（如自定义维度 JSON 配置编辑器）中选中文字变成黑字黑底不可读的问题。
- **多主题色彩系统与极简曜石黑预设 (Multi-Theme System & Obsidian Monochromatic Preset)**：支持 6 套主题色彩自由切换（曜石黑 `.theme-obsidian` / 罗兰紫 `.theme-violet` / 深海蓝 `.theme-ocean` / 翡翠绿 `.theme-emerald` / 琥珀橙 `.theme-amber` / 玫瑰红 `.theme-crimson`）。“曜石黑”作为极简黑白单色旗舰调色，在亮色模式下以 `#0f172a`（Slate-900）深藏青/黑曜石作为主品牌色，呈现与黑白 Logo 完美融为一体的现代极简教研质感。
- **系统图标与 Logo 视觉设计规范 (Flat Minimalist Icon & Logo Design Protocol)**：
  - **平面极简设计**：项目图标与 Logo 统一采用**平面极简风格（Flat Minimalist Style）**。
  - **禁忌规则**：**严禁使用廉价的 3D 浮雕、塑料质感渲染或俗套的蓝紫大渐变**。
  - **标准构图与配色**：底座统一采用深藏青/黑曜石圆角矩形（Dark Slate Squircle `#0f172a`），配合极简几何线条（Golden Ratio/Monogram、线条手稿、发光高对比度元素），确保在全平台 Favicon、App 图标与 Header Logo 各种尺寸下均兼具极致辨识度与现代科技教研感。
- **交互反馈**：所有的按钮、Tab 切换、拖拽上传区域必须有平滑的过渡动画（`transition-all`, `duration-300`）和明显的 Hover 状态反馈。
- **上传组件**：图片上传区域需设计为带虚线边框、云朵图标的拖拽上传框。API 请求期间必须有明确的 Loading 动画或骨架屏（Skeleton）提示。
- **排版原则**：参考 Notion，注重留白（Padding/Margin）与呼吸感。

## 6. 开发与运行指令
- **运行依赖安装**：`pip install -r requirements.txt`
- **开发/测试依赖安装**：`pip install -r requirements-dev.txt`
- **本地启动命令**：`uvicorn main:app --reload`
- 在每次生成或修改代码后，必须确保应用能够通过上述命令正常启动，且没有任何控制台报错。

### 6.1 单元测试套件与执行
本项目配备了高可用、完全隔离的 pytest 单元测试（使用内存 SQLite `StaticPool` 保证零持久化污染）：
- 测试用例位于 `tests/` 目录下，除业务 API、数据库、同步与试卷测试外，`test_architecture.py` 专门验证共享路径、四套大纲资源与提示构建器边界。
- **运行单元测试指令**：为了防止 Pytest 扫描到 `scratch/` 目录中的临时或废弃脚本导致报错，**执行测试时必须限定在 `tests/` 文件夹下**：
  ```bash
  python3 -m pytest tests/
  ```
