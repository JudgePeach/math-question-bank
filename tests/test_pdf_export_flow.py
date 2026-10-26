"""Run with the normal fixtures in an isolated source-only application copy."""
from types import SimpleNamespace

import pytest

from mathbank.database import Question


def test_no_ai_configuration_still_returns_actionable_local_error(monkeypatch):
    import main
    monkeypatch.setattr(main, "resolve_text_provider", lambda *_: SimpleNamespace(api_key=""))
    monkeypatch.setattr(main, "post_chat_completion", lambda *_a, **_k: pytest.fail("No paid request"))
    tex = "\n".join(["% MathBank-Question-ID: 41", "% MathBank-Question-Number: 2",
        "% MathBank-Field: content", r"求 $\badformula{x}$。", "% MathBank-Question-End"])
    diagnostic = main.explain_latex_compile_error(
        "./paper.tex:4: Undefined control sequence.\n<recently read> \\badformula\nl.4 求 $\\badformula", tex)
    assert diagnostic["question_id"] == "41"
    assert diagnostic["question_number"] == "2"
    assert diagnostic["command"] == r"\badformula"
    assert diagnostic["ai_used"] is False
    assert diagnostic["fixes"]


def test_stale_model_configuration_does_not_hide_compiler_error(monkeypatch):
    import main
    def fail(*args):
        raise ValueError("unsupported model")
    monkeypatch.setattr(main, "resolve_text_provider", fail)
    result = main.explain_latex_compile_error("! Missing $ inserted.\nl.1 x_1", "x_1")
    assert result["kind"] == "math_delimiter"
    assert not result["ai_used"]


def test_ai_failure_keeps_local_location_and_fix(monkeypatch):
    import main
    monkeypatch.setattr(main, "resolve_text_provider", lambda *_: SimpleNamespace(api_key="fake", model_name="test"))
    monkeypatch.setattr(main, "apply_model_thinking_policy", lambda payload, **kw: payload)
    monkeypatch.setattr(main, "post_chat_completion", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline")))
    result = main.explain_latex_compile_error("! Missing $ inserted.\nl.1 x_1", "x_1")
    assert result["kind"] == "math_delimiter"
    assert not result["ai_used"]
    assert r"$\triangle ABC$" in " ".join(result["fixes"])


@pytest.mark.parametrize("target", ["paper", "sheet"])
def test_pdf_failure_identifies_document_and_never_rewrites_source(client, db_session, monkeypatch, target):
    import main
    original = "在△ABC中，∠ACB=90°，填写结论。"
    question = Question(content=original, question_type="detailed_answer")
    db_session.add(question)
    db_session.commit()
    diagnostic = {"summary": "字符无法显示", "kind": "missing_glyph", "location": "第1题",
                  "fixes": [r"请使用 $\triangle ABC$、$\angle ACB$。"], "ai_used": False}
    monkeypatch.setattr(main, "compile_tex_to_pdf", lambda *_: (None, "Missing character: There is no 🦄 in font nullfont!"))
    monkeypatch.setattr(main, "explain_latex_compile_error", lambda *_: dict(diagnostic))
    reply = client.post("/api/paper/export/pdf", json={"title": "诊断", "paper_type": "exam_19",
        "target": target, "questions": [{"id": question.id}]}, headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert reply.status_code == 400, reply.text
    assert reply.json()["diagnostic"]["document"] == ("试卷正文" if target == "paper" else "答题卡")
    db_session.refresh(question)
    assert question.content == original


def test_bundle_reports_the_first_failed_document_and_stops(client, db_session, monkeypatch):
    import main
    question = Question(content="题干正常。", answer_markdown="解析正常。", question_type="detailed_answer")
    db_session.add(question)
    db_session.commit()
    calls = []
    def compile_pdf(source, images):
        calls.append(source)
        return (b"complete main pdf", "ok") if len(calls) == 1 else (None, "bad answer formula")
    monkeypatch.setattr(main, "compile_tex_to_pdf", compile_pdf)
    monkeypatch.setattr(main, "explain_latex_compile_error", lambda *_: {
        "summary": "解析公式有误", "question_id": str(question.id), "field": "answer_markdown", "fixes": []})
    reply = client.post("/api/paper/export/bundle", json={"title": "归档", "paper_type": "exam_19",
        "questions": [{"id": question.id}]}, headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert reply.status_code == 400, reply.text
    assert reply.json()["diagnostic"]["document"] == "参考答案与解析"
    assert len(calls) == 2
