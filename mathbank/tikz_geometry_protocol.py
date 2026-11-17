"""Shared validation for supported AI drawing plans."""

import re

_TOL = 1e-6
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,7}")
_LABEL_POSITIONS = {
    "above", "below", "left", "right", "above left", "above right",
    "below left", "below right",
}


class TikzGeometryError(ValueError):
    """A construction cannot be used, with a message safe for the workbench."""


def _require(condition, detail):
    if not condition:
        raise TikzGeometryError(detail)


def _number(value):
    _require(type(value) in (int, float) and abs(value) <= 10000, "几何数值无效或超出范围。")
    return float(value)


def _positive(value):
    value = _number(value)
    _require(value >= _TOL, "长度或半径须为正且不能退化。")
    return value


def _row(value, lengths):
    _require(isinstance(value, list) and len(value) in lengths, "几何计划条目的参数数量不符。")
    return value


def _point(points, name):
    _require(isinstance(name, str) and name in points, "几何计划引用了未定义的点。")
    return points[name]


def _unique_object(pairs):
    result = dict(pairs)
    _require(len(result) == len(pairs), "几何计划的 JSON 含有重复字段。")
    return result


def _evidence(value, sources):
    _require(isinstance(value, str) and 0 < len(value.strip()) <= 160
             and any(value.strip() in source for source in sources),
             "几何检查缺少题干或绘图要求中的原文依据。")
    return value.strip()


def _label_text(name):
    match = re.fullmatch(r"([A-Za-z]+)([0-9]+)", name)
    return f"{match[1]}_{{{match[2]}}}" if match else name


def _label_code(name, position):
    return rf"\fill ({name}) circle[radius=1pt] node[{position}=2pt] {{${_label_text(name)}$}};"
