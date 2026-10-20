"""Real offline preview regressions for pipe tables and numeric prose."""

import json
import os
from pathlib import Path

import pytest

from test_preview_browser import browser


pytestmark = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_BROWSER") != "1",
    reason="opt-in real Markdown table layout",
)


def test_markdown_tables_keep_cells_math_and_wide_scroll(browser, tmp_path):
    from PIL import Image

    asset = browser.root / "static/uploads/markdown-table-fixture.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 40), "#64748b").save(asset)
    cases = {
        "references": "式(6.9.5)与式(6.9.6)相加可得 $2P(A)=1$。",
        "digits": "乙的卡片分别标有数字 2, 4, 6, 8. 两人进行比赛。",
        "distribution": "因此 $X$ 的分布列为\n\n| X | -1 | 0 | 1 |\n| --- | --- | --- | --- |\n| P | $(1-\\alpha)\\beta$ | $\\alpha\\beta+(1-\\alpha)(1-\\beta)$ | $\\alpha(1-\\beta)$ |\n\n后段正文。",
        "protected_cells": "| 条件 | 插图 |\n| --- | --- |\n| $P(A|B)$ | ![](/static/uploads/markdown-table-fixture.png) |\n| `a|b` | $\\{x|x>0\\}$ |",
        "linked_cells": "| [标签](https://example.test/a|b?cost=$5) | C |\n| --- | --- |\n| [x](https://example.test) | z |",
        "code_block": "`\n| A | B |\n| --- | --- |\n| $1$ | 2 |\n`",
        "subquestions": "说明。\n(1) 求值。\n(2) 证明。",
    }
    header = ["选手", "第一轮", "第二轮", "第三轮", "第四轮", "得分"] * 2
    rows = [header] + [["甲", "1", "3", "5", "7", "2分"] * 2 for _ in range(13)]
    cases["wide"] = "表 6-17\n\n" + "\n".join(
        ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * 12) + " |"]
        + ["| " + " | ".join(row) + " |" for row in rows[1:]]
    ) + "\n\n后段正文。"
    browser.evaluate("""(() => {
        selectWorkspace('import','导入中心');
        const wrapper=document.getElementById('parsedQuestionsWrapper');
        wrapper.classList.remove('hidden');
        document.getElementById('importPlaceholder').classList.add('hidden');
        const prior=document.getElementById('markdownTableFixtures');if(prior)prior.remove();
        const panel=document.createElement('section');panel.id='markdownTableFixtures';
        panel.className='p-4 bg-white text-base leading-relaxed space-y-4';
        wrapper.insertBefore(panel,wrapper.firstChild);
        const cases=""" + json.dumps(cases) + """;
        for(const [name,text] of Object.entries(cases)) {
            const host=document.createElement('div');host.id='md-table-'+name;
            panel.appendChild(host);renderQuestionPreviewContent(host,text);
        }
        return true;
    })()""")
    browser.command("wait", "--fn", "[...document.querySelectorAll('#markdownTableFixtures img')].every(img=>img.complete && img.naturalWidth===80)")
    for width in (1600, 390):
        browser.command("set", "viewport", str(width), "1100")
        browser.settle()
        result = browser.evaluate(r"""(() => {
            const hosts=[...document.querySelectorAll('#markdownTableFixtures > div')];
            const data=Object.fromEntries(hosts.map(host=>[host.id.replace('md-table-',''), {
                rows:host.querySelectorAll('tr').length,
                columns:[...host.querySelectorAll('tr')].map(row=>row.cells.length),
                errors:host.querySelectorAll('.katex-error').length,
                paragraphs:host.querySelectorAll('.mb-preview-paragraph-break').length,
                text:host.textContent,
                formulaSources:[...host.querySelectorAll('annotation[encoding="application/x-tex"]')].map(n=>n.textContent),
                imageInCell:!!host.querySelector('td img'),
            }]));
            const table=document.querySelector('#md-table-wide table');
            let scroller=table.parentElement;
            while(scroller&&!['auto','scroll'].includes(getComputedStyle(scroller).overflowX))scroller=scroller.parentElement;
            data.wide.scroller=!!scroller;
            if(scroller) {
                scroller.scrollLeft=scroller.scrollWidth;
                const last=table.rows[0].cells[11].getBoundingClientRect();
                const box=scroller.getBoundingClientRect();
                data.wide.lastColumnReachable=last.right<=box.right+2 && last.left>=box.left-2;
                data.wide.overflowContained=scroller.clientWidth<=document.documentElement.clientWidth;
            }
            return data;
        })()""")
        assert all(item["errors"] == 0 for item in result.values()), result
        assert result["references"]["paragraphs"] == 0
        assert "式(6.9.5)与式(6.9.6)" in result["references"]["text"]
        assert result["digits"]["paragraphs"] == 0
        assert "2, 4, 6, 8. 两人" in result["digits"]["text"]
        assert result["distribution"]["rows"] == 2
        assert result["distribution"]["columns"] == [4, 4]
        assert "---" not in result["distribution"]["text"]
        assert result["protected_cells"]["columns"] == [2, 2, 2]
        assert result["protected_cells"]["imageInCell"]
        assert "P(A|B)" in result["protected_cells"]["formulaSources"]
        assert r"\{x|x\gt 0\}" in result["protected_cells"]["formulaSources"]
        assert "a|b" in result["protected_cells"]["text"]
        assert result["linked_cells"]["columns"] == [2, 2]
        assert "a|b?cost=$5" in result["linked_cells"]["text"]
        assert not result["linked_cells"]["formulaSources"]
        assert result["code_block"]["rows"] == 0
        assert not result["code_block"]["formulaSources"]
        assert result["subquestions"]["paragraphs"] == 2
        assert result["wide"]["columns"] == [12] * 14
        assert result["wide"]["scroller"] and result["wide"]["lastColumnReachable"]
        assert result["wide"]["overflowContained"]
        browser.command("scrollintoview", "#markdownTableFixtures")
        browser.command("screenshot", str(tmp_path / f"markdown-tables-{width}.png"))
        (tmp_path / f"markdown-tables-{width}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))


def test_readonly_audit_copies_keep_all_table_dimensions(browser, tmp_path):
    fixture_path = os.environ.get("MATHBANK_FORMAT_AUDIT_FIXTURES")
    if not fixture_path:
        pytest.skip("optional copies from a prior read-only audit")
    fixtures = json.loads(Path(fixture_path).read_text())
    expected = {}
    for table in fixtures["markdown_tables"]:
        expected.setdefault(table["field_key"], []).append(
            [table["columns"]] * (table["body_rows"] + table["header_rows"])
        )
    sources = fixtures["markdown_table_field_fixtures"] + fixtures["injected_break_field_fixtures"]
    # Render each copied field in a detached host: the live application's safe
    # renderer and KaTeX run, while no question is saved or original asset read.
    result = browser.evaluate("""(() => {
        const sources=""" + json.dumps(sources) + """;
        return sources.map(item=>{
            const host=document.createElement('div');
            const html=renderQuestionPreviewContent(host,item.raw_field);
            return {
                fieldKey:item.field_key,
                tables:[...host.querySelectorAll('table')].map(table=>[...table.rows].map(row=>row.cells.length)),
                brokenNumericBoundary:/\\d[.．]\\s*<span class="mb-preview-paragraph-break"[^>]*><\\/span>\\d/.test(html)
                    || /\\d\\s*[,，]\\s*<span class="mb-preview-paragraph-break"[^>]*><\\/span>\\d\\./.test(html),
            };
        });
    })()""")
    for item in result:
        if item["fieldKey"] in expected:
            assert item["tables"] == expected[item["fieldKey"]], item
        assert not item["brokenNumericBoundary"], item
    (tmp_path / "all-audit-candidates.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
