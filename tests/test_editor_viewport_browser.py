"""Editor viewport bounds with modern CSS and unsupported dvh declarations."""

import json
import re

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401


VIEWPORTS = [(1361, 657), (1093, 525), (907, 438), (375, 667), (1361, 657)]
DVH_DECLARATION = r"[-\w]+\s*:\s*[^;{}]*dvh[^;{}]*;"


@pytest.mark.parametrize("legacy_viewport", [False, True], ids=["modern", "without-dvh"])
def test_editor_stays_in_view_and_preserves_input(browser, tmp_path, legacy_viewport):
    browser.command("open", browser.evaluate("location.href"))
    browser.command("wait", "--fn", "typeof openNewQuestionEditor === 'function'")
    source = (browser.root / "static/css/app.css").read_text(encoding="utf-8")
    if legacy_viewport:
        # Filter raw declarations before parsing: CSSOM only retains the last
        # declaration of a property and would otherwise discard its vh fallback.
        source, removed = re.subn(DVH_DECLARATION, "", source, flags=re.IGNORECASE)
        assert removed > 0
    browser.evaluate("""(() => {
        const link = document.querySelector('link[href*="/static/css/app.css"]');
        const style = document.createElement('style');
        style.textContent = %s;
        link.before(style);
        link.disabled = true;
        selectWorkspace('bank');
        openNewQuestionEditor();
        return true;
    })()""" % json.dumps(source))
    browser.command("wait", "--fn", "!document.getElementById('editorSection').classList.contains('hidden')")
    content = "窗口兼容回归：保留未保存的题干 $x^2+1$。"
    browser.command("fill", "#editContent", content)

    for width, height in VIEWPORTS:
        browser.command("set", "viewport", str(width), str(height))
        for panel in ("content", "classification", "answer"):
            browser.evaluate("switchQuestionEditorPanel(%s); true" % json.dumps(panel))
            browser.settle()
            metrics = browser.evaluate("""(() => {
                const modal = document.getElementById('editorSection');
                const body = modal.querySelector('.question-editor-modal-body');
                const bounds = element => {
                    const r = element.getBoundingClientRect();
                    return {left:r.left, top:r.top, right:r.right, bottom:r.bottom,
                        width:r.width, height:r.height};
                };
                const controls = ['#editorTitle', '#saveQuestionBtn', '.question-editor-close']
                    .map(selector => {
                        const element = modal.querySelector(selector);
                        const rect = bounds(element);
                        const hit = document.elementFromPoint(
                            (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2);
                        return {selector, rect, reachable: !!hit && element.contains(hit)};
                    });
                const end = body.scrollHeight - body.clientHeight;
                body.scrollTop = end;
                return {surface:bounds(modal.querySelector('.question-editor-modal-surface')),
                    controls, bodyHeight:body.clientHeight, overflow:getComputedStyle(body).overflowY,
                    scrolledToEnd:Math.abs(body.scrollTop-end)<2,
                    input:document.getElementById('editContent').value};
            })()""")
            surface = metrics["surface"]
            assert surface["top"] >= -1 and surface["bottom"] <= height + 1, metrics
            assert surface["left"] >= -1 and surface["right"] <= width + 1, metrics
            assert metrics["bodyHeight"] > 0 and metrics["overflow"] == "auto", metrics
            assert metrics["scrolledToEnd"] and metrics["input"] == content, metrics
            for control in metrics["controls"]:
                rect = control["rect"]
                assert rect["width"] > 0 and rect["height"] > 0, metrics
                assert rect["top"] >= -1 and rect["bottom"] <= height + 1, metrics
                assert rect["left"] >= -1 and rect["right"] <= width + 1, metrics
                assert control["reachable"], metrics
        browser.command("screenshot", str(tmp_path / f"editor-{legacy_viewport}-{width}x{height}.png"))
