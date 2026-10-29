"""Keep the Pillow 13 Windows acceptance job native, complete and unprivileged."""

from pathlib import Path
import re
import shlex


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow_jobs():
    source = WORKFLOW.read_text(encoding="utf-8")
    body = source.split("\njobs:\n", 1)[1]
    starts = list(re.finditer(r"^  ([a-zA-Z0-9_-]+):\s*$", body, re.MULTILINE))
    jobs = {
        match.group(1): body[match.end():starts[index + 1].start()
                             if index + 1 < len(starts) else len(body)]
        for index, match in enumerate(starts)
    }
    return source, jobs


def test_native_pillow_job_runs_for_unprivileged_pull_requests():
    source, jobs = _workflow_jobs()
    assert re.search(r"^  pull_request:\s*$", source, re.MULTILINE)
    assert "pull_request_target" not in source
    permissions = source.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
    assert permissions.strip() == "contents: read"
    job = jobs["pillow-windows311"]
    assert "    runs-on: windows-2022" in job
    assert not re.search(r"^    if:", job, re.MULTILINE)
    assert "continue-on-error" not in job
    assert "secrets." not in job


def test_native_pillow_job_does_not_replace_existing_platform_coverage():
    _, jobs = _workflow_jobs()
    assert {"test", "sharing-windows", "pandoc-native", "release-guards"} <= jobs.keys()
    assert 'python-version: ["3.10", "3.14"]' in jobs["test"]
    assert 'python-version: "3.10"' in jobs["sharing-windows"]
    assert 'python-version: "3.10"' in jobs["release-guards"]
    assert "b.validate_windows_runtime(b.BUILD_DIR)" in jobs["release-guards"]
    for job in jobs.values():
        assert not re.search(r"^    if:", job, re.MULTILINE)


def test_native_pillow_job_checks_installed_runtime_and_dependencies():
    _, jobs = _workflow_jobs()
    job = jobs["pillow-windows311"]
    assert 'python-version: "3.11"' in job
    assert 'PYTHONUTF8: "1"' in job
    assert "actions/setup-node@" in job
    assert "-r requirements-dev.txt" in job
    assert "python -m pip check" in job
    assert 'assert sys.platform == "win32"' in job
    assert "assert sys.version_info[:2] == (3, 11)" in job
    assert 'assert PIL.__version__ == "13.0.0"' in job
    assert "import pymupdf" in job and "import pdf_inspector" in job


def test_native_pillow_job_runs_lock_image_and_full_suites_with_diagnostics():
    _, jobs = _workflow_jobs()
    job = jobs["pillow-windows311"]
    commands = re.findall(r"^          (python -m pytest[^\n]+)$", job, re.MULTILINE)
    assert len(commands) == 2
    invocations = [shlex.split(command.split("|", 1)[0]) for command in commands]
    focused, full = invocations
    assert {
        "tests/test_document_project_windows_locks.py", "tests/test_asset_lifecycle.py",
        "tests/test_asset_security.py", "tests/test_raster_validation.py",
        "tests/test_processing_limits.py",
    } <= set(focused)
    assert "tests/" in full
    for invocation in invocations:
        # A selected subset must never masquerade as the complete acceptance run.
        assert not any(arg in {"-k", "-m", "--lf", "--last-failed", "--ff", "--failed-first"}
                       or arg.startswith(("--deselect", "--ignore"))
                       for arg in invocation[3:])
        assert any(arg.startswith("--junitxml=") for arg in invocation)
    assert job.count("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }") == 2
    artifact = job.split("uses: actions/upload-artifact@", 1)
    assert len(artifact) == 2
    assert "if: always()" in artifact[0]
    for name in ("runtime-windows311.txt", "focused-windows311.xml", "focused-windows311.log",
                 "full-windows311.xml", "full-windows311.log"):
        assert name in artifact[1]
