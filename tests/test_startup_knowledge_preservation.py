"""Startup must preserve chapter-level, custom and cross-version classification."""
import pytest
from mathbank.curriculums import CURRICULUM_NAMES, load_curriculum
from mathbank.database import Question, QuestionCurriculum


def seed(session, compulsory, chapter, knowledge, version='A'):
    question=Question(content='保留分类 $\\dfrac{1}{2}$',question_type='single_choice',
        category_compulsory=compulsory,category_chapter=chapter,
        category_knowledge=knowledge,answer_markdown='原解析',review='原点评')
    session.add(question);session.flush()
    mirror=QuestionCurriculum(question_id=question.id,version_code=version,
        compulsory=compulsory,chapter=chapter,knowledge=knowledge)
    session.add(mirror);session.commit()
    return question,mirror


@pytest.mark.parametrize('compulsory,chapter,knowledge',[
    ('必修二','8. 立体几何初步','8. 立体几何初步'),
    ('必修二','9. 统计','9. 统计'),
    ('选修三','6. 计数原理','6. 计数原理'),
    ('必修二','8. 立体几何初步','8.1 基本立体图形'),
    ('必修二','8. 立体几何初步','教研自定义：空间模型'),
    ('历史自编教材','自定义章名','历史专题标签'),
    ('必修二','8. 立体几何初步',''),
    ('必修二','8. 立体几何初步',None),
])
def test_startup_preserves_stored_question_and_mirror_labels(db_session,compulsory,chapter,knowledge):
    import main
    question,mirror=seed(db_session,compulsory,chapter,knowledge)
    original=(question.category_knowledge,mirror.knowledge)
    main.heal_database_curriculum_names()
    main.heal_database_curriculum_names()
    db_session.expire_all()
    assert (question.category_compulsory,question.category_chapter)==(compulsory,chapter)
    assert (question.category_knowledge,mirror.knowledge)==original
    assert (mirror.compulsory,mirror.chapter)==(compulsory,chapter)
    assert question.content=='保留分类 $\\dfrac{1}{2}$'
    assert question.answer_markdown=='原解析' and question.review=='原点评'


@pytest.mark.parametrize('version',list(CURRICULUM_NAMES))
def test_startup_preserves_each_textbook_version(db_session,version):
    import main
    tree=load_curriculum(version)
    comp,chapter,knowledge=next((comp,chapter,leaves[0])
        for comp,chapters in tree.items() for chapter,leaves in chapters.items() if leaves)
    question,mirror=seed(db_session,comp,chapter,knowledge,version)
    # The active preset must not decide which labels survive in other versions.
    main.heal_database_curriculum_names()
    db_session.expire_all()
    assert question.category_knowledge==mirror.knowledge==knowledge
    assert mirror.version_code==version


def test_proven_book_aliases_still_normalize_without_changing_knowledge(db_session):
    import main
    question,mirror=seed(db_session,'必修第二册','8. 立体几何初步','历史章节标签')
    main.heal_database_curriculum_names()
    db_session.expire_all()
    assert question.category_compulsory==mirror.compulsory=='必修二'
    assert question.category_knowledge==mirror.knowledge=='历史章节标签'


def test_actual_post_startup_path_preserves_labels(db_session,monkeypatch):
    import main
    question,mirror=seed(db_session,'必修二','8. 立体几何初步','8. 立体几何初步')
    monkeypatch.setattr(main.time,'sleep',lambda seconds:None)
    monkeypatch.setattr(main,'create_full_backup_if_due',lambda:None)
    monkeypatch.setattr(main,'clean_orphaned_images',lambda:None)
    monkeypatch.setattr(main,'recalibrate_usage_counts',lambda:None)
    monkeypatch.setattr(main,'rebuild_all_missing_fingerprints',lambda *a,**kw:{'backfilled':0})
    monkeypatch.setattr(main,'print_optional_tool_diagnostics',lambda:None)
    main.start_startup_cleanup()
    db_session.expire_all()
    assert question.category_knowledge==mirror.knowledge=='8. 立体几何初步'
