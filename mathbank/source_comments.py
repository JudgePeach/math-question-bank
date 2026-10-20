"""Hide the workbench's exact leading source-attribution block in copies.

This is not a general HTML-comment cleaner. Stored question content and
ordinary comments, examples, incomplete blocks and non-leading blocks stay
unchanged. Callers use the result only for rendering, export or derived
duplicate fingerprints.
"""

SOURCE_ATTRIBUTION_PREFIX = "<!-- MathBank source attribution:\n"
SOURCE_ATTRIBUTION_SUFFIX = "\n-->\n\n"


def strip_source_attribution_comment(content: str) -> str:
    """Remove one complete, dedicated head block and its two added newlines."""
    if not content.startswith(SOURCE_ATTRIBUTION_PREFIX):
        return content
    closing = content.find("-->", len(SOURCE_ATTRIBUTION_PREFIX))
    if closing < 0:
        return content
    body = content[len(SOURCE_ATTRIBUTION_PREFIX):closing]
    if not body.endswith("\n") or "<!--" in body:
        return content
    if not content.startswith(SOURCE_ATTRIBUTION_SUFFIX, closing - 1):
        return content
    return content[closing + len("-->\n\n"):]
