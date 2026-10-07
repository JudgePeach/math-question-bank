"""Only an immediately introduced complete local data table proves this reference."""
from copy import deepcopy

import pytest

from mathbank.word_source_dependencies import analyze_word_source_dependencies


TABLE = r"""\begin{tabular}{|c|c|c|}
\hline
年份（ $x$ ） & 2016 & 2017 \\
\hline
GDP（ $y$ ） & 74.64 & 83.20 \\
\hline
\end{tabular}"""
INTRO = '2016—2017年GDP的数据（摘自年鉴）如下：\n\n'


def paper(first='独立第一题。', second='', *, answer=None):
    source = '1. '+first+'\n\n'
    cut = len(source)
    source += '2. '+second+'\n\n'
    questions = [{'id':'q1','source_number':1,'source_range':[0,cut],'raw_content':source[:cut]},
                 {'id':'q2','source_number':2,'source_range':[cut,len(source)],'raw_content':source[cut:]}]
    if answer is not None:
        start = len(source)
        source += answer
        questions[1]['answer_source_range']=[start,len(source)]
        questions[1]['raw_answer']=answer
    return source,questions


def analyze(body, **kwargs):
    source, questions = paper(second=body, **kwargs)
    return analyze_word_source_dependencies(source,questions)


@pytest.mark.parametrize('verb',['由','根据'])
@pytest.mark.parametrize('reference',['以上数据','上述数据'])
def test_immediately_introduced_complete_local_table_proves_only_its_data_reference(verb,reference):
    source,questions = paper(second=INTRO+TABLE+'\n\n'+verb+reference+'，得到样本数据。')
    unchanged=deepcopy(questions)
    report=analyze_word_source_dependencies(source,questions)
    assert report['status']=='complete' and not report['has_unresolved'] and not report['has_dependencies']
    assert report['references']==[] and report['groups']==[] and report['question_risks']==[]
    proof=report['local_data_references'][0]
    assert proof['owner_id']=='q2' and source[slice(*proof['table_range'])]==TABLE
    assert source[slice(*proof['reference_range'])]==reference
    assert source[slice(*proof['introduction_range'])].startswith('数据')
    assert source[slice(*proof['introduction_range'])].strip().endswith('如下：')
    assert questions==unchanged and report==analyze_word_source_dependencies(source,questions)


@pytest.mark.parametrize('body',[
    INTRO+TABLE+'\n'+TABLE+'\n由以上数据，计算。',
    '由以上数据，计算。\n'+INTRO+TABLE,
    INTRO+TABLE.replace(r'\end{tabular}','')+'\n由以上数据，计算。',
    INTRO+TABLE.replace(r'\end{tabular}',r'\end{array}')+'\n由以上数据，计算。',
    INTRO+TABLE.replace('74.64 & 83.20','74.64')+'\n由以上数据，计算。',
    INTRO+TABLE.replace('74.64 & 83.20 '+r'\\','74.64 & 83.20')+'\n由以上数据，计算。',
    INTRO+TABLE.replace('|c|c|c|','|p{2cm}|c|c|')+'\n由以上数据，计算。',
    INTRO+TABLE.replace('74.64',r'\multirow{2}{*}{74.64}')+'\n由以上数据，计算。',
    INTRO+TABLE.replace('74.64',r'\textbf{74.64')+'\n由以上数据，计算。',
    INTRO+TABLE+'\n另有一组测量数据。\n由以上数据，计算。',
    INTRO+TABLE+'\n以上数据，计算。',
    '统计表如下：\n'+TABLE+'\n由以上数据，计算。',
    INTRO+TABLE+'\n根据上述函数求值。',
    INTRO+TABLE+'\n根据上述条件求值。',
    INTRO+TABLE+'\n根据前述数据求值。',
    INTRO+TABLE+'\n根据以上结果求值。',
    INTRO+'```text\n'+TABLE+'\n```\n由以上数据，计算。',
    INTRO+'$'+TABLE+'$\n由以上数据，计算。',
    INTRO+TABLE.replace(r'\begin',r'\\begin').replace(r'\end',r'\\end')+'\n由以上数据，计算。',
])
def test_incomplete_ambiguous_or_unproved_local_table_reference_remains_uncertain(body):
    report=analyze(body)
    assert report['has_unresolved']
    assert not report.get('local_data_references')


def test_table_in_preceding_question_is_not_transferred_to_this_question():
    report=analyze('由以上数据，计算。',first=INTRO+TABLE)
    assert report['has_unresolved'] and not report.get('local_data_references')
    assert report['references'][0]['owner_id']=='q2'
    assert report['references'][0]['member_ids']==['q1','q2']


@pytest.mark.parametrize('prefix',[
    '上题的', '上一题的', '第1题的', '本题与第1题共用', '以下两题共享',
])
def test_explicit_external_or_shared_data_declaration_vetoes_the_local_exception(prefix):
    report=analyze(prefix+INTRO+TABLE+'\n由以上数据，计算。')
    assert report['has_unresolved'] and not report.get('local_data_references')
    assert any(r['kind']=='implicit_source_reference' for r in report['references'])


def test_original_answer_table_cannot_supply_a_question_content_certificate():
    report=analyze('独立第二题。',answer=INTRO+TABLE+'\n由以上数据，计算。')
    assert report['has_unresolved'] and not report.get('local_data_references')
    assert report['references'][0]['owner_id']=='q2'


def test_other_explicit_reference_keeps_its_original_dependency_evidence():
    report=analyze(INTRO+TABLE+'\n由以上数据，根据第1题的结论计算。')
    assert report['has_dependencies'] and report['has_unresolved']
    explicit=next(r for r in report['references'] if r['kind']=='explicit_question_reference')
    assert explicit['target_numbers']==[1] and explicit['member_ids']==['q1','q2']
    assert not report.get('local_data_references')


def test_second_nondirect_reference_is_not_exempted_with_the_first_one():
    report=analyze(INTRO+TABLE+'\n由以上数据，计算第一项。\n根据上述数据与其他条件计算第二项。')
    assert report['has_unresolved']
    assert len(report['local_data_references'])==1
    assert len(report['references'])==1 and report['references'][0]['evidence']=='上述数据'
