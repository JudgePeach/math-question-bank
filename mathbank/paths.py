"""Centralized, working-directory-independent paths for MathBank.

All persistent data and bundled resources are anchored to the project root so
the server and command-line tools behave identically regardless of the shell's
current working directory.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"
STATIC_CSS_DIR = STATIC_DIR / "css"
UPLOADS_DIR = STATIC_DIR / "uploads"
TEST_UPLOADS_DIR = STATIC_DIR / "test_uploads"

TEMPLATES_DIR = PROJECT_ROOT / "templates"
DATA_BACKUP_DIR = PROJECT_ROOT / "data_backup"
SYSTEM_GENERATED_DIR = PROJECT_ROOT / ".system_generated"

DATABASE_FILE = PROJECT_ROOT / "math_question_bank.db"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"

DIST_DIR = PROJECT_ROOT / "dist"
BUILD_CACHE_DIR = PROJECT_ROOT / ".build_cache"
CURRICULUMS_DIR = Path(__file__).resolve().parent / "resources" / "curriculums"


def project_path(*parts: str) -> Path:
    """Return an absolute path below the MathBank project root."""

    return PROJECT_ROOT.joinpath(*parts)


def sqlite_url(path: Path = DATABASE_FILE) -> str:
    """Build a cross-platform SQLAlchemy URL for an absolute SQLite path."""

    return f"sqlite:///{path.resolve().as_posix()}"
