"""Deterministic, conservative duplicate detection for math questions.

The helpers in this module are deliberately independent from FastAPI,
SQLAlchemy, and optional numerical libraries.  They build versioned derived
fingerprints which callers may persist, then compare only explicitly supplied
candidates.  A comparison is evidence for a teacher-facing review; it never
authorizes an automatic merge or deletion.

The canonicalizer removes presentation-only noise while retaining numbers,
variables, relation symbols, quantifiers, option order, and subquestion order.
That bias accepts an occasional missed duplicate in preference to collapsing a
mathematically different variant.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import re
import unicodedata
from typing import Iterable, Literal, Mapping, Sequence


FINGERPRINT_VERSION = 3
SIMHASH_BITS = 128
SIMHASH_BAND_COUNT = 8
SIMHASH_BAND_BITS = 16
TEXT_FRAGMENT_NGRAM_SIZE = 4
TEXT_MINHASH_BIN_COUNT = 32
TEXT_MINHASH_VALUES_PER_BAND = 4
TEXT_MINHASH_BAND_COUNT = 8
TEXT_MINHASH_EMPTY_VALUE = 0xFFFF
EMPTY_TEXT_BAND = "ffffffffffffffff"
DEFAULT_MAX_LSH_BUCKET_SIZE = 256
DEFAULT_MAX_CANDIDATES_PER_ITEM = 128

DuplicateClassification = Literal[
    "exact", "probable", "possible_variant", "none"
]


_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]\n]*\]\(\s*(?:<[^>\n]*>|[^\s)]+)"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)",
    re.IGNORECASE,
)
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_CHOICES_BEGIN_RE = re.compile(r"\\begin\s*\{\s*choices\*?\s*\}")
_ENV_TOKEN_RE = re.compile(r"\\(begin|end)\s*\{\s*([^{}]+?)\s*\}")
_OPTION_LABEL_RE = re.compile(
    r"^\s*(?:"
    r"[A-DＡ-Ｄ][.．、:：)）]|"
    r"[(（][A-DＡ-Ｄ][)）]"
    r")\s*",
    re.IGNORECASE,
)
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z@]+")
_LEXICAL_TOKEN_RE = re.compile(
    r"\\[A-Za-z@]+|\d+(?:\.\d+)?|\.\d+|[A-Za-z]+|"
    r"[\u0370-\u03ff]+|[\u3400-\u4dbf\u4e00-\u9fff]|[^\s]"
)
_CRITICAL_TOKEN_RE = re.compile(
    r"\\[A-Za-z@]+|"
    r"单调递增|单调递减|大于等于|小于等于|"
    r"有且仅有|至少一个|至多一个|"
    r"不存在|不超过|不少于|不大于|不小于|不等于|"
    r"正数|负数|非正|非负|为正|为负|"
    r"任意|任一|所有|每个|存在|至少|至多|"
    r"递增|递减|增加|减少|上升|下降|"
    r"大于|小于|等于|最大|最小|奇数|偶数|"
    r"[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億]+|"
    r"\d+(?:\.\d+)?|\.\d+|"
    r"[A-Za-z]+|[\u0370-\u03ff]+|"
    r"<=>|<->|=>|->|<=|>=|!=|==|"
    r"[≤≥≠≈≡∈∉∋⊂⊆⊄⊊⊃⊇⊅⊋∪∩∀∃∄⇒⇔→←↔∥⊥]|"
    r"[+\-×÷*/^_=<>|]|"
    r"[()\[\]{}（）【】,，;；:]"
)
_NUMBER_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?|\.\d+|"
    r"[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億]+)$"
)
_VARIABLE_RE = re.compile(r"^(?:[A-Za-z]+|[\u0370-\u03ff]+)$")
_CHINESE_NUMBER_CHARACTERS = frozenset(
    "零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億"
)

_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        **{chr(code): chr(code - 0xFEE0) for code in range(0xFF01, 0xFF5F)},
        "\u3000": " ",
        "，": ",",
        "；": ";",
        "：": ":",
        "．": ".",
    }
)

_LATEX_ALIASES = {
    "dfrac": "frac",
    "tfrac": "frac",
    "leqslant": "leq",
    "leq": "le",
    "geqslant": "geq",
    "geq": "ge",
    "ne": "neq",
    "varnothing": "emptyset",
}
_UNICODE_MATH_ALIASES = {
    "≤": r"\le ",
    "≥": r"\ge ",
    "≠": r"\neq ",
    "∈": r"\in ",
    "∉": r"\notin ",
    "∀": r"\forall ",
    "∃": r"\exists ",
    "∄": r"\nexists ",
    "∅": r"\emptyset ",
    "∞": r"\infty ",
    "×": r"\times ",
    "÷": r"\div ",
    "±": r"\pm ",
    "∥": r"\parallel ",
    "⊥": r"\perp ",
}
_PRESENTATION_COMMANDS = frozenset(
    {
        "displaystyle",
        "textstyle",
        "scriptstyle",
        "scriptscriptstyle",
        "limits",
        "nolimits",
        "text",
        "textrm",
        "textnormal",
        "mathrm",
        "operatorname",
    }
)
_RELATION_TOKENS = frozenset(
    {
        "<",
        ">",
        "<=",
        ">=",
        "!=",
        "==",
        "=",
        "≤",
        "≥",
        "≠",
        "≈",
        "≡",
        "∈",
        "∉",
        "∋",
        "⊂",
        "⊆",
        "⊄",
        "⊊",
        "⊃",
        "⊇",
        "⊅",
        "⊋",
        r"\le",
        r"\ge",
        r"\neq",
        r"\in",
        r"\notin",
        r"\subset",
        r"\subseteq",
        r"\supset",
        r"\supseteq",
        r"\approx",
        r"\equiv",
        "不超过",
        "不少于",
        "不大于",
        "不小于",
        "大于等于",
        "小于等于",
        "不等于",
        "大于",
        "小于",
        "等于",
        "正数",
        "负数",
        "非正",
        "非负",
        "为正",
        "为负",
    }
)
_QUANTIFIER_TOKENS = frozenset(
    {
        "∀",
        "∃",
        "∄",
        "任意",
        "任一",
        "所有",
        "每个",
        "存在",
        "不存在",
        "有且仅有",
        "至少一个",
        "至多一个",
        "至少",
        "至多",
        r"\forall",
        r"\exists",
        r"\nexists",
    }
)
_OPERATOR_TOKENS = frozenset(
    {
        "+",
        "-",
        "×",
        "÷",
        "*",
        "/",
        "^",
        "_",
        "|",
        r"\times",
        r"\div",
        r"\pm",
        r"\mp",
        r"\cup",
        r"\cap",
        "单调递增",
        "单调递减",
        "递增",
        "递减",
        "增加",
        "减少",
        "上升",
        "下降",
        "最大",
        "最小",
        "奇数",
        "偶数",
    }
)
_GREEK_COMMANDS = frozenset(
    {
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "pi",
        "varpi",
        "rho",
        "varrho",
        "sigma",
        "varsigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
        "Gamma",
        "Delta",
        "Theta",
        "Lambda",
        "Xi",
        "Pi",
        "Sigma",
        "Upsilon",
        "Phi",
        "Psi",
        "Omega",
    }
)


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _stable_hash(payload)


def _as_signature_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Iterable[object] = (value,)
    elif isinstance(value, Iterable):
        values = value
    else:
        raise TypeError("asset signatures must be a string or iterable of strings")
    # Signatures are opaque upstream values.  Hex digests should be normalized
    # by their producer; changing case here could corrupt a case-sensitive TikZ
    # or future structured signature.
    return tuple(str(item or "").strip() for item in values)


@dataclass(frozen=True)
class QuestionDuplicateInput:
    """Question fields which may affect duplicate review evidence.

    ``visible_image_signatures`` and ``tikz_signatures`` are computed by an
    upstream asset worker.  This module treats them as opaque, ordered exact
    signatures and never reads files from disk.
    """

    content: str
    answer_markdown: str = ""
    question_type: str = ""
    visible_image_signatures: tuple[str, ...] = ()
    tikz_signatures: tuple[str, ...] = ()
    answer_asset_signatures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", str(self.content or ""))
        object.__setattr__(
            self, "answer_markdown", str(self.answer_markdown or "")
        )
        object.__setattr__(self, "question_type", str(self.question_type or ""))
        object.__setattr__(
            self,
            "visible_image_signatures",
            _as_signature_tuple(self.visible_image_signatures),
        )
        object.__setattr__(
            self, "tikz_signatures", _as_signature_tuple(self.tikz_signatures)
        )
        object.__setattr__(
            self,
            "answer_asset_signatures",
            _as_signature_tuple(self.answer_asset_signatures),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "QuestionDuplicateInput":
        return cls(
            content=str(value.get("content") or ""),
            answer_markdown=str(value.get("answer_markdown") or ""),
            question_type=str(value.get("question_type") or ""),
            visible_image_signatures=_as_signature_tuple(
                value.get("visible_image_signatures")
            ),
            tikz_signatures=_as_signature_tuple(value.get("tikz_signatures")),
            answer_asset_signatures=_as_signature_tuple(
                value.get("answer_asset_signatures")
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "answer_markdown": self.answer_markdown,
            "question_type": self.question_type,
            "visible_image_signatures": list(self.visible_image_signatures),
            "tikz_signatures": list(self.tikz_signatures),
            "answer_asset_signatures": list(self.answer_asset_signatures),
        }


@dataclass(frozen=True)
class QuestionFingerprint:
    """Serializable, versioned duplicate-detection data for one question."""

    fingerprint_version: int
    exact_hash: str
    content_revision_hash: str
    raw_content_hash: str
    answer_hash: str
    critical_math_hash: str
    simhash_hex: str
    bands: tuple[int, ...]
    text_bands: tuple[str, ...]
    token_count: int
    choice_count: int
    subquestion_count: int
    figure_count: int
    canonical_text: str
    canonical_stem: str
    ordered_choices: tuple[str, ...]
    ordered_choices_hash: str
    unordered_choices_hash: str
    critical_tokens: tuple[str, ...]
    question_type: str
    visible_image_signatures: tuple[str, ...]
    tikz_signatures: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.fingerprint_version < 1:
            raise ValueError("fingerprint_version must be positive")
        for field_name in (
            "exact_hash",
            "content_revision_hash",
            "raw_content_hash",
            "answer_hash",
            "critical_math_hash",
            "ordered_choices_hash",
            "unordered_choices_hash",
        ):
            digest = getattr(self, field_name)
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        if not re.fullmatch(r"[0-9a-f]{32}", self.simhash_hex):
            raise ValueError("simhash_hex must contain exactly 128 bits")
        if len(self.bands) != SIMHASH_BAND_COUNT:
            raise ValueError("bands must contain eight 16-bit values")
        if any(not 0 <= int(value) <= 0xFFFF for value in self.bands):
            raise ValueError("each SimHash band must be between 0 and 65535")
        if len(self.text_bands) != TEXT_MINHASH_BAND_COUNT:
            raise ValueError("text_bands must contain eight fragment bands")
        if any(
            not re.fullmatch(r"[0-9a-f]{16}", value)
            for value in self.text_bands
        ):
            raise ValueError(
                "each text fragment band must be 16 lowercase hex characters"
            )

    @property
    def simhash_int(self) -> int:
        return int(self.simhash_hex, 16)

    @property
    def snapshot_hash(self) -> str:
        """Alias used by save-time stale-check protocols."""

        return self.content_revision_hash

    @property
    def critical_math_tokens(self) -> tuple[str, ...]:
        """Explicit alias for storage schemas using the longer field name."""

        return self.critical_tokens

    def to_dict(self) -> dict[str, object]:
        return {
            "fingerprint_version": self.fingerprint_version,
            "exact_hash": self.exact_hash,
            "content_revision_hash": self.content_revision_hash,
            "raw_content_hash": self.raw_content_hash,
            "answer_hash": self.answer_hash,
            "critical_math_hash": self.critical_math_hash,
            "simhash_hex": self.simhash_hex,
            "bands": list(self.bands),
            "text_bands": list(self.text_bands),
            "token_count": self.token_count,
            "choice_count": self.choice_count,
            "subquestion_count": self.subquestion_count,
            "figure_count": self.figure_count,
            "canonical_text": self.canonical_text,
            "canonical_stem": self.canonical_stem,
            "ordered_choices": list(self.ordered_choices),
            "ordered_choices_hash": self.ordered_choices_hash,
            "unordered_choices_hash": self.unordered_choices_hash,
            "critical_tokens": list(self.critical_tokens),
            "question_type": self.question_type,
            "visible_image_signatures": list(self.visible_image_signatures),
            "tikz_signatures": list(self.tikz_signatures),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "QuestionFingerprint":
        return cls(
            fingerprint_version=int(
                value.get("fingerprint_version", FINGERPRINT_VERSION)
            ),
            exact_hash=str(value["exact_hash"]),
            content_revision_hash=str(value["content_revision_hash"]),
            raw_content_hash=str(value["raw_content_hash"]),
            answer_hash=str(value["answer_hash"]),
            critical_math_hash=str(value["critical_math_hash"]),
            simhash_hex=str(value["simhash_hex"]),
            bands=tuple(int(item) for item in value.get("bands", ())),
            text_bands=tuple(
                str(item) for item in value.get("text_bands", ())
            ),
            token_count=int(value.get("token_count", 0)),
            choice_count=int(value.get("choice_count", 0)),
            subquestion_count=int(value.get("subquestion_count", 0)),
            figure_count=int(value.get("figure_count", 0)),
            canonical_text=str(value.get("canonical_text") or ""),
            canonical_stem=str(value.get("canonical_stem") or ""),
            ordered_choices=tuple(
                str(item) for item in value.get("ordered_choices", ())
            ),
            ordered_choices_hash=str(value["ordered_choices_hash"]),
            unordered_choices_hash=str(value["unordered_choices_hash"]),
            critical_tokens=tuple(
                str(item) for item in value.get("critical_tokens", ())
            ),
            question_type=str(value.get("question_type") or ""),
            visible_image_signatures=_as_signature_tuple(
                value.get("visible_image_signatures")
            ),
            tikz_signatures=_as_signature_tuple(value.get("tikz_signatures")),
        )


@dataclass(frozen=True)
class DuplicateComparison:
    """Explainable review result.  ``auto_merge_allowed`` is always false."""

    classification: DuplicateClassification
    score: float
    reasons: tuple[str, ...]
    needs_visual_review: bool
    auto_merge_allowed: bool = False

    def __post_init__(self) -> None:
        if self.auto_merge_allowed:
            raise ValueError("math-question duplicate results may never auto-merge")
        if self.classification not in {
            "exact",
            "probable",
            "possible_variant",
            "none",
        }:
            raise ValueError("unknown duplicate classification")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between zero and one")

    @property
    def requires_human_review(self) -> bool:
        return self.classification != "none"

    @property
    def level(self) -> DuplicateClassification:
        """API-friendly alias for ``classification``."""

        return self.classification

    def to_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "level": self.classification,
            "score": self.score,
            "reasons": list(self.reasons),
            "needs_visual_review": self.needs_visual_review,
            "requires_human_review": self.requires_human_review,
            "auto_merge_allowed": False,
        }


@dataclass(frozen=True)
class RankedDuplicateCandidate:
    candidate_index: int
    comparison: DuplicateComparison


@dataclass(frozen=True)
class BatchPairComparison:
    left_index: int
    right_index: int
    comparison: DuplicateComparison


@dataclass(frozen=True)
class BatchDuplicateGroup:
    member_indexes: tuple[int, ...]
    strongest_classification: DuplicateClassification
    comparisons: tuple[BatchPairComparison, ...]


@dataclass(frozen=True)
class BatchDuplicateScan:
    groups: tuple[BatchDuplicateGroup, ...]
    candidate_pair_count: int
    compared_pair_count: int
    exact_group_count: int
    truncated_bucket_count: int
    dropped_candidate_pair_count: int

    @property
    def index_complete(self) -> bool:
        return (
            self.truncated_bucket_count == 0
            and self.dropped_candidate_pair_count == 0
        )


def _normalize_revision_text(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_escaped(value: str, index: int) -> bool:
    slash_count = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        slash_count += 1
        cursor -= 1
    return slash_count % 2 == 1


def _replace_images(value: str) -> tuple[str, int]:
    count = 0

    def replacement(_: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f" [[image:{count}]] "

    replaced = _MARKDOWN_IMAGE_RE.sub(replacement, value)
    replaced = _HTML_IMAGE_RE.sub(replacement, replaced)
    return replaced, count


def _find_choices_end(value: str, begin: re.Match[str]) -> int | None:
    depth = 1
    for token in _ENV_TOKEN_RE.finditer(value, begin.end()):
        environment = token.group(2).strip().lower()
        if environment not in {"choices", "choices*"}:
            continue
        if token.group(1) == "begin":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return token.end()
    return None


def _consume_optional_label(value: str, cursor: int) -> int:
    while cursor < len(value) and value[cursor].isspace():
        cursor += 1
    if cursor >= len(value) or value[cursor] != "[":
        return cursor
    depth = 0
    for index in range(cursor, len(value)):
        char = value[index]
        if char == "[" and not _is_escaped(value, index):
            depth += 1
        elif char == "]" and not _is_escaped(value, index):
            depth -= 1
            if depth == 0:
                return index + 1
    return cursor


def _split_choice_items(body: str) -> tuple[str, ...]:
    item_positions: list[tuple[int, int]] = []
    brace_depth = 0
    nested_environment_depth = 0
    cursor = 0
    while cursor < len(body):
        char = body[cursor]
        if char == "\\":
            environment = _ENV_TOKEN_RE.match(body, cursor)
            if environment:
                if environment.group(1) == "begin":
                    nested_environment_depth += 1
                else:
                    nested_environment_depth = max(0, nested_environment_depth - 1)
                cursor = environment.end()
                continue
            item = re.match(r"\\item(?![A-Za-z@])", body[cursor:])
            if item and brace_depth == 0 and nested_environment_depth == 0:
                item_start = cursor
                content_start = _consume_optional_label(
                    body, cursor + item.end()
                )
                item_positions.append((item_start, content_start))
                cursor = content_start
                continue
            cursor += 2
            continue
        if char == "{" and not _is_escaped(body, cursor):
            brace_depth += 1
        elif char == "}" and not _is_escaped(body, cursor):
            brace_depth = max(0, brace_depth - 1)
        cursor += 1

    choices: list[str] = []
    for index, (_, content_start) in enumerate(item_positions):
        content_end = (
            item_positions[index + 1][0]
            if index + 1 < len(item_positions)
            else len(body)
        )
        choice = _OPTION_LABEL_RE.sub("", body[content_start:content_end]).strip()
        if choice:
            choices.append(choice)
    return tuple(choices)


def extract_choices(value: str) -> tuple[str, tuple[str, ...]]:
    """Extract top-level ``choices`` items while preserving their order.

    Malformed environments are retained as ordinary content instead of being
    guessed at.  Nested ``\\item`` commands do not become top-level options.
    """

    text = str(value or "")
    choices: list[str] = []
    stem_parts: list[str] = []
    cursor = 0
    while True:
        begin = _CHOICES_BEGIN_RE.search(text, cursor)
        if not begin:
            stem_parts.append(text[cursor:])
            break
        end = _find_choices_end(text, begin)
        if end is None:
            stem_parts.append(text[cursor:])
            break
        closing = list(_ENV_TOKEN_RE.finditer(text, begin.end(), end))[-1]
        body = text[begin.end() : closing.start()]
        extracted = _split_choice_items(body)
        if not extracted:
            stem_parts.append(text[cursor:end])
            cursor = end
            continue
        stem_parts.append(text[cursor : begin.start()])
        stem_parts.append(" ")
        choices.extend(extracted)
        cursor = end
    return "".join(stem_parts), tuple(choices)


def normalize_math_text(value: str) -> str:
    """Apply conservative presentation-only normalization to math text."""

    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.translate(_FULLWIDTH_TRANSLATION)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH_RE.sub("", text)
    # Question content is mixed Markdown/LaTeX, not a TeX source file.  A raw
    # percent sign commonly means a percentage and must never comment out the
    # rest of a problem statement.
    text = text.replace(r"\%", "%")

    for source, target in _LATEX_ALIASES.items():
        text = re.sub(
            rf"\\{re.escape(source)}(?![A-Za-z@])", rf"\\{target}", text
        )
    for source, target in _UNICODE_MATH_ALIASES.items():
        text = text.replace(source, target)

    text = re.sub(r"\\(?:left|right)(?![A-Za-z@])", "", text)
    text = re.sub(
        r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle)(?![A-Za-z@])",
        "",
        text,
    )
    text = re.sub(
        r"\\(?:quad|qquad|enspace|thinspace|medspace|thickspace)(?![A-Za-z@])",
        " ",
        text,
    )
    text = re.sub(r"\\[,;:!]", " ", text)
    text = text.replace(r"\(", " ").replace(r"\)", " ")
    text = text.replace(r"\[", " ").replace(r"\]", " ")
    text = text.replace("$", " ").replace("~", " ")
    text = re.sub(r"\s+", " ", text).strip()

    # These removals are limited to unambiguous structural adjacency.  Spaces
    # between variables remain intact, so ``x y`` never collapses to ``xy``.
    text = re.sub(r"(\\[A-Za-z@]+)\s+\{", r"\1{", text)
    text = re.sub(r"\s*([{}\[\](),;:+*/^_=<>|])\s*", r"\1", text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            r"([\u3400-\u4dbf\u4e00-\u9fff])\s+"
            r"(?=[\u3400-\u4dbf\u4e00-\u9fff])",
            r"\1",
            text,
        )
    return text.strip()


def _build_canonical_content(value: str) -> tuple[str, str, tuple[str, ...], int]:
    with_images, markdown_figure_count = _replace_images(value)
    raw_stem, raw_choices = extract_choices(with_images)
    stem = normalize_math_text(raw_stem)
    choices = tuple(normalize_math_text(choice) for choice in raw_choices)
    if choices:
        choice_payload = "".join(
            f"[[choice:{index}]]{choice}"
            for index, choice in enumerate(choices, start=1)
        )
        canonical_text = f"{stem}{choice_payload}"
    else:
        canonical_text = stem
    return canonical_text, stem, choices, markdown_figure_count


def extract_critical_math_tokens(
    canonical_stem: str, ordered_choices: Sequence[str] = ()
) -> tuple[str, ...]:
    """Extract ordered tokens whose change can alter mathematical meaning."""

    tokens: list[str] = []

    def append_from(text: str) -> None:
        # Figure ordinals are structural placeholders, not mathematical
        # numbers.  Figure count and asset evidence are compared separately.
        text = re.sub(r"\[\[image:\d+\]\]", " ", text)
        for match in _CRITICAL_TOKEN_RE.finditer(text):
            token = match.group(0)
            if token.startswith("\\") and token[1:] in _PRESENTATION_COMMANDS:
                continue
            tokens.append(token)

    append_from(canonical_stem)
    for index, choice in enumerate(ordered_choices, start=1):
        tokens.append(f"<choice:{index}>")
        append_from(choice)
    return tuple(tokens)


def _subquestion_count(value: str) -> int:
    matches = re.findall(
        r"(?:^|\s)(?:[(（](?:[1-9]|10|[ivxIVX]+)[)）]|[①②③④⑤⑥⑦⑧⑨⑩])",
        value,
    )
    return len(matches)


def _simhash_features(
    canonical_text: str, critical_tokens: Sequence[str]
) -> Counter[str]:
    features: Counter[str] = Counter()
    lexical = _LEXICAL_TOKEN_RE.findall(canonical_text)
    for token in lexical:
        features[f"token:{token}"] += 1
    compact = re.sub(r"\s+", "", canonical_text)
    if compact:
        width = min(3, len(compact))
        for index in range(0, len(compact) - width + 1):
            features[f"char:{compact[index:index + width]}"] += 1
    for token in critical_tokens:
        features[f"math:{token}"] += 2
    return features


def _text_fragment_skeleton_token(token: str) -> str:
    """Normalize numeric values for recall only, never for final comparison."""

    if _NUMBER_RE.fullmatch(token):
        return "<NUM>"
    if token and all(char in _CHINESE_NUMBER_CHARACTERS for char in token):
        return "<NUM>"
    return token


def _text_fragment_features(canonical_text: str) -> set[str]:
    """Return literal and numeric-skeleton lexical token 4-gram features."""

    literal_tokens = _LEXICAL_TOKEN_RE.findall(canonical_text)
    if len(literal_tokens) < TEXT_FRAGMENT_NGRAM_SIZE:
        return set()
    skeleton_tokens = [
        _text_fragment_skeleton_token(token) for token in literal_tokens
    ]
    features: set[str] = set()
    for namespace, tokens in (
        ("literal", literal_tokens),
        ("skeleton", skeleton_tokens),
    ):
        for index in range(len(tokens) - TEXT_FRAGMENT_NGRAM_SIZE + 1):
            gram = "\x1f".join(
                tokens[index : index + TEXT_FRAGMENT_NGRAM_SIZE]
            )
            features.add(f"{namespace}\x1f{gram}")
    return features


def compute_text_fragment_bands(canonical_text: str) -> tuple[str, ...]:
    """Build eight 64-bit OPH bands from lexical token 4-grams.

    Every unique literal or numeric-skeleton feature is SHA-256 hashed exactly
    once.  Five digest bits select one of 32 bins and the lowest 16-bit value
    wins inside that bin.  Four adjacent minima form one 16-hex-character band.
    Empty bins use ``ffff``; a fully empty band is not indexed by batch recall.

    Numeric skeletons only widen candidate recall.  Exact hashes, critical math
    tokens, comparison scores, and final classifications retain original values.
    """

    minima = [TEXT_MINHASH_EMPTY_VALUE] * TEXT_MINHASH_BIN_COUNT
    for feature in _text_fragment_features(str(canonical_text or "")):
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        bin_no = digest[0] & (TEXT_MINHASH_BIN_COUNT - 1)
        value = min(int.from_bytes(digest[1:3], "big"), 0xFFFE)
        if value < minima[bin_no]:
            minima[bin_no] = value
    return tuple(
        "".join(
            f"{value:04x}"
            for value in minima[
                start : start + TEXT_MINHASH_VALUES_PER_BAND
            ]
        )
        for start in range(
            0, TEXT_MINHASH_BIN_COUNT, TEXT_MINHASH_VALUES_PER_BAND
        )
    )


def compute_simhash_128(features: Mapping[str, int] | Iterable[str]) -> int:
    """Return a deterministic 128-bit SimHash from weighted features."""

    if isinstance(features, Mapping):
        weighted_features = sorted(
            (str(feature), int(weight))
            for feature, weight in features.items()
            if int(weight) > 0
        )
    else:
        weighted_features = sorted(Counter(str(item) for item in features).items())
    if not weighted_features:
        return 0

    vector = [0] * SIMHASH_BITS
    for feature, weight in weighted_features:
        digest = int.from_bytes(
            hashlib.sha256(feature.encode("utf-8")).digest()[:16], "big"
        )
        for bit in range(SIMHASH_BITS):
            vector[bit] += weight if digest & (1 << bit) else -weight

    result = 0
    for bit, value in enumerate(vector):
        if value > 0:
            result |= 1 << bit
    return result


def simhash_bands(simhash: int | str) -> tuple[int, ...]:
    """Split a 128-bit SimHash into eight deterministic high-to-low bands."""

    value = int(simhash, 16) if isinstance(simhash, str) else int(simhash)
    if not 0 <= value < 1 << SIMHASH_BITS:
        raise ValueError("SimHash must fit in 128 bits")
    return tuple(
        (value >> (SIMHASH_BAND_BITS * (SIMHASH_BAND_COUNT - 1 - index)))
        & 0xFFFF
        for index in range(SIMHASH_BAND_COUNT)
    )


def build_question_fingerprint(
    question: QuestionDuplicateInput | Mapping[str, object],
) -> QuestionFingerprint:
    """Build a deterministic fingerprint without performing I/O."""

    source = (
        question
        if isinstance(question, QuestionDuplicateInput)
        else QuestionDuplicateInput.from_mapping(question)
    )
    canonical_text, stem, choices, markdown_figure_count = _build_canonical_content(
        source.content
    )
    critical_tokens = extract_critical_math_tokens(stem, choices)
    features = _simhash_features(canonical_text, critical_tokens)
    simhash = compute_simhash_128(features)
    text_bands = compute_text_fragment_bands(canonical_text)
    answer_with_placeholders, _answer_figure_count = _replace_images(
        source.answer_markdown
    )
    canonical_answer = normalize_math_text(answer_with_placeholders)
    answer_hash = _stable_json_hash(
        {
            "text": canonical_answer,
            "assets": source.answer_asset_signatures,
        }
    )
    visible_signature_count = len(source.visible_image_signatures) + len(
        source.tikz_signatures
    )
    figure_count = max(markdown_figure_count, visible_signature_count)
    revision_payload = {
        # Persisted asset promotion rewrites temporary image URLs in the raw
        # Markdown.  The snapshot therefore uses canonical placeholders plus
        # decoded image/TikZ signatures, keeping preflight and save equivalent
        # without weakening semantic stale detection.
        "content": canonical_text,
        "answer_hash": answer_hash,
        "question_type": source.question_type,
        "visible_image_signatures": source.visible_image_signatures,
        "tikz_signatures": source.tikz_signatures,
    }
    lexical_token_count = len(_LEXICAL_TOKEN_RE.findall(canonical_text))
    return QuestionFingerprint(
        fingerprint_version=FINGERPRINT_VERSION,
        exact_hash=_stable_json_hash({"stem": stem, "choices": choices}),
        content_revision_hash=_stable_json_hash(revision_payload),
        raw_content_hash=_stable_hash(_normalize_revision_text(source.content)),
        answer_hash=answer_hash,
        critical_math_hash=_stable_json_hash(critical_tokens),
        simhash_hex=f"{simhash:032x}",
        bands=simhash_bands(simhash),
        text_bands=text_bands,
        token_count=lexical_token_count,
        choice_count=len(choices),
        subquestion_count=_subquestion_count(stem),
        figure_count=figure_count,
        canonical_text=canonical_text,
        canonical_stem=stem,
        ordered_choices=choices,
        ordered_choices_hash=_stable_json_hash(choices),
        unordered_choices_hash=_stable_json_hash(sorted(choices)),
        critical_tokens=critical_tokens,
        question_type=source.question_type,
        visible_image_signatures=source.visible_image_signatures,
        tikz_signatures=source.tikz_signatures,
    )


def _char_ngrams(value: str, width: int = 3) -> Counter[str]:
    compact = re.sub(r"\s+", "", value)
    if not compact:
        return Counter()
    actual_width = min(width, len(compact))
    return Counter(
        compact[index : index + actual_width]
        for index in range(len(compact) - actual_width + 1)
    )


def _multiset_jaccard(left: Counter[str], right: Counter[str]) -> float:
    if not left and not right:
        return 0.0
    keys = set(left) | set(right)
    intersection = sum(min(left[key], right[key]) for key in keys)
    union = sum(max(left[key], right[key]) for key in keys)
    return intersection / union if union else 0.0


def simhash_similarity(left: int | str, right: int | str) -> float:
    left_value = int(left, 16) if isinstance(left, str) else int(left)
    right_value = int(right, 16) if isinstance(right, str) else int(right)
    distance = (left_value ^ right_value).bit_count()
    return 1.0 - distance / SIMHASH_BITS


def _similarity_score(
    left: QuestionFingerprint, right: QuestionFingerprint
) -> float:
    sequence_score = SequenceMatcher(
        None, left.canonical_text, right.canonical_text, autojunk=False
    ).ratio()
    ngram_score = _multiset_jaccard(
        _char_ngrams(left.canonical_text), _char_ngrams(right.canonical_text)
    )
    if left.critical_tokens or right.critical_tokens:
        math_score = SequenceMatcher(
            None, left.critical_tokens, right.critical_tokens, autojunk=False
        ).ratio()
    else:
        math_score = 0.0
    structure_parts = (
        left.choice_count == right.choice_count,
        left.subquestion_count == right.subquestion_count,
        left.figure_count == right.figure_count,
    )
    structure_score = sum(structure_parts) / len(structure_parts)
    score = (
        0.35 * sequence_score
        + 0.25 * ngram_score
        + 0.30 * math_score
        + 0.10 * structure_score
    )
    return max(0.0, min(1.0, score))


def _critical_category(tokens: Sequence[str], category: str) -> tuple[str, ...]:
    if category == "number":
        return tuple(token for token in tokens if _NUMBER_RE.fullmatch(token))
    if category == "relation":
        return tuple(token for token in tokens if token in _RELATION_TOKENS)
    if category == "quantifier":
        return tuple(token for token in tokens if token in _QUANTIFIER_TOKENS)
    if category == "operator":
        return tuple(token for token in tokens if token in _OPERATOR_TOKENS)
    if category == "variable":
        result: list[str] = []
        for token in tokens:
            if _VARIABLE_RE.fullmatch(token):
                result.append(token)
            elif token.startswith("\\") and token[1:] in _GREEK_COMMANDS:
                result.append(token)
        return tuple(result)
    raise ValueError(f"unknown critical token category: {category}")


def _critical_change_reasons(
    left: QuestionFingerprint, right: QuestionFingerprint
) -> tuple[str, ...]:
    reasons: list[str] = []
    categories = (
        ("number", "NUMBER_CHANGED"),
        ("relation", "RELATION_CHANGED"),
        ("quantifier", "QUANTIFIER_CHANGED"),
        ("variable", "VARIABLE_CHANGED"),
        ("operator", "OPERATOR_CHANGED"),
    )
    for category, reason in categories:
        if _critical_category(left.critical_tokens, category) != _critical_category(
            right.critical_tokens, category
        ):
            reasons.append(reason)
    if left.critical_math_hash != right.critical_math_hash and not reasons:
        reasons.append("MATH_STRUCTURE_CHANGED")
    return tuple(reasons)


def _critical_shape(tokens: Sequence[str]) -> tuple[str, ...]:
    """Replace critical values with categories while preserving structure."""

    shaped: list[str] = []
    for token in tokens:
        if _NUMBER_RE.fullmatch(token):
            shaped.append("<number>")
        elif token in _RELATION_TOKENS:
            shaped.append("<relation>")
        elif token in _QUANTIFIER_TOKENS:
            shaped.append("<quantifier>")
        elif token in _OPERATOR_TOKENS:
            shaped.append("<operator>")
        elif _VARIABLE_RE.fullmatch(token) or (
            token.startswith("\\") and token[1:] in _GREEK_COMMANDS
        ):
            shaped.append("<variable>")
        else:
            shaped.append(token)
    return tuple(shaped)


def _visual_evidence(
    left: QuestionFingerprint, right: QuestionFingerprint
) -> tuple[bool, bool, tuple[str, ...]]:
    """Return ``(exact, needs_review, reasons)`` for visible assets."""

    if left.figure_count != right.figure_count:
        reason = (
            "FIGURE_MISSING"
            if 0 in {left.figure_count, right.figure_count}
            else "FIGURE_COUNT_DIFF"
        )
        return False, True, (reason,)
    if left.figure_count == 0:
        return True, False, ()

    left_signatures = left.visible_image_signatures + left.tikz_signatures
    right_signatures = right.visible_image_signatures + right.tikz_signatures
    left_complete = (
        len(left_signatures) >= left.figure_count
        and all(left_signatures[: left.figure_count])
    )
    right_complete = (
        len(right_signatures) >= right.figure_count
        and all(right_signatures[: right.figure_count])
    )
    if not left_complete or not right_complete:
        return False, True, ("VISUAL_SIGNATURE_PENDING",)

    reasons: list[str] = []
    if left.visible_image_signatures != right.visible_image_signatures:
        reasons.append("FIGURE_DIFF")
    if left.tikz_signatures != right.tikz_signatures:
        reasons.append("TIKZ_DIFF")
    if reasons:
        return False, True, tuple(reasons)
    return True, False, ("FIGURE_EXACT",)


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def compare_question_fingerprints(
    left: QuestionFingerprint, right: QuestionFingerprint
) -> DuplicateComparison:
    """Compare two fingerprints conservatively and explain the result."""

    if left.fingerprint_version != right.fingerprint_version:
        return DuplicateComparison(
            classification="none",
            score=0.0,
            reasons=("FINGERPRINT_VERSION_MISMATCH",),
            needs_visual_review=False,
        )

    reasons: list[str] = []
    visual_exact, needs_visual_review, visual_reasons = _visual_evidence(left, right)
    for reason in visual_reasons:
        _append_reason(reasons, reason)
    if left.question_type != right.question_type:
        _append_reason(reasons, "TYPE_DIFF")
    if left.answer_hash != right.answer_hash:
        _append_reason(reasons, "ANSWER_DIFF")

    exact_text = left.exact_hash == right.exact_hash
    choices_reordered = (
        left.choice_count > 0
        and left.choice_count == right.choice_count
        and left.canonical_stem == right.canonical_stem
        and left.unordered_choices_hash == right.unordered_choices_hash
        and left.ordered_choices_hash != right.ordered_choices_hash
    )

    if exact_text:
        _append_reason(reasons, "EXACT_TEXT")
        if left.raw_content_hash != right.raw_content_hash:
            _append_reason(reasons, "SAFE_NORMALIZATION_ONLY")
        classification: DuplicateClassification = (
            "exact" if visual_exact else "probable"
        )
        return DuplicateComparison(
            classification=classification,
            score=1.0,
            reasons=tuple(reasons),
            needs_visual_review=needs_visual_review,
        )

    score = _similarity_score(left, right)
    if choices_reordered:
        _append_reason(reasons, "OPTION_ORDER_CHANGED")
        return DuplicateComparison(
            classification="probable",
            score=round(max(score, 0.9), 6),
            reasons=tuple(reasons),
            needs_visual_review=needs_visual_review,
        )

    if left.choice_count != right.choice_count:
        _append_reason(reasons, "OPTION_COUNT_DIFF")

    critical_matches = left.critical_math_hash == right.critical_math_hash
    if critical_matches:
        _append_reason(reasons, "CRITICAL_MATH_MATCH")
    else:
        for reason in _critical_change_reasons(left, right):
            _append_reason(reasons, reason)

    substantive_length = min(
        len(re.sub(r"\s+", "", left.canonical_text)),
        len(re.sub(r"\s+", "", right.canonical_text)),
    )
    probable_threshold = 0.94 if substantive_length < 20 else 0.88
    variant_threshold = 0.75 if substantive_length < 20 else 0.68

    if critical_matches and score >= probable_threshold:
        classification = "probable"
    elif not critical_matches and (
        score >= variant_threshold
        or (
            score >= 0.60
            and bool(left.critical_tokens)
            and _critical_shape(left.critical_tokens)
            == _critical_shape(right.critical_tokens)
        )
    ):
        classification = "possible_variant"
    elif (
        not left.critical_tokens
        and not right.critical_tokens
        and substantive_length >= 24
        and score >= 0.94
    ):
        classification = "probable"
    else:
        classification = "none"

    if classification == "none":
        # Asset and answer differences alone are not useful duplicate reasons.
        keep = {
            "TYPE_DIFF",
            "OPTION_COUNT_DIFF",
            "NUMBER_CHANGED",
            "RELATION_CHANGED",
            "QUANTIFIER_CHANGED",
            "VARIABLE_CHANGED",
            "OPERATOR_CHANGED",
            "MATH_STRUCTURE_CHANGED",
        }
        reasons = [reason for reason in reasons if reason in keep]
        needs_visual_review = False

    return DuplicateComparison(
        classification=classification,
        score=round(score, 6),
        reasons=tuple(reasons),
        needs_visual_review=needs_visual_review,
    )


def compare_questions(
    left: QuestionDuplicateInput | Mapping[str, object],
    right: QuestionDuplicateInput | Mapping[str, object],
) -> DuplicateComparison:
    return compare_question_fingerprints(
        build_question_fingerprint(left), build_question_fingerprint(right)
    )


def rank_duplicate_candidates(
    query: QuestionFingerprint,
    candidates: Sequence[QuestionFingerprint],
    *,
    limit: int = 5,
) -> tuple[RankedDuplicateCandidate, ...]:
    """Refine already-recalled candidates and return review-worthy matches."""

    if limit < 1:
        return ()
    ranking = {
        "exact": 3,
        "probable": 2,
        "possible_variant": 1,
        "none": 0,
    }
    results: list[RankedDuplicateCandidate] = []
    for index, candidate in enumerate(candidates):
        comparison = compare_question_fingerprints(query, candidate)
        if comparison.classification != "none":
            results.append(RankedDuplicateCandidate(index, comparison))
    results.sort(
        key=lambda result: (
            -ranking[result.comparison.classification],
            -result.comparison.score,
            result.candidate_index,
        )
    )
    return tuple(results[:limit])


def find_batch_duplicate_groups(
    fingerprints: Sequence[QuestionFingerprint],
    *,
    max_bucket_size: int = DEFAULT_MAX_LSH_BUCKET_SIZE,
    max_candidates_per_item: int = DEFAULT_MAX_CANDIDATES_PER_ITEM,
) -> BatchDuplicateScan:
    """Find batch-local duplicate groups without an all-pairs scan.

    Exact hashes are grouped in O(B), then SimHash candidates are refined.
    Text-fragment bands are consulted only for items which still have no
    review-worthy primary result.  Both stages share one per-item candidate
    budget and deduplicate pairs before refinement.
    Very broad LSH buckets and per-item candidate lists are bounded and reported
    through ``index_complete`` so callers never describe a partial scan as
    complete.
    """

    if max_bucket_size < 2:
        raise ValueError("max_bucket_size must be at least two")
    if max_candidates_per_item < 1:
        raise ValueError("max_candidates_per_item must be positive")
    if not fingerprints:
        return BatchDuplicateScan((), 0, 0, 0, 0, 0)

    exact_groups: dict[str, list[int]] = defaultdict(list)
    simhash_buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, fingerprint in enumerate(fingerprints):
        exact_groups[fingerprint.exact_hash].append(index)
        for band_no, band_value in enumerate(fingerprint.bands):
            simhash_buckets[(band_no, band_value)].append(index)

    candidate_pairs: set[tuple[int, int]] = set()
    per_item_count: Counter[int] = Counter()
    exact_group_count = 0
    for members in exact_groups.values():
        if len(members) < 2:
            continue
        exact_group_count += 1
        # A chain retains one connected exact group with O(B) evidence without
        # concentrating every comparison on a single representative item.
        for left_index, right_index in zip(members, members[1:]):
            pair = (left_index, right_index)
            candidate_pairs.add(pair)
            per_item_count[left_index] += 1
            per_item_count[right_index] += 1

    def add_bucket_candidates(
        buckets: Mapping[tuple[object, ...], Sequence[int]],
        *,
        per_item_limit: int,
        eligible_items: set[int] | None = None,
    ) -> tuple[int, set[tuple[int, int]]]:
        truncated = 0
        stage_rejected: set[tuple[int, int]] = set()
        for bucket_key in sorted(buckets):
            members = buckets[bucket_key]
            if len(members) < 2:
                continue
            if eligible_items is not None and not any(
                member in eligible_items for member in members
            ):
                continue
            if len(members) > max_bucket_size:
                truncated += 1
                continue
            for offset, left_index in enumerate(members[:-1]):
                for right_index in members[offset + 1 :]:
                    if eligible_items is not None and not (
                        left_index in eligible_items
                        or right_index in eligible_items
                    ):
                        continue
                    if (
                        fingerprints[left_index].exact_hash
                        == fingerprints[right_index].exact_hash
                    ):
                        continue
                    pair = (left_index, right_index)
                    if pair in candidate_pairs or pair in stage_rejected:
                        continue
                    if (
                        per_item_count[left_index] >= per_item_limit
                        or per_item_count[right_index] >= per_item_limit
                    ):
                        stage_rejected.add(pair)
                        continue
                    candidate_pairs.add(pair)
                    per_item_count[left_index] += 1
                    per_item_count[right_index] += 1
        return truncated, stage_rejected

    # Reserve part of the shared cap so noisy SimHash buckets cannot starve the
    # text-fragment fallback for items whose primary candidates all refine away.
    primary_item_limit = max(1, (max_candidates_per_item + 1) // 2)
    primary_truncated, primary_rejected = add_bucket_candidates(
        simhash_buckets,
        per_item_limit=primary_item_limit,
    )
    primary_pairs = set(candidate_pairs)
    comparisons: list[BatchPairComparison] = []
    primary_review_items: set[int] = set()
    for left_index, right_index in sorted(primary_pairs):
        comparison = compare_question_fingerprints(
            fingerprints[left_index], fingerprints[right_index]
        )
        if comparison.classification != "none":
            comparisons.append(
                BatchPairComparison(left_index, right_index, comparison)
            )
            primary_review_items.update((left_index, right_index))

    fallback_items = set(range(len(fingerprints))) - primary_review_items
    text_truncated = 0
    text_rejected: set[tuple[int, int]] = set()
    if fallback_items:
        text_buckets: dict[tuple[int, str], list[int]] = defaultdict(list)
        for index, fingerprint in enumerate(fingerprints):
            for band_no, band_value in enumerate(fingerprint.text_bands):
                if band_value != EMPTY_TEXT_BAND:
                    text_buckets[(band_no, band_value)].append(index)
        text_truncated, text_rejected = add_bucket_candidates(
            text_buckets,
            per_item_limit=max_candidates_per_item,
            eligible_items=fallback_items,
        )

    text_pairs = candidate_pairs - primary_pairs
    for left_index, right_index in sorted(text_pairs):
        comparison = compare_question_fingerprints(
            fingerprints[left_index], fingerprints[right_index]
        )
        if comparison.classification != "none":
            comparisons.append(
                BatchPairComparison(left_index, right_index, comparison)
            )

    parent = list(range(len(fingerprints)))

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left_index: int, right_index: int) -> None:
        left_root = find(left_index)
        right_root = find(right_index)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for comparison in comparisons:
        union(comparison.left_index, comparison.right_index)

    members_by_root: dict[int, list[int]] = defaultdict(list)
    comparisons_by_root: dict[int, list[BatchPairComparison]] = defaultdict(list)
    for comparison in comparisons:
        root = find(comparison.left_index)
        members_by_root[root].extend(
            (comparison.left_index, comparison.right_index)
        )
        comparisons_by_root[root].append(comparison)

    rank = {"exact": 3, "probable": 2, "possible_variant": 1, "none": 0}
    groups: list[BatchDuplicateGroup] = []
    for root, raw_members in members_by_root.items():
        members = tuple(sorted(set(raw_members)))
        group_comparisons = tuple(comparisons_by_root[root])
        strongest = max(
            (item.comparison.classification for item in group_comparisons),
            key=lambda classification: rank[classification],
        )
        groups.append(
            BatchDuplicateGroup(members, strongest, group_comparisons)
        )
    groups.sort(key=lambda group: group.member_indexes)

    return BatchDuplicateScan(
        groups=tuple(groups),
        candidate_pair_count=len(candidate_pairs),
        compared_pair_count=len(candidate_pairs),
        exact_group_count=exact_group_count,
        truncated_bucket_count=primary_truncated + text_truncated,
        dropped_candidate_pair_count=len(
            (primary_rejected | text_rejected) - candidate_pairs
        ),
    )


__all__ = [
    "BatchDuplicateGroup",
    "BatchDuplicateScan",
    "BatchPairComparison",
    "DuplicateComparison",
    "FINGERPRINT_VERSION",
    "QuestionDuplicateInput",
    "QuestionFingerprint",
    "RankedDuplicateCandidate",
    "build_question_fingerprint",
    "compare_question_fingerprints",
    "compare_questions",
    "compute_simhash_128",
    "compute_text_fragment_bands",
    "EMPTY_TEXT_BAND",
    "extract_choices",
    "extract_critical_math_tokens",
    "find_batch_duplicate_groups",
    "normalize_math_text",
    "rank_duplicate_candidates",
    "simhash_bands",
    "simhash_similarity",
]
