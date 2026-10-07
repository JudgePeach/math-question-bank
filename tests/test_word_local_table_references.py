"""A direct indexical table reference must retain complete local ownership."""
import pytest
from mathbank.word_source_dependencies import analyze_word_source_dependencies

TABLE = r"""\begin{tabular}{|c|c|c|}
\hline
规格 & 一号 & 二号 \\
\hline
尺寸 & $288\times192$ & $240\times160$ \\
\hline
\end{tabular}"""

def analyze(body, previous="独立第一题。"):
    source = "1. " + previous + "\n\n"
    cut = len(source)
    source += "2. " + body
    questions = [{"id":"a", "source_number":1, "source_range":[0,cut], "raw_content":source[:cut]},
                 {"id":"b", "source_number":2, "source_range":[cut,len(source)], "raw_content":source[cut:]}]
    return source, analyze_word_source_dependencies(source,questions)

def test_direct_reference_immediately_after_the_unique_complete_local_table():
    source, report = analyze("国旗有五种规格。\n\n" + TABLE + "\n\n根据上表，可以判断面积的关系。")
    assert not report["has_unresolved"] and not report["has_dependencies"]
    proof = report["local_data_references"][0]
    assert proof["owner_id"] == "b"
    assert source[slice(*proof["table_range"])] == TABLE
    assert source[slice(*proof["reference_range"])] == "根据上表"

@pytest.mark.parametrize("body", [
    "根据上表，可以判断。", TABLE + "\n另有一张表。\n根据上表，可以判断。",
    TABLE + "\n" + TABLE + "\n根据上表，可以判断。",
    TABLE.replace(r"\end{tabular}", "") + "\n根据上表，可以判断。",
    TABLE.replace("一号 & 二号", "一号") + "\n根据上表，可以判断。",
    TABLE + "\n由以上数据，可以判断。", "表述代码。\n\n```\n" + TABLE + "\n```\n根据上表，可以判断。",
    "$" + TABLE + "$\n根据上表，可以判断。", TABLE + "\n根据上表数据以及前题条件，可以判断。",
    TABLE + "\n根据上表，结合第1题的条件判断。",
])
def test_missing_external_ambiguous_or_incomplete_tables_remain_uncertain(body):
    _, report = analyze(body,previous=TABLE)
    assert report["has_unresolved"] or report["has_dependencies"]

def test_proven_local_table_does_not_clear_another_unresolved_function_reference():
    _, report = analyze(TABLE + "\n根据上表，可以判断。根据上述函数再求值。")
    assert report["has_unresolved"]
    assert any("函数" in r["evidence"] for r in report["references"])
