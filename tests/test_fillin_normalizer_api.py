"""Proposed regression module: copy into tests/ in an ISOLATED source checkout.

Uses the project's existing client/db_session fixtures. Importing live main or
executing this against the real service is not part of this proposal.
"""
import pytest


def _headers():
    from main import LOCAL_TOKEN
    return {"X-Local-Token": LOCAL_TOKEN}


def _payload(content):
    return {"content": content, "question_type": "fill_in_blank", "difficulty": "normal",
            "source": "填空规范化隔离回归", "answer_markdown": "原解析", "review": "原评析",
            "tags": "", "image_paths": "[]", "content_tikz_assets": "[]", "answer_tikz_assets": "[]"}


def _question(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success", body
    return body["question"]


@pytest.mark.parametrize("raw, expected", [
    # Real book pattern: MinerU escapes each glyph of a printed answer line.
    (r"已知$x>0$，则$x$的最小值为\_\_\_\_。", r"已知$x>0$，则$x$的最小值为\fillin。"),
    # Real Example 2.6: one escaped glyph at the RHS of a complete math shell.
    (r"则$S_n = \_$。", r"则$S_n = \fillin$。"),
])
def test_book_escaped_blanks_are_stored_consistently_on_create_and_update(client, db_session, raw, expected):
    from mathbank.database import Question, QuestionFingerprint
    from mathbank.question_duplicates import build_question_fingerprint

    checked = client.post("/api/questions/check-duplicates", headers=_headers(), json={"items": [{
        "client_key": "real-book-blank", "content": raw, "question_type": "fill_in_blank",
        "answer_markdown": "原解析", "image_paths": [], "content_tikz_assets": [], "answer_tikz_assets": [],
    }]})
    assert checked.status_code == 200, checked.text
    snapshot = checked.json()["items"][0]["snapshot_hash"]
    created = _question(client.post("/api/questions", headers=_headers(), data={
        **_payload(raw), "duplicate_snapshot_hash": snapshot,
    }))
    assert created["content"] == expected
    assert created["answer_markdown"] == "原解析" and created["review"] == "原评析"
    identifier = created["id"]
    assert db_session.get(Question, identifier).content == expected
    fingerprint = db_session.query(QuestionFingerprint).filter_by(question_id=identifier).one()
    wanted = build_question_fingerprint({"content": expected, "question_type": "fill_in_blank"})
    assert fingerprint.exact_hash == wanted.exact_hash
    assert client.get(f"/api/questions/{identifier}").json()["content"] == expected

    # Re-submit the true escaped source during update; normalization is idempotent
    # and must not destroy a neighboring subscript or add math-shell delimiters.
    suffix = r" 邻式$a_n+a_{n+1}$。"
    updated = _question(client.put(f"/api/questions/{identifier}", headers=_headers(), data=_payload(raw + suffix)))
    assert updated["content"] == expected + suffix
    assert db_session.get(Question, identifier).content == expected + suffix
    assert client.get(f"/api/questions/{identifier}").json()["content"] == expected + suffix


def test_api_keeps_code_urls_subscripts_and_explicit_macro_contents_exact(client, db_session):
    from mathbank.database import Question

    protected = (
        r"源码`\_\_\_\_`。" + "\n\n```tex\n\\_\\_\\_\\_\n```\n\n" +
        r"链接 https://example.test/a___b/\_\_\_\_ 。" + "\n" +
        r"字面\verb|\_\_\_\_|与\detokenize{a\_b}。" + "\n" +
        r"数学$a_n+a_{n+1}+a_{\_\_\_\_}$；强调$\underline{a_n}$。" + "\n" +
        r"保留\fillin[2cm][a_n]及$\frac{\fillin}{2}$、\%\#\&\{\}\$。"
    )
    raw = protected + "\n\n" + r"真实填写$x=\_\_\_\_$。"
    expected = protected + "\n\n" + r"真实填写$x=\fillin$。"
    created = _question(client.post("/api/questions", headers=_headers(), data=_payload(raw)))
    assert created["content"] == expected
    identifier = created["id"]
    updated = _question(client.put(f"/api/questions/{identifier}", headers=_headers(), data=_payload(raw)))
    assert updated["content"] == expected
    assert db_session.get(Question, identifier).content == expected
    assert client.get(f"/api/questions/{identifier}").json()["content"] == expected
