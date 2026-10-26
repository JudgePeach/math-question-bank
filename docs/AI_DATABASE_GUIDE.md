# Agent 只读取题指南

本指南面向通过终端或本地 HTTP 接口读取 MathBank 的 Agent。推荐流程是：**发现可用条件 → 检索知识点 → 小页搜索候选 → 按 ID 读取完整题目 → 查看必要图片及证据**。

命令行入口为 `scripts.search_questions`；查询使用只读 SQLite 连接和同一读取快照，不初始化、迁移或修改题库。CLI 与 Agent HTTP 接口共用读取模块。题目内容、解析和来源仍需按任务核对，检索结果不保证数学正确性或完整召回。

## 1. 先发现当前题库的能力与取值

从包含 `mathbank/` 和 `scripts/` 的项目根目录运行，使用项目可用的 Python 解释器：

```sh
python3 -m scripts.search_questions --capabilities --format json
```

先查看当前题库实际支持的题型、难度和教材版本，再构造筛选。不要把旧文档中的 `easy/medium/hard` 当作所有题库的固定枚举，也不要自行猜测自定义取值的含义。

在其他工作目录调用时，应先进入项目目录，或将项目根目录加入 Python 模块搜索路径；数据库和资源位置始终由 `mathbank.paths` 锚定，不随终端当前目录改变。CLI 不需要启动网页服务。

## 2. 知识点先定位到编码，再用编码找题

查询知识点目录，匹配名称、来源原名或别名：

```sh
python3 -m scripts.search_questions --catalog --knowledge-query "单调性" --format json -n 10
```

从结果中选择与任务相符的编码。例如，确认本库的 `1136` 对应所需知识点后：

```sh
python3 -m scripts.search_questions --knowledge-point 1136 --format json -n 10 --page 1
```

多个知识点可以重复传入，明确采用任一命中还是全部命中：

```sh
python3 -m scripts.search_questions --knowledge-point 1136 --knowledge-point 1138 --knowledge-mode all --format json -n 10
```

默认按主要考查知识点筛选；需要包含辅助知识点时增加 `--knowledge-role all`。`--knowledge-query` 用于知识点检索；直接筛题时会匹配目录候选，再查询相应编码的题目。`-q` 用于题文等文字搜索，知识点名称未出现在题干中，不代表题目没有考查它。遇到宽泛主题，应先检查目录候选，避免将所有相近知识点视为同一教学任务。

既有来源编码别名按正常目录资源解析为稳定编码，结果使用实际编码；不要据旧来源编码另建重复知识点。

可以叠加 `--source`、`--compulsory`、`--chapter`、`--knowledge`、`-t`、`-d` 等条件。教材册章小节是教材定位，独立知识点是考查标注，两者含义不同。使用 `--version-code` 时先核对该版本及其已有定位；缺少对应版本定位不能凭其他版本猜测。

```sh
python3 -m scripts.search_questions --knowledge-point 1136 --source "原创示例" --format json -n 5
```

来源文本只按已有记录筛选；“某机构”不等于“教师版讲义”，也不能证明题目已经与原书核对。需要限制教师版、学生版、讲义或题集时，应核查真实来源信息。

## 3. 按用途选择返回层次

| 层次 | 用途 | 阅读要求 |
| --- | --- | --- |
| `candidate` | 小页选择候选，减少上下文占用 | 用 ID、题干摘要、来源、知识点、答案及图片状态筛选；摘要不能用于直接解题 |
| `teaching` | 读取准备采用的完整题目 | 读取完整题干、答案解析、评析、必要配图及引用；尤其检查评析中的错印、待核与修订说明 |
| `evidence` | 核对知识点或来源依据 | 在完整题目基础上读取现有依据、教材映射及相关记录；缺少证据保持未知 |

JSON 搜索默认使用 `candidate`，默认每页 20 题。`-n` 指定每页数量，正常分页上限为 100；通过 `--page` 继续读取。返回中的 `total` 是总命中数，`items` 是本页题目；结合 `next_page` 和 `truncated` 判断是否还有结果，不把“返回 10 题”当成“全库只有 10 题”。

选定候选后，使用返回的真实 ID。以下 `123,456` 只是语法示例，需替换为本次查询的 ID：

```sh
python3 -m scripts.search_questions --ids 123,456 --profile teaching --format json
python3 -m scripts.search_questions --ids 123 --profile evidence --format json
```

按 ID 批量读取应检查 `requested_ids`、`missing_ids` 和返回题目。无效 ID 会报错；不存在 ID 会明确列出，不得将已返回部分称为完整材料。ID 是当前题库中的引用身份；动态连续序号 `seq_num` 只用于界面展示，增删或整理顺序后可以变化。跨库分享会分配新 ID。

`teaching` 保留完整题干和评析；`evidence` 进一步包含现有知识点依据、来源标签和 TikZ 材料。`has_answer` 只表示答案字段非空；知识点标注的 `confirmed` 不代表题干、解析或答案已经教师核验。读取返回的质量状态为未知时应如实保留，最终用于教学的题目仍应独立演算，核对定义域、参数范围、边界和图文关系。

## 4. 查看图片，保留其用途与路径语义

读取所选题目的资产清单：

```sh
python3 -m scripts.search_questions --ids 123 --assets --format json
```

资产项的 `roles` 区分 `content`（题干）、`answer`（解析）、`review`（评析）及 `reference`（绘图参考）。检查 `exists`、`status`、`width`、`height`、`media_type` 和 `size_bytes`，再通过视觉读取工具打开实际图片；仅收到图片路径不等于已经读过图片。参考图不能直接当作题目展示图，评析专用图也不应遗漏。

本地 CLI 可以提供经过校验的绝对文件路径 `local_path`；HTTP 提供安全同源路径 `url`，不返回本机绝对路径。`/static/uploads/...` 需要与实际 MathBank 服务地址组合，不能直接作为其他目录下 Markdown 的本地文件路径；远端 Agent 也不能假定能访问用户电脑的 `127.0.0.1`。

生成教案或课件时，应将采用且有权使用的图片复制到产物自己的资源目录，验证文件存在、图文对应和链接可用。图片缺失时保留缺失事实，回查原资料或选择其他题目，不补造原图。接口默认返回资产信息，避免无条件将全部图片以 Base64 塞入上下文。

## 5. 原命令继续兼容

旧参数 `-q/-n/-a/-t/-d/-r` 继续可用。默认 Markdown 模式保留完整题干的阅读方式，默认最多返回 50 题；`-a` 显示答案与评析。新 Agent 流程建议显式使用 JSON 和分页。

```sh
# 原有文字检索：完整题干，默认不显示答案
python3 -m scripts.search_questions -q "三角函数" -n 3

# 原有教师阅读方式：包含答案和评析
python3 -m scripts.search_questions -q "三角函数" -n 3 -a

# 原有变式组查询：这里的 123 需替换为真实题目 ID
python3 -m scripts.search_questions -r 123 -n 5 -a
```

旧 `-n -1` 显式不限量和大于100的数量要求为兼容保留，内部仍分批读取；日常 Agent 检索应使用每页不超过100题的有界分页。变式组查询和正文中的有向题间引用不是同一种关系；需要读懂“见例某某”时，应查看详情中的引用状态，不能仅凭原题号猜目标。

## 6. 参数速查

| 参数 | 含义 |
| --- | --- |
| `--capabilities` | 发现当前能力、题型、难度及版本等可用值 |
| `--catalog` | 查询知识点目录 |
| `--knowledge-query TEXT` | 按知识点名称、原名或别名检索 |
| `--knowledge-point CODE` | 按知识点编码筛题，可重复 |
| `--knowledge-mode any/all` | 多个知识点的匹配方式 |
| `--knowledge-role main/all` | 主要考查或包含辅助考查 |
| `-q/--query TEXT` | 文字查询；与独立知识点检索区别使用 |
| `--source TEXT` | 来源筛选 |
| `--compulsory/--chapter/--knowledge` | 教材册、章、小节筛选 |
| `--version-code CODE` | 指定读取教材版本 |
| `-t/--type`、`-d/--difficulty` | 题型、难度；先发现本库实际取值 |
| `-n/--limit N`、`--page N` | 每页数量与页码 |
| `--sort asc/desc` | 正序或倒序 |
| `--ids 123,456` | 按当前题库 ID 读取所选题目 |
| `--profile candidate/teaching/evidence` | 返回层次 |
| `--assets` | 读取 `--ids` 所指定题目的资产清单 |
| `--format markdown/json` | 阅读输出或结构化输出 |
| `-a/--with-answers` | 原 Markdown 调用中显示答案与评析 |
| `-r/--related-to ID` | 查询同一变式关联组内的其他题目 |

CLI 输出使用 UTF-8。无命中是正常结果，退出码为 0；执行错误或参数错误应检查非零退出码和错误信息，不把失败当作空库。JSON 模式用于程序处理，也应检查结构化错误状态。

## 7. 本地 HTTP 读取

服务就绪后可以调用以下只读 GET 接口。这里的 `http://127.0.0.1:8000` 仅为本机示例，实际主机和端口以启动器为准；这些接口不需要修改题库的 Token。

| 接口 | 用途 |
| --- | --- |
| `/api/agent/capabilities` | 能力、枚举和默认值 |
| `/api/agent/knowledge-points?q=单调性&limit=10&offset=0` | 知识点发现，可指定 `version_code` |
| `/api/agent/questions?knowledge_point_codes=1136&page=1&page_size=10` | 候选搜索；编码可重复传入 |
| `/api/agent/questions/batch?ids=123,456&profile=teaching` | 按 ID 批量取题，检查 `missing_ids` |
| `/api/agent/questions/123?profile=teaching` | 单题完整读取 |
| `/api/agent/assets?ids=123,456` | 多题资产清单 |
| `/api/agent/questions/123/assets` | 单题资产清单 |

搜索参数包括 `q`、`source`、`compulsory`、`chapter`、`knowledge`、`version_code`、`qtype`、`difficulty`、`knowledge_query`、重复的 `knowledge_point_codes`、`knowledge_point_mode`、`knowledge_point_role`、`knowledge_point_status`、`related_to`、`sort`、`page`、`page_size`、`profile` 和 `include_answers`。文字搜索按字面匹配，百分号和下划线不作为 SQL 通配符；显式 ID 与显示序号应区别理解。

`candidate` 为搜索默认层次；详情默认 `teaching`。显式选择 `teaching/evidence` 时默认包含答案，可用 `include_answers=false` 排除答案字段，评析中的核对说明仍须关注。单题和批量详情也可指定 `version_code`，只读已有教材定位，不切换工作台设置。HTTP 400/422 表示参数无效，单题不存在返回 404，题库暂不可读返回 503；列表或批量读取的 HTTP 200 仍要检查结果是否为空或有缺失项。既有 `/api/questions` 和组卷接口保持各自原有契约，Agent 新流程优先使用 `/api/agent/`。

## 8. 一次取材应记录什么

记录本次筛选条件、教材版本、选中题目 ID、可用内容哈希、真实来源及采用图片。保留题库原文和原资料核对说明；需要教学改编时另存副本并标注改编，不回写题库。来源不完整、证据未知、图片缺失或数学待核应如实记录。

本指南描述读取接口，不授权 Agent 修改题目、迁移数据库、切换全局教材、调用付费 AI 或发布材料。需要进一步核查表结构时，应先检查实际 schema；不要将旧版简化 DDL 当作当前数据库完整结构。
