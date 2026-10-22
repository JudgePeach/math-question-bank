"""Verify publication boundaries with Git's own ignore matcher."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ignored_paths(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required to verify .gitignore semantics")
    subprocess.run(
        [git, "init", "--quiet", str(tmp_path)], check=True, capture_output=True
    )
    shutil.copyfile(PROJECT_ROOT / ".gitignore", tmp_path / ".gitignore")

    def match(paths):
        result = subprocess.run(
            [
                git,
                "-c",
                f"core.excludesFile={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "check-ignore",
                "--no-index",
                "--verbose",
                "-z",
                "--stdin",
            ],
            input=b"".join(path.encode("utf-8") + b"\0" for path in paths),
            cwd=tmp_path,
            check=False,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode in (0, 1), result.stderr.decode("utf-8")
        fields = result.stdout.decode("utf-8").split("\0")
        assert fields.pop() == ""
        assert len(fields) % 4 == 0
        return {
            fields[index + 3]: not fields[index + 2].startswith("!")
            for index in range(0, len(fields), 4)
        }

    return match


def test_only_root_environment_template_can_be_published(ignored_paths):
    private = [
        ".env",
        ".env.local",
        ".env.production",
        "service/.env",
        "service/.env.example",
        "service/.env.production",
        "service/development.env.local",
    ]
    result = ignored_paths([*private, ".env.example"])
    assert all(result.get(path, False) for path in private)
    assert not result.get(".env.example", False)


@pytest.mark.parametrize("extension", ["db", "sqlite", "sqlite3"])
def test_sqlite_databases_and_companions_stay_private(ignored_paths, extension):
    private = [
        f"{prefix}questions.{extension}{suffix}"
        for prefix in ("", "local/")
        for suffix in ("", "-journal", "-wal", "-shm", ".backup", ".sha256")
    ]
    result = ignored_paths(private)
    assert all(result.get(path, False) for path in private)


def test_actual_runtime_and_export_locations_stay_private(ignored_paths):
    private = [
        ".system_generated/local_token",
        ".system_generated/server.log",
        ".system_generated/library_transfer_tasks/receipt.json",
        ".system_generated/audits/privacy/report.json",
        "data_backup/questions_backup.json",
        "data_backup/snapshots/backup.txt",
        "data_backup/archive/archive.json",
        "static/uploads/question.png",
        "static/uploads/subdirectory/question.png",
        "static_副本/question.png",
        "restored_content_sources/backup/content_sources.json",
        ".build_cache/release-build.log",
        "Release_Output/MathBank_3.0.1/manifest.json",
        "dist/MathBank/main.py",
        "build/MathBank/main.py",
        "venv/pyvenv.cfg",
        "docs/local-audit/report.json",
        "export.zip",
        "paper.pdf",
        "server.log",
    ]
    result = ignored_paths(private)
    assert all(result.get(path, False) for path in private)


def test_product_code_resources_and_fixture_exceptions_remain_publishable(ignored_paths):
    public = [
        "main.py",
        "mathbank/library_transfer.py",
        "mathbank/library_transfer_tasks.py",
        "mathbank/library_transfer_upload.py",
        "mathbank/storage_access.py",
        "mathbank/resources/curriculums/A.json",
        "mathbank/resources/content_sources.json",
        "scripts/build_release.py",
        "scripts/windows_launcher.py",
        "static/js/api.js",
        "tests/test_gitignore_privacy.py",
        "tests/test_library_transfer_batch_export.py",
        "tests/test_library_transfer_batch_import.py",
        "tests/test_library_transfer_record_memory.py",
        "tests/test_library_transfer_storage_api.py",
        "tests/test_library_transfer_tasks.py",
        "tests/test_library_transfer_tasks_api.py",
        "tests/test_library_transfer_upload.py",
        "tests/test_library_transfer_windows.py",
        "tests/test_question_fingerprint_batches.py",
        "tests/test_question_reference_batches.py",
        "tests/test_raster_validation.py",
        "tests/test_storage_access.py",
        "tests/test_sync_batch_export.py",
        "tests/test_sync_exchange_catalog_memory.py",
        "tests/fixtures/questions/example.json",
        "tests/fixtures/images/question.png",
        "tests/fixtures/pdf_source_scopes/supported_cm_script.pdf",
        "data_backup/.gitkeep",
        "data_backup/archive/.gitkeep",
        "static/uploads/.gitkeep",
        "tests/artifacts/.gitkeep",
        "docs/AI_DATABASE_GUIDE.md",
        "docs/images/screenshot.png",
    ]
    result = ignored_paths(public)
    assert not any(result.get(path, False) for path in public)


def test_internal_tools_are_limited_to_the_declared_paths(ignored_paths):
    names = (
        "association_batch",
        "classification_proposals",
        "difficulty_batch",
        "knowledge_backfill",
        "knowledge_backfill_apply",
        "question_exchange",
    )
    private = [
        f"{package}/{name}.py"
        for package in ("mathbank", "scripts")
        for name in names
    ] + [
        "scripts/backfill_question_references.py",
        "scripts/difficulty_classify.py",
        "scripts/migrate_word_math_private_chars.py",
        "scripts/resources/nested/protocol.json",
        "tests/test_backfill_question_references.py",
        "tests/test_question_references_exchange.py",
        "tests/test_migrate_word_math_private_chars.py",
        *(f"tests/test_{name}.py" for name in names),
    ]
    public = [
        "mathbank/resources/question_exchange.json",
        "mathbank/runtime/question_exchange.py",
        "tests/fixtures/question_exchange/example.json",
    ]
    result = ignored_paths([*private, *public])
    assert all(result.get(path, False) for path in private)
    assert not any(result.get(path, False) for path in public)
