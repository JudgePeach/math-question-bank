"""Source comparison is diagnostic information, not an import permission gate.

The underlying comparison/verification evidence is retained verbatim. A failed
alignment is not proof that a question is wrong, and must not force users to
approve every question before importing it. This policy does not approve content
or relax upload, duplicate, database, or model-response validation.
"""
from __future__ import annotations


def make_source_review_advisory(questions: list[dict], diagnostics: dict) -> None:
    pending = 0
    for question in questions:
        review = question.get("source_review")
        if not isinstance(review, dict):
            continue
        if review.get("required") is True:
            pending += 1
        if review.get("required") is True or review.get("verified_by") == "vision":
            review["blocking"] = False
            review["disposition"] = "advisory"
    diagnostics.update(
        source_review_mode="advisory",
        source_review_notice_count=pending,
        source_review_blocking_count=0,
    )
