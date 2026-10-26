"""Budgets for user-requested local compilation, conversion and setup work.

These bounds do not control cancellation, process termination, locks, cache
retention or automatic retry counts. Keep those lifecycle limits independent.
"""

TIKZ_COMPILE_TIMEOUT_SECONDS = 120
LATEX_EXPORT_TIMEOUT_SECONDS = 300
WORD_CONVERSION_TIMEOUT_SECONDS = 300
SOURCE_RENDER_TIMEOUT_SECONDS = 300
RUNTIME_DOWNLOAD_TIMEOUT_SECONDS = 120
RUNTIME_PROBE_TIMEOUT_SECONDS = 30
