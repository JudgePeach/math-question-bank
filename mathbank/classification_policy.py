"""Explicit classification effort profiles; the quick default is unchanged."""
from dataclasses import dataclass
import os
import warnings

from .processing_limits import bounded_integer


@dataclass(frozen=True)
class ClassificationPolicy:
    mode: str
    output_tokens: int
    thinking_enabled: bool


def resolve_classification_policy(environ=None, *, mode=None):
    environment = os.environ if environ is None else environ
    selected = mode if mode is not None else environment.get('MATHBANK_CLASSIFY_MODE', 'quick')
    selected = selected.strip().lower() if isinstance(selected, str) else ''
    if selected not in {'quick', 'precise'}:
        if mode is not None:
            raise ValueError('分类质量只支持 quick 或 precise。')
        warnings.warn('MATHBANK_CLASSIFY_MODE must be quick or precise; using quick.',
                      RuntimeWarning, stacklevel=2)
        selected = 'quick'
    precise = selected == 'precise'
    limit = bounded_integer(environment,
        'MATHBANK_CLASSIFY_PRECISE_MAX_TOKENS' if precise else 'MATHBANK_CLASSIFY_QUICK_MAX_TOKENS',
        8192 if precise else 4096, 1024, 65536 if precise else 32768)
    return ClassificationPolicy(selected, limit, precise)


def public_classification_policy(policy=None):
    value = policy or resolve_classification_policy()
    return {'mode': value.mode, 'output_tokens': value.output_tokens,
            'thinking_requested': value.thinking_enabled}
