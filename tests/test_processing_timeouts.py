"""Processing budgets reach real subprocess seams without extending cleanup."""
import subprocess
from types import SimpleNamespace

import pytest

from mathbank.processing_timeouts import (
    LATEX_EXPORT_TIMEOUT_SECONDS, RUNTIME_DOWNLOAD_TIMEOUT_SECONDS,
    RUNTIME_PROBE_TIMEOUT_SECONDS, SOURCE_RENDER_TIMEOUT_SECONDS,
    TIKZ_COMPILE_TIMEOUT_SECONDS, WORD_CONVERSION_TIMEOUT_SECONDS,
)


def test_processing_budgets_have_explicit_operation_scope():
    assert (TIKZ_COMPILE_TIMEOUT_SECONDS, LATEX_EXPORT_TIMEOUT_SECONDS,
            WORD_CONVERSION_TIMEOUT_SECONDS, SOURCE_RENDER_TIMEOUT_SECONDS,
            RUNTIME_DOWNLOAD_TIMEOUT_SECONDS, RUNTIME_PROBE_TIMEOUT_SECONDS) == (120, 300, 300, 300, 120, 30)


def test_word_conversion_and_formula_fallback_receive_different_budgets(monkeypatch):
    from mathbank import word_export_helper as word
    calls = []
    def run(command, **options):
        calls.append((command, options["timeout"]))
        return subprocess.CompletedProcess(command, 1, b"", b"failed fixture")
    monkeypatch.setattr(word.subprocess, "run", run)
    monkeypatch.setattr(word.shutil, "which", lambda _name: "/fixture/xelatex")
    assert word.PandocOmmlConverter("/fixture/pandoc")._run_pandoc(["x+1"]) == {}
    assert word._formula_fallback_png("x+1") is None
    assert [entry[1] for entry in calls] == [WORD_CONVERSION_TIMEOUT_SECONDS, TIKZ_COMPILE_TIMEOUT_SECONDS]


def test_latex_timeout_message_matches_current_per_pass_budget(monkeypatch):
    from mathbank import paper_helper as paper
    with paper._PDF_CACHE_LOCK:
        paper._PDF_CACHE.clear()
    def run(command, **options):
        assert options["timeout"] == LATEX_EXPORT_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, options["timeout"])
    monkeypatch.setattr(paper.subprocess, "run", run)
    pdf, message = paper.compile_tex_to_pdf("processing-timeout-only-fixture")
    assert pdf is None
    assert f"单次编译超过 {LATEX_EXPORT_TIMEOUT_SECONDS} 秒" in message


def test_native_source_renderer_can_pass_old_budget_and_stops_at_new_budget(tmp_path, monkeypatch):
    from mathbank import docx_source_evidence as evidence
    assert evidence.RENDER_TIMEOUT_SECONDS == SOURCE_RENDER_TIMEOUT_SECONDS
    original = tmp_path / "original.docx"
    original.write_bytes(b"fixture-only")
    process = SimpleNamespace(returncode=None, pid=123, poll=lambda: None)
    stopped, sleeps = [], []
    ticks = iter([0, SOURCE_RENDER_TIMEOUT_SECONDS - 1, SOURCE_RENDER_TIMEOUT_SECONDS + 1])
    monkeypatch.setattr(evidence.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(evidence, "_configure_native_fonts", lambda *_a: None)
    monkeypatch.setattr(evidence, "_stop_own_process", stopped.append)
    monkeypatch.setattr(evidence.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(evidence.time, "sleep", sleeps.append)
    with pytest.raises(evidence._EvidenceError, match=f"超过{SOURCE_RENDER_TIMEOUT_SECONDS}秒"):
        evidence._native_pdf("/fixture/soffice", original, tmp_path, lambda: None)
    assert sleeps == [0.1] and stopped == [process]


def test_process_termination_keeps_its_short_grace_period(monkeypatch):
    from mathbank import docx_source_evidence as evidence
    waits, commands = [], []
    def wait(*, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("fixture child", timeout)
    process = SimpleNamespace(pid=123, poll=lambda: None, wait=wait)
    monkeypatch.setattr(evidence, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(evidence.subprocess, "run", lambda command, **options: commands.append((command, options)))
    evidence._stop_own_process(process)
    assert waits == [3, 3]
    assert [options["timeout"] for _, options in commands] == [3, 3]
    assert "/F" not in commands[0][0] and "/F" in commands[1][0]
