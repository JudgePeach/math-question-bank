"""Shared model-request limits for document import and source review."""


DOCUMENT_AI_TIMEOUT_SECONDS = 600


def is_retryable_document_http_status(status_code: int) -> bool:
    """Only transient provider responses justify another document request.

    This classifies a response; it does not authorize or initiate a retry.
    Callers must still enforce their existing cancellation and request budget.
    """
    return type(status_code) is int and (
        status_code in {408, 425, 429} or 500 <= status_code <= 599
    )
