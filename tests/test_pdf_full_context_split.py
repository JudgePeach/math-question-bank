"""No HTTP: full context is never cut and each output scope has a shared budget."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import pytest
import requests

from mathbank.content_locks import lock_visible_math
from mathbank.document_projects import ProjectConflict
from mathbank.pdf_full_context_split import full_context_pdf_split_eligibility, request_full_context_pdf_split
from mathbank.task_manager import TaskCancelled

PROVIDER = SimpleNamespace(provider_code="offline", model_name="same-original-model", reasoning_effort="low",
    api_key="never-network", chat_completions_url="https://no-network.invalid/v1/chat/completions")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **kw: pytest.fail("No model HTTP permitted"))


def source(n=19):
    preamble = "2026年模拟考试\n注意事项\n1. 填写姓名。\n2. 使用铅笔。\n3. 交回试卷。\n"
    questions = "\n\n".join(f"{i}. 第{i}题完整条件$x_{i}=1$。" + "原题完整说明。"*65 +
        ("\n<!-- MATHBANK_PDF_PAGE:2 -->\n这是跨页续文，仍属同一题。" if i==10 else "") for i in range(1,n+1))
    return preamble + "一、单项选择题\n" + questions + "\n# 参考答案与解析\n" + "\n".join(
        f"{i}\\. 第{i}题原答案$x_{i}+1=2$，原始完整推理。" for i in range(1,n+1))


def rows(numbers):
    return [{"source_number": n, "content": f"原题{n}完整正文", "answer_markdown": f"原解{n}",
             "unknown_audit": {"kept": n}} for n in reversed(numbers)]


class Cache:
    def __init__(self):
        self.saved = {}
        self.saves = []
    @staticmethod
    def key(scope, binding):
        return scope, json.dumps(binding,sort_keys=True)
    def load_partial(self, scope, input_binding=None):
        return deepcopy(self.saved.get(self.key(scope,input_binding)))
    def save_partial(self, scope, result, input_binding=None):
        self.saved[self.key(scope,input_binding)] = deepcopy(result)
        self.saves.append(deepcopy(result["source_numbers"]))
        return True


def run(text=None, *, parse=None, post=None, **kwargs):
    text = text or source()
    diagnostics = kwargs.pop("diagnostics", {})
    if parse is None:
        def parse(full, **options):
            if post is not None:
                options["request_post"](PROVIDER, {"same_full_source":full}, timeout=600)
            return rows(options["requested_source_numbers"])
    value = request_full_context_pdf_split(text, locked_source=kwargs.pop("locked_source",lock_visible_math(text,"scope")[0]),
        provider=kwargs.pop("provider",PROVIDER), parse_full=parse,
        request_post=post or (lambda *a, **kw: pytest.fail("Unexpected HTTP")), diagnostics=diagnostics, **kwargs)
    return value, diagnostics.get("pdf_parallel_split")


def test_explicit_census_ignores_administrative_notes_and_cross_page_continuation():
    plan = full_context_pdf_split_eligibility(source())
    assert plan["source_numbers"]==list(range(1,20)) and plan["question_count"]==19
    assert plan["stem_range"][0] > source().index("3. 交回试卷")


def test_estimated_answer_lengths_only_balance_output_and_never_cut_source():
    text = source()
    text = text.replace("19\\. 第19题原答案", "19\\. " + "最后一题原解很长。"*600 + "第19题原答案")
    plan = full_context_pdf_split_eligibility(text)
    assert plan["source_numbers"] == list(range(1,20))
    seen = []
    def parse(full, **options):
        seen.append((full, options["requested_source_numbers"]))
        return rows(options["requested_source_numbers"])
    result, report = run(text, parse=parse)
    assert len(result.questions)==19 and all(full==lock_visible_math(text,"scope")[0] for full,_ in seen)
    first = sorted([nums for _,nums in seen])[0]
    assert first[-1] > 10
    assert report["estimate_is_token_measurement"] is False


@pytest.mark.parametrize("mutate", [
    lambda s:s.replace("一、单项选择题\n",""),
    lambda s:s.replace("# 参考答案与解析","答案仅供参考，以下是说明"),
    lambda s:s+"\n# 参考答案\n其他内容",
    lambda s:s.replace("10. 第10题", "11. 第10题"),
    lambda s:s.replace("10. 第10题", "20. 第10题"),
    lambda s:s.replace("10. 第10题", "(10) 第10题"),
    lambda s:s.replace("10. 第10题", "3. 第10题"),
    lambda s:s[:7000],
    lambda s:s.replace("1. 第1题", "\\begin{choices}\n1. 第1题",1),
])
def test_uncertain_census_does_not_dispatch_or_use_cache(mutate):
    text = mutate(source())
    assert full_context_pdf_split_eligibility(text) is None
    result, report = run(text,parse=lambda *a,**kw:pytest.fail("No paid planning or parse"))
    assert result is None and report is None


def test_numbers_in_code_math_choices_nested_tables_and_subquestions_are_not_heads():
    extra = "\n```text\n40. 代码并非题目\n```\n$$\n41. 数学并非题目\n$$\n"
    extra += "\\begin{choices}\n\\item A\n42. 选项并非题目\n\\end{choices}\n"
    extra += "\\begin{tabular}{c}\n43. 外表\n\\begin{tabular}{c}\n44. 内表\n\\end{tabular}\\end{tabular}\n"
    extra += "（1）这只是小问\n(2) 这也是小问\n1.5不是整数题号\n"
    text = source().replace("# 参考答案与解析",extra+"# 参考答案与解析")
    assert full_context_pdf_split_eligibility(text)["source_numbers"]==list(range(1,20))


def test_both_old_parser_calls_receive_identical_complete_locked_source_and_original_options():
    text = source()
    locked = lock_visible_math(text,"full-input")[0]
    seen = []
    def parse(full, **options):
        seen.append((full,options))
        assert options["pdf_retry"] is True and options["pdf_max_attempts"]==1
        assert options["connection_retry"] is False
        assert "这是跨页续文" in full and "第19题原答案" in full and "第1题完整条件" in full
        return rows(options["requested_source_numbers"])
    result, report = run(text,parse=parse,locked_source=locked)
    assert [row["source_number"] for row in result.questions]==list(range(1,20))
    assert len(seen)==2 and all(full==locked for full,_ in seen)
    assert sorted([options["requested_source_numbers"] for _,options in seen])==[list(range(1,11)),list(range(11,20))]
    assert not result.preserved_indices and result.used_parallel and not result.partial
    assert report["mode"]=="full_context" and report["source_verified"] is False


def test_changed_or_unrecognized_locked_source_never_dispatches():
    result, report = run(parse=lambda *a,**kw:pytest.fail("Changed input"),locked_source="other source")
    assert result is None and report is None


def test_real_two_way_concurrency_has_stable_order_and_success_saved_immediately():
    cache = Cache()
    barrier = threading.Barrier(2)
    completed = threading.Event()
    active = 0
    maximum = 0
    lock = threading.Lock()
    def post(*a,**kw):
        nonlocal active,maximum
        with lock:
            active+=1;maximum=max(maximum,active)
        barrier.wait(timeout=3)
        with lock:active-=1
        return SimpleNamespace(status_code=200)
    def parse(full, **options):
        options["request_post"](PROVIDER,{},timeout=600)
        nums=options["requested_source_numbers"]
        if nums[0]==1:
            assert completed.wait(3)
        else:
            completed.set()
        return rows(nums)
    result, report = run(parse=parse,post=post,cache=cache)
    assert maximum==2 and report["calls"]==2 and len(cache.saves)==2
    assert [r["source_number"] for r in result.questions]==list(range(1,20))


def test_only_failed_scope_is_retried_and_success_is_cached_before_repair():
    cache = Cache()
    counts = {}
    lock = threading.Lock()
    def parse(full, **options):
        nums=options["requested_source_numbers"]
        options["request_post"](PROVIDER,{},timeout=600)
        with lock:counts[nums[0]]=counts.get(nums[0],0)+1;number=counts[nums[0]]
        if nums[0]==1 and number==1:
            return rows(nums[:-1])
        if number==2:
            assert cache.saves==[list(range(11,20))]
        return rows(nums)
    result,report=run(parse=parse,post=lambda *a,**kw:SimpleNamespace(status_code=200),cache=cache)
    assert counts=={1:2,11:1} and report["calls"]==3 and len(result.questions)==19


def test_two_bad_scopes_share_only_one_scope_repair_and_keep_pending_raw_numbers():
    counts={}
    def parse(full,**options):
        nums=options["requested_source_numbers"];options["request_post"](PROVIDER,{},timeout=600)
        counts[nums[0]]=counts.get(nums[0],0)+1
        return rows(nums) if counts[nums[0]]>1 else []
    result,report=run(parse=parse,post=lambda *a,**kw:SimpleNamespace(status_code=200))
    assert report["calls"]==3 and counts=={1:2,11:1}
    assert result.partial and [r["source_number"] for r in result.questions]==list(range(1,11))
    assert report["pending_source_numbers"]==list(range(11,20))


@pytest.mark.parametrize("bad", ["duplicates","extra","missing","bool","missing_number"])
def test_invalid_source_number_coverage_is_never_adopted(bad):
    def parse(full,**options):
        nums=options["requested_source_numbers"]
        values=rows(nums)
        if nums[0]!=1:return values
        if bad=="duplicates":values[-1]["source_number"]=values[0]["source_number"]
        elif bad=="extra":values[-1]["source_number"]=99
        elif bad=="missing":values.pop()
        elif bad=="bool":values[-1]["source_number"]=True
        else:values[-1].pop("source_number")
        return values
    result, report=run(parse=parse)
    assert result.partial and [r["source_number"] for r in result.questions]==list(range(11,20))
    assert report["pending_source_numbers"]==list(range(1,11))


def test_full_success_cache_reuses_all_fields_with_zero_model_calls():
    cache=Cache()
    first,_=run(cache=cache)
    second,report=run(cache=cache,parse=lambda *a,**kw:pytest.fail("Successful scopes must not be repeated"))
    assert second.questions==first.questions and all(r["unknown_audit"]["kept"]==r["source_number"] for r in second.questions)
    assert report["calls"]==0 and report["reused_groups"]==report["completed_groups"]==2


def test_original_number_resolution_audit_is_copied_to_each_completed_attempt():
    audits=[]
    def parse(full,**options):
        audit={"inferred_count":len(options["requested_source_numbers"]),"source_verified":False,
               "inferred_source_numbers":list(options["requested_source_numbers"])}
        audits.append(audit)
        options["request_diagnostics"]["pdf_source_number_resolution"]=audit
        return rows(options["requested_source_numbers"])
    result,report=run(parse=parse)
    assert len(result.questions)==19
    assert sorted(a["source_number_resolution"]["inferred_count"] for a in report["attempts"])==[9,10]
    for audit in audits:audit["inferred_source_numbers"].clear()
    assert all(a["source_number_resolution"]["inferred_source_numbers"] for a in report["attempts"])


def test_partial_resume_sends_missing_scope_only():
    cache=Cache()
    first,_=run(cache=cache,parse=lambda full,**options:rows(options["requested_source_numbers"])
        if options["requested_source_numbers"][0]==1 else [])
    assert first.partial
    seen=[]
    def parse(full,**options):
        seen.append(options["requested_source_numbers"])
        return rows(options["requested_source_numbers"])
    second,report=run(cache=cache,parse=parse)
    assert seen==[list(range(11,20))] and len(second.questions)==19 and report["reused_groups"]==1


def test_provider_or_effort_change_invalidates_scope_cache():
    cache=Cache();run(cache=cache)
    other=SimpleNamespace(**{**vars(PROVIDER),"reasoning_effort":"high"})
    seen=[]
    result,report=run(cache=cache,provider=other,parse=lambda full,**options:(seen.append(True) or rows(options["requested_source_numbers"])))
    assert len(seen)==2 and report["reused_groups"]==0 and len(result.questions)==19


def test_all_invalid_scopes_raise_instead_of_false_zero_success():
    diagnostics={}
    with pytest.raises(ValueError,match="均未得到可用题目"):
        run(parse=lambda *a,**kw:[],diagnostics=diagnostics)
    assert diagnostics["pdf_parallel_split"]["status"]=="failed"


def test_permanent_provider_error_is_not_retried_or_full_fallback():
    calls=[]
    def post(*a,**kw):
        calls.append(1)
        error=requests.exceptions.HTTPError("safe failure")
        error.response=SimpleNamespace(status_code=403)
        raise error
    with pytest.raises(ValueError,match="均未得到可用题目"):
        run(post=post)
    assert len(calls)==2


def test_supplied_post_hook_forbids_lower_retries_and_redirects():
    seen=[]
    def post(*a,**kw):seen.append(kw);return SimpleNamespace(status_code=200)
    result,report=run(post=post)
    assert report["calls"]==2 and len(result.questions)==19
    assert all(kw["retry_connection"] is False and kw["allow_redirects"] is False for kw in seen)


def test_cancelled_late_request_cannot_save_a_scope():
    cache=Cache();barrier=threading.Barrier(3);release=threading.Event();cancel=threading.Event()
    def post(*a,**kw):barrier.wait(timeout=3);assert release.wait(3);return SimpleNamespace(status_code=200)
    def checked():
        if cancel.is_set():raise TaskCancelled()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(run,post=post,cache=cache,check_cancelled=checked)
        barrier.wait(timeout=3);cancel.set()
        with pytest.raises(TaskCancelled):future.result(timeout=2)
        release.set()
    assert not cache.saves


def test_changed_source_checkpoint_stops_without_paid_repair():
    def parse(*a,**kw):raise ProjectConflict("source changed")
    with pytest.raises(ProjectConflict):run(parse=parse)
