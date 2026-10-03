/* Curated community answers, not a raw chat export. See docs/community-qa.md. */
window.MathBankQaData = {
    period: {start: '2026-09-08', end: '2026-10-03'},
    entries: [
        {
            id: 'download-release', category: '安装与更新',
            question: '第一次使用 MathBank，应该从哪里下载？',
            answer: ['到项目 GitHub 页面的 Releases（发行版）下载适合自己系统的发布包，再按仓库安装说明操作。源码下载与已打包的发行版不是同一种安装方式。'],
            keywords: ['安装', '下载', 'Windows', 'macOS', 'Release'],
            sources: [{date: '2026-09-13', answerSeqs: [1328]}]
        },
        {
            id: 'upgrade-with-backup', category: '安装与更新',
            question: '覆盖更新前要做什么？macOS 合并时应该选哪一项？',
            answer: ['群主建议先完整备份原文件夹，再把新版本文件覆盖到原目录；macOS 合并文件夹时选择“替换”。这是发布包覆盖更新的操作，不是让 Git 源码用户强行覆盖未提交的修改。', '操作前先正常关闭服务。保留自己的题库、插图、自定义配置和私密 API 配置；不要把备份公开上传。若自己改过代码，先另外保存改动。'],
            keywords: ['升级', '更新', 'Mac', '替换', '备份'],
            sources: [{date: '2026-09-14', answerSeqs: [1362]}, {date: '2026-09-28', answerSeqs: [2023, 2071]}]
        },
        {
            id: 'move-folder', category: '安装与更新',
            question: '移动程序文件夹后打不开，应该先检查什么？',
            answer: ['群内有一次案例是原服务没有正常关闭。群主建议先把文件夹放回原位置，打开程序后用电源按钮彻底关闭，再移动整个文件夹。', '以后移动或复制程序前先停止服务。若仍然打不开，要结合启动日志排查，不能把所有启动失败都归因于这个原因。'],
            keywords: ['启动', '打不开', '移动', '桌面', '下载目录'],
            sources: [{date: '2026-09-28', answerSeqs: [2026]}]
        },
        {
            id: 'api-base-url', category: 'API 与模型',
            question: 'Base URL 应该填什么？一定要加 /v1 吗？',
            answer: ['填写服务商提供的 API 接口地址，不是服务商网页首页。群主在具体案例中提示补上 /v1；是否需要这段路径，应以所用服务商的接口说明为准，不要对所有地址重复添加。'],
            keywords: ['api', 'base url', '中转站', 'v1', '接口'],
            sources: [{date: '2026-09-08', answerSeqs: [1260, 1261, 1262]}, {date: '2026-09-14', answerSeqs: [1336]}]
        },
        {
            id: 'provider-match', category: 'API 与模型',
            question: '填了 API Key，为什么识图仍提示没有配置，或题干没有自动填入？',
            answer: ['检查“公式识图”等任务实际选择的服务商和模型，再确认该服务商对应的 API Key 已保存。只配置另一家服务商的 Key，不会自动给当前任务使用。正常识别后，结果应进入题干编辑区。', '如果配置匹配仍失败，再检查服务商返回的错误和 Key 是否有效；不要仅凭“已经填了 Key”排除接口配置问题。'],
            keywords: ['api', 'key', '识图', '配置', '服务商', '鉴权'],
            sources: [{date: '2026-09-16', answerSeqs: [1373, 1378, 1379]}, {date: '2026-09-20', answerSeqs: [1445, 1462]}]
        },
        {
            id: 'vision-model', category: 'API 与模型',
            question: '扫描版 PDF 和截图应该选什么模型？',
            answer: ['图像识别需要支持视觉输入的多模态模型。模型能力应按所用服务商和具体型号核对，不能仅按品牌名判断支持或不支持图片。群聊中的模型推荐属于当时的使用经验，不是长期兼容或效果保证。'],
            currentNote: '公式识图与试卷拆分是可分别配置的任务。扫描页识别使用识图配置，拆题和属性匹配使用拆卷配置；不要把两个阶段的模型要求混为一谈。',
            keywords: ['扫描', 'pdf', 'OCR', '视觉', '多模态', 'DeepSeek'],
            sources: [{date: '2026-09-20', answerSeqs: [1416, 1426, 1427, 1428, 1462]}]
        },
        {
            id: 'custom-api', category: 'API 与模型',
            question: '能接入火山引擎、豆包或其他自定义 API 吗？',
            answer: ['群主说明“中转站”入口相当于自定义配置，可以按服务商的接口地址、模型标识和 Key 尝试接入。', '当时并没有确认火山引擎或豆包的兼容性；存在配置入口不等于所有服务商都已测试支持。'],
            keywords: ['api', '自定义', '火山', '豆包', '中转站'],
            sources: [{date: '2026-09-28', answerSeqs: [2008, 2009, 2010]}]
        },
        {
            id: 'large-pdf', category: '导入与识别',
            question: '能一次导入几百页 PDF 吗？为什么大文件更容易失败？',
            answer: ['不建议一次处理几百页。群主提醒，大文件可能超出模型上下文或输出 token 限制，并建议分成十页以内的小批次，页数越少通常越稳妥。', '“十页以内”是使用建议，不是程序强制上限；复杂图文、题量和模型能力同样会影响结果。'],
            keywords: ['pdf', '页数', '批量', 'token', '上下文', '大文件'],
            sources: [{date: '2026-09-08', answerSeqs: [1265, 1266, 1267]}]
        },
        {
            id: 'import-failure', category: '导入与识别',
            question: 'Word / PDF 拆题失败或停在校验阶段，怎样排查？',
            answer: ['先检查 API 配置、服务商返回的错误以及所选模型是否适合当前任务；缩小导入批次，必要时重试或更换合适的模型。复杂排版、多图和大量题目都可能增加处理难度。', '群主曾复现模型返回的公式没有带项目要求的标记，导致校验失败的案例。因此不能把所有失败都归结为模型能力弱，也不建议以关闭校验作为通用解决办法。持续失败时保留原文件和日志反馈。'],
            keywords: ['拆卷', '失败', '卡住', '校验', 'DeepSeek', '公式锁'],
            sources: [{date: '2026-09-20', answerSeqs: [1423, 1513, 1541, 1542]}, {date: '2026-09-23', answerSeqs: [1667, 1673, 1683, 1695]}]
        },
        {
            id: 'custom-tex-import', category: '导入与识别',
            question: '自定义 LaTeX 模板的试卷拆分失败，有什么替代方式？',
            answer: ['群主处理过因自定义模板导致 TeX 导入困难的案例：可以先把 TeX 编译成 PDF，再尝试导入 PDF。个别题仍需核对和手动调整，不能保证所有自定义模板都能直接拆分。'],
            keywords: ['tex', 'latex', '自定义模板', '拆分', 'pdf'],
            sources: [{date: '2026-09-17', answerSeqs: [1395]}]
        },
        {
            id: 'existing-answers', category: '导入与识别',
            question: 'Word 原卷已有答案，能避免 AI 重新生成解答吗？',
            answer: ['不要勾选“AI 生成解答”，系统会尝试匹配原 Word 中已有的答案。群主提醒，答案集中放在卷末时可能匹配不准，仍需逐题核对。', '群主当时认为 Word 的答案匹配效果相对较好，PDF 可能较弱；这不是对任何文件都能正确匹配的保证。'],
            keywords: ['word', '答案', '解析', '重复生成', 'token'],
            sources: [{date: '2026-09-23', answerSeqs: [1717, 1719, 1720, 1721, 1724]}]
        },
        {
            id: 'pdf-images', category: '导入与识别',
            question: 'PDF 导入能自动提取插图和选项中的图片吗？',
            answer: ['可以尝试自动提取。早期“PDF 插图只能手动补”的回答已经过时：群主在 9 月 25 日宣布开始支持插图提取，9 月 28 日进一步说明新版支持自动截图；10 月 2 日确认 PDF Inspector 可以处理选项中的图片。', '这些答复不代表所有复杂排版都能完整恢复。导入后仍要逐题检查缺图、错图和图片位置，必要时手动补图。'],
            currentNote: 'PDF 的“智能图文提取”策略支持自动提取与归位；“文字优先”和“全页识图”仍需手动配图。',
            keywords: ['pdf', '插图', '自动截图', '选项图片', 'PDF Inspector', '缺图'],
            sources: [{date: '2026-09-25', answerSeqs: [1893]}, {date: '2026-09-28', answerSeqs: [2053]}, {date: '2026-10-02', answerSeqs: [2755]}]
        },
        {
            id: 'formula-review-warning', category: '导入与识别',
            question: '出现“公式结构待核对”，是否说明公式一定错了？',
            answer: ['不一定。群主在 9 月 29 日的排查中初步判断，Word / PDF 拆图流程可能误加核对提示，但仍可能存在真正的公式错误。', '请对照原文逐项核对，不要批量忽略或删除提示。那段对话中没有确认根因或修复完成；持续出现时可提供脱敏后的原文档供排查。'],
            keywords: ['公式结构待核对', '警告', '拆图', '导入'],
            sources: [{date: '2026-09-29', answerSeqs: [2245, 2252, 2254, 2256]}]
        },
        {
            id: 'screenshot-latex', category: '编辑与排版',
            question: '能把题目截图转成可编辑的 LaTeX 吗？',
            answer: ['可以。群主说明截图识别得到的就是 LaTeX 内容，需要先配置识图模型及对应 API。识别完成后在题干编辑区检查和修改，再保存入库；识别结果不保证完全准确。'],
            keywords: ['截图', 'latex', 'OCR', '题干', '识别'],
            sources: [{date: '2026-09-16', answerSeqs: [1373]}, {date: '2026-09-20', answerSeqs: [1433]}]
        },
        {
            id: 'image-reference', category: '编辑与排版',
            question: '怎样删除图片，或把题末的图片移到正文中？',
            answer: ['在题干编辑框中找到对应的 Markdown 图片引用，例如 ![](图片路径)。删除这段引用可移除题干中的图片；剪切后粘贴到目标文字位置，可改变插入位置。', '这里调整的是题干中的图片引用，不是直接删除磁盘文件，也不等于支持任意拖动或文字环绕。修改后检查预览并保存。'],
            keywords: ['插图', '删除', '移动', 'Markdown', '位置'],
            sources: [{date: '2026-09-10', answerSeqs: [1269]}, {date: '2026-09-23', answerSeqs: [1630, 1631]}]
        },
        {
            id: 'image-border', category: '编辑与排版',
            question: '预览中的图片外框会出现在导出文件里吗？',
            answer: ['群主说明，网页中的外框用于标识图片和提供交互，正常导出的是图片本身，不会附带这层界面外框。', '如果原始图片自身就带边框，导出不会自动去除原图里的边框。'],
            keywords: ['图片', '外框', '边框', '预览', '导出'],
            sources: [{date: '2026-09-20', answerSeqs: [1489]}, {date: '2026-09-28', answerSeqs: [2084]}]
        },
        {
            id: 'image-size-wrap', category: '编辑与排版',
            question: '图片大小能自由调整吗？需要文字环绕怎么办？',
            answer: ['群主说明，系统提供几个固定图片大小档位；任意放大可能影响 PDF 编译和版面。需要更精细的尺寸或文字环绕时，可以导出 Word 后调整。', '9 月 23 日的答复中，文字环绕尚未计划实现；不要把 Word 的后期编辑能力当作网页内置功能。'],
            currentNote: '图片布局菜单已有自动 / 小 / 中 / 大尺寸；题末图片组可选题干右侧及下方居左、居中、居右。这些预设布局不同于任意自由环绕。',
            keywords: ['图片', '大小', '缩放', '大中小', '文字环绕', 'word'],
            sources: [{date: '2026-09-20', answerSeqs: [1503]}, {date: '2026-09-23', answerSeqs: [1640, 1641]}, {date: '2026-10-02', answerSeqs: [2755]}]
        },
        {
            id: 'math-delimiters', category: '编辑与排版',
            question: '向量、上下标或公式显示成代码，应该检查什么？',
            answer: ['先检查公式是否放在完整的数学定界符内，例如 $x_1$、$y^2$。群主在粗体向量的具体案例中建议使用 $\\boldsymbol{a}$，并指出缺少 $...$ 会影响渲染。', '不能因此把所有 \\mathbf 都改成 \\boldsymbol：原文明确使用的粗体正体应保留，最终应对照原题。'],
            currentNote: '已有公式、跨行环境和表格会受到保护，自动补定界符仅处理高置信度片段，不能替代人工核对。',
            keywords: ['公式', '美元符号', '向量', '下标', '上标', 'mathbf', 'boldsymbol', '乱码'],
            sources: [{date: '2026-09-13', answerSeqs: [1310]}]
        },
        {
            id: 'fraction-size', category: '编辑与排版',
            question: '分数太小，可以全局把 \\frac 替换成 \\dfrac 吗？',
            answer: ['不宜全局替换。群主建议主体分数使用 \\dfrac，指数、下标里的小分数保留 \\frac，以免破坏层次和行距。'],
            currentNote: '题干和答案编辑区已有“规范分式”操作。它保护明确的数学结构，只修改当前编辑内容，正常保存后才入库，不会批量修改所有历史题。',
            keywords: ['分数', '分式', 'frac', 'dfrac', '规范分式'],
            sources: [{date: '2026-09-22', answerSeqs: [1606]}]
        },
        {
            id: 'choice-environment', category: '编辑与排版',
            question: '选择题选项应该怎样录入？需要手动加空格排列吗？',
            answer: ['使用项目的 choices 环境，每个选项以 \\item 开头，例如：\\begin{choices} \\item $x=1$ \\item $x=2$ \\item $x=3$ \\item $x=4$ \\end{choices}。', '群主建议让模板自动安排选项布局，而不是用 \\quad 等空格手动拼成一行。choices 是本项目模板的约定，不是所有 LaTeX 文档都默认提供。'],
            keywords: ['选择题', '选项', 'choices', 'item', 'quad'],
            sources: [{date: '2026-09-26', answerSeqs: [1920, 1922]}]
        },
        {
            id: 'preview-punctuation', category: '编辑与排版',
            question: '小问前的句号消失，需要输入两个句号吗？',
            answer: ['不需要用重复标点补偿。群主后续发布的 v2.4.1 说明已修复小问另起一段时吞掉前一句句号、分号、感叹号等标点的问题。', '这次修复只影响网页预览，不修改已保存的题目原文以及 PDF / Word 导出内容。旧版本出现此问题时先更新；如果原文已手动输入两个句号，需要自行核对重复标点。'],
            keywords: ['句号', '标点', '两个点', '换行', '小问', '2.4.1'],
            sources: [{date: '2026-09-29', answerSeqs: [2236]}]
        },
        {
            id: 'preview-choice-wrap', category: '编辑与排版',
            question: '选项公式在运算符处意外断行，怎样处理？',
            answer: ['群主发布的 v2.4.1 说明已优化网页选项排版：避免公式在运算符处意外断行，空间不足时自动减少选项列数。', '这不等于强制四个选项永远挤在同一行。该修复只改变网页预览，不改写题目原文和 PDF / Word 导出内容。'],
            keywords: ['选择题', '断行', '换行', '公式', '列数', '2.4.1'],
            sources: [{date: '2026-09-29', answerSeqs: [2236]}]
        },
        {
            id: 'preview-vs-export', category: '组卷与导出',
            question: '预览里的分页或空行异常，是否代表导出的 PDF 也一样？',
            answer: ['不一定。群主说明网页预览是模拟排版，不是真正的 LaTeX 编译。可检查导出的 TeX 是否包含相应题目，并实际打开编译得到的 PDF 核对。', '不能反过来保证所有缺题都只是预览问题。反馈时同时说明网页表现与实际导出结果，更容易定位。'],
            keywords: ['分页', '缺题', '空行', '预览', 'pdf', 'tex'],
            sources: [{date: '2026-09-13', answerSeqs: [1321, 1322]}, {date: '2026-09-24', answerSeqs: [1736, 1737, 1743]}]
        },
        {
            id: 'latex-required', category: '组卷与导出',
            question: 'TikZ 绘图和生成 PDF 为什么需要安装 LaTeX？',
            answer: ['TikZ 图形和试卷 PDF 都需要通过 LaTeX 编译。群主建议，有绘图或 PDF 导出需求时安装所需环境。', '少量录题且不使用 TikZ 时，可以把原图截图粘贴到题干；这能避免为图片重绘调用编译，但不能替代生成试卷 PDF 所需的环境。'],
            keywords: ['latex', 'XeLaTeX', 'TikZ', '环境', '安装', 'pdf'],
            sources: [{date: '2026-09-11', answerSeqs: [1290]}]
        },
        {
            id: 'latex-package', category: '组卷与导出',
            question: '怎样取得 LaTeX 源码？没有安装 LaTeX 也能打包吗？',
            answer: ['使用组卷页面的 LaTeX 打包导出。群主说明，未安装 LaTeX 时也可取得源码包；只有安装环境并编译成功后，才能同时得到生成的 PDF。'],
            keywords: ['latex', '源码', 'tex', '打包', 'zip', '导出'],
            sources: [{date: '2026-09-20', answerSeqs: [1437, 1438, 1439]}]
        },
        {
            id: 'pdf-compile-failure', category: '组卷与导出',
            question: '安装了 LaTeX，为什么仍然无法生成 PDF？',
            answer: ['安装环境不代表任何题干和模板都能成功编译。群主建议先打包导出 TeX 源码，结合编译错误检查题干、模板及相关图片。', '反馈时提供可复现的源码或原题与错误日志；不要仅凭“装了 LaTeX”就断定是软件问题，也不要把所有失败都归为同一种语法错误。'],
            keywords: ['pdf', '编译', '失败', 'latex', 'tex', '日志'],
            sources: [{date: '2026-09-13', answerSeqs: [1312, 1317, 1322]}, {date: '2026-09-17', answerSeqs: [1389, 1391]}]
        },
        {
            id: 'paper-title', category: '组卷与导出',
            question: '模板标题写着高中，做初中试卷怎样修改？',
            answer: ['群主建议选择“常规试卷”模板，再直接修改试卷标题。修改标题与接入任意外部 LaTeX 模板是两件事，后者需要另行适配。'],
            keywords: ['标题', '初中', '高中', '模板', '试卷信息'],
            sources: [{date: '2026-09-23', answerSeqs: [1707]}]
        },
        {
            id: 'custom-question-type', category: '组卷与导出',
            question: '自定义题型在旧版本导出时不显示，怎么办？',
            answer: ['早期版本曾只导出内置题型。群主在 9 月 12 日确认，已为“常规试卷”和“日常小练”模板更新自定义题型支持。', '遇到同样问题先核对版本和模板，不要继续把旧版限制当作当前规则；更新后仍缺失时，附题型配置和导出样例反馈。'],
            keywords: ['自定义题型', '导出', '常规试卷', '日常小练', '缺题'],
            sources: [{date: '2026-09-11', answerSeqs: [1292, 1293, 1294]}, {date: '2026-09-12', answerSeqs: [1296]}]
        },
        {
            id: 'generate-answer-later', category: '编辑与排版',
            question: '导入时没有解析，之后还能让 AI 补充吗？',
            answer: ['可以。群主给出的操作是先把题目导入题库，再打开题目使用 AI 解析功能。生成后仍需人工核对，确认后保存。'],
            currentNote: '题目编辑器有 AI 智能生成解答入口，导入题卡也提供 AI 生成解析操作；无需重新导入整份试卷。',
            keywords: ['ai', '解析', '答案', '补充', '解答'],
            sources: [{date: '2026-09-28', answerSeqs: [2058, 2059]}]
        },
        {
            id: 'local-backup', category: '数据与备份',
            question: '题库数据存在哪里？升级前应该备份什么？',
            answer: ['群主说明题目保存在本地 SQLite 数据库中。历史手动备份清单还包括上传插图、自定义配置和私密的 .env；覆盖升级前备份完整文件夹更稳妥。', '手动复制数据库前先正常停止服务，避免只复制运行中的主数据库文件而遗漏尚未写回的数据。含 .env 的私密备份只能自己保管，不可公开分享。'],
            currentNote: '当前可通过 python -m scripts.backup 创建带校验的完整备份，包含数据库、引用的插图和自定义元数据，但不含 .env、API Key、本地控制令牌及浏览器内未入库草稿。data_backup/questions_backup.json 只是同步导出，不等同于完整恢复备份。',
            keywords: ['备份', '数据库', 'sqlite', 'db', 'data_backup', 'uploads', 'env'],
            sources: [{date: '2026-09-24', answerSeqs: [1749]}, {date: '2026-09-28', answerSeqs: [2071]}]
        },
        {
            id: 'migrate-computer', category: '数据与备份',
            question: '换电脑时可以迁移原题库吗？',
            answer: ['可以。群主在新电脑已具备所需运行及导出环境的前提下，说明可复制完整本地题库文件夹迁移。请先停止旧服务并保留备份，再迁移题库和图片。', '不要只复制数据库而漏掉插图与自定义配置；跨系统时还需要使用适合新系统的程序和依赖，不能直接复用另一系统的运行环境。'],
            currentNote: '数据路径锚定在程序根目录，包括 math_question_bank.db、static/uploads/ 和 data_backup/。浏览器内未入库的草稿不随文件夹迁移，应先保存入库。恢复完整备份前须停止服务，并按备份恢复说明验证后操作。',
            keywords: ['迁移', '换电脑', '数据目录', '恢复', 'Mac', 'Windows'],
            sources: [{date: '2026-09-14', answerSeqs: [1344]}, {date: '2026-09-28', answerSeqs: [2071]}]
        },
        {
            id: 'ai-readable-library', category: '数据与备份',
            question: '能把题库给 AI 检索，或迁移到其他题库系统吗？',
            answer: ['群主说明，题目保存在 SQLite 中，也曾提供 data_backup 下的 Markdown 题库文件供 AI 读取。题量大时不宜把整份文件一次塞进模型，可按需检索。', '迁移到其他系统仍需转换字段和格式；这不意味着与其他题库一键兼容。'],
            currentNote: 'data_backup/questions_library.md 仍是自动生成的 AI 只读题库，包含题干、插图和大纲，不包含答案解析。它不是完整备份，不应直接改写来替代题库保存；项目另有 scripts/search_questions.py 按需检索工具。',
            keywords: ['ai', '检索', 'markdown', 'questions_library', 'sqlite', '迁移'],
            sources: [{date: '2026-09-24', answerSeqs: [1749, 1883, 1884, 1885]}]
        },
        {
            id: 'share-without-secrets', category: '数据与备份',
            question: '分享修改后的项目或整包时，怎样避免泄露 API Key？',
            answer: ['群主提醒，API Key 配置在 .env 中，打包前要排除敏感配置，不能直接分享自己的完整工作目录或私密备份。', '同时检查配置副本、日志和截图，确认没有密钥、令牌或无关个人信息。只删除公开文件不代表已撤销泄露的凭据；如果已经泄露，应到相应服务商撤销并更换。'],
            keywords: ['api key', '隐私', '密钥', '泄露', 'env', '分享', '打包'],
            sources: [{date: '2026-10-01', answerSeqs: [2367, 2443]}]
        },
        {
            id: 'custom-curriculum', category: '定制与反馈',
            question: '学段、章节和目录可以修改吗？',
            answer: ['可以。群主建议先整理好需要的学段和目录，再到系统设置中修改相关配置。不要通过批量改题干文字来代替目录设置。'],
            keywords: ['学段', '章节', '大纲', '目录', '设置', '自定义维度'],
            sources: [{date: '2026-09-28', answerSeqs: [1982, 1984]}]
        },
        {
            id: 'other-subjects', category: '定制与反馈',
            question: '可以改造成物理等其他学科的题库吗？',
            answer: ['群主支持自行定制标题、标签和目录，群内也分享过基于项目修改的物理题库。原版主要围绕数学场景设计，其他学科需要适配目录和处理规则。', '社区改造不等于原版已经内置通用学科切换，也不能保证其他学科的识别效果。涉及分发或其他使用方式时，应另外遵守项目许可证。'],
            keywords: ['物理', '学科', '定制', 'PhysicBank', '目录'],
            sources: [{date: '2026-09-24', answerSeqs: [1744, 1746, 1806]}, {date: '2026-09-29', answerSeqs: [2302, 2315]}]
        },
        {
            id: 'report-issue', category: '定制与反馈',
            question: '怎样反馈故障？应该提供哪些信息？',
            answer: ['群主建议在项目 GitHub 仓库提交 Issue，附截图并在同一条 Issue 中持续交流。最好包含版本、系统、复现步骤、完整报错和可复现的原文件。', '群主曾要求提供 .system_generated 下的 server.log；该目录可能被系统隐藏。公开发送前先去除 API Key、控制令牌、个人路径及不便公开的题目内容。'],
            keywords: ['issue', '反馈', 'bug', '日志', 'server.log', '报错'],
            sources: [{date: '2026-09-21', answerSeqs: [1560, 1561]}, {date: '2026-09-23', answerSeqs: [1667]}, {date: '2026-09-28', answerSeqs: [2063, 2064]}]
        }
    ]
};
