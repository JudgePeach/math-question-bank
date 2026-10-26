"""Image-free, literal search contracts on isolated SQLite and app copies."""

import pytest

from mathbank.question_search import (
    apply_question_search,
    searchable_question_text,
    SQLITE_SEARCH_FUNCTION,
)


@pytest.mark.parametrize("source", [
    "正文 ![](/static/uploads/hash2348.png) 后文",
    "正文 ![可见说明](/static/uploads/hash2348.jpg '图题') 后文",
    '正文 ![可见说明](<https://example.test/a 2348.png> "图题") 后文',
    r"正文 \includegraphics[width=4cm]{/static/uploads/hash2348.png} 后文",
    '<img src="/static/uploads/hash2348.png" alt="可见说明">',
    "<IMG alt='可见说明' SRC='/static/uploads/hash2348.png'>",
    '<img src=/static/uploads/hash2348.png alt="可见说明">',
    '<img src="/static/uploads/a.png" srcset="/static/uploads/b2348.png 2x, https://example.test/c2348.png 3x" alt="可见说明">',
    '<img srcset="/static/uploads/hash2348.png 2x" alt="可见说明">',
    "<img srcset='/static/uploads/hash2348.png 2x' alt='可见说明'>",
    '<img alt="可见说明 > 其他" srcset="/static/uploads/hash2348.png 2x">',
])
def test_image_targets_do_not_match_but_surrounding_text_is_preserved(source):
    masked = searchable_question_text(source)
    assert "2348" not in masked
    assert len(masked) == len(source)
    if "可见说明" in source:
        assert "可见说明" in masked
    if source.startswith("正文"):
        assert masked.startswith("正文") and masked.endswith("后文")


@pytest.mark.parametrize("source", [
    r"正文 $a_{2348}=\frac{2348}{x}$ 后文",
    "![2348可见说明](/static/uploads/other.png)",
    '<img alt="2348可见说明" src="/static/uploads/other.png">',
    '<img alt="可见 srcset=2348 说明" src="/static/uploads/other.png">',
    '''<img alt="可见 src='2348' 说明" src="/static/uploads/other.png">''',
    "`![](/static/uploads/hash2348.png)`",
    "```markdown\n![](/static/uploads/hash2348.png)\n```",
    "    ![](/static/uploads/hash2348.png)\n",
    "<code><img srcset='/static/uploads/hash2348.png 2x'></code>",
    r"\begin{verbatim}\includegraphics{hash2348.png}\end{verbatim}",
    r"\verb|![](/static/uploads/hash2348.png)|",
    r"\![](/static/uploads/hash2348.png)",
    "https://example.test/2348",
    "[字面链接](https://example.test/2348)",
    None,
])
def test_visible_text_formulas_and_literal_code_keep_their_original_text(source):
    masked = searchable_question_text(source)
    if source is None:
        assert masked == ""
    elif "other.png" in source:
        assert "2348" in masked
    else:
        assert masked == source


@pytest.fixture
def search_db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from mathbank.database import Base, Question

    engine = create_engine(f"sqlite:///{tmp_path / 'search.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Question(id=100, content="目标题", display_order=1),
            Question(id=200, content="真正文本 2348 和 #1 和 #999", display_order=3),
            Question(id=300, content="题干 ![可见说明](/static/uploads/body2348.png)", display_order=2),
            Question(id=400, content="普通题", answer_markdown="![解析说明](/static/uploads/answer2348.png)",
                     review="<img srcset='/static/uploads/review2348.png 2x' alt='评析说明'>"),
            Question(id=500, content=r"字面 100%_ 和 $x_{2}=3$", source="来源记录", tags="分类标签"),
        ])
        db.commit()
        yield db
    engine.dispose()


def ids(db, keyword):
    from mathbank.database import Question
    return {item.id for item in apply_question_search(db.query(Question), keyword).all()}


def test_image_targets_excluded_across_question_answer_and_review(search_db):
    assert ids(search_db, "2348") == {200}
    assert ids(search_db, "可见说明") == {300}
    assert ids(search_db, "解析说明") == {400}
    assert ids(search_db, "评析说明") == {400}
    assert ids(search_db, "来源记录") == {500}
    assert ids(search_db, "分类标签") == {500}
    assert ids(search_db, r"$x_{2}=3$") == {500}


def test_hash_sequence_is_strict_and_plain_numbers_keep_text_compatibility(search_db):
    assert ids(search_db, "#1") == {100}
    assert ids(search_db, "#2") == {300}
    assert ids(search_db, "  #0002  ") == {300}
    assert ids(search_db, "#999") == set()
    assert ids(search_db, "#0") == set()
    assert ids(search_db, "#" + "9" * 4000) == set()
    assert ids(search_db, "1") == {100, 200, 500}
    assert ids(search_db, "2348") == {200}
    assert ids(search_db, "  ") == {100, 200, 300, 400, 500}


def test_wildcards_are_literal_and_udf_only_runs_for_raw_candidates(search_db):
    assert ids(search_db, "%_") == {500}
    assert ids(search_db, "100%_") == {500}
    connection = search_db.connection().connection.driver_connection
    calls = []

    def counted(value):
        calls.append(value)
        return searchable_question_text(value)

    connection.create_function(SQLITE_SEARCH_FUNCTION, 1, counted, deterministic=True)
    assert ids(search_db, "完全无命中") == set()
    assert calls == []
    assert ids(search_db, "body2348") == set()
    assert calls == ["题干 ![可见说明](/static/uploads/body2348.png)"]


def test_search_does_not_modify_raw_question_fields_or_assets(search_db):
    from sqlalchemy import text

    before = search_db.execute(text("SELECT * FROM questions ORDER BY id")).fetchall()
    for keyword in ("2348", "#2", "%_", "解析说明"):
        ids(search_db, keyword)
    after = search_db.execute(text("SELECT * FROM questions ORDER BY id")).fetchall()
    assert after == before


# This fixture copies runtime source/resources only and forbids external AI/
# network calls. It never imports main against the user's real database.
from test_large_library_api import isolated_app  # noqa: E402,F401


def test_search_api_preserves_pagination_filters_alias_and_raw_details(isolated_app):
    isolated_app(r'''
        first = seed_question(10)
        image = seed_question(20, answer_markdown='![解析图](/static/uploads/a2348.png)',
                              review='<img srcset="/static/uploads/r2348.png 2x" alt="评析说明">')
        image.content = '![可见说明](/static/uploads/b2348.png)'
        literal = seed_question(30, source='来源 2348')
        literal.content = '真正正文 2348'
        literal.tags = '100%_'
        db.commit()
        assert [x['id'] for x in get('/api/questions', q='2348', page=1)['items']] == [30]
        assert get('/api/questions', q='2348', page=1, page_size=1)['total'] == 1
        assert get('/api/questions', search='2348', page=1)['total'] == 1
        assert [x['id'] for x in get('/api/questions', q='2348')] == [30]
        assert get('/api/questions', q='2348', qtype='single_choice', page=1)['total'] == 0
        assert get('/api/questions', q='2348', source='找不到', page=1)['total'] == 0
        assert get('/api/questions', q='%_', page=1)['items'][0]['id'] == 30
        assert get('/api/questions', q='可见说明', page=1)['items'][0]['id'] == 20
        assert get('/api/questions', q='评析说明', page=1)['items'][0]['id'] == 20
        detail = get('/api/questions/20')
        assert detail['content'] == image.content
        assert detail['answer_markdown'] == image.answer_markdown
        assert detail['review'] == image.review
    ''')


def test_strict_sequence_api_uses_global_display_order_and_no_text_fallback(isolated_app):
    isolated_app(r'''
        a = seed_question(10)
        b = seed_question(20)
        c = seed_question(30)
        a.display_order = 3
        b.display_order = 1
        c.display_order = 2
        a.content = '文字含有 #2 和 #999 和数字 2348'
        db.commit()
        assert [x['id'] for x in get('/api/questions', q='#2', page=1)['items']] == [30]
        assert get('/api/questions', q='#2', page=1)['items'][0]['seq_num'] == 2
        assert get('/api/questions', q='#999', page=1)['total'] == 0
        assert get('/api/questions', q='#' + '9' * 4000, page=1)['total'] == 0
        assert get('/api/questions', q='#2', source='不存在', page=1)['total'] == 0
        assert {x['id'] for x in get('/api/questions', q='2', page=1)['items']} == {10, 20, 30}
    ''')
