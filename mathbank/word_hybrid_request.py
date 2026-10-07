"""Word compatibility adapter for the shared bounded source-hybrid request."""
from mathbank.source_hybrid_request import (
    SourceHybridResult as WordHybridResult,
    SourceHybridContractError as WordHybridContractError,
    build_source_hybrid_messages as build_word_hybrid_messages,
    request_source_hybrid,
)
from mathbank.source_metadata import (
    SourceMetadataContractError,
    SourceMetadataMessageBudgetError as WordHybridMessageBudgetError,
)
from mathbank.word_hybrid_plan import WordHybridPlanError, require_word_hybrid_plan, word_hybrid_plan_diagnostics


def request_word_hybrid(*args, **kwargs):
    return request_source_hybrid(*args, **kwargs,
        plan_validator=require_word_hybrid_plan, plan_diagnostics=word_hybrid_plan_diagnostics,
        hard_stop_errors=(SourceMetadataContractError, WordHybridPlanError),
        diagnostics_key="word_hybrid", source_label="Word")
