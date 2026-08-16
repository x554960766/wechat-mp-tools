"""Tests for scripts/verify_macos_bundle.py — written first (red phase)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Module under test — must exist before import works
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import verify_macos_bundle as vmb  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Helpers: fake subprocess.run that returns canned stdout/stderr/returncode
# ---------------------------------------------------------------------------

def _make_run(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> Any:
    """Return a mock for subprocess.run that returns a CompletedProcess."""
    result = subprocess.CompletedProcess(
        args=[],
        stdout=stdout.encode(),
        stderr=stderr.encode(),
        returncode=returncode,
    )
    mock = MagicMock(return_value=result)
    return mock


def _fake_bundle(tmp_path: Path, has_main: bool = True, has_dylib: bool = True,
                 has_chromium: bool = False) -> Path:
    """Create a minimal .app tree and return the path."""
    app = tmp_path / "WeChat MP Tools.app"
    contents = app / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True)
    (contents / "Info.plist").write_text("")
    if has_main:
        (macos / "WeChat MP Tools").write_text("")
    if has_dylib:
        lib_dir = contents / "MacOS" / "_internal" / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "foo.dylib").write_text("")
    if has_chromium:
        fw_dir = contents / "Frameworks"
        fw_dir.mkdir(parents=True)
        (fw_dir / "Chromium.app").mkdir()
        chrom_macos = fw_dir / "Chromium.app" / "Contents" / "MacOS"
        chrom_macos.mkdir(parents=True)
        (chrom_macos / "Chromium").write_text("")
    return app


# ---------------------------------------------------------------------------
# 1. Parsing `lipo -archs` output
# ---------------------------------------------------------------------------

class TestParseLipoArches:
    def test_single_arch(self):
        assert vmb._parse_lipo_arches("x86_64\n") == {"x86_64"}

    def test_multiple_arches(self):
        assert vmb._parse_lipo_arches("x86_64 arm64\n") == {"x86_64", "arm64"}

    def test_empty_output(self):
        assert vmb._parse_lipo_arches("") == set()

    def test_trailing_whitespace(self):
        assert vmb._parse_lipo_arches("  arm64  \n") == {"arm64"}


# ---------------------------------------------------------------------------
# 2. architectures(path) -> set[str]
# ---------------------------------------------------------------------------

class TestArchitectures:
    def test_returns_lipo_output(self):
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            result = vmb.architectures(Path("/fake/binary"))
        assert result == {"arm64"}
        mock_run.assert_called_once()

    def test_returns_empty_on_failure(self):
        mock_run = _make_run(returncode=1, stderr="not a fat file")
        with patch.object(vmb, "_run", mock_run):
            result = vmb.architectures(Path("/fake/binary"))
        assert result == set()


# ---------------------------------------------------------------------------
# 3. Requiring the main executable
# ---------------------------------------------------------------------------

class TestMainExecutableRequired:
    def test_missing_main_exe_fails(self, tmp_path):
        app = _fake_bundle(tmp_path, has_main=False)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert any("main executable" in e.lower() for e in errs)

    def test_main_exe_wrong_arch_fails(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="x86_64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert any("arm64" in e and "main" in e.lower() for e in errs)

    def test_main_exe_correct_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("main" in e.lower() for e in errs)


# ---------------------------------------------------------------------------
# 4. Requiring a .dylib / .so native extension match
# ---------------------------------------------------------------------------

class TestNativeExtensionRequired:
    def test_no_native_extensions_fails(self, tmp_path):
        app = _fake_bundle(tmp_path, has_dylib=False)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert any("native" in e.lower() or ".dylib" in e.lower() or ".so" in e.lower() for e in errs)

    def test_dylib_wrong_arch_fails(self, tmp_path):
        app = _fake_bundle(tmp_path)
        call_count = 0
        def side_effect(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            arch = "arm64" if call_count == 1 else "x86_64"
            return subprocess.CompletedProcess([], arch.encode(), b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert any("arm64" in e for e in errs)

    def test_dylib_correct_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("native" in e.lower() for e in errs)


# ---------------------------------------------------------------------------
# 5. Checking Chromium when bundled
# ---------------------------------------------------------------------------

class TestChromiumCheck:
    def test_chromium_missing_arch_fails(self, tmp_path):
        app = _fake_bundle(tmp_path, has_chromium=True)
        call_count = 0
        def side_effect(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            arch = "x86_64" if call_count >= 3 else "arm64"
            return subprocess.CompletedProcess([], arch.encode(), b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert any("chromium" in e.lower() for e in errs)

    def test_chromium_correct_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path, has_chromium=True)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("chromium" in e.lower() for e in errs)

    def test_no_chromium_bundled_no_error(self, tmp_path):
        app = _fake_bundle(tmp_path, has_chromium=False)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert not any("chromium" in e.lower() for e in errs)


# ---------------------------------------------------------------------------
# 6. Universal binary acceptance
# ---------------------------------------------------------------------------

class TestUniversalBinary:
    def test_universal_binary_containing_requested_arch_passes(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="x86_64 arm64")
        with patch.object(vmb, "_run", mock_run):
            errs = vmb.verify(app, "arm64")
        assert errs == []

    def test_universal_binary_wrong_single_request_fails(self, tmp_path):
        app = _fake_bundle(tmp_path)
        call_count = 0
        def side_effect(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess([], b"x86_64\n", b"", 0)
        with patch.object(vmb, "_run", side_effect=side_effect):
            errs = vmb.verify(app, "arm64")
        assert len(errs) > 0


# ---------------------------------------------------------------------------
# 7. Unsupported requested architecture rejection (before subprocess)
# ---------------------------------------------------------------------------

class TestUnsupportedArch:
    def test_rejects_unsupported_arch(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run) as m:
            errs = vmb.verify(app, "i386")
        assert any("unsupported" in e.lower() for e in errs)
        m.assert_not_called()

    def test_rejects_empty_arch(self, tmp_path):
        app = _fake_bundle(tmp_path)
        with patch.object(vmb, "_run") as m:
            errs = vmb.verify(app, "")
        assert any("unsupported" in e.lower() for e in errs)
        m.assert_not_called()


# ---------------------------------------------------------------------------
# 8. CLI integration
# ---------------------------------------------------------------------------

class TestCLI:
    def test_exit_zero_on_success(self, tmp_path):
        app = _fake_bundle(tmp_path)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            with pytest.raises(SystemExit) as exc_info:
                vmb.main([str(app), "arm64"])
        assert exc_info.value.code == 0

    def test_exit_nonzero_on_failure(self, tmp_path, capsys):
        app = _fake_bundle(tmp_path, has_main=False)
        mock_run = _make_run(stdout="arm64")
        with patch.object(vmb, "_run", mock_run):
            with pytest.raises(SystemExit) as exc_info:
                vmb.main([str(app), "arm64"])
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert len(captured.err) > 0

    def test_missing_args_exits_nonzero(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            vmb.main([])
        assert exc_info.value.code != 0

    def test_deterministic_output_order(self, tmp_path, capsys):
        """Errors are sorted for deterministic output."""
        app = _fake_bundle(tmp_path, has_main=False, has_dylib=False)
        with patch.object(vmb, "_run", _make_run(stdout="arm64")):
            with pytest.raises(SystemExit):
                vmb.main([str(app), "arm64"])
        captured = capsys.readouterr()
        lines = [l for l in captured.err.strip().split("\n") if l]
        assert lines == sorted(lines)
