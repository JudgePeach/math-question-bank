"""Test gates for retained PDF experiments, read without importing the app.

PDF_CLOUD_ONLY is the product's explicit feature boundary.  The local project,
v2 transcription and checkpoint adapters are retained historical components;
their integration tests are applicable only when that feature is enabled.
Pure component tests and the current upstream/cloud upload tests stay active.
"""
import ast
from pathlib import Path

import pytest


def pdf_cloud_only_enabled():
    source = Path(__file__).resolve().parents[1] / "main.py"
    assignments = [node for node in ast.parse(source.read_text(encoding="utf-8")).body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "PDF_CLOUD_ONLY"
                           for target in node.targets)]
    if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Constant):
        raise AssertionError("PDF feature tests require one explicit PDF_CLOUD_ONLY boolean")
    value = assignments[0].value.value
    if type(value) is not bool:
        raise AssertionError("PDF_CLOUD_ONLY must be an explicit boolean")
    return value


PDF_CLOUD_ONLY = pdf_cloud_only_enabled()
experimental_pdf_only = pytest.mark.skipif(
    PDF_CLOUD_ONLY,
    reason="PDF_CLOUD_ONLY=True disables the retained local-project/v2/checkpoint experiment",
)
