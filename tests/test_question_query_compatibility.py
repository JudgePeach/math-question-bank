"""Public search contracts in disposable source-only application copies.

Run with ``python3 -B -m pytest --noconftest tests/test_question_query_compatibility.py``.
The shared fixture never imports the real application's database or credentials.
"""

from test_large_library_api import isolated_app  # noqa: F401


def test_null_order_keeps_date_list_order_separate_from_global_numbers(isolated_app):
    isolated_app(r'''
        from datetime import datetime

        dates = {3: datetime(2020, 1, 1), 8: datetime(2022, 1, 1),
                 15: datetime(2022, 1, 1), 21: None}
        for qid, created_at in dates.items():
            seed_question(qid)
            db.query(Question).filter_by(id=qid).update({
                Question.created_at: created_at, Question.display_order: None,
            }, synchronize_session=False)
        db.commit()

        expected_numbers = {3: 1, 8: 2, 15: 3, 21: 4}
        for direction, expected in [('asc', [21, 3, 8, 15]),
                                    ('desc', [15, 8, 3, 21])]:
            legacy = get('/api/questions', sort=direction)
            assert [item['id'] for item in legacy] == expected
            collected = []
            for page in (1, 2):
                payload = get('/api/questions', page=page, page_size=2, sort=direction)
                assert (payload['total'], payload['page'], payload['total_pages']) == (4, page, 2)
                collected.extend(payload['items'])
            assert [item['id'] for item in collected] == expected
            assert {item['id']: item['seq_num'] for item in collected} == expected_numbers

        for number, qid in enumerate([3, 8, 15, 21], 1):
            found = get('/api/questions', q='#' + str(number), page=1)
            assert [(item['id'], item['seq_num']) for item in found['items']] == [(qid, number)]
            candidate = get('/api/questions/association-candidates', seq_num=number)
            assert candidate['items'][0]['id'] == qid
    ''')


def test_sparse_duplicate_and_mixed_orders_preserve_ties_and_contiguous_numbers(isolated_app):
    isolated_app(r'''
        for qid in (3, 8, 15, 21):
            seed_question(qid)
        cases = [
            ({3: 20, 8: 4, 15: 4, 21: 1}, [21, 8, 15, 3]),
            ({3: None, 8: 20, 15: 3, 21: 2}, [21, 3, 15, 8]),
        ]
        for values, ascending in cases:
            for qid, value in values.items():
                db.query(Question).filter_by(id=qid).update(
                    {Question.display_order: value}, synchronize_session=False)
            db.commit()
            numbers = {qid: number for number, qid in enumerate(ascending, 1)}
            for direction, expected in [('asc', ascending), ('desc', ascending[::-1])]:
                legacy = get('/api/questions', sort=direction)
                assert [item['id'] for item in legacy] == expected
                collected = []
                for page in (1, 2):
                    payload = get('/api/questions', page=page, page_size=3, sort=direction)
                    assert (payload['total'], payload['page_size'], payload['total_pages']) == (4, 3, 2)
                    collected.extend(payload['items'])
                assert [item['id'] for item in collected] == expected
                assert {item['id']: item['seq_num'] for item in collected} == numbers
            for number, qid in enumerate(ascending, 1):
                found = get('/api/questions', q='#' + str(number), page=1)
                assert [(item['id'], item['seq_num']) for item in found['items']] == [(qid, number)]
    ''')


def test_empty_out_of_range_and_arbitrarily_large_pagination_remain_bounded(isolated_app):
    isolated_app(r'''
        huge = 10 ** 80
        for params in ({'page': huge, 'page_size': huge},
                       {'page': -huge, 'page_size': -huge},
                       {'q': '不存在的关键词', 'page': huge, 'page_size': 2}):
            payload = get('/api/questions', **params)
            assert payload['items'] == []
            assert (payload['total'], payload['page'], payload['total_pages']) == (0, 1, 1)
            assert 1 <= payload['page_size'] <= 100

        for qid in (3, 8, 15):
            item = seed_question(qid)
            item.content = '命中共同关键词'
        db.commit()
        for query_params in ({}, {'q': '命中共同关键词'}):
            last = get('/api/questions', **query_params, page=huge, page_size=2, sort='asc')
            assert (last['total'], last['page'], last['total_pages']) == (3, 2, 2)
            assert [item['id'] for item in last['items']] == [15]
            first = get('/api/questions', **query_params, page=-huge, page_size=-huge, sort='asc')
            assert (first['page'], first['page_size']) == (1, 1)
            assert [item['id'] for item in first['items']] == [3]
            wide = get('/api/questions', **query_params, page=huge, page_size=huge, sort='asc')
            assert (wide['page'], wide['page_size'], wide['total_pages']) == (1, 100, 1)
            assert [item['id'] for item in wide['items']] == [3, 8, 15]
    ''')


def test_literal_search_combined_filters_and_reads_preserve_database_bytes(isolated_app):
    isolated_app(r'''
        import sqlite3
        from pathlib import Path

        first = seed_question(10, source='研究来源甲', difficulty='normal',
                              category_compulsory='必修一', category_chapter='集合',
                              category_knowledge='集合的含义', tags='标签甲',
                              answer_markdown='解析专用词', review='评析专用词')
        first.content = r'命中 alpha 100%_ $x_{2}=3$ ![可见标签](/static/uploads/plain.png)'
        image = seed_question(20, source='其他来源', difficulty='normal')
        image.content = '![图片说明](/static/uploads/100%_唯一目标.png)'
        third = seed_question(30, source='研究来源甲', difficulty='challenge')
        third.content = '命中 beta'
        db.add_all([
            QuestionKnowledgePoint(question_id=10, point_code='1015', role='main', status='confirmed'),
            QuestionKnowledgePoint(question_id=10, point_code='1016', role='main', status='confirmed'),
            QuestionKnowledgePoint(question_id=20, point_code='1015', role='main', status='confirmed'),
            QuestionKnowledgePoint(question_id=30, point_code='1016', role='main', status='suggested'),
        ])
        db.commit()

        def database_state():
            uri = Path(main.DATABASE_PATH).resolve().as_uri() + '?mode=ro'
            with sqlite3.connect(uri, uri=True) as connection:
                return (connection.execute('PRAGMA user_version').fetchone()[0],
                        tuple(connection.iterdump()))

        before = database_state()
        searches = {
            '%_': [10], '100%_': [10], '唯一目标': [], '可见标签': [10],
            '解析专用词': [10], '评析专用词': [10], '标签甲': [10],
            r'$x_{2}=3$': [10],
        }
        for keyword, expected in searches.items():
            page = get('/api/questions', q=keyword, page=1, page_size=1, sort='asc')
            assert page['total'] == len(expected)
            assert [item['id'] for item in page['items']] == expected
            legacy = get('/api/questions', search=keyword, sort='asc')
            assert [item['id'] for item in legacy] == expected

        combined = get('/api/questions', q='命中', page=1,
                       compulsory='必修一', chapter='集合', knowledge='集合的含义',
                       qtype='detailed_answer', difficulty='normal', source='研究来源甲',
                       knowledge_point_codes=['1015', '1016'], knowledge_point_mode='all',
                       knowledge_point_role='main', knowledge_point_status='confirmed')
        assert combined['total'] == 1 and combined['items'][0]['id'] == 10
        any_points = get('/api/questions', q='命中', page=1, sort='asc',
                         knowledge_point_codes=['1016'], knowledge_point_status='all')
        assert [item['id'] for item in any_points['items']] == [10, 30]
        confirmed = get('/api/questions', q='命中', page=1, sort='asc',
                        knowledge_point_codes=['1016'], knowledge_point_status='confirmed')
        assert [item['id'] for item in confirmed['items']] == [10]
        assert database_state() == before
    ''')
