"""Central prompt constants and pure prompt builders for MathBank AI flows."""

import json


COMMON_OCR_PROMPT = (
    "请精确识别并提取图像中的所有文字与数学公式（不得遗漏方括号与题目来源），直接输出转录结果，严禁包含任何前言或解释。\n"
    "【排版与 LaTeX 语法规范】:\n"
    "1. 公式级包裹：仅对纯数学符号、方程、集合与代数式使用 LaTeX 包裹（行内 $...$，独立 $$...$$）。严禁整段中文包裹 LaTeX，严禁在公式内部滥用 \\text{...} 包裹大段中文。\n"
    "2. 变量与坐标包裹：几何点符号、单个字母变量及坐标表达式（如 $(1,2)$）必须严格包裹单美元符号 $...$。\n"
    "3. 选择题 choices 环境规范：选项部分统一格式化为 LaTeX 的 `choices` 环境（使用 \\begin{choices} 和 \\end{choices} 包裹，每项以 \\item 开头，剥离原本的 A., B., C., D. 标号前缀）。\n"
    "4. 空行换行规范：在多行推导、解答步骤或小标号（如 (1), (2), ①, ②）之间，必须使用空行/双回车（\\n\\n）分隔。\n"
    "5. 纯净符号：过滤 \\, 或 \\! 等干扰渲染的薄空格。"
)

ILLUSTRATION_BOX_PROMPT = (
    "\n6. 几何插图识别：若包含几何/函数插图，务必在文末追加归一化百分比包围框："
    "`[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`（数值 0-100 整数，无插图则绝对不输出）。"
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
        "3. 你的输出必须是一个合法的 JSON 字符串，包含且仅包含以下两个 key，不要有任何多余的 Markdown 标记、代码块或解释文字：\n"
        "{\n"
        '  "compulsory": "学段名称",\n'
        '  "chapter": "具体章节名称"\n'
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
        "1. 严禁超纲：解题思路与技巧必须完全限制在中国普通高中数学大纲范围内（严禁使用微积分、洛必达法则、泰勒展开等大学高等数学方法）。\n"
        "2. 逻辑严密与极简凝练：推导过程必须逻辑完备、因果严谨（写明公理定理前提，不盲目跳步），但语言精练直奔得分点，拒绝任何多余的口水话或过度解释。\n"
        f"3. 零多余前言尾注：回答必须干净地从结构化板块开始，第一个字符必须是“{first_block_header}”，严禁包含任何问候语、前言、导语或尾注总结。\n"
        "【LaTeX 与段落换行规范】\n"
        "1. 必须使用标准 LaTeX 语法书写公式（行内 $...$，行间 $$\\n...\\n$$）。绝对禁止使用 Markdown 的 ** 双星号加粗语法，标题必须使用 LaTeX 粗体 `\\\\textbf{...}`。\n"
        "2. 物理换行与空行规范 (极重要)：在分步推导、证明步骤（如“解：”、“(1) 证明：”、“因为”、“所以”、“故”）、不同小问以及标题与段落之间，必须使用空行/双回车（\\n\\n）分隔！单回车无法实现物理换行。\n"
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
    # Build curriculum text for instructions
    curriculum_text = ""
    for book, chapters in curriculum.items():
        curriculum_text += f"- {book}: {list(chapters.keys())}\n"
        
    if generate_answers_bool:
        answer_generation_rule = "   - 若试卷缺答案，请自动生成标准解答步骤填入 `answer_markdown`（写明推理逻辑与【解析思路】）。\n"
    else:
        answer_generation_rule = (
            "   - 【严禁生成答案】：未开启答案生成功能。除原试卷明确自带原版答案外（提取时在开头标注 [EXTRACTED_ORIGINAL]），必须将 `answer_markdown` 设为空字符串 \"\"！严禁主动推导或编造答案。\n"
        )
    
    system_instructions = (
        "你是一位资深教研专家与 LaTeX 排版大师。请解析输入的试卷 LaTeX 源码，智能拆解为题目列表 JSON。\n"
        "【可选教材范围及各章名称】:\n"
        f"{curriculum_text}\n"
        "【拆题与分类规则】:\n"
        "1. 题干拆解与公式自愈：保留并提纯题目格式。如果输入文本包含从原生电子 PDF 提取的 Unicode 数学符号或折行分式（如 √、∈、α、β、上下标等），必须自动将其规范化转译为标准 LaTeX 语法（如 \\sqrt{...}、\\frac{...}{...}、\\in、\\alpha）；选择题选项统一格式化为 \\begin{choices} \\item ... \\end{choices} 环境；填空题下划线统一使用标准的 \\fillin 宏。挑选精确的【学段】与【所属章节】。\n"
        "2. 插图路径保护：若包含 `/static/uploads/tmp/pdf_crop_` 临时裁剪路径，必须 100% 完整原样保留该 URL（不可改名或重命名），并提取至 `referenced_images` 数组中。\n"
        "3. 题型与难度判断：question_type 设为 single_choice/multi_choice/fill_in_blank/detailed_answer；difficulty 设为 easy_error/challenge/qiangji。\n"
        "4. 答案与解析处理：\n"
        f"{answer_generation_rule}"
        "5. 题干净化（去答案）：若选择题/填空题题干中保留了答案（如括号中含字母或下划线内含数值），必须擦除还原为纯净的占位符；客观题必须在 `answer_markdown` 第一行醒目输出最终答案。\n"
        "6. JSON 与排版规范：必须返回严格合法的 JSON；字符串内部换行必须写成 JSON 转义序列 `\\n`（反斜杠+n），严禁直接放入真实回车或制表符；LaTeX 命令的反斜杠必须按 JSON 规范转义为双反斜杠 `\\\\`。分步推导/段落之间可在解析后的文本中使用双换行；加粗必须使用 `\\\\textbf{...}`（严禁双星号 `**`）。\n"
        "7. 出处提取：识别题干开头/结尾的来源信息（如 2024·上海·高考真题），提取至 `source` 字段并从题干中剥离题号与出处。\n"
        "8. 输出 JSON 格式（根键为 `\"questions\"` 数组，只输出干净 JSON，不要包含 ```json 代码块）：\n"
        "{\n"
        "  \"questions\": [\n"
        "    {\n"
        "      \"content\": \"纯净题干内容\",\n"
        "      \"answer_markdown\": \"答案与解析\",\n"
        "      \"question_type\": \"single_choice / multi_choice / fill_in_blank / detailed_answer\",\n"
        "      \"category_compulsory\": \"学段名称\",\n"
        "      \"category_chapter\": \"章节名称\",\n"
        "      \"difficulty\": \"easy_error / challenge / qiangji\",\n"
        "      \"source\": \"具体出处或 null\",\n"
        "      \"referenced_images\": [\"/static/uploads/tmp/pdf_crop_xxx.png\"]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "注意：只输出最干净的 JSON，千万不要包含 ```json ``` 等 Markdown 代码块标记！如果试卷中没有插图，referenced_images 数组留空。"
    )
    
    return system_instructions


def build_import_parse_system_prompt(curriculum: dict) -> str:
    # Build curriculum text for instructions
    curriculum_text = ""
    for book, chapters in curriculum.items():
        curriculum_text += f"- {book}: {list(chapters.keys())}\n"
        
    answer_generation_rule = (
        "   - **【拆解阶段只提取原版答案，不现场推导】**：在当前拆卷阶段，你只负责提取输入的原始试卷 LaTeX 源码中【本来就明确显式写着】的原版参考答案或解析（如看到“【答案】”、“解析：”、“解：”等且包含解答文本）。提取时请在 `answer_markdown` 的最开头原样打上 `[EXTRACTED_ORIGINAL]` 标记。\n"
        "   - 如果原始试卷源码中该题只有题干，**绝对不要**现场推导、计算、猜测或生成解答过程！直接将该题的 `answer_markdown` 设为绝对的空字符串 `\"\"`！\n"
    )
    
    system_instructions = (
        "你是一位极其严谨的教研专家与 LaTeX 排版大师。请阅读并解析用户输入的【整张试卷 LaTeX 源码】，将其智能拆解为独立的题目列表，并分析每一题属性。\n"
        "【可选教材范围及各章名称】:\n"
        f"{curriculum_text}\n"
        "【分类规则】:\n"
        "1. 仔细阅读并拆解试卷中的每一道题目。请保留题目中的 LaTeX 公式和格式。\n"
        "2. 为每道题挑选最合适的一个【学段】（例如：必修一）和一个【所属章节】（例如：5. 三角函数，必须是可选章节中的精确字符串）。\n"
        "3. 如果题目包含图片引用标签（形如 \\includegraphics{filename.png} 或类似标记，或文字描述引用的图片名），请将其提取并放入 `referenced_images` 字段中。\n"
        "4. 判断题目类型：single_choice（单选题，若选项是 A/B/C/D 形式，建议保留选项在 `content` 尾部，并归类为 single_choice）、multi_choice（多选题）、fill_in_blank（填空题）、detailed_answer（解答题）。\n"
        "5. 判断题目难度等级，由于是私人题库，请将其归类为：easy_error（易错题）、challenge（挑战题）或 qiangji（强基题）。\n"
        "6. **智能识别答案与解析**：试卷中可能只包含题干，也可能同时包含答案和解析。请仔细辨别：\n"
        "   - 如果试卷中包含答案（如参考答案、解答过程等），请将其提取到 `answer_markdown` 字段。\n"
        f"{answer_generation_rule}"
        "7. **【题干智能去答案规范】**：很多时候，输入的试卷 LaTeX 源码中，某些选择题或填空题的题干部分直接保留了答案（例如：选择题括号中直接写了答案字母如“（ B ）”、“(C)”；填空题下划线命令内部直接填了答案数字或符号如“\\underline{\\quad 7 \\quad}”、“\\underline{x^2}”）：\n"
        "   - 必须主动识别并清除这些题干中保留的答案，还原为纯净的空占位符！\n"
        "   - 对于选择题，将括号内代表答案的字母剥离清除，还原为干净的括号（如“（  ）”或“（ ）”）。\n"
        "   - 对于填空题，将下划线中代表答案的文本挖空，替换为纯粹的 LaTeX 空白占位符（如“\\underline{\\quad\\quad}”或“\\underline{\\quad \\quad}”）。\n"
        "   - 对于客观题（选择题/填空题），必须在 `answer_markdown` 最开头第一句/第一行【直接、醒目】输出最终正确答案（选择题如选项字母“A”或“ABD”，填空题如需填入的正确数值/公式/表达式“\\sqrt{3}”或“(-1, 1)”），然后再在下面呈现详细解析步骤。\n"
        "8. **【排版与字符格式规范】（极其重要）**：\n"
        "   - **推荐使用标准的 LaTeX 列表与排版环境**：为了方便用户直接复制高价值的 LaTeX 源码，推荐在需要列表、段落或编号排版时输出标准的 LaTeX 语法环境，如 `\\begin{itemize}`, `\\end{itemize}`, `\\item`, `\\begin{enumerate}`, `\\end{enumerate}`, `\\begin{center}`, `\\end{center}`。LaTeX 标记（如 `$` 或 `$$`）应该包围所有纯数学公式。\n"
        "   - **【加粗文本排版规范】**：在输出需要加粗的结构化文本时，**绝对禁止**使用 Markdown 的双星号 `**加粗文本**` 语法，必须且只能使用 LaTeX 标准的 `\\\\textbf{加粗文本}` 语法。\n"
        "   - **严格遵守 JSON 转义**：在 `content` 或 `answer_markdown` 字符串内部换行时，必须输出 JSON 转义序列 `\\n`（反斜杠+n），严禁直接放入真实回车、制表符等控制字符；所有 LaTeX 命令的反斜杠必须按 JSON 规范转义为双反斜杠 `\\\\`。JSON 解析后系统会自动恢复真实换行与单个 LaTeX 反斜杠。\n"
        "9. **【出处智能提取规范】**：仔细辨认题干开头（如“1. (2024·上海·高考真题) 已知...”中的“(2024·上海·高考真题)”)或结尾是否包含年份、考试来源等括号标注的出处信息：\n"
        "   - 若有，必须将其完整提取至 `source` 字段中（去除外层括号），并在 `content` 字段中彻底剥离删除该出处标注以及前面的题号前缀（如“10.”、“1.”），只保留纯净的题目内容。\n"
        "   - 若无特定出处，则 `source` 字段设为 null 或不填。\n"
        "10. **你的输出必须是一个合法的 JSON 对象，其根键为 `\"questions\"`，对应的值为一个 JSON 数组（包含以下结构化对象）。不要有任何多余的 Markdown 标记、代码块或解释文字**：\n"
        "{\n"
        "  \"questions\": [\n"
        "    {\n"
        '      "content": "题干内容，包含 LaTeX 排版公式，且保留图片排版占位标记 (例如 ![插图](filename.png))",\n'
        '      "answer_markdown": "该题的答案与详细解析过程，使用标准 LaTeX 与 Markdown 排版",\n'
        '      "question_type": "single_choice / multi_choice / fill_in_blank / detailed_answer",\n'
        '      "category_compulsory": "人教A学段名称",\n'
        '      "category_chapter": "人教A章节名称",\n'
        '      "difficulty": "easy_error / challenge / qiangji",\n'
        '      "source": "提取出的具体出处（如 2019·全国·高考真题），没有则填 null",\n'
        '      "referenced_images": ["引用的原始插图文件名1.png", "fig2.jpg"]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "注意：只输出最干净的 JSON，千万不要包含 ```json ``` 等 Markdown 代码块标记！如果试卷中没有插图，referenced_images 数组留空。"
    )
    
    return system_instructions


def build_tikz_draw_prompt(latex_content: str = "", multimodal: bool = True) -> str:
    """Build the prompt used to reconstruct a question illustration as TikZ."""

    stem = latex_content or "暂无题干"
    if multimodal:
        return (
            "你是一个 LaTeX/TikZ 几何绘图专家。下面第一张图是从试卷题目中分割裁剪出来的几何插图局部。\n"
            "另外，这道数学几何题目的完整题干文本如下，请务必作为绘图逻辑参考：\n"
            f"```latex\n{stem}\n```\n"
            "请使用标准的 LaTeX TikZ 几何绘图语言，将这幅几何图形高精度重新绘制一遍。\n"
            "【绘图重要规范与自愈提示】：\n"
            "1. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分）。请确保不输出任何与代码无关的闲聊、问候或说明文字。\n"
            "2. 结合题干文本（如提及线线垂直、线面平行以及几何点的真实名称）来理解和校正剪切图中可能缺失、磨损或由于裁剪漏掉的字母。例如，如果题干提到 PA 垂直于面 ABC，但插图顶部顶点上面没有字母，请根据题意在顶部顶点标注为 'P'（绝对不要随意编造非题干中提及的字母，如 D 等）。\n"
            "3. 仔细识别并使用 `\\node` 或 `label` 标在对应的物理位置。重要被遮挡线条请使用 `dashed` 虚线绘制。不要在大片空白处留多余线头。"
        )

    return (
        "你是一个 LaTeX/TikZ 几何绘图专家。已知有一道数学几何题目，其文字描述和公式如下：\n"
        f"```latex\n{stem}\n```\n"
        "请仔细分析该题目中各几何元素之间的逻辑关系（如线面垂直、平行、坐标位置、夹角等），"
        "编写出一段最精确、美观的 LaTeX TikZ 代码来绘制这道题目的示意插图。\n"
        "【绘图重要规范】：\n"
        "1. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分）。请确保不输出任何与代码无关的闲聊或说明文字。\n"
        "2. 绘图比例和字母标注位置要协调美观，重要被遮挡线条请使用 `dashed` 虚线绘制。"
    )


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
