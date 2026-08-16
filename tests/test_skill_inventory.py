import importlib.util
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/ljt-repo-architect/scripts/inventory.py"
spec = importlib.util.spec_from_file_location("ljt_inventory", SCRIPT)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)

classify_path = inventory.classify_path


def test_classify_entrypoints():
    assert classify_path(PurePosixPath("main.py")) == "entrypoints"


def test_classify_backend():
    assert classify_path(PurePosixPath("backend/douyin.py")) == "backend"


def test_classify_frontend():
    assert classify_path(PurePosixPath("frontend/js/api.js")) == "frontend"


def test_classify_injection():
    assert classify_path(PurePosixPath("injection_scripts/src/feed.js")) == "injection"


def test_classify_vendored_subtitle_remover():
    assert classify_path(PurePosixPath("backend/subtitle_remover/x.py")) == "vendored"


def test_classify_vendored_injection_lib():
    assert classify_path(PurePosixPath("injection_scripts/lib/x.js")) == "vendored"


def test_classify_vendored_graphify_out():
    assert classify_path(PurePosixPath("graphify-out/graph.json")) == "vendored"


def test_classify_docs():
    assert classify_path(PurePosixPath("docs/design.md")) == "docs"


def test_classify_build():
    assert classify_path(PurePosixPath(".github/workflows/build.yml")) == "build"


def test_classify_other():
    assert classify_path(PurePosixPath("LICENSE")) == "other"
    assert classify_path(PurePosixPath("README.md")) == "other"
    assert classify_path(PurePosixPath("requirements.txt")) == "other"


def test_classify_none_for_ignored():
    assert classify_path(PurePosixPath("__pycache__/x.py")) is None
    assert classify_path(PurePosixPath("data/secret.json")) is None
    assert classify_path(PurePosixPath("build/output.exe")) is None


def test_cli_json_output():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), "--json"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["root"].startswith("/"), "root must be absolute"
    assert len(data["tracked_files"]) > 100, f"expected >100 tracked files, got {len(data['tracked_files'])}"
    groups = data["groups"]
    assert "backend/douyin.py" in groups["backend"], "backend/douyin.py missing from backend group"
    # Vendored group must appear in output with tracked vendored files
    assert "vendored" in groups, "vendored group must appear in output"
    assert any(f.startswith("backend/subtitle_remover/") for f in groups["vendored"]), \
        "vendored group must contain backend/subtitle_remover files"
    assert any(f.startswith("injection_scripts/lib/") for f in groups["vendored"]), \
        "vendored group must contain injection_scripts/lib files"
    # No group should contain graphify-out paths
    for group_name, files in groups.items():
        for f in files:
            assert not f.startswith("graphify-out/"), f"graphify-out found in {group_name}: {f}"


def test_cli_non_ascii_paths_preserved():
    """C-quoted non-ASCII paths like docs/B站模块设计实现文档.md must appear in groups.docs."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), "--json"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    docs_files = data["groups"].get("docs", [])
    # At least one Chinese-named doc file must be present and unescaped
    cn_docs = [f for f in docs_files if any(ord(c) > 127 for c in f)]
    assert len(cn_docs) >= 1, f"no unescaped CJK paths in docs group; docs={docs_files}"
    # The specific file must be present with its real Unicode name
    assert "docs/B站模块设计实现文档.md" in docs_files, \
        "docs/B站模块设计实现文档.md missing from docs group (C-quoting bug)"
