"""Strict parsing for visual verdicts; never repair incomplete evidence."""

import json
import re


def parse_review_response(raw: str):
    """Accept a complete JSON value, optionally inside a complete JSON fence.

    Unknown/duplicate question IDs are validated by each document verifier.
    Duplicate JSON fields and non-JSON constants are envelope ambiguity, so
    they must fail before any individual verdict can be accepted.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Empty verification response")
    value = raw.strip()
    fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", value, re.IGNORECASE)
    if fence:
        value = fence.group(1).strip()

    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = item
        return result

    def reject_constant(_value):
        raise ValueError("Non-JSON constant")

    return json.loads(value, object_pairs_hook=unique_object, parse_constant=reject_constant)
