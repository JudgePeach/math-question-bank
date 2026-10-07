"""Local crop evidence must not increase either cloud prompt's input."""
from copy import deepcopy

from mathbank.prompts import build_pdf_layout_prompt
from mathbank.pdf_page_vision import _prompt_page_info


def test_private_vector_endpoint_evidence_stays_local_for_both_cloud_paths():
    candidate = {"id": "p1_vector_001", "bbox": [100, 100, 300, 300],
                 "type": "vector", "native_box_eligible": False}
    page = {"page_index": 0, "width": 600, "height": 800,
            "candidates": [candidate], "figure_slots": []}
    plain = build_pdf_layout_prompt("原题 $x=1$", page)
    richer = deepcopy(page)
    richer["candidates"][0]["_label_geometry"] = {
        "complete": True, "anchors": [{"point": [100, 100], "degree": 2}],
    }
    richer["candidates"][0]["_private_test_sentinel"] = "server-only-evidence"
    assert build_pdf_layout_prompt("原题 $x=1$", richer) == plain
    assert _prompt_page_info(richer) == _prompt_page_info(page)
    assert richer["candidates"][0]["_label_geometry"]["complete"] is True
    assert '"native_box_eligible": false' in plain
