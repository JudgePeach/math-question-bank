"""Small shared layout limits for paper previews and editable exports."""
import math

MAX_SOLUTION_SPACE_CM = 30.0


def normalize_solution_space_cm(value, default=0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if not math.isfinite(result):
        result = default
    return round(max(0.0, min(MAX_SOLUTION_SPACE_CM, result)), 1)


def item_solution_space_cm(item, question) -> float:
    value = item.get("solution_space")
    if value is None or value == "":
        value = question.get("solution_space", 0)
    return normalize_solution_space_cm(value)
