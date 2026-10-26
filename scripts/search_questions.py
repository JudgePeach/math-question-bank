#!/usr/bin/env python3
"""Read-only Agent CLI; legacy flags and Python tuple results stay supported."""

import argparse
import json
import math
import sqlite3
import sys

from mathbank.agent_reads import AgentReader
from mathbank.paths import DATABASE_FILE


DB_PATH = str(DATABASE_FILE)
LEGACY_COLUMNS = (
    "id", "content", "question_type", "category_compulsory", "category_chapter",
    "category_knowledge", "difficulty", "source", "answer_markdown", "review",
    "association_group_id", "tags",
)


def _reader():
    # A local terminal can open local files. HTTP adapters omit local paths.
    return AgentReader(db_path=DB_PATH, include_local_paths=True)


def get_association_group(question_id):
    """Return the existing group without opening a writable connection."""
    with _reader() as reader:
        return reader.association_group(question_id)


def _search_page(reader, *, limit=20, page=1, **filters):
    """Keep old explicit large limits using bounded reads in one snapshot."""
    if page < 1:
        raise ValueError("页码必须为正整数")
    if limit != -1 and limit < 1:
        raise ValueError("返回数量必须为正整数；兼容旧调用时可显式使用 -1")
    if limit == -1 and page != 1:
        raise ValueError("不限量读取只支持第1页")
    if 0 < limit <= 100:
        return reader.search(page=page, page_size=limit, **filters)

    # Historical -n -1 and larger positive -n remain opt-in. Each database
    # fetch is bounded, and the reader keeps the entire operation consistent.
    start = 0 if limit == -1 else (page - 1) * limit
    first = reader.search(page=1, page_size=100, **filters)
    total = first["total"]
    total_pages = 1 if limit == -1 else max(1, math.ceil(total / limit))
    safe_page = min(page, total_pages)
    if safe_page != page:
        start = (safe_page - 1) * limit
    stop = total if limit == -1 else min(total, start + limit)
    items = []
    for offset in range((start // 100) * 100, stop, 100):
        chunk = first if offset == 0 else reader.search(
            page=offset // 100 + 1, page_size=100, **filters
        )
        begin = max(0, start - offset)
        end = min(len(chunk["items"]), stop - offset)
        items.extend(chunk["items"][begin:end])
    result = dict(first)
    result.update(
        items=items, page=safe_page, page_size=limit, total_pages=total_pages,
        next_page=safe_page + 1 if safe_page < total_pages else None,
        truncated=len(items) < total,
    )
    return result


def search_questions(query=None, qtype=None, difficulty=None, limit=50,
                     with_answers=False, associated_to_id=None):
    """Compatibility API: return the original twelve-column tuple layout.

    As before, ``with_answers`` controls presentation in the CLI, not which
    columns this Python helper returns. New clients should use AgentReader.
    """
    if limit == 0:
        return []
    with _reader() as reader:
        result = _search_page(
            reader, query=query or "", qtype=qtype or "",
            difficulty=difficulty or "", limit=-1 if limit is None else limit,
            related_to=associated_to_id, profile="evidence", include_answers=True,
            sort="id_desc",
        )
    return [tuple(item.get(key, "") for key in LEGACY_COLUMNS)
            for item in result["items"]]


def format_type(value):
    return {"single_choice": "单选题", "multi_choice": "多选题",
            "fill_in_blank": "填空题", "detailed_answer": "解答题"}.get(value, value)


def format_difficulty(value):
    return {"easy": "简单", "medium": "中等", "hard": "困难",
            "normal": "常规题", "easy_error": "易错题",
            "challenge": "挑战题", "qiangji": "强基题"}.get(value, value)


class AgentArgumentParser(argparse.ArgumentParser):
    """Keep JSON stdout parseable even when argparse rejects an argument."""

    json_errors = False

    def error(self, message):
        if self.json_errors:
            print(json.dumps({"status": "error", "error": {
                "type": "ArgumentError", "message": message,
            }}, ensure_ascii=False))
            raise SystemExit(2)
        super().error(message)


def build_parser():
    parser = AgentArgumentParser(
        description="只读检索数学题库，按知识点找题、按ID取题和读取图片清单。"
    )
    parser.add_argument("-q", "--query", help="字面关键词；保留原册/章/小节、正文、来源和标签检索")
    parser.add_argument("-t", "--type", dest="qtype", help="题型值；通过 --capabilities 查看当前可用值")
    parser.add_argument("-d", "--difficulty", help="难度值；支持当前与历史自定义值")
    parser.add_argument("-n", "--limit", type=int, help="本页题数（Markdown默认50，JSON默认20）；-1保留显式全量兼容")
    parser.add_argument("-a", "--with-answers", action="store_true", help="包含答案、解析和评析")
    parser.add_argument("-r", "--related-to", type=int, help="查找与指定稳定题目ID同组的其他题")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--source", default="", help="来源包含的字面文字")
    parser.add_argument("--compulsory", default="", help="精确册名")
    parser.add_argument("--chapter", default="", help="精确章节名称")
    parser.add_argument("--knowledge", default="", help="精确教材小节名称")
    parser.add_argument("--knowledge-point", action="append", default=[], help="独立知识点编码，可重复传入")
    parser.add_argument("--knowledge-query", default="", help="按知识点名称、原名、别名或编码搜索")
    parser.add_argument("--knowledge-mode", choices=("any", "all"), default="any")
    parser.add_argument("--knowledge-role", choices=("main", "all"), default="main")
    parser.add_argument("--version-code", help="教材版本；省略时使用现有活跃版本")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--sort", choices=("asc", "desc"), default="desc")
    parser.add_argument("--ids", action="append", help="按稳定ID读取，如 --ids 12,34；可重复传入")
    parser.add_argument("--profile", choices=("candidate", "teaching", "evidence"), help="候选摘要、完整教学材料或全部标注依据")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--capabilities", action="store_true", help="读取能力、当前枚举与字段说明")
    actions.add_argument("--catalog", action="store_true", help="查询知识点目录，配合 --knowledge-query 或 -q")
    actions.add_argument("--assets", action="store_true", help="读取 --ids 指定题目的图片清单和本地可读路径")
    return parser


def _ids(values):
    if not values:
        return None
    result = []
    for value in values:
        for token in value.split(","):
            token = token.strip()
            if not token or not token.isascii() or not token.isdecimal() or int(token) < 1:
                raise ValueError("题目ID必须为逗号分隔的正整数")
            result.append(int(token))
    return result


def _read(reader, args):
    ids = _ids(args.ids)
    limit = args.limit if args.limit is not None else (20 if args.format == "json" else 50)
    if args.page < 1:
        raise ValueError("页码必须为正整数")
    if args.capabilities:
        if any((ids, args.query, args.source, args.compulsory, args.chapter, args.knowledge,
                args.knowledge_point, args.knowledge_query, args.qtype, args.difficulty,
                args.related_to is not None, args.profile, args.with_answers, args.version_code,
                args.page != 1, args.limit is not None, args.sort != "desc",
                args.knowledge_mode != "any", args.knowledge_role != "main")):
            raise ValueError("--capabilities 读取全库能力，不能同时使用题目筛选、分层或分页参数")
        return reader.capabilities(), "capabilities", False
    if args.catalog:
        if any((ids, args.source, args.knowledge_point, args.qtype, args.difficulty,
                args.related_to is not None, args.profile, args.with_answers,
                args.sort != "desc", args.knowledge_mode != "any", args.knowledge_role != "main")):
            raise ValueError("--catalog 查询知识点目录，不支持题目ID、来源、题型、难度或详情参数")
        if not 1 <= limit <= 100:
            raise ValueError("知识点目录每页数量必须在1到100之间")
        return reader.knowledge_points(
            query=args.knowledge_query or args.query or "", version_code=args.version_code,
            compulsory=args.compulsory, chapter=args.chapter, knowledge=args.knowledge,
            limit=limit, offset=(args.page - 1) * limit,
        ), "catalog", False
    if args.assets:
        if not ids:
            raise ValueError("--assets 需要 --ids 指定题目")
        if any((args.query, args.source, args.compulsory, args.chapter, args.knowledge,
                args.knowledge_point, args.knowledge_query, args.qtype, args.difficulty,
                args.related_to is not None, args.profile, args.with_answers,
                args.version_code, args.page != 1, args.limit is not None,
                args.sort != "desc", args.knowledge_mode != "any",
                args.knowledge_role != "main")):
            raise ValueError("--assets 只读取指定ID的图片清单，不能同时使用搜索、分层或分页参数")
        return reader.assets(ids), "assets", False

    profile = args.profile or (
        "teaching" if ids or args.with_answers or args.format == "markdown" else "candidate"
    )
    include_answers = args.with_answers or bool(ids) or args.profile in {"teaching", "evidence"}
    if profile == "candidate" and args.with_answers:
        raise ValueError("candidate仅返回候选摘要；需要答案时请使用 teaching 或 evidence")
    if ids:
        if any((args.query, args.source, args.compulsory, args.chapter,
                args.knowledge, args.knowledge_point, args.knowledge_query,
                args.qtype, args.difficulty, args.related_to is not None,
                args.page != 1, args.limit is not None, args.sort != "desc",
                args.knowledge_mode != "any", args.knowledge_role != "main")):
            raise ValueError("--ids 按指定ID读取，不能同时使用搜索筛选条件")
        result = reader.get_questions(
            ids, profile=profile, include_answers=include_answers,
            version_code=args.version_code,
        )
    else:
        result = _search_page(
            reader, limit=limit, page=args.page, query=args.query or "",
            source=args.source, compulsory=args.compulsory, chapter=args.chapter,
            knowledge=args.knowledge, knowledge_point_codes=args.knowledge_point,
            knowledge_query=args.knowledge_query, knowledge_point_mode=args.knowledge_mode,
            knowledge_point_role=args.knowledge_role, qtype=args.qtype or "",
            difficulty=args.difficulty or "", related_to=args.related_to,
            profile=profile, include_answers=include_answers, sort=args.sort,
            version_code=args.version_code,
        )
    return result, "questions", include_answers


def _print_assets(assets):
    for asset in assets or []:
        path = asset.get("local_path") or asset.get("path") or asset.get("url") or "未知路径"
        status = asset.get("status") or ("存在" if asset.get("exists") else "缺失或不可读")
        roles = asset.get("roles") or asset.get("role") or asset.get("usage") or "图片"
        print(f"- {path}；用途：{roles}；状态：{status}")


def _markdown(result, action, include_answers):
    if action == "capabilities":
        print("## 题库读取能力\n")
        print("```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```")
        return
    if action == "catalog":
        print(f"## 知识点目录（总命中 {result.get('total', 0)}，本次返回 {len(result.get('items', []))}）\n")
        for item in result.get("items", []):
            print(f"- `{item.get('code', '')}` {item.get('name', '')}")
            if item.get("aliases"):
                print(f"  别名：{'、'.join(item['aliases'])}")
        return
    items = result.get("items", [])
    print(f"## 题库检索结果（总命中 {result.get('total', len(items))}，本次返回 {len(items)}）\n")
    if result.get("missing_ids"):
        print("缺失题目ID：" + ", ".join(map(str, result["missing_ids"])) + "\n")
    if result.get("next_page"):
        print(f"下一页：--page {result['next_page']}\n")
    for index, item in enumerate(items, 1):
        print(f"### 题目 {index} (ID: #{item['id']})")
        if action == "assets":
            _print_assets(item.get("assets", []))
            print()
            continue
        print(f"- **分类学段**: `{item.get('category_compulsory') or '未分类'}`")
        print(f"- **章节知识点**: `{item.get('category_chapter') or '无'}` -> `{item.get('category_knowledge') or '无'}`")
        print(f"- **题型/难度**: {format_type(item.get('question_type', ''))} | {format_difficulty(item.get('difficulty', ''))}")
        for key, label in (("source", "来源"), ("association_group_id", "关联组 ID"), ("tags", "自定义标签")):
            if item.get(key):
                print(f"- **{label}**: {item[key]}")
        if item.get("knowledge_points"):
            print("- **考查知识点**: " + "；".join(
                f"{point.get('name', '')} [{point.get('code', '')}] ({point.get('role', '')})"
                for point in item["knowledge_points"]
            ))
        print("\n#### 【题干】\n" + (item.get("content") or item.get("content_preview") or "") + "\n")
        if include_answers:
            if item.get("answer_markdown"):
                print("#### 【答案与解析】\n" + item["answer_markdown"] + "\n")
            if item.get("review"):
                print("#### 【教师点评】\n" + item["review"] + "\n")
        elif "content" in item:
            print("> [!NOTE]\n> *答案与详细解析已隐藏。如需查看，请添加 -a 或 --profile teaching。*\n")
        assets = item.get("assets", [])
        if not include_answers:
            # Legacy no-answer output must not expose answer/commentary images.
            assets = [asset for asset in assets if "content" in asset.get("roles", [])]
        if assets:
            print("#### 【图片清单】")
            _print_assets(assets)
            print()
        print("---\n")


def main(argv=None):
    # Redirection on Windows may otherwise select CP936, which cannot encode
    # all mathematical symbols. Keep both human and machine output lossless.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    parser.json_errors = any(
        value == "--format=json" or (
            value == "--format" and index + 1 < len(arguments) and arguments[index + 1] == "json"
        ) for index, value in enumerate(arguments)
    )
    args = parser.parse_args(arguments)
    try:
        with _reader() as reader:
            result, action, include_answers = _read(reader, args)
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        else:
            _markdown(result, action, include_answers)
        return 0
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        if args.format == "json":
            print(json.dumps({"status": "error", "error": {"type": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
