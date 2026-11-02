"""Keep answer illustrations out of student questions and in teacher answers."""

import copy
import io
import zipfile

import pytest
from lxml import etree
from PIL import Image

from mathbank.word_export_helper import build_word_document


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
ANSWER_IMAGE = "![](/static/uploads/answer.png)"


def _export(tmp_path, *, content, answer, stored=(), include_answers=True, paper_type="exam",
            answer_tikz_assets=()):
    for name, color in (("stem.png", "blue"), ("answer.png", "red"), ("legacy.png", "green")):
        Image.new("RGB", (120, 80), color).save(tmp_path / name)
    questions = [{
        "question": {
            "id": 1, "question_type": "detailed_answer", "content": content,
            "answer_markdown": answer, "image_paths": list(stored), "figure_align": "center",
            "answer_tikz_assets": list(answer_tikz_assets),
        },
        "score": 12,
    }]
    original = copy.deepcopy(questions)
    data, diagnostics = build_word_document(
        "图片答案回归", "", paper_type, questions, uploads_dir=tmp_path,
        include_answers=include_answers,
    )
    assert questions == original
    assert diagnostics["missing_images"] == diagnostics["failed_formulas"] == 0
    return data


def _image_sections(data):
    """Read each drawing's actual media bytes, split at the answer heading."""
    sections = ([], [])
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        root = etree.fromstring(package.read("word/document.xml"))
        rels = etree.fromstring(package.read("word/_rels/document.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        in_answers = False
        for block in root.find("w:body", NS):
            text = "".join(block.xpath(".//w:t/text()", namespaces=NS))
            if "参考答案与解析" in text:
                in_answers = True
            for rel_id in block.xpath(".//a:blip/@r:embed", namespaces=NS):
                with Image.open(io.BytesIO(package.read("word/" + targets[rel_id]))) as image:
                    sections[int(in_answers)].append(image.getpixel((0, 0)))
    return sections


@pytest.mark.parametrize("include_answers", [False, True])
@pytest.mark.parametrize("stored", [[], ["/static/uploads/answer.png"]])
@pytest.mark.parametrize("paper_type", ["quiz", "exam", "exam_19"])
def test_image_only_answer_never_appears_in_questions(tmp_path, include_answers, stored, paper_type):
    data = _export(tmp_path, content="根据条件完成证明。", answer=ANSWER_IMAGE,
                   stored=stored, include_answers=include_answers, paper_type=paper_type)
    question_images, answer_images = _image_sections(data)
    assert question_images == []
    assert answer_images == ([(255, 0, 0)] if include_answers else [])
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        media = [name for name in package.namelist() if name.startswith("word/media/")]
        assert len(media) == int(include_answers), "Student DOCX must not embed answer media"
        assert "暂无答案" not in package.read("word/document.xml").decode()


@pytest.mark.parametrize("answer", [
    "解析开头。\n\n" + ANSWER_IMAGE + "\n\n解析结尾。",
    r"\begin{enumerate}\item 由图可得。" + ANSWER_IMAGE + r"\item 得证。\end{enumerate}",
    "| 步骤 | 图示 |\n| --- | --- |\n| 证明 | " + ANSWER_IMAGE + " |",
    r"\begin{tabular}{cc}证明 & " + ANSWER_IMAGE + r"\end{tabular}",
])
def test_answer_images_keep_their_answer_anchor(tmp_path, answer):
    data = _export(tmp_path, content="完成证明。", answer=answer,
                   stored=["/static/uploads/answer.png"])
    assert _image_sections(data) == ([], [(255, 0, 0)])


@pytest.mark.parametrize("content", [
    "看图证明。\n\n![](/static/uploads/stem.png)",
    "先看图。![](/static/uploads/stem.png)再完成证明。",
    r"选择图形。\begin{choices}\item ![](/static/uploads/stem.png)\item 无图\end{choices}",
])
def test_stem_choice_and_legacy_images_survive_answer_separation(tmp_path, content):
    data = _export(tmp_path, content=content, answer=ANSWER_IMAGE, stored=[
        "/static/uploads/stem.png", "/static/uploads/answer.png", "/static/uploads/legacy.png",
    ])
    expected_stem = ([(0, 128, 0), (0, 0, 255)] if r"\begin{choices}" in content
                     else [(0, 0, 255), (0, 128, 0)])
    assert _image_sections(data) == (expected_stem, [(255, 0, 0)])


def test_image_explicitly_shared_by_stem_and_answer_appears_in_both(tmp_path):
    data = _export(tmp_path, content="参照此图。" + ANSWER_IMAGE, answer=ANSWER_IMAGE,
                   stored=["/static/uploads/answer.png"])
    assert _image_sections(data) == ([(255, 0, 0)], [(255, 0, 0)])


@pytest.mark.parametrize("answer", [
    '![答案](/static/uploads/answer.png "答案图")',
    '![答案](</static/uploads/answer.png>)',
    '![答案](</static/uploads/answer.png> "答案图")',
])
def test_markdown_image_titles_and_angle_paths_do_not_change_answer_ownership(tmp_path, answer):
    data = _export(tmp_path, content="完成证明。", answer=answer,
                   stored=["/static/uploads/answer.png"])
    assert _image_sections(data) == ([], [(255, 0, 0)])


@pytest.mark.parametrize("content, expected", [("完成证明。", []),
                                                ("参照此图。" + ANSWER_IMAGE, [(255, 0, 0)])])
def test_unanchored_answer_tikz_asset_is_not_a_legacy_question_figure(tmp_path, content, expected):
    data = _export(tmp_path, content=content, answer="得证。",
                   stored=["/static/uploads/answer.png"], answer_tikz_assets=[
                       {"image_path": "/static/uploads/answer.png", "tikz_code": "saved drawing"},
                   ])
    assert _image_sections(data) == (expected, [])


@pytest.mark.parametrize("answer", [
    r"\includegraphics{/static/uploads/answer.png}",
    '<img src="/static/uploads/answer.png">',
])
def test_other_answer_reference_syntax_never_creates_a_question_figure(tmp_path, answer):
    data = _export(tmp_path, content="完成证明。", answer=answer,
                   stored=["/static/uploads/answer.png"], include_answers=False)
    assert _image_sections(data) == ([], [])


def test_missing_answer_image_is_diagnosed_only_when_answers_are_exported(tmp_path):
    questions = [{"question": {
        "question_type": "detailed_answer", "content": "完成证明。",
        "answer_markdown": "![](/static/uploads/missing.png)",
        "image_paths": ["/static/uploads/missing.png"],
    }}]
    for include_answers in (False, True):
        _, diagnostics = build_word_document("缺失答案图", "", "exam", questions,
                                             uploads_dir=tmp_path, include_answers=include_answers)
        assert diagnostics["missing_images"] == int(include_answers)
