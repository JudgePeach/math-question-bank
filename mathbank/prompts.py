"""Central prompt constants and pure prompt builders for MathBank AI flows."""

import json


FRACTION_STYLE_RULE = (
    "【分式】新生成或未锁定的转录公式：主体分式用 `\\dfrac`；上标（含指数）、下标和嵌套内层分式用 `\\frac`。"
    "例：`$\\dfrac{x+1}{x-1}$`、`$2^{\\frac{n+1}{2}}$`、`$\\dfrac{1+\\frac{1}{x}}{2}$`。"
    "原文显式 `\\tfrac`/`\\cfrac` 保留；锁定公式及其 ID 优先原样保留，不受本规则改写。\n"
)


COMMON_OCR_PROMPT = (
    "转录文字、公式、括号、来源，只输出结果；不输出代码块、前言。看不清公式/符号标[公式待核对]，不得猜数字、正负号、条件或字形。\n"
    "【LaTeX】数学片段在`$...$`或`$$...$$`中，中文在外；已有定界符不得重复包裹。锁定ID `[[MBM_...]]`、结构和协议原样保留；独立 equation/align/gather/multline、tabular 原样；cases/aligned/array 整体置于数学环境。\n"
    "变量、坐标、上下标、函数、方程、不等式、集合、区间、分式、几何符号均为数学：`$f(x_1) \\leqslant f(x_2)$`、`$\\triangle PQR$`。\n"
    f"{FRACTION_STYLE_RULE}"
    "向量字形按图：粗体斜体用 `\\boldsymbol`，明确的粗体正体保留 `\\mathbf`，箭头保留 `\\vec` 或 `\\overrightarrow`；不凭题意改字形。\n"
    "选择题用完整 `\\begin{choices}...\\end{choices}`，每项以 `\\item` 开头并去掉原 A/B/C/D 标号；填空用 `\\fillin`；小问和推导以空行分段。\n"
    "表格用 `tabular` 保留行列，合并格用 `multicolumn/multirow`；标题、小问和图号顺序不变。\n"
    "多图/表内图在原位写 `[插图待补: 图1]`（无图号按阅读顺序编号），表格内占位留在所属单元格；勿描述、猜测或重绘。过滤 \\, 与 \\!。"
)

ILLUSTRATION_BOX_PROMPT = (
    "\n仅当全图恰有一幅且位于表格外的独立几何/函数图时，在文末追加"
    " `[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`（0-100 整数）；多图或无图时绝不输出该标记。"
)


CLASSIFICATION_PRIORITY_RULE = (
    "多模块融合题定位：先识别解题过程中实际参与推导的全部教材模块；若涉及两个或以上候选模块，"
    "必须按上方教材大纲的排列顺序，选择位置最靠后的模块作为最终分类。先比较学段从上到下的顺序，"
    "若属于同一学段，再比较章节从前到后的顺序；不得按考点在题干中的出现先后、篇幅多少或主次印象决定。"
    "例如一道题同时实质考查必修一“三角函数”和必修二“平面向量及其应用”，最终必须定位到必修二的"
    "“平面向量及其应用”。仅作为背景条件被提及、且解题无需使用的内容不计入候选模块。"
)


def build_curriculum_text(curriculum: dict) -> str:
    """Render the active curriculum as a compact prompt fragment."""

    lines = []
    for book, chapters in curriculum.items():
        lines.append(f"- {book}: {list(chapters.keys())}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_classification_system_prompt(curriculum: dict) -> str:
    curriculum_text = build_curriculum_text(curriculum)
    return (
        "你是一个专门为教材分类的 AI 专家。请分析以下输入的题目，将其归入特定的教材体系中。\n"
        "【可选教材范围及各章名称】:\n"
        f"{curriculum_text}\n"
        "【分类规则】:\n"
        "1. 仔细阅读并推导题目考点。\n"
        "2. 必须在上面的可选教材范围中为本题挑选最合适的一个【学段】（例如：必修一）和一个【所属章节】（例如：5. 三角函数，必须是可选章节中的精确字符串）。\n"
        f"3. {CLASSIFICATION_PRIORITY_RULE}\n"
        "4. 只判定粗粒度题型 `question_form`：填空题为 `fill_in_blank`，解答题为 `detailed_answer`，任何选择题一律为 `choice`，无法可靠判断时为 `unknown`。严禁输出或猜测 `single_choice`、`multi_choice`、单选题、多选题。题干出现 `\\fillin` 时应判为填空题，出现 `\\begin{choices}` 时应判为选择题。\n"
        "5. 你的输出必须是一个合法的 JSON 字符串，包含且仅包含以下三个 key，不要有任何多余的 Markdown 标记、代码块或解释文字：\n"
        "{\n"
        '  "compulsory": "学段名称",\n'
        '  "chapter": "具体章节名称",\n'
        '  "question_form": "choice / fill_in_blank / detailed_answer / unknown"\n'
        "}\n"
        "不要包含 ```json ``` 标记，只输出最干净的 JSON。"
    )


def build_ai_solve_prompts(
    question_type: str,
    content: str,
    ocr_result: str = "",
    custom_prompt: str = "",
) -> tuple[str, str]:
    """Build the system/user prompt pair for the single-question solver."""

    type_mapping = {
        "single_choice": "单选题",
        "multi_choice": "多选题",
        "fill_in_blank": "填空题",
        "detailed_answer": "解答题",
    }
    type_str = type_mapping.get(question_type, "数学题")

    if question_type == "single_choice":
        first_block_header = "\\\\textbf{【参考答案】}"
        format_rules = (
            "【必须包含的结构化板块 (使用 LaTeX 粗体 `\\\\textbf{...}`)】:\n"
            "1. \\\\textbf{【参考答案】}：最开头第一行直接醒目输出该单选题的唯一正确选项字母（如 A、B、C 或 D），严禁包含长篇推导。\n"
            "2. \\\\textbf{【解析过程】}：详细写出选项推导、排除理由与分析步骤。\n"
            "3. \\\\textbf{【解析思路】}：概括解题核心突破口与思考脉络。\n"
            "4. \\\\textbf{【核心知识点】}：列出关键公式、定理或思想方法。"
        )
    elif question_type == "multi_choice":
        first_block_header = "\\\\textbf{【参考答案】}"
        format_rules = (
            "【必须包含的结构化板块 (使用 LaTeX 粗体 `\\\\textbf{...}`)】:\n"
            "1. \\\\textbf{【参考答案】}：最开头第一行直接醒目输出该多选题的所有正确选项字母（如 AB、ACD），严禁包含长篇推导。\n"
            "2. \\\\textbf{【解析过程】}：详细写出各个选项的逐一推导、证明与分析步骤。\n"
            "3. \\\\textbf{【解析思路】}：概括解题核心突破口与思考脉络。\n"
            "4. \\\\textbf{【核心知识点】}：列出关键公式、定理或思想方法。"
        )
    elif question_type == "fill_in_blank":
        first_block_header = "\\\\textbf{【参考答案】}"
        format_rules = (
            "【必须包含的结构化板块 (使用 LaTeX 粗体 `\\\\textbf{...}`)】:\n"
            "1. \\\\textbf{【参考答案】}：最开头第一行直接醒目输出该填空题最终正确答案（如具体数值、公式、集合或区间），严禁包含长篇推导。\n"
            "2. \\\\textbf{【解析过程】}：详细写出求解推导与分析步骤。\n"
            "3. \\\\textbf{【解析思路】}：概括解题核心突破口与思考脉络。\n"
            "4. \\\\textbf{【核心知识点】}：列出关键公式、定理或思想方法。"
        )
    else:
        first_block_header = "\\\\textbf{【规范解答】}"
        format_rules = (
            "【必须包含的结构化板块 (使用 LaTeX 粗体 `\\\\textbf{...}`)】:\n"
            "1. \\\\textbf{【规范解答】}：符合高考卷面得分规范的标准解答步骤，写齐必要推理逻辑与定理依据，无冗余废话。\n"
            "2. \\\\textbf{【解析思路】}：若有多个小问（如 (1)、(2)），按小问分点概括破题脉络与定理转化（可一句话点拨易错陷阱或另解）。\n"
            "3. \\\\textbf{【核心知识点】}：列出关键公式、定理或思想方法。"
        )

    system_instructions = (
        "你是一位极其严谨的资深高中数学教研专家。请解答用户输入的高中数学题目。\n"
        "【解题纪律与超纲禁令】\n"
        "1. 严禁超纲：允许使用普通高中课程中的导数及其应用；禁止使用大学微积分方法（洛必达法则、泰勒展开、积分等）或未给定的高等结论。\n"
        "2. 逻辑严密与极简凝练：推导过程必须逻辑完备、因果严谨（写明公理定理前提，不盲目跳步），但语言精练直奔得分点，拒绝任何多余的口水话或过度解释。\n"
        f"3. 零多余前言尾注：回答必须干净地从结构化板块开始，第一个字符必须是“{first_block_header}”，严禁包含任何问候语、前言、导语或尾注总结。\n"
        "【LaTeX 与段落换行规范】\n"
        "1. 必须使用标准 LaTeX 语法书写公式（行内 $...$，行间 $$\\n...\\n$$）。绝对禁止使用 Markdown 的 ** 双星号加粗语法，标题必须使用 LaTeX 粗体 `\\\\textbf{...}`。\n"
        "2. 物理换行与空行规范 (极重要)：在分步推导、证明步骤（如“解：”、“(1) 证明：”、“因为”、“所以”、“故”）、不同小问以及标题与段落之间，必须使用空行/双回车（\\n\\n）分隔！单回车无法实现物理换行。\n"
        f"{FRACTION_STYLE_RULE}"
        f"{format_rules}\n"
        "请直接输出上述结构化板块。"
    )

    user_prompt = f"题目类型: {type_str}\n"
    if ocr_result.strip():
        user_prompt += (
            "已有的 OCR 识别解析/草稿内容如下，请在此基础上进行润色、修正、细化或简化，并生成最终解答步骤：\n"
            f"{ocr_result}\n\n"
        )
    if custom_prompt:
        user_prompt += f"补充引导指令: {custom_prompt}\n"
    # Keep one LaTeX backslash in the model-visible prompt headings. The
    # request serializer adds JSON escaping separately.
    system_instructions = system_instructions.replace("\\\\textbf", "\\textbf")
    user_prompt += f"题干内容:\n{content}"
    return system_instructions, user_prompt


def build_paper_selection_prompts(
    teacher_prompt: str,
    limit: int,
    candidates: list[dict],
    is_review_intent: bool,
) -> tuple[str, str]:
    """Build prompts for pedagogical AI paper selection."""

    review_hint = (
        "\n特别注意：教师明确要求提取【复习/考过/做过的题目】，请优先从候选池中遴选 usage_count > 0 的试题！\n"
        if is_review_intent
        else ""
    )
    system_prompt = (
        "你是一位极其资深的高中数学教研组长和智能命题专家。\n"
        "请根据教师输入的自然语言组卷需求，从给出的候选试题池中遴选出最符合教学意图、涵盖关键结构情形、具备错解检验能力的题目。"
        f"{review_hint}\n"
        "【输出格式规范】\n"
        "必须且只能返回一个可解析的合法 JSON 对象，绝对禁止包含 Markdown 格式标记代码块（例如不要写 ```json ... ```）：\n"
        "{\n"
        '  "selected_ids": [12, 45, 89],\n'
        '  "ai_analysis": "【教研组卷分析与情形覆盖】\\n1. 考察重点：...\\n2. 难度与结构情形：涵盖保底情形与易错辨析...\\n3. 教学建议：..."\n'
        "}"
    )
    user_content = (
        f"【教师组卷需求】: {teacher_prompt}\n"
        f"【需要挑选的题目数量】: {limit} 道\n"
        f"【候选试题池】:\n{json.dumps(candidates, ensure_ascii=False)}"
    )
    return system_prompt, user_content


def build_pdf_parse_system_prompt(curriculum: dict, generate_answers_bool: bool) -> str:
    curriculum_text = build_curriculum_text(curriculum)
    
    if generate_answers_bool:
        answer_rule = (
            "【答案提取与生成规则】:\n"
            "- 若原试卷自带答案，请完整提取并在 `answer_markdown` 开头标明 `[EXTRACTED_ORIGINAL]`。\n"
            "- 若原试卷缺答案，请自动推导生成标准解答步骤填入 `answer_markdown`。"
        )
    else:
        answer_rule = (
            "【答案提取规则（严禁主动生成）】:\n"
            "- 仅提取原试卷中明确自带的原版参考答案与解析，并在 `answer_markdown` 开头标明 `[EXTRACTED_ORIGINAL]`。\n"
            "- 若原试卷无答案，必须将 `answer_markdown` 设为空字符串 \"\"，绝对不要现场推导或编造答案！"
        )

    system_instructions = (
        "你是一位资深高中数学教研专家与 LaTeX 排版大师。请阅读输入的试卷源码，智能切分为题目列表 JSON。\n\n"
        "【可选教材范围与章节】:\n"
        f"{curriculum_text}\n"
        "【核心拆题与分类规范】:\n"
        "1. 字段分类：挑选精确匹配的学段 `category_compulsory` 与章节 `category_chapter`；题型 `question_type`（single_choice / multi_choice / fill_in_blank / detailed_answer）；难度 `difficulty`（easy_error / challenge / qiangji）；剥离题号与出处信息（如 2024·全国·高考真题）填入 `source`。\n"
        f"1.1 {CLASSIFICATION_PRIORITY_RULE}\n"
        "2. 文字与插图忠实保留：100% 完整保留题干所有汉字，绝对禁止删除“（如图）”、“如图所示”、“如右图所示”等几何指代描述！绝对保留 Markdown/LaTeX 原有的图片链接（如 `![](/static/uploads/...)` 或 `\\includegraphics{...}`），并将其 URL/文件名提取至 `referenced_images` 数组中。如输入中出现 `[公式待核对]`、`[公式结构待核对]`、`[特殊字符待核对]` 或“公式无法安全提取”，必须原样保留标记及紧随的预览图，绝不得猜测、补写或替换公式。\n"
        "2.1 公式锁定协议：若正文出现 `<mathbank-math id=\"MBM_...\">完整公式</mathbank-math>`，标签内公式在原位置完整可见，可用于理解、分类和解题，但它是只读来源。输出题干或原版答案时，优先在同一语义位置将每个标签替换为且仅替换为一次 `[[对应的完整 id]]`，例如 `[[MBM_xxx_0001]]`；禁止遗漏、重复、改名或把同一 id 放入多个题目。若未使用 ID 引用，则必须在原位置逐字保留标签内的完整公式和定界符，不得省略、化简、合并或改写公式；重复出现的相同公式也须分别保留。若需要生成新解析，可另写普通 LaTeX 公式，但不得在新解析中重复这些锁定 id。\n"
        "3. 公式格式化与排版环境：选择题选项统一格式化为 `\\begin{choices} \\item ... \\end{choices}` 环境；填空题下划线统一使用标准的 `\\fillin` 宏；文本加粗必须使用 `\\textbf{...}`（严禁双星号 `**`）。`content` 与 `answer_markdown` 中的数学片段须完整置于行内 `$...$` 或独立 `$$...$$` 中，中文留在外部；保留已有定界符，不得重复包裹。公式锁定 ID `[[MBM_...]]`、完整数学/表格结构和协议标记保持原样。正确示例：`$x_1$`、`$y^2$`、`当 $x \\geqslant 0$ 时`、`$f(x_1) \\leqslant f(x_2)$`、`$C: \\dfrac{x^2}{a^2}+\\dfrac{y^2}{b^2}=1$`、`$\\triangle PQR$ 的面积`；禁止输出裸露的 `x_1`、`y^2`、`\\frac`、`\\dfrac`、`\\geqslant`、`\\subseteq` 或 `\\triangle`。\n"
        f"{FRACTION_STYLE_RULE}"
        "3.1 向量字形必须忠实原文：粗体斜体使用 `$\\boldsymbol{a}$`，明确的粗体正体保留 `$\\mathbf{a}$`，箭头形式保留 `\\vec` 或 `\\overrightarrow`；不得仅凭题意改变字形。\n"
        "4. 符号与公式规范：仅对含义明确的 Unicode 数学字符与结构（如 √、∈、α、β以及分子/分母边界清晰的分式）规范化为等价 LaTeX 语法（如 `\\sqrt{...}`, `\\dfrac{...}{...}`, `\\in`, `\\alpha`），分式大小按上述分式规则。不得将普通字母 `j`、`p` 等根据语境猜成希腊字母或分式；不得根据题意自行重建原文中已损坏、缺失或标记待核对的公式。\n"
        "4.1 PDF 跨页协议：`<!-- MATHBANK_PDF_PAGE:N -->` 仅表示后续原文来自 PDF 第 N 页，用于来源追踪，不是题目边界，也不得出现在输出题干中。若一道题的题干、公式、表格、选项或解析跨越页标，必须按上下文合并为同一道完整题目，禁止按页拆成两题。\n"
        "5. 换行与段落规范：不同小问（如 (1)、(2)、(i)、(ii)）、证明推导步骤与自然段落之间，必须使用双换行/空行（`\\n\\n`）分隔！\n"
        "6. 题干净化与客观题答案：若题干/括号/下划线中夹带了答案，必须擦除还原为纯净的空占位符；仅在答案规则允许提取或生成时，客观题（选择题/填空题）才在 `answer_markdown` 第一行输出最终答案；若规则要求空字符串则保持空白，不得自行生成。\n"
        f"{answer_rule}\n\n"
        "【输出约束与 JSON 格式】:\n"
        "必须且只能输出严格合法的 JSON 对象，绝对不要包裹 ```json Markdown 代码块！字符串内部换行必须输出 JSON 转义序列 `\\n`（反斜杠+n），LaTeX 命令的反斜杠必须按 JSON 规范转义为双反斜杠 `\\\\`。\n"
        "{\n"
        '  "questions": [\n'
        '    {\n'
        '      "content": "纯净题干（包含 LaTeX 排版与图片标记）",\n'
        '      "answer_markdown": "答案与解析",\n'
        '      "question_type": "single_choice / multi_choice / fill_in_blank / detailed_answer",\n'
        '      "category_compulsory": "学段名称",\n'
        '      "category_chapter": "章节名称",\n'
        '      "difficulty": "easy_error / challenge / qiangji",\n'
        '      "source": "出处信息或 null",\n'
        '      "referenced_images": ["/static/uploads/xxx.png"]\n'
        '    }\n'
        '  ]\n'
        "}\n"
    )
    return system_instructions


def build_import_parse_system_prompt(curriculum: dict) -> str:
    """Prompt for pasted and uploaded single-file TeX paper parsing."""
    return build_pdf_parse_system_prompt(curriculum, generate_answers_bool=False) + (
        "\n【单文件 TeX 源码专项规则】:\n"
        "1. 输入已由本地预处理器提取 document 正文并清除普通注释；不得把 documentclass、usepackage、页眉页脚或宏定义上下文当成题目。\n"
        "2. 识别 question/problem/exercise/enumerate/item、parts/subparts/part、choices/choice/CorrectChoice、tasks/task 等常见结构。每个顶层题目只输出一次，小问必须保留在所属大题内。\n"
        "3. `[MATHBANK_ORIGINAL_CORRECT]` 只表示原卷标注的正确选项：将其转换为带 `[EXTRACTED_ORIGINAL]` 的答案字母，题干 choices 中不得保留该标记。\n"
        "4. solution/answer/analysis/proof 等原卷答案环境必须关联到前一道题并标记 `[EXTRACTED_ORIGINAL]`，不得把答案误拆成新题。\n"
        "5. `[MATHBANK_TEX_MACRO_CONTEXT_BEGIN/END]` 之间仅是未展开宏的定义上下文。应将正文中的自定义宏转换为等价标准 LaTeX，不得把上下文或未定义宏原样写进题干。\n"
        "6. 完整保留 tabular/array/matrix/cases/aligned 等数学和表格结构；TikZ 源码不得擅自改写，若无法安全转为题干插图则原样保留并提示人工核对。\n"
        "7. includegraphics 的原始文件名必须同时放入 referenced_images；不要虚构、改名或丢弃图片引用。\n"
        "8. input/include 指向的外部子文件在单文件模式下不可读取，不得根据文件名猜测缺失题目。\n"
    )


def build_latex_error_explanation_prompts(diagnostic: dict) -> tuple[str, str]:
    """Build a privacy-minimized prompt for explaining one compile error."""
    system_prompt = (
        "你是一名高中数学试卷 LaTeX 排版故障解释助手。"
        "请把编译错误解释成普通教师能看懂的中文，并给出可操作的修复方法。"
        "只能依据提供的错误和局部源码判断，不得编造不存在的题目内容、宏包或文件。"
        "不要重写整份试卷，不要改变数学含义。"
        "必须只返回严格 JSON 对象，字段为 summary、cause、location、fixes、package、command；"
        "fixes 必须是 1 至 4 条短句组成的数组。"
    )
    user_prompt = (
        "请解释下面这一个 XeLaTeX 编译错误：\n"
        f"本地初步判断：{diagnostic.get('summary', '')}\n"
        f"技术错误：{diagnostic.get('technical_error', '')}\n"
        f"疑似命令：{diagnostic.get('command', '')}\n"
        f"疑似宏包：{diagnostic.get('package', '')}\n"
        f"位置：{diagnostic.get('location', '')}\n"
        "出错位置附近的最小源码片段：\n"
        f"{diagnostic.get('source_context', '')}"
    )
    return system_prompt, user_prompt


def build_tikz_draw_prompt(
    latex_content: str = "",
    multimodal: bool = True,
    *,
    instruction: str = "",
    existing_tikz: str = "",
) -> str:
    """Build the prompt used by OCR reconstruction and the manual workbench."""

    stem = (latex_content or "").strip() or "暂无题干或解答上下文"
    guidance = (instruction or "").strip()
    current_code = (existing_tikz or "").strip()
    source_description = (
        "用户同时提供了一张参考图。请以参考图的拓扑关系、点线位置、"
        "字母标注和实虚线为主要视觉依据，文字要求优先用于澄清或修改局部细节。"
        if multimodal
        else "本次没有参考图，请依据用户要求与数学上下文建立合理、简洁的示意图。"
    )
    prompt = (
        "你是一个 LaTeX/TikZ 数学绘图专家。\n"
        f"{source_description}\n"
        "【数学上下文】\n"
        f"```latex\n{stem}\n```\n"
    )
    if guidance:
        prompt += f"【用户绘图或修改要求】\n{guidance}\n"
    if current_code:
        prompt += (
            "【当前 TikZ 源码】\n"
            f"```latex\n{current_code}\n```\n"
            "请在当前源码基础上完成修改，保留未被新要求否定的正确结构。\n"
        )
    prompt += (
        "【输出与绘图规范】\n"
        "1. 只输出一个 ```latex ... ``` 代码块，其中必须是完整的 "
        "\\begin{tikzpicture}...\\end{tikzpicture}；不得输出闲聊或解释。\n"
        "2. 优先保证数学拓扑正确：点的连接、交点、平行、垂直、角标记、"
        "箭头、实虚线和坐标方向不得凭空改变。\n"
        "3. 点名和符号必须优先使用参考图、用户要求或题干中已出现的名称，"
        "不得无据增加字母。\n"
        "4. 绘图比例、留白与标注位置应适合试卷黑白打印；遮挡线使用 dashed，"
        "避免装饰性背景、大面积填色和不必要的线头。\n"
        "5. 仅使用常规 TikZ/PGFPlots 与系统已支持的库，不得读写外部文件、"
        "调用 shell 或依赖网络资源。"
    )
    return prompt


def build_tikz_correction_prompt(
    tikz_code: str,
    user_guidance: str = "",
    compile_error_log: str = "",
    rendered_comparison: bool = False,
) -> str:
    """Build either the visual-comparison or compile-error TikZ repair prompt."""

    if rendered_comparison:
        prompt = (
            "你是一个 TikZ 几何绘图专家。对比 Image A（原题目插图）与 Image B（TikZ 当前渲染图）：\n"
            "```latex\n"
            f"{tikz_code}\n"
            "```\n"
            "任务：对比拓扑与细节差异（点位置、实虚线、字母、箭头等），修改 TikZ 代码使其 100% 还原 Image A。\n"
            "必须使用 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture}...\\end{tikzpicture}），严禁输出闲聊文字。"
        )
        if user_guidance.strip():
            prompt += f"\n\n【人工修改意见】：请务必优先遵循：{user_guidance.strip()}"
        return prompt

    prompt = (
        "你是一个 LaTeX/TikZ 几何绘图专家。下面第一张图是原始题目的正确几何插图。\n"
        "我试图用 TikZ 绘制它，但我的代码在编译时报错了。\n"
        "这是我当前编写的代码：\n"
        "```latex\n"
        f"{tikz_code}\n"
        "```\n"
        f"编译器的具体报错日志如下：\n```text\n{compile_error_log}\n```\n"
        "请完成以下任务：\n"
        "1. 结合原始图片以及报错日志，找出代码中的语法错误或逻辑死循环。\n"
        "2. 修正这些语法错误，使其能通过 LaTeX 编译，并精准绘制出原始图中的几何图形。\n"
        "3. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分，或者包含它们）。请确保不输出任何与代码无关的开场白或闲聊文字。"
    )
    if user_guidance.strip():
        prompt += (
            "\n\n【人工修改和纠错指导意见】：\n用户指出了当前图形的以下具体错误或修改意见，请你在生成修正代码时务必优先且绝对遵循这一意见：\n"
            f"{user_guidance.strip()}"
        )
    return prompt
