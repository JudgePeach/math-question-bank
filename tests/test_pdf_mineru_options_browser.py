"""Exercise the actual MinerU settings and PDF upload controls in a disposable UI."""

import os

import pymupdf as fitz
import pytest

from test_preview_browser import browser  # noqa: F401


pytestmark = pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1",
                                reason="opt-in isolated browser")


def test_mineru_settings_and_per_import_choices(browser, tmp_path):
    browser.evaluate("openSettingsModal(); true")
    browser.command("wait", "#settingsMineruToken")
    assert browser.evaluate(
        "document.querySelector('#settingsMineruToken').type === 'password' && "
        "document.querySelector('#settingsModal a[href=\"https://mineru.net/\"]') !== null"
    )
    browser.evaluate("closeSettingsModal(); true")
    browser.command("wait", "--fn", "document.getElementById('settingsModal').classList.contains('hidden')")

    source = tmp_path / "pdf-options.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 80), "1. Calculate 1+1.")
        pdf.save(source)
    browser.command("click", "#appNavImport")
    browser.command("upload", "#texFileInput", str(source))
    browser.command("wait", "--fn", "!document.getElementById('pdfPageRangeContainer').classList.contains('hidden')")
    browser.command("select", "#pdfOcrEngine", "mineru")
    browser.command("select", "#pdfWholePageOcrDpi", "300")
    browser.command("select", "#pdfParseProvider", "SILICONFLOW")
    browser.command("fill", "#pdfParseModel", "deepseek-ai/DeepSeek-V4.1-Flash")
    assert browser.evaluate("document.querySelector('#pdfMineruFigures').disabled")
    browser.evaluate(r"""(() => {
        const fallback = window.fetch;
        window.fetch = (url, init) => {
            if (url !== '/api/upload/pdf-task') return fallback(url, init);
            window.__pdfImportOptions = Object.fromEntries([...init.body.entries()].filter(([key]) => key !== 'file'));
            return Promise.resolve(new Response(JSON.stringify({detail:'Fixture captured upload'}), {
                status:400, headers:{'Content-Type':'application/json'},
            }));
        };
        return true;
    })()""")
    browser.command("fill", "#importPaperTitle", "测试试卷")
    browser.command("click", "#runParseBtn")
    browser.command("wait", "--fn", "!!window.__pdfImportOptions")
    assert browser.evaluate(r"""window.__pdfImportOptions.pdf_ocr_engine === 'mineru' &&
        window.__pdfImportOptions.whole_page_ocr_dpi === '300' &&
        window.__pdfImportOptions.pdf_parse_model === 'SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash' &&
        !('pdf_mineru_figures' in window.__pdfImportOptions)""")
