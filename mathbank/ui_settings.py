"""Machine-local visibility settings for optional application workspaces."""

import os
import re


def community_qa_enabled() -> bool:
    """Ship QA by default; an ignored local .env can opt out."""
    return os.environ.get("MATHBANK_QA_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def render_qa_visibility(html: str, *, enabled: bool) -> str:
    """Remove only explicitly marked, bundled QA blocks when disabled."""
    if enabled:
        return html
    html = re.sub(
        r"<!-- QA_START -->.*?<!-- QA_END -->", "", html, flags=re.DOTALL,
    )
    return html.replace('<html lang="zh-CN">', '<html lang="zh-CN" class="qa-disabled">', 1)
