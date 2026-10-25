"""Real file SQLite, deterministic model replies, no live app or credentials."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from mathbank import agent as module
from mathbank.agent import ApplyInput, ChatInput, LibraryAgent, create_agent_router
from mathbank.backup import create_full_backup, verify_full_backup
from mathbank.database import Base, Question, get_db


def action_proposal(setup, monkeypatch, tool, arguments, ids=(4, 9), stream=False):
    actions = [{"tool": "get_questions", "arguments": {"ids": list(ids)}},
               {"tool": tool, "arguments": arguments}]
    if stream:
        iterator = iter(actions)
        monkeypatch.setattr(setup.agent, "stream_completion", lambda messages: iter([json.dumps(next(iterator))]))
        events = "".join(setup.agent.stream_chat(ChatInput(message="用户要求执行该操作，先生成方案"), setup.db))
        result = next(json.loads(block.split("data: ", 1)[1]) for block in events.split("\n\n")
                      if block.startswith("event: result"))
    else:
        scripted(monkeypatch, setup.agent, *actions)
        result = setup.agent.chat(ChatInput(message="用户要求执行该操作，先生成方案"), setup.db)
    return result, ApplyInput(session_id=result["session_id"], proposal_id=result["proposal"]["id"])


@pytest.mark.parametrize("stream", [False, True])
def test_paper_proposal_confirmation_preserves_order_scores_and_replays_once(setup, monkeypatch, stream):
    from mathbank.database import Paper, PaperQuestion

    result, payload = action_proposal(setup, monkeypatch, "propose_paper", {
        "title": "合成小练", "paper_type": "quiz", "questions": [
            {"id": 9, "score": 0, "reason": "热身"}, {"id": 4, "score": 12, "reason": "巩固"}]}, stream=stream)
    assert result["proposal"]["kind"] == "paper"
    assert result["proposal"]["paper"]["total_score"] == 12
    assert setup.db.query(Paper).count() == 0 and not setup.calls
    receipt = setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert receipt == setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert setup.calls == ["backup", "export"]
    with setup.factory() as db:
        paper = db.get(Paper, receipt["paper_id"])
        assert (paper.title, paper.total_score, paper.paper_type) == ("合成小练", 12, "quiz")
        assert [(q.question_id, q.score) for q in db.query(PaperQuestion).order_by(PaperQuestion.order_index)] == [(9, 0), (4, 12)]
        assert db.get(Question, 4).usage_count == db.get(Question, 9).usage_count == 1
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []


@pytest.mark.parametrize("stream", [False, True])
def test_delete_confirmation_repairs_papers_and_keeps_deleted_reference_identity(setup, monkeypatch, stream):
    from mathbank.database import Paper, PaperQuestion, QuestionCurriculum, QuestionReference

    with setup.factory() as db:
        db.add(Paper(id=1, title="受影响试卷", total_score=17))
        db.flush()
        db.add_all([PaperQuestion(paper_id=1, question_id=4, score=5, order_index=1),
                    PaperQuestion(paper_id=1, question_id=9, score=12, order_index=2),
                    QuestionCurriculum(question_id=4, version_code="A", compulsory="必修"),
                    QuestionReference(source_question_id=9, target_question_id=4, field="content", start=0,
                                      end=1, text="题", field_sha256="0" * 64, resolution_state="resolved")])
        db.commit()
    result, payload = action_proposal(setup, monkeypatch, "propose_delete", {
        "questions": [{"id": 4, "reason": "用户核对后删除"}]}, ids=(4,), stream=stream)
    assert result["proposal"]["changes"][0]["impact"]["paper_count"] == 1
    assert result["proposal"]["changes"][0]["impact"]["reference_count"] == 1
    assert setup.db.get(Question, 4) is not None and not setup.calls
    receipt = setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert receipt["deleted"] == 1 and "updated" not in receipt
    assert setup.agent.apply(payload, setup.db, BackgroundTasks()) == receipt
    with setup.factory() as db:
        assert db.get(Question, 4) is None and db.get(Question, 9) is not None
        assert db.get(Paper, 1).total_score == 12
        assert db.query(PaperQuestion).count() == 1
        assert db.query(QuestionCurriculum).count() == 0
        reference = db.query(QuestionReference).one()
        assert reference.target_question_id is None and reference.resolution_state == "resolved"
        db.add(Question(id=4, content="复用ID的新题"))
        db.commit()
        assert reference.target_question_id is None
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    assert setup.calls == ["backup", "export"]


@pytest.mark.parametrize("tool", ["propose_paper", "propose_delete"])
def test_new_actions_backup_failure_and_mid_backup_conflict_are_atomic(setup, monkeypatch, tool):
    from mathbank.database import Paper

    args = {"questions": [{"id": 4}, {"id": 9}]}
    if tool == "propose_paper":
        args["title"] = "冲突测试"
    _, payload = action_proposal(setup, monkeypatch, tool, args)
    backup = setup.agent.backup
    monkeypatch.setattr(setup.agent, "backup", lambda: (_ for _ in ()).throw(OSError("private path")))
    with pytest.raises(HTTPException, match="备份失败"):
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    with setup.factory() as db:
        assert db.query(Question).count() == 2 and db.query(Paper).count() == 0

    def concurrent_change():
        result = backup()
        with setup.factory() as db:
            db.get(Question, 9).answer_markdown = "并发修改"
            db.commit()
        return result

    monkeypatch.setattr(setup.agent, "backup", concurrent_change)
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert error.value.status_code == 409
    with setup.factory() as db:
        assert db.query(Question).count() == 2 and db.query(Paper).count() == 0


def test_changed_paper_impact_invalidates_delete_before_backup(setup, monkeypatch):
    from mathbank.database import Paper, PaperQuestion

    _, payload = action_proposal(setup, monkeypatch, "propose_delete", {"questions": [{"id": 4}]})
    with setup.factory() as db:
        db.add(Paper(id=1, title="新引用", total_score=5))
        db.flush()
        db.add(PaperQuestion(paper_id=1, question_id=4, order_index=1, score=5))
        db.commit()
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert error.value.status_code == 409 and not setup.calls


@pytest.mark.parametrize("tool,args", [
    ("propose_delete", {"questions": [{"id": 4}, {"id": 4}]}),
    ("propose_delete", {"questions": [{"id": 4, "patch": {"tags": "x"}}]}),
    ("propose_paper", {"title": "", "questions": [{"id": 4}]}),
    ("propose_paper", {"title": "试卷", "questions": [{"id": 4, "score": True}]}),
    ("propose_paper", {"title": "试卷", "questions": [{"id": 4, "score": 101}]}),
    ("propose_paper", {"title": "试卷", "paper_type": "unknown", "questions": [{"id": 4}]}),
    ("propose_answer", {"changes": [{"id": 4, "patch": {"tags": "x"}}]}),
])
def test_action_specific_arguments_cannot_expand_permissions(setup, tool, args):
    reads = {}
    setup.agent.tool("get_questions", {"ids": [4, 9]}, setup.db, reads)
    with pytest.raises(ValueError):
        setup.agent.build_proposal(tool, args, reads, setup.db)
    assert not setup.calls


@pytest.mark.parametrize("assets", [
    {"image_paths": ["/static/uploads/a.png"]},
    {"tikz_code": r"\begin{tikzpicture}\end{tikzpicture}"},
    {"content": "题目 ![图](/static/uploads/a.png)"},
    {"_content_tikz_assets": "invalid-json"},
])
def test_delete_proposals_reject_questions_with_hidden_or_malformed_assets(setup, assets):
    from mathbank.agent import _snapshot

    question = setup.db.get(Question, 4)
    for field, value in assets.items():
        if field == "image_paths":
            question.image_paths = value
        else:
            setattr(question, field, value)
    setup.db.commit()
    read_rows = {4: _snapshot(setup.db, 4)}
    with pytest.raises(ValueError, match="图片或TikZ"):
        setup.agent.build_proposal("propose_delete", {"questions": [{"id": 4}]}, read_rows, setup.db)
    assert not setup.calls


def test_deletion_impact_includes_incoming_and_outgoing_question_references(setup):
    from mathbank.database import QuestionReference

    setup.db.add(QuestionReference(source_question_id=9, target_question_id=4, field="content", start=0, end=1,
                                   text="指向4", field_sha256="0" * 64, resolution_state="resolved"))
    setup.db.add(QuestionReference(source_question_id=4, target_question_id=9, field="review", start=0, end=1,
                                   text="指向9", field_sha256="1" * 64, resolution_state="resolved"))
    setup.db.commit()
    impact = setup.agent.public_impact(module._snapshot(setup.db, 4)["_deletion_impact"])
    assert impact["reference_count"] == 2


@pytest.mark.parametrize("tool", ["propose_delete", "propose_paper"])
def test_new_actions_require_fresh_complete_reads(setup, tool):
    args = {"questions": [{"id": 4}]}
    if tool == "propose_paper":
        args["title"] = "试卷"
    with pytest.raises(ValueError, match="完整读取"):
        setup.agent.build_proposal(tool, args, {}, setup.db)


def test_dedicated_answer_tool_previews_then_updates_only_answer(setup, monkeypatch):
    result, payload = action_proposal(setup, monkeypatch, "propose_answer", {"changes": [
        {"id": 4, "patch": {"answer_markdown": "**新解析** $x^2$"}, "reason": "完善推导"}]})
    assert result["proposal"]["changes"][0]["before"] == {"answer_markdown": "旧解析"}
    assert setup.db.get(Question, 4).answer_markdown == "旧解析"
    setup.agent.apply(payload, setup.db, BackgroundTasks())
    with setup.factory() as db:
        question = db.get(Question, 4)
        assert question.answer_markdown == "**新解析** $x^2$"
        assert question.tags == "保留标签" and question.content == "导数 $x^2$"


def test_streamed_reply_decodes_every_json_chunk_boundary_without_exposing_tools():
    node = shutil.which('node')
    assert node, 'Node.js is required for frontend executable validation'
    source = (Path(__file__).resolve().parents[1] / 'static/js/agent.js').read_text(encoding='utf-8')
    helper = source[source.index('    function partialReply('):source.index('    function appendMessage(')]
    script = helper + r'''
const assert = require('node:assert/strict');
const text = '## 标题\n**公式** $x_1\\le 2$\n"引号" \\ 路径 😀\t末尾';
for (const encoded of [JSON.stringify({reply:text}), JSON.stringify({reply:text}).replace('😀','\\ud83d\\ude00')]) {
    for (let end=0;end<=encoded.length;end++) {
        const partial=partialReply(encoded.slice(0,end));
        assert.ok(partial===null || text.startsWith(partial), 'bad decoded prefix at '+end);
    }
    assert.equal(partialReply(encoded),text);
    assert.equal(partialReply('```json\n'+encoded+'\n```'),text);
}
assert.equal(partialReply('{"tool":"get_question","arguments":{"reply":"不要显示"}}'),null);
assert.equal(partialReply('{"reply":"bad\\x"}'),null);
assert.equal(partialReply('{"reply":"bad\\u000z"}'),null);
assert.equal(partialReply(JSON.stringify({reply:'a'.repeat(13000)})).length,12000);
'''
    result = subprocess.run([node, '-'], input=script, encoding='utf-8', text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_stream_chat_allows_active_runs_past_five_minutes_but_times_out_when_idle():
    node = shutil.which('node')
    assert node, 'Node.js is required for frontend executable validation'
    source = (Path(__file__).resolve().parents[1] / 'static/js/agent.js').read_text(encoding='utf-8')
    helper = source[source.index('    async function streamChat('):source.index('    function setBusy(')]
    script = helper + r'''
const assert = require('node:assert/strict');
const window = {__localToken:'synthetic'};
let now = 0, nextTimer = 0, signal, reads = 0;
const timers = new Map();
function setTimeout(callback, delay) {
    timers.set(++nextTimer, {callback, at:now+delay});
    return nextTimer;
}
function clearTimeout(id) { timers.delete(id); }
function advance(milliseconds) {
    now += milliseconds;
    for (const [id, timer] of timers) {
        if (timer.at <= now) { timers.delete(id); timer.callback(); }
    }
}
let fetch = async (_path, options) => {
    signal = options.signal;
    return {ok:true, body:{getReader:()=>({read:async()=>{
        advance(20000);
        assert.equal(signal.aborted,false,'active stream was cut off');
        reads++;
        if (reads === 21) return {done:true};
        const event = reads === 20 ? 'result' : 'status';
        return {done:false,value:new TextEncoder().encode('event: '+event+'\ndata: '+JSON.stringify(
            event === 'result' ? {reply:'完成'} : {message:'继续调用'})+'\n\n')};
    }})}};
};
(async()=>{
    assert.deepEqual(await streamChat({message:'合成任务'},{}),{reply:'完成'});
    assert.equal(reads,21);
    assert.ok(now > 300000);
    assert.equal(timers.size,0);
    fetch = async (_path, options) => {
        signal = options.signal;
        return {ok:true,body:{getReader:()=>({read:()=>new Promise((_resolve,reject)=>{
            signal.addEventListener('abort',()=>reject(Object.assign(new Error(),{name:'AbortError'})),{once:true});
        })})}};
    };
    const stalled = streamChat({message:'停滞任务'},{});
    await Promise.resolve();
    advance(59999);
    assert.equal(signal.aborted,false);
    advance(1);
    await assert.rejects(stalled,/等待超时/);
    assert.equal(timers.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, '-'], input=script, encoding='utf-8', text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA user_version=1"))
    with factory() as db:
        db.add_all([Question(id=4, content="导数 $x^2$", tags="保留标签", difficulty="normal", answer_markdown="旧解析", source="合成来源"),
                    Question(id=9, content="集合与导数", difficulty="challenge")])
        db.commit()
    calls = []

    def backup():
        calls.append("backup")
        return create_full_backup(database_path=path, uploads_dir=tmp_path / "uploads",
                                  metadata_path=tmp_path / "custom_metadata.json",
                                  output_dir=tmp_path / "backups", retention=None)

    agent = LibraryAgent(backup=backup, prepare_write=lambda *args: calls.append("prepare") or "prepared",
                         commit_write=lambda *args: calls.append("commit"),
                         after_commit=lambda *args: calls.append("export"), check_storage=lambda: None,
                         write_lock=threading.RLock())
    with factory() as db:
        yield SimpleNamespace(agent=agent, db=db, factory=factory, path=path, calls=calls)
    engine.dispose()


def scripted(monkeypatch, agent, *actions):
    iterator = iter(actions)
    monkeypatch.setattr(agent, "complete", lambda messages: next(iterator))


def run_model_actions(setup, monkeypatch, actions, stream):
    iterator = iter(actions)
    calls = []
    def complete(messages):
        calls.append(json.loads(json.dumps(messages)))
        action = next(iterator)
        return module._parse_model_output(action) if isinstance(action, str) else action
    if stream:
        def stream_completion(messages):
            calls.append(json.loads(json.dumps(messages)))
            action = next(iterator)
            encoded = action if isinstance(action, str) else json.dumps(action, ensure_ascii=False)
            for start in range(0, len(encoded), 3):
                yield encoded[start:start + 3]
        monkeypatch.setattr(setup.agent, "stream_completion", stream_completion)
    else:
        monkeypatch.setattr(setup.agent, "complete", complete)
    app = FastAPI()
    app.include_router(create_agent_router(setup.agent))
    app.dependency_overrides[get_db] = lambda: setup.db
    with TestClient(app) as client:
        response = client.post("/api/agent/chat" + ("/stream" if stream else ""),
                               json={"message": "请为题库第1题生成操作方案"})
    events = [(block.splitlines()[0][7:], json.loads(block.split("data: ", 1)[1]))
              for block in response.text.split("\n\n") if block.startswith("event: ")] if stream else []
    result = next((payload for event, payload in events if event == "result"), None) if stream else (
        response.json() if response.status_code == 200 else None)
    return response, result, events, calls


DSML_SEARCHES = '''<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="tool">
<｜｜DSML｜｜ parameter name="arguments" string="false">{"q": "函数", "offset": 0}</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="tool" string="true">search_questions</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
<｜｜DSML｜｜ invoke name="tool">
<｜｜DSML｜｜ parameter name="arguments" string="false">{"q": "数列", "offset": 0}</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="tool" string="true">search_questions</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
<｜｜DSML｜｜ invoke name="tool">
<｜｜DSML｜｜ parameter name="arguments" string="false">{"q": "立体几何", "offset": 0}</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="tool" string="true">search_questions</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
<｜｜DSML｜｜ invoke name="tool">
<｜｜DSML｜｜ parameter name="arguments" string="false">{"q": "概率", "offset": 0}</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="tool" string="true">search_questions</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
<｜｜DSML｜｜ invoke name="tool">
<｜｜DSML｜｜ parameter name="arguments" string="false">{"q": "圆锥曲线", "offset": 0}</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="tool" string="true">search_questions</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>'''

DSML_STATISTICS_CONTINUATION = '''{}</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="tool" string="true">statistics</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>'''


def dsml_actions(actions):
    def parameter(key, value):
        kind = "true" if isinstance(value, str) else "false"
        encoded = value if kind == "true" else json.dumps(value, ensure_ascii=False)
        return f'<｜｜DSML｜｜ parameter name="{key}" string="{kind}">{encoded}</｜｜DSML｜｜ parameter>'
    return "<｜｜DSML｜｜ calls>\n" + "\n".join(
        '<｜｜DSML｜｜ invoke name="tool">\n'
        + "\n".join(parameter(key, value) for key, value in sorted(
            action.items(), key=lambda item: item[0] != "arguments"))
        + '\n</｜｜DSML｜｜ invoke>' for action in actions) + '\n</｜｜DSML｜｜ calls>'


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("encoding", ["reported", "single_marker", "fenced", "argument_continuation"])
def test_dsml_reported_five_searches_execute_in_order_then_reply(setup, monkeypatch, stream, encoding):
    themes = ["函数", "数列", "立体几何", "概率", "圆锥曲线"]
    with setup.factory() as db:
        db.add_all([Question(id=index + 20, content=theme + '合成题') for index, theme in enumerate(themes)])
        db.commit()
    first = DSML_SEARCHES
    if encoding == "single_marker":
        first = first.replace('｜｜DSML｜｜ ', '｜DSML｜').replace('calls>', 'function_calls>')
    elif encoding == "fenced":
        first = '```dsml\n' + first + '\n```'
    elif encoding == "argument_continuation":
        first = first[first.index('{"q"'):]
    response, result, events, calls = run_model_actions(
        setup, monkeypatch, [first, {"reply": "五个主题已检索"}], stream)
    assert response.status_code == 200 and result["reply"] == "五个主题已检索"
    assert len(calls) == 2  # No paid format-repair round trip.
    assert [item["tool"] for item in result["trace"]] == ["search_questions"] * 5
    assert [item["items"][0]["id"] for item in result["trace"]] == list(range(20, 25))
    sent = [json.loads(item["content"]) for item in calls[1] if item["role"] == "assistant"]
    assert [item["arguments"]["q"] for item in sent] == themes
    assert result["proposal"] is None and not setup.calls
    assert not any(event == "error" for event, _ in events)


def test_dsml_parameters_preserve_json_math_and_literal_markers():
    literal = '引号"与\\frac{1}{2}及</｜｜DSML｜｜ parameter>和&amp;'
    actions = [{"tool": "search_questions", "arguments": {"q": literal, "offset": 0}}]
    assert module._parse_model_output(dsml_actions(actions)) == actions[0]
    source = r'''<｜DSML｜function_calls><｜DSML｜invoke name="search_questions">
<｜DSML｜parameter string="true" name="q">$\frac{1}{2}$ &amp; "引号"</｜DSML｜parameter>
<｜DSML｜parameter name="offset" string="false">0</｜DSML｜parameter>
</｜DSML｜invoke></｜DSML｜function_calls>'''
    assert module._parse_model_output(source) == {"tool": "search_questions", "arguments": {
        "q": r'$\frac{1}{2}$ &amp; "引号"', "offset": 0}}
    assert module._parse_model_output(dsml_actions([{"tool": "search_questions", "arguments": {
        "q": r"$\iu^2=-1$"}}]).replace(r"\\iu", r"\iu"))["arguments"]["q"] == r"$\iu^2=-1$"
    reply = {"reply": "DSML示例仅作为文字：" + DSML_SEARCHES}
    assert module._parse_model_output(json.dumps(reply, ensure_ascii=False)) == reply


@pytest.mark.parametrize("stream", [False, True])
def test_screenshot_statistics_continuation_executes_once_then_continues(setup, monkeypatch, stream):
    response, result, events, calls = run_model_actions(setup, monkeypatch, [
        DSML_STATISTICS_CONTINUATION, {"reply": "已统计题库，继续按19题高考卷选题"}], stream)
    assert response.status_code == 200 and len(calls) == 2
    assert result["reply"] == "已统计题库，继续按19题高考卷选题"
    assert result["trace"] == [{"tool": "statistics", "count": 2, "by_difficulty": {"normal": 1, "challenge": 1}}]
    assert result["proposal"] is None and not setup.calls
    assert not any(event == "error" for event, _ in events)


@pytest.mark.parametrize("stream", [False, True])
def test_deepseek_http_receipt_of_screenshot_continuation_reaches_tools(setup, monkeypatch, stream):
    provider = module.resolve_text_provider('DEEPSEEK/deepseek-flash', {
        'DEEPSEEK_API_KEY': 'synthetic-key', 'DEEPSEEK_API_BASE': 'https://example.invalid'})
    monkeypatch.setattr(setup.agent, "config", lambda: ('DEEPSEEK/deepseek-flash', provider))
    contents = iter([DSML_STATISTICS_CONTINUATION, '{"reply":"统计已完成"}'])
    sent, closed = [], []
    class Response:
        status_code = 200
        def __init__(self, content):
            self.content = content
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": self.content}}]}
        def iter_content(self, chunk_size):
            events = [{"choices": [{"index": 0, "delta": {"content": piece}}]}
                      for piece in self.content]
            events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            wire = b''.join(b'data: ' + json.dumps(event, ensure_ascii=False).encode() + b'\n\n'
                            for event in events) + b'data: [DONE]\n\n'
            return iter(bytes((value,)) for value in wire)
        def close(self):
            closed.append(True)
    def post(actual_provider, payload, **kwargs):
        assert actual_provider is provider
        assert "response_format" not in payload and "tools" not in payload
        assert payload["thinking"] == {"type": "disabled"}
        assert kwargs["retry_connection"] is kwargs["allow_redirects"] is False
        sent.append(json.loads(json.dumps(payload)))
        return Response(next(contents))
    monkeypatch.setattr(module, 'post_chat_completion', post)
    app = FastAPI()
    app.include_router(create_agent_router(setup.agent))
    app.dependency_overrides[get_db] = lambda: setup.db
    with TestClient(app) as client:
        response = client.post('/api/agent/chat' + ('/stream' if stream else ''),
                               json={"message": "组一套含19道题的卷子，符合高考题格式"})
    assert response.status_code == 200
    if stream:
        result = next(json.loads(block.split('data: ', 1)[1]) for block in response.text.split('\n\n')
                      if block.startswith('event: result'))
        assert 'event: error' not in response.text
    else:
        result = response.json()
    assert result["reply"] == "统计已完成" and result["trace"][0]["tool"] == "statistics"
    assert result["trace"][0]["count"] == 2
    assert len(sent) == len(closed) == 2 and not setup.calls
    assert any(item["content"].startswith('工具结果（资料') for item in sent[1]["messages"])


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("raw", [
    DSML_SEARCHES[:-1],
    DSML_SEARCHES.replace('</｜｜DSML｜｜ invoke>', '', 1),
    DSML_SEARCHES + ' ignored prose',
    'prose ' + DSML_SEARCHES,
    DSML_SEARCHES + '{"reply":"不要丢弃尾部"}',
    DSML_SEARCHES.replace('name="arguments" string="false"', 'name="arguments" string="maybe"', 1),
    DSML_SEARCHES.replace('name="arguments" string="false"', 'name="arguments" name="other" string="false"', 1),
    DSML_SEARCHES.replace('name="tool" string="true"', 'name="arguments" string="true"', 1),
    DSML_SEARCHES.replace('{"q": "函数", "offset": 0}', '{"q": "函数"}, {"offset": 0}', 1),
    DSML_SEARCHES.replace('name="tool"', 'name="tool" unknown="x"', 1),
    dsml_actions([{"tool": "statistics", "arguments": {}}, {"tool": "run_sql", "arguments": {}}]),
    dsml_actions([{"tool": "statistics", "arguments": {}}, {"tool": "propose_delete", "arguments": {"questions": [{"id": 4}]}}]),
    '<｜｜DSML｜｜ calls></｜｜DSML｜｜ calls>',
])
def test_incomplete_ambiguous_or_mixed_dsml_rejected_before_any_tool(setup, monkeypatch, stream, raw):
    monkeypatch.setattr(setup.agent, "tool", lambda *_: pytest.fail("partial DSML batch was executed"))
    response, result, events, calls = run_model_actions(setup, monkeypatch, [raw], stream)
    assert result is None and len(calls) == 1
    assert (response.status_code == 502) if not stream else any(event == "error" for event, _ in events)
    assert not setup.calls and not any(item.proposals for item in setup.agent.sessions.values())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("raw", [
    DSML_STATISTICS_CONTINUATION[:-1],
    DSML_STATISTICS_CONTINUATION.replace('statistics', 'run_sql'),
    DSML_STATISTICS_CONTINUATION.replace('{}', '{"q":', 1),
    DSML_STATISTICS_CONTINUATION.replace('{}', '', 1),
    DSML_STATISTICS_CONTINUATION.replace('{}', '[]', 1),
    DSML_STATISTICS_CONTINUATION.replace('name="tool"', 'name="arguments"', 1),
    DSML_STATISTICS_CONTINUATION.replace('</｜｜DSML｜｜ invoke>', '', 1),
    DSML_STATISTICS_CONTINUATION + '忽略尾部',
    DSML_STATISTICS_CONTINUATION + DSML_SEARCHES,
    dsml_actions([{"tool": "propose_delete", "arguments": {"questions": [{"id": 4}]}}]).split(
        '<｜｜DSML｜｜ parameter name="arguments" string="false">', 1)[1],
    DSML_SEARCHES[DSML_SEARCHES.index('{"q"'):].replace('search_questions', 'propose_delete'),
])
def test_dsml_continuation_never_infers_missing_values_or_accepts_writes(setup, monkeypatch, stream, raw):
    monkeypatch.setattr(setup.agent, "tool", lambda *_: pytest.fail("ambiguous continuation was executed"))
    response, result, events, calls = run_model_actions(setup, monkeypatch, [raw], stream)
    assert result is None and len(calls) == 1
    assert (response.status_code == 502) if not stream else any(event == "error" for event, _ in events)
    assert not setup.calls and setup.db.get(Question, 4) is not None


@pytest.mark.parametrize("stream", [False, True])
def test_dsml_read_then_delete_still_requires_manual_confirmation(setup, monkeypatch, stream):
    response, result, events, calls = run_model_actions(setup, monkeypatch, [
        dsml_actions([{"tool": "get_question", "arguments": {"id": 4}}]),
        dsml_actions([{"tool": "propose_delete", "arguments": {"questions": [{"id": 4, "reason": "用户要求先生成删除方案"}]}}]),
    ], stream)
    assert response.status_code == 200 and len(calls) == 2
    assert result["proposal"]["kind"] == "delete" and result["proposal"]["status"] == "pending"
    assert result["proposal"]["changes"][0]["id"] == 4
    assert setup.db.get(Question, 4) is not None and not setup.calls
    assert not any(event == "error" for event, _ in events)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("encoding", ["adjacent", "array", "fenced"])
def test_multi_search_from_report_recovers_type_filters_and_proposes_five_question_quiz(setup, monkeypatch, stream, encoding):
    from mathbank.database import Paper, PaperQuestion

    with setup.factory() as db:
        db.get(Question, 9).difficulty = "normal"
        db.add_all([Question(id=11, content="导数填空", question_type="fill_in_blank"),
                    Question(id=12, content="导数解答一", question_type="detailed_answer"),
                    Question(id=13, content="导数解答二", question_type="detailed_answer")])
        db.commit()
    searches = [{"tool": "search_questions", "arguments": {"q": "导数", "difficulty": "normal",
                 "question_type": kind, "offset": 0}} for kind in ("single_choice", "fill_blank", "solution")]
    if encoding == "array":
        first = json.dumps(searches, ensure_ascii=False)
    else:
        first = "\n".join(json.dumps(action, ensure_ascii=False) for action in searches)
        if encoding == "fenced":
            first = "```json\n" + first + "\n```"
    searches = [{"tool": "search_questions", "arguments": {"q": "导数", "question_type": kind}}
                for kind in ("fill_in_blank", "detailed_answer")]
    response, result, events, calls = run_model_actions(setup, monkeypatch, [first, searches,
        {"tool": "get_questions", "arguments": {"ids": [4, 9, 11, 12, 13]}},
        {"tool": "propose_paper", "arguments": {"title": "导数日常小练", "paper_type": "quiz",
            "questions": [{"id": qid, "score": 10, "reason": "导数专项"} for qid in (4, 9, 11, 12, 13)]}}], stream)
    assert response.status_code == 200
    assert len(calls) == 4  # No additional paid repair requests.
    assert result["trace"][:3] == [result["trace"][0], {"tool": "rejected"}, {"tool": "rejected"}]
    assert result["trace"][0]["count"] == 2
    assert [item["count"] for item in result["trace"][3:5]] == [1, 2]
    assert "fill_in_blank" in calls[1][-3]["content"] and "detailed_answer" in calls[1][-1]["content"]
    assert result["proposal"]["kind"] == "paper"
    assert result["proposal"]["paper"]["paper_type"] == "quiz"
    assert result["proposal"]["paper"]["total_score"] == 50
    assert [item["id"] for item in result["proposal"]["changes"]] == [4, 9, 11, 12, 13]
    assert all(item["score"] == 10 for item in result["proposal"]["changes"])
    assert not any(event == "error" for event, _ in events)
    assert not setup.calls and setup.db.query(Paper).count() == 0
    receipt = setup.agent.apply(ApplyInput(session_id=result["session_id"],
                                          proposal_id=result["proposal"]["id"]), setup.db, BackgroundTasks())
    assert receipt["question_count"] == 5 and receipt["total_score"] == 50
    with setup.factory() as db:
        assert [(item.question_id, item.score) for item in db.query(PaperQuestion).order_by(PaperQuestion.order_index)] == [
            (qid, 10) for qid in (4, 9, 11, 12, 13)]
    assert setup.calls == ["backup", "export"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("raw", [
    '{"tool":"statistics","arguments":{}}\n{"tool":"search_questions","arguments":',
    '{"tool":"statistics","arguments":{}} junk',
    'prefix {"tool":"statistics","arguments":{}}',
    '{"tool":"statistics","arguments":{}}\n{"tool":"run_sql","arguments":{}}',
    '[{"tool":"statistics","arguments":{}},{"tool":"propose_delete","arguments":{"questions":[{"id":4}]}}]',
    '[{"tool":"statistics","arguments":{}},{"reply":"已完成"}]',
    '{"tool":"propose_delete","questions":[{"id":4}]}\n{"tool":"get_question","id":4}',
    '[]',
    '{"tool":"statistics","arguments":{}}\n[]',
])
def test_invalid_or_nonreadonly_batches_are_rejected_in_full_before_any_tool(setup, monkeypatch, stream, raw):
    monkeypatch.setattr(setup.agent, "tool", lambda *_: pytest.fail("part of invalid batch executed"))
    response, result, events, calls = run_model_actions(setup, monkeypatch, [raw], stream)
    assert len(calls) == 1 and result is None
    if stream:
        assert any(event == "error" for event, _ in events)
    else:
        assert response.status_code == 502
    assert not setup.calls
    assert not any(conversation.proposals for conversation in setup.agent.sessions.values())


def test_multi_document_parser_keeps_strings_latex_and_nested_json_intact():
    actions = [{"tool": "search_questions", "arguments": {"q": '带引号"和括号}{[]及\\frac{1}{2}'}},
               {"tool": "get_questions", "arguments": {"ids": [4, 9]}}]
    assert module._parse_model_output("".join(json.dumps(action, ensure_ascii=False) for action in actions)) == actions
    assert module._parse_model_output(r'{"reply":"$\\frac{1}{2}$"}') == {"reply": r"$\frac{1}{2}$"}
    assert module._parse_model_output(r'{"reply":"$\iu^2=-1$"}') == {"reply": r"$\iu^2=-1$"}
    assert module._normalize_actions({"reply": "完成"}) == [{"reply": "完成"}]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("encoding", ["array", "adjacent"])
def test_model_rounds_tool_calls_and_response_actions_have_no_count_limit(setup, monkeypatch, stream, encoding):
    actions = [{"tool": "statistics", "arguments": {}}] * 9
    batch = actions if encoding == "array" else "\n".join(json.dumps(action) for action in actions)
    tool = setup.agent.tool
    executed = []
    def counted(*args):
        executed.append(args[0])
        return tool(*args)
    monkeypatch.setattr(setup.agent, "tool", counted)
    response, result, events, calls = run_model_actions(
        setup, monkeypatch, [batch] * 8 + [{"reply": "已完成连续查询"}], stream)
    assert response.status_code == 200
    assert len(calls) == 9 and len(executed) == len(result["trace"]) == 72
    assert result["reply"] == "已完成连续查询" and result["proposal"] is None
    if stream:
        assert sum(event == "tool" for event, _ in events) == 72
        assert any(event == "status" and data["message"] == "正在生成第 9 步…" for event, data in events)
        assert not any(event == "error" for event, _ in events)
    assert setup.agent.sessions[result["session_id"]].turns == 1
    assert not setup.calls


@pytest.mark.parametrize("stream", [False, True])
def test_batch_results_share_the_existing_character_budget(setup, monkeypatch, stream):
    monkeypatch.setattr(setup.agent, "tool", lambda *_: {"total": 1, "detail": "x" * 61000})
    response, result, events, _ = run_model_actions(setup, monkeypatch, [
        [{"tool": "statistics", "arguments": {}}] * 2], stream)
    assert result is None and "120000字符" in response.text
    assert not setup.calls


def test_query_invalid_types_report_canonical_values_but_existing_custom_types_still_work(setup):
    with pytest.raises(ValueError, match="fill_in_blank"):
        setup.agent.tool("search_questions", {"question_type": "fill_blank"}, setup.db, {})
    with pytest.raises(ValueError, match="detailed_answer"):
        setup.agent.tool("statistics", {"question_type": "solution"}, setup.db, {})
    with pytest.raises(ValueError, match="难度参数无效"):
        setup.agent.tool("statistics", {"difficulty": "medium"}, setup.db, {})
    setup.db.get(Question, 4).question_type = "自定义证明题"
    setup.db.commit()
    assert setup.agent.tool("statistics", {"question_type": "自定义证明题"}, setup.db, {})["total"] == 1


@pytest.mark.parametrize("stream", [False, True])
def test_flat_delete_from_report_generates_review_and_waits_for_confirmation(setup, monkeypatch, stream):
    # Reproduce the reported mixed pair: wrapped read followed by a flat delete.
    actions = [{"tool": "get_question", "arguments": {"seq_num": 1}},
               {"tool": "propose_delete", "questions": [{
                   "id": 4, "reason": "用户明确要求为题库第1题先生成删除方案，删除前已读取完整题目详情。"}]}]
    response, result, events, calls = run_model_actions(setup, monkeypatch, actions, stream)
    assert response.status_code == 200
    assert len(calls) == 2
    assert not any(event == "error" for event, _ in events)
    assert result["proposal"]["kind"] == "delete"
    assert result["proposal"]["status"] == "pending"
    assert result["proposal"]["changes"][0]["id"] == 4
    assert result["proposal"]["changes"][0]["content"] == "导数 $x^2$"
    assert "impact" in result["proposal"]["changes"][0]
    with setup.factory() as db:
        assert db.query(Question).count() == 2
    assert not setup.calls
    receipt = setup.agent.apply(ApplyInput(session_id=result["session_id"],
                                          proposal_id=result["proposal"]["id"]), setup.db, BackgroundTasks())
    assert receipt["deleted"] == 1
    assert setup.calls == ["backup", "export"]
    with setup.factory() as db:
        assert db.get(Question, 4) is None and db.get(Question, 9) is not None


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("action,kind", [
    ({"tool": "propose_changes", "changes": [{"id": 4, "patch": {"tags": "保留标签,导数"}}]}, "update"),
    ({"tool": "propose_answer", "changes": [{"id": 4, "patch": {"answer_markdown": "新解析 $x^2$"}}]}, "update"),
    ({"tool": "propose_paper", "title": "合成小练", "paper_type": "quiz", "questions": [{"id": 4, "score": 12}]}, "paper"),
])
def test_flat_known_proposals_still_require_review(setup, monkeypatch, stream, action, kind):
    _, result, _, calls = run_model_actions(setup, monkeypatch, [
        {"tool": "get_question", "id": 4}, action], stream)
    assert len(calls) == 2
    assert result["proposal"]["kind"] == kind
    assert result["proposal"]["status"] == "pending"
    assert json.loads(calls[1][-2]["content"]) == {"tool": "get_question", "arguments": {"id": 4}}
    assert not setup.calls
    with setup.factory() as db:
        assert db.get(Question, 4).tags == "保留标签"
        assert db.get(Question, 4).answer_markdown == "旧解析"


@pytest.mark.parametrize("stream", [False, True])
def test_flat_queries_preserve_filters_and_batch_reads(setup, monkeypatch, stream):
    _, result, _, calls = run_model_actions(setup, monkeypatch, [
        {"tool": "search_questions", "q": "导数", "offset": 0},
        {"tool": "statistics", "difficulty": "normal"},
        {"tool": "get_questions", "ids": [4, 9]},
        {"reply": "查询完成"}], stream)
    assert len(calls) == 4
    assert result["trace"][0]["count"] == 2
    assert result["trace"][1]["count"] == 1
    assert result["trace"][2]["ids"] == [4, 9]
    assert result["proposal"] is None and not setup.calls


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("action", [
    {"tool": "propose_delete", "arguments": {"questions": [{"id": 4}]}, "questions": [{"id": 9}]},
    {"tool": "propose_delete", "questions": [{"id": 4}], "confirm": True},
    {"tool": "propose_delete", "questions": [{"id": 4}], "reply": "已删除"},
    {"tool": "run_sql", "sql": "DELETE FROM questions"},
    {"tool": "propose_answer", "changes": [], "path": "private.db"},
    {"tool": ["propose_delete"], "questions": [{"id": 4}]},
])
def test_flat_ambiguous_or_unknown_envelopes_cannot_expand_permissions(setup, monkeypatch, stream, action):
    response, result, events, calls = run_model_actions(setup, monkeypatch, [
        {"tool": "get_question", "arguments": {"id": 4}}, action], stream)
    assert len(calls) == 2 and result is None
    if stream:
        assert any(event == "error" and "格式无效" in payload["message"] for event, payload in events)
    else:
        assert response.status_code == 502 and "格式无效" in response.json()["detail"]
    assert not setup.calls
    assert not any(conversation.proposals for conversation in setup.agent.sessions.values())
    with setup.factory() as db:
        assert db.query(Question).count() == 2


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("case", ["unread", "wrong_id", "duplicate", "non_integer", "image"])
def test_flat_delete_keeps_original_question_guards(setup, monkeypatch, stream, case):
    if case == "image":
        with setup.factory() as db:
            db.get(Question, 4).content += " ![图](/static/uploads/synthetic.png)"
            db.commit()
    items = [{"id": 9 if case == "wrong_id" else "4" if case == "non_integer" else 4}]
    if case == "duplicate":
        items *= 2
    actions = [] if case == "unread" else [{"tool": "get_question", "id": 4}]
    actions += [{"tool": "propose_delete", "questions": items}, {"reply": "方案未通过校验"}]
    _, result, _, calls = run_model_actions(setup, monkeypatch, actions, stream)
    assert len(calls) == len(actions)
    assert result["proposal"] is None
    assert result["trace"][-1] == {"tool": "rejected"}
    assert not setup.calls
    with setup.factory() as db:
        assert db.query(Question).count() == 2


def proposal(setup, monkeypatch, patch=None, two=False):
    actions = [{"tool": "get_question", "arguments": {"seq_num": 1}}]
    changes = [{"id": 4, "patch": patch or {"tags": "保留标签,导数"}, "reason": "合成验证"}]
    if two:
        actions.append({"tool": "get_question", "arguments": {"id": 9}})
        changes.append({"id": 9, "patch": {"tags": "第二题"}})
    actions.append({"tool": "propose_changes", "arguments": {"changes": changes}})
    scripted(monkeypatch, setup.agent, *actions)
    result = setup.agent.chat(ChatInput(message="为这些题生成修改方案"), setup.db)
    return result, ApplyInput(session_id=result["session_id"], proposal_id=result["proposal"]["id"])


def test_bounded_search_statistics_and_sequence_ids(setup):
    agent, db = setup.agent, setup.db
    assert agent.tool("search_questions", {"q": "导数"}, db, {})["total"] == 2
    assert agent.tool("search_questions", {"q": "%"}, db, {})["total"] == 0
    assert agent.tool("statistics", {}, db, {}) == {"total": 2, "by_difficulty": {"challenge": 1, "normal": 1}}
    assert agent.tool("statistics", {"source": "合成"}, db, {})["total"] == 1
    assert agent.tool("get_question", {"seq_num": 1}, db, {})["id"] == 4
    assert agent.tool("get_question", {"id": 9}, db, {})["seq_num"] == 2
    with pytest.raises(ValueError):
        agent.tool("search_questions", {"offset": 10001}, db, {})


def test_batch_reads_support_ten_question_proposals_within_round_budget(setup, monkeypatch):
    ids = [4, 9, *range(20, 28)]
    setup.db.add_all(Question(id=qid, content=f"合成题 {qid}", tags="") for qid in ids[2:])
    setup.db.commit()
    scripted(monkeypatch, setup.agent,
             {"tool": "get_questions", "arguments": {"ids": ids}},
             {"tool": "propose_changes", "arguments": {"changes": [
                 {"id": qid, "patch": {"tags": "批量整理"}, "reason": "用户要求"} for qid in ids]}})
    result = setup.agent.chat(ChatInput(message="整理这10题的标签"), setup.db)
    assert len(result["proposal"]["changes"]) == 10
    assert result["trace"][0]["ids"] == ids
    assert not setup.calls
    receipt = setup.agent.apply(ApplyInput(session_id=result["session_id"], proposal_id=result["proposal"]["id"]),
                                setup.db, BackgroundTasks())
    assert receipt["updated"] == 10
    with setup.factory() as db:
        assert all(db.get(Question, qid).tags == "批量整理" for qid in ids)


@pytest.mark.parametrize("ids", [[], [4, 4], [True], list(range(1, 12)), ["4"]])
def test_batch_read_rejects_ambiguous_or_unbounded_ids(setup, ids):
    with pytest.raises(ValueError):
        setup.agent.tool("get_questions", {"ids": ids}, setup.db, {})


def test_batch_read_handles_missing_and_oversized_results_without_granting_write(setup):
    reads = {}
    result = setup.agent.tool("get_questions", {"ids": [4, 99]}, setup.db, reads)
    assert set(reads) == {4}
    assert result["items"][1] == {"id": 99, "error": "题目不存在。"}
    for qid in (4, 9):
        question = setup.db.get(Question, qid)
        question.content = question.answer_markdown = question.review = "长" * 20000
    setup.db.commit()
    with pytest.raises(ValueError, match="减少题数"):
        setup.agent.tool("get_questions", {"ids": [4, 9]}, setup.db, {})


def test_followup_keeps_query_ids_but_requires_fresh_reads(setup, monkeypatch):
    scripted(monkeypatch, setup.agent, {"tool": "search_questions", "arguments": {"q": "导数"}},
             {"reply": "找到两题"})
    first = setup.agent.chat(ChatInput(message="找导数题"), setup.db)
    captured = []
    monkeypatch.setattr(setup.agent, "complete", lambda messages: captured.extend(messages) or {"reply": "请重新读取"})
    setup.agent.chat(ChatInput(message="整理刚才的题", session_id=first["session_id"]), setup.db)
    context = next(message["content"] for message in captured if message["content"].startswith("历史工具结果"))
    assert '"id": 4' in context and '"id": 9' in context
    with pytest.raises(ValueError, match="完整读取"):
        setup.agent.propose({"changes": [{"id": 4, "patch": {"tags": "新标签"}}]}, {}, setup.db)
    conversation = setup.agent.sessions[first["session_id"]]
    assert conversation.turns == 2
    assert len(json.dumps(conversation.context, ensure_ascii=False)) <= module.MAX_CONTEXT_CHARS


def test_detail_preserves_answer_text_and_reports_inline_images(setup):
    answer = "<!-- MathBank source attribution:\n原答案说明\n-->\n\n旧解析 ![图](/static/uploads/synthetic.png)"
    setup.db.get(Question, 4).answer_markdown = answer
    setup.db.commit()
    result = setup.agent.tool("get_question", {"id": 4}, setup.db, {})
    assert result["answer_markdown"] == answer
    assert result["has_images"] is True


def test_new_chat_releases_session_capacity_and_invalidates_old_proposals(setup, monkeypatch):
    result, payload = proposal(setup, monkeypatch)
    conversation = setup.agent.sessions[result["session_id"]]
    with conversation.lock:
        with pytest.raises(HTTPException) as error:
            setup.agent.close_session(result["session_id"])
        assert error.value.status_code == 409
    assert setup.agent.close_session(result["session_id"])["status"] == "closed"
    assert setup.agent.close_session(result["session_id"])["status"] == "closed"
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert error.value.status_code == 410
    for _ in range(20):
        sid, _ = setup.agent.session()
        setup.agent.close_session(sid)
    assert not setup.agent.sessions


def test_chat_does_not_write_then_confirmation_is_atomic_backed_up_and_idempotent(setup, monkeypatch):
    result, payload = proposal(setup, monkeypatch, two=True)
    assert setup.db.get(Question, 4).tags == "保留标签"
    assert not setup.calls
    assert "snapshot" not in result["proposal"]["changes"][0]
    written = setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert written["updated"] == 2
    assert setup.calls == ["prepare", "prepare", "backup", "commit", "commit", "export"]
    backup_path = setup.path.parent / "backups" / written["backup"]
    assert verify_full_backup(backup_path)
    with setup.factory() as independent:
        assert independent.get(Question, 4).tags == "保留标签,导数"
        assert independent.get(Question, 9).tags == "第二题"
    setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert setup.calls.count("backup") == 1


def test_real_connection_edit_invalidates_whole_proposal(setup, monkeypatch):
    _, payload = proposal(setup, monkeypatch, two=True)
    with setup.factory() as other:
        other.get(Question, 9).content = "人工编辑"
        other.commit()
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert error.value.status_code == 409
    assert setup.db.get(Question, 4).tags == "保留标签"
    assert not setup.calls


def test_curriculum_and_knowledge_point_changes_invalidate_proposal(setup, monkeypatch):
    from mathbank.database import KnowledgePoint, QuestionCurriculum, QuestionKnowledgePoint

    with setup.factory() as db:
        db.add(QuestionCurriculum(question_id=4, version_code="A", compulsory="必修一", chapter="函数"))
        db.add(KnowledgePoint(code="agent-point", name="原知识点", original_name="原知识点", source="test"))
        db.flush()
        db.add(QuestionKnowledgePoint(question_id=4, point_code="agent-point", role="main", status="confirmed"))
        db.commit()
    _, payload = proposal(setup, monkeypatch)
    with setup.factory() as db:
        db.query(QuestionKnowledgePoint).filter_by(question_id=4).delete()
        db.query(QuestionCurriculum).filter_by(question_id=4, version_code="A").update({"chapter": "数列"})
        db.commit()
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert error.value.status_code == 409
    assert setup.db.get(Question, 4).tags == "保留标签"
    assert not setup.calls


@pytest.mark.parametrize("failure", ["backup", "prepare"])
def test_backup_or_second_write_failure_rolls_back_every_question(setup, monkeypatch, failure):
    _, payload = proposal(setup, monkeypatch, two=True)

    def fail(*args):
        if failure == "backup" or args[1] == 9:
            raise RuntimeError("sensitive path and prompt")

    monkeypatch.setattr(setup.agent, failure if failure == "backup" else "prepare_write", fail)
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert "sensitive" not in error.value.detail
    with setup.factory() as other:
        assert other.get(Question, 4).tags == "保留标签"
        assert other.get(Question, 9).tags == ""


def test_commit_exception_blocks_replay_without_claiming_rollback(setup, monkeypatch):
    _, payload = proposal(setup, monkeypatch)
    real_commit = setup.db.commit

    def ambiguous_commit():
        real_commit()
        raise RuntimeError("after commit error")

    monkeypatch.setattr(setup.db, "commit", ambiguous_commit)
    with pytest.raises(HTTPException, match="待核实"):
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert error.value.status_code == 409
    assert setup.calls.count("backup") == 1
    with setup.factory() as other:
        assert other.get(Question, 4).tags == "保留标签,导数"


def test_post_commit_export_failure_does_not_mask_success(setup, monkeypatch):
    _, payload = proposal(setup, monkeypatch)
    monkeypatch.setattr(setup.agent, "after_commit", lambda tasks: (_ for _ in ()).throw(RuntimeError()))
    assert setup.agent.apply(payload, setup.db, BackgroundTasks())["status"] == "applied"


@pytest.mark.parametrize("patch", [{"content": "改写题干"}, {"difficulty": "hard"}, {"tags": ["x"]},
                                  {"answer_markdown": "![图](https://invalid.example/image.png)"}])
def test_invalid_or_asset_modifying_proposals_rejected(setup, patch):
    reads = {}
    setup.agent.tool("get_question", {"id": 4}, setup.db, reads)
    with pytest.raises(ValueError):
        setup.agent.propose({"changes": [{"id": 4, "patch": patch}]}, reads, setup.db)
    assert setup.db.get(Question, 4).content == "导数 $x^2$"


def test_unread_truncated_and_duplicate_targets_cannot_be_proposed(setup):
    change = {"id": 4, "patch": {"tags": "x"}}
    with pytest.raises(ValueError):
        setup.agent.propose({"changes": [change]}, {}, setup.db)
    reads = {}
    setup.agent.tool("get_question", {"id": 4}, setup.db, reads)
    with pytest.raises(ValueError):
        setup.agent.propose({"changes": [change, change]}, reads, setup.db)
    setup.db.get(Question, 4).content = "长" * 20001
    setup.db.commit()
    reads = {}
    assert setup.agent.tool("get_question", {"id": 4}, setup.db, reads)["truncated"]
    assert not reads


def test_other_sessions_expired_or_superseded_proposals_cannot_apply(setup, monkeypatch):
    result, payload = proposal(setup, monkeypatch)
    sid, _ = setup.agent.session()
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(ApplyInput(session_id=sid, proposal_id=payload.proposal_id), setup.db, BackgroundTasks())
    assert error.value.status_code == 404
    scripted(monkeypatch, setup.agent, {"reply": "新一轮答复"})
    setup.agent.chat(ChatInput(message="继续", session_id=result["session_id"]), setup.db)
    with pytest.raises(HTTPException) as error:
        setup.agent.apply(payload, setup.db, BackgroundTasks())
    assert error.value.status_code == 409
    setup.agent.sessions[result["session_id"]].touched -= 3601
    with pytest.raises(HTTPException) as error:
        setup.agent.session(result["session_id"])
    assert error.value.status_code == 410


def test_unknown_tools_remain_rejected_without_round_limit_and_strict_http_contract(setup, monkeypatch):
    calls = []
    actions = iter([{"tool": "run_sql", "arguments": {"sql": "DELETE FROM questions"}}] * 8
                   + [{"reply": "该工具不支持，未修改题库"}])
    monkeypatch.setattr(setup.agent, "complete", lambda messages: calls.append(1) or next(actions))
    result = setup.agent.chat(ChatInput(message="test"), setup.db)
    assert len(calls) == 9 and result["proposal"] is None
    assert result["trace"] == [{"tool": "rejected"}] * 8
    assert setup.db.query(Question).count() == 2
    app = FastAPI()
    app.include_router(create_agent_router(setup.agent))
    app.dependency_overrides[get_db] = lambda: setup.db
    monkeypatch.setattr(setup.agent, "config", lambda: ("fake", SimpleNamespace(api_key="fake", chat_completions_url="https://example.invalid")))
    with TestClient(app) as client:
        config = client.get("/api/agent/config").json()
        assert config["max_rounds"] is config["max_tool_calls"] is config["max_actions_per_response"] is None
        assert config["configured"] is True
        assert client.post("/api/agent/apply", json={"session_id": result["session_id"], "proposal_id": "forged", "patch": {}}).status_code == 422
        assert client.post("/api/agent/chat", json={"message": " "}).status_code == 422
        assert client.post("/api/agent/chat", json={"message": "x", "history": []}).status_code == 422
        monkeypatch.setattr(setup.agent, "stream_completion", lambda messages: iter(['{"reply":"流式端点正常"}']))
        streamed = client.post("/api/agent/chat/stream", json={"message": "继续"})
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        assert "流式端点正常" in streamed.text
    conversation = setup.agent.sessions[result["session_id"]]
    with conversation.lock:
        with pytest.raises(HTTPException) as error:
            setup.agent.chat(ChatInput(message="same", session_id=result["session_id"]), setup.db)
        assert error.value.status_code == 409


def test_stream_chat_emits_incremental_model_and_tool_events_without_writing(setup, monkeypatch):
    actions = iter([
        {"tool": "get_question", "arguments": {"id": 4}},
        {"tool": "propose_changes", "arguments": {"changes": [
            {"id": 4, "patch": {"tags": "保留标签,流式测试"}, "reason": "流式提案验证"}
        ]}},
    ])

    def stream_completion(messages):
        encoded = json.dumps(next(actions), ensure_ascii=False)
        yield encoded[:9]
        yield encoded[9:]

    monkeypatch.setattr(setup.agent, "stream_completion", stream_completion)
    events = "".join(setup.agent.stream_chat(ChatInput(message="整理这道题"), setup.db))
    assert "event: model_delta" in events
    assert "event: tool" in events and '"name":"get_question"' in events
    assert "event: result" in events and "修改方案已生成" in events
    assert events.index('"text":"{') < events.index("event: tool")
    assert setup.db.get(Question, 4).tags == "保留标签"
    assert not setup.calls


@pytest.mark.parametrize("content", ['{"reply":"ok"}', DSML_SEARCHES, DSML_STATISTICS_CONTINUATION])
@pytest.mark.parametrize("finish", ["stop", "tool_calls"])
def test_stream_completion_decodes_sse_without_exposing_reasoning(setup, monkeypatch, content, finish):
    monkeypatch.setenv("PREFER_AGENT_MODEL", "ZHONGZHAN_GPT/gpt-5.6(high)")
    monkeypatch.setenv("ZHONGZHAN_GPT_API_KEY", "fake-test-key")
    monkeypatch.setenv("ZHONGZHAN_GPT_BASE_URL", "https://example.invalid/v1")
    captured = {}

    class Response:
        status_code = 200

        def __init__(self):
            events = [
                {"choices": [{"index": 0, "delta": {"reasoning_content": "private"}}]},
                *[{"choices": [{"index": 0, "delta": {"content": content[start:start + 7]}}]}
                  for start in range(0, len(content), 7)],
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            body = b"".join(
                b"data: " + json.dumps(event).encode() + b"\n\n"
                for event in events
            ) + b"data: [DONE]\n\n"
            self.chunks = iter(bytes((value,)) for value in body)

        def iter_content(self, chunk_size):
            assert chunk_size == 1
            return self.chunks

        def close(self):
            captured["closed"] = True

    def post(provider, payload, **kwargs):
        captured.update(payload=payload, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(module, "post_chat_completion", post)
    received = "".join(setup.agent.stream_completion([{"role": "user", "content": "synthetic"}]))
    assert received == content
    assert module._parse_model_output(received) == module._parse_model_output(content)
    assert "private" not in received
    assert captured["payload"]["stream"] is True
    assert "response_format" not in captured["payload"]
    assert captured["kwargs"]["stream"] is True
    assert captured["kwargs"]["timeout"] == (10, 30)
    assert captured["closed"]


def test_stream_chat_masks_provider_errors_and_releases_capacity(setup, monkeypatch):
    def failed_stream(_messages):
        yield '{"reply":'
        raise RuntimeError("secret provider body")

    monkeypatch.setattr(setup.agent, "stream_completion", failed_stream)
    events = "".join(setup.agent.stream_chat(ChatInput(message="统计题目"), setup.db))
    assert "event: error" in events
    assert "secret provider body" not in events
    assert "未完成" in events
    assert not any(conversation.lock.locked() for conversation in setup.agent.sessions.values())
    assert setup.agent.slots.acquire(blocking=False)
    setup.agent.slots.release()


def test_http_uses_shared_provider_policy_no_retry_and_masks_private_errors(setup, monkeypatch):
    monkeypatch.setenv("PREFER_AGENT_MODEL", "ZHONGZHAN_GPT/gpt-5.6(high)")
    monkeypatch.setenv("ZHONGZHAN_GPT_API_KEY", "fake-test-key")
    monkeypatch.setenv("ZHONGZHAN_GPT_BASE_URL", "https://example.invalid/v1")
    captured = {}

    class Response:
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": '{"reply":"ok"}'}}]}
        def close(self):
            captured["closed"] = True

    def post(provider, payload, **kwargs):
        captured.update(payload=payload, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(module, "post_chat_completion", post)
    assert setup.agent.complete([{"role": "user", "content": "synthetic"}]) == {"reply": "ok"}
    assert "response_format" not in captured["payload"]
    assert captured["kwargs"]["retry_connection"] is False
    assert captured["kwargs"]["allow_redirects"] is False
    assert captured["kwargs"]["timeout"] == (10, 30)
    assert captured["closed"]
    monkeypatch.setattr(module, "post_chat_completion", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fake-test-key private prompt")))
    with pytest.raises(HTTPException) as error:
        setup.agent.complete([])
    assert "fake-test-key" not in error.value.detail


@pytest.mark.parametrize("encoding", ["adjacent", "dsml", "argument_continuation"])
def test_regular_http_completion_decodes_all_commands(setup, monkeypatch, encoding):
    monkeypatch.setenv("PREFER_AGENT_MODEL", "ZHONGZHAN_GPT/gpt-5.6(high)")
    monkeypatch.setenv("ZHONGZHAN_GPT_API_KEY", "fake-test-key")
    monkeypatch.setenv("ZHONGZHAN_GPT_BASE_URL", "https://example.invalid/v1")
    actions = [{"tool": "search_questions", "arguments": {"q": "导数"}},
               {"tool": "statistics", "arguments": {}}]
    content = dsml_actions(actions) if encoding == "dsml" else "\n".join(
        json.dumps(action, ensure_ascii=False) for action in actions)
    if encoding == "argument_continuation":
        content = dsml_actions(actions).split('<｜｜DSML｜｜ parameter name="arguments" string="false">', 1)[1]
    closed = []
    class Response:
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": content}}]}
        def close(self):
            closed.append(True)
    monkeypatch.setattr(module, "post_chat_completion", lambda *_args, **_kwargs: Response())
    assert setup.agent.complete([{"role": "user", "content": "合成多指令测试"}]) == actions
    assert closed == [True]


def test_real_main_prepare_hook_updates_fingerprint_and_references(setup):
    # Load only the pure transaction hook, never importing main (which initializes the live app).
    from sqlalchemy.orm import lazyload
    from mathbank.question_duplicate_service import upsert_question_fingerprint
    from mathbank.question_duplicates import QuestionDuplicateInput, build_question_fingerprint
    from mathbank.question_references import sync_question_references

    source = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "_prepare_agent_question_write")
    namespace = {"Question": Question, "upsert_question_fingerprint": upsert_question_fingerprint,
                 "sync_question_references": sync_question_references,
                 "build_prepared_question_fingerprint": lambda **values: build_question_fingerprint(QuestionDuplicateInput(
                     content=values["content"], answer_markdown=values["answer_markdown"], question_type=values["question_type"]))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "agent-hook", "exec"), namespace)
    fingerprint = namespace["_prepare_agent_question_write"](setup.db, 4, {"answer_markdown": "新解析"})
    expected = build_question_fingerprint(QuestionDuplicateInput(
        content="导数 $x^2$", answer_markdown="新解析",
        question_type=setup.db.get(Question, 4).question_type,
    ))
    assert fingerprint.answer_hash == expected.answer_hash
    setup.db.get(Question, 4).answer_markdown = "新解析"
    from mathbank.question_duplicate_service import upsert_question_fingerprint
    upsert_question_fingerprint(setup.db, setup.db.get(Question, 4), fingerprint)
    setup.db.commit()
    assert setup.db.execute(text("SELECT COUNT(*) FROM question_fingerprints WHERE question_id=4")).scalar() == 1
