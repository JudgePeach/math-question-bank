"""Regression boundaries found while independently reviewing import edits."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from mathbank.formula_notices import separate_formula_notices
from test_import_source_review import review_script  # noqa: F401


@pytest.mark.parametrize("source,expected", [
    ("$x$[公式待核对]$y$", "$x$$y$"),
    ("$$x$$[公式待核对]$$y$$", "$$x$$$$y$$"),
    (r"\(x\)[公式待核对]\(y\)", r"\(x\)\(y\)"),
])
def test_notice_between_formulas_cannot_consume_either_formula_boundary(source, expected):
    question = {"content": source}
    separate_formula_notices([question])
    assert question["content"] == expected


@pytest.mark.parametrize("source", [
    r"$\alpha[公式待核对]x$", r"$\alpha\text{[公式待核对]}x$",
    r"$\alpha[公式待核对][公式结构待核对]x$",
    r"$\alpha\text{[公式待核对]}\mathrm{[公式结构待核对]}x$",
])
def test_notice_removal_preserves_latex_control_word_boundary(source):
    question = {"content": source}
    separate_formula_notices([question])
    assert re.findall(r"\\[A-Za-z]+", question["content"]) == [r"\alpha"]
    assert question["content"].endswith("x$")


def test_generated_answer_marks_saved_card_dirty_and_schedules_session_save(review_script):
    script = review_script.rsplit("scenarios[process.argv[1]]", 1)[0]
    script += r"""
(async () => {
    let writes = 0;
    window.MathBankDocumentImportSession = { saveSoon() { writes++; } };
    const question = verifiedQuestion();
    const card = show([question]);
    question.savedSnapshot = captureParsedQuestionSaveSnapshot(0, question, card);
    question.saved = true;
    const checkbox = card.querySelector('.card-select-checkbox');
    checkbox.disabled = true; checkbox.checked = false;
    writes = 0;
    const pending = generateSingleAnswer(0);
    requests.at(-1).resolve(success('新生成、尚未入库的解析'));
    await pending;
    assert.equal(question.saved, false, 'a generated answer is a new unsaved revision');
    assert.equal(checkbox.disabled, false);
    assert.equal(checkbox.checked, true);
    assert.ok(writes > 0, 'programmatic model results must persist without an input event');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "-e", "eval(require('node:fs').readFileSync(0,'utf8'))"],
        input=script, cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
